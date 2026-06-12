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
from agent.tools import get_active_loans, get_applications, get_client_info

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

def classify_node(state: AgentState, deps: GraphDeps) -> dict:
    """
    Классифицировать обращение: категория, триггер эскалации, нужна ли БД/RAG,
    продукт, маркеры негатива. Тело классификации — в deps.classify_fn
    (GigaChat-промпт Участника 3 или baseline-заглушка).
    """
    result = deps.classify_fn(state)

    # Продукт: берём из классификатора, иначе — лёгкая эвристика Участника 1.
    detected_product = result.get("detected_product") or detect_product(
        latest_client_text(state)
    )

    return {
        "category": result.get("category"),
        "escalation_trigger": result.get("escalation_trigger"),
        "needs_db": bool(result.get("needs_db", False)),
        "needs_rag": bool(result.get("needs_rag", False)),
        "detected_product": detected_product,
        "negative_markers": result.get("negative_markers", []),
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

    hits = deps.retriever.retrieve(query=query, product_filter=product_filter, k=deps.top_k)

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
