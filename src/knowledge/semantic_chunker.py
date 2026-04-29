"""语义切片器（Semantic Chunker）

基于句子 embedding 的语义分块器。
工作流程：
1. 将文档按句子边界拆分（中英文标点均支持）
2. 用 embedding 模型将每个句子向量化
3. 贪婪式地将相邻句子归并为 chunk，以语义相似度为合并依据
4. 当添加下一句会导致 token 超量且语义相似度低于阈值时，断开 chunk

与固定长度切片的区别：保护段落/主题完整性，同一主题的句子不会被强行拆散。
"""

from __future__ import annotations

import asyncio
import re
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .vector_store import EmbeddingFunction


# 句子分割正则：中文句号/感叹号/问号/分号 + 英文对应符号
_SENTENCE_BOUNDARY_RE = re.compile(
    r"(?<=[。！？；；\!\?\.])\s*",
    re.UNICODE,
)


# 估算 token 数（中英文混合：中文 1 char ≈ 1 token，英文 1 word ≈ 1.3 token）
def _estimate_tokens(text: str) -> int:
    chinese_chars = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    english_words = len(re.findall(r"[a-zA-Z]+", text))
    return chinese_chars + int(english_words * 1.3) + text.count(" ")  # type: ignore[return-value]


@dataclass
class Sentence:
    """单个句子"""
    text: str
    start: int      # 在原文中的起始位置
    end: int        # 在原文中的结束位置


@dataclass
class SemanticChunk:
    """语义切片结果"""
    sentences: list[Sentence]
    content: str          # 所有句子拼接后的文本
    topic_score: float    # 切片内部语义一致性得分（0-1，越高越连贯）


class SemanticChunker:
    """语义切片器

    参数：
        embedding_fn: 向量化函数（接受 list[str]，返回 list[list[float]]）
        chunk_size: 最大 token 数（软限制，估算值）
        chunk_overlap: 两个相邻 chunk 之间的句子重叠数量（保持上下文连贯）
        similarity_threshold: 相邻 chunk 间的最小语义相似度阈值，
                             低于此值则断开（0.0-1.0，越高越严格）

    用法：
        chunker = SemanticChunker(embedding_fn=embedding_fn)
        chunks = await chunker.chunk("很长的文档文本...")
    """

    def __init__(
        self,
        embedding_fn: EmbeddingFunction,
        chunk_size: int = 500,
        chunk_overlap: int = 1,
        similarity_threshold: float = 0.5,
    ):
        self.embedding_fn = embedding_fn
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.similarity_threshold = similarity_threshold

    # ── 公开 API ──────────────────────────────────────────────────────────────

    async def chunk(self, text: str) -> list[SemanticChunk]:
        """对文本进行语义分块，返回语义块列表"""
        if not text or not text.strip():
            return []

        sentences = self._split_sentences(text)
        if not sentences:
            return []

        if len(sentences) == 1:
            return [SemanticChunk(
                sentences=sentences,
                content=sentences[0].text,
                topic_score=1.0,
            )]

        # 向量化所有句子
        sentence_texts = [s.text for s in sentences]
        try:
            embeddings = await self.embedding_fn.embed(sentence_texts)
        except Exception:
            # embedding 失败时降级到纯长度分块
            return self._fallback_chunk(text)

        if not embeddings or len(embeddings) != len(sentences):
            return self._fallback_chunk(text)

        # 计算余弦相似度（dot product，已归一化向量）
        def cosine_sim(a: list[float], b: list[float]) -> float:
            return sum(x * y for x, y in zip(a, b))

        # 贪婪式分块
        chunks: list[SemanticChunk] = []
        current_sentences: list[Sentence] = []
        current_embeddings: list[list[float]] = []
        current_tokens: int = 0

        for sent, emb in zip(sentences, embeddings, strict=False):
            sent_tokens = _estimate_tokens(sent.text)

            if not current_sentences:
                # 第一个句子：直接开始新 chunk
                current_sentences.append(sent)
                current_embeddings.append(emb)
                current_tokens = sent_tokens
                continue

            # 计算新句子与当前 chunk 主题向量的相似度
            topic_vec = [
                sum(dim) / len(current_embeddings)
                for dim in zip(*current_embeddings)
            ]
            sim = cosine_sim(emb, topic_vec)

            # 尝试加入当前 chunk
            new_tokens = current_tokens + sent_tokens
            new_sim = (current_tokens * sim + sent_tokens * 1.0) / new_tokens  # 加权相似度

            # 决策：超 token 限制时，优先保留下一个 chunk 的语义完整性
            over_limit = new_tokens > self.chunk_size

            # 判断是否应该分裂：超过 token 限制 且 新句子与当前 chunk 主题差异大
            # 注意：即使未超限制，若相似度极低（正交嵌入）也不应强行合并
            low_similarity = new_sim < self.similarity_threshold

            if over_limit and low_similarity and len(current_sentences) >= 1:
                # 断开：保存当前 chunk，开启新的
                chunks.append(self._make_chunk(current_sentences, current_embeddings))
                # overlap
                overlap_n = min(self.chunk_overlap, len(current_sentences))
                current_sentences = current_sentences[-overlap_n:]
                current_embeddings = current_embeddings[-overlap_n:]
                current_tokens = sum(_estimate_tokens(s.text) for s in current_sentences)
                # 新句子加入（不重蹈 current_embeddings 的问题）
                if current_sentences and _estimate_tokens(current_sentences[0].text) + sent_tokens > self.chunk_size:
                    # overlap 之后仍然超限 → 强制单句作为 chunk
                    chunks.extend(self._force_split([sent], [emb]))
                    current_sentences = []
                    current_embeddings = []
                    current_tokens = 0
                    continue
                current_sentences.append(sent)
                current_embeddings.append(emb)
                current_tokens += sent_tokens
            else:
                current_sentences.append(sent)
                current_embeddings.append(emb)
                current_tokens = new_tokens

        # 收尾最后一个 chunk
        if current_sentences:
            chunks.append(self._make_chunk(current_sentences, current_embeddings))

        return chunks

    # ── 内部方法 ─────────────────────────────────────────────────────────────

    def _split_sentences(self, text: str) -> list[Sentence]:
        """按中英文句末标点拆分文本为句子列表（保持位置信息）

        句末标点：中文 "。！？；" / 英文 ".!?"
        空白换行不计入句子内容。
        """
        sentences: list[Sentence] = []
        # 找到所有句末标点的位置
        SENTENCE_END = re.compile(r"[。！？；\.!?]")
        matches = list(SENTENCE_END.finditer(text))
        if not matches:
            if text.strip():
                return [Sentence(text=text.strip(), start=0, end=len(text))]
            return []

        prev_end = 0
        for m in matches:
            # 句子的文本范围：prev_end 到标点之前（不含标点）
            sent_text = text[prev_end:m.start()].strip()
            if sent_text:
                sentences.append(Sentence(text=sent_text, start=prev_end, end=m.start()))
            prev_end = m.end()  # 跳过标点本身（m.end() = m.start()+1）

        # 尾部残余（最后一个标点之后）
        remaining = text[prev_end:].strip()
        if remaining:
            sentences.append(Sentence(text=remaining, start=prev_end, end=len(text)))
        return sentences

    def _make_chunk(self, sentences: list[Sentence], embeddings: list[list[float]]) -> SemanticChunk:
        """从句子列表构建 SemanticChunk，计算内容一致性的主题得分"""
        content = "".join(s.text for s in sentences)
        if len(embeddings) <= 1:
            topic_score = 1.0
        else:
            # 主题向量 = 所有句子 embedding 的均值
            topic_vec = [sum(dim) / len(embeddings) for dim in zip(*embeddings)]
            # 各句子与主题向量的平均余弦相似度
            def cosine_sim(a, b):
                return sum(x * y for x, y in zip(a, b))
            scores = [cosine_sim(e, topic_vec) for e in embeddings]
            topic_score = sum(scores) / len(scores)
        return SemanticChunk(
            sentences=sentences,
            content=content,
            topic_score=topic_score,
        )

    def _force_split(self, sentences: list[Sentence], embeddings: list[list[float]]) -> list[SemanticChunk]:
        """强制将超长句子列表逐条拆分为独立 chunk（不得已的下策）"""
        results: list[SemanticChunk] = []
        for s, e in zip(sentences, embeddings, strict=False):
            results.append(SemanticChunk(sentences=[s], content=s.text, topic_score=1.0))
        return results

    def _fallback_chunk(self, text: str) -> list[SemanticChunk]:
        """embedding 失败时的降级方案：按固定 token 数 + 句子边界切分"""
        sentences = self._split_sentences(text)
        chunks: list[SemanticChunk] = []
        current: list[Sentence] = []
        current_tokens = 0
        for sent in sentences:
            sent_tokens = _estimate_tokens(sent.text)
            if current_tokens + sent_tokens > self.chunk_size and current:
                chunks.append(self._make_chunk(current, []))
                overlap_n = min(self.chunk_overlap, len(current))
                current = current[-overlap_n:]
                current_tokens = sum(_estimate_tokens(s.text) for s in current)
            current.append(sent)
            current_tokens += sent_tokens
        if current:
            chunks.append(self._make_chunk(current, []))
        return chunks


# ── 与现有 TextChunk 的适配器 ────────────────────────────────────────────────


def semantic_chunks_to_text_chunks(
    semantic_chunks: list[SemanticChunk],
    doc_id: str,
    base_metadata: dict,
) -> list:
    """将 SemanticChunk 列表转换为 TextChunk 列表（供 RAGEngine 使用）

    每个 TextChunk 的 metadata 中增加：
        chunk_strategy: "semantic"
        topic_score: float
    """
    from .document_parser import TextChunk

    return [
        TextChunk(
            id=f"{doc_id}_chunk_{i}",
            content=chunk.content,
            metadata={
                **base_metadata,
                "chunk_index": i,
                "chunk_strategy": "semantic",
                "topic_score": chunk.topic_score,
                "sentence_count": len(chunk.sentences),
            },
            index=i,
        )
        for i, chunk in enumerate(semantic_chunks)
    ]
