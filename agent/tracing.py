"""
tracing.py — наблюдаемость графа через Arize Phoenix (OpenTelemetry / OpenInference).

Трейсит весь прогон LangGraph и вложенные вызовы GigaChat: GigaChat — это LangChain
ChatModel, а LangGraph эмитит колбэки LangChain, поэтому инструментация LangChain
ловит и узлы графа (classify / retrieve_rag / query_db / generate / escalate), и
сами LLM-запросы с промптами, токенами и таймингами.

Включение: переменная окружения PHOENIX_TRACING=1 (иначе функция ничего не делает —
в обычном прогоне трейсинг выключен и не тянет лишних зависимостей).

Phoenix UI запускается ОТДЕЛЬНО (подробности — в OBSERVABILITY.md):
    python -m phoenix.server.main serve     # UI на http://localhost:6006
Эндпоинт коллектора берётся из PHOENIX_COLLECTOR_ENDPOINT (по умолчанию localhost:6006).
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger("agent.tracing")

_ENABLED = False  # защита от повторной инструментации (Streamlit перезапускает скрипт)


def setup_tracing(project_name: str = "msb-agent") -> bool:
    """
    Подключить трейсинг к Phoenix, если PHOENIX_TRACING=1. Идемпотентно.

    Returns:
        True — трейсинг включён (или уже был); False — выключен/недоступен.
    """
    global _ENABLED
    if _ENABLED:
        return True
    if not os.getenv("PHOENIX_TRACING"):
        return False
    try:
        from phoenix.otel import register

        # register сам читает PHOENIX_COLLECTOR_ENDPOINT (по умолчанию http://localhost:6006)
        # и при auto_instrument=True подключает установленные OpenInference-инструменторы
        # (в т.ч. LangChain) — отдельный вызов LangChainInstrumentor не нужен.
        register(
            project_name=project_name,
            auto_instrument=True,
            batch=True,
        )
        _ENABLED = True
        endpoint = os.getenv("PHOENIX_COLLECTOR_ENDPOINT", "http://localhost:6006")
        logger.info("Phoenix tracing включён → %s (project=%s)", endpoint, project_name)
        return True
    except Exception as exc:  # noqa: BLE001 — наблюдаемость не должна ронять приложение
        logger.warning("Phoenix tracing не включён (%s) — продолжаю без трейсинга.", exc)
        return False
