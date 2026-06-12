# Слой агента (Участник 2): tools + граф + эскалация

Зона ответственности по декомпозиции — задачи **2.1–2.6**: инструменты доступа к
БД клиентов, идентификация по каналу, состояние диалога, граф LangGraph с
маршрутизацией, multi-turn и эскалация на оператора.

Пакет `agent/` ставится рядом с `rag/` (Участник 1) в корне репозитория.

## Структура

```
agent/
  db.py          # read-only доступ к clients.sqlite
  tools.py       # 2.1 — get_client_info / get_active_loans / get_applications (+ LangChain-обёртки)
  auth.py        # 2.2 — resolve_client (доступ = функция канала), ensure_self_access
  state.py       # 2.3 — AgentState (TypedDict + add_messages), seed/конвертация диалога
  nodes.py       # 2.4 — узлы classify / retrieve_rag / query_db / generate_answer / escalate
  graph.py       # 2.4 + 2.5 — сборка графа, маршрутизация, MemorySaver
  escalation.py  # 2.6 — EscalationPayload (п.5.1), сводка ≤500 (п.5.2), симуляция передачи
  llm.py         # боевые classify/generate на GigaChat + make_gigachat_deps()
  stubs.py       # оффлайн-заглушки (baseline-классификатор, шаблонный генератор, FakeRetriever)
  prompts/
    system_prompt.md # 3.1 — роль/ограничения (п.6.2)/тон (п.9); подмешивается в generate
    classify.md      # 3.2 + 3.4 — классификатор + детекторы edge (manipulation/injection/чужие данные)
    generate.md      # 3.3 — грунтинг + chain-of-thought для коллизий
tests/
  test_layer1.py # tools + авторизация
  test_layer2.py # состояние + стык с RAG-хелперами Участника 1
  test_layer3.py # маршрутизация графа + multi-turn
  test_layer4.py # эскалация
```

## Поток графа

```
START → classify ─┬─ escalate ─────────────────────────→ END   (триггер; приоритет п.4.1)
                  ├─ query_db ─→ [нужен RAG?] ─→ retrieve_rag ─→ generate_answer → END
                  ├─ retrieve_rag ─→ generate_answer → END       (info / edge_conflict)
                  └─ generate_answer → END                       (offtopic / edge_no_data / манипуляции)
```

## Запуск

### Оффлайн (без GigaChat) — проверка структуры и маршрутизации

```bash
python -m tests.test_layer1
python -m tests.test_layer2
python -m tests.test_layer3
python -m tests.test_layer4
```

### Боевой режим (с GigaChat)

1. Заполнить `.env` (см. `.env.example`): `GIGACHAT_CREDENTIALS`, `GIGACHAT_SCOPE`.
2. Убедиться, что индекс Участника 1 собран в `rag/index` (`setup_rag.sh`).
3. Собрать граф на реальных зависимостях:

```python
from agent.llm import make_gigachat_deps
from agent.graph import build_graph
from agent.state import make_initial_state

deps = make_gigachat_deps(index_dir="rag/index")   # реальный Retriever + GigaChat
graph = build_graph(deps)

state = make_initial_state(channel="chat_intern", session_client_id="C-000004",
                           question="Какая ставка по Бизнес-Развитие?")
result = graph.invoke(state, config={"configurable": {"thread_id": "session-1"}})
print(result["answer"])
```

## Точки подключения соседей

- **Участник 1 (RAG).** Граф ждёт объект с методом `retrieve(query, product_filter, k)`
  и хелперы `build_query_from_history`, `detect_product` (импортируются из `rag`).
  Реальный ретривер подключается в `make_gigachat_deps` через `Retriever.load("rag/index")`.
  Правило коллизий читается из `rag/prompts/collision_priority.md`.
- **Участник 3 (промпты + оценка).** Тело `classify`/`generate_answer` — в
  `agent/prompts/{system_prompt,classify,generate}.md` (3.1–3.4). Контракт узлов не
  меняется: `classify_fn(state) -> dict{category, escalation_trigger, needs_db,
  needs_rag, detected_product, negative_markers}`, `generate_fn(state) -> str`.
  E2E-оценка (3.5–3.6) — скрипт `evaluate.py` в корне репозитория, метрики и находки
  в `EVALUATION.md`. Прогон: `python evaluate.py --mode stub|gigachat [--judge]`.

## Принципы безопасности

- Доступ к БД — read-only (`db.get_connection`, `mode=ro`).
- Авторизация определяется **каналом**, не наличием `client_id` (`auth.resolve_client`).
- Инструменты вызываются только с авторизованным `client_id`; чужой id и анонимный
  канал отсекаются (`auth.ensure_self_access`, п. 7.2 РП-ОБ-005).
- Приоритет эскалации над инфо-ответом (п. 4.1) зашит в маршрутизацию.
