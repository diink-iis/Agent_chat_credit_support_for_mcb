# RAG-часть

Зона ответственности: **документы → векторная база → поиск**. Финальный продукт для
команды — функция `retrieve(query, product_filter?, k)` и подсчет метрика recall@5.

Embeddings: **GigaChat** (`langchain-gigachat`). Разрешение коллизий «продуктовый
регламент > общий» вынесено на уровень генерации (промпт), а не ранжирования —
см. ниже.

## Структура модулей

| Файл | Назначение |
|------|-----------|
| `chunker.py` | Section-aware чанкинг 5 документов + метаданные (scope, product_code) |
| `embeddings.py` | GigaChat backend (интерфейс `EmbeddingsBase`) |
| `vector_store.py` | ChromaDB-индекс: косинусный поиск + фильтрация по метаданным |
| `retriever.py` | `retrieve()` (top-k по score) + `build_query_from_history()` + `detect_product()` |
| `evaluate_recall.py` | recall@5 по qa.jsonl, разбивка по категориям |
| `build_index.py` | Сборка и сохранение индекса |
| `prompts/collision_priority.md` | Промпт-шаблон приоритета «specific > general» для генерации |

## Чанкинг

Документы режутся по нумерованным markdown-заголовкам. Номер заголовка (`2.4.3`)
становится `section_id` и **совпадает с разметкой `referenced_documents` в qa.jsonl**
(`01_credit_products.md#2.4.3`) — это даёт честную проверку recall. Базовая единица —
листовая секция целиком (перекрёстные ссылки внутри пункта не разрываются); длинные
секции дробятся по абзацам с перекрытием. Параметры: `max_chars=1200`, `overlap=150`.

Метаданные каждого чанка: `doc_id`, `section_id`, `title`, `heading_trail`,
`product_code`, `scope` (`specific` | `general`), `priority`. Итог: **224 чанка**.

## Разрешение коллизий — на уровне генерации, не ранжирования

Когда по одному вопросу конфликтуют общее и продуктовое правило (общий срок счёта
6 мес vs Бизнес-Старт 3 мес), приоритет частного случая решает **LLM по промпту**
`prompts/collision_priority.md`, опираясь на теги `scope`/`product_code` в каждом чанке.

`retrieve()` ранжирует результаты **по релевантности (score)** и НЕ переставляет
specific выше general принудительно. Глобальная пересортировка «все specific выше всех
general» проверялась и была отвергнута: она роняет recall на общих регламентах
(эскалации, процессы, edge-кейсы) с 56% до 43%. При этом на коллизионных запросах
top-k по score и так уверенно поднимает в выдачу ОБА пункта — и общий, и продуктовый,
а выбор между ними делает промпт.

## Метрика recall

`recall@k` = доля кейсов, где хотя бы один `referenced_document` попал в top-k
(с префиксным совпадением секций: ссылка `#2` покрывает `2.x`). Для multi-turn кейсов
(пустой `question`, диалог в `history`) запрос собирается `build_query_from_history()`.

**Осмысленный показатель — грунтинг по citable-категориям** (`info`, `transactional`,
`edge_conflict`), где ответ реально опирается на нормативку. В поведенческих категориях
(`escalation_*`, `edge_no_data`, `edge_manipulation`, `offtopic`) gold-ссылка указывает
на регламент *поведения* агента, а не на смысловой пункт; корректность там обеспечивают
`classify`/`escalate`, и recall неинформативен (`CITABLE_CATEGORIES` в `evaluate_recall.py`,
та же логика, что у `citation` в `evaluate.py`).

**Грунтинг по citable: recall@5 = 78.8% (78/99), recall@8 = 86.9% (86/99).**
В проде агент ретривит с `k=8`, поэтому ориентир — recall@8; с ним согласуется
`citation` из E2E. Размытый общий по всем 8 категориям (recall@5 = 56.1%, recall@8 = 64.4%)
занижен поведенческими категориями и для оценки качества retrieval не используется.

| Категория | recall@5 | recall@8 | Комментарий |
|-----------|---------:|---------:|-------------|
| info | 82.2% | 84.4% | основной retrieval — высокий |
| transactional | 73.3% | 86.7% | основной retrieval — высокий |
| edge_conflict | 88.9% | 100.0% | коллизии: нужные пункты в выдаче |
| **citable (итог)** | **78.8%** | **86.9%** | **показатель качества retrieval** |
| escalation_negative | 44.4% | 61.1% | поведенческая — recall неприменим |
| edge_manipulation | 38.9% | 50.0% | поведенческая — recall неприменим |
| edge_no_data | 22.2% | 22.2% | ответ = «нет данных», не зависит от документа |
| escalation_sales | 16.7% | 22.2% | gold-ссылка — политика маршрутизации, не контент |
| offtopic | 11.1% | 22.2% | агент должен отклонить, retrieval не нужен |

Артефакты: `recall_results_k5.json`, `recall_results_k8.json` (поле `citable` — итоговый
грунтинг). Числа поведенческих категорий приведены для полноты, в итог не входят.

## Запуск

```bash
# .env (см. .env.example): GIGACHAT_CREDENTIALS=<authorization key>
bash setup_rag.sh           # собрать индекс
bash setup_rag.sh --eval    # собрать индекс + посчитать recall@5
```

Точечно:

```bash
python rag/chunker.py                                  # пересобрать corpus_chunks.jsonl
python rag/build_index.py --corpus rag/corpus_chunks.jsonl --out rag/index
python rag/evaluate_recall.py --qa data/qa/qa.jsonl --corpus rag/corpus_chunks.jsonl --k 5
```

## Интерфейс для Участника 2 (handoff)

Публичный API доступен прямо из пакета `rag`:

```python
from rag import Retriever, build_query_from_history, detect_product

retriever = Retriever.load("rag/index")           # один раз при старте приложения

# В узле retrieve_rag. Для multi-turn собрать запрос из истории:
query = build_query_from_history({"question": user_text, "history": dialog_history})

# Продукт берём из classify, иначе — лёгкая эвристика по тексту запроса:
product = detected_product or detect_product(query)

hits = retriever.retrieve(query, product_filter=product, k=5)

# hit: {chunk_id, doc_id, section_id, title, text, product_code, scope, priority, score}
# Передавай text + теги scope/product_code в generate_answer вместе с промптом
# prompts/collision_priority.md — LLM выберет частное правило над общим.
```

- `retrieve(query, product_filter=None, k=5)` — top-k чанков, отсортированы по `score` (убывание).
- `product_filter` (опционально) ограничивает поиск чанками продукта + общими.
- `detect_product(query)` — код продукта по явному упоминанию («Бизнес-Старт») или `None`.
  Это лёгкая эвристика для запасного варианта; надёжное определение продукта — узел classify.
- `build_query_from_history(case)` — строит запрос для multi-turn (пустой `question`, диалог в `history`).
- Источник для цитирования — `doc_id#section_id`.

## Операционные требования

`retrieve()` на **каждый вызов** обращается к GigaChat для эмбеддинга запроса:

- нужен валидный `GIGACHAT_CREDENTIALS` в `.env` у каждого, кто запускает граф;
- это сетевой вызов (~0.3–2 с на запрос) — учитывайте в latency узла и при тестах;
- сам индекс (`rag/index/`, ChromaDB) уже собран и читается локально, пересборка не нужна.

Быстрая проверка, что всё подключилось (из корня проекта, с заполненным `.env`):

```bash
python -c "from rag import Retriever; r=Retriever.load('rag/index'); \
print(len(r.vector_store.chunks), 'чанков;', r.retrieve('ставка по обороту', k=1)[0]['doc_id'])"
# ожидаемо: 224 чанков; 01_credit_products.md
```
