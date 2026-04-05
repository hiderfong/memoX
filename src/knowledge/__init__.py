"""知识库模块"""

from .document_parser import DocumentParser, Document, TextChunk
from .rag_engine import (
    RAGEngine,
    DocumentInfo,
    SearchResult,
    ChatMessage,
    ChatSession,
    get_rag_engine,
    init_rag_engine,
)

# 优先使用完整版向量存储，失败则用简化版
try:
    from .vector_store import ChromaVectorStore, get_vector_store as _get_chroma_store
    get_vector_store = _get_chroma_store
except ImportError:
    from .simple_vector_store import SimpleVectorStore, get_vector_store

__all__ = [
    "DocumentParser",
    "Document", 
    "TextChunk",
    "RAGEngine",
    "DocumentInfo",
    "SearchResult",
    "ChatMessage",
    "ChatSession",
    "get_rag_engine",
    "init_rag_engine",
    "get_vector_store",
]
