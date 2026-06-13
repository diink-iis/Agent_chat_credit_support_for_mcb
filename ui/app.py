"""
ui/app.py — Streamlit-интерфейс для агента поддержки кредитования МСБ.

Чат поверх скомпилированного графа LangGraph (agent/graph.py). UI вызывает граф
напрямую — отдельного API-слоя нет, Streamlit нативно работает на Python.

Что показывает UI:
  - сам диалог (multi-turn через MemorySaver + thread_id);
  - канал и уровень доступа (авторизация = функция канала, см. agent/auth.py);
  - живой прогресс по узлам графа (classify → retrieve_rag → query_db → generate);
  - служебную «изнанку» ответа: маршрут (outcome_type), источники RAG, данные
    клиента из БД, и — при срабатывании триггера — пакет эскалации оператору.

Два режима зависимостей:
  - «Оффлайн (заглушки)» — make_stub_deps(): без GigaChat и без RAG-индекса.
  - «Боевой (GigaChat)» — make_gigachat_deps(): реальный Retriever + GigaChat.
    Требует GIGACHAT_CREDENTIALS и собранный индекс rag/index.

Запуск:
    streamlit run ui/app.py
"""

from __future__ import annotations

import sqlite3
import sys
import time
import uuid
from pathlib import Path

import streamlit as st

# Гарантируем, что корень репозитория в sys.path (пакеты agent/ и rag/).
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from langchain_core.messages import HumanMessage

# Боевой режим (GigaChat) — дефолт, поэтому загружаем .env с GIGACHAT_CREDENTIALS
# из корня репозитория до сборки зависимостей.
try:
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")
except ImportError:  # python-dotenv не обязателен, если ключ уже в окружении
    pass

from agent.graph import build_graph
from agent.state import make_initial_state

DB_PATH = REPO_ROOT / "data" / "clients" / "clients.sqlite"

# Каналы: первые два — авторизованные (доступны личные данные), остальные — анонимные.
CHANNELS = {
    "chat_intern": "Чат в интернет-банке (авторизован)",
    "mobile": "Мобильное приложение (авторизован)",
    "chat_site": "Чат на сайте (аноним)",
    "contact_center": "Контакт-центр (аноним)",
}
AUTHORIZED_CHANNELS = {"chat_intern", "mobile"}

# Подписи/иконка/цвет бейджа для типа исхода (outcome_type из графа).
OUTCOME_BADGE = {
    "info": ("ℹ️", "Информационный ответ", "blue"),
    "calculation": ("🧮", "Расчёт", "violet"),
    "escalation": ("🚨", "Эскалация на оператора", "red"),
    "rejection": ("⛔", "Отказ", "orange"),
    "clarification": ("❓", "Уточнение", "gray"),
}

# Человекочитаемые подписи узлов графа (для живого прогресса).
NODE_LABELS = {
    "classify": "🧭 Классификация обращения",
    "query_db": "🗂 Запрос к БД клиента",
    "retrieve_rag": "📚 Поиск по нормативке",
    "generate_answer": "✍️ Генерация ответа",
    "escalate": "🚨 Эскалация на оператора",
}

# Короткие иконки узлов — для компактного трейса маршрута под ответом.
ROUTE_ICONS = {
    "classify": "🧭",
    "query_db": "🗂",
    "retrieve_rag": "📚",
    "generate_answer": "✍️",
    "escalate": "🚨",
}

# Человекочитаемые названия триггеров эскалации.
TRIGGER_LABEL = {
    "intent": "Намерение оформить продукт",
    "negative": "Негатив клиента",
    "human_request": "Просьба переключить на человека",
    "out_of_competence": "Вне компетенции",
    "suspicious": "Подозрительное обращение",
    "technical": "Технический сбой",
}

# Примеры для пустого экрана (онбординг). Подставляются в поле ввода по клику.
EXAMPLE_PROMPTS = [
    "Какие кредиты вы предлагаете малому бизнесу?",
    "Какая минимальная ставка по оборотному кредиту?",
    "Как работает досрочное погашение?",
    "Хочу оформить Бизнес-Развитие",
]

# Поля состояния, которые нужно сбрасывать на каждом новом ходе диалога, иначе в
# checkpoint'е они «протекают» с прошлого хода (особенно outcome_type — узел
# generate пересчитывает его только если он None). messages не сбрасываем —
# add_messages дописывает новую реплику к истории.
TURN_RESET = {
    "category": None,
    "escalation_trigger": None,
    "needs_db": False,
    "needs_rag": False,
    "detected_product": None,
    "negative_markers": [],
    "retrieved_context": "",
    "scope_tags": [],
    "retrieved_count": 0,
    "tool_results": {},
    "escalation": None,
    "outcome_type": None,
    "answer": None,
    "sources": [],
}


@st.cache_data(show_spinner=False)
def load_scenario_clients() -> list[tuple[str, str]]:
    """Сценарные клиенты C-000001..C-000035 (id, подпись) для выпадающего списка."""
    if not DB_PATH.exists():
        return []
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT client_id, name, legal_form FROM clients "
            "WHERE client_id <= 'C-000035' ORDER BY client_id"
        ).fetchall()
    finally:
        conn.close()
    return [(cid, f"{cid} — {name} ({form})") for cid, name, form in rows]


def build_deps(mode: str):
    """Собрать GraphDeps под выбранный режим. Боевой режим может бросить исключение
    (нет ключа GigaChat / нет индекса) — пробрасываем наверх для показа ошибки."""
    if mode == "gigachat":
        from agent.llm import make_gigachat_deps

        return make_gigachat_deps(index_dir=str(REPO_ROOT / "rag" / "index"))
    from agent.llm import make_stub_deps

    return make_stub_deps()


@st.cache_resource(show_spinner="Загружаю Retriever и GigaChat…")
def get_deps(mode: str):
    """
    Тяжёлые зависимости (Chroma-индекс + GigaChat) — кэшируются по режиму и
    переиспользуются между сессиями. Без кэша смена клиента/канала в сайдбаре
    перезагружала бы индекс и реинициализировала GigaChat на каждое переключение.
    Сами deps без состояния, поэтому шарить их между сессиями безопасно.
    """
    return build_deps(mode)


def reset_session(mode: str, channel: str, client_id: str | None) -> None:
    """Начать новую сессию: новый thread_id, чистая история, свежий граф+checkpointer.
    Граф пересобирается дёшево (новый MemorySaver) поверх кэшированных deps."""
    st.session_state.thread_id = f"ui-{uuid.uuid4().hex[:12]}"
    st.session_state.history = []
    st.session_state.seeded = False
    st.session_state.mode = mode
    st.session_state.channel = channel
    st.session_state.client_id = client_id
    st.session_state.pop("pending", None)
    try:
        st.session_state.graph = build_graph(get_deps(mode))
        st.session_state.graph_error = None
    except Exception as exc:  # noqa: BLE001 — показать пользователю причину
        st.session_state.graph = None
        st.session_state.graph_error = str(exc)


def stream_turn(question: str):
    """
    Прогнать один ход через граф в режиме updates: на каждый отработавший узел
    yield'им (имя_узла, накопленное_итоговое_состояние). Последнее значение —
    финальное состояние хода.
    """
    graph = st.session_state.graph
    config = {"configurable": {"thread_id": st.session_state.thread_id}}

    if not st.session_state.seeded:
        # Первый ход: полное стартовое состояние (резолвит канал → доступ).
        state = make_initial_state(
            channel=st.session_state.channel,
            session_client_id=st.session_state.client_id,
            question=question,
            session_id=st.session_state.thread_id,
        )
        st.session_state.seeded = True
    else:
        # Последующие ходы: только новая реплика + сброс эфемерных полей.
        state = {**TURN_RESET, "messages": [HumanMessage(question)]}

    result: dict = {}
    for chunk in graph.stream(state, config=config, stream_mode="updates"):
        for node, delta in chunk.items():
            if isinstance(delta, dict):
                result.update(delta)
            yield node, result


def typewriter(text: str):
    """Генератор для st.write_stream — печатает ответ словами (эффект потока)."""
    for word in text.split(" "):
        yield word + " "
        time.sleep(0.012)


# --- Рендер служебной «изнанки» ответа ----------------------------------------

def _source_label(source: str) -> str:
    """'[1] 01_credit_products.md#2.1.4 (CODE)' → '01_credit_products.md#2.1.4 (CODE)'."""
    return source.split("] ", 1)[-1] if "] " in source else source


def render_meta(result: dict) -> None:
    """Развёрнутые блоки под ответом ассистента: маршрут, источники, данные, эскалация."""
    outcome = result.get("outcome_type")
    icon, label, color = OUTCOME_BADGE.get(outcome, ("•", outcome or "—", "gray"))
    category = result.get("category") or "—"
    st.markdown(f":{color}-badge[{icon} {label}] :gray-badge[категория: {category}]")

    # Компактный трейс реального маршрута по графу.
    route = result.get("_route") or []
    if route:
        trace = " → ".join(f"{ROUTE_ICONS.get(n, '•')} {n}" for n in route)
        st.caption(f"Маршрут: {trace}")

    sources = result.get("sources") or []
    tool_results = result.get("tool_results") or {}
    escalation = result.get("escalation")

    # Компактные чипы источников прямо под ответом (топ-3).
    if sources:
        chips = " ".join(
            f":gray-badge[{'🎯' if s.get('scope') == 'specific' else '📄'} "
            f"{_source_label(s.get('source', ''))}]"
            for s in sources[:3]
        )
        st.markdown(chips)

    if escalation:
        trigger = escalation.get("trigger")
        with st.expander(f"🚨 Пакет эскалации оператору — {TRIGGER_LABEL.get(trigger, trigger)}", expanded=True):
            st.markdown(f"**Сводка (≤500):** {escalation.get('summary') or '—'}")
            cols = st.columns(3)
            cols[0].metric("Триггер", trigger or "—")
            cols[1].metric("Клиент", escalation.get("client_id") or "аноним")
            cols[2].metric("Реплик в диалоге", len(escalation.get("dialog_history") or []))
            markers = escalation.get("negative_markers") or []
            if markers:
                st.markdown("**Маркеры негатива:** " + ", ".join(markers))
            with st.popover("Полный JSON пакета"):
                st.json(escalation)

    if tool_results and not tool_results.get("access_denied"):
        with st.expander("🗂 Данные клиента из БД (tools)"):
            profile = tool_results.get("profile")
            if profile:
                st.markdown(
                    f"**{profile.get('name', profile.get('client_id'))}** · "
                    f"{profile.get('legal_form')} · {profile.get('industry')}  \n"
                    f"Выручка: {profile.get('annual_revenue'):,} ₽/год · "
                    f"счёт в банке: {'да' if profile.get('has_account_in_bank') else 'нет'}".replace(",", " ")
                )
            loans = tool_results.get("loans") or []
            apps = tool_results.get("applications") or []
            st.caption(f"Действующих кредитов: {len(loans)} · Заявок: {len(apps)}")
            elig = tool_results.get("eligible_products")
            tabs = st.tabs(["Кредиты", "Заявки"] + (["Подбор продуктов"] if elig else []))
            with tabs[0]:
                st.json(loans) if loans else st.caption("Нет действующих кредитов.")
            with tabs[1]:
                st.json(apps) if apps else st.caption("Нет заявок.")
            if elig:
                with tabs[2]:
                    st.json(elig)

    if tool_results.get("access_denied"):
        st.warning(f"Доступ к данным отклонён: {tool_results.get('reason', '—')}")

    if sources:
        with st.expander(f"📚 Все источники RAG ({len(sources)})"):
            for src in sources:
                scope = src.get("scope")
                tag = "🎯 продуктовый" if scope == "specific" else "📄 общий"
                product = f" · {src['product_code']}" if src.get("product_code") else ""
                st.markdown(
                    f"- `{src.get('source')}` — {tag}{product} · score={src.get('score')}"
                )


# --- UI -----------------------------------------------------------------------

st.set_page_config(page_title="Поддержка кредитования МСБ", page_icon="🏦", layout="centered")
st.markdown("## 🏦 Помощник по кредитованию МСБ")
st.caption("Учебный агент: RAG по нормативке + доступ к БД клиента + эскалация на оператора.")

# --- Сайдбар: режим, канал, клиент, управление сессией ---
with st.sidebar:
    st.header("Параметры сессии")

    mode_label = st.radio(
        "Режим",
        ["Боевой (GigaChat)", "Оффлайн (заглушки)"],
        index=0,  # по умолчанию боевой: реальные ответы и источники
        help="Боевой — реальный Retriever + GigaChat (нужны ключ и индекс), "
             "настоящие ответы и цитаты. Оффлайн — без GigaChat/RAG: rule-based "
             "классификатор + шаблон, проверяет ТОЛЬКО маршрутизацию (ответы и "
             "источники ненастоящие).",
    )
    mode = "gigachat" if mode_label.startswith("Боевой") else "stub"
    if mode == "stub":
        st.warning(
            "Оффлайн-режим: ответы шаблонные, источники — заглушка (FakeRetriever). "
            "Для оценки качества ответов переключитесь на **Боевой (GigaChat)**.",
            icon="⚠️",
        )

    channel = st.selectbox(
        "Канал обращения",
        list(CHANNELS),
        format_func=lambda c: CHANNELS[c],
    )
    is_authorized = channel in AUTHORIZED_CHANNELS

    client_id: str | None = None
    if is_authorized:
        clients = load_scenario_clients()
        if clients:
            options = [c[0] for c in clients]
            labels = {c[0]: c[1] for c in clients}
            client_id = st.selectbox(
                "Клиент (авторизован)",
                options,
                format_func=lambda c: labels.get(c, c),
            )
        else:
            client_id = st.text_input("client_id", value="C-000004")
        st.success(f"Уровень доступа: **basic** · {client_id}")
    else:
        st.info("Уровень доступа: **anonymous** — личные данные недоступны.")

    st.divider()
    start_new = st.button("🔄 Начать новую сессию", use_container_width=True)
    dev_mode = st.toggle(
        "🛠 Режим разработчика",
        value=False,
        help="Показывать «изнанку» под ответом: маршрут по графу, категорию, "
             "источники RAG, данные клиента из БД, пакет эскалации. "
             "Выкл — чистый клиентский чат (только ответ).",
    )

# Инициализация / пересборка сессии при первом запуске или смене параметров.
params_changed = (
    "graph" not in st.session_state
    or st.session_state.get("mode") != mode
    or st.session_state.get("channel") != channel
    or st.session_state.get("client_id") != client_id
)
if start_new or params_changed:
    reset_session(mode, channel, client_id)

if st.session_state.get("graph_error"):
    st.error(
        "Не удалось собрать боевой граф (GigaChat). Проверь `GIGACHAT_CREDENTIALS` "
        f"и индекс `rag/index`. Подробности:\n\n```\n{st.session_state.graph_error}\n```\n"
        "Можно переключиться на режим «Оффлайн (заглушки)» в сайдбаре."
    )

# История диалога.
for entry in st.session_state.get("history", []):
    avatar = "🏦" if entry["role"] == "assistant" else "🧑‍💼"
    with st.chat_message(entry["role"], avatar=avatar):
        st.markdown(entry["content"])
        if entry["role"] == "assistant" and entry.get("result") and dev_mode:
            render_meta(entry["result"])

# Ввод: поле + (по клику) пример из онбординга.
disabled = st.session_state.get("graph") is None
pending = st.session_state.pop("pending", None)
typed = st.chat_input("Спросите про кредит, ставку, заявку…", disabled=disabled)
prompt = pending or typed

# Пустой экран — онбординг с примерами.
if not st.session_state.get("history") and not prompt:
    st.markdown("#### 👋 С чего начать")
    st.caption("Выберите пример или задайте свой вопрос в поле ниже.")
    cols = st.columns(2)
    for i, example in enumerate(EXAMPLE_PROMPTS):
        if cols[i % 2].button(example, use_container_width=True, key=f"ex_{i}", disabled=disabled):
            st.session_state.pending = example
            st.rerun()

# Прогон хода.
if prompt:
    st.session_state.history.append({"role": "user", "content": prompt})
    with st.chat_message("user", avatar="🧑‍💼"):
        st.markdown(prompt)

    with st.chat_message("assistant", avatar="🏦"):
        result: dict = {}
        route: list[str] = []
        try:
            started = time.perf_counter()
            with st.status("Агент работает…", expanded=True) as status:
                for node, result in stream_turn(prompt):
                    if not route or route[-1] != node:
                        route.append(node)
                    status.write(NODE_LABELS.get(node, node))
                elapsed = time.perf_counter() - started
                status.update(label=f"✅ Готово за {elapsed:.1f} с", state="complete", expanded=False)
            if result:
                result["_route"] = route
            answer = result.get("answer") or "_(пустой ответ)_"
            st.write_stream(typewriter(answer))
        except Exception as exc:  # noqa: BLE001
            result, answer = {}, f"⚠️ Ошибка прогона графа: {exc}"
            st.markdown(answer)
        if result and dev_mode:
            render_meta(result)

    st.session_state.history.append(
        {"role": "assistant", "content": answer, "result": result or None}
    )
    st.rerun()
