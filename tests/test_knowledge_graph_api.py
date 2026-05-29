from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from src.knowledge.knowledge_graph import NetworkXKnowledgeGraph, Triple
from src.web.api import app


@pytest.fixture
def kg_api_client(monkeypatch, tmp_path):
    import storage.persistence as storage_mod
    from src.auth import _get_auth_from_request
    from src.web import api as api_mod

    kg = NetworkXKnowledgeGraph(persist_path=str(tmp_path / "kg-api.gml"), enabled=True)
    kg.clear()
    original_store = storage_mod._store
    store = storage_mod.init_store(tmp_path / "memox.db")

    class FakeRagEngine:
        _knowledge_graph = kg

    fake_auth = MagicMock()
    fake_auth.validate_token = MagicMock(return_value={
        "username": "admin",
        "role": "admin",
        "display_name": "Admin",
    })
    original_overrides = dict(app.dependency_overrides)
    monkeypatch.setattr(app.state, "_auth_manager", fake_auth, raising=False)
    monkeypatch.setattr(api_mod, "_rag_engine", FakeRagEngine())
    app.dependency_overrides[_get_auth_from_request] = lambda request: fake_auth

    try:
        yield TestClient(app), kg
    finally:
        store.close()
        storage_mod._store = original_store
        app.dependency_overrides.clear()
        app.dependency_overrides.update(original_overrides)


def test_merge_entities_endpoint(kg_api_client):
    client, kg = kg_api_client
    kg.add_triple(Triple("Memo X", "支持", "知识图谱", "doc1_chunk_0", 0.8))

    response = client.post(
        "/api/knowledge/graph/entities/merge",
        json={"source": "Memo X", "target": "MemoX"},
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert kg.export_triples()[0]["subject"] == "MemoX"


def test_split_entity_endpoint(kg_api_client):
    client, kg = kg_api_client
    kg.add_triple(Triple("Apple", "发布", "iPhone", "doc1_chunk_0", 0.9))
    kg.add_triple(Triple("Apple", "是", "水果", "doc2_chunk_0", 0.8))

    response = client.post(
        "/api/knowledge/graph/entities/split",
        json={
            "source": "Apple",
            "new_entity": "Apple Inc",
            "triples": [
                {
                    "subject": "Apple",
                    "predicate": "发布",
                    "object": "iPhone",
                    "source_chunk_id": "doc1_chunk_0",
                    "confidence": 0.9,
                }
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["moved_edges"] == 1
    assert {
        (item["subject"], item["predicate"], item["object"])
        for item in kg.export_triples()
    } == {
        ("Apple Inc", "发布", "iPhone"),
        ("Apple", "是", "水果"),
    }


def test_quality_endpoint_returns_review_candidates(kg_api_client):
    client, kg = kg_api_client
    kg.add_triple(Triple("Memo X", "支持", "知识图谱", "doc1_chunk_0", 0.9))
    kg.add_triple(Triple("MemoX", "用于", "长期记忆", "doc2_chunk_0", 0.85))
    kg.add_triple(Triple("噪声实体", "关联", "偶发对象", "doc3_chunk_0", 0.25))

    response = client.get("/api/knowledge/graph/quality", params={"confidence_threshold": 0.6})

    assert response.status_code == 200
    data = response.json()
    assert data["summary"]["total_candidates"] >= 2
    assert {item["type"] for item in data["candidates"]} >= {
        "duplicate_entity",
        "low_confidence_relation",
    }
    assert data["summary"]["quality_metrics"]["alerts"]
    assert data["summary"]["quality_gate"]["passed"] is False
    assert data["summary"]["quality_metrics"]["quality_gate"]["status"] == "warning"
    from storage import get_store

    store = get_store()
    latest_alert = store.get_latest_ops_event("knowledge_graph_quality_alert")
    assert latest_alert["status"] in {"warning", "error"}
    assert latest_alert["details"]["alerts"]
    before_count = store.count_ops_events("knowledge_graph_quality_alert")
    repeated = client.get("/api/knowledge/graph/quality", params={"confidence_threshold": 0.6})
    assert repeated.status_code == 200
    assert store.count_ops_events("knowledge_graph_quality_alert") == before_count
    history = client.get("/api/knowledge/graph/quality/history", params={"limit": 5})
    assert history.status_code == 200
    snapshots = history.json()["snapshots"]
    assert snapshots
    assert snapshots[-1]["health_score"] == data["summary"]["quality_metrics"]["health_score"]
    assert snapshots[-1]["open_candidate_count"] == data["summary"]["quality_metrics"]["open_candidate_count"]
    assert snapshots[-1]["metrics"]["alerts"]


def test_quality_snapshot_records_import_trigger_and_health_drop(kg_api_client):
    _, kg = kg_api_client
    from src.web.routers.documents import _build_knowledge_graph_quality_payload
    from storage import get_store

    store = get_store()
    store.save_knowledge_graph_quality_snapshot({
        "health_score": 88,
        "risk_level": "low",
        "relation_count": 3,
        "entity_count": 4,
        "source_doc_count": 1,
        "source_chunk_count": 3,
    })
    kg.add_triple(Triple("Memo X", "支持", "知识图谱", "doc1_chunk_0", 0.9))
    kg.add_triple(Triple("MemoX", "用于", "长期记忆", "doc2_chunk_0", 0.85))
    kg.add_triple(Triple("噪声实体", "关联", "偶发对象", "doc_bad_chunk_0", 0.25))

    payload = _build_knowledge_graph_quality_payload(
        confidence_threshold=0.6,
        limit=1,
        status="all",
        trigger={
            "action": "document_upload",
            "document_action": "indexed",
            "doc_id": "doc_bad",
            "filename": "bad.md",
            "chunk_count": 1,
        },
    )

    trigger = payload["summary"]["trigger"]
    assert trigger["doc_id"] == "doc_bad"
    assert trigger["filename"] == "bad.md"
    assert trigger["previous_health_score"] == 88
    assert trigger["health_drop"] > 0
    latest_snapshot = store.list_knowledge_graph_quality_snapshots(limit=1)[-1]
    assert latest_snapshot["metrics"]["trigger"]["filename"] == "bad.md"
    latest_alert = store.get_latest_ops_event("knowledge_graph_quality_alert")
    assert latest_alert["details"]["trigger"]["doc_id"] == "doc_bad"
    assert latest_alert["details"]["health_drop"] == trigger["health_drop"]
    governance_task = store.get_latest_ops_event("knowledge_graph_governance_task")
    assert governance_task["status"] in {"warning", "error"}
    assert governance_task["action"] == "governance_task_opened"
    assert governance_task["details"]["trigger"]["filename"] == "bad.md"
    assert governance_task["details"]["suggested_actions"]


def test_graph_mutation_rechecks_quality_and_resolves_governance_task(kg_api_client):
    client, kg = kg_api_client
    from storage import get_store

    store = get_store()
    kg.add_triple(Triple("噪声实体", "关联", "偶发对象", "doc3_chunk_0", 0.25))
    response = client.get("/api/knowledge/graph/quality", params={"confidence_threshold": 0.6})
    assert response.status_code == 200
    open_task = store.get_latest_ops_event("knowledge_graph_governance_task")
    assert open_task["status"] in {"warning", "error"}

    deleted = client.post(
        "/api/knowledge/graph/triples/delete",
        json={
            "subject": "噪声实体",
            "predicate": "关联",
            "object": "偶发对象",
            "source_chunk_id": "doc3_chunk_0",
            "confidence": 0.25,
        },
    )

    assert deleted.status_code == 200
    assert deleted.json()["governance_task_event_id"]
    latest_task = store.get_latest_ops_event("knowledge_graph_governance_task")
    assert latest_task["status"] == "ok"
    assert latest_task["action"] == "governance_task_resolved"
    assert latest_task["details"]["previous_task_key"] == open_task["details"]["task_key"]


def test_quality_decision_endpoint_hides_decided_candidate(kg_api_client):
    client, kg = kg_api_client
    kg.add_triple(Triple("噪声实体", "关联", "偶发对象", "doc3_chunk_0", 0.25))
    response = client.get("/api/knowledge/graph/quality", params={"confidence_threshold": 0.6})
    assert response.status_code == 200
    candidate = response.json()["candidates"][0]
    candidate_id = candidate["id"]
    fingerprint = candidate["fingerprint"]

    decision = client.post(
        "/api/knowledge/graph/quality/decisions",
        json={
            "candidate_id": candidate_id,
            "status": "ignored",
            "note": "noise",
            "details": {"candidate_fingerprint": fingerprint},
        },
    )
    assert decision.status_code == 200
    assert decision.json()["decision"]["status"] == "ignored"

    open_response = client.get("/api/knowledge/graph/quality", params={"confidence_threshold": 0.6})
    all_response = client.get("/api/knowledge/graph/quality", params={"confidence_threshold": 0.6, "status": "all"})

    assert candidate_id not in {item["id"] for item in open_response.json()["candidates"]}
    assert candidate_id in {item["id"] for item in all_response.json()["candidates"]}

    kg.add_triple(Triple("噪声实体", "关联", "偶发对象", "doc3_chunk_0", 0.35))
    reactivated = client.get("/api/knowledge/graph/quality", params={"confidence_threshold": 0.6})
    assert reactivated.status_code == 200
    reactivated_candidate = next(
        item for item in reactivated.json()["candidates"] if item["id"] == candidate_id
    )
    assert reactivated_candidate["fingerprint"] != fingerprint
    assert reactivated_candidate["stale_decision"]["status"] == "ignored"
    assert reactivated.json()["summary"]["stale_decision_count"] >= 1


def test_quality_decision_batch_endpoint_updates_multiple_candidates(kg_api_client):
    client, kg = kg_api_client
    kg.add_triple(Triple("Memo X", "支持", "知识图谱", "doc1_chunk_0", 0.9))
    kg.add_triple(Triple("MemoX", "用于", "长期记忆", "doc2_chunk_0", 0.85))
    kg.add_triple(Triple("噪声实体", "关联", "偶发对象", "doc3_chunk_0", 0.25))

    response = client.get("/api/knowledge/graph/quality", params={"confidence_threshold": 0.6})
    assert response.status_code == 200
    candidates = response.json()["candidates"][:2]
    assert len(candidates) == 2

    batch_response = client.post(
        "/api/knowledge/graph/quality/decisions/batch",
        json={
            "decisions": [
                {
                    "candidate_id": candidate["id"],
                    "status": "snoozed",
                    "details": {"candidate_fingerprint": candidate["fingerprint"]},
                }
                for candidate in candidates
            ]
        },
    )

    assert batch_response.status_code == 200
    assert batch_response.json()["updated"] == 2
    open_response = client.get("/api/knowledge/graph/quality", params={"confidence_threshold": 0.6})
    open_ids = {item["id"] for item in open_response.json()["candidates"]}
    assert not {candidate["id"] for candidate in candidates} & open_ids
    metrics = open_response.json()["summary"]["quality_metrics"]
    assert metrics["open_candidate_count"] == open_response.json()["summary"]["total_candidates"] - 2
    assert metrics["decided_candidate_count"] == 2
    assert 0 <= metrics["open_review_backlog_ratio"] <= metrics["review_backlog_ratio"]
    snoozed_response = client.get(
        "/api/knowledge/graph/quality",
        params={"confidence_threshold": 0.6, "status": "snoozed"},
    )
    snoozed_ids = {item["id"] for item in snoozed_response.json()["candidates"]}
    assert {candidate["id"] for candidate in candidates} <= snoozed_ids


def test_update_triple_endpoint(kg_api_client):
    client, kg = kg_api_client
    kg.add_triple(Triple("A", "旧关系", "B", "doc1_chunk_0", 0.4))

    response = client.put(
        "/api/knowledge/graph/triples",
        json={
            "old": {
                "subject": "A",
                "predicate": "旧关系",
                "object": "B",
                "source_chunk_id": "doc1_chunk_0",
            },
            "new": {
                "subject": "A",
                "predicate": "新关系",
                "object": "C",
                "source_chunk_id": "doc1_chunk_0",
                "confidence": 0.95,
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert kg.export_triples() == [{
        "subject": "A",
        "predicate": "新关系",
        "object": "C",
        "source_chunk_id": "doc1_chunk_0",
        "confidence": 0.95,
    }]


def test_delete_triple_endpoint(kg_api_client):
    client, kg = kg_api_client
    kg.add_triple(Triple("A", "关联", "B", "doc1_chunk_0", 0.8))

    response = client.post(
        "/api/knowledge/graph/triples/delete",
        json={
            "subject": "A",
            "predicate": "关联",
            "object": "B",
            "source_chunk_id": "doc1_chunk_0",
        },
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert kg.export_triples() == []
