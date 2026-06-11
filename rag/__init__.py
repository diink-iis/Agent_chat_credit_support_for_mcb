"""RAG-слой: поиск по нормативным документам банка для агента поддержки МСБ.

Публичный API для Участника 2:
    from rag import Retriever, build_query_from_history, detect_product
"""

from rag.retriever import Retriever, build_query_from_history, detect_product

__all__ = ["Retriever", "build_query_from_history", "detect_product"]
