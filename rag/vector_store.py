"""
Векторный индекс на ChromaDB — хранит эмбеддинги чанков и метаданные,
реализует косинусный поиск и фильтрацию по метаданным.

Эмбеддинги считаются заранее (GigaChat) и передаются в Chroma напрямую; Chroma
отвечает за хранение, персист на диск и ANN-поиск (cosine). Публичный интерфейс:
    add_chunks(chunks_with_embeddings)
    search(query_embedding, k)
    search_with_filters(query_embedding, k, product_filter, scope_filter)
    save(path) / load(path)
    .chunks  — список метаданных чанков (без эмбеддингов)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import chromadb
from chromadb.config import Settings

COLLECTION = "msb_rag"
# косинусное расстояние; score = 1 - distance
_COSINE = {"hnsw:space": "cosine"}
_SETTINGS = Settings(anonymized_telemetry=False)

# Поля чанка, попадающие в метаданные Chroma (скаляры). text хранится как document,
# heading_trail сериализуется в JSON (Chroma не принимает списки/None в метаданных).
_SCALAR_FIELDS = ("doc_id", "doc_code", "section_id", "title", "scope", "priority", "part")


def _chunk_to_meta(chunk: dict) -> dict:
    meta = {f: chunk.get(f) for f in _SCALAR_FIELDS if chunk.get(f) is not None}
    # chunk_id хранится в метаданных: он не обязан быть уникальным (преамбула и
    # безномерный заголовок дают одинаковый id), поэтому ключом Chroma служит индекс строки.
    meta["chunk_id"] = chunk.get("chunk_id", "")
    # обязательные строковые поля — гарантируем тип
    meta["section_id"] = str(chunk.get("section_id", ""))
    meta["scope"] = chunk.get("scope", "general")
    meta["priority"] = int(chunk.get("priority", 0))
    # product_code может быть None — Chroma не хранит None, пишем "" и восстанавливаем обратно
    meta["product_code"] = chunk.get("product_code") or ""
    meta["heading_trail"] = json.dumps(chunk.get("heading_trail", []), ensure_ascii=False)
    return meta


def _meta_to_chunk(document: str, meta: dict) -> dict:
    return {
        "chunk_id": meta.get("chunk_id", ""),
        "doc_id": meta.get("doc_id", ""),
        "doc_code": meta.get("doc_code", ""),
        "section_id": meta.get("section_id", ""),
        "title": meta.get("title", ""),
        "heading_trail": json.loads(meta.get("heading_trail", "[]")),
        "product_code": meta.get("product_code") or None,
        "scope": meta.get("scope", "general"),
        "priority": int(meta.get("priority", 0)),
        "part": int(meta.get("part", 0)),
        "text": document,
    }


class VectorStore:
    """ChromaDB-индекс чанков с косинусным поиском и фильтрацией по метаданным."""

    def __init__(self):
        # Эфемерный (in-memory) клиент для сборки/оценки. На диск кладёт save().
        self._client = chromadb.EphemeralClient(settings=_SETTINGS)
        self._collection = None
        self.chunks: list[dict] = []

    def add_chunks(self, chunks_with_embeddings: list[dict]) -> None:
        """
        Загрузить чанки с эмбеддингами в коллекцию.

        Args:
            chunks_with_embeddings: список dict с ключами чанка + "embedding": list[float].
        """
        ids, embeddings, documents, metadatas = [], [], [], []
        self.chunks = []
        for i, item in enumerate(chunks_with_embeddings):
            emb = item.pop("embedding")
            ids.append(str(i))  # ключ Chroma — индекс строки (гарантированно уникален)
            embeddings.append([float(x) for x in emb])
            documents.append(item.get("text", ""))
            metadatas.append(_chunk_to_meta(item))
            self.chunks.append(item)

        self._collection = self._client.get_or_create_collection(COLLECTION, metadata=_COSINE)
        self._collection.add(
            ids=ids, embeddings=embeddings, documents=documents, metadatas=metadatas
        )

    def _query(self, query_embedding, k: int, where: Optional[dict]) -> list[dict]:
        if self._collection is None:
            return []
        res = self._collection.query(
            query_embeddings=[[float(x) for x in query_embedding]],
            n_results=k,
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        docs = res["documents"][0]
        metas = res["metadatas"][0]
        dists = res["distances"][0]
        results = []
        for doc, meta, dist in zip(docs, metas, dists):
            chunk = _meta_to_chunk(doc, meta)
            chunk["score"] = 1.0 - float(dist)  # cosine similarity
            results.append(chunk)
        return results

    def search(self, query_embedding: list[float], k: int = 5) -> list[dict]:
        """Top-k поиск по косинусному сходству (без фильтрации)."""
        return self._query(query_embedding, k, where=None)

    def search_with_filters(
        self,
        query_embedding: list[float],
        k: int = 5,
        product_filter: Optional[str] = None,
        scope_filter: Optional[str] = None,
    ) -> list[dict]:
        """
        Поиск с фильтрацией по метаданным.

        product_filter: оставляет чанки этого продукта + все общие (scope=general).
        scope_filter:   оставляет только указанный scope.
        """
        clauses = []
        if product_filter:
            clauses.append(
                {"$or": [{"product_code": {"$eq": product_filter}}, {"scope": {"$eq": "general"}}]}
            )
        if scope_filter:
            clauses.append({"scope": {"$eq": scope_filter}})

        if not clauses:
            where = None
        elif len(clauses) == 1:
            where = clauses[0]
        else:
            where = {"$and": clauses}

        return self._query(query_embedding, k, where=where)

    def save(self, path: Path) -> None:
        """Сохранить индекс на диск (персистентная коллекция Chroma)."""
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        data = self._collection.get(include=["embeddings", "documents", "metadatas"])

        client = chromadb.PersistentClient(path=str(path), settings=_SETTINGS)
        try:
            client.delete_collection(COLLECTION)
        except Exception:
            pass
        col = client.create_collection(COLLECTION, metadata=_COSINE)
        col.add(
            ids=data["ids"],
            embeddings=data["embeddings"],
            documents=data["documents"],
            metadatas=data["metadatas"],
        )

    @classmethod
    def load(cls, path: Path) -> "VectorStore":
        """Загрузить индекс с диска."""
        store = cls.__new__(cls)
        store._client = chromadb.PersistentClient(path=str(Path(path)), settings=_SETTINGS)
        store._collection = store._client.get_collection(COLLECTION)
        got = store._collection.get(include=["documents", "metadatas"])
        store.chunks = [
            _meta_to_chunk(doc, meta)
            for doc, meta in zip(got["documents"], got["metadatas"])
        ]
        return store
