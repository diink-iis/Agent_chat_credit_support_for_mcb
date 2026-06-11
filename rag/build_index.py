"""
build_index.py — сборка векторного индекса из corpus_chunks.jsonl.

Использование:
  python build_index.py --corpus rag/corpus_chunks.jsonl --out rag/index
"""

import argparse
import os
from pathlib import Path
from dotenv import load_dotenv

from retriever import Retriever


def main():
    """Собрать индекс и сохранить на диск."""
    parser = argparse.ArgumentParser(description="Сборка векторного индекса МСБ-RAG")
    parser.add_argument(
        "--corpus",
        type=Path,
        default=Path(__file__).parent / "corpus_chunks.jsonl",
        help="Путь к corpus_chunks.jsonl",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).parent / "index",
        help="Директория для сохранения индекса",
    )
    args = parser.parse_args()

    # Загружаем .env для GIGACHAT_CREDENTIALS.
    load_dotenv()

    print(f"Сборка индекса из {args.corpus}")

    from embeddings import GigaChatEmbeddings
    embeddings = GigaChatEmbeddings()

    # Строим retriever (эмбеддит корпус через GigaChat).
    retriever = Retriever.build_from_corpus(args.corpus, embeddings=embeddings)

    # Сохраняем индекс.
    args.out.mkdir(parents=True, exist_ok=True)
    retriever.vector_store.save(args.out / "vector_store")

    print(f"Готово: {args.out}")
    print(f"  {len(retriever.vector_store.chunks)} чанков, "
          f"размерность {retriever.embeddings.dimensions}")


if __name__ == "__main__":
    main()
