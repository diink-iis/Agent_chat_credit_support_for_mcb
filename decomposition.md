# Декомпозиция задач на 3 участника

## Участник 1 — RAG-инженер

**Зона ответственности**: документы → векторная база → поиск

| # | Задача | Что сделано |
|---|---|---|
| 1.1 | Стратегия чанкинга 5 документов с сохранением ссылок между пунктами | `chunker.py`: section-aware чанкинг по нумерованным заголовкам, листовая секция целиком, длинные дробятся по абзацам (`max_chars=1200`, `overlap=150`). Итог — `corpus_chunks.jsonl`, 224 чанка |
| 1.2 | Метаданные чанков | В каждом чанке: `doc_id`, `section_id` (совпадает с разметкой `qa.jsonl`), `product_code`, `scope` (`specific`/`general`), `priority`, `title`, `heading_trail` |
| 1.3 | Векторная БД, загрузка корпуса | `vector_store.py` на **ChromaDB** (persistent, косинус) + эмбеддинги **GigaChat** (`embeddings.py`, `langchain-gigachat`). Сборка — `build_index.py` → `rag/index/` |
| 1.4 | Retriever с фильтрацией по метаданным | `retriever.py`: `retrieve(query, product_filter=None, k=5)` — top-k по релевантности; `product_filter` ограничивает поиск продуктом + общими. Плюс `build_query_from_history()` для multi-turn |
| 1.5 | Тест retrieval по `referenced_documents` из `qa.jsonl` (recall@k) | `evaluate_recall.py`: **recall@5 = 56.1% (101/180)**, все 180 кейсов, разбивка по 8 категориям (info 82%, transactional 73%, edge_conflict 89%) |
| 1.6 | Разрешение коллизий: продуктовый регламент > общий | Решается **на генерации** промптом `prompts/collision_priority.md` по тегам `scope`, а не пересортировкой retriever (она роняла recall 56%→43%). На коллизионных запросах в top-k попадают оба пункта |

**Ключевые решения:**
- Embeddings — GigaChat (`Embeddings`), хранилище — ChromaDB; query-эмбеддинги считаются тем же backend'ом.
- Коллизии «частное > общее» вынесены в промпт генерации (теги `scope`/`product_code` в каждом чанке), retriever ранжирует честно по score.
- Multi-turn кейсы (пустой `question`, диалог в `history`) обрабатываются `build_query_from_history()`.
- Низкий recall на поведенческих категориях (offtopic, escalation_sales, manipulation) ожидаем: там gold-ссылка указывает на регламент поведения агента, а не на смысловой ответ — это зона `classify`/`escalate` Участников 2–3.

**Зависимости**: передаёт Участнику 2 готовый retriever (`Retriever.load("rag/index")` → `retrieve(...)`) + метрику recall.

---

## Участник 2 — Tool & Graph инженер

**Зона ответственности**: БД-инструменты + граф LangGraph + состояние диалога

| # | Задача | Результат |
|---|---|---|
| 2.1 | Реализовать 3 SQL-инструмента: `get_client_info`, `get_active_loans`, `get_applications` | Tool-функции с описаниями для LLM |
| 2.2 | Реализовать логику идентификации клиента по каналу: `chat_intern`/`mobile` → auto, `chat_site` → anonymous | Функция `resolve_client(channel, session)` |
| 2.3 | Спроектировать `AgentState`: поля для `client_id`, `channel`, `history`, `escalation_flag`, `retrieved_docs`, `tool_results` | TypedDict / Pydantic-схема стейта |
| 2.4 | Реализовать узлы LangGraph: `classify`, `retrieve_rag`, `query_db`, `generate_answer`, `escalate` | Граф с маршрутизацией |
| 2.5 | Реализовать multi-turn: передача `history` в каждый узел, накопление контекста | Корректная работа на 34 multi-turn кейсах |
| 2.6 | Реализовать узел `escalate`: формировать сводку ≤ 500 символов + передавать метаданные (п. 5.1 РП-ОБ-005) | Структура `EscalationPayload` |

**Зависимости**: получает retriever от Участника 1, передаёт граф Участнику 3 для оценки.

---

## Участник 3 — Prompt-инженер & Evaluator

**Зона ответственности**: промпты + классификатор + E2E-оценка

| # | Задача | Результат |
|---|---|---|
| 3.1 | Написать системный промпт агента: роль, ограничения (п. 6.2 РП-ОБ-005), тон (п. 9) | `system_prompt.md` |
| 3.2 | Разработать промпт для узла `classify`: определение категории + детекция триггеров эскалации | Structured output: `{category, escalation_trigger, needs_db}` |
| 3.3 | Разработать промпт для узла `generate_answer`: использование RAG-контекста + запрет на hallucination | Промпт с chain-of-thought для коллизий |
| 3.4 | Реализовать детекторы edge-кейсов: manipulation, prompt injection, запрос чужих данных | Список маркеров + правила в промпте |
| 3.5 | Написать E2E-evaluator по `qa.jsonl`: прогон 180 кейсов, метрики по 8 категориям | Скрипт `evaluate.py` + таблица результатов |
| 3.6 | Итерировать промпты по результатам оценки до достижения приемлемого качества | Финальный набор промптов |

**Зависимости**: для 3.5–3.6 нужен рабочий граф от Участника 2.

---

## Карта зависимостей

```
Участник 1 (RAG)
    └── retriever + recall-метрика ──→ Участник 2 (граф)
                                             └── рабочий агент ──→ Участник 3 (оценка)
                                       Участник 3 (промпты)
                                             └── classify-промпт ──→ Участник 2 (classify-узел)
```

Участники 1 и 3 могут стартовать параллельно.
Участник 2 разблокируется, когда есть хотя бы заглушка retriever.
