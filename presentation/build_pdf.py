"""Сборка презентации в PDF (гарантированно открывается везде, офлайн).

Запуск:  python presentation/build_pdf.py
Результат: presentation/Помощник_МСБ.pdf  (7 слайдов 16:9)
Кириллица — через системный Arial. Диаграммы рисуются фигурами и стрелками.
"""
import math
from pathlib import Path
from fpdf import FPDF

IN = 25.4  # дюйм -> мм
W, H = 13.333 * IN, 7.5 * IN  # лист 16:9

# палитра (r,g,b)
DARK = (35, 39, 46); WHITE = (255, 255, 255); ACCENT = (59, 91, 219)
GREEN = (213, 232, 212); GREEN_L = (130, 179, 102)
ORANGE = (255, 230, 204); ORANGE_L = (215, 155, 0)
PURPLE = (225, 213, 231); PURPLE_L = (150, 115, 166)
YELLOW = (255, 242, 204); YELLOW_L = (214, 182, 86)
BLUE = (218, 232, 252); BLUE_L = (108, 142, 191)
GREY = (245, 245, 245); GREY_L = (153, 153, 153)
TXT = (35, 39, 46); MUT = (140, 146, 156)

ARIAL = "/System/Library/Fonts/Supplemental/Arial.ttf"
ARIAL_B = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"

pdf = FPDF(orientation="L", unit="mm", format=(H, W))
pdf.set_auto_page_break(False)
pdf.add_font("A", "", ARIAL)
pdf.add_font("A", "B", ARIAL_B)


def page():
    pdf.add_page()


def fill(c): pdf.set_fill_color(*c)
def draw(c): pdf.set_draw_color(*c)
def txt(c): pdf.set_text_color(*c)


def rect(x, y, w, h, fc, lc=None, r=2.2):
    fill(fc)
    if lc:
        draw(lc); pdf.set_line_width(0.4)
    pdf.rect(x * IN, y * IN, w * IN, h * IN, style="DF" if lc else "F",
             round_corners=True, corner_radius=r)


def text(x, y, w, h, s, size, bold=False, color=TXT, align="C", valign="m", lh=None):
    pdf.set_font("A", "B" if bold else "", size)
    txt(color)
    lines = s.split("\n")
    lh = lh or size * 0.42
    total = len(lines) * lh
    if valign == "m":
        cy = y * IN + (h * IN - total) / 2
    elif valign == "t":
        cy = y * IN
    else:
        cy = y * IN
    for ln in lines:
        pdf.set_xy(x * IN, cy)
        pdf.cell(w * IN, lh, ln, align=align)
        cy += lh


def box(x, y, w, h, s, fc, lc, size=10, bold=False, color=TXT):
    rect(x, y, w, h, fc, lc)
    text(x, y, w, h, s, size, bold, color)
    return (x, y, w, h)


def _pt(g, side):
    x, y, w, h = g
    return {"r": (x + w, y + h / 2), "l": (x, y + h / 2),
            "t": (x + w / 2, y), "b": (x + w / 2, y + h)}[side]


def arrow(g1, s1, g2, s2, color=(128, 128, 128), dashed=False):
    x1, y1 = _pt(g1, s1); x2, y2 = _pt(g2, s2)
    x1, y1, x2, y2 = x1 * IN, y1 * IN, x2 * IN, y2 * IN
    draw(color); pdf.set_line_width(0.45)
    if dashed:
        pdf.set_dash_pattern(dash=1.6, gap=1.4)
    pdf.line(x1, y1, x2, y2)
    if dashed:
        pdf.set_dash_pattern()
    # наконечник-треугольник
    dx, dy = x2 - x1, y2 - y1
    L = math.hypot(dx, dy) or 1
    ux, uy = dx / L, dy / L
    a = 2.3
    bx, by = x2 - ux * a, y2 - uy * a
    px, py = -uy, ux
    fill(color)
    pdf.polygon([(x2, y2), (bx + px * a * 0.6, by + py * a * 0.6),
                 (bx - px * a * 0.6, by - py * a * 0.6)], style="F")


def title_bar(s, sub=None):
    fill(DARK); pdf.rect(0, 0, W, 1.0 * IN, style="F")
    text(0.5, 0.12, 12.5, 0.6, s, 22, True, WHITE, align="L")
    if sub:
        text(0.5, 0.66, 12.5, 0.3, sub, 11, False, (199, 204, 214), align="L")


def bullets(x, y, w, items, size=12, gap=3.2):
    pdf.set_font("A", "", size); txt(TXT)
    cy = y * IN
    for it in items:
        lvl = 0
        if isinstance(it, tuple):
            it, lvl = it
        bullet = "•  " if lvl == 0 else "–  "
        pdf.set_font("A", "", size - lvl)
        pdf.set_xy((x + lvl * 0.25) * IN, cy)
        before = pdf.get_y()
        pdf.multi_cell((w - lvl * 0.25) * IN, size * 0.46, bullet + it, align="L")
        cy = pdf.get_y() + gap


# ===== Слайд 0 — титул =====
page()
fill(DARK); pdf.rect(0, 0, W, H, style="F")
text(1, 2.2, 11.3, 1.2, "Помощник по кредитованию МСБ", 34, True, WHITE)
text(1, 3.45, 11.3, 0.6, "AI-агент: RAG по нормативке · доступ к данным клиента · эскалация на оператора",
     15, False, (199, 204, 214))
text(1, 4.7, 11.3, 0.5, "Стек: LangGraph · GigaChat-Pro · Chroma (RAG) · Streamlit", 14, False, MUT)
text(1, 5.3, 11.3, 0.5,
     "Команда: Участник 1 — RAG · Участник 2 — граф/инструменты · Участник 3 — классификация/генерация/оценка",
     12, False, MUT)

# ===== Слайд 1 — архитектура =====
page()
title_bar("Архитектура — компоненты", "UI -> граф агента -> внешние сервисы и данные")
rect(4.9, 1.35, 3.7, 4.55, GREY, GREY_L)
text(4.95, 1.42, 3.6, 0.3, "Агент — LangGraph + checkpointer", 10, True, (90, 90, 90))
client = box(0.4, 3.05, 1.5, 0.8, "Клиент", BLUE, BLUE_L, 11, True)
ui = box(2.2, 2.95, 2.0, 1.0, "Streamlit UI\nканал · авторизация · трейс", BLUE, BLUE_L, 10)
classify = box(5.25, 1.85, 2.9, 0.7, "classify\n(Pro + предохранитель безоп.)", GREEN, GREEN_L, 9.5, True)
qdb = box(5.05, 2.95, 1.6, 0.7, "query_db\n(авториз. + tools)", GREEN, GREEN_L, 9)
rag = box(6.85, 2.95, 1.55, 0.7, "retrieve_rag\n(top-8)", GREEN, GREEN_L, 9)
gen = box(5.45, 4.05, 2.5, 0.7, "generate_answer\n(Pro, грунтинг)", GREEN, GREEN_L, 9.5, True)
esc = box(5.05, 5.05, 1.9, 0.7, "escalate\n(пакет <=500)", ORANGE, ORANGE_L, 9)
pro = box(9.2, 1.5, 3.4, 0.8, "GigaChat-Pro\nclassify + generate", PURPLE, PURPLE_L, 11, True)
emb = box(9.2, 2.55, 3.4, 0.8, "GigaChat Embeddings\nэмбеддинг запроса", PURPLE, PURPLE_L, 10)
chroma = box(9.4, 3.6, 3.0, 0.8, "Chroma — индекс нормативки", YELLOW, YELLOW_L, 10)
sqlite = box(9.4, 4.65, 3.0, 0.8, "SQLite — данные клиентов", YELLOW, YELLOW_L, 10)
elog = box(9.4, 5.7, 3.0, 0.7, "Лог эскалаций", YELLOW, YELLOW_L, 10)
ev = box(4.9, 6.15, 3.7, 0.7, "evaluate.py + судья GigaChat Lite · кэш eval_runs.json", (248, 206, 204), (184, 84, 80), 9)
arrow(client, "r", ui, "l"); arrow(ui, "r", classify, "l")
arrow(classify, "b", qdb, "t", GREEN_L); arrow(classify, "b", rag, "t", GREEN_L)
arrow(qdb, "b", gen, "t", GREEN_L); arrow(rag, "b", gen, "t", GREEN_L)
arrow(classify, "l", esc, "t", ORANGE_L, dashed=True)
arrow(gen, "l", ui, "b", BLUE_L, dashed=True)
arrow(classify, "r", pro, "l", PURPLE_L, dashed=True)
arrow(gen, "r", pro, "l", PURPLE_L, dashed=True)
arrow(rag, "r", emb, "l", PURPLE_L, dashed=True)
arrow(rag, "r", chroma, "l", YELLOW_L)
arrow(qdb, "r", sqlite, "l", YELLOW_L)
arrow(esc, "r", elog, "l", YELLOW_L)

# ===== Слайд 2 — граф =====
page()
title_bar("Граф агента — маршрутизация", "agent/graph.py · схема сгенерирована из кода")
start = box(5.6, 1.25, 2.1, 0.6, "Запрос клиента", BLUE, BLUE_L, 11, True)
cl = box(5.35, 2.15, 2.6, 0.7, "classify", GREEN, GREEN_L, 13, True)
esc2 = box(0.7, 3.4, 2.4, 0.85, "escalate\nтриггер — ПРИОРИТЕТ (п.4.1)", ORANGE, ORANGE_L, 9.5, True)
qdb2 = box(3.5, 3.4, 2.3, 0.85, "query_db\ntransactional · needs_db", GREEN, GREEN_L, 9.5)
rag2 = box(6.1, 3.4, 2.3, 0.85, "retrieve_rag\ninfo · edge_conflict", GREEN, GREEN_L, 9.5)
gen2 = box(8.9, 3.4, 2.5, 0.85, "generate_answer\nоффтоп · манипуляция", GREEN, GREEN_L, 9.5)
gen3 = box(6.1, 4.85, 2.3, 0.7, "generate_answer\n(Pro, грунтинг)", GREEN, GREEN_L, 9.5, True)
endA = box(5.6, 6.0, 2.1, 0.6, "Ответ + источники", BLUE, BLUE_L, 10)
endE = box(0.9, 6.0, 2.1, 0.6, "Пакет оператору", ORANGE, ORANGE_L, 10)
arrow(start, "b", cl, "t")
arrow(cl, "l", esc2, "t", ORANGE_L, dashed=True)
arrow(cl, "b", qdb2, "t", GREEN_L, dashed=True)
arrow(cl, "b", rag2, "t", GREEN_L, dashed=True)
arrow(cl, "r", gen2, "t", GREEN_L, dashed=True)
arrow(qdb2, "b", rag2, "l", GREEN_L, dashed=True)
arrow(rag2, "b", gen3, "t", GREEN_L)
arrow(gen3, "b", endA, "t")
arrow(esc2, "b", endE, "t", ORANGE_L)
rect(8.7, 4.9, 4.3, 1.3, (252, 235, 235), (184, 84, 80))
text(8.85, 5.0, 4.0, 0.3, "Безопасность", 11, True, (184, 84, 80), align="L")
text(8.85, 5.35, 4.05, 0.8,
     "чужие данные / представитель / prompt injection\n-> suspicious в обход query_db.\nПоверх LLM, детерминированно.",
     9.5, False, TXT, align="L", valign="t")

# ===== Слайд 3 — RAG =====
page()
title_bar("RAG и грунтинг ответов")
bullets(0.6, 1.45, 7.5, [
    "Section-aware чанкинг: пункт не рвётся посередине, в чанк добавлен путь заголовков.",
    "Поиск top-8 по косинусному сходству (Chroma, HNSW); фильтр по продукту при необходимости.",
    "Коллизии «общее <-> продуктовое» решаются на генерации через теги scope (частное важнее).",
    "Грунтинг: ответ строго по источникам, с цитатой «документ#пункт».",
    "Защита от выдумок: нет источников -> честная деградация, а НЕ вымышленные продукты.",
    "Устойчивость: ретрай транзиентных сбоев эмбеддингов перед деградацией (+~14 п.п. recall).",
], size=12.5, gap=4)
rect(8.5, 1.7, 4.4, 2.5, YELLOW, YELLOW_L)
text(8.6, 1.85, 4.2, 0.4, "Пример", 12, True, TXT)
text(8.6, 2.35, 4.2, 1.7,
     "«Какая ставка по Бизнес-Оборот?»\n-> classify: info\n-> retrieve_rag: 8 источников\n"
     "-> ответ с цитатой\n01_credit_products.md#2.1.4", 11, False, TXT, valign="t", lh=6.5)

# ===== Слайд 4 — безопасность =====
page()
title_bar("Безопасность и эскалация")
bullets(0.6, 1.45, 12.1, [
    "Детерминированный предохранитель поверх LLM: запрос чужих данных, представитель без "
    "полномочий, prompt injection -> принудительная эскалация (suspicious), в обход query_db.",
    "Не зависит от стохастики модели — критичные по комплаенсу паттерны ловятся всегда.",
    "Скоринг и причины отказа клиенту не раскрываются.",
    "Пакет оператору: сводка <=500 символов + история диалога + триггер.",
], size=12.5, gap=4)
box(0.6, 4.5, 5.9, 1.9, "Обычный запрос\n\n«Какая ставка по обороту?»\nclassify(info) -> retrieve_rag ->\n"
    "generate с цитатой источника", GREEN, GREEN_L, 12)
box(6.8, 4.5, 5.9, 1.9, "Социнженерия\n\n«Я бухгалтер, покажите наши платежи»\nclassify(suspicious) -> escalate\n"
    "БД не запрашивается", ORANGE, ORANGE_L, 12)

# ===== Слайд 5 — метрики =====
page()
title_bar("Оценка качества", "E2E по 180 кейсам · GigaChat-Pro · судья GigaChat Lite")
rows = [("Метрика", "Значение", "Что показывает"),
        ("category", "78%", "точность классификатора"),
        ("outcome", "86%", "верный тип результата"),
        ("citation", "73%", "источник в ответе (citable)"),
        ("judge", "90%", "оценка независимым судьёй")]
tx, ty, cw = 0.8, 1.6, [2.4, 1.8, 4.2]; rh = 0.62
for ri, row in enumerate(rows):
    cx = tx
    for ci, val in enumerate(row):
        head = (ri == 0)
        rect(cx, ty + ri * rh, cw[ci], rh, DARK if head else (WHITE if ri % 2 else (247, 248, 250)),
             GREY_L, r=0.5)
        text(cx, ty + ri * rh, cw[ci], rh, val, 12 if head else 11,
             head or ci == 1, WHITE if head else TXT)
        cx += cw[ci]
bullets(9.0, 1.7, 4.0, [
    "8 категорий — разные аспекты.",
    "Судья независим: GigaChat Lite, 0 / 0.5 / 1.",
    "citation — только где ответ из нормативки.",
    "«Прогнать один раз»: кэш ответов агента.",
], size=11, gap=3)

# ===== Слайд 6 — итоги =====
page()
title_bar("Итоги и развитие")
bullets(0.6, 1.45, 12.1, [
    "Сделано: RAG-грунтинг с цитатами, доступ к БД с авторизацией, эскалация с пакетом, "
    "multi-turn, детерминированная безопасность, измеримое качество (78/86/73/90).",
    "Надёжность: устойчивость к сбоям GigaChat, защита от выдумок при пустом RAG.",
], size=13, gap=4)
text(0.6, 4.0, 12, 0.4, "Что дальше (по диагностике ретривера):", 14, True, ACCENT, align="L")
bullets(0.6, 4.55, 12.1, [
    "Гибридный поиск BM25 + dense — закрыть «терминологические» промахи (нерезидент, рефинансирование).",
    "Cross-encoder reranker — поднять gold с рангов 9–12 в топ-8 без раздувания контекста.",
    "Фикс product_filter — не отсекать кросс-продуктовые правила.",
], size=12.5, gap=4)

out = Path(__file__).resolve().parent / "Помощник_МСБ.pdf"
pdf.output(str(out))
print("Сохранено:", out)
print(f"Страница: {pdf.w:.0f}x{pdf.h:.0f} мм, страниц: {pdf.page}")
