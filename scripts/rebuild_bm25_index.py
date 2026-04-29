#!/usr/bin/env python3
"""从现有 ChromaDB 数据全量重建 BM25 索引

用法：
    uv run python scripts/rebuild_bm25_index.py
    # 或
    .venv/bin/python scripts/rebuild_bm25_index.py
"""
import asyncio
from pathlib import Path

# 确保 src 在路径中
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import load_config
from src.knowledge.vector_store import get_vector_store
from src.knowledge.rag_engine import RAGEngine
from src.knowledge.bm25_indexer import get_bm25_indexer
from src.knowledge.hybrid_retriever import HybridRetriever


def init_hybrid_retriever() -> HybridRetriever | None:
    """从 config.yaml 初始化混合检索器（standalone 脚本用）"""
    config_path = Path(__file__).parent.parent / "config.yaml"
    cfg = load_config(str(config_path))
    kb = cfg.knowledge_base

    hybrid_cfg = kb.hybrid_search
    if not hybrid_cfg.get("enabled", True):
        print("[INFO] Hybrid search is disabled in config.yaml")
        return None

    vector_store = get_vector_store(kb.persist_directory)
    bm25_indexer = get_bm25_indexer(hybrid_cfg.get("bm25_persist_path", "./data/bm25_index.pkl"))
    return HybridRetriever(vector_store=vector_store, bm25_indexer=bm25_indexer)


async def main() -> None:
    hybrid = init_hybrid_retriever()
    if hybrid is None:
        sys.exit(1)

    total_before = hybrid.bm25_indexer.size
    chroma_stats = hybrid.vector_store.get_collection_stats()
    print(f"[BM25 Rebuild] 当前 BM25 索引: {total_before} chunks")
    print(f"[BM25 Rebuild] ChromaDB 集合 '{chroma_stats['name']}' 共有: {chroma_stats['count']} chunks")

    count = await hybrid.rebuild_from_vector_store()
    total_after = hybrid.bm25_indexer.size
    print(f"[BM25 Rebuild] 重建完成：写入 {count} chunks，BM25 索引当前共 {total_after} chunks")
    print(f"[BM25 Rebuild] 索引版本: {hybrid.bm25_indexer.version}")
    print(f"[BM25 Rebuild] 持久化路径: {hybrid.bm25_indexer.persist_path}")


if __name__ == "__main__":
    asyncio.run(main())
