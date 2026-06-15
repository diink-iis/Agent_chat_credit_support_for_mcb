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

import base64
import json
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
LOGO_PATH = REPO_ROOT / "ui" / "assets" / "logo.png"      # зелёный «росток» (бренд/hero)
FAVICON_PATH = REPO_ROOT / "ui" / "assets" / "favicon.png"  # чёрный «росток» для вкладки


@st.cache_data(show_spinner=False)
def logo_data_uri() -> str:
    """Логотип как data-URI для встраивания в брендовый блок (без обращения к диску в HTML)."""
    if not LOGO_PATH.exists():
        return ""
    return "data:image/png;base64," + base64.b64encode(LOGO_PATH.read_bytes()).decode()


# Персистентность диалогов между запусками: видимая переписка — в JSON, память графа
# (multi-turn контекст по thread_id) — в SQLite-checkpointer'е. Оба переживают рестарт.
SESS_DIR = REPO_ROOT / "ui" / ".sessions"
CONV_FILE = SESS_DIR / "conversations.json"
CHECKPOINT_FILE = SESS_DIR / "checkpoints.sqlite"

# Ключи итогового состояния, которые нужны для отрисовки «изнанки» и безопасны для JSON
# (без объектов-сообщений LangChain, которые лежат в result["messages"]).
META_KEYS = ("outcome_type", "category", "_route", "sources", "tool_results", "escalation")

# Каналы: первые два — авторизованные (доступны личные данные), остальные — анонимные.
CHANNELS = {
    "chat_intern": "Чат в интернет-банке (авторизован)",
    "mobile": "Мобильное приложение (авторизован)",
    "chat_site": "Чат на сайте (аноним)",
    "contact_center": "Контакт-центр (аноним)",
}
AUTHORIZED_CHANNELS = {"chat_intern", "mobile"}

# Сессия по умолчанию для клиентского вида (без «Режима разработчика»): клиент уже
# авторизован в бизнес-кабинете банка, как если бы вошёл по логину.
DEFAULT_MODE = "gigachat"
DEFAULT_CHANNEL = "chat_intern"
DEFAULT_CLIENT = "C-000001"

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


@st.cache_resource
def get_checkpointer():
    """SQLite-checkpointer (один на процесс): память графа по thread_id переживает
    рестарт сервера. check_same_thread=False — Streamlit работает из разных потоков."""
    from langgraph.checkpoint.sqlite import SqliteSaver

    SESS_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(CHECKPOINT_FILE), check_same_thread=False)
    saver = SqliteSaver(conn)
    saver.setup()
    return saver


@st.cache_resource(show_spinner="Загружаю Retriever и GigaChat…")
def get_graph(mode: str):
    """
    Скомпилированный граф с ПЕРСИСТЕНТНЫМ SQLite-checkpointer'ом — один на режим, живёт
    между диалогами, реранами и рестартами. Каждый диалог = свой thread_id, поэтому
    переключение на прошлый диалог восстанавливает его multi-turn контекст автоматически.
    """
    return build_graph(get_deps(mode), checkpointer=get_checkpointer())


def slim_result(result: dict) -> dict:
    """Оставить только JSON-безопасные мета-поля (без объектов-сообщений) для хранения."""
    return {k: result[k] for k in META_KEYS if k in result}


def save_conversations() -> None:
    """Сохранить список диалогов (переписка + метаданные) на диск."""
    SESS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "active_id": st.session_state.get("active_id"),
        "conversations": st.session_state.get("conversations", {}),
    }
    CONV_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_conversations() -> tuple[dict, str | None]:
    """Загрузить диалоги с диска (или пустое состояние при первом запуске/ошибке)."""
    if CONV_FILE.exists():
        try:
            data = json.loads(CONV_FILE.read_text(encoding="utf-8"))
            return data.get("conversations", {}), data.get("active_id")
        except (json.JSONDecodeError, OSError):
            return {}, None
    return {}, None


def new_conversation(mode: str, channel: str, client_id: str | None) -> dict:
    """Создать новый диалог и сделать его активным. Прошлые остаются в списке."""
    conv = {
        "id": f"conv-{uuid.uuid4().hex[:8]}",
        "thread_id": f"ui-{uuid.uuid4().hex[:12]}",
        "title": "Новый диалог",
        "mode": mode,
        "channel": channel,
        "client_id": client_id,
        "seeded": False,
        "history": [],   # [{role, content, result}] — для отрисовки
    }
    st.session_state.conversations[conv["id"]] = conv
    st.session_state.active_id = conv["id"]
    st.session_state.pop("pending", None)
    save_conversations()
    return conv


def stream_turn(conv: dict, question: str):
    """
    Прогнать один ход активного диалога через граф в режиме updates: на каждый
    отработавший узел yield'им (имя_узла, накопленное_итоговое_состояние).
    Память диалога живёт в checkpointer'е графа по conv["thread_id"].
    """
    graph = get_graph(conv["mode"])
    config = {"configurable": {"thread_id": conv["thread_id"]}}

    if not conv["seeded"]:
        # Первый ход: полное стартовое состояние (резолвит канал → доступ).
        state = make_initial_state(
            channel=conv["channel"],
            session_client_id=conv["client_id"],
            question=question,
            session_id=conv["thread_id"],
        )
        conv["seeded"] = True
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

def _favicon():
    """Иконка вкладки — чёрный «росток» из pptx. PIL-объект надёжнее пути-строки для page_icon."""
    if FAVICON_PATH.exists():
        try:
            from PIL import Image
            return Image.open(FAVICON_PATH)
        except Exception:  # noqa: BLE001
            return str(FAVICON_PATH)
    return "🌱"


st.set_page_config(page_title="МСБ.ai · кредитный ассистент",
                   page_icon=_favicon(), layout="centered")

# Порт дизайн-системы референса (ui/ref/МСБ-Ассистент): эмеральд-зелёный, Inter,
# белые карточки с мягкими тенями, «орб»-логотип, пузыри с accent-tint.
st.markdown(
    """
    <style>
      /* === Порт дизайн-системы референса (ui/ref) === */
      @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
      :root{
        --bg-app:#f4f7f4; --surface:#ffffff; --sidebar:#fbfcfb;
        --border:rgba(16,42,28,.085); --border-strong:rgba(16,42,28,.14);
        --text:#15201a; --text-2:#586259; --text-3:#8b958d;
        --accent:#0F9650; --accent-dark:#0C7A44;
        --accent-tint:#ECF6F0; --accent-tint-2:#DBEFE4; --accent-line:#C1E4D2;
        --radius:12px;
        --shadow-sm:0 1px 2px rgba(16,42,28,.05);
        --shadow-md:0 4px 16px rgba(16,42,28,.07);
      }
      html, body, .stApp, button, input, textarea,
      [data-testid="stMarkdownContainer"], [data-testid="stChatInput"] textarea{
        font-family:'Inter', system-ui, -apple-system, sans-serif; }
      /* НЕ трогаем шрифт Material-иконок (иначе ":material/add:" печатается как "add") */
      [data-testid="stIconMaterial"]{ font-family:'Material Symbols Rounded' !important; }
      .stApp{ background:var(--bg-app); color:var(--text); }
      /* Диалог и поле ввода — одинаковой ширины: одинаковый боковой отступ на обоих контейнерах */
      .stApp [data-testid="stMainBlockContainer"], .block-container,
      [data-testid="stBottomBlockContainer"]{
        max-width:none !important; padding-left:7rem !important; padding-right:7rem !important; }
      .block-container{ padding-top:1.4rem !important; padding-bottom:5rem !important; }
      /* Свёрнутый сайдбар: main шире на ~ширину сайдбара — добавляем отступ, чтобы диалог не растягивался */
      .stApp:has([data-testid="stExpandSidebarButton"]) [data-testid="stMainBlockContainer"],
      .stApp:has([data-testid="stExpandSidebarButton"]) .block-container,
      .stApp:has([data-testid="stExpandSidebarButton"]) [data-testid="stBottomBlockContainer"]{
        padding-left:16rem !important; padding-right:16rem !important; }
      [data-testid="stDecoration"]{ background-image:none; background:var(--accent); }

      /* Рабочая область — отдельный белый блок со скруглением 12px (как по референсу).
         У stMain emotion задаёт width:100% и height:100dvh, поэтому ширину/высоту
         пересчитываем через calc() с учётом полей — иначе правый/нижний край уходят за экран. */
      .stApp section.stMain, .stApp [data-testid="stMain"]{
        background:var(--surface) !important; border:1px solid var(--border-strong) !important;
        border-radius:12px !important; box-shadow:var(--shadow-lg) !important;
        width:calc(100% - 24px) !important; height:calc(100dvh - 24px) !important;
        margin:12px !important; }
      /* нижняя панель (поле ввода) — прозрачная, чтобы был виден белый блок */
      [data-testid="stBottom"], [data-testid="stBottom"] > div,
      [data-testid="stBottomBlockContainer"]{ background:transparent !important; }

      /* Сайдбар сливается с фоном — без границ и карточки (как в референсе).
         Единственный выделенный блок-карточка — область чата (main). */
      section[data-testid="stSidebar"]{
        background:var(--bg-app) !important; border:none !important;
        box-shadow:none !important; position:relative !important; }
      /* Кнопка сворачивания — внутрь карточки (верх-право), а не за её краем */
      [data-testid="stSidebarCollapseButton"]{ position:absolute !important; top:8px; right:8px;
        z-index:5; margin:0 !important; }
      /* Сайдбар: схлопываем верхнюю шапку и паддинг, чтобы бренд был у самого верха */
      [data-testid="stSidebarHeader"]{ height:0 !important; min-height:0 !important; padding:0 !important; }
      [data-testid="stSidebarUserContent"]{ padding-top:6px !important; }
      section[data-testid="stSidebar"] [data-testid="stSidebarContent"]{ padding-top:0 !important; }
      /* разделитель под брендом — выше и компактнее, без лишнего воздуха */
      section[data-testid="stSidebar"] hr{ margin:6px 0 10px !important; }

      /* Бренд: лого-«росток» + вордмарк «МСБ.ai» + подпись (по центру, как на слайде) */
      /* бренд закреплён слева — не «бегает» при изменении ширины сайдбара.
         align-items:center + лёгкий подъём лого: его центр совпадает с центром
         вордмарка «МСБ.ai», а не со всем блоком (иначе росток визуально ниже текста). */
      .brand{ display:flex; align-items:center; justify-content:flex-start; gap:11px; margin:0 0 4px; }
      .brand-logo{ width:40px; height:40px; flex:0 0 auto; object-fit:contain;
        transform:translateY(-5px); }
      .brand-text{ display:flex; flex-direction:column; line-height:1.05; }
      .brand-name{ font-weight:800; font-size:23px; letter-spacing:-.02em; color:var(--text); }
      .brand-name span{ color:var(--accent); }
      .brand-sub{ font-size:10px; font-weight:600; letter-spacing:.1em; text-transform:uppercase;
        color:var(--text-3); margin-top:2px; }

      /* Поле поиска по диалогам */
      section[data-testid="stSidebar"] [data-testid="stTextInput"] input{
        background:var(--surface) !important; border:1px solid var(--border) !important;
        border-radius:10px !important; font-size:13.5px !important; color:var(--text) !important; }
      section[data-testid="stSidebar"] [data-testid="stTextInput"] input:focus{
        border-color:var(--accent-line) !important; box-shadow:none !important; }

      /* Кнопки сайдбара — плоские строки навигации */
      section[data-testid="stSidebar"] .stButton button{
        border:1px solid transparent; background:none; box-shadow:none; color:var(--text-2);
        font-weight:500; border-radius:10px; justify-content:flex-start; }
      section[data-testid="stSidebar"] .stButton button:hover{
        background:rgba(16,42,28,.045); color:var(--text); border-color:transparent; }
      section[data-testid="stSidebar"] .stButton button p{
        white-space:nowrap; overflow:hidden; text-overflow:ellipsis; text-align:left; }
      /* заголовки диалогов в истории — метка на всю ширину, начало закреплено слева,
         обрезается только конец (text-overflow), поэтому при расширении видно больше с начала */
      section[data-testid="stSidebar"] [data-testid="stHorizontalBlock"] [data-testid="stColumn"]:first-child button,
      section[data-testid="stSidebar"] [data-testid="stHorizontalBlock"] [data-testid="column"]:first-child button{
        justify-content:flex-start !important; text-align:left !important; padding-left:12px !important; }
      section[data-testid="stSidebar"] [data-testid="stHorizontalBlock"] button [data-testid="stMarkdownContainer"]{
        width:100% !important; text-align:left !important; }
      section[data-testid="stSidebar"] [data-testid="stHorizontalBlock"] button p{
        text-align:left !important; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
      /* «Новый диалог» — лёгкая кнопка (карандаш + светлый фон), как в референсе */
      .st-key-newdlg button{ background:var(--surface)!important; border:1px solid var(--border)!important;
        color:var(--text)!important; font-weight:600!important; justify-content:flex-start!important;
        box-shadow:var(--shadow-sm)!important; }
      .st-key-newdlg button:hover{ border-color:var(--accent-line)!important;
        background:var(--surface)!important; color:var(--text)!important; }
      .st-key-newdlg button *{ color:var(--text)!important; }
      /* удаление (последняя колонка строки) — приглушённое, красный hover */
      section[data-testid="stSidebar"] [data-testid="stHorizontalBlock"] [data-testid="stColumn"]:last-child button,
      section[data-testid="stSidebar"] [data-testid="stHorizontalBlock"] [data-testid="column"]:last-child button{ color:var(--text-3); }
      section[data-testid="stSidebar"] [data-testid="stHorizontalBlock"] [data-testid="stColumn"]:last-child button:hover,
      section[data-testid="stSidebar"] [data-testid="stHorizontalBlock"] [data-testid="column"]:last-child button:hover{
        color:#C0392B; background:#FBEAEA; }

      /* Кнопки-подсказки в главной области — карточки */
      .block-container .stButton button{
        border:1px solid var(--border); background:#fff; color:var(--text); border-radius:var(--radius);
        box-shadow:var(--shadow-sm); font-weight:500; padding:13px 16px;
        transition:border-color .14s, box-shadow .14s, transform .12s; }
      .block-container .stButton button:hover{
        border-color:var(--accent-line); box-shadow:var(--shadow-md); transform:translateY(-1px); color:var(--text); }

      /* Реплики: юзер — пузырь у крайне правого края, ассистент — текст слева */
      [data-testid="stChatMessage"]{ background:transparent; padding:6px 0; }
      [data-testid^="stChatMessageAvatar"]{ display:none; }
      /* реплика юзера: пузырь по тексту, прижат к крайне правому краю (флекс) */
      [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]){
        justify-content:flex-end !important; }
      [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"])
        [data-testid="stChatMessageContent"]{
        width:fit-content !important; max-width:82% !important; flex:0 1 auto !important;
        margin-left:auto !important; margin-right:0 !important;
        background:var(--accent-tint) !important; border:1px solid var(--accent-line) !important;
        border-radius:14px !important; padding:10px 16px !important; }
      [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"])
        [data-testid="stChatMessageContent"] *{ margin:0 !important; padding:0 !important; }
      [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"])
        [data-testid="stChatMessageContent"] p{ color:var(--accent-dark); font-size:14.5px; line-height:1.4; }
      /* Ответ ассистента — слева, во всю ширину колонки, без пузыря (как DeepSeek) */
      [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"]){ justify-content:flex-start; }
      [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"])
        [data-testid="stChatMessageContent"]{ background:transparent; border:none; padding:0;
        max-width:100%; min-width:0 !important; margin-right:auto; }
      [data-testid="stChatMessageContent"] p, [data-testid="stChatMessageContent"] li{ line-height:1.6; font-size:14.5px; }

      /* Индикатор «агент думает» — три бегающие точки (как в референсе) */
      .typing{ display:flex; gap:6px; padding:6px 2px; }
      .typing i{ width:8px; height:8px; border-radius:50%; background:var(--accent); opacity:.4;
        animation:typedot 1.2s infinite; }
      .typing i:nth-child(2){ animation-delay:.18s; }
      .typing i:nth-child(3){ animation-delay:.36s; }
      @keyframes typedot{ 0%,60%,100%{ opacity:.3; transform:translateY(0); }
        30%{ opacity:1; transform:translateY(-5px); } }

      /* Поле ввода — карточка-композер с фокус-акцентом */
      [data-testid="stChatInput"]{ background:#fff; border:1px solid var(--border);
        border-radius:16px; box-shadow:var(--shadow-md); }
      [data-testid="stChatInput"]:focus-within{ border-color:var(--accent-line);
        box-shadow:0 6px 22px rgba(15,150,80,.14); }

      /* Пустой экран — hero с лого-«ростком» и градиентным заголовком */
      .hero{ text-align:center; margin:8vh 0 26px; }
      .hero-orb{ width:72px; height:72px; margin:0 auto 18px; object-fit:contain;
        filter:drop-shadow(0 8px 18px rgba(15,150,80,.22)); }
      .hero-title{ font-size:clamp(26px,3vw,36px); font-weight:700; color:var(--text);
        letter-spacing:-.025em; line-height:1.12; }
      .hero-title span{ background:linear-gradient(95deg,var(--accent),#3FB873);
        -webkit-background-clip:text; background-clip:text; color:transparent; }
      .hero-sub{ font-size:15px; color:var(--text-2); margin-top:9px; }
    </style>
    """,
    unsafe_allow_html=True,
)

# Свёрнутый сайдбар: показываем лого-«росток» рядом с кнопкой «развернуть».
# (отдельная инъекция — нужен data-URI логотипа)
st.markdown(
    f"""<style>
      [data-testid="stExpandSidebarButton"]{{ display:inline-flex !important; align-items:center;
        gap:8px; width:auto !important; }}
      [data-testid="stExpandSidebarButton"]::before{{
        content:""; width:28px; height:28px; flex:0 0 auto; order:-1;
        background:url("{logo_data_uri()}") center/contain no-repeat; }}
    </style>""",
    unsafe_allow_html=True,
)

ss = st.session_state
if "conversations" not in ss:        # первый запуск сессии — поднимаем диалоги с диска
    ss.conversations, ss.active_id = load_conversations()

# --- Сайдбар: бренд + «Новый диалог»; параметры сессии — только в режиме разработчика ---
with st.sidebar:
    st.markdown(
        f'<div class="brand">'
        f'<img class="brand-logo" src="{logo_data_uri()}" alt="МСБ.ai"/>'
        f'<div class="brand-text">'
        f'<div class="brand-name">МСБ<span>.ai</span></div>'
        f'<div class="brand-sub">Кредитный ассистент</div>'
        f'</div></div>',
        unsafe_allow_html=True,
    )

# Клиентский вид: фиксированная авторизованная сессия (как вход в бизнес-кабинет).
# Служебная «изнанка» под ответами выключена всегда.
mode = DEFAULT_MODE
channel = DEFAULT_CHANNEL
client_id = DEFAULT_CLIENT
dev_mode = False

# --- Резолвим активный диалог. Прошлые не теряются: лежат в ss.conversations. ---
active = ss.conversations.get(ss.active_id)
if active is None:
    active = new_conversation(mode, channel, client_id)
elif not active["history"]:
    # Активный диалог ещё пуст — просто подхватываем текущие параметры (dev может менять).
    active.update(mode=mode, channel=channel, client_id=client_id)
elif (active["channel"], active["client_id"]) != (channel, client_id):
    # Сменили ИДЕНТИЧНОСТЬ (канал/клиент) на непустом диалоге — это новый диалог.
    active = new_conversation(mode, channel, client_id)
else:
    # Та же идентичность — допускаем лишь смену режима LLM внутри текущего диалога.
    active["mode"] = mode

# Прогреваем граф для режима активного диалога; ошибку (нет ключа GigaChat) показываем.
graph_error = None
try:
    get_graph(active["mode"])
except Exception as exc:  # noqa: BLE001
    graph_error = str(exc)

# --- Сайдбар (продолжение): «Новый диалог» + история диалогов ---
with st.sidebar:
    st.divider()
    if st.button("Новый диалог", icon=":material/edit:",
                 use_container_width=True, key="newdlg"):
        # Создаём свежий диалог (если текущий уже непустой), старый остаётся в списке.
        if active["history"]:
            new_conversation(mode, channel, client_id)
            st.rerun()
    query = st.text_input("Поиск по диалогам", placeholder="Поиск по диалогам…",
                          label_visibility="collapsed", key="convsearch").strip().lower()
    st.caption("История диалогов")
    convs = list(ss.conversations.values())

    def _matches(c: dict) -> bool:
        """Совпадение по заголовку или по тексту любой реплики (без учёта регистра)."""
        if not query:
            return True
        if query in c["title"].lower():
            return True
        return any(query in (m.get("content") or "").lower() for m in c["history"])

    shown = [c for c in convs if _matches(c)]
    if not query and len(convs) == 1 and not convs[0]["history"]:
        st.caption("Пока пусто — начните диалог.")
    elif query and not shown:
        st.caption("Ничего не найдено.")
    else:
        # Единый список: все строки — кнопки одинаковой формы; активная подсвечена
        # через её st-key-класс (Streamlit вешает .st-key-<key> на контейнер виджета).
        if active:
            st.markdown(
                f"<style>.st-key-{active['id']} button{{background:var(--accent-tint)!important;"
                f"color:var(--accent-dark)!important;font-weight:600!important;"
                f"justify-content:flex-start!important;text-align:left!important;"
                f"padding-left:12px!important;}}</style>",
                unsafe_allow_html=True,
            )
        for conv in reversed(shown):  # новые сверху
            row_open, row_del = st.columns([0.82, 0.18])
            if row_open.button(conv["title"], key=conv["id"], use_container_width=True):
                if ss.active_id != conv["id"]:
                    ss.active_id = conv["id"]
                    save_conversations()
                    st.rerun()
            if row_del.button("", key=f"del-{conv['id']}", icon=":material/delete:",
                              use_container_width=True, help="Удалить диалог"):
                ss.conversations.pop(conv["id"], None)
                if ss.active_id == conv["id"]:
                    ss.active_id = next(reversed(ss.conversations), None)
                save_conversations()
                st.rerun()

if graph_error:
    st.error(
        "Не удалось собрать боевой граф (GigaChat). Проверь `GIGACHAT_CREDENTIALS` "
        f"в `.env` и собранный индекс `rag/index`. Подробности:\n\n```\n{graph_error}\n```"
    )

# История активного диалога.
for entry in active["history"]:
    with st.chat_message(entry["role"]):
        st.markdown(entry["content"])
        if entry["role"] == "assistant" and entry.get("result") and dev_mode:
            render_meta(entry["result"])

# Ввод: поле + (по клику) пример из онбординга.
disabled = graph_error is not None
pending = ss.pop("pending", None)
typed = st.chat_input("Спросите про кредит, ставку, заявку…", disabled=disabled)
prompt = pending or typed

# Пустой экран — центрированный hero с примерами (DeepSeek/Qwen-стиль).
if not active["history"] and not prompt:
    st.markdown(
        f'<div class="hero">'
        f'<img class="hero-orb" src="{logo_data_uri()}" alt="МСБ.ai"/>'
        f'<div class="hero-title">Чем помочь <span>по кредитованию</span>?</div>'
        f'<div class="hero-sub">Продукты, ставки, статус заявки, досрочное погашение — спрашивайте</div>'
        f'</div>',
        unsafe_allow_html=True,
    )
    cols = st.columns(2)
    for i, example in enumerate(EXAMPLE_PROMPTS):
        if cols[i % 2].button(example, use_container_width=True, key=f"ex_{i}", disabled=disabled):
            ss.pending = example
            st.rerun()

# Прогон хода.
if prompt:
    active["history"].append({"role": "user", "content": prompt})
    if active["title"] == "Новый диалог":
        # храним полный текст — обрезку по ширине делает CSS (text-overflow:ellipsis),
        # поэтому при расширении сайдбара видно больше текста.
        active["title"] = prompt.strip()
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        result: dict = {}
        route: list[str] = []
        thinking = st.empty()
        try:
            # «Думает» — три бегающие точки, пока крутится граф.
            thinking.markdown('<div class="typing"><i></i><i></i><i></i></div>',
                              unsafe_allow_html=True)
            for node, result in stream_turn(active, prompt):
                if not route or route[-1] != node:
                    route.append(node)
            if result:
                result["_route"] = route
            thinking.empty()
            answer = result.get("answer") or "_(пустой ответ)_"
            st.write_stream(typewriter(answer))
        except Exception as exc:  # noqa: BLE001
            thinking.empty()
            result, answer = {}, f"⚠️ Ошибка прогона графа: {exc}"
            st.markdown(answer)
        if result and dev_mode:
            render_meta(result)

    active["history"].append(
        {"role": "assistant", "content": answer, "result": slim_result(result) or None}
    )
    save_conversations()
    st.rerun()
