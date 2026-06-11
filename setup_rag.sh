#!/bin/bash
# setup_rag.sh — полная подготовка RAG-части к работе (GigaChat).
#
#   bash setup_rag.sh            # собрать индекс
#   bash setup_rag.sh --eval     # собрать индекс и посчитать recall@5
#
# Требуется .env с GIGACHAT_CREDENTIALS (см. .env.example).

set -e

echo "=========================================="
echo "RAG-часть: полная подготовка (GigaChat)"
echo "=========================================="
echo ""

# 1. Установка зависимостей
echo "1) Установка зависимостей..."
python -m pip install -q -r rag/requirements.txt
echo "   ok"
echo ""

# 2. Проверка корпуса
echo "2) Проверка корпуса чанков..."
if [ ! -f rag/corpus_chunks.jsonl ]; then
    echo "   corpus_chunks.jsonl не найден, генерируем..."
    python rag/chunker.py
else
    CHUNK_COUNT=$(wc -l < rag/corpus_chunks.jsonl | tr -d ' ')
    echo "   найдено $CHUNK_COUNT чанков"
fi
echo ""

# 3. Сборка индекса
echo "3) Сборка индекса (GigaChat, требует GIGACHAT_CREDENTIALS в .env)..."
python rag/build_index.py --corpus rag/corpus_chunks.jsonl --out rag/index
echo ""

# 4. Оценка качества (опционально)
if [ "$1" = "--eval" ]; then
    echo "4) Оценка recall@5..."
    python rag/evaluate_recall.py --qa data/qa/qa.jsonl \
        --corpus rag/corpus_chunks.jsonl --k 5
else
    echo "4) Пропускаем оценку (добавь --eval чтобы запустить)"
fi
echo ""

echo "=========================================="
echo "RAG готово к работе."
echo "=========================================="
echo ""
echo "Использование в Участнике 2 (LangGraph):"
echo ""
echo "  from rag import Retriever, build_query_from_history, detect_product"
echo "  retriever = Retriever.load('rag/index')"
echo "  hits = retriever.retrieve(query, product_filter=detect_product(query), k=5)"
echo ""
