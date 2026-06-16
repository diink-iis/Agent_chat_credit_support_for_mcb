# Помощник по кредитованию МСБ

AI-агент поддержки клиентов малого и микробизнеса по вопросам кредитования.
Граф **LangGraph + GigaChat**: классификация обращения → RAG по нормативке →
доступ к БД клиента → генерация ответа → эскалация на оператора. Поверх — чат-интерфейс
на Streamlit и трейсинг графа в Arize Phoenix.

## Быстрый старт

```bash
# 1. Зависимости — одним файлом (агент + RAG + UI + Phoenix)
pip install -r requirements.txt

# 2. Ключи GigaChat: скопировать .env.example в .env и подставить свои значения
cp .env.example .env        # затем вписать GIGACHAT_CREDENTIALS

# 3. Собрать RAG-индекс по нормативным документам (нужен .env)
bash setup_rag.sh
```

### Запуск UI + Phoenix

Два терминала из корня репозитория:

```bash
# Терминал 1 — Phoenix (трейсинг графа), UI на http://localhost:6006
python -m phoenix.server.main serve

# Терминал 2 — чат-интерфейс с включённым трейсингом, на http://localhost:8501
PHOENIX_TRACING=1 streamlit run ui/app.py
```

Открой **http://localhost:8501** — чат. Каждый запрос трейсится в Phoenix
(**http://localhost:6006**, проект `msb-agent`): дерево узлов графа, промпты, латентность.

> **Без трейсинга:** просто `streamlit run ui/app.py` (Phoenix не нужен).
> **Без ключей GigaChat:** в сайдбаре выбери режим **Оффлайн (заглушки)** — граф,
> маршрутизация и эскалация работают на правилах и `FakeRetriever`, без `.env` и индекса
> (ответы и источники — ненастоящие).

В сайдбаре выбираются **режим**, **канал** обращения и (для авторизованных каналов) **клиент**.
Тумблер **🛠 Режим разработчика** показывает «изнанку» под ответом: маршрут по графу,
категорию, источники RAG, данные клиента из БД, пакет эскалации. Готовые тестовые запросы —
в [ui/test_queries.md](ui/test_queries.md).

## Что внутри

| Слой | Папка | Назначение |
|---|---|---|
| RAG | [rag/](rag/) | чанкинг нормативки, индекс ChromaDB, retriever (`retrieve(query, k)`) |
| Агент | [agent/](agent/) | граф LangGraph, tools (БД клиентов), состояние, эскалация, промпты |
| UI | [ui/](ui/) | Streamlit-чат поверх графа |
| Оценка | [evaluate.py](evaluate.py) | E2E-метрики по `qa.jsonl` |

Граф проходит узлы `classify → retrieve_rag / query_db → generate_answer → escalate`.
Поверх LLM — детерминированные предохранители (безопасность, грунтинг ответа в нормативке),
чтобы поведение не зависело от стохастики модели.

## Оценка качества

- **E2E-агента** — [`evaluate.py`](evaluate.py), метрики по 8 категориям; методика и
  результаты в [EVALUATION.md](EVALUATION.md). Прогон один раз с кэшем ответов:
  ```bash
  python evaluate.py --mode gigachat            # агент + RAG → кэш eval_runs.json
  python evaluate.py --from-runs --judge        # судья поверх кэша (бесплатный пересчёт)
  ```
- **RAG-retrieval** — [`rag/evaluate_recall.py`](rag/evaluate_recall.py), recall@k;
  итоговый грунтинг по citable-категориям. Подробности в [rag/README.md](rag/README.md).
- **Наблюдаемость** — настройка трейсинга и переменные в [OBSERVABILITY.md](OBSERVABILITY.md).

## Данные

**Документы (RAG).** Пять нормативных регламентов «Банка» в [data/documents/](data/documents/) —
продуктовая линейка, процесс подачи заявки, досрочное погашение, реструктуризация, регламент
работы Помощника (триггеры эскалации, компетенция, конфиденциальность). В них заложены
контролируемые коллизии общего и продуктового правила (приоритет — у продуктового), исключения
для сезонных отраслей и запреты на раскрытие скоринга.

**База клиентов (tools).** [clients.sqlite](data/clients/clients.sqlite) — 385 клиентов,
86 действующих кредитов, 143 заявки. Таблицы `clients` / `credit_products` / `applications`;
схема — [schema.sql](data/generation/schema.sql). Сценарные клиенты `C-000001 — C-000035`
имеют заданные профили и используются в Q&A.

**Q&A-датасет (E2E).** [qa.jsonl](data/qa/qa.jsonl) — 180 кейсов, размеченных по 8 категориям
(`info`, `transactional`, `escalation_sales/negative`, `edge_no_data/conflict/manipulation`,
`offtopic`); 34 кейса — multi-turn. Формат и подкатегории — в [categories.md](data/qa/categories.md).
Поле `referenced_documents` — точные ссылки на пункты нормативки (gold для recall/citation).

## Структура репозитория

```
data/
  documents/         # 5 нормативных документов (RAG-база)
  clients/           # БД клиентов (SQLite) + карта сценарных клиентов
  qa/                # Q&A-датасет для E2E + описание категорий
rag/                 # RAG-слой: чанкинг, индекс ChromaDB, retriever, recall
agent/               # Агент: граф, tools, состояние, эскалация, промпты
ui/                  # Streamlit-интерфейс
evaluate.py          # E2E-оценка
requirements.txt     # все зависимости одним файлом
setup_rag.sh         # сборка RAG-индекса
```
