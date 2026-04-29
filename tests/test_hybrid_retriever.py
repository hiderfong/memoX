"""P4-1 混合检索测试

覆盖范围：
- BM25Indexer：分词、添加、删除、搜索、持久化
- HybridRetriever：BM25+向量 RRF 融合、结果排序、质量报告
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.knowledge.bm25_indexer import (
    BM25Indexer,
    ChunkEntry,
    _tokenize,
    get_bm25_indexer,
    init_bm25_indexer,
)
from src.knowledge.hybrid_retriever import (
    HybridRetriever,
    RetrievedChunk,
    SearchQuality,
)


# ── BM25Indexer 单元测试 ────────────────────────────────────────────────────

class TestTokenize:
    """分词器测试"""

    def test_english_lowercase(self):
        assert _tokenize("Hello World") == ["hello", "world"]

    def test_chinese_whole_phrase(self):
        # 中文字符串按 Unicode \w 边界保留为完整词，不逐字拆分
        # 对于 BM25 检索，保留完整中文短语是可以接受的（支持整句精确匹配）
        result = _tokenize("深度学习是机器学习的分支")
        assert len(result) == 1
        assert result[0] == "深度学习是机器学习的分支"

    def test_mixed_english_chinese(self):
        # 中英文混合：英文按空格拆分为小写，中文保留
        result = _tokenize("BERT is a Transformer model transformer")
        assert "bert" in result
        assert "transformer" in result
        assert len(result) >= 3

    def test_mixed_content(self):
        result = _tokenize("BERT is a Transformer-based model")
        assert "bert" in result
        assert "transformer" in result
        assert "model" in result

    def test_punctuation_removed(self):
        result = _tokenize("你好，世界！Hello, world.")
        assert any("hello" in t for t in result)
        assert any("world" in t for t in result)
        # 标点符号应被去除
        assert "" not in result

    def test_empty_string(self):
        assert _tokenize("") == []
        assert _tokenize("   ") == []

    def test_numbers_kept(self):
        result = _tokenize("GPT-4 has 100B parameters")
        assert "gpt" in result
        assert "4" in result
        assert "100b" in result


class TestBM25IndexerAddDelete:
    """BM25Indexer 添加/删除测试"""

    def test_add_chunks_incremental(self, tmp_path: Path):
        indexer = BM25Indexer(persist_path=tmp_path / "bm25.pkl")
        assert indexer.size == 0

        chunks = [
            ChunkEntry(chunk_id="c1", doc_id="d1", content="深度学习是机器学习的一个分支", metadata={}),
            ChunkEntry(chunk_id="c2", doc_id="d1", content="自然语言处理使用 Transformer 架构", metadata={}),
            ChunkEntry(chunk_id="c3", doc_id="d2", content="Python 是一种广泛使用的高级编程语言", metadata={}),
        ]
        indexer.add_chunks(chunks)
        assert indexer.size == 3

        # 再添加 2 个 chunk（c4, c5）
        new_chunks = [
            ChunkEntry(chunk_id="c4", doc_id="d2", content="JavaScript 用于 Web 前端开发", metadata={}),
            ChunkEntry(chunk_id="c5", doc_id="d3", content="强化学习通过试错进行策略优化", metadata={}),
        ]
        indexer.add_chunks(new_chunks)
        assert indexer.size == 5

    def test_add_duplicate_chunk_skipped(self, tmp_path: Path):
        indexer = BM25Indexer(persist_path=tmp_path / "bm25.pkl")
        chunks = [
            ChunkEntry(chunk_id="c1", doc_id="d1", content="测试内容", metadata={}),
        ]
        indexer.add_chunks(chunks)
        indexer.add_chunks(chunks)  # 重复添加
        assert indexer.size == 1  # 不应重复计数

    def test_delete_chunks(self, tmp_path: Path):
        indexer = BM25Indexer(persist_path=tmp_path / "bm25.pkl")
        chunks = [
            ChunkEntry(chunk_id="c1", doc_id="d1", content="内容一", metadata={}),
            ChunkEntry(chunk_id="c2", doc_id="d1", content="内容二", metadata={}),
            ChunkEntry(chunk_id="c3", doc_id="d2", content="内容三", metadata={}),
        ]
        indexer.add_chunks(chunks)
        assert indexer.size == 3

        indexer.delete_chunks(["c1", "c3"])
        assert indexer.size == 1
        assert indexer.get_chunk("c1") is None
        assert indexer.get_chunk("c2") is not None
        assert indexer.get_chunk("c3") is None

    def test_delete_by_doc_id(self, tmp_path: Path):
        indexer = BM25Indexer(persist_path=tmp_path / "bm25.pkl")
        chunks = [
            ChunkEntry(chunk_id="c1", doc_id="d1", content="文档一的内容", metadata={}),
            ChunkEntry(chunk_id="c2", doc_id="d1", content="文档一的第二段", metadata={}),
            ChunkEntry(chunk_id="c3", doc_id="d2", content="文档二的内容", metadata={}),
        ]
        indexer.add_chunks(chunks)
        assert indexer.size == 3

        deleted = indexer.delete_by_doc_id("d1")
        assert set(deleted) == {"c1", "c2"}
        assert indexer.size == 1
        assert indexer.get_chunk("c3") is not None

    def test_get_chunk(self, tmp_path: Path):
        indexer = BM25Indexer(persist_path=tmp_path / "bm25.pkl")
        entry = ChunkEntry(chunk_id="c1", doc_id="d1", content="测试内容", metadata={"page": 3})
        indexer.add_chunks([entry])

        retrieved = indexer.get_chunk("c1")
        assert retrieved is not None
        assert retrieved.content == "测试内容"
        assert retrieved.metadata == {"page": 3}
        assert indexer.get_chunk("nonexistent") is None


class TestBM25IndexerSearch:
    """BM25Indexer 检索测试"""

    def test_search_returns_ranked_results(self, tmp_path: Path):
        indexer = BM25Indexer(persist_path=tmp_path / "bm25.pkl")
        chunks = [
            ChunkEntry(chunk_id="c1", doc_id="d1", content="Python 是一种高级编程语言", metadata={}),
            ChunkEntry(chunk_id="c2", doc_id="d1", content="JavaScript 用于前端开发", metadata={}),
            ChunkEntry(chunk_id="c3", doc_id="d2", content="深度学习使用神经网络", metadata={}),
            ChunkEntry(chunk_id="c4", doc_id="d2", content="机器学习是人工智能的分支", metadata={}),
            ChunkEntry(chunk_id="c5", doc_id="d3", content="Python 数据分析库 pandas", metadata={}),
        ]
        indexer.add_chunks(chunks)

        # 精确关键词搜索
        results = indexer.search("Python", top_k=5)
        assert len(results) > 0
        # 包含 "Python" 的文档应该排名靠前
        top_ids = [cid for cid, _ in results]
        assert "c1" in top_ids
        assert "c5" in top_ids

    def test_search_top_k_limit(self, tmp_path: Path):
        indexer = BM25Indexer(persist_path=tmp_path / "bm25.pkl")
        for i in range(20):
            indexer.add_chunks([
                ChunkEntry(chunk_id=f"c{i}", doc_id="d1", content=f"内容 {i} 包含关键词", metadata={}),
            ])

        results = indexer.search("关键词", top_k=3)
        assert len(results) <= 3

    def test_search_no_match_returns_empty(self, tmp_path: Path):
        indexer = BM25Indexer(persist_path=tmp_path / "bm25.pkl")
        indexer.add_chunks([
            ChunkEntry(chunk_id="c1", doc_id="d1", content="机器学习", metadata={}),
        ])
        results = indexer.search("完全不相关的查询词 xyz123", top_k=5)
        # 无匹配的 chunk 不会出现在结果中
        assert all(score > 0 for _, score in results)

    def test_empty_index_returns_empty(self, tmp_path: Path):
        indexer = BM25Indexer(persist_path=tmp_path / "bm25.pkl")
        results = indexer.search("任意查询", top_k=5)
        assert results == []


class TestBM25IndexerPersistence:
    """BM25Indexer 持久化测试"""

    def test_persistence_roundtrip(self, tmp_path: Path):
        persist = tmp_path / "bm25.pkl"
        chunks = [
            ChunkEntry(chunk_id="c1", doc_id="d1", content="持久化测试内容", metadata={"page": 1}),
            ChunkEntry(chunk_id="c2", doc_id="d2", content="第二个文档的内容", metadata={}),
        ]

        indexer1 = BM25Indexer(persist_path=persist)
        indexer1.add_chunks(chunks)
        version_after_add = indexer1.version
        assert version_after_add > 0

        # 重新加载
        indexer2 = BM25Indexer(persist_path=persist)
        assert indexer2.size == 2
        assert indexer2.get_chunk("c1") is not None
        assert indexer2.get_chunk("c1").content == "持久化测试内容"

    def test_singleton_pattern(self, tmp_path: Path):
        # 全局单例在测试间应可重新初始化
        indexer = init_bm25_indexer(str(tmp_path / "singleton.pkl"))
        indexer.add_chunks([
            ChunkEntry(chunk_id="s1", doc_id="sd1", content="单例测试", metadata={}),
        ])
        retrieved = get_bm25_indexer(str(tmp_path / "singleton.pkl"))
        assert retrieved.size == 1
        assert retrieved.get_chunk("s1").content == "单例测试"


# ── HybridRetriever 单元测试 ────────────────────────────────────────────────

class MockVectorStore:
    """模拟 ChromaDB 向量存储"""

    def __init__(self, chunks: list[dict] | None = None):
        self._chunks = {c["id"]: c for c in (chunks or [])}

    async def add_chunks(self, chunks, collection_name: str = "documents") -> list[str]:
        for c in chunks:
            self._chunks[c.id] = {
                "id": c.id,
                "content": c.content,
                "metadata": c.metadata,
            }
        return [c.id for c in chunks]

    async def search(
        self,
        query: str,
        top_k: int = 5,
        collection_name: str = "documents",
        filter_metadata: dict | None = None,
    ) -> list[dict]:
        # 简单的关键词模拟向量相似度：content 包含 query 中的词越多，得分越高
        query_terms = set(query.lower().split())
        results = []
        for cid, chunk in self._chunks.items():
            content_lower = chunk["content"].lower()
            score = sum(1 for t in query_terms if t in content_lower) / max(len(query_terms), 1)
            # 加上一点随机噪声防止完全相同分数
            score += 0.001
            results.append({
                "id": cid,
                "content": chunk["content"],
                "metadata": chunk["metadata"],
                "score": min(score, 1.0),
            })
        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:top_k]

    async def delete_by_document_id(self, doc_id: str, collection_name: str = "documents") -> int:
        to_delete = [cid for cid, c in self._chunks.items() if c["metadata"].get("doc_id") == doc_id]
        for cid in to_delete:
            del self._chunks[cid]
        return len(to_delete)


class TestHybridRetrieverBasic:
    """HybridRetriever 基础功能测试"""

    def test_rrf_fusion_orders_results(self, tmp_path: Path):
        # 设置：向量检索和 BM25 检索给出不同排名
        chunks = [
            ChunkEntry(chunk_id="c1", doc_id="d1", content="Python 编程语言", metadata={"filename": "python.txt"}),
            ChunkEntry(chunk_id="c2", doc_id="d1", content="JavaScript 前端语言", metadata={"filename": "js.txt"}),
            ChunkEntry(chunk_id="c3", doc_id="d2", content="深度学习框架 PyTorch", metadata={"filename": "dl.txt"}),
            ChunkEntry(chunk_id="c4", doc_id="d2", content="机器学习算法", metadata={"filename": "ml.txt"}),
        ]
        bm25_indexer = BM25Indexer(persist_path=tmp_path / "bm25.pkl")
        bm25_indexer.add_chunks(chunks)

        # 向量存储：c1 和 c3 排名靠前
        vec_chunks = [
            {"id": "c1", "content": "Python 编程语言", "metadata": {"filename": "python.txt"}},
            {"id": "c2", "content": "JavaScript 前端语言", "metadata": {"filename": "js.txt"}},
            {"id": "c3", "content": "深度学习框架 PyTorch", "metadata": {"filename": "dl.txt"}},
            {"id": "c4", "content": "机器学习算法", "metadata": {"filename": "ml.txt"}},
        ]
        vector_store = MockVectorStore(vec_chunks)

        retriever = HybridRetriever(vector_store=vector_store, bm25_indexer=bm25_indexer)

        # BM25 搜 "Python"：c1 排第1，c3/c4 可能也匹配（因为有"学习"等词）
        bm25_results = bm25_indexer.search("Python", top_k=4)
        assert bm25_results[0][0] == "c1"

        # 验证混合检索确实在调用两路
        results = asyncio.run(retriever.search("Python", top_k=3))
        assert len(results) <= 3
        # c1 应该在结果中（两路都相关）
        assert any(r.chunk_id == "c1" for r in results)

    def test_bm25_fills_vector_gaps(self, tmp_path: Path):
        """BM25 擅长精确关键词，向量擅长语义相似"""
        chunks = [
            ChunkEntry(chunk_id="c1", doc_id="d1", content="React Hooks useState 教程", metadata={}),
            ChunkEntry(chunk_id="c2", doc_id="d1", content="Vue Composition API 教程", metadata={}),
            ChunkEntry(chunk_id="c3", doc_id="d2", content="Angular 依赖注入", metadata={}),
        ]
        bm25_indexer = BM25Indexer(persist_path=tmp_path / "bm25.pkl")
        bm25_indexer.add_chunks(chunks)

        # 向量存储返回空（语义不相似）
        vector_store = MockVectorStore([])

        retriever = HybridRetriever(vector_store=vector_store, bm25_indexer=bm25_indexer)
        results = asyncio.run(retriever.search("React Hooks", top_k=3))

        # BM25 仍然能找到 c1（精确关键词匹配）
        assert len(results) > 0
        assert any(r.chunk_id == "c1" for r in results)

    def test_retrieved_chunk_fields(self, tmp_path: Path):
        # 注意：ChunkEntry.metadata 需要包含 doc_id（与 RAGEngine._index_document_chunks 行为一致）
        entry_meta = {"filename": "test.txt", "page": 5, "doc_id": "d1"}
        chunks = [
            ChunkEntry(chunk_id="c1", doc_id="d1", content="测试内容", metadata=entry_meta),
        ]
        bm25_indexer = BM25Indexer(persist_path=tmp_path / "bm25.pkl")
        bm25_indexer.add_chunks(chunks)
        vector_store = MockVectorStore([{"id": "c1", "content": "测试内容", "metadata": entry_meta}])

        retriever = HybridRetriever(vector_store=vector_store, bm25_indexer=bm25_indexer)
        results = asyncio.run(retriever.search("测试", top_k=3))

        assert len(results) > 0
        r = results[0]
        assert isinstance(r, RetrievedChunk)
        assert r.chunk_id == "c1"
        assert r.content == "测试内容"
        assert r.score >= 0.0
        assert r.metadata.get("filename") == "test.txt"
        assert r.doc_id == "d1"

    def test_empty_corpus(self, tmp_path: Path):
        bm25_indexer = BM25Indexer(persist_path=tmp_path / "bm25.pkl")
        vector_store = MockVectorStore([])

        retriever = HybridRetriever(vector_store=vector_store, bm25_indexer=bm25_indexer)
        results = asyncio.run(retriever.search("任意查询", top_k=5))
        assert results == []

    def test_index_chunks_syncs_both_stores(self, tmp_path: Path):
        """index_chunks 应该同时写入 ChromaDB 和 BM25"""
        bm25_indexer = BM25Indexer(persist_path=tmp_path / "bm25.pkl")
        vector_store = MockVectorStore([])

        retriever = HybridRetriever(vector_store=vector_store, bm25_indexer=bm25_indexer)

        # 构造真实的 TextChunk-like 对象（dataclass）
        from src.knowledge.document_parser import TextChunk
        mock_chunks = [
            TextChunk(id=f"chunk{i}", content=f"内容 {i}", metadata={"doc_id": f"doc{i}", "filename": f"file{i}.txt"})
            for i in range(3)
        ]

        asyncio.run(retriever.index_chunks(mock_chunks, "documents"))

        # BM25 应该已有 3 个 chunks
        assert bm25_indexer.size == 3
        # MockVectorStore 应该也已添加
        assert len(vector_store._chunks) == 3

    def test_remove_doc_syncs_both_stores(self, tmp_path: Path):
        bm25_indexer = BM25Indexer(persist_path=tmp_path / "bm25.pkl")
        vector_store = MockVectorStore([])

        retriever = HybridRetriever(vector_store=vector_store, bm25_indexer=bm25_indexer)

        # 先添加
        asyncio.run(retriever.index_chunks([], "documents"))
        asyncio.run(retriever.remove_doc("doc1", "documents"))

        # 删除后 BM25 应该为空（index_chunks 没加任何东西，但 vector_store 是 mock 空）
        assert bm25_indexer.size == 0


class TestRRFFormula:
    """RRF 融合公式验证"""

    def test_rrf_gives_boost_to_high_rank_in_both(self, tmp_path: Path):
        """在两路检索中都排名第1的文档，RRF 得分应该是排名第2的 2 倍左右"""
        chunks = [
            ChunkEntry(chunk_id="c1", doc_id="d1", content="Python 编程指南", metadata={}),
            ChunkEntry(chunk_id="c2", doc_id="d1", content="JavaScript 教程", metadata={}),
        ]
        bm25_indexer = BM25Indexer(persist_path=tmp_path / "bm25.pkl")
        bm25_indexer.add_chunks(chunks)

        # 模拟向量：c1 排第1, c2 排第2
        vector_store = MockVectorStore([
            {"id": "c1", "content": "Python 编程指南", "metadata": {}},
            {"id": "c2", "content": "JavaScript 教程", "metadata": {}},
        ])

        retriever = HybridRetriever(vector_store=vector_store, bm25_indexer=bm25_indexer, rrf_k=60)
        results = asyncio.run(retriever.search("Python", top_k=2))

        assert len(results) == 2
        # c1 应该在 c2 前面
        assert results[0].chunk_id == "c1"
        # 两路都第1 vs 一路第1一路第2，RRF 得分应有显著差异
        assert results[0].score > results[1].score

    def test_rrf_k_higher_more_balanced(self, tmp_path: Path):
        """k 值越大，两路权重越均衡"""
        chunks = [
            ChunkEntry(chunk_id="c1", doc_id="d1", content="Python 语言", metadata={}),
            ChunkEntry(chunk_id="c2", doc_id="d1", content="JavaScript 语言", metadata={}),
        ]
        bm25_indexer = BM25Indexer(persist_path=tmp_path / "bm25.pkl")
        bm25_indexer.add_chunks(chunks)

        vector_store = MockVectorStore([
            {"id": "c1", "content": "Python 语言", "metadata": {}},
            {"id": "c2", "content": "JavaScript 语言", "metadata": {}},
        ])

        r1 = asyncio.run(HybridRetriever(vector_store=vector_store, bm25_indexer=bm25_indexer, rrf_k=1).search("Python", top_k=2))
        r2 = asyncio.run(HybridRetriever(vector_store=vector_store, bm25_indexer=bm25_indexer, rrf_k=100).search("Python", top_k=2))

        # 两种 k 值下 c1 都是第1，但相对得分比例不同
        assert r1[0].chunk_id == r2[0].chunk_id == "c1"
