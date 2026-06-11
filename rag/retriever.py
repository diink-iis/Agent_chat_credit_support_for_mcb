"""
Retriever — главный интерфейс поиска с фильтрацией и приоритизацией.

Функция retrieve(query, product_filter=None, k=5) — основной API для Участника 2.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

# Импорт работает и при запуске из каталога rag/ (script-режим: `from embeddings ...`),
# и при импорте как пакет из корня проекта (`from rag.retriever import ...`).
try:
    from embeddings import EmbeddingsBase, GigaChatEmbeddings
    from vector_store import VectorStore
except ImportError:  # pragma: no cover
    from rag.embeddings import EmbeddingsBase, GigaChatEmbeddings
    from rag.vector_store import VectorStore


# Основа названия продукта (для сопоставления со склонениями: «Бизнес-Развитию» и т.п.).
_PRODUCT_STEMS = {
    "оборот": "BUSINESS_OBOROT",
    "развит": "BUSINESS_RAZVITIE",
    "лимит": "BUSINESS_LIMIT",
    "старт": "BUSINESS_START",
    "перезагруз": "BUSINESS_PEREZAGRUZKA",
}


def detect_product(query: str) -> Optional[str]:
    """
    Лёгкая эвристика: найти явное упоминание продукта линейки → код продукта.

    Нужна, чтобы Участник 2 мог прокинуть `product_filter` в retrieve(). Ищет
    конструкцию «бизнес-<продукт>» без учёта регистра и дефиса/пробела, по основе
    слова — поэтому ловит склонения («Бизнес-Развитию», «Бизнес-Старта»). Якорь
    «бизнес» убирает ложные срабатывания на общих словах («лимит кредита»).
    Возвращает код продукта или None.

    Это НЕ классификатор: для надёжного определения продукта в диалоге используйте
    узел classify (Участник 3). Хелпер — быстрый запасной вариант.
    """
    q = query.lower().replace("-", " ")
    for stem, code in _PRODUCT_STEMS.items():
        if f"бизнес {stem}" in q:
            return code
    return None


def build_query_from_history(case: dict) -> str:
    """
    Сформировать строку запроса для retrieval.

    Для одношаговых кейсов это просто `question`. Для multi-turn (поле `question`
    пустое, диалог лежит в `history`) собираем запрос из реплик клиента + последней
    реплики ассистента (она несёт продуктовый контекст, к которому относится
    уточняющий вопрос). Используется и в оценке recall, и Участником 2 в узле
    retrieve_rag для накопленного контекста диалога.
    """
    question = (case.get("question") or "").strip()
    if question:
        return question
    client_turns: list[str] = []
    last_assistant = ""
    for turn in case.get("history", []):
        text = (turn.get("text") or "").strip()
        if not text:
            continue
        if turn.get("role") == "client":
            client_turns.append(text)
        elif turn.get("role") == "assistant":
            last_assistant = text
    parts = client_turns + ([last_assistant] if last_assistant else [])
    return " ".join(parts).strip()


class Retriever:
    """
    Retriever с поддержкой:
    - Косинусного поиска по эмбеддингам.
    - Фильтрации по product_code (частный случай > общий).
    - Приоритизации: specific > general.
    """

    def __init__(self, vector_store: VectorStore, embeddings: EmbeddingsBase):
        """
        Args:
            vector_store: индекс с эмбеддингами.
            embeddings: backend для встраивания query.
        """
        self.vector_store = vector_store
        self.embeddings = embeddings

    def retrieve(
        self,
        query: str,
        product_filter: Optional[str] = None,
        k: int = 5,
    ) -> list[dict]:
        """
        Поиск релевантных чанков для query — top-k по косинусному сходству.

        РАНЖИРОВАНИЕ
        ============
        Результаты сортируются ПО РЕЛЕВАНТНОСТИ (косинусный score). Это и есть
        корректное поведение для recall: нужный пункт должен попадать в top-k по
        смыслу запроса. Глобальная пересортировка "все specific выше всех general"
        НЕ применяется — она ломает recall на общих регламентах (эскалации,
        процессы, edge-кейсы), где правильный ответ лежит в general-секции.

        РАЗРЕШЕНИЕ КОЛЛИЗИЙ
        ===================
        Когда по одному вопросу конфликтуют общее и продуктовое правило (общий срок
        счёта 6 мес vs Бизнес-Старт 3 мес), приоритет частного случая над общим
        решается НА УРОВНЕ ГЕНЕРАЦИИ — через промпт rag/prompts/collision_priority.md.
        Для этого каждый чанк несёт теги `scope` ("specific" | "general"),
        `product_code` и `priority`, которые передаются в LLM. Retriever лишь честно
        возвращает релевантные кандидаты обоих видов; на edge_conflict-кейсах top-k
        по score и так уверенно поднимает нужные секции.

        product_filter (опционально): если указан код продукта, поиск ограничивается
        чанками этого продукта + общими (search_with_filters). Используется
        Участником 2, когда продукт в диалоге уже определён.

        Args:
            query: текстовый запрос (для multi-turn — собранный из history).
            product_filter: код продукта (напр. "BUSINESS_OBOROT") для фильтрации.
            k: количество результатов.

        Returns:
            list[dict]: [{
                "chunk_id", "doc_id", "doc_code", "section_id", "title", "text",
                "product_code", "scope", "priority", "score",
            }, ...] — отсортировано по убыванию score.
        """
        query_embedding = self.embeddings.embed_text(query)

        if product_filter:
            results = self.vector_store.search_with_filters(
                query_embedding,
                k=k,
                product_filter=product_filter,
            )
        else:
            results = self.vector_store.search(query_embedding, k=k)

        # search() уже возвращает top-k по score; гарантируем порядок на случай фильтра.
        results.sort(key=lambda x: -x.get("score", 0))
        return results[:k]

    @classmethod
    def load(cls, index_dir: Path | str) -> Retriever:
        """
        Загрузить retriever из сохранённого индекса.

        Ожидает персистентную коллекцию ChromaDB в index_dir/vector_store/
        (chroma.sqlite3 + бинарники HNSW). Query-эмбеддинги считаются тем же
        backend'ом (GigaChat), что и при сборке индекса.
        """
        index_dir = Path(index_dir)
        
        # Загружаем vector store.
        vs_dir = index_dir / "vector_store"
        vector_store = VectorStore.load(vs_dir)
        
        # Загружаем GigaChat embeddings.
        embeddings = GigaChatEmbeddings()
        
        return cls(vector_store, embeddings)

    @classmethod
    def build_from_corpus(
        cls,
        corpus_path: Path,
        embeddings: Optional[EmbeddingsBase] = None,
    ) -> Retriever:
        """
        Построить retriever из корпуса чанков (corpus_chunks.jsonl).
        
        Args:
            corpus_path: путь к corpus_chunks.jsonl.
            embeddings: backend для эмбеддингов (если None, используем GigaChat).
        """
        import json

        # Если embeddings не передан, используем GigaChat.
        if embeddings is None:
            embeddings = GigaChatEmbeddings()

        # Загружаем корпус.
        chunks = []
        texts = []
        with corpus_path.open("r", encoding="utf-8") as f:
            for line in f:
                chunk = json.loads(line)
                chunks.append(chunk)
                texts.append(chunk["text"])

        # Встраиваем все чанки.
        embeddings_list = embeddings.embed_texts(texts)

        # Подготавливаем данные для vector_store.
        chunks_with_embeddings = []
        for chunk, emb in zip(chunks, embeddings_list):
            item = {
                "chunk_id": chunk["chunk_id"],
                "doc_id": chunk["doc_id"],
                "doc_code": chunk["doc_code"],
                "section_id": chunk["section_id"],
                "title": chunk["title"],
                "heading_trail": chunk.get("heading_trail", []),
                "text": chunk["text"],
                "product_code": chunk.get("product_code"),
                "scope": chunk.get("scope", "general"),
                "priority": chunk.get("priority", 0),
                "part": chunk.get("part", 0),
                "embedding": emb,
            }
            chunks_with_embeddings.append(item)

        # Строим vector store.
        vector_store = VectorStore()
        vector_store.add_chunks(chunks_with_embeddings)

        return cls(vector_store, embeddings)
