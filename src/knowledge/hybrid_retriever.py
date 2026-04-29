"""混合检索器（Hybrid Retriever）

结合 BM25 全文检索 + ChromaDB 向量相似度检索，
使用 Reciprocal Rank Fusion（RRF）算法融合两路结果。

融合公式（RRF@k）：
    RRF(d) = Σ 1 / (k + rank(d))
其中 k 通常取 60，rank(d) 为该 chunk 在某路检索中的排名（从 1 开始）。

参考文献：
    Reciprocal Rank Fusion outperforms Condorcet and individual Rank Fusion Methods
    (Cormack et al., 2009)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .bm25_indexer import BM25Indexer
    from .vector_store import ChromaVectorStore


# RRF 融合参数：k 值越大，各路结果的权重越趋于均衡
_RRF_K = 60


@dataclass
class RetrievedChunk:
    """混合检索返回的单个结果"""
    chunk_id: str
    content: str
    score: float  # RRF 融合后的综合得分（越高越好）
    vector_score: float = 0.0   # 向量检索原始得分（0-1，cosine similarity）
    bm25_score: float = 0.0     # BM25 原始得分
    rank_vector: int = 0        # 在向量检索中的排名
    rank_bm25: int = 0           # 在 BM25 检索中的排名
    metadata: dict = field(default_factory=dict)

    @property
    def doc_id(self) -> str:
        return self.metadata.get("doc_id", "")

    @property
    def filename(self) -> str:
        return self.metadata.get("filename", "")


@dataclass
class SearchQuality:
    """检索质量诊断报告"""
    total_candidates: int          # 两路去重后的候选总数
    fusion_count: int             # 被 RRF 融合命中的数量
    vector_only_count: int         # 仅向量检索独有的结果数
    bm25_only_count: int           # 仅 BM25 独有的结果数
    avg_rrf_score: float          # 最终结果的平均 RRF 得分
    vector_avg_score: float        # 向量平均分
    bm25_avg_score: float          # BM25 平均分


class HybridRetriever:
    """混合检索器

    使用方式：
        retriever = HybridRetriever(vector_store=vector_store, bm25_indexer=bm25_indexer)
        results = await retriever.search(
            query="如何配置 Worker",
            collection_name="documents",
            top_k=10,
        )
    """

    def __init__(
        self,
        vector_store: ChromaVectorStore,
        bm25_indexer: BM25Indexer,
        rrf_k: int = _RRF_K,
    ):
        self.vector_store = vector_store
        self.bm25_indexer = bm25_indexer
        self.rrf_k = rrf_k

    async def search(
        self,
        query: str,
        collection_name: str = "documents",
        top_k: int = 5,
        filter_metadata: dict | None = None,
    ) -> list[RetrievedChunk]:
        """混合检索主入口

        步骤：
        1. 并行执行 BM25 + 向量检索（asyncio.gather）
        2. 构建 chunk_id → rank 映射
        3. RRF 融合，输出综合得分
        4. 截取 top_k，返回结果
        """
        import asyncio

        # 并行两路检索
        vector_task = self._vector_search(query, collection_name, top_k * 3, filter_metadata)
        bm25_task = self._bm25_search(query, top_k * 3)

        vector_results, bm25_results = await asyncio.gather(vector_task, bm25_task)

        # 构建排名映射 {chunk_id: rank}
        vector_ranks = {cid: rank + 1 for rank, cid in enumerate(cid for cid, _ in vector_results)}
        bm25_ranks = {cid: rank + 1 for rank, cid in enumerate(cid for cid, _ in bm25_results)}

        # chunk_id → 原始分数
        vector_scores_map = {cid: score for cid, score in vector_results}
        bm25_scores_map = {cid: score for cid, score in bm25_results}

        # 合并候选集（去重）
        all_chunk_ids = set(vector_ranks) | set(bm25_ranks)

        # RRF 融合
        rrf_scores: dict[str, float] = {}
        for chunk_id in all_chunk_ids:
            rank_v = vector_ranks.get(chunk_id, float("inf"))
            rank_b = bm25_ranks.get(chunk_id, float("inf"))
            score = 0.0
            if rank_v < float("inf"):
                score += 1.0 / (self.rrf_k + rank_v)
            if rank_b < float("inf"):
                score += 1.0 / (self.rrf_k + rank_b)
            rrf_scores[chunk_id] = score

        # 按 RRF 得分降序
        sorted_chunk_ids = sorted(rrf_scores, key=lambda cid: rrf_scores[cid], reverse=True)

        # 组装最终结果（截取 top_k）
        out: list[RetrievedChunk] = []
        for chunk_id in sorted_chunk_ids[:top_k]:
            bm25_entry = self.bm25_indexer.get_chunk(chunk_id)
            metadata = bm25_entry.metadata if bm25_entry else {}

            # 尝试从向量结果中补充 metadata（向量结果有完整的 metadata）
            if chunk_id in vector_scores_map:
                # 从向量结果重建 metadata（向量 store 返回的格式）
                vec_meta = metadata.copy()  # 先用 BM25 的
                # NOTE: 这里向量结果只返回了 content，没有额外 metadata 字段，
                # 因为 ChromaDB search 返回的 documents + distances 本身不含额外 metadata。
                # metadata 已在 ChromaDB 的 chunk 中，以 field 形式存在。
                # 从 ChromaDB 的 get_by_id 或已缓存获取更完整的 metadata 是设计问题，
                # 目前的折中方案是 metadata 优先取 BM25Indexer 存储的副本。
            else:
                vec_meta = {}

            vec_score = float(vector_scores_map.get(chunk_id, 0.0))
            bm_score = float(bm25_scores_map.get(chunk_id, 0.0))
            rank_v = vector_ranks.get(chunk_id, 0)
            rank_b = bm25_ranks.get(chunk_id, 0)

            out.append(RetrievedChunk(
                chunk_id=chunk_id,
                content=bm25_entry.content if bm25_entry else "",
                score=rrf_scores[chunk_id],
                vector_score=vec_score,
                bm25_score=bm_score,
                rank_vector=rank_v,
                rank_bm25=rank_b,
                metadata=metadata,
            ))

        return out

    def quality_report(
        self,
        query: str,
        collection_name: str = "documents",
        top_k: int = 20,
        filter_metadata: dict | None = None,
    ) -> SearchQuality:
        """同步版本的检索质量诊断（用于调试）"""
        import asyncio

        return asyncio.run(self.search(query, collection_name, top_k, filter_metadata))  # type: ignore[arg-type]

    # ── 内部方法 ──────────────────────────────────────────────────────────────

    async def _vector_search(
        self,
        query: str,
        collection_name: str,
        top_k: int,
        filter_metadata: dict | None,
    ) -> list[tuple[str, float]]:
        """向量检索，返回 [(chunk_id, cosine_score)]"""
        results = await self.vector_store.search(query, top_k, collection_name, filter_metadata)
        # score = 1 - distance（distance 越小相似度越高）
        return [(r["id"], r.get("score", 0.0)) for r in results]

    async def _bm25_search(
        self,
        query: str,
        top_k: int,
    ) -> list[tuple[str, float]]:
        """BM25 检索，返回 [(chunk_id, bm25_score)]"""
        return self.bm25_indexer.search(query, top_k)

    # ── 索引同步 ─────────────────────────────────────────────────────────────

    async def index_chunks(
        self,
        chunks: list,  # list[TextChunk]
        collection_name: str = "documents",
    ) -> None:
        """添加文档时：同步向 ChromaDB + BM25Indexer 写入

        调用方：RAGEngine.add_document() 之后。
        """
        from .bm25_indexer import ChunkEntry

        # 1. 写入 ChromaDB（已有逻辑）
        await self.vector_store.add_chunks(chunks, collection_name)

        # 2. 写入 BM25 索引（注意：metadata 中也存入 doc_id，方便检索后还原）
        entries = [
            ChunkEntry(
                chunk_id=c.id,
                doc_id=c.metadata.get("doc_id", ""),
                content=c.content,
                metadata={**c.metadata},  # 副本，避免污染原对象
            )
            for c in chunks
        ]
        self.bm25_indexer.add_chunks(entries)

    async def remove_doc(
        self,
        doc_id: str,
        collection_name: str = "documents",
    ) -> None:
        """删除文档时：同步从 ChromaDB + BM25Indexer 删除"""
        # 1. 从 ChromaDB 删除（已有逻辑）
        await self.vector_store.delete_by_document_id(doc_id, collection_name)

        # 2. 从 BM25 删除
        self.bm25_indexer.delete_by_doc_id(doc_id)

    async def rebuild_from_vector_store(self, collection_name: str = "documents") -> int:
        """从 ChromaDB 全量重建 BM25 索引（用于首次启用混合搜索时初始化）

        返回重建的 chunk 总数。
        """
        from .bm25_indexer import ChunkEntry

        # 从 ChromaDB 读取所有 chunk
        collection = self.vector_store.get_or_create_collection(collection_name)
        results = collection.get(include=["documents", "metadatas"])

        if not results["ids"]:
            return 0

        entries: list[ChunkEntry] = []
        for chunk_id, content, meta in zip(
            results["ids"], results["documents"], results["metadatas"], strict=False
        ):
            if not content or not chunk_id:
                continue
            entries.append(ChunkEntry(
                chunk_id=chunk_id,
                doc_id=(meta or {}).get("doc_id", ""),
                content=content,
                metadata=meta or {},
            ))

        self.bm25_indexer.rebuild_from_entries(entries)
        return len(entries)
