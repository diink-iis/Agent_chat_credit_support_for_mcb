"""
evaluate.py — E2E-оценка агента по qa.jsonl (задача 3.5 Участника 3).

Прогоняет все 180 кейсов qa.jsonl через собранный граф и считает метрики
ОТДЕЛЬНО ПО КАЖДОЙ из 8 категорий (рекомендация README: разные категории
проверяют разные аспекты).

Что измеряется (детерминированно, из состояния графа):
  - category_acc  — классификатор поставил верную категорию (qa.category);
  - outcome_acc   — тип финального результата совпал с expected_outcome_type
                    (info|calculation|escalation|rejection|clarification);
  - для escalation_* — доля верных эскалаций + совпадение триггера;
  - для edge_*/offtopic — доля корректных отказов (outcome_type=rejection);
  - citation_recall — попал ли хотя бы один referenced_document в источники
                    ответа. Считается ТОЛЬКО для citable-категорий (info,
                    transactional, edge_conflict), где ответ опирается на нормативку;
                    в refusal/escalation/offtopic корректный ответ цитаты не содержит,
                    там метрика неприменима (→ «—»). Осмысленно с реальным RAG;
  - judge (опц., --judge) — LLM-судья на GigaChat оценивает соответствие ответа
                    expected_behavior (0/1).

Режимы:
  --mode stub      — оффлайн, без GigaChat (baseline-классификатор + FakeRetriever +
                     шаблонный генератор). Метрики category/outcome/escalation
                     осмысленны; citation/judge — нет (RAG фейковый).
  --mode gigachat  — боевой: реальный Retriever Участника 1 + GigaChat-узлы.
                     Нужен .env с GIGACHAT_CREDENTIALS и собранный rag/index.

Использование:
  python evaluate.py --mode stub
  python evaluate.py --mode gigachat --judge
  python evaluate.py --mode gigachat --limit 30 --categories info,transactional

  # «Прогнать один раз»: дорогой проход агента сохраняется в eval_runs.json.
  python evaluate.py --mode gigachat               # 1 раз: агент + RAG (+ кэш ответов)
  python evaluate.py --from-runs --judge           # потом: судья/метрики без агента
  python evaluate.py --from-runs                   # пересчёт метрик с кэша (бесплатно)
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Optional

from agent.graph import build_graph
from agent.state import make_initial_state

REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_QA = REPO_ROOT / "data" / "qa" / "qa.jsonl"

# Все 8 категорий датасета — фиксируем порядок вывода.
ALL_CATEGORIES = [
    "info", "transactional", "escalation_sales", "escalation_negative",
    "edge_no_data", "edge_conflict", "edge_manipulation", "offtopic",
]
ESCALATION_CATEGORIES = {"escalation_sales", "escalation_negative"}

# Категории, где правильный ответ ОПИРАЕТСЯ на нормативную базу — только здесь
# citation осмысленна как метрика грунтинга. В остальных (escalation_*, edge_no_data,
# edge_manipulation, offtopic) корректное поведение — эскалация / отказ / вежливое
# перенаправление БЕЗ цитаты, а gold-референс носит справочный характер. Считать там
# citation значит штрафовать за верное поведение (что и подтверждает judge: 75–100%).
# Поэтому для них citation помечается «—», как escalation.
CITABLE_CATEGORIES = {"info", "transactional", "edge_conflict"}

# Категория → ожидаемый триггер эскалации (для проверки точности триггера).
_CATEGORY_TO_TRIGGER = {
    "escalation_sales": {"intent"},
    "escalation_negative": {"negative", "human_request"},
}


# --- Сопоставление источников с referenced_documents (как в rag/evaluate_recall) ---

def _extract_doc_and_section(ref: str) -> tuple[str, str]:
    """Из '01_credit_products.md#2.1.3' → ('01_credit_products.md', '2.1.3')."""
    if "#" in ref:
        doc_id, section_id = ref.split("#", 1)
        return doc_id, section_id
    return ref, ""


def _section_matches(result_section: str, ref_section: str) -> bool:
    """Совпадение секции: точное или префиксное ('2' покрывает '2.1.3')."""
    if not ref_section:
        return True
    if result_section == ref_section:
        return True
    return result_section.startswith(ref_section + ".")


# Источник в scope_tags имеет вид: "[3] 01_credit_products.md#2.1.5 (BUSINESS_OBOROT)".
_SOURCE_RE = re.compile(r"([\w./-]+\.md)#([\d.]+)")


def _citation_hit(sources: list[dict], referenced_documents: list[str]) -> Optional[bool]:
    """
    Попал ли хотя бы один gold-документ в источники ответа.
    Возвращает None, если у кейса нет referenced_documents (метрика неприменима).
    """
    if not referenced_documents:
        return None
    parsed: list[tuple[str, str]] = []
    for tag in sources:
        match = _SOURCE_RE.search(tag.get("source", ""))
        if match:
            parsed.append((match.group(1), match.group(2)))
    for ref in referenced_documents:
        ref_doc, ref_section = _extract_doc_and_section(ref)
        for doc_id, section_id in parsed:
            if doc_id == ref_doc and _section_matches(section_id, ref_section):
                return True
    return False


# --- Прогон одного кейса -------------------------------------------------------

def run_case(graph, case: dict) -> dict:
    """Прогнать один кейс через граф, вернуть наблюдаемые поля состояния."""
    state = make_initial_state(
        channel=case.get("channel", "chat_site"),
        session_client_id=case.get("client_id"),
        question=case.get("question") or "",
        history=case.get("history") or [],
    )
    result = graph.invoke(
        state,
        config={"configurable": {"thread_id": f"eval-{case['id']}"}},
    )
    return {
        "category": result.get("category"),
        "escalation_trigger": result.get("escalation_trigger"),
        "outcome_type": result.get("outcome_type"),
        "answer": result.get("answer", ""),
        "sources": result.get("sources", []),
        "escalated": result.get("escalation") is not None,
    }


# --- Метрики -------------------------------------------------------------------

def _blank_bucket() -> dict:
    return {
        "total": 0,
        "category_hits": 0,
        "outcome_hits": 0,
        "escalation_hits": 0,    # верно эскалировано + триггер
        "citation_total": 0,
        "citation_hits": 0,
        "judge_total": 0,
        "judge_hits": 0,
    }


def evaluate(
    graph,
    cases: list[dict],
    judge_fn=None,
    verbose: bool = False,
    cached_runs: Optional[dict] = None,
) -> dict:
    """
    Прогнать все кейсы и собрать метрики по категориям.

    Дорогой проход агента (RAG + GigaChat) отделён от оценки: наблюдаемые ответы
    собираются в `runs` (id → observed) и возвращаются, чтобы их можно было сохранить
    и потом пересчитывать метрики / перепрогонять судью без повторного вызова агента
    (см. cached_runs и флаги --runs/--from-runs). Если для кейса есть запись в
    cached_runs — агент НЕ вызывается, берём готовый ответ.
    """
    by_cat: dict[str, dict] = defaultdict(_blank_bucket)
    per_case: list[dict] = []
    runs: dict[str, dict] = {}

    for i, case in enumerate(cases, 1):
        gold_category = case.get("category", "unknown")
        gold_outcome = case.get("expected_outcome_type")
        case_id = case["id"]

        if cached_runs is not None and case_id in cached_runs:
            # Реплей: берём сохранённый ответ агента, граф не трогаем.
            observed = cached_runs[case_id]
        else:
            # Устойчивость к транзиентным сбоям (сеть/GigaChat): один упавший кейс не
            # должен ронять весь прогон из 180. Помечаем как промах и идём дальше.
            try:
                observed = run_case(graph, case)
            except Exception as exc:  # noqa: BLE001
                print(f"  [{case_id}] сбой прогона: {exc} — помечаю как промах.")
                observed = {"category": None, "escalation_trigger": None,
                            "outcome_type": None, "answer": f"[ошибка прогона: {exc}]",
                            "sources": [], "escalated": False}
        runs[case_id] = observed

        bucket = by_cat[gold_category]
        bucket["total"] += 1

        category_ok = observed["category"] == gold_category
        outcome_ok = observed["outcome_type"] == gold_outcome
        bucket["category_hits"] += int(category_ok)
        bucket["outcome_hits"] += int(outcome_ok)

        # Эскалация: корректна, если эскалировали И триггер из допустимого набора.
        escalation_ok = None
        if gold_category in ESCALATION_CATEGORIES:
            allowed = _CATEGORY_TO_TRIGGER[gold_category]
            escalation_ok = observed["escalated"] and observed["escalation_trigger"] in allowed
            bucket["escalation_hits"] += int(escalation_ok)

        # Цитирование (RAG): осмысленно только в «отвечающих из нормативки»
        # категориях (CITABLE_CATEGORIES). В refusal/escalation/offtopic корректный
        # ответ не содержит цитаты — там метрику не считаем (→ «—»).
        citation_ok = None
        if gold_category in CITABLE_CATEGORIES:
            citation_ok = _citation_hit(observed["sources"], case.get("referenced_documents", []))
        if citation_ok is not None:
            bucket["citation_total"] += 1
            bucket["citation_hits"] += int(citation_ok)

        # LLM-судья (опционально, градуированная оценка 0/0.5/1).
        judge_ok = None
        if judge_fn is not None:
            judge_ok = judge_fn(case, observed)
            bucket["judge_total"] += 1
            bucket["judge_hits"] += judge_ok  # сумма дробных баллов

        per_case.append({
            "id": case["id"],
            "category": gold_category,
            "predicted_category": observed["category"],
            "category_ok": category_ok,
            "expected_outcome": gold_outcome,
            "predicted_outcome": observed["outcome_type"],
            "outcome_ok": outcome_ok,
            "escalation_ok": escalation_ok,
            "citation_ok": citation_ok,
            "judge_ok": judge_ok,
            "answer": observed["answer"],
        })

        if verbose and not category_ok:
            print(f"  [{case['id']}] {gold_category} → предсказано {observed['category']!r}")
        if i % 20 == 0:
            print(f"  ...{i}/{len(cases)}")

    return {"by_category": dict(by_cat), "per_case": per_case, "runs": runs}


def _rate(hits: int, total: int) -> float:
    return hits / total if total else 0.0


def summarize(results: dict) -> dict:
    """Свернуть сырые счётчики в проценты + общий итог."""
    by_cat = results["by_category"]
    summary: dict[str, dict] = {}
    overall = _blank_bucket()

    for cat in ALL_CATEGORIES:
        b = by_cat.get(cat)
        if not b:
            continue
        for key, value in b.items():
            overall[key] += value
        row = {
            "total": b["total"],
            "category_acc": _rate(b["category_hits"], b["total"]),
            "outcome_acc": _rate(b["outcome_hits"], b["total"]),
        }
        if cat in ESCALATION_CATEGORIES:
            row["escalation_acc"] = _rate(b["escalation_hits"], b["total"])
        if b["citation_total"]:
            row["citation_recall"] = _rate(b["citation_hits"], b["citation_total"])
        if b["judge_total"]:
            row["judge_acc"] = _rate(b["judge_hits"], b["judge_total"])
        summary[cat] = row

    summary["overall"] = {
        "total": overall["total"],
        "category_acc": _rate(overall["category_hits"], overall["total"]),
        "outcome_acc": _rate(overall["outcome_hits"], overall["total"]),
        "citation_recall": _rate(overall["citation_hits"], overall["citation_total"])
        if overall["citation_total"] else None,
        "judge_acc": _rate(overall["judge_hits"], overall["judge_total"])
        if overall["judge_total"] else None,
    }
    return summary


def print_summary(summary: dict) -> None:
    print("\n" + "=" * 92)
    print("E2E-ОЦЕНКА АГЕНТА ПО qa.jsonl")
    print("=" * 92)
    header = (f"{'категория':<20}{'n':>4}{'category':>11}{'outcome':>10}"
              f"{'escalation':>12}{'citation':>11}{'judge':>9}")
    print(header)
    print("-" * 92)
    for cat in ALL_CATEGORIES:
        row = summary.get(cat)
        if not row:
            continue
        esc = row.get("escalation_acc")
        esc_str = f"{esc:.0%}" if esc is not None else "—"
        cite_str = f"{row['citation_recall']:.0%}" if "citation_recall" in row else "—"
        judge_str = f"{row['judge_acc']:.0%}" if "judge_acc" in row else "—"
        print(f"{cat:<20}{row['total']:>4}{row['category_acc']:>11.0%}"
              f"{row['outcome_acc']:>10.0%}{esc_str:>12}{cite_str:>11}{judge_str:>9}")
    print("-" * 92)
    ov = summary["overall"]
    cite = f"{ov['citation_recall']:.0%}" if ov["citation_recall"] is not None else "—"
    judge = f"{ov['judge_acc']:.0%}" if ov["judge_acc"] is not None else "—"
    print(f"{'ИТОГО':<20}{ov['total']:>4}{ov['category_acc']:>11.0%}"
          f"{ov['outcome_acc']:>10.0%}{'—':>12}{cite:>11}{judge:>9}")
    print("=" * 92)
    print("category — точность классификатора (qa.category); "
          "outcome — тип результата (qa.expected_outcome_type);\nescalation — "
          "эскалировано с верным триггером; citation — gold-источник в ответе "
          "(RAG); judge — оценка ответа LLM-судьёй.")


# --- LLM-судья (опционально, gigachat-режим) -----------------------------------

def make_judge(model: str = "GigaChat"):
    """
    Собрать LLM-судью на GigaChat. Градуированная оценка (0 / 0.5 / 1), чтобы
    отличать «по сути верно, но неполно» от провала и снизить шум бинарной метрики.

    Судья — базовая GigaChat (Lite): дешевле и независим от модели агента (Pro),
    что снижает риск «само себя хвалит». Модель явная, не зависит от дефолта фабрики.
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    from agent.llm import build_gigachat_chat

    chat = build_gigachat_chat(model=model, temperature=0.0)
    system = (
        "Ты — оценщик качества ответов банковского Помощника по кредитованию МСБ. "
        "Тебе дают ожидаемое поведение и фактический ответ. Оцени соответствие по сути "
        "(факты, корректный отказ/эскалация, отсутствие выдумок и нарушений ограничений) "
        "по шкале: 1.0 — выполнено; 0.5 — по сути верно, но неполно/с мелкими огрехами; "
        "0.0 — не выполнено/выдумка/нарушение. Верни ТОЛЬКО JSON: "
        '{"score": 1.0|0.5|0.0, "reason": "коротко"}.'
    )

    def judge(case: dict, observed: dict) -> float:
        user = (
            f"Вопрос клиента: {case.get('question') or '(см. историю диалога)'}\n"
            f"Ожидаемое поведение: {case.get('expected_behavior')}\n"
            f"Ожидаемый тип результата: {case.get('expected_outcome_type')}\n\n"
            f"Фактический ответ Помощника:\n{observed.get('answer')}\n\n"
            "Верни только JSON."
        )
        try:
            response = chat.invoke([SystemMessage(content=system), HumanMessage(content=user)])
            data = json.loads(response.content.replace("```json", "").replace("```", "").strip())
            score = float(data.get("score", 0.0))
            return min(max(score, 0.0), 1.0)
        except Exception:  # noqa: BLE001 — судья не должен ронять прогон
            return 0.0

    return judge


# --- Сборка зависимостей графа -------------------------------------------------

def build_eval_graph(mode: str, index_dir: str, db_path: Optional[str], top_k: int):
    if mode == "stub":
        from agent.llm import make_stub_deps
        deps = make_stub_deps()
    elif mode == "gigachat":
        from dotenv import load_dotenv

        from agent.llm import make_gigachat_deps
        load_dotenv()
        deps = make_gigachat_deps(index_dir=index_dir, db_path=db_path, top_k=top_k)
    else:
        raise ValueError(f"Неизвестный режим: {mode}")
    return build_graph(deps)


def load_cases(qa_path: Path, categories: Optional[set[str]], limit: Optional[int]) -> list[dict]:
    cases = []
    with qa_path.open("r", encoding="utf-8") as f:
        for line in f:
            case = json.loads(line)
            if categories and case.get("category") not in categories:
                continue
            cases.append(case)
    if limit:
        cases = cases[:limit]
    return cases


def main() -> None:
    parser = argparse.ArgumentParser(description="E2E-оценка агента по qa.jsonl")
    parser.add_argument("--mode", choices=["stub", "gigachat"], default="stub")
    parser.add_argument("--qa", type=Path, default=DEFAULT_QA)
    parser.add_argument("--index-dir", default="rag/index")
    parser.add_argument("--db-path", default=None)
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--judge", action="store_true",
                        help="Включить LLM-судью (только gigachat-режим).")
    parser.add_argument("--categories", default=None,
                        help="Подмножество категорий через запятую.")
    parser.add_argument("--limit", type=int, default=None, help="Ограничить число кейсов.")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "eval_results.json")
    parser.add_argument("--runs", type=Path, default=REPO_ROOT / "eval_runs.json",
                        help="Файл-кэш ответов агента (сохраняется при прогоне, "
                             "переиспользуется с --from-runs).")
    parser.add_argument("--from-runs", action="store_true",
                        help="Не вызывать агента: взять ответы из --runs (реплей) и "
                             "только пересчитать метрики / прогнать судью. Дёшево.")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    # Креды GigaChat нужны и судье (--judge), и агенту. Грузим .env здесь, чтобы
    # путь --from-runs --judge тоже видел GIGACHAT_CREDENTIALS (без поднятия агента).
    from dotenv import load_dotenv
    load_dotenv()

    categories = set(args.categories.split(",")) if args.categories else None
    cases = load_cases(args.qa, categories, args.limit)

    # Реплей из кэша: агента не поднимаем (нет затрат RAG/Pro), граф не нужен.
    cached_runs = None
    graph = None
    if args.from_runs:
        if not args.runs.exists():
            raise SystemExit(f"--from-runs: файл прогонов {args.runs} не найден. "
                             "Сначала сделай обычный прогон, чтобы его создать.")
        cached = json.loads(args.runs.read_text(encoding="utf-8"))
        cached_runs = cached.get("runs", cached)
        print(f"Реплей из {args.runs}: {len(cached_runs)} сохранённых ответов "
              f"(агент НЕ вызывается). Кейсов к оценке: {len(cases)}")
    else:
        print(f"Режим: {args.mode}. Кейсов к прогону: {len(cases)}")
        graph = build_eval_graph(args.mode, args.index_dir, args.db_path, args.top_k)

    judge_fn = None
    if args.judge:
        # Судья работает и при реплее (читает сохранённые ответы). В stub-режиме без
        # реплея судить нечего — RAG фейковый.
        if args.mode != "gigachat" and not args.from_runs:
            print("ВНИМАНИЕ: --judge работает в gigachat-режиме или с --from-runs, игнорирую.")
        else:
            judge_fn = make_judge()

    results = evaluate(graph, cases, judge_fn=judge_fn, verbose=args.verbose,
                       cached_runs=cached_runs)
    summary = summarize(results)
    print_summary(summary)

    out = {"mode": args.mode, "summary": summary, "per_case": results["per_case"]}
    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nДетальные результаты сохранены в {args.out}")

    # Кэш ответов агента сохраняем только когда реально прогоняли агента (не реплей),
    # чтобы один дорогой проход переиспользовать для пересчёта метрик и судьи.
    if not args.from_runs:
        runs_out = {"mode": args.mode, "runs": results["runs"]}
        args.runs.write_text(json.dumps(runs_out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Ответы агента (кэш для --from-runs) сохранены в {args.runs}")


if __name__ == "__main__":
    main()
