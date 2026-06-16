# presentation/ — материалы для защиты

Наглядное описание архитектуры для показа преподавателям.

## Файлы

| Файл | Что это | Как открыть |
|---|---|---|
| `architecture_components.drawio` | **Уровень 1** — компоненты системы (UI, граф, GigaChat, Chroma, SQLite, оценка) | draw.io / app.diagrams.net / расширение Draw.io в VS Code |
| `architecture_components.mmd` | Тот же Уровень 1 в Mermaid (источник для слайда) | mermaid.live, или импорт в draw.io |
| `agent_graph_annotated.mmd` | **Уровень 2** — граф агента с условиями на рёбрах | mermaid.live |
| `agent_graph_raw.mmd` | Граф, **авто-сгенерированный из кода** (диаграмма = реализация) | mermaid.live |
| `SLIDES.md` | Структура защиты: 6 слайдов + что говорить | любой редактор |

## Как использовать

**draw.io-файл:**
- Онлайн: открыть https://app.diagrams.net → File → Open → выбрать `.drawio`.
- В VS Code: установить расширение «Draw.io Integration», открыть файл — редактируется прямо в IDE.
- Экспорт в картинку для слайда: File → Export as → PNG/SVG.

**Mermaid (`.mmd`):**
- Быстро посмотреть/выгрузить PNG: вставить содержимое в https://mermaid.live
- В draw.io: Arrange → Insert → Mermaid (вставить текст из `.mmd`).
- В Markdown-слайдах (Marp/Slidev/Obsidian/GitHub) — рендерится как блок ```mermaid.

**Пересобрать авто-граф из кода** (если граф менялся):
```bash
python -c "from agent.graph import build_graph; from agent.llm import make_stub_deps; \
print(build_graph(make_stub_deps()).get_graph().draw_mermaid())" > presentation/agent_graph_raw.mmd
```

## Рекомендация

Сильнее всего на защите работает связка: **1 слайд архитектуры (Уровень 1) + живой
UI-трейс маршрута (демо) + 1 слайд метрик**. Подробности в `SLIDES.md`.
