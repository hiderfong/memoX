"""RAG 引擎 - 检索增强生成"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .bm25_indexer import BM25Indexer, get_bm25_indexer
from .document_parser import DocumentParser
from .hybrid_retriever import HybridRetriever
from .knowledge_graph import KnowledgeGraph, get_knowledge_graph
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
class Citation:
    """结构化引用来源信息（P4-3）

    描述检索结果中的具体来源，支持前端渲染可点击引用链接。
    """
    ref_id: str          # 引用编号，如 "ref-1"
    doc_id: str          # 文档唯一 ID
    filename: str         # 原始文件名
    chunk_index: int     # 在文档中的块索引
    content_preview: str # 内容预览（前100字）
    score: float         # 检索得分

    def to_dict(self) -> dict:
        return {
            "ref_id": self.ref_id,
            "doc_id": self.doc_id,
            "filename": self.filename,
            "chunk_index": self.chunk_index,
            "content_preview": self.content_preview,
            "score": self.score,
        }


@dataclass
class SearchResult:
    """搜索结果"""
    id: str
    content: str
    score: float
    metadata: dict = field(default_factory=dict)

    @property
    def citation(self) -> Citation | None:
        """从 metadata 中提取结构化引用信息"""
        filename = self.metadata.get("filename", "unknown")
        doc_id = self.metadata.get("doc_id", "")
        chunk_index = self.metadata.get("chunk_index", 0)
        preview = self.content[:100] + ("..." if len(self.content) > 100 else "")

        # 从 id 中提取 ref 编号（格式: docid_chunk_N）
        chunk_id = self.id
        ref_num = ""
        if "_chunk_" in chunk_id:
            try:
                ref_num = chunk_id.rsplit("_chunk_", 1)[-1]
            except Exception:
                ref_num = str(chunk_index)
        else:
            ref_num = str(chunk_index)

        return Citation(
            ref_id=f"ref-{ref_num}",
            doc_id=doc_id,
            filename=filename,
            chunk_index=chunk_index,
            content_preview=preview,
            score=self.score,
        )

    def to_context_string(self, max_length: int = 1000, ref_id: str = "") -> str:
        """转换为上下文字符串，包含引用标记"""
        content = self.content
        if len(content) > max_length:
            content = content[:max_length] + "..."
        ref_tag = f"[{ref_id}]" if ref_id else ""
        return f"[来源: {self.metadata.get('filename', 'unknown')}]{ref_tag}\n{content}"


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
        chunk_strategy: str = "size",
        enable_graph: bool = False,
        graph_persist_path: str = "./data/knowledge_graph.gml",
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
        self.chunk_strategy = chunk_strategy
        self.enable_graph = enable_graph

        # 混合检索初始化
        if self.hybrid_search_enabled:
            bm25_indexer = get_bm25_indexer(bm25_persist_path)
            self._hybrid_retriever: HybridRetriever | None = HybridRetriever(
                vector_store=self.vector_store,
                bm25_indexer=bm25_indexer,
            )
        else:
            self._hybrid_retriever = None

        # 知识图谱初始化（实验性）
        if self.enable_graph:
            self._knowledge_graph = get_knowledge_graph(
                persist_path=graph_persist_path,
                enabled=True,
            )
        else:
            self._knowledge_graph = None

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
        embedding_fn = None
        if self.chunk_strategy == "semantic" and hasattr(self.vector_store, "embedding_fn"):
            embedding_fn = self.vector_store.embedding_fn
        document, chunks = await self.document_parser.parse_and_chunk(
            file_path, doc_id, self.chunk_size, self.chunk_overlap,
            chunk_strategy=self.chunk_strategy,
            embedding_fn=embedding_fn,
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

        # 知识图谱（启用时同步写入三元组）
        if self._knowledge_graph is not None:
            for c in chunks:
                from .knowledge_graph import _extract_triples_rule_based, Triple
                triples = _extract_triples_rule_based(c.content, c.id)
                for t in triples:
                    self._knowledge_graph.add_triple(t)
            self._knowledge_graph.save()

    async def delete_document(self, doc_id: str, collection_name: str = "documents") -> bool:
        """从知识库删除文档"""
        count = await self.vector_store.delete_by_document_id(doc_id, collection_name)
        if self._hybrid_retriever is not None:
            self._hybrid_retriever.bm25_indexer.delete_by_doc_id(doc_id)
        if self._knowledge_graph is not None:
            self._knowledge_graph.remove_by_chunk_id(doc_id)
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

    async def search_with_graph(
        self,
        query: str,
        collection_name: str = "documents",
        top_k: int | None = None,
        doc_ids: list[str] | None = None,
        group_ids: list[str] | None = None,
    ) -> dict:
        """检索 + 知识图谱融合结果。

        返回结构：
        {
            "search_results": [...SearchResult],   # 向量/混合检索结果
            "graph_result": GraphSearchResult | None,  # 知识图谱匹配结果
            "graph_boosted_ids": [...],             # 被图谱增强的 chunk id（来自高连接度实体）
        }

        当 enable_graph=False 时退化为纯 search() 结果。
        """
        # 1. 基础检索
        search_results = await self.search(
            query=query,
            collection_name=collection_name,
            top_k=top_k,
            doc_ids=doc_ids,
            group_ids=group_ids,
        )

        result: dict = {
            "search_results": search_results,
            "graph_result": None,
            "graph_boosted_ids": [],
        }

        if self._knowledge_graph is None:
            return result

        # 2. 图谱模糊搜索（用 query 中最长词/短语作实体匹配）
        graph_search_result = self._knowledge_graph.search(query, top_k=3)
        if graph_search_result is None:
            return result

        result["graph_result"] = graph_search_result

        # 3. 图谱连接度增强：来自高度连接实体的 chunk 提升 score
        connected_entities = set(graph_search_result.connected_entities + [graph_search_result.entity])
        boosted_ids: list[str] = []
        for sr in search_results:
            # 检查 chunk 内容是否包含高连接度实体
            content_lower = sr.content.lower()
            for ent in connected_entities:
                if ent.lower() in content_lower:
                    # Boost score by 20%
                    sr.score = min(sr.score * 1.2, 1.0)
                    boosted_ids.append(sr.id)
                    break

        result["graph_boosted_ids"] = boosted_ids
        return result

    def build_rag_prompt(
        self,
        query: str,
        search_results: list[SearchResult],
        system_prompt: str | None = None,
    ) -> list[dict[str, str]]:
        """构建 RAG 提示

        每个上下文来源前会标注 [ref-N] 编号，并指示 LLM 在回答时引用对应编号。
        """
        # 构建带编号的上下文
        context_parts = ["已知信息："]
        citations: list[Citation] = []
        for i, result in enumerate(search_results, 1):
            citation = result.citation
            ref_id = f"ref-{i}"
            if citation:
                # 统一 ref_id 为 1-based 顺序编号
                citation = Citation(
                    ref_id=ref_id,
                    doc_id=citation.doc_id,
                    filename=citation.filename,
                    chunk_index=citation.chunk_index,
                    content_preview=citation.content_preview,
                    score=citation.score,
                )
                citations.append(citation)
            context_parts.append(
                f"\n【文档 {i}】({result.metadata.get('filename', 'unknown')}) [ref-{i}]:\n{result.content}"
            )

        context = "\n".join(context_parts)

        # 系统提示（含引用规则）
        default_system = """你是一个知识库问答助手。请根据提供的上下文信息回答用户的问题。

要求：
1. 优先使用上下文中的信息回答
2. 如果上下文信息不足以回答，请明确说明
3. **引用规则**：当使用某个来源的信息时，必须在对应句子后用 [ref-N] 标注来源编号（N 为【文档 N】后的编号）
4. 引用格式示例：巴黎是法国的首都 [ref-1]
5. 回答要准确、简洁、有条理
6. 多个来源时分别标注，如：根据 [ref-1] 和 [ref-3]
"""

        messages = [
            {"role": "system", "content": system_prompt or default_system},
            {"role": "system", "content": context},
            {"role": "user", "content": query},
        ]

        return messages

    @staticmethod
    def extract_citations_from_text(text: str) -> list[str]:
        """从 LLM 回复文本中解析出所有引用的 [ref-N] 编号

        返回如 ["ref-1", "ref-3"] 的列表（去重按出现顺序排列）
        """
        import re
        found = re.findall(r"\[ref-(\d+)\]", text)
        seen: set[str] = set()
        result: list[str] = []
        for num in found:
            key = f"ref-{num}"
            if key not in seen:
                seen.add(key)
                result.append(key)
        return result

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
    chunk_strategy: str = "size",
    enable_graph: bool = False,
    graph_persist_path: str = "./data/knowledge_graph.gml",
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
        chunk_strategy=chunk_strategy,
        enable_graph=enable_graph,
        graph_persist_path=graph_persist_path,
    )
    return _rag_engine
