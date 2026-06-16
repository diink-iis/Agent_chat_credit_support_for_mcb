"""
evaluate_recall.py — оценка качества retrieval по qa.jsonl.

Метрика: recall@k = процент кейсов, где хотя бы один referenced_document попал в top-k.
Разбивка по категориям и подкатегориям. Итоговый показатель качества retrieval —
грунтинг по citable-категориям (CITABLE_CATEGORIES: info / transactional /
edge_conflict); поведенческие категории в него не входят (там recall неинформативен).

Использование:
  python evaluate_recall.py --qa data/qa/qa.jsonl --corpus rag/corpus_chunks.jsonl --k 5
"""

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv

from retriever import Retriever, build_query_from_history

# Категории, где ответ опирается на нормативку и retrieval осмыслен (как citation в
# evaluate.py). В поведенческих категориях (escalation_*, edge_no_data,
# edge_manipulation, offtopic) gold-ссылка указывает на регламент поведения агента,
# а не на смысловой пункт, — там корректность обеспечивают classify/escalate, и
# recall неинформативен. Поэтому итоговый грунтинг считаем по этим трём.
CITABLE_CATEGORIES = {"info", "transactional", "edge_conflict"}


def section_matches(result_section: str, ref_section: str) -> bool:
    """
    Проверяет совпадение section_id.
    ref_section - из referenced_documents (например: "2", "2.1", "2.1.2")
    result_section - из corpus_chunks (например: "2.1.3")
    """
    if not ref_section:  # Пустая = весь документ
        return True
    if result_section == ref_section:  # Точное совпадение
        return True
    # Проверяем префикс: "2" должна совпадать с "2.1", "2.1.2" и т.д.
    return result_section.startswith(ref_section + ".")


def extract_doc_and_section(ref_doc: str) -> tuple[str, str]:
    """
    Из "01_credit_products.md#2.1.3" извлечь (doc_id, section_id).
    """
    if "#" in ref_doc:
        doc_id, section_id = ref_doc.split("#", 1)
    else:
        doc_id = ref_doc
        section_id = ""
    return doc_id, section_id


def evaluate_recall(
    qa_path: Path,
    corpus_path: Path,
    k: int = 5,
) -> dict:
    """
    Оценить recall@k retriever на qa.jsonl.
    
    Returns:
        dict с метриками: {
            "overall": {"hits": int, "total": int, "recall": float},
            "by_category": {...},
            "by_difficulty": {...},
        }
    """
    print(f"Загружаем индекс из {corpus_path}...")
    
    # Строим retriever с GigaChat embeddings.
    print("Используем GigaChat embeddings...")
    from embeddings import GigaChatEmbeddings
    embeddings = GigaChatEmbeddings()
    
    retriever = Retriever.build_from_corpus(corpus_path, embeddings=embeddings)
    print(f"Индекс загружен: {len(retriever.vector_store.chunks)} чанков\n")

    # Загружаем qa.jsonl.
    print(f"Загружаем QA из {qa_path}...")
    qa_cases = []
    with qa_path.open("r", encoding="utf-8") as f:
        for line in f:
            case = json.loads(line)
            qa_cases.append(case)
    print(f"Загружено {len(qa_cases)} кейсов\n")

    # Оценка.
    metrics = {
        "overall": {"hits": 0, "total": len(qa_cases), "recall": 0.0},
        "by_category": defaultdict(lambda: {"hits": 0, "total": 0, "recall": 0.0}),
        "by_subcategory": defaultdict(lambda: {"hits": 0, "total": 0, "recall": 0.0}),
        "by_difficulty": defaultdict(lambda: {"hits": 0, "total": 0, "recall": 0.0}),
    }

    print(f"Оцениваем recall@{k}...\n")
    for i, case in enumerate(qa_cases):
        # Для multi-turn кейсов (пустой question) собираем запрос из history.
        query = build_query_from_history(case)
        referenced_docs = case.get("referenced_documents", [])
        category = case.get("category", "unknown")
        subcategory = case.get("subcategory", "unknown")
        difficulty = case.get("difficulty", "unknown")

        # Запускаем retriever.
        results = retriever.retrieve(query, k=k)

        # Проверяем, попали ли хотя бы один referenced_document в top-k.
        hit = False
        for ref_doc in referenced_docs:
            ref_doc_id, ref_section = extract_doc_and_section(ref_doc)
            for result in results:
                chunk_doc_id = result.get("doc_id", "")
                chunk_section = result.get("section_id", "")
                if chunk_doc_id == ref_doc_id and section_matches(chunk_section, ref_section):
                    hit = True
                    break
            if hit:
                break

        # Обновляем метрики.
        metrics["overall"]["hits"] += hit
        metrics["by_category"][category]["hits"] += hit
        metrics["by_category"][category]["total"] += 1
        metrics["by_subcategory"][subcategory]["hits"] += hit
        metrics["by_subcategory"][subcategory]["total"] += 1
        metrics["by_difficulty"][difficulty]["hits"] += hit
        metrics["by_difficulty"][difficulty]["total"] += 1

        if (i + 1) % 10 == 0:
            print(f"  {i + 1}/{len(qa_cases)}...")

    # Считаем recall.
    metrics["overall"]["recall"] = (
        metrics["overall"]["hits"] / metrics["overall"]["total"]
        if metrics["overall"]["total"] > 0
        else 0.0
    )
    for key_dict in [metrics["by_category"], metrics["by_subcategory"], metrics["by_difficulty"]]:
        for key in key_dict:
            key_dict[key]["recall"] = (
                key_dict[key]["hits"] / key_dict[key]["total"]
                if key_dict[key]["total"] > 0
                else 0.0
            )

    # Грунтинг по citable-категориям: осмысленный показатель качества retrieval
    # (поведенческие категории исключены — см. CITABLE_CATEGORIES). Это число, а не
    # размытый overall по 8 категориям, и стоит сравнивать с citation из evaluate.py.
    citable_hits = sum(
        metrics["by_category"][c]["hits"] for c in CITABLE_CATEGORIES if c in metrics["by_category"]
    )
    citable_total = sum(
        metrics["by_category"][c]["total"] for c in CITABLE_CATEGORIES if c in metrics["by_category"]
    )
    metrics["citable"] = {
        "categories": sorted(CITABLE_CATEGORIES),
        "hits": citable_hits,
        "total": citable_total,
        "recall": citable_hits / citable_total if citable_total > 0 else 0.0,
    }

    return metrics


def print_metrics(metrics: dict) -> None:
    """Вывести метрики в читаемом формате."""
    print("\n" + "=" * 70)
    print("РЕЗУЛЬТАТЫ ОЦЕНКИ RECALL")
    print("=" * 70)

    overall = metrics["overall"]
    print(
        f"\nОбщий recall (все 8 категорий): {overall['recall']:.2%} "
        f"({overall['hits']}/{overall['total']})"
    )

    citable = metrics.get("citable")
    if citable:
        print(
            f"Грунтинг по citable ({', '.join(citable['categories'])}): "
            f"{citable['recall']:.2%} ({citable['hits']}/{citable['total']})  "
            f"← осмысленный показатель качества retrieval"
        )

    print("\nПо категориям:")
    for cat, m in sorted(metrics["by_category"].items()):
        print(
            f"  {cat:25s}: {m['recall']:.2%} ({m['hits']}/{m['total']})"
        )

    print("\nПо подкатегориям:")
    for subcat, m in sorted(metrics["by_subcategory"].items()):
        print(
            f"  {subcat:35s}: {m['recall']:.2%} ({m['hits']}/{m['total']})"
        )

    print("\nПо сложности:")
    for diff, m in sorted(metrics["by_difficulty"].items()):
        print(
            f"  {diff:15s}: {m['recall']:.2%} ({m['hits']}/{m['total']})"
        )

    print("\n" + "=" * 70)


def main():
    parser = argparse.ArgumentParser(description="Оценка recall@k retriever")
    parser.add_argument(
        "--qa",
        type=Path,
        default=Path(__file__).parent.parent / "data" / "qa" / "qa.jsonl",
        help="Путь к qa.jsonl",
    )
    parser.add_argument(
        "--corpus",
        type=Path,
        default=Path(__file__).parent / "corpus_chunks.jsonl",
        help="Путь к corpus_chunks.jsonl",
    )
    parser.add_argument(
        "--k",
        type=int,
        default=5,
        help="Размер top-k",
    )
    args = parser.parse_args()

    # Загружаем .env для GIGACHAT_CREDENTIALS
    load_dotenv()

    metrics = evaluate_recall(args.qa, args.corpus, k=args.k)
    print_metrics(metrics)

    # Сохраняем результаты.
    out_path = Path(__file__).parent / f"recall_results_k{args.k}.json"
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2, default=str)
    print(f"\nРезультаты сохранены в {out_path}")


if __name__ == "__main__":
    main()
