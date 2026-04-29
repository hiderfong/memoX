"""RAG 引擎 - 检索增强生成"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .bm25_indexer import BM25Indexer, get_bm25_indexer
from .document_parser import DocumentParser
from .hybrid_retriever import HybridRetriever
from .vector_store import ChromaVectorStore, EmbeddingFunction, get_vector_store


@dataclass
class DocumentInfo:
    """文档信息"""
    id: str
    filename: str
    type: str
    chunk_count: int
    created_at: str
    size: int = 0
    group_id: str = "ungrouped"


@dataclass
class SearchResult:
    """搜索结果"""
    id: str
    content: str
    score: float
    metadata: dict = field(default_factory=dict)

    def to_context_string(self, max_length: int = 1000) -> str:
        """转换为上下文字符串"""
        content = self.content
        if len(content) > max_length:
            content = content[:max_length] + "..."
        return f"[来源: {self.metadata.get('filename', 'unknown')}]\n{content}"


@dataclass
class ChatMessage:
    """聊天消息"""
    role: str  # "user" | "assistant" | "system"
    content: str
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    metadata: dict = field(default_factory=dict)


@dataclass
class ChatSession:
    """聊天会话"""
    id: str
    title: str = ""
    messages: list[ChatMessage] = field(default_factory=list)
    document_ids: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())


class RAGEngine:
    """RAG 引擎"""

    def __init__(
        self,
        vector_store: ChromaVectorStore | None = None,
        document_parser: DocumentParser | None = None,
        chunk_size: int = 500,
        chunk_overlap: int = 50,
        top_k: int = 5,
        dashscope_api_key: str = "",
        dashscope_base_url: str = "",
        hybrid_search_enabled: bool = True,
        bm25_persist_path: str = "./data/bm25_index.pkl",
    ):
        self.vector_store = vector_store or get_vector_store()
        self.document_parser = document_parser or DocumentParser(
            dashscope_api_key=dashscope_api_key,
            dashscope_base_url=dashscope_base_url,
        )
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.top_k = top_k
        self.hybrid_search_enabled = hybrid_search_enabled

        # 混合检索初始化
        if self.hybrid_search_enabled:
            bm25_indexer = get_bm25_indexer(bm25_persist_path)
            self._hybrid_retriever: HybridRetriever | None = HybridRetriever(
                vector_store=self.vector_store,
                bm25_indexer=bm25_indexer,
            )
        else:
            self._hybrid_retriever = None

        # 内存存储会话（生产环境应使用数据库）
        self._sessions: dict[str, ChatSession] = {}
        self._documents: dict[str, DocumentInfo] = {}

    async def add_document(
        self,
        file_path: Path,
        collection_name: str = "documents",
        group_id: str = "ungrouped",
        original_filename: str | None = None,
    ) -> DocumentInfo:
        """添加文档到知识库"""
        doc_id = str(uuid.uuid4())[:8]
        display_name = original_filename or file_path.name

        print(f"[RAG] Starting to process document: {display_name}")
        print(f"[RAG] Document ID: {doc_id}")

        # 解析并分块
        print("[RAG] Parsing document...")
        document, chunks = await self.document_parser.parse_and_chunk(
            file_path, doc_id, self.chunk_size, self.chunk_overlap
        )
        print(f"[RAG] Parsed into {len(chunks)} chunks")

        # 添加文档元数据（写入 ChromaDB 以持久化，重启后可恢复列表）
        created_at = datetime.now().isoformat()
        file_size = file_path.stat().st_size
        for chunk in chunks:
            chunk.metadata["doc_id"] = doc_id
            chunk.metadata["filename"] = display_name
            chunk.metadata["type"] = document.metadata.get("type", "unknown")
            chunk.metadata["created_at"] = created_at
            chunk.metadata["file_size"] = file_size
            chunk.metadata["chunk_count"] = len(chunks)
            chunk.metadata["group_id"] = group_id

        # 同步写入向量库 + BM25 索引
        print(f"[RAG] Adding {len(chunks)} chunks to vector store and BM25 index...")
        await self._index_document_chunks(chunks, collection_name)

        print("[RAG] Successfully added all chunks")

        # 记录文档信息（内存缓存，同时 ChromaDB 已持久化）
        doc_info = DocumentInfo(
            id=doc_id,
            filename=display_name,
            type=document.metadata.get("type", "unknown"),
            chunk_count=len(chunks),
            created_at=created_at,
            size=file_size,
            group_id=group_id,
        )
        self._documents[doc_id] = doc_info

        print(f"[RAG] Document processing complete: {doc_info}")
        return doc_info

    async def _index_document_chunks(
        self,
        chunks: list,
        collection_name: str,
    ) -> None:
        """将解析好的 chunks 同步写入向量库和 BM25 索引"""
        # 向量库（ChromaDB）
        BATCH_SIZE = 100
        total_chunks = len(chunks)
        if total_chunks > BATCH_SIZE:
            for i in range(0, total_chunks, BATCH_SIZE):
                batch = chunks[i:i + BATCH_SIZE]
                await self.vector_store.add_chunks(batch, collection_name)
        else:
            await self.vector_store.add_chunks(chunks, collection_name)

        # BM25 索引（混合检索时同步）
        if self._hybrid_retriever is not None:
            from .bm25_indexer import ChunkEntry

            entries = [
                ChunkEntry(
                    chunk_id=c.id,
                    doc_id=c.metadata.get("doc_id", ""),
                    content=c.content,
                    metadata=c.metadata,
                )
                for c in chunks
            ]
            self._hybrid_retriever.bm25_indexer.add_chunks(entries)

    async def delete_document(self, doc_id: str, collection_name: str = "documents") -> bool:
        """从知识库删除文档"""
        count = await self.vector_store.delete_by_document_id(doc_id, collection_name)
        if self._hybrid_retriever is not None:
            self._hybrid_retriever.bm25_indexer.delete_by_doc_id(doc_id)
        if doc_id in self._documents:
            del self._documents[doc_id]
        return count > 0

    @staticmethod
    def _strip_uuid_prefix(filename: str) -> str:
        """去掉早期上传时添加的 UUID hex 前缀（格式: 32位hex_原始文件名）"""
        import re
        return re.sub(r'^[0-9a-f]{32}_', '', filename)

    def list_documents(self, collection_name: str = "documents") -> list[DocumentInfo]:
        """列出知识库中的文档（从 ChromaDB 读取，重启后不丢失）"""
        try:
            chroma_docs = self.vector_store.list_documents(collection_name)
        except Exception:
            # ChromaDB 不可用时回退到内存
            return list(self._documents.values())

        result: list[DocumentInfo] = []
        seen: set[str] = set()
        for d in chroma_docs:
            doc_id = d["doc_id"]
            if doc_id in seen:
                continue
            seen.add(doc_id)
            # 优先用内存中更完整的信息，否则用 ChromaDB 中的
            if doc_id in self._documents:
                result.append(self._documents[doc_id])
            else:
                raw_name = d.get("filename", "unknown")
                result.append(DocumentInfo(
                    id=doc_id,
                    filename=self._strip_uuid_prefix(raw_name),
                    type=d.get("type", "unknown"),
                    chunk_count=d.get("chunk_count", 0),
                    created_at=d.get("created_at", ""),
                    size=d.get("file_size", 0),
                    group_id=d.get("group_id", "ungrouped"),
                ))
        return result

    async def search(
        self,
        query: str,
        collection_name: str = "documents",
        top_k: int | None = None,
        doc_ids: list[str] | None = None,
        group_ids: list[str] | None = None,
    ) -> list[SearchResult]:
        """检索相关文档片段

        当 hybrid_search_enabled=True 时使用 BM25+向量混合检索（RRF 融合），
        否则退化为纯向量检索。
        """
        k = top_k or self.top_k

        # 构建 ChromaDB 元数据过滤条件（用于向量检索）
        try:
            valid_doc_ids = {d["doc_id"] for d in self.vector_store.list_documents(collection_name)}
        except Exception:
            valid_doc_ids = None

        conditions: list[dict] = []
        if doc_ids:
            effective = list(set(doc_ids) & valid_doc_ids) if valid_doc_ids is not None else list(doc_ids)
            if not effective:
                return []
            conditions.append({"doc_id": {"$in": effective}})
        elif valid_doc_ids is not None:
            if not valid_doc_ids:
                return []
            conditions.append({"doc_id": {"$in": list(valid_doc_ids)}})

        if group_ids is not None:
            conditions.append({"group_id": {"$in": group_ids}})

        filter_metadata: dict | None
        if not conditions:
            filter_metadata = None
        elif len(conditions) == 1:
            filter_metadata = conditions[0]
        else:
            filter_metadata = {"$and": conditions}

        if self._hybrid_retriever is not None:
            # 混合检索路径：BM25 + 向量 + RRF 融合
            hybrid_results = await self._hybrid_retriever.search(
                query=query,
                collection_name=collection_name,
                top_k=k,
                filter_metadata=filter_metadata,
            )
            # 过滤低分结果
            MIN_SCORE = 0.01
            return [
                SearchResult(
                    id=r.chunk_id,
                    content=r.content,
                    score=r.score,
                    metadata=r.metadata,
                )
                for r in hybrid_results
                if r.score >= MIN_SCORE
            ]

        # 纯向量检索路径（hybrid_search_enabled=False）
        results = await self.vector_store.search(query, k, collection_name, filter_metadata)
        MIN_SCORE = 0.2
        results = [r for r in results if (r.get("score") or 0) >= MIN_SCORE]
        return [
            SearchResult(
                id=r["id"],
                content=r["content"],
                score=r["score"],
                metadata=r.get("metadata", {}),
            )
            for r in results
        ]

    def build_rag_prompt(
        self,
        query: str,
        search_results: list[SearchResult],
        system_prompt: str | None = None,
    ) -> list[dict[str, str]]:
        """构建 RAG 提示"""
        # 上下文
        context_parts = ["已知信息："]
        for i, result in enumerate(search_results, 1):
            context_parts.append(f"\n【文档 {i}】({result.metadata.get('filename', 'unknown')}):\n{result.content}")

        context = "\n".join(context_parts)

        # 系统提示
        default_system = """你是一个知识库问答助手。请根据提供的上下文信息回答用户的问题。

要求：
1. 优先使用上下文中的信息回答
2. 如果上下文信息不足以回答，请明确说明
3. 引用相关文档来源
4. 回答要准确、简洁、有条理
"""

        messages = [
            {"role": "system", "content": system_prompt or default_system},
            {"role": "system", "content": context},
            {"role": "user", "content": query},
        ]

        return messages

    # ==================== 会话管理 ====================

    def create_session(self, title: str = "", document_ids: list[str] | None = None) -> ChatSession:
        """创建聊天会话"""
        session_id = str(uuid.uuid4())[:8]
        session = ChatSession(
            id=session_id,
            title=title or f"会话 {len(self._sessions) + 1}",
            document_ids=document_ids or [],
        )
        self._sessions[session_id] = session
        return session

    def get_session(self, session_id: str) -> ChatSession | None:
        """获取会话"""
        return self._sessions.get(session_id)

    def add_message(self, session_id: str, role: str, content: str) -> ChatMessage | None:
        """添加消息"""
        session = self._sessions.get(session_id)
        if not session:
            return None

        message = ChatMessage(role=role, content=content)
        session.messages.append(message)
        session.updated_at = datetime.now().isoformat()
        return message

    def get_sessions(self) -> list[ChatSession]:
        """获取所有会话"""
        return list(self._sessions.values())

    def delete_session(self, session_id: str) -> bool:
        """删除会话"""
        if session_id in self._sessions:
            del self._sessions[session_id]
            return True
        return False

    def get_document_chunks(self, doc_id: str, collection_name: str = "documents") -> list[dict]:
        """获取文档的所有分块内容"""
        return self.vector_store.get_chunks_by_doc(doc_id, collection_name)

    def move_document_group(
        self,
        doc_id: str,
        new_group_id: str,
        collection_name: str = "documents",
    ) -> bool:
        """将文档移到指定分组（更新所有 chunk 的 group_id metadata）"""
        count = self.vector_store.update_metadata_by_doc_id(
            doc_id, {"group_id": new_group_id}, collection_name
        )
        if doc_id in self._documents:
            self._documents[doc_id].group_id = new_group_id
        return count > 0


# 全局 RAG 引擎实例
_rag_engine: RAGEngine | None = None


def get_rag_engine() -> RAGEngine:
    """获取 RAG 引擎实例"""
    global _rag_engine
    if _rag_engine is None:
        _rag_engine = RAGEngine()
    return _rag_engine


def init_rag_engine(
    persist_directory: str = "./data/chroma",
    embedding_function: EmbeddingFunction | None = None,
    chunk_size: int = 500,
    chunk_overlap: int = 50,
    top_k: int = 5,
    dashscope_api_key: str = "",
    dashscope_base_url: str = "",
    hybrid_search_enabled: bool = True,
    bm25_persist_path: str = "./data/bm25_index.pkl",
) -> RAGEngine:
    """初始化 RAG 引擎"""
    global _rag_engine
    vector_store = get_vector_store(persist_directory, embedding_function)
    _rag_engine = RAGEngine(
        vector_store=vector_store,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        top_k=top_k,
        dashscope_api_key=dashscope_api_key,
        dashscope_base_url=dashscope_base_url,
        hybrid_search_enabled=hybrid_search_enabled,
        bm25_persist_path=bm25_persist_path,
    )
    return _rag_engine
