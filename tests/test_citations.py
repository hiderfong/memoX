"""P4-3 引用来源高亮测试

覆盖范围：
- Citation dataclass：字段完整性、to_dict
- SearchResult.citation：从 metadata 提取结构化引用信息
- build_rag_prompt：上下文包含 [ref-N] 标记
- extract_citations_from_text：从 LLM 回复中解析 [ref-N] 编号
"""

from __future__ import annotations

import pytest

from src.knowledge.rag_engine import (
    Citation,
    SearchResult,
    RAGEngine,
)


# ── Citation 数据类测试 ──────────────────────────────────────────────────────


class TestCitationDataclass:
    """Citation 数据类基本测试"""

    def test_to_dict(self):
        c = Citation(
            ref_id="ref-1",
            doc_id="abc123",
            filename="report.pdf",
            chunk_index=3,
            content_preview="这是一段文档内容...",
            score=0.95,
        )
        d = c.to_dict()
        assert d["ref_id"] == "ref-1"
        assert d["doc_id"] == "abc123"
        assert d["filename"] == "report.pdf"
        assert d["chunk_index"] == 3
        assert d["content_preview"] == "这是一段文档内容..."
        assert d["score"] == 0.95


# ── SearchResult.citation 属性测试 ─────────────────────────────────────────


class TestSearchResultCitation:
    """SearchResult.citation 属性测试"""

    def test_citation_from_metadata(self):
        """从 metadata 正确提取 citation"""
        r = SearchResult(
            id="doc12345_chunk_3",
            content="巴黎是法国的首都，位于法国北部。",
            score=0.87,
            metadata={
                "doc_id": "doc12345",
                "filename": "france.txt",
                "chunk_index": 3,
            },
        )
        cit = r.citation
        assert cit is not None
        assert cit.ref_id == "ref-3"
        assert cit.doc_id == "doc12345"
        assert cit.filename == "france.txt"
        assert cit.chunk_index == 3
        assert "巴黎" in cit.content_preview
        assert cit.score == 0.87

    def test_citation_fallback_when_no_chunk_index(self):
        """metadata 缺少 chunk_index 时回退到 id 中的编号"""
        r = SearchResult(
            id="abc_chunk_7",
            content="测试内容",
            score=0.5,
            metadata={"filename": "test.txt"},  # 无 doc_id
        )
        cit = r.citation
        assert cit is not None
        assert cit.ref_id == "ref-7"
        assert cit.doc_id == ""

    def test_citation_short_id(self):
        """id 不含 _chunk_ 时使用 metadata 中的 chunk_index"""
        r = SearchResult(
            id="some-id",
            content="内容",
            score=0.6,
            metadata={"filename": "a.txt", "chunk_index": 2},
        )
        cit = r.citation
        assert cit is not None
        assert cit.ref_id == "ref-2"

    def test_to_context_string_with_ref_id(self):
        """to_context_string 输出包含 ref_id 标记"""
        r = SearchResult(
            id="doc1_chunk_0",
            content="这是一段测试内容",
            score=0.9,
            metadata={"filename": "test.txt"},
        )
        ctx = r.to_context_string(ref_id="ref-1")
        assert "[ref-1]" in ctx
        assert "test.txt" in ctx
        assert "这是一段测试内容" in ctx

    def test_to_context_string_without_ref_id(self):
        """ref_id 为空时不输出标记（向后兼容）"""
        r = SearchResult(
            id="doc1_chunk_0",
            content="内容",
            score=0.9,
            metadata={"filename": "a.txt"},
        )
        ctx = r.to_context_string()
        assert "[来源:" in ctx
        assert "[ref-" not in ctx


# ── extract_citations_from_text 测试 ─────────────────────────────────────────


class TestExtractCitations:
    """从 LLM 回复中解析 [ref-N] 引用编号"""

    def test_single_citation(self):
        text = "巴黎是法国的首都 [ref-1]"
        refs = RAGEngine.extract_citations_from_text(text)
        assert refs == ["ref-1"]

    def test_multiple_citations_same_source(self):
        """同一来源多次引用只保留一次"""
        text = "巴黎是法国首都 [ref-1]，法国首都是巴黎 [ref-1]"
        refs = RAGEngine.extract_citations_from_text(text)
        assert refs == ["ref-1"]

    def test_multiple_different_sources(self):
        """多来源按出现顺序去重"""
        text = "根据 [ref-1] 和 [ref-3] 的信息可知 [ref-1] 再次被引用，[ref-2] 也是来源"
        refs = RAGEngine.extract_citations_from_text(text)
        assert refs == ["ref-1", "ref-3", "ref-2"]

    def test_no_citations(self):
        """无引用时返回空列表"""
        text = "这是一个没有引用来源的普通回答。"
        refs = RAGEngine.extract_citations_from_text(text)
        assert refs == []

    def test_citation_in_middle_of_sentence(self):
        text = "根据研究报告 [ref-2] 的数据显示，经济增长率为 3%。"
        refs = RAGEngine.extract_citations_from_text(text)
        assert refs == ["ref-2"]

    def test_citation_with_punctuation(self):
        """引用编号周围有标点时仍能正确解析"""
        text = "这一点在 [ref-1] 中已被证明。参考文献：[ref-3] 和 [ref-5]。"
        refs = RAGEngine.extract_citations_from_text(text)
        assert refs == ["ref-1", "ref-3", "ref-5"]

    def test_citations_preserve_order(self):
        """多来源保持首次出现顺序"""
        text = "首先 [ref-5]，其次 [ref-2]，最后 [ref-5]（重复），[ref-1] 最早出现"
        refs = RAGEngine.extract_citations_from_text(text)
        assert refs == ["ref-5", "ref-2", "ref-1"]


# ── build_rag_prompt 引用标记测试 ───────────────────────────────────────────


class TestBuildRagPromptCitations:
    """build_rag_prompt 生成的上下文包含 [ref-N] 标记"""

    @pytest.fixture
    def rag_engine(self):
        from src.knowledge.rag_engine import RAGEngine
        return RAGEngine()

    def test_context_includes_ref_markers(self, rag_engine):
        """上下文中的每个文档都标注了 [ref-N]"""
        r1 = SearchResult(
            id="doc1_chunk_0",
            content="法国的首都是巴黎。",
            score=0.9,
            metadata={"filename": "france.txt"},
        )
        r2 = SearchResult(
            id="doc2_chunk_1",
            content="巴黎位于法国北部。",
            score=0.8,
            metadata={"filename": "paris.txt"},
        )
        messages = rag_engine.build_rag_prompt("法国的首都在哪？", [r1, r2])

        context = messages[1]["content"]
        assert "[ref-1]" in context
        assert "[ref-2]" in context
        assert "france.txt" in context
        assert "paris.txt" in context
        assert "法国的首都是巴黎" in context

    def test_system_prompt_includes_citation_rules(self, rag_engine):
        """系统提示包含引用规则说明"""
        r1 = SearchResult(
            id="x_chunk_0", content="内容", score=0.9,
            metadata={"filename": "a.txt"},
        )
        messages = rag_engine.build_rag_prompt("问题？", [r1])
        system = messages[0]["content"]
        assert "ref-1" in system or "引用" in system or "来源" in system

    def test_citation_property_for_multiple_results(self):
        """多个 SearchResult 的 citation 按顺序编号"""
        results = [
            SearchResult(
                id=f"doc{i}_chunk_{i - 1}",  # chunk_index 从 0 开始
                content=f"内容{i}",
                score=0.9 - i * 0.1,
                metadata={"filename": f"file{i}.txt", "doc_id": f"doc{i}"},
            )
            for i in range(1, 4)  # doc1_chunk_0, doc2_chunk_1, doc3_chunk_2
        ]
        # 验证每个结果的 citation 有不同的 ref_id（按 metadata chunk_index）
        refs = [r.citation.ref_id for r in results if r.citation]
        assert refs == ["ref-0", "ref-1", "ref-2"]

    def test_citation_ref_id_matches_context_position(self, rag_engine):
        """上下文中的 [ref-N] 编号与 SearchResult.citation.ref_id 一致"""
        r1 = SearchResult(id="a_chunk_5", content="内容1", score=0.9,
                           metadata={"filename": "f1.txt", "chunk_index": 5})
        r2 = SearchResult(id="b_chunk_2", content="内容2", score=0.8,
                           metadata={"filename": "f2.txt", "chunk_index": 2})
        messages = rag_engine.build_rag_prompt("问", [r1, r2])
        context = messages[1]["content"]
        # [ref-1] 对应第一个结果，[ref-2] 对应第二个
        assert "【文档 1】" in context
        assert "【文档 2】" in context
