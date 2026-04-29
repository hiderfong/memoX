"""BM25 全文索引管理器

使用 rank_bm25 库构建和维护 BM25 倒排索引，
索引持久化为 pickle 文件，支持增量更新（添加/删除文档）。

BM25（Best Matching 25）是一种经典的信息检索模型，
比纯向量相似度在关键词精确匹配上更可靠，常与向量检索混合使用提升召回。
"""

from __future__ import annotations

import pickle
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rank_bm25 import BM25Plus

# 令牌化：用非字母字符拆分，转小写，去除空 token
_TOKENIZER_PATTERN = re.compile(r"\W+", re.UNICODE)


def _tokenize(text: str) -> list[str]:
    """中英文混合分词（空格/标点拆分 + 小写化）"""
    return [t.lower() for t in _TOKENIZER_PATTERN.split(text) if t.strip()]


@dataclass
class ChunkEntry:
    """索引中的单个 chunk 条目"""
    chunk_id: str
    doc_id: str
    content: str
    metadata: dict = field(default_factory=dict)


@dataclass
class BM25Indexer:
    """BM25 索引管理器

    索引存储在 `{persist_dir}/bm25_index.pkl`，包含：
    - `corpus: list[ChunkEntry]` — chunk 列表（id → entry 映射）
    - `tokenized_corpus: list[list[str]]` — 对应的分词结果
    - `version: int` — 索引版本号（单调递增）

    线程安全（读写使用锁）。
    """

    persist_path: Path
    version: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    _corpus: dict[str, ChunkEntry] = field(default_factory=dict, repr=False)
    _tokenized: dict[str, list[str]] = field(default_factory=dict, repr=False)
    _bm25: BM25Plus | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self.persist_path = Path(self.persist_path)
        self.persist_path.parent.mkdir(parents=True, exist_ok=True)
        self._load()

    # ── 持久化 ────────────────────────────────────────────────────────────────

    def _load(self) -> None:
        """从 pickle 文件加载索引（如果存在）"""
        if not self.persist_path.exists():
            return
        try:
            data = pickle.loads(self.persist_path.read_bytes())
            self._corpus = {e.chunk_id: e for e in data["entries"]}
            self._tokenized = {e.chunk_id: _tokenize(e.content) for e in data["entries"]}
            self.version = data.get("version", 0)
            self._rebuild_bm25()
        except Exception as e:
            # 索引损坏时静默重建，不阻塞服务
            import loguru

            loguru.logger.warning(f"[BM25] 索引加载失败，将重建: {e}")

    def _save(self) -> None:
        """将当前索引写入 pickle 文件"""
        entries = list(self._corpus.values())
        data = {"entries": entries, "version": self.version}
        self.persist_path.write_bytes(pickle.dumps(data))

    # ── BM25 内部操作 ─────────────────────────────────────────────────────────

    def _rebuild_bm25(self) -> None:
        """根据当前 corpus 重建 BM25 索引"""
        from rank_bm25 import BM25Plus

        if not self._corpus:
            self._bm25 = None
            return
        corpus_texts = [self._tokenized[cid] for cid in self._corpus]
        self._bm25 = BM25Plus(corpus_texts, k1=1.5, delta=0.5)

    # ── 公开 API ──────────────────────────────────────────────────────────────

    def add_chunks(self, chunks: list[ChunkEntry]) -> None:
        """批量添加 chunks 到索引（增量更新，无需全量重建）"""
        with self._lock:
            new_ids: set[str] = set()
            for chunk in chunks:
                if chunk.chunk_id in self._corpus:
                    continue  # 已有则跳过（应该走 update）
                self._corpus[chunk.chunk_id] = chunk
                self._tokenized[chunk.chunk_id] = _tokenize(chunk.content)
                new_ids.add(chunk.chunk_id)

            if new_ids:
                # 增量更新 BM25：只对新增 tokenized 文本重建
                self._rebuild_bm25()
                self.version += 1
                self._save()

    def delete_chunks(self, chunk_ids: list[str]) -> None:
        """从索引中删除指定 chunk_ids"""
        with self._lock:
            for cid in chunk_ids:
                self._corpus.pop(cid, None)
                self._tokenized.pop(cid, None)
            self._rebuild_bm25()
            self.version += 1
            self._save()

    def delete_by_doc_id(self, doc_id: str) -> list[str]:
        """删除某文档的所有 chunks，返回被删除的 chunk_id 列表"""
        to_delete = [cid for cid, e in self._corpus.items() if e.doc_id == doc_id]
        self.delete_chunks(to_delete)
        return to_delete

    def search(self, query: str, top_k: int = 20) -> list[tuple[str, float]]:
        """BM25 检索：返回 [(chunk_id, score)] 按得分降序

        chunk_id 用于后续与向量检索结果做 RRF 融合。
        """
        with self._lock:
            if self._bm25 is None:
                return []
            tokenized_query = _tokenize(query)
            raw_scores = self._bm25.get_scores(tokenized_query)
            # 按分数降序，取 top_k
            sorted_indices = sorted(range(len(raw_scores)), key=lambda i: raw_scores[i], reverse=True)
            chunk_ids = list(self._corpus.keys())
            return [
                (chunk_ids[i], raw_scores[i])
                for i in sorted_indices[:top_k]
                if raw_scores[i] > 0
            ]

    def get_chunk(self, chunk_id: str) -> ChunkEntry | None:
        """根据 chunk_id 获取原始 entry（用于融合后还原 content/metadata）"""
        return self._corpus.get(chunk_id)

    @property
    def size(self) -> int:
        """当前索引中的 chunk 总数"""
        return len(self._corpus)


# ── 全局单例实例 ────────────────────────────────────────────────────────────

_bm25_indexer: BM25Indexer | None = None
_indexer_lock = threading.Lock()


def get_bm25_indexer(persist_path: str = "./data/bm25_index.pkl") -> BM25Indexer:
    """获取 BM25 索引全局单例"""
    global _bm25_indexer
    if _bm25_indexer is None:
        with _indexer_lock:
            if _bm25_indexer is None:
                _bm25_indexer = BM25Indexer(persist_path=Path(persist_path))
    return _bm25_indexer


def init_bm25_indexer(persist_path: str = "./data/bm25_index.pkl") -> BM25Indexer:
    """初始化 BM25 索引（可强制重建）"""
    global _bm25_indexer
    with _indexer_lock:
        _bm25_indexer = BM25Indexer(persist_path=Path(persist_path))
    return _bm25_indexer
