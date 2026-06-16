# Наблюдаемость графа — Arize Phoenix

Трейсинг прогона LangGraph и вложенных вызовов GigaChat через Phoenix
(OpenTelemetry / OpenInference). Видно дерево узлов графа (classify → retrieve_rag →
query_db → generate → escalate) с раскрытием промптов, ответов, латентности и токенов.

## Установка (один раз)

Зависимости Phoenix входят в общий `requirements.txt` — отдельной установки не нужно:

```bash
pip install -r requirements.txt
```

## Запуск (два терминала)

**Терминал 1 — Phoenix UI + коллектор:**
```bash
python -m phoenix.server.main serve
# UI: http://localhost:6006
```

**Терминал 2 — приложение с включённым трейсингом:**
```bash
PHOENIX_TRACING=1 streamlit run ui/app.py
```
или для прогона оценки:
```bash
PHOENIX_TRACING=1 python evaluate.py --mode gigachat
```

Открой **http://localhost:6006**, выбери проект **`msb-agent`** — каждый запрос
отображается как трейс-дерево.

## Как это устроено

- `agent/tracing.py::setup_tracing()` — подключается, только если задана
  `PHOENIX_TRACING=1` (иначе no-op; обычный прогон не затрагивается). Идемпотентна
  (Streamlit перезапускает скрипт на каждом ходе).
- Инструментируется **LangChain** (`auto_instrument=True`): GigaChat — это LangChain
  ChatModel, а LangGraph эмитит колбэки LangChain, поэтому в трейсы попадают и узлы
  графа, и сами LLM-запросы.
- UI вызывает `setup_tracing()` при старте (см. `ui/app.py`). Для других точек входа
  (evaluate.py, свои скрипты) — вызвать `setup_tracing()` в начале.

## Переменные окружения

| Переменная | Назначение | По умолчанию |
|---|---|---|
| `PHOENIX_TRACING` | включить трейсинг (`1`) | выкл |
| `PHOENIX_COLLECTOR_ENDPOINT` | адрес Phoenix-коллектора | `http://localhost:6006` |

> Трейсинг — для отладки/демо. В обычной работе переменную не ставят, и накладных
> расходов нет.
