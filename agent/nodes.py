"""
nodes.py — узлы графа LangGraph (задача 2.4).

Узлы: classify, retrieve_rag, query_db, generate_answer, escalate.
Каждый узел — функция (state, deps) -> частичное обновление состояния.
Зависимости (ретривер, классификатор, генератор) приходят через GraphDeps,
поэтому узлы одинаково работают с GigaChat-реализациями и с оффлайн-заглушками.

escalate здесь — минимальная версия; полный пакет EscalationPayload (п. 5.1,
задача 2.6) добавляется в слое 4.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional, Union
from pathlib import Path

from agent.auth import AccessDenied, IdentificationLevel, ResolvedIdentity, ensure_self_access
from agent.escalation import (
    build_escalation_payload,
    client_notification,
    resolve_trigger,
    simulate_handoff,
)
from agent.state import AgentState, latest_client_text, to_rag_case
from agent.tools import (
    get_active_loans,
    get_applications,
    get_client_info,
    get_eligible_products,
)

# Маркеры запроса о доступности продуктов (подкатегория tx_eligible_products) —
# тогда дополнительно считаем детерминированный подбор get_eligible_products.
_ELIGIBILITY_MARKERS = (
    "доступн", "подойдёт", "подойдет", "подойдут", "могу рассчитывать",
    "на какие продукт", "какие продукт", "какие кредиты мне", "что мне доступно",
    "что у вас есть", "могу взять", "могу ли я взять", "мне он подойдёт",
    "рассчитывать на кредит", "что мне подходит",
)

# Хелперы Участника 1.
from rag import build_query_from_history, detect_product


@dataclass
class GraphDeps:
    """
    Зависимости графа. Подменяемы: в проде — GigaChat + Retriever Участника 1,
    в тестах — заглушки из agent/stubs.py.
    """

    retriever: object                              # объект с .retrieve(query, product_filter, k)
    classify_fn: Callable[[AgentState], dict]      # классификатор
    generate_fn: Callable[[AgentState], str]       # генератор ответа
    db_path: Optional[Union[Path, str]] = None
    top_k: int = 5
    escalation_log_path: Optional[str] = None


# --- classify -----------------------------------------------------------------

# Защита в глубину (п. 7 РП-ОБ-005): высокоточные маркеры безопасности, при которых
# обращение ВСЕГДА эскалируется как suspicious — независимо от решения LLM. Это
# детерминированный предохранитель для критичных по комплаенсу паттернов: запрос
# чужих данных, обращение представителя без подтверждённых полномочий, prompt
# injection. LLM-классификатор стохастичен и иногда пропускает их (наблюдалось на
# прогоне: «я бухгалтер ООО …» классифицировался как self-query и выдавал данные).
# Жёсткие маркеры: уводят в suspicious ВСЕГДА, независимо от остального содержания —
# запрос данных третьих лиц (п. 7.2) и prompt injection / переопределение роли (п. 7.3).
_HARD_SECURITY_MARKERS = (
    # Данные третьих лиц (п. 7.2).
    "моего партнёр", "моего партнер", "моего супруг", "моей супруг", "моего контрагент",
    "моего конкурент", "моего коллег", "моего знаком", "данные другого", "чужой клиент",
    "чужие данные", "выведи всех клиент", "данные клиента c-",
    # Переопределение инструкций / prompt injection (п. 7.3).
    "забудь инструкц", "забудь все инструкц", "забудь всё инструкц", "игнорируй",
    "ignore previous", "ignore all", "системный промпт", "system prompt", "ты теперь",
    "с этого момента ты", "притворись",
)

# Заявление о роли представителя без подтверждённых полномочий (п. 8.6, 7.1). Само по
# себе НЕ эскалируем (бухгалтер/юрист может задать общий вопрос про условия) — только
# в связке с запросом КОНКРЕТНЫХ данных/операции клиента (_DATA_REQUEST_MARKERS).
_REPRESENTATIVE_MARKERS = (
    "я бухгалтер", "их бухгалтер", "я юрист", "я представитель", "представляю интересы",
    "по доверенности", "есть доверенность", "я директор отделения", "я сотрудник банка",
)

# Признак запроса конкретных данных/операции (а не общей справки). Используется только
# вместе с _REPRESENTATIVE_MARKERS, поэтому широкие термины здесь безопасны.
_DATA_REQUEST_MARKERS = (
    "наш кредит", "наши кредит", "наш долг", "наш остаток", "наши платеж", "наш платеж",
    "наша заявк", "нашу заявк", "наш счёт", "наш счет", "наши данные", "наш договор",
    "по нашему", "по нашей", "у нас платеж", "у нас кредит", "остаток", "платёж",
    "платеж", "задолженност", "выписк", "оформи", "переведи", "погаси", "статус заявк",
)


def _security_override(text: str) -> bool:
    """
    True, если в реплике есть высокоточный маркер угрозы безопасности (п. 7).

    Жёсткие маркеры (чужие данные, prompt injection) срабатывают всегда. Заявление о
    роли представителя уводит в suspicious только вместе с запросом конкретных данных/
    операции — чтобы общий вопрос «я бухгалтер, какие у вас кредиты для МСБ?» не
    эскалировался ложно, а «я бухгалтер …, какие у нас платежи?» — эскалировался.
    """
    low = text.lower()
    if any(marker in low for marker in _HARD_SECURITY_MARKERS):
        return True
    if any(marker in low for marker in _REPRESENTATIVE_MARKERS):
        return any(marker in low for marker in _DATA_REQUEST_MARKERS)
    return False


def classify_node(state: AgentState, deps: GraphDeps) -> dict:
    """
    Классифицировать обращение: категория, триггер эскалации, нужна ли БД/RAG,
    продукт, маркеры негатива. Тело классификации — в deps.classify_fn
    (GigaChat-промпт Участника 3 или baseline-заглушка).

    Поверх LLM работает детерминированный предохранитель безопасности
    (_security_override): критичные по п. 7 паттерны принудительно уводятся в
    suspicious-эскалацию, чтобы запрос чужих/служебных данных не зависел от
    стохастики модели и не доходил до query_db.
    """
    result = deps.classify_fn(state)

    # Продукт: берём из классификатора, иначе — лёгкая эвристика Участника 1.
    detected_product = result.get("detected_product") or detect_product(
        latest_client_text(state)
    )

    category = result.get("category")
    escalation_trigger = result.get("escalation_trigger")
    needs_db = bool(result.get("needs_db", False))
    needs_rag = bool(result.get("needs_rag", False))

    # Предохранитель безопасности: чужие данные / представитель / injection → suspicious.
    if _security_override(latest_client_text(state)):
        category = "edge_manipulation"
        escalation_trigger = "suspicious"
        needs_db = False
        needs_rag = False

    return {
        "category": category,
        "escalation_trigger": escalation_trigger,
        "needs_db": needs_db,
        "needs_rag": needs_rag,
        "detected_product": detected_product,
        "negative_markers": result.get("negative_markers", []),
        # Сброс эфемерных полей прошлого хода (multi-turn). classify — точка входа
        # КАЖДОГО хода, а checkpointer хранит состояние между ходами. Без сброса
        # outcome_type / tool_results / retrieved_* «протекают» с прошлой реплики
        # (узел generate пересчитывает outcome_type только если он None) — баг при
        # любом использовании графа как API. messages НЕ трогаем: их копит add_messages.
        "retrieved_context": "",
        "scope_tags": [],
        "retrieved_count": 0,
        "tool_results": {},
        "escalation": None,
        "outcome_type": None,
        "answer": None,
        "sources": [],
    }


# --- query_db -----------------------------------------------------------------

def _identity_from_state(state: AgentState) -> ResolvedIdentity:
    """Восстановить ResolvedIdentity из полей состояния (его выставил resolve_client)."""
    level = IdentificationLevel(state.get("identification_level", "anonymous"))
    return ResolvedIdentity(
        channel=state.get("channel", ""),
        level=level,
        client_id=state.get("client_id"),
    )


def query_db_node(state: AgentState, deps: GraphDeps) -> dict:
    """
    Получить данные клиента из БД. Авторизация проверяется здесь же
    (ensure_self_access) — это граница инструментов и защита в глубину.
    При анонимном канале / запросе чужих данных — фиксируем отказ, не падаем.
    """
    identity = _identity_from_state(state)
    requested_client_id = state.get("client_id") or ""

    try:
        authorized_id = ensure_self_access(identity, requested_client_id)
    except AccessDenied as exc:
        # Не лезем в БД — отдаём осмысленный отказ узлу generate.
        return {
            "tool_results": {"access_denied": True, "reason": str(exc)},
            "outcome_type": "rejection",
        }

    tool_results = {
        "client_id": authorized_id,
        "profile": get_client_info(authorized_id, deps.db_path),
        "loans": get_active_loans(authorized_id, deps.db_path),
        "applications": get_applications(authorized_id, deps.db_path),
    }
    # Запрос о доступности продуктов — добавляем детерминированный подбор (п. 4.1):
    # скоринг гатит доступ, но в результат попадают только нескоринговые причины.
    if any(marker in latest_client_text(state).lower() for marker in _ELIGIBILITY_MARKERS):
        tool_results["eligible_products"] = get_eligible_products(authorized_id, deps.db_path)
    return {"tool_results": tool_results}


# --- retrieve_rag -------------------------------------------------------------

def retrieve_rag_node(state: AgentState, deps: GraphDeps) -> dict:
    """
    Поиск по нормативным документам через Retriever Участника 1.
    Запрос собирается из накопленного контекста диалога (build_query_from_history),
    продукт — из classify или эвристики. Форматируем контекст и теги источников
    для узла generate (включая scope для разрешения коллизий).
    """
    rag_case = to_rag_case(state)
    query = build_query_from_history(rag_case)
    product_filter = state.get("detected_product") or detect_product(query)

    # Устойчивость к сбою поиска/эмбеддингов (сеть/GigaChat): не роняем граф, а
    # деградируем — отдаём пустой контекст. Генератор по промпту честно скажет, что
    # не может подтвердить факт, либо ответит из данных клиента (п. 4.4.3, 6.3).
    try:
        hits = deps.retriever.retrieve(query=query, product_filter=product_filter, k=deps.top_k)
    except Exception as exc:  # noqa: BLE001
        import logging
        logging.getLogger("agent.nodes").warning("retrieve_rag: сбой поиска (%s) — пустой контекст.", exc)
        return {"retrieved_context": "", "scope_tags": [], "retrieved_count": 0}

    context_parts: list[str] = []
    scope_tags: list[dict] = []
    for i, hit in enumerate(hits, 1):
        source_info = f"[{i}] {hit['doc_id']}#{hit['section_id']}"
        if hit.get("product_code"):
            source_info += f" ({hit['product_code']})"
        scope_tag = f"[scope={hit['scope']}]"

        context_parts.append(f"{source_info} {scope_tag}:\n{hit['text']}\n")
        scope_tags.append({
            "source": source_info,
            "scope": hit["scope"],
            "score": hit["score"],
            "product_code": hit.get("product_code"),
        })

    context = "=== КОНТЕКСТ ИЗ БАЗЫ ЗНАНИЙ ===\n\n" + "\n".join(context_parts)
    return {
        "retrieved_context": context,
        "scope_tags": scope_tags,
        "retrieved_count": len(hits),
    }


# --- generate_answer ----------------------------------------------------------

# Категории, в которых ответ — корректный отказ от недопустимого.
# offtopic / edge_no_data сюда НЕ входят: в датасете вежливое перенаправление
# и честное «нет данных» размечены как outcome_type=info (gold qa.jsonl), а не
# rejection. rejection зарезервирован за отказом от недопустимого (манипуляции).
_REJECTION_CATEGORIES = {"edge_manipulation"}

# Маркеры расчёта досрочного погашения — единственный транзакционный кейс,
# который датасет размечает как outcome_type=calculation (подкатегория
# tx_repayment_calc). Прочие транзакционные запросы (статус, остаток) — info.
_CALCULATION_MARKERS = ("досрочн", "закрыть кредит", "закрою", "закрыть свой",
                        "погасить полностью", "полного погашения")


def _is_repayment_calc(text: str) -> bool:
    return any(marker in text.lower() for marker in _CALCULATION_MARKERS)


def generate_answer_node(state: AgentState, deps: GraphDeps) -> dict:
    """
    Сгенерировать финальный ответ клиенту. Тело генерации — в deps.generate_fn
    (GigaChat-промпт Участника 3 или шаблон-заглушка). Здесь же выставляем
    outcome_type и собираем sources.
    """
    answer = deps.generate_fn(state)

    # outcome_type: если query_db уже отметил rejection — не перетираем.
    outcome_type = state.get("outcome_type")
    if outcome_type is None:
        if state.get("category") in _REJECTION_CATEGORIES:
            outcome_type = "rejection"
        elif state.get("needs_db") and _is_repayment_calc(latest_client_text(state)):
            outcome_type = "calculation"
        else:
            outcome_type = "info"

    from langchain_core.messages import AIMessage

    return {
        "answer": answer,
        "outcome_type": outcome_type,
        "sources": state.get("scope_tags", []),
        "messages": [AIMessage(answer)],  # add_messages допишет ответ в историю
    }


# --- escalate -----------------------------------------------------------------

def escalate_node(state: AgentState, deps: GraphDeps) -> dict:
    """
    Передать обращение «оператору» (учебная симуляция).

    Собирает полный пакет EscalationPayload по п. 5.1 РП-ОБ-005, логирует передачу
    (simulate_handoff) и уведомляет клиента о переключении (п. 5.3). Путь
    эскалации лога настраивается через deps.escalation_log_path.
    """
    from langchain_core.messages import AIMessage

    payload = build_escalation_payload(state)
    trigger = resolve_trigger(state)

    handoff = simulate_handoff(payload, log_path=getattr(deps, "escalation_log_path", None))
    answer = client_notification(trigger)

    return {
        "escalation": handoff,
        "outcome_type": "escalation",
        "answer": answer,
        "messages": [AIMessage(answer)],
    }
