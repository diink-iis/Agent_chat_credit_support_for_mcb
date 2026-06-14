"""
llm.py — «боевые» реализации classify_fn / generate_fn на GigaChat.

Подключаются в граф через те же точки, что и оффлайн-заглушки (agent/stubs.py):
  - make_gigachat_classifier() читает prompts/classify.md, просит structured output;
  - make_gigachat_generator()  читает prompts/generate.md + правило коллизий
    Участника 1 (rag/prompts/collision_priority.md);
  - make_gigachat_deps()       собирает GraphDeps с реальным Retriever Участника 1.

Требует GIGACHAT_CREDENTIALS (см. .env.example). При сбое разбора ответа
классификатор откатывается на baseline (agent/stubs.rule_based_classify), чтобы
граф не падал.

ВНИМАНИЕ: модуль не запускается в песочнице без доступа к GigaChat — тестируется
на стороне, где есть ключ. Импорт langchain_gigachat ленивый (внутри функций).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Callable, Optional

from langchain_core.messages import HumanMessage, SystemMessage

from agent.nodes import GraphDeps
from agent.state import AgentState, latest_client_text, messages_to_history
from agent.stubs import rule_based_classify, template_generate

logger = logging.getLogger("agent.llm")

PROMPTS_DIR = Path(__file__).parent / "prompts"
COLLISION_PROMPT_PATH = Path(__file__).parent.parent / "rag" / "prompts" / "collision_priority.md"

_VALID_CATEGORIES = {
    "info", "transactional", "escalation_sales", "escalation_negative",
    "edge_no_data", "edge_conflict", "edge_manipulation", "offtopic",
}

# Триггеры эскалации (раздел 4). Всё, что не отсюда, считаем отсутствием триггера.
_VALID_TRIGGERS = {
    "intent", "negative", "human_request",
    "out_of_competence", "suspicious", "technical",
}

# Значения, которые LLM иногда присылает вместо настоящего null.
_NULLISH = {"", "null", "none", "нет", "n/a", "na", "-"}


def _clean(value):
    """Привести «null»-подобные строки к настоящему None."""
    if value is None:
        return None
    if isinstance(value, str) and value.strip().lower() in _NULLISH:
        return None
    return value


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_collision_rule() -> str:
    """Достать блок правила (первый ``` ... ```) из collision_priority.md Участника 1."""
    if not COLLISION_PROMPT_PATH.exists():
        return ""
    text = _read(COLLISION_PROMPT_PATH)
    parts = text.split("```")
    return parts[1].strip() if len(parts) >= 3 else text.strip()


def _strip_fences(text: str) -> str:
    """Убрать markdown-ограждения ```json ... ``` из ответа LLM."""
    return text.replace("```json", "").replace("```", "").strip()


def build_gigachat_chat(model: str = "GigaChat", temperature: float = 0.0):
    """
    Создать чат-клиент GigaChat. Берёт ключ из GIGACHAT_CREDENTIALS, область — из
    GIGACHAT_SCOPE (по умолчанию GIGACHAT_API_PERS).
    """
    from langchain_gigachat import GigaChat  # ленивый импорт

    credentials = os.getenv("GIGACHAT_CREDENTIALS")
    if not credentials:
        raise ValueError(
            "GIGACHAT_CREDENTIALS не установлены. Скопируй .env.example в .env."
        )
    scope = os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
    return GigaChat(
        credentials=credentials,
        scope=scope,
        model=model,
        temperature=temperature,
        verify_ssl_certs=False,
    )


def _format_dialog(state: AgentState) -> str:
    """Текстовое представление диалога для подачи в LLM."""
    lines = []
    for turn in messages_to_history(state):
        role = "Клиент" if turn["role"] == "client" else "Помощник"
        lines.append(f"{role}: {turn['text']}")
    return "\n".join(lines)


def _money(value) -> str:
    """Формат суммы с разделителями тысяч: 1800000 -> '1 800 000 ₽'."""
    try:
        return f"{int(value):,}".replace(",", " ") + " ₽"
    except (TypeError, ValueError):
        return str(value)


def _format_tool_results(tool_results: dict) -> str:
    """
    Человекочитаемая сводка данных клиента для генератора.

    Сырой JSON модель игнорировала (отправляла «смотрите в интернет-банке»). Здесь —
    только операционные поля, нужные для ответа. Скоринг (`credit_score`), долговую
    нагрузку и категорию отказа НЕ включаем вовсе: их нельзя раскрывать (п. 6.2), а
    любое присутствие в контексте провоцирует утечку — что и наблюдалось на прогоне.
    """
    lines: list[str] = []

    profile = tool_results.get("profile") or {}
    if profile:
        bits = [profile.get("name") or profile.get("client_id", "")]
        if profile.get("industry"):
            bits.append(profile["industry"])
        if profile.get("annual_revenue") is not None:
            bits.append(f"выручка {_money(profile['annual_revenue'])}/год")
        if profile.get("registration_date"):
            bits.append(f"в бизнесе с {profile['registration_date']}")
        bits.append("счёт в банке: " + ("да" if profile.get("has_account_in_bank") else "нет"))
        bits.append("зарплатный проект: " + ("да" if profile.get("has_payroll_project") else "нет"))
        lines.append("Профиль: " + ", ".join(b for b in bits if b))

    loans = tool_results.get("loans") or []
    if loans:
        lines.append(f"Действующие кредиты ({len(loans)}):")
        for ln in loans:
            row = (f"  • {ln.get('product_name', ln.get('product_code'))} "
                   f"({ln.get('contract_id')}): остаток {_money(ln.get('principal_outstanding'))}, "
                   f"ставка {ln.get('interest_rate')}%, срок {ln.get('term_months')} мес")
            term, passed = ln.get("term_months"), ln.get("months_passed")
            if isinstance(term, int) and isinstance(passed, int):
                row += f" (прошло {passed}, осталось ~{max(term - passed, 0)} мес)"
            row += (f", след. платёж {ln.get('next_payment_date')} на "
                    f"{_money(ln.get('next_payment_amount'))}")
            if ln.get("has_overdue"):
                row += f", ПРОСРОЧКА {ln.get('overdue_days')} дн. на {_money(ln.get('overdue_amount'))}"
            if ln.get("is_restructured"):
                row += ", реструктурирован"
            lines.append(row)

    apps = tool_results.get("applications") or []
    if apps:
        lines.append(f"Заявки ({len(apps)}):")
        for ap in apps:
            row = (f"  • {ap.get('product_code')}: запрошено {_money(ap.get('amount_requested'))} "
                   f"на {ap.get('term_requested_months')} мес, подана {ap.get('application_date')}, "
                   f"статус «{ap.get('status')}»")
            if ap.get("decision"):
                row += f", решение «{ap['decision']}» от {ap.get('decision_date')}"
            # decision_reason_category НЕ включаем — это скоринг-чувствительно (п. 6.2).
            lines.append(row)

    elig = tool_results.get("eligible_products")
    if elig and elig.get("products"):
        seg = {"micro": "микробизнес", "small": "малый бизнес",
               "out_of_segment": "вне сегмента МСБ"}.get(elig.get("segment"), "")
        lines.append(f"Подбор продуктов (по порогам регламента, сегмент: {seg}):")
        if elig.get("stop_factors"):
            lines.append("  Стоп-факторы (блокируют всё): " + "; ".join(elig["stop_factors"]))
        for p in elig["products"]:
            if p["eligible"]:
                lines.append(f"  ✓ {p['name']} — соответствует базовым требованиям")
            else:
                lines.append(f"  ✗ {p['name']} — " + "; ".join(p["reasons"]))

    return "\n".join(lines)


def make_gigachat_classifier(chat=None) -> Callable[[AgentState], dict]:
    """
    Классификатор на GigaChat. Возвращает функцию для GraphDeps.classify_fn.
    При ошибке разбора JSON откатывается на rule_based_classify.
    """
    chat = chat or build_gigachat_chat(temperature=0.0)
    system_prompt = _read(PROMPTS_DIR / "classify.md")

    def classify(state: AgentState) -> dict:
        user_prompt = (
            f"Канал обращения: {state.get('channel')}\n"
            f"Клиент идентифицирован: {'да' if state.get('client_id') else 'нет'}\n\n"
            f"Диалог:\n{_format_dialog(state)}\n\n"
            "Верни ТОЛЬКО JSON по схеме из инструкции, без markdown и пояснений."
        )
        try:
            response = chat.invoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt),
            ])
            data = json.loads(_strip_fences(response.content))
        except Exception as exc:  # noqa: BLE001 — любой сбой → безопасный откат
            logger.warning("classify: сбой GigaChat/JSON (%s) — откат на baseline.", exc)
            return rule_based_classify(state)

        category = data.get("category")
        if category not in _VALID_CATEGORIES:
            logger.warning("classify: неизвестная категория %r — откат на baseline.", category)
            return rule_based_classify(state)

        # Чистим триггер: «null»-строки → None, и принимаем только валидные значения.
        trigger = _clean(data.get("escalation_trigger"))
        if trigger is not None and trigger not in _VALID_TRIGGERS:
            logger.warning("classify: неизвестный триггер %r — обнуляю.", trigger)
            trigger = None

        return {
            "category": category,
            "escalation_trigger": trigger,
            "needs_db": bool(data.get("needs_db", False)),
            "needs_rag": bool(data.get("needs_rag", False)),
            "detected_product": _clean(data.get("detected_product")),
            "negative_markers": data.get("negative_markers", []) or [],
        }

    return classify


def make_gigachat_generator(chat=None) -> Callable[[AgentState], str]:
    """
    Генератор ответа на GigaChat. Возвращает функцию для GraphDeps.generate_fn.
    Подмешивает правило разрешения коллизий Участника 1.
    """
    chat = chat or build_gigachat_chat(temperature=0.0)
    # Системный промпт = общая роль/ограничения (3.1) + инструкция по задаче генерации (3.3).
    system_prompt = _read(PROMPTS_DIR / "system_prompt.md") + "\n\n---\n\n" + _read(PROMPTS_DIR / "generate.md")
    collision_rule = load_collision_rule()

    def generate(state: AgentState) -> str:
        context = state.get("retrieved_context", "")
        tool_results = state.get("tool_results", {})

        # Контекст диалога: без него генератор не понимает уточняющих реплик
        # («а если внесу 1 млн?», «а как разблокировать?») — теряет тему/продукт,
        # обсуждавшиеся выше, и отвечает невпопад. Подаём предыдущие реплики и
        # помечаем последнюю как текущий вопрос.
        dialog = messages_to_history(state)
        if len(dialog) > 1:
            prior = "\n".join(
                f"{'Клиент' if t['role'] == 'client' else 'Помощник'}: {t['text']}"
                for t in dialog[:-1]
            )
            user_parts = [
                f"Контекст диалога (предыдущие реплики):\n{prior}",
                f"\nТекущий вопрос клиента (понимай его в контексте диалога выше — "
                f"уточнения «а если…», «а по нему…» относятся к продукту/теме из "
                f"истории): {latest_client_text(state)}",
            ]
        else:
            user_parts = [f"Вопрос клиента: {latest_client_text(state)}"]

        # Данные клиента — АВТОРИТЕТНЫЙ источник, идут первыми и важнее нормативки
        # для запросов о статусе/состоянии (иначе модель отправляет в интернет-банк).
        has_data = bool(tool_results) and not tool_results.get("access_denied") and (
            tool_results.get("profile") or tool_results.get("loans")
            or tool_results.get("applications") or tool_results.get("eligible_products")
        )
        if has_data:
            user_parts.append(
                "\n=== ДАННЫЕ КЛИЕНТА (из БД банка, АВТОРИТЕТНЫЙ ИСТОЧНИК) ===\n"
                + _format_tool_results(tool_results)
                + "\n\nЭто СОБСТВЕННЫЕ данные авторизованного клиента — отвечать по ним "
                  "разрешено, это не данные третьих лиц. Используй конкретные значения "
                  "(статус, остаток, даты, суммы, просрочка). НЕ отправляй клиента в "
                  "интернет-банк и НЕ проси уточнить то, что уже есть выше."
            )
        if tool_results.get("access_denied"):
            user_parts.append("\nДанные клиента недоступны (анонимный канал). "
                              "Предложи авторизоваться, не выдумывай данные.")
        if collision_rule:
            user_parts.append(f"\nПравило приоритета при коллизиях:\n{collision_rule}")
        if context:
            label = ("Нормативка (для процедур/условий; по статусу/остатку приоритет "
                     "у данных клиента выше)") if has_data else "Источники из базы знаний"
            user_parts.append(f"\n{label}:\n{context}")
        user_parts.append("\nОтветь кратко и точно, цитируя источники (документ#пункт).")

        try:
            response = chat.invoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content="\n".join(user_parts)),
            ])
            return response.content
        except Exception as exc:  # noqa: BLE001
            logger.warning("generate: сбой GigaChat (%s) — откат на шаблон.", exc)
            return template_generate(state)

    return generate


def make_gigachat_deps(
    index_dir: str = "rag/index",
    db_path: Optional[str] = None,
    top_k: int = 8,
    escalation_log_path: Optional[str] = None,
    model: Optional[str] = None,
) -> GraphDeps:
    """
    Собрать GraphDeps для прода: реальный Retriever Участника 1 + GigaChat-узлы.
    Один чат-клиент переиспользуется для classify и generate.

    База агента — GigaChat-Pro (выше потолок на пограничных intent↔info кейсах);
    судья при оценке остаётся на Lite (см. evaluate.make_judge). Модель можно
    переопределить аргументом `model` или переменной окружения GIGACHAT_MODEL.
    """
    from rag import Retriever  # ленивый импорт (тянет chromadb)

    retriever = Retriever.load(index_dir)
    chat = build_gigachat_chat(model=model or os.getenv("GIGACHAT_MODEL", "GigaChat-Pro"))
    return GraphDeps(
        retriever=retriever,
        classify_fn=make_gigachat_classifier(chat),
        generate_fn=make_gigachat_generator(chat),
        db_path=db_path,
        top_k=top_k,
        escalation_log_path=escalation_log_path,
    )


def make_stub_deps(escalation_log_path: Optional[str] = None) -> GraphDeps:
    """GraphDeps на заглушках (без GigaChat) — для оффлайн-прогона и тестов."""
    from agent.stubs import FakeRetriever

    return GraphDeps(
        retriever=FakeRetriever(),
        classify_fn=rule_based_classify,
        generate_fn=template_generate,
        escalation_log_path=escalation_log_path,
    )
