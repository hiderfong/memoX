import os
import sys
from dataclasses import dataclass

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from knowledge.group_store import GroupStore
from storage.persistence import PersistenceStore
from web.routers import projects as projects_router


@dataclass
class FakeDoc:
    id: str
    filename: str
    type: str
    chunk_count: int
    created_at: str
    group_id: str


class FakeRagEngine:
    def __init__(self, docs: list[FakeDoc]):
        self._docs = docs

    def list_documents(self):
        return self._docs


def _client(monkeypatch, tmp_path, docs: list[FakeDoc]):
    import web.api as api_mod

    store = PersistenceStore(tmp_path / "projects.db")
    group_store = GroupStore(str(tmp_path / "groups.json"))
    product = group_store.create_group("产品资料", "#1677ff")
    research = group_store.create_group("研究项目", "#52c41a")
    monkeypatch.setattr(projects_router, "_gs", lambda: store)
    monkeypatch.setattr(api_mod, "_group_store", group_store)
    monkeypatch.setattr(api_mod, "_rag_engine", FakeRagEngine(docs))
    app = FastAPI()
    app.include_router(projects_router.router)
    return TestClient(app), store, product, research


def test_projects_summary_groups_docs_and_tasks(monkeypatch, tmp_path):
    docs = [
        FakeDoc("doc_a", "产品.md", "markdown", 3, "2026-06-01T10:00:00", "placeholder"),
        FakeDoc("doc_b", "研究.md", "markdown", 5, "2026-06-02T10:00:00", "placeholder"),
    ]
    client, store, product, research = _client(monkeypatch, tmp_path, docs)
    docs[0].group_id = product.id
    docs[1].group_id = research.id

    store.save_task({"task_id": "task_product", "description": "整理产品资料", "status": "running", "final_score": 0})
    store.save_task_job_request("task_product", "整理产品资料", active_group_ids=[product.id])
    store.save_task({"task_id": "task_global", "description": "全局复盘", "status": "completed", "final_score": 0.9})
    store.save_task_job_request("task_global", "全局复盘", active_group_ids=None)
    store.create_scheduled_task("sched_product", "定期更新产品资料", "0 9 * * *", active_group_ids=[product.id], next_run_at="2026-06-03T09:00:00")

    response = client.get("/api/projects")

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["project_count"] == 3
    product_project = next(item for item in payload["projects"] if item["id"] == product.id)
    research_project = next(item for item in payload["projects"] if item["id"] == research.id)
    assert product_project["metrics"]["doc_count"] == 1
    assert product_project["metrics"]["chunk_count"] == 3
    assert product_project["metrics"]["task_count"] == 2
    assert product_project["metrics"]["active_task_count"] == 1
    assert product_project["metrics"]["scheduled_task_count"] == 1
    assert product_project["health"] == "active"
    assert product_project["recent_tasks"][0]["task_id"] in {"task_product", "task_global"}
    assert research_project["metrics"]["doc_count"] == 1
    assert research_project["metrics"]["task_count"] == 1
    assert research_project["metrics"]["average_score"] == 0.9
    store.close()


def test_get_project_returns_404_for_unknown(monkeypatch, tmp_path):
    client, store, _product, _research = _client(monkeypatch, tmp_path, [])

    response = client.get("/api/projects/missing")

    assert response.status_code == 404
    store.close()
