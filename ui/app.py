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
import re
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

# Наблюдаемость: при PHOENIX_TRACING=1 трейсы графа и вызовов GigaChat уходят в
# Phoenix (UI на http://localhost:6006, запускается отдельно: `phoenix serve`).
# Без переменной — no-op, обычный прогон не затрагивается.
from agent.tracing import setup_tracing
setup_tracing()

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

# Карточки-подсказки на пустом экране (онбординг): (категория, текст-вопрос).
# Текст вопроса по клику уходит в поле ввода. 3 карточки — как в референсе.
EXAMPLE_PROMPTS = [
    ("Продукты", "Какие кредиты вы предлагаете малому бизнесу?"),
    ("Ставки", "Какая минимальная ставка по оборотному кредиту?"),
    ("Оформление", "Хочу оформить Бизнес-Развитие"),
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


# Организационно-правовые префиксы — срезаем их для инициалов аватара.
_LEGAL_PREFIXES = ("ООО", "ОАО", "ЗАО", "ПАО", "АО", "ИП")


def _client_initials(name: str) -> str:
    """Одна буква для аватара (как в референсе): первая буква значимого слова имени
    (без ООО/ИП и кавычек)."""
    s = name.replace('"', "").replace("«", "").replace("»", "").strip()
    for p in _LEGAL_PREFIXES:
        if s.upper().startswith(p + " ") or s.upper() == p:
            s = s[len(p):].strip()
            break
    parts = [w for w in re.split(r"[\s\-]+", s) if w]
    return (parts[0][0] if parts else "К").upper()


@st.cache_data(show_spinner=False)
def client_profile(client_id: str | None) -> dict:
    """Карточка профиля для сайдбара: имя, подпись (форма · регион), инициалы аватара.
    None / отсутствие в БД → гостевой профиль (анонимный доступ)."""
    guest = {"name": "Гость", "sub": "Анонимный доступ", "initials": "Г", "auth": False}
    if not client_id or not DB_PATH.exists():
        return guest
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT name, legal_form, region FROM clients WHERE client_id = ?",
            (client_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return guest
    name, legal_form, region = row
    sub = " · ".join(x for x in (legal_form, region) if x) or client_id
    return {"name": name, "sub": sub, "initials": _client_initials(name), "auth": True}


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
      /* === Порт дизайн-системы референса «Чат редизайн» === */
      @import url('https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&family=Unbounded:wght@500;600;700&display=swap&subset=cyrillic,latin');
      :root{
        --bg-app:#EDF3EF; --surface:#ffffff; --sidebar:#EDF3EF;
        --border:#E6EDE8; --border-strong:#D6EADD;
        --text:#16241C; --text-2:#55695C; --text-3:#8B9A90;
        --accent:#0F9650; --accent-dark:#0A6E3B;
        --accent-tint:#EAF5EE; --accent-tint-2:#D6EADD; --accent-line:#7FC79E;
        --mint-50:#F4FAF6; --mint-100:#EAF5EE; --mint-200:#D6EADD;
        --radius:14px;
        --shadow-sm:0 1px 2px rgba(22,36,28,.04);
        --shadow-md:0 4px 16px rgba(22,36,28,.08);
        --shadow-lg:0 1px 2px rgba(22,36,28,.03), 0 24px 50px -34px rgba(22,36,28,.28);
      }
      html, body, .stApp, button, input, textarea,
      [data-testid="stMarkdownContainer"], [data-testid="stChatInput"] textarea{
        font-family:'Manrope', system-ui, -apple-system, sans-serif; }
      /* брендовые акценты — Unbounded (вордмарк, заголовок героя) */
      .brand-name, .hero-title, .loginwm-name{ font-family:'Unbounded', 'Manrope', sans-serif !important; }
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
        background:var(--surface) !important; border:1px solid var(--border) !important;
        border-radius:24px !important; box-shadow:var(--shadow-lg) !important;
        width:calc(100% - 28px) !important; height:calc(100dvh - 28px) !important;
        margin:14px 14px 14px 0 !important; }
      /* при свёрнутом сайдбаре слева нет сайдбара — добавляем левый отступ, чтобы
         рабочая область не сливалась с краем экрана */
      .stApp:has([data-testid="stExpandSidebarButton"]) section.stMain,
      .stApp:has([data-testid="stExpandSidebarButton"]) [data-testid="stMain"]{
        width:calc(100% - 28px) !important; margin-left:14px !important; }
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
      [data-testid="stSidebarUserContent"]{ padding-top:16px !important; }
      /* всё боковое поле сайдбара переносим на user-content (14px), чтобы absolute-карточка
         профиля с теми же инсетами совпадала по ширине с кнопками/поиском */
      section[data-testid="stSidebar"]{ padding-left:0 !important; padding-right:0 !important; }
      section[data-testid="stSidebar"] [data-testid="stSidebarContent"]{
        padding-top:0 !important; padding-left:0 !important; padding-right:0 !important; }
      /* разделитель под брендом — выше и компактнее, без лишнего воздуха */
      section[data-testid="stSidebar"] hr{ margin:6px 0 10px !important; }

      /* Бренд: лого-«росток» + вордмарк «МСБ.ai» + подпись (по центру, как на слайде) */
      /* бренд закреплён слева — не «бегает» при изменении ширины сайдбара.
         align-items:center + лёгкий подъём лого: его центр совпадает с центром
         вордмарка «МСБ.ai», а не со всем блоком (иначе росток визуально ниже текста). */
      .brand{ display:flex; align-items:center; justify-content:flex-start; gap:11px; margin:0 0 16px; }
      /* лого-плашка: зелёный скруглённый квадрат с белым ростком (как в референсе).
         фильтр-в-белый только на img внутри — иначе перекрасит и сам зелёный квадрат */
      .brand-badge{ width:38px; height:38px; flex:0 0 auto; border-radius:12px;
        background:linear-gradient(150deg,#17A659,#0C7E43); display:flex;
        align-items:center; justify-content:center; box-shadow:0 6px 14px -6px rgba(15,150,80,.5); }
      .brand-badge img{ width:23px; height:23px; object-fit:contain; filter:brightness(0) invert(1); }
      .brand-text{ display:flex; flex-direction:column; line-height:1.05; }
      .brand-name{ font-weight:600; font-size:17px; letter-spacing:-.01em; color:var(--text); }
      .brand-name span{ color:var(--accent); }
      .brand-sub{ font-size:9px; font-weight:700; letter-spacing:.13em; text-transform:uppercase;
        color:var(--text-3); margin-top:5px; }

      /* Профиль — карточка-строка как у диалогов: некликабельная инфо-зона (аватар +
         клиент) + отдельные иконки справа. К низу прижимаем через margin-top:auto,
         а блок-родитель растягиваем на высоту сайдбара (через :has). */
      /* Прижатие к низу: главный вертикальный блок сайдбара (тот, что содержит карточку)
         тянем на всю высоту, а его ПОСЛЕДНИЙ прямой ребёнок (обёртка карточки, какой бы
         testid у неё ни был) толкаем вниз через margin-top:auto. */
      [data-testid="stSidebarUserContent"]{ overflow:visible !important;
        padding-left:14px !important; padding-right:14px !important; }
      section[data-testid="stSidebar"] [data-testid="stVerticalBlock"]:has(.st-key-acctcard){
        min-height:calc(100dvh - 30px) !important;
        display:flex !important; flex-direction:column !important; }
      section[data-testid="stSidebar"] [data-testid="stVerticalBlock"]:has(.st-key-acctcard) > *:last-child{
        margin-top:auto !important; margin-bottom:16px !important; }
      /* сама карточка — flex-строка с центрированием по вертикали */
      section[data-testid="stSidebar"] .st-key-acctcard{
        background:var(--surface) !important; border:1px solid var(--border) !important;
        border-radius:12px !important; box-shadow:var(--shadow-sm) !important;
        padding:7px 8px 7px 11px !important; transition:border-color .14s, box-shadow .14s; }
      section[data-testid="stSidebar"] .st-key-acctcard:hover{
        border-color:var(--accent-line) !important; box-shadow:var(--shadow-md) !important; }
      /* внутренние блоки/колонки прозрачны, по центру по вертикали, без лишних полей/отступов */
      .st-key-acctcard [data-testid="stVerticalBlock"]{ gap:0 !important; background:transparent !important; }
      .st-key-acctcard [data-testid="stHorizontalBlock"]{ gap:4px !important; align-items:center !important; }
      .st-key-acctcard [data-testid="stColumn"]{ align-self:center !important; }
      .st-key-acctcard [data-testid="stElementContainer"]{ margin:0 !important; }
      .st-key-acctcard [data-testid="stMarkdownContainer"]{ margin:0 !important; }
      /* инфо-зона: аватар + имя/подпись (некликабельно) */
      .profile{ display:flex; align-items:center; gap:11px; min-width:0; }
      .profile-av{ width:34px; height:34px; flex:0 0 auto; border-radius:10px;
        background:var(--mint-100); border:1px solid var(--mint-200); color:var(--accent-dark);
        font-weight:700; font-size:14px; display:flex; align-items:center; justify-content:center; }
      .profile-av.guest{ background:#EEF2F0; border-color:#E1E7E3; color:var(--text-3); }
      .profile-txt{ display:flex; flex-direction:column; min-width:0; line-height:1.2; }
      .profile-name{ font-size:13px; font-weight:700; color:var(--text);
        white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
      .profile-sub{ font-size:10.5px; color:var(--text-3);
        white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
      /* иконки справа (сменить клиента / выйти / войти) — как «удалить» у диалога */
      .st-key-acct_switch button, .st-key-acct_logout button, .st-key-acct_login button{
        border:none !important; background:transparent !important; box-shadow:none !important;
        color:var(--text-3) !important; width:32px !important; height:32px !important;
        min-height:0 !important; padding:0 !important; border-radius:8px !important;
        display:flex !important; align-items:center !important; justify-content:center !important; }
      .st-key-acct_switch button:hover, .st-key-acct_login button:hover{
        color:var(--accent-dark) !important; background:var(--accent-tint) !important; }
      .st-key-acct_logout button:hover{ color:#C0392B !important; background:#FBEAEA !important; }

      /* Поле поиска по диалогам — с иконкой-лупой слева (как в референсе) */
      section[data-testid="stSidebar"] [data-testid="stTextInput"] input{
        background:var(--surface)
          url("data:image/svg+xml,%3Csvg%20xmlns='http://www.w3.org/2000/svg'%20width='15'%20height='15'%20viewBox='0%200%2018%2018'%20fill='none'%3E%3Ccircle%20cx='8'%20cy='8'%20r='5.5'%20stroke='%238B9A90'%20stroke-width='1.6'/%3E%3Cpath%20d='M12.5%2012.5%2016%2016'%20stroke='%238B9A90'%20stroke-width='1.6'%20stroke-linecap='round'/%3E%3C/svg%3E")
          no-repeat left 13px center !important;
        border:1px solid transparent !important; border-radius:11px !important;
        padding-left:38px !important; font-size:13px !important; color:var(--text) !important; }
      section[data-testid="stSidebar"] [data-testid="stTextInput"] input:focus{
        border-color:var(--accent-line) !important; box-shadow:0 0 0 4px rgba(15,150,80,.09) !important; }

      /* Заголовок секции истории — капсом, как в референсе */
      .histlabel{ font-size:10px; font-weight:700; letter-spacing:.1em; text-transform:uppercase;
        color:var(--text-3); padding:0 8px; margin:14px 0 8px; }
      /* немного воздуха между «Новый диалог» и поиском (свободнее, как в референсе) */
      .st-key-newdlg{ margin-bottom:8px !important; }

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
        justify-content:flex-start !important; text-align:left !important; padding-left:11px !important;
        gap:10px !important; }
      /* точка-маркёр перед названием диалога (как в референсе) */
      section[data-testid="stSidebar"] [data-testid="stHorizontalBlock"] [data-testid="stColumn"]:first-child button::before,
      section[data-testid="stSidebar"] [data-testid="stHorizontalBlock"] [data-testid="column"]:first-child button::before{
        content:""; width:6px; height:6px; border-radius:50%; background:var(--mint-200);
        flex:0 0 auto; transition:.12s; }
      section[data-testid="stSidebar"] [data-testid="stHorizontalBlock"] button [data-testid="stMarkdownContainer"]{
        width:100% !important; text-align:left !important; }
      section[data-testid="stSidebar"] [data-testid="stHorizontalBlock"] button p{
        text-align:left !important; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
      /* «Новый диалог» — белая кнопка, простой зелёный «+», текст прижат к левому краю */
      .st-key-newdlg button{ background:var(--surface)!important; border:1px solid var(--border)!important;
        color:var(--text)!important; font-weight:600!important; justify-content:flex-start!important;
        gap:9px!important; padding-left:13px!important; box-shadow:var(--shadow-sm)!important; }
      .st-key-newdlg button:hover{ border-color:var(--accent-line)!important;
        background:var(--surface)!important; color:var(--text)!important;
        box-shadow:0 5px 12px -8px rgba(15,150,80,.5)!important; }
      /* содержимое (иконка + метка) к левому краю: margin-right:auto на метке съедает
         всё свободное место справа и сдвигает группу влево независимо от обёртки кнопки */
      .st-key-newdlg button{ display:flex!important; }
      .st-key-newdlg button [data-testid="stMarkdownContainer"]{ margin-right:auto!important; text-align:left!important; }
      .st-key-newdlg button p{ color:var(--text)!important; text-align:left!important; }
      .st-key-newdlg button [data-testid="stIconMaterial"]{
        color:var(--accent)!important; font-size:20px!important; flex:0 0 auto!important; }
      /* удаление (последняя колонка строки) — приглушённое, красный hover */
      section[data-testid="stSidebar"] [data-testid="stHorizontalBlock"] [data-testid="stColumn"]:last-child button,
      section[data-testid="stSidebar"] [data-testid="stHorizontalBlock"] [data-testid="column"]:last-child button{ color:var(--text-3); }
      section[data-testid="stSidebar"] [data-testid="stHorizontalBlock"] [data-testid="stColumn"]:last-child button:hover,
      section[data-testid="stSidebar"] [data-testid="stHorizontalBlock"] [data-testid="column"]:last-child button:hover{
        color:#C0392B; background:#FBEAEA; }

      /* Карточки-подсказки в главной области — белые карточки (как .scard в референсе):
         категория жирная сверху, вопрос приглушённым ниже, на hover — подъём и зелёная рамка */
      .block-container .stButton button{
        border:1px solid var(--border); background:#fff; color:var(--text-3);
        border-radius:16px; box-shadow:none; font-weight:500; font-size:12px; line-height:1.45;
        padding:15px; text-align:left !important; height:116px !important; align-items:flex-start !important;
        transition:border-color .15s, transform .15s, box-shadow .15s; }
      .block-container .stButton button:hover{
        border-color:var(--accent-line); transform:translateY(-2px);
        box-shadow:0 14px 26px -18px rgba(15,150,80,.4); color:var(--text-3); }
      .block-container .stButton button [data-testid="stMarkdownContainer"]{ text-align:left !important; width:100%; }
      .block-container .stButton button p{ text-align:left !important; margin:0 !important; }
      /* первая строка метки (категория) — заголовок карточки */
      .block-container .stButton button strong{ display:block; color:var(--ink, var(--text)); color:var(--text);
        font-family:'Manrope'; font-weight:700; font-size:13.5px; margin-bottom:3px; }

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

      /* Верхняя панель блока — «живой» индикатор + название активного диалога */
      .ptop{ display:flex; align-items:center; gap:9px; margin:-6px 0 16px;
        padding-bottom:14px; border-bottom:1px solid var(--border); }
      .ptop .crumb{ display:flex; align-items:center; gap:9px; font-size:14px;
        font-weight:600; color:var(--text); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
      .ptop .live{ width:7px; height:7px; flex:0 0 auto; border-radius:50%;
        background:var(--accent); box-shadow:0 0 0 3px rgba(15,150,80,.16); }

      /* Пустой экран — hero: мятная плашка-квадрат с ростком + заголовок Unbounded */
      .hero{ text-align:center; margin:6vh 0 24px; display:flex; flex-direction:column; align-items:center; }
      .hero-badge{ width:78px; height:78px; border-radius:22px; margin:0 auto 22px;
        display:flex; align-items:center; justify-content:center;
        background:linear-gradient(150deg,#EAF6EE,#DCEFE3); border:1px solid var(--mint-200);
        box-shadow:0 16px 32px -20px rgba(15,150,80,.45); }
      .hero-badge img{ width:42px; height:42px; object-fit:contain; }
      .hero-title{ font-size:clamp(25px,3vw,32px); font-weight:600; color:var(--text);
        letter-spacing:-.02em; line-height:1.14; }
      .hero-title span{ color:var(--accent); }
      .hero-sub{ font-size:15px; color:var(--text-2); margin-top:11px; }
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
if "auth_client" not in ss:          # кто «вошёл»: client_id или None (вышел = аноним)
    ss.auth_client = DEFAULT_CLIENT


@st.dialog("Вход в бизнес-кабинет")
def login_dialog() -> None:
    """Спросить, под какого сценарного клиента авторизоваться. Демо-замена реального
    логина: список берётся из БД (C-000001…C-000035). Оформление — под референс
    ui/ref/AI МСБ - Окно авторизации (standalone)-2.html."""
    clients = load_scenario_clients()
    if not clients:
        st.error("База клиентов недоступна — войти нельзя.")
        return
    # CSS окна (прицел по :has(.st-key-login_confirm) — только это диалоговое окно).
    # Свой заголовок рисуем сами, штатный h2 от st.dialog прячем.
    st.markdown(
        """<style>
        div[role="dialog"]:has(.st-key-login_confirm){ border-radius:20px !important; }
        /* штатный заголовок st.dialog — это markdown-label в шапке (вне stVerticalBlock);
           прячем его, свой заголовок рисуем ниже. «×» (BaseWeb) остаётся. */
        div[role="dialog"]:has(.st-key-login_confirm)
          [data-testid="stMarkdownContainer"]:not([data-testid="stVerticalBlock"] *){ display:none !important; }
        .loginbrand{ display:flex; align-items:center; gap:10px; margin:0 0 14px; }
        .loginlogo{ width:42px; height:42px; flex:0 0 auto; border-radius:12px;
          background:linear-gradient(150deg,#37A86E,#0F9650); display:flex;
          align-items:center; justify-content:center; box-shadow:0 4px 12px rgba(15,150,80,.24); }
        .loginlogo img{ width:25px; height:25px; object-fit:contain; filter:brightness(0) invert(1); }
        .loginwm-name{ font-size:18px; font-weight:800; letter-spacing:-.02em;
          color:var(--text); line-height:1; }
        .loginwm-name span{ color:var(--accent); }
        .loginwm-sub{ font-size:9px; font-weight:600; letter-spacing:.12em;
          text-transform:uppercase; color:var(--text-3); margin-top:4px; }
        .logintitle{ font-size:24px; font-weight:800; letter-spacing:-.02em;
          color:var(--text); line-height:1.1; margin:0 0 5px; }
        .loginsub{ font-size:13.5px; color:var(--text-2); line-height:1.4; margin:0 0 14px; }
        /* подпись поля */
        .st-key-login_pick label p{ font-size:13px !important; font-weight:700 !important;
          color:var(--text) !important; }
        /* селект — зелёный оттенок (как в референсе), скруглённый, зелёная рамка */
        .st-key-login_pick [data-baseweb="select"] > div{
          border-radius:12px !important; border:1.5px solid var(--accent-line) !important;
          min-height:44px !important; background:var(--accent-tint) !important; box-shadow:none !important; }
        .st-key-login_pick [data-baseweb="select"] > div:focus-within{
          border-color:var(--accent) !important; box-shadow:0 0 0 3px rgba(15,150,80,.12) !important; }
        /* кнопка «Войти» — сплошная зелёная */
        .st-key-login_confirm button{
          background:var(--accent) !important; color:#fff !important; border:none !important;
          border-radius:12px !important; height:48px !important; margin-top:4px !important;
          box-shadow:0 7px 16px rgba(15,150,80,.22) !important; transition:background .14s, box-shadow .14s; }
        .st-key-login_confirm button:hover{
          background:var(--accent-dark) !important; box-shadow:0 9px 20px rgba(15,150,80,.28) !important; }
        .st-key-login_confirm button p{ color:#fff !important; font-weight:700 !important; font-size:15px !important; }
        </style>""",
        unsafe_allow_html=True,
    )
    # Шапка: лого-плашка + вордмарк, заголовок, подзаголовок.
    st.markdown(
        f'<div class="loginbrand">'
        f'<div class="loginlogo"><img src="{logo_data_uri()}" alt="МСБ.ai"/></div>'
        f'<div><div class="loginwm-name">МСБ<span>.ai</span></div>'
        f'<div class="loginwm-sub">Кредитный ассистент</div></div></div>'
        f'<div class="logintitle">Вход в бизнес-кабинет</div>'
        f'<div class="loginsub">Авторизуйтесь, чтобы продолжить работу с кредитным ассистентом.</div>',
        unsafe_allow_html=True,
    )
    labels = [label for _, label in clients]
    # Если повторный вход — предвыбрать прошлого клиента, иначе первого.
    prev = ss.get("last_auth_client", DEFAULT_CLIENT)
    prev_idx = next((i for i, (cid, _) in enumerate(clients) if cid == prev), 0)
    choice = st.selectbox("Выберите клиента", labels, index=prev_idx, key="login_pick")
    st.caption("Демо: авторизация под выбранным сценарным клиентом.")
    clicked = st.button("Войти  →", type="primary", use_container_width=True, key="login_confirm")
    if clicked:
        cid = clients[labels.index(choice)][0]
        ss.auth_client = cid
        ss.last_auth_client = cid
        ss.pop("pending", None)
        # Диалоги нового профиля подхватит резолв активного диалога (свои чаты).
        st.rerun()

# Клиентский вид: авторизация = вход в бизнес-кабинет. «Выход» из профиля делает
# сессию анонимной (chat_site) — личные данные становятся недоступны (см. agent/auth.py).
# Служебная «изнанка» под ответами выключена всегда.
mode = DEFAULT_MODE
client_id = ss.auth_client
channel = DEFAULT_CHANNEL if client_id else "chat_site"
dev_mode = False

# --- Резолвим активный диалог. У каждого профиля (client_id) — своя история. ---
# Диалог самодостаточен: хранит СВОЮ личность (channel/client_id), под которой идут
# его ходы (см. stream_turn). При смене профиля (вход/выход) показываем диалоги нового
# профиля: открываем его последний диалог или заводим свежий. Клик по диалогу внутри
# профиля просто открывает его (список и так отфильтрован по client_id).
active = ss.conversations.get(ss.active_id)
if active is None or active.get("client_id") != client_id:
    own = [c for c in ss.conversations.values() if c.get("client_id") == client_id]
    if own:
        active = own[-1]                 # последний диалог этого профиля
        ss.active_id = active["id"]
    else:
        active = new_conversation(mode, channel, client_id)

# Прогреваем граф для режима активного диалога; ошибку (нет ключа GigaChat) показываем.
graph_error = None
try:
    get_graph(active["mode"])
except Exception as exc:  # noqa: BLE001
    graph_error = str(exc)

# --- Сайдбар: бренд + «Новый диалог» + история + карточка аккаунта (один блок,
# чтобы прижатие карточки к низу было однозначным). ---
with st.sidebar:
    st.markdown(
        f'<div class="brand">'
        f'<span class="brand-badge"><img src="{logo_data_uri()}" alt="МСБ.ai"/></span>'
        f'<div class="brand-text">'
        f'<div class="brand-name">МСБ<span>.ai</span></div>'
        f'<div class="brand-sub">Кредитный ассистент</div>'
        f'</div></div>',
        unsafe_allow_html=True,
    )
    if st.button("Новый диалог", icon=":material/add:",
                 use_container_width=True, key="newdlg"):
        # Создаём свежий диалог (если текущий уже непустой), старый остаётся в списке.
        if active["history"]:
            new_conversation(mode, channel, client_id)
            st.rerun()
    query = st.text_input("Поиск по диалогам", placeholder="Поиск по диалогам…",
                          label_visibility="collapsed", key="convsearch").strip().lower()
    st.markdown('<div class="histlabel">История диалогов</div>', unsafe_allow_html=True)
    # Показываем только диалоги текущего профиля (client_id), а не все вперемешку.
    convs = [c for c in ss.conversations.values() if c.get("client_id") == client_id]

    def _matches(c: dict) -> bool:
        """Совпадение по заголовку или по тексту любой реплики (без учёта регистра)."""
        if not query:
            return True
        if query in c["title"].lower():
            return True
        return any(query in (m.get("content") or "").lower() for m in c["history"])

    shown = [c for c in convs if _matches(c)]
    if not query and len(convs) == 1 and not convs[0]["history"]:
        pass  # пустая история — без подписи-заглушки (чище)
    elif query and not shown:
        st.caption("Ничего не найдено.")
    else:
        # Единый список: все строки — кнопки одинаковой формы; активная подсвечена
        # через её st-key-класс (Streamlit вешает .st-key-<key> на контейнер виджета).
        if active:
            st.markdown(
                f"<style>.st-key-{active['id']} button{{background:#fff!important;"
                f"box-shadow:0 4px 10px -7px rgba(22,36,28,.25)!important;"
                f"color:var(--accent-dark)!important;font-weight:600!important;"
                f"justify-content:flex-start!important;text-align:left!important;"
                f"padding-left:11px!important;gap:10px!important;}}"
                f".st-key-{active['id']} button::before{{background:var(--accent)!important;"
                f"box-shadow:0 0 0 3px rgba(15,150,80,.15)!important;}}"
                f".st-key-{active['id']} button [data-testid=\"stMarkdownContainer\"]{{"
                f"width:100%!important;text-align:left!important;}}"
                f".st-key-{active['id']} button p{{text-align:left!important;}}</style>",
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

    # --- Профиль пользователя: карточка-строка как у диалогов — некликабельная зона
    # с аватаром и клиентом + отдельные иконки справа (как «удалить» у диалога):
    # сменить клиента и выйти (для гостя — войти). ---
    prof = client_profile(client_id)
    _av_cls = "" if prof["auth"] else " guest"
    _open_login = False
    with st.container(key="acctcard"):
        if prof["auth"]:
            info_col, sw_col, out_col = st.columns([0.7, 0.15, 0.15], vertical_alignment="center")
        else:
            info_col, out_col = st.columns([0.82, 0.18], vertical_alignment="center")
            sw_col = None
        info_col.markdown(
            f'<div class="profile">'
            f'<div class="profile-av{_av_cls}">{prof["initials"]}</div>'
            f'<div class="profile-txt">'
            f'<div class="profile-name">{prof["name"]}</div>'
            f'<div class="profile-sub">{prof["sub"]}</div>'
            f'</div></div>',
            unsafe_allow_html=True,
        )
        if prof["auth"]:
            if sw_col.button("", icon=":material/swap_horiz:", key="acct_switch",
                             help="Сменить клиента"):
                _open_login = True
            if out_col.button("", icon=":material/logout:", key="acct_logout",
                              help="Выйти — сессия станет анонимной"):
                ss.last_auth_client = ss.auth_client
                ss.auth_client = None
                ss.pop("pending", None)
                st.rerun()
        else:
            if out_col.button("", icon=":material/login:", key="acct_login",
                              help="Войти — выбрать клиента"):
                _open_login = True
    if _open_login:
        login_dialog()

# Верхняя панель «плавающего» блока: «живой» индикатор + название активного диалога.
_crumb_title = (active["title"] or "Новый диалог").strip()
st.markdown(
    f'<div class="ptop"><span class="crumb"><span class="live"></span>{_crumb_title}</span></div>',
    unsafe_allow_html=True,
)

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
        f'<div class="hero-badge"><img src="{logo_data_uri()}" alt="МСБ.ai"/></div>'
        f'<div class="hero-title">Чем помочь <span>по кредитованию?</span></div>'
        f'<div class="hero-sub">Задайте вопрос или выберите подсказку ниже</div>'
        f'</div>',
        unsafe_allow_html=True,
    )
    cols = st.columns(3)
    for i, (cat, question) in enumerate(EXAMPLE_PROMPTS):
        # Категория-заголовок жирным + вопрос обычным в одной markdown-метке кнопки.
        if cols[i].button(f"**{cat}**  \n{question}", use_container_width=True,
                          key=f"ex_{i}", disabled=disabled):
            ss.pending = question
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
