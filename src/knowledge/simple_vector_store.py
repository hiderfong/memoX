"""简化版向量存储 - 内存模式"""

import hashlib
from dataclasses import dataclass, field

from .document_parser import TextChunk


@dataclass
class SearchResult:
    """搜索结果"""
    id: str
    content: str
    score: float
    metadata: dict = field(default_factory=dict)


class SimpleVectorStore:
    """简单向量存储（内存模式）"""

    def __init__(self):
        self._documents: dict[str, dict] = {}
        self._chunks: dict[str, dict] = {}

    async def add_chunks(self, chunks: list[TextChunk], collection_name: str = "documents") -> list[str]:
        """添加文本块"""
        ids = []
        for chunk in chunks:
            # 简单文本匹配（实际生产用嵌入模型）
            chunk_hash = hashlib.md5(chunk.content.encode()).hexdigest()[:12]
            chunk_id = f"{chunk.id}_{chunk_hash}"

            self._chunks[chunk_id] = {
                "id": chunk_id,
                "content": chunk.content,
                "metadata": chunk.metadata,
            }
            ids.append(chunk_id)
        return ids

    async def search(
        self,
        query: str,
        top_k: int = 5,
        collection_name: str = "documents",
        filter_metadata: dict | None = None,
    ) -> list[dict]:
        """简单文本搜索"""
        query_lower = query.lower()
        query_words = set(query_lower.split())

        results = []

        for chunk_id, chunk in self._chunks.items():
            # 过滤器
            if filter_metadata:
                doc_id = filter_metadata.get("doc_id")
                if doc_id and chunk["metadata"].get("doc_id") != doc_id:
                    continue

            content_lower = chunk["content"].lower()

            # 计算简单的相关性分数
            score = 0
            for word in query_words:
                if word in content_lower:
                    score += content_lower.count(word) / len(chunk["content"])

            # 包含查询词附近的内容加分
            if query_lower in content_lower:
                score += 2.0

            if score > 0:
                results.append({
                    "id": chunk_id,
                    "content": chunk["content"],
                    "metadata": chunk["metadata"],
                    "score": min(score, 1.0),  # 归一化
                    "distance": 1 - min(score, 1.0),
                })

        # 排序并返回 top_k
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    async def delete_by_document_id(self, doc_id: str, collection_name: str = "documents") -> int:
        """删除文档相关的块"""
        to_delete = [
            cid for cid, c in self._chunks.items()
            if c["metadata"].get("doc_id") == doc_id
        ]
        for cid in to_delete:
            del self._chunks[cid]
        return len(to_delete)

    def list_documents(self, collection_name: str = "documents") -> list[dict]:
        """列出文档"""
        doc_ids = set()
        docs = []

        for chunk in self._chunks.values():
            doc_id = chunk["metadata"].get("doc_id")
            if doc_id and doc_id not in doc_ids:
                doc_ids.add(doc_id)
                docs.append({
                    "doc_id": doc_id,
                    "filename": chunk["metadata"].get("filename", "unknown"),
                    "type": chunk["metadata"].get("type", "unknown"),
                    "chunk_count": sum(1 for c in self._chunks.values() if c["metadata"].get("doc_id") == doc_id),
                })

        return docs

    def get_stats(self) -> dict:
        """获取统计"""
        return {
            "total_chunks": len(self._chunks),
            "total_documents": len({c["metadata"].get("doc_id") for c in self._chunks.values() if c["metadata"].get("doc_id")}),
        }


# 全局实例
_vector_store: SimpleVectorStore | None = None


def get_vector_store() -> SimpleVectorStore:
    """获取向量存储实例"""
    global _vector_store
    if _vector_store is None:
        _vector_store = SimpleVectorStore()
    return _vector_store
