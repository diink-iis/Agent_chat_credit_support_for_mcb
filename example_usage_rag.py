"""
example_usage_rag.py — Пример использования RAG в LangGraph графе.

Как загружать retriever и использовать в узле retrieve_rag.
"""

from pathlib import Path
from rag.retriever import Retriever, build_query_from_history, detect_product
from typing import Optional

# Промпт приоритета коллизий — единственный источник в prompts/collision_priority.md.
COLLISION_PROMPT_PATH = Path(__file__).parent / "rag" / "prompts" / "collision_priority.md"


def load_collision_rule() -> str:
    """Достаёт блок правила (первый ``` ... ```) из prompts/collision_priority.md."""
    text = COLLISION_PROMPT_PATH.read_text(encoding="utf-8")
    parts = text.split("```")
    return parts[1].strip() if len(parts) >= 3 else text.strip()


def example_node_retrieve_rag(
    state: dict,
    retriever: Retriever,
    k: int = 5,
) -> dict:
    """
    Узел retrieve_rag в LangGraph графе.
    
    Args:
        state: состояние графа содержит:
            - query (str): вопрос пользователя
            - detected_product (str | None): определённый продукт
            - history: история диалога
    
        retriever: инициализированный Retriever
        k: количество результатов (по умолчанию 5)
    
    Returns:
        Обновлённое состояние с retrieved_context и scope_tags
    """
    
    # Для multi-turn собираем запрос из истории, если текущий вопрос пуст.
    query = state.get("query", "")
    if not query.strip() and state.get("history"):
        query = build_query_from_history({"question": query, "history": state["history"]})
    # Продукт берём из состояния (его определяет узел classify), иначе — лёгкая эвристика.
    product_filter = state.get("detected_product") or detect_product(query)

    # ВЫЗОВ RETRIEVER
    hits = retriever.retrieve(
        query=query,
        product_filter=product_filter,
        k=k,
    )
    
    # Подготовка контекста для LLM
    context_parts = []
    scope_tags = []
    
    for i, hit in enumerate(hits, 1):
        # Форматирование источника с тегом scope
        source_info = f"[{i}] {hit['doc_id']}#{hit['section_id']}"
        if hit.get("product_code"):
            source_info += f" ({hit['product_code']})"
        scope_tag = f"[scope={hit['scope']}]"
        
        context_parts.append(f"{source_info} {scope_tag}:\n{hit['text']}\n")
        scope_tags.append({
            "source": source_info,
            "scope": hit["scope"],
            "score": hit["score"],
            "product_code": hit.get("product_code"),
        })
    
    # Формируем контекст для LLM
    context = "=== КОНТЕКСТ ИЗ БАЗЫ ЗНАНИЙ ===\n\n" + "\n".join(context_parts)
    
    # Обновляем состояние
    state["retrieved_context"] = context
    state["scope_tags"] = scope_tags
    state["retrieved_count"] = len(hits)
    
    return state


def example_node_generate_answer(
    state: dict,
    llm_client,
) -> dict:
    """
    Узел generate_answer в LangGraph графе (упрощённый пример).
    
    Args:
        state: состояние графа с retrieved_context
        llm_client: клиент LLM (OpenAI, GigaChat, etc.)
    
    Returns:
        Обновлённое состояние с answer
    """
    
    query = state.get("query", "")
    context = state.get("retrieved_context", "")
    scope_tags = state.get("scope_tags", [])
    
    # Инструкция разрешения коллизий — читаем из prompts/collision_priority.md.
    collision_instruction = load_collision_rule()

    # Формируем промпт для LLM
    prompt = f"""
Ты ассистент банка МСБ. Ответь на вопрос пользователя используя предоставленную информацию.

Вопрос: {query}

{collision_instruction}

Источники:
{context}

Ответь кратко и точно, цитируя источники (например: "согласно пункту 2.1.3 документа 01_credit_products.md").
"""
    
    # Вызов LLM (пример)
    # response = llm_client.generate(prompt)
    # В реальном коде используй GigaChat или другой LLM
    
    response = f"[Mock response] Ответ на '{query}' с контекстом из {state['retrieved_count']} источников"
    
    state["answer"] = response
    state["sources"] = scope_tags
    
    return state


# === ПРИМЕР ИСПОЛЬЗОВАНИЯ В ГРАФЕ ===

def main():
    """Демонстрация использования retriever в узлах графа."""
    
    # 1. Инициализация retriever (в начале приложения)
    print("1. Загружаем retriever...")
    retriever = Retriever.load("rag/index")
    print("   Готов\n")
    
    # 2. Симуляция состояния графа
    state = {
        "query": "На какой максимальный срок я могу взять Бизнес-Развитие?",
        "detected_product": "BUSINESS_RAZVITIE",
        "history": [],
    }
    print(f"2. Вопрос: {state['query']}\n")
    
    # 3. Узел retrieve_rag
    print("3. Выполняем узел retrieve_rag...")
    state = example_node_retrieve_rag(state, retriever, k=5)
    print(f"   Найдено источников: {state['retrieved_count']}\n")
    
    # 4. Вывод результатов retriever
    print("4. Теги scope (для разрешения коллизий):")
    for tag in state["scope_tags"]:
        scope = "SPECIFIC" if tag["scope"] == "specific" else "GENERAL"
        print(f"   {scope} | {tag['source']} | score={tag['score']:.3f}")
    print()
    
    # 5. Контекст для LLM
    print("5. Контекст для LLM:")
    print(state["retrieved_context"][:500] + "...\n")
    
    # 6. Узел generate_answer (симуляция)
    print("6. Выполняем узел generate_answer...")
    # state = example_node_generate_answer(state, llm_client=None)  # в реальном коде
    print(f"   Ответ готов\n")


if __name__ == "__main__":
    main()
