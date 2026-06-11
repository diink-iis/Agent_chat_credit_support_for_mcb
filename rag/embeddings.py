"""
Embeddings backend для RAG.

GigaChat — единственный backend. Интерфейс EmbeddingsBase оставлен абстрактным,
чтобы при необходимости можно было подменить реализацию, но проект использует
GigaChat Embeddings через пакет langchain-gigachat.

Требует GIGACHAT_CREDENTIALS (base64 "client_id:client_secret") в .env.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


class EmbeddingsBase(ABC):
    """Базовый интерфейс для embeddings backend."""

    name: str = "base"

    @abstractmethod
    def embed_text(self, text: str) -> list[float]:
        """Встроить один текст в вектор."""

    @abstractmethod
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Встроить список текстов в матрицу векторов."""

    @property
    @abstractmethod
    def dimensions(self) -> int:
        """Размерность вектора."""


class GigaChatEmbeddings(EmbeddingsBase):
    """
    GigaChat embeddings backend.

    Использует пакет langchain-gigachat с авторизацией через GIGACHAT_CREDENTIALS.
    """

    name = "gigachat"

    def __init__(
        self,
        credentials: Optional[str] = None,
        model: str = "Embeddings",
        scope: Optional[str] = None,
    ):
        """
        Args:
            credentials: base64 authorization key. Если None — берём из .env GIGACHAT_CREDENTIALS.
            model: модель эмбеддингов ("Embeddings" по умолчанию; "EmbeddingsGigaR" — альтернатива).
            scope: область доступа. Если None — из .env GIGACHAT_SCOPE или GIGACHAT_API_PERS.
        """
        env_path = Path(__file__).parent.parent / ".env"
        if env_path.exists():
            load_dotenv(env_path, override=True)

        self.credentials = credentials or os.getenv("GIGACHAT_CREDENTIALS")
        if not self.credentials:
            raise ValueError(
                "GIGACHAT_CREDENTIALS не установлены. Скопируй .env.example в .env "
                "и подставь authorization key, либо передай credentials= явно."
            )

        self.model = model
        self.scope = scope or os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
        self._client = None
        self._dimensions = 1024  # GigaChat Embeddings размерность

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def _get_client(self):
        """Ленивая инициализация клиента GigaChat."""
        if self._client is None:
            try:
                from langchain_gigachat import GigaChatEmbeddings as GigaChatEmb
            except ImportError as e:  # pragma: no cover
                raise ImportError(
                    "langchain-gigachat не установлен. Установи: pip install langchain-gigachat"
                ) from e
            self._client = GigaChatEmb(
                credentials=self.credentials,
                model=self.model,
                scope=self.scope,
                verify_ssl_certs=False,
            )
        return self._client

    def embed_text(self, text: str) -> list[float]:
        return self._get_client().embed_query(text)

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return self._get_client().embed_documents(texts)
