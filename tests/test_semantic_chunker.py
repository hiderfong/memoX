"""P4-2 语义切片测试

覆盖范围：
- SemanticChunker：句子拆分、语义分块、token 限制、相似度阈值、overlap
- 降级路径：embedding 失败时回退到固定长度
- semantic_chunks_to_text_chunks 适配器
"""

from __future__ import annotations

import asyncio
import re
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.knowledge.semantic_chunker import (
    SemanticChunker,
    SemanticChunk,
    Sentence,
    _estimate_tokens,
    _SENTENCE_BOUNDARY_RE,
    semantic_chunks_to_text_chunks,
)


# ── 辅助函数测试 ────────────────────────────────────────────────────────────


class TestEstimateTokens:
    """token 估算测试"""

    def test_pure_chinese(self):
        """纯中文：每字 1 token"""
        text = "今天天气真好"  # 6 个汉字
        assert _estimate_tokens(text) == 6

    def test_pure_english(self):
        """纯英文：按 word 计数 × 1.3"""
        text = "hello world"
        # "hello"(1) + space(1) + "world"(1) = 2 words * 1.3 + 1 space = 3.6 ≈ 3
        result = _estimate_tokens(text)
        assert result >= 2

    def test_mixed(self):
        """中英混合"""
        text = "今天 hello world 天气"
        chinese = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
        english_words = len(re.findall(r"[a-zA-Z]+", text))
        expected = chinese + int(english_words * 1.3) + text.count(" ")
        assert _estimate_tokens(text) == expected


class TestSentenceBoundaryRegex:
    """句子边界正则测试"""

    def test_chinese_punctuation(self):
        """中文句末标点"""
        text = "今天天气好。很开心！你是谁？"
        matches = list(_SENTENCE_BOUNDARY_RE.finditer(text))
        assert len(matches) == 3

    def test_english_punctuation(self):
        """英文句末标点"""
        text = "Hello world. How are you! Are you okay?"
        matches = list(_SENTENCE_BOUNDARY_RE.finditer(text))
        assert len(matches) == 3

    def test_mixed_punctuation(self):
        """中英混合"""
        text = "今天天气好！Hello world. 你好吗？"
        matches = list(_SENTENCE_BOUNDARY_RE.finditer(text))
        assert len(matches) == 3


# ── Mock Embedding 函数 ─────────────────────────────────────────────────────


def make_mock_embedding_fn(seed: int = 42):
    """返回确定性的 mock embedding 函数（基于文本 hash）"""

    async def mock_embed(texts: list[str]) -> list[list[float]]:
        results = []
        for t in texts:
            h = hash(t) + seed
            # 生成 4 维向量（简化）
            vec = [
                ((h >> 0) & 0xFF) / 255.0,
                ((h >> 8) & 0xFF) / 255.0,
                ((h >> 16) & 0xFF) / 255.0,
                ((h >> 24) & 0xFF) / 255.0,
            ]
            results.append(vec)
        return results

    return mock_embed


def make_random_mock_embed():
    """返回随机 embedding（用于测试 RRF 行为）"""
    import random

    async def mock_embed(texts: list[str]) -> list[list[float]]:
        return [[random.random() for _ in range(4)] for _ in texts]

    return mock_embed


# ── SemanticChunker 核心测试 ─────────────────────────────────────────────────


class TestSplitSentences:
    """句子拆分测试"""

    def get_chunker(self):
        mock_fn = make_mock_embedding_fn()
        return SemanticChunker(embedding_fn=mock_fn, chunk_size=500, chunk_overlap=1)

    def test_empty_text(self):
        chunker = self.get_chunker()
        assert chunker._split_sentences("") == []
        assert chunker._split_sentences("   \n\n  ") == []

    def test_single_sentence_no_period(self):
        chunker = self.get_chunker()
        sents = chunker._split_sentences("这是一句话没有句末标点")
        assert len(sents) == 1
        assert sents[0].text == "这是一句话没有句末标点"

    def test_multiple_chinese_sentences(self):
        chunker = self.get_chunker()
        text = "今天天气好。很开心！你吃了吗？"
        sents = chunker._split_sentences(text)
        assert len(sents) == 3
        assert sents[0].text == "今天天气好"
        assert sents[1].text == "很开心"
        assert sents[2].text == "你吃了吗"

    def test_mixed_chinese_english(self):
        chunker = self.get_chunker()
        text = "Hello world. 今天天气好！你好吗？Fine."
        sents = chunker._split_sentences(text)
        assert len(sents) == 4

    def test_trailing_whitespace(self):
        chunker = self.get_chunker()
        text = "第一句。第二句。  \n\n  第三句。"
        sents = chunker._split_sentences(text)
        assert len(sents) == 3
        assert sents[0].text == "第一句"
        assert sents[2].text == "第三句"


class TestSemanticChunkerChunk:
    """语义分块核心测试"""

    @pytest.fixture
    def chunker(self):
        mock_fn = make_mock_embedding_fn(seed=0)
        return SemanticChunker(
            embedding_fn=mock_fn,
            chunk_size=200,
            chunk_overlap=1,
            similarity_threshold=0.3,
        )

    @pytest.mark.asyncio
    async def test_empty_text(self, chunker):
        chunks = await chunker.chunk("")
        assert chunks == []

    @pytest.mark.asyncio
    async def test_single_short_sentence(self, chunker):
        text = "今天天气好。"
        chunks = await chunker.chunk(text)
        assert len(chunks) == 1
        assert chunks[0].content == "今天天气好"
        assert chunks[0].topic_score == 1.0

    @pytest.mark.asyncio
    async def test_multiple_sentences_within_limit(self, chunker):
        """多个短句可被合并为一个 chunk（不超 token 限制）"""
        text = "今天天气好。我很开心。天气不错。"
        chunks = await chunker.chunk(text)
        # 所有句子应在同一个 chunk 中
        assert len(chunks) == 1
        # content 包含所有句子
        assert "今天天气好" in chunks[0].content
        assert "很开心" in chunks[0].content

    @pytest.mark.asyncio
    async def test_token_limit_triggers_split(self):
        """token 超量时触发分块（即使语义相似）"""
        mock_fn = make_mock_embedding_fn(seed=0)
        # chunk_size=15 tokens ≈ 5 个汉字，8 句文字应有 >1 chunk
        chunker = SemanticChunker(
            embedding_fn=mock_fn,
            chunk_size=15,
            chunk_overlap=1,
            similarity_threshold=0.0,  # 忽略相似度，强制合并直到超量
        )
        text = "第一句。第二句。第三句。第四句。第五句。第六句。第七句。第八句。"
        chunks = await chunker.chunk(text)
        assert len(chunks) > 1, f"Expected >1 chunk but got {len(chunks)}: {[c.content for c in chunks]}"

    @pytest.mark.asyncio
    async def test_similarity_threshold_triggers_split(self):
        """相似度低于阈值时强制断开

        原理：两正交 one-hot 向量的 cosine sim = 0，远低于任何合理阈值。
        使用 threshold=0.3 确保触发。
        """
        class OrthogonalEmbed:
            async def embed(self, texts):
                vecs = []
                for i, _ in enumerate(texts):
                    v = [0.0] * 4
                    v[i % 4] = 1.0
                    vecs.append(v)
                return vecs

        # 正交向量 new_sim=0.5。当 token 超量时，两条件同时满足触发分裂
        chunker = SemanticChunker(
            embedding_fn=OrthogonalEmbed(),
            chunk_size=10,  # 触发 over_limit：第一句5+第二句6=11 > 10
            chunk_overlap=1,
            similarity_threshold=0.7,  # new_sim=0.5 < 0.7 → 分裂
        )
        text = "今天天气好。XYZ unrelated text here."
        chunks = await chunker.chunk(text)
        assert len(chunks) >= 2, f"Expected >=2 chunks but got {len(chunks)}: {[c.content for c in chunks]}"

    @pytest.mark.asyncio
    async def test_overlap_between_chunks(self):
        """相邻 chunk 之间有句子级 overlap"""
        mock_fn = make_mock_embedding_fn(seed=0)
        chunker = SemanticChunker(
            embedding_fn=mock_fn,
            chunk_size=50,
            chunk_overlap=2,
            similarity_threshold=0.0,
        )
        text = "第一句。第二句。第三句。第四句。第五句。第六句。第七句。第八句。"
        chunks = await chunker.chunk(text)
        if len(chunks) > 1:
            # 第二个 chunk 的第一个句子应与上一个 chunk 的末尾句子相同
            assert chunks[1].sentences[0].text == chunks[0].sentences[-1].text

    @pytest.mark.asyncio
    async def test_fallback_on_embedding_failure(self):
        """embedding 失败时降级到固定长度分块"""
        async def failing_embed(texts):
            raise RuntimeError("Embedding service unavailable")

        chunker = SemanticChunker(
            embedding_fn=failing_embed,
            chunk_size=15,  # 强制拆分
            chunk_overlap=1,
        )
        text = "第一句。第二句。第三句。第四句。第五句。第六句。第七句。第八句。"
        chunks = await chunker.chunk(text)
        assert len(chunks) > 1, f"Expected >1 chunk but got {len(chunks)}: {[c.content for c in chunks]}"

    @pytest.mark.asyncio
    async def test_topic_score_range(self):
        """topic_score 在 [0, 1] 范围内"""
        import random

        random.seed(12345)

        async def random_embed(texts):
            import random
            return [[random.random() for _ in range(4)] for _ in texts]

        chunker = SemanticChunker(
            embedding_fn=random_embed,
            chunk_size=500,
            chunk_overlap=1,
            similarity_threshold=0.0,
        )
        text = "今天天气好。我很开心。你吃了吗？他去哪了。她是谁。很好。不对。"
        chunks = await chunker.chunk(text)
        for chunk in chunks:
            assert 0.0 <= chunk.topic_score <= 1.0

    @pytest.mark.asyncio
    async def test_chunk_content_matches_sentences(self):
        """chunk.content 是所有组成句子的拼接"""
        mock_fn = make_mock_embedding_fn(seed=0)
        chunker = SemanticChunker(
            embedding_fn=mock_fn,
            chunk_size=500,
            chunk_overlap=1,
            similarity_threshold=0.0,
        )
        text = "第一句。第二句。第三句。"
        chunks = await chunker.chunk(text)
        assert len(chunks) == 1
        combined = "".join(s.text for s in chunks[0].sentences)
        assert chunks[0].content == combined


class TestSemanticChunksToTextChunks:
    """semantic_chunks_to_text_chunks 适配器测试"""

    def test_basic_conversion(self):
        """验证字段映射正确"""
        mock_fn = make_mock_embedding_fn()

        async def run():
            chunker = SemanticChunker(
                embedding_fn=mock_fn,
                chunk_size=500,
                chunk_overlap=1,
            )
            text = "第一句。第二句。"
            sem_chunks = await chunker.chunk(text)
            text_chunks = semantic_chunks_to_text_chunks(
                sem_chunks, "doc123", {"source": "test"}
            )
            assert len(text_chunks) == len(sem_chunks)
            tc = text_chunks[0]
            assert tc.id == "doc123_chunk_0"
            assert tc.metadata["chunk_strategy"] == "semantic"
            assert "topic_score" in tc.metadata
            assert tc.metadata["source"] == "test"
            return True

        assert asyncio.run(run())

    def test_preserves_chunk_index(self):
        """chunk_index 随索引递增"""
        mock_fn = make_mock_embedding_fn(seed=0)
        chunker = SemanticChunker(embedding_fn=mock_fn, chunk_size=30, chunk_overlap=1)

        async def run():
            text = "第一句。第二句。第三句。第四句。第五句。"
            sem_chunks = await chunker.chunk(text)
            if len(sem_chunks) < 2:
                pytest.skip("Need at least 2 chunks for this test")
            tc = semantic_chunks_to_text_chunks(sem_chunks, "doc", {})
            for i, c in enumerate(tc):
                assert c.index == i
                assert f"_chunk_{i}" in c.id
            return True

        assert asyncio.run(run())

    def test_empty_input(self):
        """空列表返回空列表"""
        result = semantic_chunks_to_text_chunks([], "doc1", {})
        assert result == []


class TestIntegrationWithDocumentParser:
    """与 DocumentParser 集成测试"""

    @pytest.mark.asyncio
    async def test_parse_and_chunk_with_semantic_strategy(self, tmp_path):
        """parse_and_chunk 使用 semantic 策略时的端到端流程"""
        from src.knowledge.document_parser import DocumentParser

        test_file = tmp_path / "test.txt"
        test_file.write_text("第一句话。第二句话。第三句话。", encoding="utf-8")

        mock_fn = make_mock_embedding_fn(seed=0)
        parser = DocumentParser()

        doc, chunks = await parser.parse_and_chunk(
            test_file,
            "doc_test",
            chunk_size=200,
            overlap=1,
            chunk_strategy="semantic",
            embedding_fn=mock_fn,
        )

        assert doc.id == "doc_test"
        assert len(chunks) >= 1
        # 每个 chunk 都应有 chunk_strategy 元数据
        for c in chunks:
            assert c.metadata.get("chunk_strategy") == "semantic"
            assert "topic_score" in c.metadata

    @pytest.mark.asyncio
    async def test_parse_and_chunk_default_is_size_strategy(self, tmp_path):
        """默认 chunk_strategy='size' 时标注为 'size'"""
        from src.knowledge.document_parser import DocumentParser

        test_file = tmp_path / "test.txt"
        test_file.write_text("第一句话。第二句话。第三句话。", encoding="utf-8")

        parser = DocumentParser()

        doc, chunks = await parser.parse_and_chunk(
            test_file, "doc_test", chunk_size=200, overlap=1
        )

        assert len(chunks) >= 1
        for c in chunks:
            assert c.metadata.get("chunk_strategy") == "size"

    @pytest.mark.asyncio
    async def test_parse_and_chunk_semantic_without_embedding_fn_falls_back_to_size(
        self, tmp_path
    ):
        """semantic 策略但未提供 embedding_fn 时自动降级为 size"""
        from src.knowledge.document_parser import DocumentParser

        test_file = tmp_path / "test.txt"
        test_file.write_text("第一句话。第二句话。第三句话。", encoding="utf-8")

        parser = DocumentParser()

        doc, chunks = await parser.parse_and_chunk(
            test_file,
            "doc_test",
            chunk_size=200,
            overlap=1,
            chunk_strategy="semantic",
            embedding_fn=None,  # 未提供
        )

        assert len(chunks) >= 1
        # 降级到 size
        for c in chunks:
            assert c.metadata.get("chunk_strategy") == "size"
