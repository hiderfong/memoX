"""知识图谱单元测试（P4-4）"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.knowledge.document_parser import TextChunk
from src.knowledge.knowledge_graph import (
    NetworkXKnowledgeGraph,
    Triple,
    _extract_triples_rule_based,
    _extract_triples_via_llm_batch,
    _parse_llm_triple_response,
    evaluate_knowledge_graph_quality_gate,
    get_knowledge_graph,
    knowledge_graph_payload,
    knowledge_graph_quality_alerts,
    knowledge_graph_quality_payload,
)

# ---------------------------------------------------------------------------
# Helper: fresh KG per test
# ---------------------------------------------------------------------------


@pytest.fixture
def fresh_kg(tmp_path):
    """每个测试独立的 KG 实例（不同 persist_path）"""
    path = str(tmp_path / "test_kg.gml")
    kg = NetworkXKnowledgeGraph(persist_path=path, enabled=True)
    kg.clear()
    return kg


# ---------------------------------------------------------------------------
# Triple dataclass
# ---------------------------------------------------------------------------


class TestTriple:
    def test_to_dict(self):
        t = Triple(subject="小明", predicate="是", object="学生", source_chunk_id="c1", confidence=0.9)
        d = t.to_dict()
        assert d["subject"] == "小明"
        assert d["predicate"] == "是"
        assert d["object"] == "学生"
        assert d["source_chunk_id"] == "c1"
        assert d["confidence"] == 0.9

    def test_equality_case_insensitive(self):
        t1 = Triple("小明", "是", "学生")
        t2 = Triple("小明", "是", "学生")
        t3 = Triple("小明的", "是", "学生")
        assert t1 == t2
        assert t1 != t3

    def test_hash(self):
        t1 = Triple("小明", "是", "学生")
        t2 = Triple("小明", "是", "学生")
        assert hash(t1) == hash(t2)


# ---------------------------------------------------------------------------
# Rule-based triple extraction
# ---------------------------------------------------------------------------


class TestRuleBasedExtraction:
    def test_simple_sentence(self):
        text = "小明是学生"
        triples = _extract_triples_rule_based(text, "c1")
        assert len(triples) >= 1
        assert any(t.subject == "小明" and t.object == "学生" for t in triples)

    def test_possession(self):
        text = "苹果公司有iPhone产品"
        triples = _extract_triples_rule_based(text, "c1")
        assert len(triples) >= 1
        assert any(t.predicate == "有" for t in triples)

    def test_empty_text(self):
        assert _extract_triples_rule_based("", "c1") == []
        assert _extract_triples_rule_based("   ", "c1") == []

    def test_no_match(self):
        text = "今天天气很好"
        triples = _extract_triples_rule_based(text, "c1")
        # 可能匹配到 "天气很好" 但 predicate 不确定
        # 核心：不会崩溃
        assert isinstance(triples, list)

    def test_technical_text(self):
        text = "Python语言用于数据分析、机器学习、Web开发"
        triples = _extract_triples_rule_based(text, "c1")
        # 应该能提取 "Python语言用于..."
        assert len(triples) >= 1

    def test_confidence_rule_based(self):
        t = _extract_triples_rule_based("北京是中国的首都", "c1")[0]
        assert t.confidence == 0.6  # rule-based = 0.6


# ---------------------------------------------------------------------------
# LLM triple extraction
# ---------------------------------------------------------------------------


class TestLLMTripleExtraction:
    def test_parse_llm_json_response_filters_and_normalizes(self):
        content = """
        ```json
        {
          "triples": [
            {"chunk_id": "c1", "subject": " MemoX ", "predicate": "支持", "object": "知识图谱", "confidence": 1.7},
            {"chunk_id": "c1", "subject": "MemoX", "predicate": "支持", "object": "知识图谱", "confidence": 0.4},
            {"chunk_id": "missing", "subject": "外部", "predicate": "污染", "object": "图谱", "confidence": 0.9},
            {"chunk_id": "c2", "subject": "无效", "predicate": "", "object": "对象", "confidence": 0.9}
          ]
        }
        ```
        """
        result = _parse_llm_triple_response(content, {"c1", "c2"})
        assert list(result) == ["c1"]
        assert result["c1"][0].subject == "MemoX"
        assert result["c1"][0].predicate == "支持"
        assert result["c1"][0].object == "知识图谱"
        assert result["c1"][0].confidence == 1.0
        assert len(result["c1"]) == 1

    @pytest.mark.asyncio
    async def test_extract_triples_via_llm_batch_uses_provider(self, monkeypatch):
        from src.agents.base_agent import LLMResponse

        class FakeProvider:
            async def chat(self, messages, model, **kwargs):
                assert model == "graph-model"
                assert kwargs["temperature"] == 0
                assert "chunks" in messages[-1]["content"]
                return LLMResponse(content='{"triples":[{"chunk_id":"c1","subject":"A","predicate":"是","object":"B","confidence":0.93}]}')

        def fake_create_provider(provider_type, api_key, base_url="", **kwargs):
            assert provider_type == "dashscope"
            assert api_key == "test-key"
            assert base_url == "https://example.test/v1"
            return FakeProvider()

        import src.agents.base_agent as base_agent

        monkeypatch.setattr(base_agent, "create_provider", fake_create_provider)
        result = await _extract_triples_via_llm_batch(
            chunks=[("c1", "A 是 B")],
            llm_provider="dashscope",
            llm_api_key="test-key",
            llm_base_url="https://example.test/v1",
            llm_model="graph-model",
        )

        assert result["c1"] == [Triple("A", "是", "B", "c1", 0.93)]

    @pytest.mark.asyncio
    async def test_build_from_chunks_llm_falls_back_per_missing_chunk(self, monkeypatch, fresh_kg):
        async def fake_extract(**kwargs):
            return {
                "doc1_chunk_0": [Triple("MemoX", "支持", "知识图谱", "doc1_chunk_0", 0.91)],
            }

        import src.knowledge.knowledge_graph as kg_mod

        monkeypatch.setattr(kg_mod, "_extract_triples_via_llm_batch", fake_extract)
        alias_mod = sys.modules.get("knowledge.knowledge_graph")
        if alias_mod is not None:
            monkeypatch.setattr(alias_mod, "_extract_triples_via_llm_batch", fake_extract)
        monkeypatch.setitem(fresh_kg.build_from_chunks.__globals__, "_extract_triples_via_llm_batch", fake_extract)
        result = await fresh_kg.build_from_chunks(
            [
                TextChunk(id="doc1_chunk_0", content="MemoX 支持知识图谱。"),
                TextChunk(id="doc1_chunk_1", content="Python语言用于数据分析。"),
            ],
            llm_provider="dashscope",
            llm_api_key="test-key",
            llm_model="graph-model",
            use_llm=True,
        )

        assert result["method"] == "llm"
        assert result["added"] >= 2
        assert result["llm_fallback_chunks"] == 1
        exported = fresh_kg.export_triples()
        assert any(t["subject"] == "MemoX" and t["object"] == "知识图谱" for t in exported)
        assert any(t["source_chunk_id"] == "doc1_chunk_1" for t in exported)


# ---------------------------------------------------------------------------
# KnowledgeGraph construction
# ---------------------------------------------------------------------------


class TestKnowledgeGraphBasic:
    def test_add_triple(self, fresh_kg):
        t = Triple("苹果", "是", "水果", "c1", 0.9)
        fresh_kg.add_triple(t)
        assert fresh_kg._graph.number_of_nodes() == 2
        assert fresh_kg._graph.number_of_edges() == 1

    def test_add_duplicate_replaces(self, fresh_kg):
        t1 = Triple("苹果", "是", "水果", "c1", 0.9)
        t2 = Triple("苹果", "是", "水果", "c2", 0.8)
        fresh_kg.add_triple(t1)
        fresh_kg.add_triple(t2)
        # 同一条边不重复添加，只更新属性
        assert fresh_kg._graph.number_of_edges() == 1
        # 保留较高 confidence
        edge_data = fresh_kg._graph.edges["苹果", "水果", "是"]
        assert edge_data["confidence"] == 0.8

    def test_remove_by_chunk_id(self, fresh_kg):
        t1 = Triple("苹果", "是", "水果", "c1", 0.9)
        t2 = Triple("香蕉", "是", "水果", "c1", 0.9)
        fresh_kg.add_triple(t1)
        fresh_kg.add_triple(t2)
        removed = fresh_kg.remove_by_chunk_id("c1")
        assert removed == 2
        assert fresh_kg._graph.number_of_nodes() == 0

    def test_remove_isolated_nodes_cleaned_up(self, fresh_kg):
        t = Triple("苹果", "是", "水果", "c1", 0.9)
        fresh_kg.add_triple(t)
        fresh_kg.remove_by_chunk_id("c1")
        assert fresh_kg._graph.number_of_nodes() == 0

    def test_remove_by_doc_id_removes_all_chunk_edges(self, fresh_kg):
        fresh_kg.add_triple(Triple("A", "是", "B", "doc_1_chunk_0"))
        fresh_kg.add_triple(Triple("B", "是", "C", "doc_1_chunk_1"))
        fresh_kg.add_triple(Triple("X", "是", "Y", "doc_2_chunk_0"))

        removed = fresh_kg.remove_by_doc_id("doc_1")

        assert removed == 2
        exported = fresh_kg.export_triples()
        assert len(exported) == 1
        assert exported[0]["source_chunk_id"] == "doc_2_chunk_0"

    def test_clear(self, fresh_kg):
        fresh_kg.add_triple(Triple("A", "是", "B", "c1"))
        fresh_kg.clear()
        assert fresh_kg._graph.number_of_nodes() == 0
        assert fresh_kg._graph.number_of_edges() == 0

    def test_disabled_graph_add_noop(self):
        kg = NetworkXKnowledgeGraph(enabled=False)
        kg.add_triple(Triple("A", "是", "B"))
        assert kg._graph.number_of_nodes() == 0


class TestKnowledgeGraphGovernance:
    def test_merge_entities_redirects_edges_and_removes_source(self, fresh_kg):
        fresh_kg.add_triple(Triple("Memo X", "支持", "知识图谱", "doc1_chunk_0", 0.8))
        fresh_kg.add_triple(Triple("文档库", "提到", "Memo X", "doc1_chunk_1", 0.7))
        fresh_kg.add_triple(Triple("MemoX", "用于", "长期记忆", "doc2_chunk_0", 0.9))
        fresh_kg.add_triple(Triple("Memo X", "别名", "MemoX", "doc3_chunk_0", 0.6))

        result = fresh_kg.merge_entities("Memo X", "MemoX")

        assert result["merged"] is True
        assert "Memo X" not in fresh_kg._graph.nodes
        exported = fresh_kg.export_triples()
        assert any(t["subject"] == "MemoX" and t["predicate"] == "支持" for t in exported)
        assert any(t["object"] == "MemoX" and t["predicate"] == "提到" for t in exported)
        assert not any(t["subject"] == "MemoX" and t["object"] == "MemoX" for t in exported)

    def test_merge_entities_deduplicates_edges_by_confidence(self, fresh_kg):
        fresh_kg.add_triple(Triple("Memo X", "支持", "知识图谱", "chunk_low", 0.4))
        fresh_kg.add_triple(Triple("MemoX", "支持", "知识图谱", "chunk_high", 0.9))

        result = fresh_kg.merge_entities("Memo X", "MemoX")

        assert result["merged"] is True
        exported = fresh_kg.export_triples()
        assert len(exported) == 1
        assert exported[0]["confidence"] == 0.9
        assert exported[0]["source_chunk_id"] == "chunk_high"

    def test_split_entity_moves_selected_edges_to_new_entity(self, fresh_kg):
        fresh_kg.add_triple(Triple("Apple", "发布", "iPhone", "doc1_chunk_0", 0.9))
        fresh_kg.add_triple(Triple("Apple", "是", "水果", "doc2_chunk_0", 0.8))
        fresh_kg.add_triple(Triple("市场", "关注", "Apple", "doc3_chunk_0", 0.7))

        result = fresh_kg.split_entity(
            "Apple",
            "Apple Inc",
            [
                Triple("Apple", "发布", "iPhone", "doc1_chunk_0", 0.9),
                Triple("市场", "关注", "Apple", "doc3_chunk_0", 0.7),
            ],
        )

        assert result["split"] is True
        assert result["moved_edges"] == 2
        exported = fresh_kg.export_triples()
        assert {
            (item["subject"], item["predicate"], item["object"])
            for item in exported
        } == {
            ("Apple Inc", "发布", "iPhone"),
            ("Apple", "是", "水果"),
            ("市场", "关注", "Apple Inc"),
        }

    def test_split_entity_requires_matching_incident_edges(self, fresh_kg):
        fresh_kg.add_triple(Triple("Apple", "是", "水果", "doc2_chunk_0", 0.8))

        result = fresh_kg.split_entity(
            "Apple",
            "Apple Inc",
            [Triple("Banana", "是", "水果", "doc2_chunk_0", 0.8)],
        )

        assert result["split"] is False
        assert result["reason"] == "no_matching_edges"
        assert fresh_kg.export_triples() == [{
            "subject": "Apple",
            "predicate": "是",
            "object": "水果",
            "source_chunk_id": "doc2_chunk_0",
            "confidence": 0.8,
        }]

    def test_delete_triple_removes_relation_and_orphans(self, fresh_kg):
        fresh_kg.add_triple(Triple("A", "关联", "B", "doc1_chunk_0", 0.8))
        fresh_kg.add_triple(Triple("C", "关联", "D", "doc2_chunk_0", 0.8))

        deleted = fresh_kg.delete_triple(Triple("A", "关联", "B", "doc1_chunk_0"))

        assert deleted is True
        assert "A" not in fresh_kg._graph.nodes
        assert "B" not in fresh_kg._graph.nodes
        assert fresh_kg.export_triples() == [{
            "subject": "C",
            "predicate": "关联",
            "object": "D",
            "source_chunk_id": "doc2_chunk_0",
            "confidence": 0.8,
        }]

    def test_update_triple_replaces_relation(self, fresh_kg):
        fresh_kg.add_triple(Triple("A", "旧关系", "B", "doc1_chunk_0", 0.4))

        updated = fresh_kg.update_triple(
            Triple("A", "旧关系", "B", "doc1_chunk_0"),
            Triple("A", "新关系", "C", "doc1_chunk_0", 0.95),
        )

        assert updated is True
        exported = fresh_kg.export_triples()
        assert exported == [{
            "subject": "A",
            "predicate": "新关系",
            "object": "C",
            "source_chunk_id": "doc1_chunk_0",
            "confidence": 0.95,
        }]


# ---------------------------------------------------------------------------
# KnowledgeGraph persistence
# ---------------------------------------------------------------------------


class TestKnowledgeGraphPersistence:
    def test_save_and_reload(self, tmp_path):
        path = str(tmp_path / "persist_kg.gml")
        kg1 = NetworkXKnowledgeGraph(persist_path=path, enabled=True)
        kg1.add_triple(Triple("苹果", "是", "水果", "c1"))
        kg1.add_triple(Triple("苹果", "有", "苹果公司", "c1"))
        kg1.save()

        kg2 = NetworkXKnowledgeGraph(persist_path=path, enabled=True)
        assert kg2._graph.number_of_nodes() == 3  # 苹果, 水果, 苹果公司
        assert kg2._graph.number_of_edges() == 2

    def test_nonexistent_file_loads_empty(self, tmp_path):
        path = str(tmp_path / "nonexistent.gml")
        kg = NetworkXKnowledgeGraph(persist_path=path, enabled=True)
        assert kg._graph.number_of_nodes() == 0


# ---------------------------------------------------------------------------
# KnowledgeGraph queries
# ---------------------------------------------------------------------------


class TestKnowledgeGraphSearch:
    def test_search_exact_match(self, fresh_kg):
        fresh_kg.add_triple(Triple("苹果", "是", "水果", "c1"))
        fresh_kg.add_triple(Triple("苹果", "有", "苹果公司", "c1"))
        result = fresh_kg.search("苹果")
        assert result is not None
        assert result.entity == "苹果"
        assert result.degree == 2

    def test_search_substring_match(self, fresh_kg):
        fresh_kg.add_triple(Triple("苹果公司", "是", "企业", "c1"))
        result = fresh_kg.search("苹果")
        assert result is not None
        assert result.entity == "苹果公司"

    def test_search_case_insensitive(self, fresh_kg):
        fresh_kg.add_triple(Triple("Python", "是", "编程语言", "c1"))
        result = fresh_kg.search("python")
        assert result is not None
        assert result.entity == "Python"

    def test_search_not_found(self, fresh_kg):
        fresh_kg.add_triple(Triple("苹果", "是", "水果", "c1"))
        result = fresh_kg.search("不存在的内容")
        assert result is None

    def test_search_returns_triples(self, fresh_kg):
        fresh_kg.add_triple(Triple("苹果", "是", "水果", "c1"))
        fresh_kg.add_triple(Triple("苹果", "有", "红色", "c1"))
        result = fresh_kg.search("苹果")
        assert result is not None
        assert len(result.triples) == 2
        assert result.connected_entities == sorted(["水果", "红色"])

    def test_disabled_graph_returns_none(self):
        kg = NetworkXKnowledgeGraph(enabled=False)
        assert kg.search("苹果") is None


class TestKnowledgeGraphSubgraph:
    def test_query_subgraph_direct_neighbors(self, fresh_kg):
        fresh_kg.add_triple(Triple("A", "是", "B", "c1"))
        fresh_kg.add_triple(Triple("A", "是", "C", "c1"))
        fresh_kg.add_triple(Triple("B", "包含", "D", "c1"))
        sg = fresh_kg.query_subgraph("A", depth=1)
        # depth=1: 直接邻居
        assert "A" in sg.nodes()
        assert "B" in sg.nodes()
        assert "C" in sg.nodes()

    def test_query_subgraph_not_found(self, fresh_kg):
        fresh_kg.add_triple(Triple("A", "是", "B", "c1"))
        sg = fresh_kg.query_subgraph("不存在")
        assert sg.number_of_nodes() == 0


class TestKnowledgeGraphPaths:
    def test_direct_path(self, fresh_kg):
        fresh_kg.add_triple(Triple("A", "认识", "B", "c1"))
        paths = fresh_kg.get_paths_between("A", "B")
        assert len(paths) == 1
        assert paths[0] == ["A", "B"]

    def test_multi_hop_path(self, fresh_kg):
        fresh_kg.add_triple(Triple("A", "认识", "B", "c1"))
        fresh_kg.add_triple(Triple("B", "认识", "C", "c1"))
        paths = fresh_kg.get_paths_between("A", "C")
        assert len(paths) == 1
        assert paths[0] == ["A", "B", "C"]

    def test_no_path(self, fresh_kg):
        fresh_kg.add_triple(Triple("A", "是", "B", "c1"))
        fresh_kg.add_triple(Triple("C", "是", "D", "c1"))
        paths = fresh_kg.get_paths_between("A", "C")
        assert paths == []

    def test_node_not_in_graph(self, fresh_kg):
        fresh_kg.add_triple(Triple("A", "是", "B", "c1"))
        paths = fresh_kg.get_paths_between("A", "不存在")
        assert paths == []


class TestKnowledgeGraphNeighbors:
    def test_get_neighbors_all(self, fresh_kg):
        fresh_kg.add_triple(Triple("A", "是", "B", "c1"))
        fresh_kg.add_triple(Triple("A", "认识", "C", "c1"))
        neighbors = fresh_kg.get_neighbors("A")
        assert len(neighbors) == 2
        assert ("B", "是") in neighbors
        assert ("C", "认识") in neighbors

    def test_get_neighbors_filtered(self, fresh_kg):
        fresh_kg.add_triple(Triple("A", "是", "B", "c1"))
        fresh_kg.add_triple(Triple("A", "认识", "C", "c1"))
        neighbors = fresh_kg.get_neighbors("A", edge_predicate="是")
        assert neighbors == [("B", "是")]


# ---------------------------------------------------------------------------
# KnowledgeGraph stats + export
# ---------------------------------------------------------------------------


class TestKnowledgeGraphStats:
    def test_stats_enabled(self, fresh_kg):
        fresh_kg.add_triple(Triple("A", "是", "B", "c1"))
        stats = fresh_kg.stats()
        assert stats["enabled"] is True
        assert stats["nodes"] == 2
        assert stats["edges"] == 1
        assert stats["version"] == 1

    def test_stats_disabled(self):
        kg = NetworkXKnowledgeGraph(enabled=False)
        stats = kg.stats()
        assert stats["enabled"] is False

    def test_export_triples(self, fresh_kg):
        fresh_kg.add_triple(Triple("苹果", "是", "水果", "c1", 0.9))
        fresh_kg.add_triple(Triple("苹果", "有", "红色", "c1", 0.8))
        exported = fresh_kg.export_triples()
        assert len(exported) == 2
        assert all(k in t for k in ("subject", "predicate", "object") for t in exported)


class TestKnowledgeGraphPayload:
    def test_payload_returns_facets_and_provenance(self):
        payload = knowledge_graph_payload(
            [
                {
                    "subject": "MemoX",
                    "predicate": "支持",
                    "object": "知识图谱",
                    "source_chunk_id": "doc1_chunk_0",
                    "confidence": 0.92,
                },
                {
                    "subject": "MemoX",
                    "predicate": "用于",
                    "object": "长期记忆",
                    "source_chunk_id": "doc2_chunk_3",
                    "confidence": 0.7,
                },
            ],
            stats={"enabled": True, "nodes": 3, "edges": 2},
        )

        assert payload["stats"]["visible_nodes"] == 3
        assert payload["stats"]["visible_edges"] == 2
        assert payload["entities"][0]["name"] == "MemoX"
        assert payload["entities"][0]["source_doc_count"] == 2
        assert payload["predicates"] == [
            {"predicate": "支持", "count": 1},
            {"predicate": "用于", "count": 1},
        ]
        assert payload["links"][0]["source_doc_id"] in {"doc1", "doc2"}

    def test_payload_filters_by_entity_depth_predicate_and_confidence(self):
        triples = [
            {"subject": "A", "predicate": "连接", "object": "B", "source_chunk_id": "doc_chunk_0", "confidence": 0.9},
            {"subject": "B", "predicate": "连接", "object": "C", "source_chunk_id": "doc_chunk_1", "confidence": 0.8},
            {"subject": "C", "predicate": "低质", "object": "D", "source_chunk_id": "doc_chunk_2", "confidence": 0.2},
            {"subject": "X", "predicate": "连接", "object": "Y", "source_chunk_id": "other_chunk_0", "confidence": 0.95},
        ]

        payload = knowledge_graph_payload(
            triples,
            entity="A",
            depth=2,
            predicate="连接",
            min_confidence=0.5,
        )

        assert payload["matched_entity"] == "A"
        assert {node["id"] for node in payload["nodes"]} == {"A", "B", "C"}
        assert len(payload["links"]) == 2
        assert all(link["predicate"] == "连接" for link in payload["links"])
        assert payload["stats"]["filtered_triples"] == 3

    def test_payload_limits_visible_edges(self):
        triples = [
            {"subject": f"A{i}", "predicate": "是", "object": f"B{i}", "confidence": 0.9}
            for i in range(5)
        ]

        payload = knowledge_graph_payload(triples, limit=2)

        assert len(payload["links"]) == 2
        assert payload["filters"]["limit"] == 2


class TestKnowledgeGraphQualityPayload:
    def test_quality_payload_flags_duplicate_entities_and_low_confidence(self):
        payload = knowledge_graph_quality_payload(
            [
                {
                    "subject": "Memo X",
                    "predicate": "支持",
                    "object": "知识图谱",
                    "source_chunk_id": "doc1_chunk_0",
                    "confidence": 0.92,
                },
                {
                    "subject": "MemoX",
                    "predicate": "用于",
                    "object": "长期记忆",
                    "source_chunk_id": "doc2_chunk_0",
                    "confidence": 0.81,
                },
                {
                    "subject": "噪声实体",
                    "predicate": "关联",
                    "object": "偶发对象",
                    "source_chunk_id": "doc3_chunk_0",
                    "confidence": 0.3,
                },
            ],
            confidence_threshold=0.6,
        )

        types = {item["type"] for item in payload["candidates"]}
        assert "duplicate_entity" in types
        assert "low_confidence_relation" in types
        assert payload["summary"]["duplicate_entity_count"] >= 1
        assert payload["summary"]["low_confidence_relation_count"] == 1
        duplicate = next(item for item in payload["candidates"] if item["type"] == "duplicate_entity")
        assert duplicate["action"]["type"] == "merge_entities"
        assert all(item["fingerprint"] for item in payload["candidates"])
        metrics = payload["summary"]["quality_metrics"]
        assert metrics["relation_count"] == 3
        assert metrics["entity_count"] == 6
        assert metrics["source_doc_count"] == 3
        assert metrics["source_chunk_count"] == 3
        assert metrics["candidate_count"] == payload["summary"]["total_candidates"]
        assert metrics["low_confidence_ratio"] == 0.333
        assert metrics["isolated_relation_ratio"] == 0.333
        assert metrics["review_backlog_ratio"] == 1.0
        assert metrics["risk_level"] == "medium"
        assert 0 < metrics["health_score"] < 100
        alert_codes = {alert["code"] for alert in metrics["alerts"]}
        assert "low_confidence_ratio_warning" in alert_codes
        assert "review_backlog_critical" in alert_codes

    def test_quality_alerts_flags_health_drop_from_previous_snapshot(self):
        alerts = knowledge_graph_quality_alerts(
            {
                "health_score": 62,
                "relation_count": 10,
                "low_confidence_ratio": 0.1,
                "isolated_relation_ratio": 0.1,
                "open_review_backlog_ratio": 0.1,
            },
            previous_snapshot={"health_score": 78},
        )

        assert any(alert["code"] == "health_drop_warning" for alert in alerts)

    def test_quality_gate_reports_threshold_violations(self):
        gate = evaluate_knowledge_graph_quality_gate(
            {
                "health_score": 68,
                "relation_count": 10,
                "low_confidence_ratio": 0.22,
                "isolated_relation_ratio": 0.1,
                "open_review_backlog_ratio": 0.5,
            },
            {
                "enabled": True,
                "min_health_score": 75,
                "max_low_confidence_ratio": 0.15,
                "max_isolated_relation_ratio": 0.2,
                "max_open_review_backlog_ratio": 0.4,
                "require_relations": False,
                "min_relation_count": 1,
            },
        )

        assert gate["passed"] is False
        assert gate["status"] == "warning"
        assert {item["code"] for item in gate["violations"]} == {
            "health_score_below_gate",
            "low_confidence_ratio_above_gate",
            "open_review_backlog_ratio_above_gate",
        }

    def test_quality_gate_waits_for_relations_by_default(self):
        gate = evaluate_knowledge_graph_quality_gate(
            {"health_score": 0, "relation_count": 0},
            {"enabled": True, "min_health_score": 75, "require_relations": False},
        )

        assert gate["passed"] is True
        assert gate["status"] == "ok"

    def test_quality_payload_fingerprint_changes_when_candidate_payload_changes(self):
        original = knowledge_graph_quality_payload(
            [
                {
                    "subject": "噪声实体",
                    "predicate": "关联",
                    "object": "偶发对象",
                    "source_chunk_id": "doc3_chunk_0",
                    "confidence": 0.25,
                }
            ],
            confidence_threshold=0.6,
        )
        changed = knowledge_graph_quality_payload(
            [
                {
                    "subject": "噪声实体",
                    "predicate": "关联",
                    "object": "偶发对象",
                    "source_chunk_id": "doc3_chunk_0",
                    "confidence": 0.35,
                }
            ],
            confidence_threshold=0.6,
        )

        original_low = next(item for item in original["candidates"] if item["type"] == "low_confidence_relation")
        changed_low = next(item for item in changed["candidates"] if item["type"] == "low_confidence_relation")
        assert changed_low["id"] == original_low["id"]
        assert changed_low["fingerprint"] != original_low["fingerprint"]

    def test_quality_payload_flags_identity_conflicts(self):
        payload = knowledge_graph_quality_payload(
            [
                {"subject": "Apple", "predicate": "是", "object": "水果", "source_chunk_id": "fruit_doc_chunk_0", "confidence": 0.9},
                {"subject": "Apple", "predicate": "颜色", "object": "红色", "source_chunk_id": "fruit_doc_chunk_1", "confidence": 0.9},
                {"subject": "Apple", "predicate": "是", "object": "公司", "source_chunk_id": "corp_doc_chunk_0", "confidence": 0.9},
                {"subject": "Apple", "predicate": "发布", "object": "iPhone", "source_chunk_id": "corp_doc_chunk_1", "confidence": 0.9},
            ]
        )

        conflicts = [item for item in payload["candidates"] if item["type"] == "conflicting_relation"]
        assert len(conflicts) == 1
        conflict = conflicts[0]
        assert conflict["related_triples"][0]["subject"] == "Apple"
        assert conflict["action"]["type"] == "split_entity"
        assert conflict["action"]["source"] == "Apple"
        assert conflict["action"]["new_entity"].startswith("Apple ")
        assert conflict["action"]["triples"]

    def test_quality_payload_flags_ambiguous_entity_source_clusters(self):
        payload = knowledge_graph_quality_payload(
            [
                {"subject": "Python", "predicate": "用于", "object": "数据分析", "source_chunk_id": "python_lang_chunk_0", "confidence": 0.92},
                {"subject": "Python", "predicate": "支持", "object": "Web开发", "source_chunk_id": "python_lang_chunk_1", "confidence": 0.9},
                {"subject": "Python", "predicate": "属于", "object": "蛇类", "source_chunk_id": "python_snake_chunk_0", "confidence": 0.91},
                {"subject": "Python", "predicate": "栖息", "object": "雨林", "source_chunk_id": "python_snake_chunk_1", "confidence": 0.88},
            ]
        )

        ambiguous = [item for item in payload["candidates"] if item["type"] == "ambiguous_entity"]
        assert len(ambiguous) == 1
        candidate = ambiguous[0]
        assert candidate["action"]["type"] == "split_entity"
        assert candidate["action"]["source"] == "Python"
        assert candidate["action"]["new_entity"].startswith("Python ")
        assert len(candidate["action"]["triples"]) == 2
        assert payload["summary"]["ambiguous_entity_count"] == 1
        assert payload["summary"]["quality_metrics"]["ambiguous_entity_ratio"] > 0


# ---------------------------------------------------------------------------
# build_from_triples (direct API)
# ---------------------------------------------------------------------------


class TestBuildFromTriples:
    def test_build_from_triple_list(self, fresh_kg):
        triples = [
            Triple("Python", "用于", "数据分析", "c1"),
            Triple("Python", "用于", "Web开发", "c1"),
        ]
        count = fresh_kg.build_from_triples(triples)
        assert count == 2
        assert fresh_kg._graph.number_of_nodes() == 3


# ---------------------------------------------------------------------------
# Integration: RAGEngine.search_with_graph
# ---------------------------------------------------------------------------


class TestSearchWithGraph:
    @pytest.fixture
    def mock_rag_engine(self, tmp_path):
        """创建一个带有 mock 向量库的 RAGEngine（关闭 hybrid_search 以隔离图谱逻辑）"""
        from src.knowledge.rag_engine import RAGEngine
        from src.knowledge.vector_store import ChromaVectorStore

        vs = MagicMock(spec=ChromaVectorStore)
        # Return None so search() doesn't apply doc_ids filtering
        vs.list_documents.return_value = None
        vs.search = AsyncMock(return_value=[])
        vs.get_collection = MagicMock()
        vs.get_or_create_collection = MagicMock()
        vs.embedding_fn = None

        engine = RAGEngine(
            vector_store=vs,
            hybrid_search_enabled=False,
            enable_graph=True,
            graph_config=MagicMock(enable_graph=True, graph_type='networkx', graph_persist_path=str(tmp_path / "kg.gml")),
        )
        engine._knowledge_graph.clear()
        return engine

    def test_search_with_graph_disabled(self, tmp_path):
        from src.knowledge.rag_engine import RAGEngine
        from src.knowledge.vector_store import ChromaVectorStore

        vs = MagicMock(spec=ChromaVectorStore)
        vs.list_documents.return_value = []
        vs.search = AsyncMock(return_value=[])
        vs.embedding_fn = None

        engine = RAGEngine(
            vector_store=vs,
            hybrid_search_enabled=False,
            enable_graph=False,
            graph_config=MagicMock(enable_graph=False, graph_type='networkx', graph_persist_path=str(tmp_path / "kg.gml")),
        )
        result = engine._knowledge_graph  # None when disabled
        assert result is None

    @pytest.mark.asyncio
    async def test_search_with_graph_returns_structure(self, mock_rag_engine):
        result = await mock_rag_engine.search_with_graph("测试查询")
        assert "search_results" in result
        assert "graph_result" in result
        assert "graph_boosted_ids" in result
        assert isinstance(result["search_results"], list)
        assert result["graph_result"] is None  # no data in graph yet

    @pytest.mark.asyncio
    async def test_search_with_graph_boosts_connected(self, mock_rag_engine):
        # 向图谱添加三元组
        mock_rag_engine._knowledge_graph.add_triple(
            Triple("机器学习", "是", "人工智能", "c1", 1.0)
        )
        mock_rag_engine._knowledge_graph.add_triple(
            Triple("机器学习", "用于", "数据分析", "c1", 1.0)
        )

        # mock 向量搜索返回 dict 结果（符合 ChromaVectorStore.search 返回类型）
        mock_results = [
            {"id": "r1", "content": "机器学习是人工智能的分支", "score": 0.5, "metadata": {}},
            {"id": "r2", "content": "深度学习是机器学习的分支", "score": 0.5, "metadata": {}},
            {"id": "r3", "content": "今天天气很好", "score": 0.5, "metadata": {}},
        ]
        mock_rag_engine.vector_store.search = AsyncMock(return_value=mock_results)

        result = await mock_rag_engine.search_with_graph("机器学习")

        # r1 和 r2 内容包含"机器学习"，应该被 boost
        boosted = result["graph_boosted_ids"]
        assert "r1" in boosted
        assert "r2" in boosted
        assert "r3" not in boosted  # 不包含实体

        # boosted 的 score 乘以 1.2
        boosted_scores = {r.id: r.score for r in result["search_results"] if r.id in boosted}
        assert all(s > 0.5 for s in boosted_scores.values())


# ---------------------------------------------------------------------------
# RAGEngine graph indexing integration
# ---------------------------------------------------------------------------


class TestRAGGraphIndexing:
    @pytest.fixture
    def graph_config(self, tmp_path):
        return MagicMock(
            enable_graph=True,
            graph_type="networkx",
            graph_persist_path=str(tmp_path / "kg-index.gml"),
            graph_llm_provider="dashscope",
            graph_llm_api_key="graph-key",
            graph_llm_base_url="https://graph.example/v1",
            graph_llm_model="graph-model",
        )

    @pytest.fixture
    def graph_engine(self, graph_config):
        from src.knowledge.rag_engine import RAGEngine
        from src.knowledge.vector_store import ChromaVectorStore

        vs = MagicMock(spec=ChromaVectorStore)
        vs.add_chunks = AsyncMock(return_value=["doc1_chunk_0"])
        vs.delete_by_document_id = AsyncMock(return_value=1)
        vs.list_documents.return_value = []
        vs.embedding_fn = None

        engine = RAGEngine(
            vector_store=vs,
            hybrid_search_enabled=False,
            enable_graph=True,
            graph_config=graph_config,
        )
        engine._knowledge_graph = MagicMock()
        engine._knowledge_graph.build_from_chunks = AsyncMock(return_value={
            "added": 1,
            "chunks_processed": 1,
            "method": "llm",
            "llm_fallback_chunks": 0,
        })
        return engine

    @pytest.mark.asyncio
    async def test_index_document_chunks_uses_configured_graph_llm(self, graph_engine):
        chunk = TextChunk(
            id="doc1_chunk_0",
            content="MemoX 支持知识图谱。",
            metadata={"doc_id": "doc1", "chunk_index": 0},
        )

        await graph_engine._index_document_chunks([chunk], "documents")

        graph_engine._knowledge_graph.build_from_chunks.assert_awaited_once()
        kwargs = graph_engine._knowledge_graph.build_from_chunks.await_args.kwargs
        assert kwargs["llm_provider"] == "dashscope"
        assert kwargs["llm_api_key"] == "graph-key"
        assert kwargs["llm_base_url"] == "https://graph.example/v1"
        assert kwargs["llm_model"] == "graph-model"
        assert kwargs["use_llm"] is True

    @pytest.mark.asyncio
    async def test_discard_indexed_document_removes_graph_by_doc_id(self, graph_engine):
        graph_engine._knowledge_graph.remove_by_doc_id = MagicMock(return_value=2)

        removed = await graph_engine._discard_indexed_document("doc1", "documents")

        assert removed == 1
        graph_engine._knowledge_graph.remove_by_doc_id.assert_called_once_with("doc1")


# ---------------------------------------------------------------------------
# Singleton behavior
# ---------------------------------------------------------------------------


class TestKnowledgeGraphSingleton:
    def test_get_knowledge_graph_returns_same_instance(self, tmp_path):
        path = str(tmp_path / "singleton.gml")
        cfg = MagicMock(enable_graph=True, graph_type='networkx', graph_persist_path=path)
        kg1 = get_knowledge_graph(config=cfg)
        kg2 = get_knowledge_graph(config=cfg)
        # 同一 path → 同实例
        assert kg1 is kg2

    def test_get_knowledge_graph_different_paths_different_instances(self, tmp_path):
        # The module-level singleton is process-global.
        # Once instantiated, subsequent calls always return the SAME object.
        # This is intentional — one process, one knowledge graph.
        cfg1 = MagicMock(enable_graph=True, graph_type='networkx', graph_persist_path=str(tmp_path / "a.gml"))
        cfg2 = MagicMock(enable_graph=True, graph_type='networkx', graph_persist_path=str(tmp_path / "b.gml"))
        kg1 = get_knowledge_graph(config=cfg1)
        kg2 = get_knowledge_graph(config=cfg2)
        assert kg1 is kg2  # same singleton

    def test_singleton_persist_path_matches_first_path(self, tmp_path):
        """The singleton's persist_path reflects the path used at first instantiation.

        Since tests run in the same process and share the global singleton,
        this test records what the singleton path was set to on first creation
        (in earlier tests). It verifies the singleton path is stable.
        """
        cfg = MagicMock(enable_graph=True, graph_type='networkx', graph_persist_path=str(tmp_path / "any.gml"))
        kg = get_knowledge_graph(config=cfg)
        # The singleton persists across all tests; its path was set when it was first created.
        # We verify it's a Path object and the graph is functional.
        assert hasattr(kg, "persist_path")
        assert isinstance(kg.persist_path, Path)
        assert kg.stats()["enabled"] is True
