"""向量存储 - ChromaDB 集成"""

import asyncio
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from .document_parser import TextChunk


@dataclass
class EmbeddingResult:
    """嵌入结果"""
    id: str
    embedding: list[float]
    document: str
    metadata: dict = field(default_factory=dict)


class EmbeddingFunction:
    """嵌入函数基类"""
    
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """嵌入文本列表"""
        raise NotImplementedError


class SentenceTransformerEmbedding(EmbeddingFunction):
    """Sentence Transformers 嵌入"""
    
    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2"):
        self.model_name = model_name
        self._model = None
        self._executor = None
    
    def _load_model(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
                print(f"[SentenceTransformer] Loading model: {self.model_name}")
                self._model = SentenceTransformer(self.model_name)
                print(f"[SentenceTransformer] Model loaded successfully")
            except ImportError:
                raise ImportError("请安装 sentence-transformers: pip install sentence-transformers")
    
    def _get_executor(self):
        """获取线程池执行器"""
        if self._executor is None:
            from concurrent.futures import ThreadPoolExecutor
            self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="embedding_")
        return self._executor
    
    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        
        self._load_model()
        
        # 在线程池中运行 CPU 密集型编码
        def _encode_sync(texts_to_encode):
            return self._model.encode(texts_to_encode, convert_to_numpy=True, show_progress_bar=False)
        
        loop = asyncio.get_event_loop()
        embeddings = await loop.run_in_executor(
            self._get_executor(),
            _encode_sync,
            texts
        )
        return embeddings.tolist()


class OpenAIEmbedding(EmbeddingFunction):
    """OpenAI 嵌入"""
    
    def __init__(self, api_key: str, model: str = "text-embedding-3-small"):
        self.api_key = api_key
        self.model = model
    
    async def embed(self, texts: list[str]) -> list[list[float]]:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.openai.com/v1/embeddings",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "input": texts,
                    "model": self.model,
                },
                timeout=60.0,
            )
            response.raise_for_status()
            data = response.json()
            return [item["embedding"] for item in data["data"]]


class DashScopeEmbedding(EmbeddingFunction):
    """阿里云 DashScope 嵌入 - text-embedding-v3"""
    
    def __init__(self, api_key: str, model: str = "text-embedding-v3"):
        self.api_key = api_key
        self.model = model
        self.api_url = "https://dashscope.aliyuncs.com/api/v1/services/embeddings/text-embedding/text-embedding"
        self._client: httpx.AsyncClient | None = None
    
    def _get_client(self) -> httpx.AsyncClient:
        """获取或创建 HTTP 客户端"""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(60.0, connect=10.0),
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            )
        return self._client
    
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """调用 DashScope Embedding API
        
        API 文档: https://help.aliyun.com/zh/model-studio/dashscopeembedding-in-llamaindex
        """
        if not texts:
            return []
        
        # DashScope text-embedding-v3 每批最多 10 条
        BATCH_SIZE = 10
        all_embeddings = []
        
        for i in range(0, len(texts), BATCH_SIZE):
            batch = texts[i:i + BATCH_SIZE]
            batch_embeddings = await self._embed_batch(batch)
            all_embeddings.extend(batch_embeddings)
            print(f"[DashScope] Embedded batch {i//BATCH_SIZE + 1}/{(len(texts)-1)//BATCH_SIZE + 1}")
        
        return all_embeddings
    
    async def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        """嵌入单个批次"""
        import asyncio
        
        max_retries = 3
        retry_delay = 1.0
        
        for attempt in range(max_retries):
            try:
                client = self._get_client()
                response = await client.post(
                    self.api_url,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "input": {
                            "texts": texts
                        },
                    },
                )
                response.raise_for_status()
                data = response.json()
                
                # DashScope 返回格式: output.embeddings[{text_index, embedding}]
                embeddings = data["output"]["embeddings"]
                # 按 text_index 排序确保顺序正确
                embeddings.sort(key=lambda x: x["text_index"])
                return [item["embedding"] for item in embeddings]
                
            except httpx.HTTPStatusError as e:
                print(f"[DashScope ERROR] HTTP {e.response.status_code}: {e.response.text}")
                print(f"[DashScope DEBUG] Texts sample: {[t[:50] for t in texts[:3]]}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2  # 指数退避
                else:
                    raise
            except Exception as e:
                print(f"[DashScope ERROR] Attempt {attempt + 1} failed: {type(e).__name__}: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2
                else:
                    raise


class ChromaVectorStore:
    """ChromaDB 向量存储"""
    
    def __init__(
        self,
        persist_directory: str = "./data/chroma",
        embedding_function: EmbeddingFunction | None = None,
    ):
        self.persist_directory = Path(persist_directory)
        self.persist_directory.mkdir(parents=True, exist_ok=True)
        
        self.embedding_function = embedding_function or SentenceTransformerEmbedding()
        self._client = None
        self._collection = None
    
    def _get_client(self):
        if self._client is None:
            try:
                import chromadb
                from chromadb.config import Settings
                
                self._client = chromadb.PersistentClient(
                    path=str(self.persist_directory),
                    settings=Settings(anonymized_telemetry=False),
                )
            except ImportError:
                raise ImportError("请安装 chromadb: pip install chromadb")
        return self._client
    
    def get_or_create_collection(self, name: str = "documents") -> Any:
        """获取或创建集合"""
        client = self._get_client()
        try:
            collection = client.get_collection(name=name)
        except Exception:
            collection = client.create_collection(
                name=name,
                metadata={"description": "Document chunks for RAG"},
            )
        return collection
    
    async def add_chunks(self, chunks: list[TextChunk], collection_name: str = "documents") -> list[str]:
        """添加文本块到向量库"""
        if not chunks:
            return []

        # 过滤空内容或过短的 chunk（embedding API 通常要求至少有实质内容）
        MIN_CONTENT_LEN = 5
        valid_chunks = [c for c in chunks if c.content and len(c.content.strip()) >= MIN_CONTENT_LEN]
        if not valid_chunks:
            print(f"[VectorStore] All {len(chunks)} chunks filtered out (too short)")
            return []
        if len(valid_chunks) < len(chunks):
            print(f"[VectorStore] Filtered {len(chunks) - len(valid_chunks)} short chunks")
        chunks = valid_chunks

        collection = self.get_or_create_collection(collection_name)

        # 批量嵌入
        texts = [chunk.content for chunk in chunks]

        # 调试日志
        print(f"[VectorStore] Embedding {len(texts)} chunks...")
        
        try:
            embeddings = await self.embedding_function.embed(texts)
        except Exception as e:
            print(f"[VectorStore ERROR] Embedding failed: {type(e).__name__}: {e}")
            raise
        
        # 验证 embeddings 类型
        if embeddings is None:
            raise ValueError("Embedding function returned None")
        if not isinstance(embeddings, list):
            raise TypeError(f"Expected list of embeddings, got {type(embeddings).__name__}")
        if len(embeddings) != len(texts):
            raise ValueError(f"Embedding count mismatch: {len(embeddings)} vs {len(texts)}")
        
        print(f"[VectorStore] Got {len(embeddings)} embeddings, adding to collection...")
        
        # 批量添加
        ids = [chunk.id for chunk in chunks]
        metadatas = [
            {**chunk.metadata, "content_length": len(chunk.content)}
            for chunk in chunks
        ]
        
        # ChromaDB 的 add 是同步方法
        try:
            collection.add(
                ids=ids,
                embeddings=embeddings,
                documents=texts,
                metadatas=metadatas,
            )
        except Exception as e:
            print(f"[VectorStore ERROR] ChromaDB add failed: {type(e).__name__}: {e}")
            raise
        
        print(f"[VectorStore] Successfully added {len(ids)} chunks")
        return ids
    
    async def search(
        self, 
        query: str, 
        top_k: int = 5, 
        collection_name: str = "documents",
        filter_metadata: dict | None = None,
    ) -> list[dict]:
        """向量检索"""
        collection = self.get_or_create_collection(collection_name)
        
        # 嵌入查询
        query_embedding = await self.embedding_function.embed([query])
        
        # 检索
        results = collection.query(
            query_embeddings=query_embedding,
            n_results=top_k,
            where=filter_metadata,
            include=["documents", "metadatas", "distances"],
        )
        
        # 格式化结果
        formatted = []
        if results["ids"] and results["ids"][0]:
            for i, doc_id in enumerate(results["ids"][0]):
                formatted.append({
                    "id": doc_id,
                    "content": results["documents"][0][i],
                    "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
                    "distance": results["distances"][0][i] if results["distances"] else 0,
                    "score": 1 - results["distances"][0][i] if results["distances"] else 1,
                })
        
        return formatted
    
    async def delete_by_document_id(self, doc_id_prefix: str, collection_name: str = "documents") -> int:
        """删除文档相关的所有块"""
        collection = self.get_or_create_collection(collection_name)
        
        # 获取该文档的所有块
        results = collection.get(where={"document_id": {"$contains": doc_id_prefix}})
        
        if results and results["ids"]:
            collection.delete(ids=results["ids"])
            return len(results["ids"])
        
        return 0
    
    def list_documents(self, collection_name: str = "documents") -> list[dict]:
        """列出所有文档"""
        collection = self.get_or_create_collection(collection_name)
        
        # 获取所有唯一文档 ID
        results = collection.get(include=["metadatas"])
        
        # 按 doc_id 分组，统计实际 chunk 数并取第一个 chunk 的元数据
        doc_map: dict[str, dict] = {}
        for metadata in (results.get("metadatas") or []):
            doc_id = metadata.get("doc_id")
            if not doc_id:
                continue
            if doc_id not in doc_map:
                doc_map[doc_id] = {
                    "doc_id": doc_id,
                    "filename": metadata.get("filename", "unknown"),
                    "type": metadata.get("type", "unknown"),
                    "created_at": metadata.get("created_at", ""),
                    "file_size": metadata.get("file_size", 0),
                    "_count": 0,
                }
            doc_map[doc_id]["_count"] += 1

        documents = []
        for d in doc_map.values():
            # 优先使用存储的 chunk_count，否则用实际统计数
            documents.append({
                "doc_id": d["doc_id"],
                "filename": d["filename"],
                "type": d["type"],
                "chunk_count": d["_count"],
                "created_at": d["created_at"],
                "file_size": d["file_size"],
            })

        return documents
    
    def get_collection_stats(self, collection_name: str = "documents") -> dict:
        """获取集合统计"""
        collection = self.get_or_create_collection(collection_name)
        return {
            "name": collection.name,
            "count": collection.count(),
            "persist_directory": str(self.persist_directory),
        }


# 默认向量存储实例
_vector_store: ChromaVectorStore | None = None


def get_vector_store(
    persist_directory: str = "./data/chroma",
    embedding_function: EmbeddingFunction | None = None,
) -> ChromaVectorStore:
    """获取向量存储实例"""
    global _vector_store
    if _vector_store is None:
        _vector_store = ChromaVectorStore(persist_directory, embedding_function)
    return _vector_store
