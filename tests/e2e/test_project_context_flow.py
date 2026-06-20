from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from agents.base_agent import LLMResponse
from agents.worker_pool import Task, TaskStatus
from auth import init_auth
from coordinator.iterative_orchestrator import IterationRecord, IterationResult
from knowledge import DocumentInfo, SearchResult
from knowledge.document_parser import Document, TextChunk
from knowledge.group_store import GroupStore
from scheduler.runner import ScheduledTaskRunner
from storage.persistence import PersistenceStore

pytestmark = pytest.mark.e2e


class ProjectContextRag:
    def __init__(self):
        self._documents: dict[str, DocumentInfo] = {}
        self.indexed_chunks: list[TextChunk] = []
        self.search_group_calls: list[list[str] | None] = []
        self.messages: list[tuple[str, str, str]] = []

    def list_documents(self):
        return list(self._documents.values())

    async def _index_document_chunks(self, chunks: list[TextChunk], collection_name: str) -> None:
        self.indexed_chunks.extend(chunks)

    def get_session(self, session_id: str):
        return {"id": session_id}

    def create_session(self):
        return {"id": "session_fake"}

    def add_message(self, session_id: str, role: str, content: str) -> None:
        self.messages.append((session_id, role, content))

    async def search_with_graph(self, query: str, group_ids: list[str] | None = None, top_k: int = 20) -> dict:
        self.search_group_calls.append(list(group_ids) if group_ids is not None else None)
        project_docs = [
            doc for doc in self._documents.values()
            if group_ids is None or doc.group_id in group_ids
        ]
        doc = project_docs[0] if project_docs else DocumentInfo(
            id="missing",
            filename="missing.md",
            type="markdown",
            chunk_count=0,
            group_id="ungrouped",
        )
        return {
            "search_results": [
                SearchResult(
                    id=f"{doc.id}_chunk_0",
                    content=f"{doc.filename} contains durable project context.",
                    score=0.92,
                    metadata={
                        "doc_id": doc.id,
                        "filename": doc.filename,
                        "chunk_index": 0,
                        "group_id": doc.group_id,
                    },
                )
            ],
            "graph_result": None,
            "graph_boosted_ids": [],
        }

    def build_evidence_payload(self, search_results: list[SearchResult], graph_result=None, graph_boosted_ids=None):
        return [
            {
                "ref_id": f"ref-{index}",
                "doc_id": result.metadata.get("doc_id", ""),
                "filename": result.metadata.get("filename", ""),
                "content": result.content,
                "content_preview": result.content[:100],
                "chunk_index": result.metadata.get("chunk_index", 0),
                "score": result.score,
                "group_id": result.metadata.get("group_id", ""),
                "evidence_quality": "basic",
                "evidence_reason": "project-scoped fake retrieval",
            }
            for index, result in enumerate(search_results, 1)
        ]

    def build_rag_prompt(self, message: str, search_results: list[SearchResult]) -> list[dict]:
        return [
            {"role": "system", "content": "Use project-scoped retrieval evidence."},
            {"role": "user", "content": message},
        ]

    def extract_citations_from_text(self, answer: str) -> list[str]:
        return ["ref-1"] if "ref-1" in answer else []

    def build_citation_payload(self, cited_ref_ids: list[str], search_results: list[SearchResult], evidence_payload: list[dict]):
        cited = set(cited_ref_ids)
        return [item for item in evidence_payload if item["ref_id"] in cited]


class ProjectContextProvider:
    async def chat(self, **kwargs):
        return LLMResponse(content="Project-scoped answer uses [ref-1].")


class ProjectContextOrchestrator:
    def __init__(self, shared_dir: Path):
        self._shared_dir = shared_dir
        self.calls: list[dict[str, Any]] = []
        self._running: set[str] = set()

    async def run(
        self,
        description: str,
        context: dict[str, Any] | None = None,
        active_group_ids: list[str] | None = None,
        task_id: str | None = None,
        on_task_update=None,
    ) -> IterationResult:
        assert task_id
        self.calls.append({
            "description": description,
            "context": dict(context or {}),
            "active_group_ids": list(active_group_ids) if active_group_ids is not None else None,
        })
        self._running.add(task_id)
        try:
            task = Task(id=task_id, description=description)
            task.status = TaskStatus.RUNNING
            task.started_at = datetime.now().isoformat()
            if on_task_update:
                await on_task_update(task, "planned", {
                    "active_group_ids": active_group_ids or [],
                    "context_keys": sorted((context or {}).keys()),
                })
            task.status = TaskStatus.COMPLETED
            task.result = "Project-scoped task completed."
            task.completed_at = datetime.now().isoformat()
            if on_task_update:
                await on_task_update(task, "task_completed", {"quality_score": 0.91})
            self._shared_dir.mkdir(parents=True, exist_ok=True)
            return IterationResult(
                task_id=task_id,
                shared_dir=str(self._shared_dir),
                final_score=0.91,
                iterations=[IterationRecord(iteration=0, score=0.91, improvements=[])],
                result_summary=task.result,
            )
        finally:
            self._running.discard(task_id)

    def cancel_task(self, task_id: str) -> bool:
        return False

    def list_running_tasks(self) -> list[str]:
        return sorted(self._running)


class ProjectContextWebPageParser:
    async def fetch_url(self, url: str, doc_id: str) -> Document:
        return Document(id=doc_id, filename=url, content="Project Alpha launch memo", metadata={"type": "webpage"})

    async def chunk(self, document: Document, chunk_size: int = 500, overlap: int = 50) -> list[TextChunk]:
        return [
            TextChunk(
                id=f"{document.id}_chunk_0",
                content=document.content,
                metadata={"doc_id": document.id, "filename": document.filename, "type": "webpage", "chunk_index": 0},
                index=0,
            )
        ]


async def _wait_for_task(store: PersistenceStore, description: str) -> dict[str, Any]:
    for _ in range(100):
        for task in store.list_tasks(limit=20):
            if task["description"] == description and task["status"] == "completed":
                return task
        await asyncio.sleep(0.02)
    raise AssertionError(f"task did not complete: {description}")


def _client_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    import web.api as api_module
    from web.routers import chat as chat_router
    from web.routers import documents as documents_router
    from web.routers import projects as projects_router
    from web.routers import scheduled as scheduled_router
    from web.routers import tasks as tasks_router

    store = PersistenceStore(tmp_path / "project_context.db")
    group_store = GroupStore(str(tmp_path / "groups.json"))
    rag = ProjectContextRag()
    orchestrator = ProjectContextOrchestrator(tmp_path / "shared")

    api_module._group_store = group_store
    api_module._rag_engine = rag
    api_module._orchestrator = orchestrator
    api_module._task_planner = None
    api_module._task_results = {}
    api_module._config = None
    api_module.WebPageParser = ProjectContextWebPageParser

    monkeypatch.setattr(documents_router, "_record_knowledge_graph_quality_after_document", lambda *args, **kwargs: None)
    monkeypatch.setattr(documents_router, "_check_url_not_ssrf", lambda url: None)
    monkeypatch.setattr(chat_router, "_gs", lambda: store)
    monkeypatch.setattr(chat_router, "_resolve_chat_llm", lambda worker_id: (ProjectContextProvider(), "fake", 0, 256, worker_id))
    monkeypatch.setattr(projects_router, "_gs", lambda: store)
    monkeypatch.setattr(scheduled_router, "_gs", lambda: store)
    monkeypatch.setattr(tasks_router, "_gs", lambda: store)
    tasks_router._task_job_runner = None
    tasks_router._task_job_runner_key = None
    task_runner = tasks_router.init_task_job_runner(orchestrator, None, store, api_module._task_results)

    app = FastAPI()
    app.include_router(documents_router.router)
    app.include_router(projects_router.router)
    app.include_router(chat_router.router)
    app.include_router(tasks_router.router)
    app.include_router(scheduled_router.router)
    auth = init_auth(
        [{"username": "admin", "password": "pw", "role": "admin", "display_name": "Admin"}],
        app_state=app.state,
    )
    token = auth.login("admin", "pw")
    assert token
    headers = {"Authorization": f"Bearer {token}"}
    return TestClient(app, raise_server_exceptions=False), ScheduledTaskRunner(store, task_runner), store, rag, orchestrator, headers


@pytest.mark.asyncio
async def test_project_context_scopes_documents_chat_tasks_and_schedules(monkeypatch, tmp_path: Path) -> None:
    client, scheduled_runner, store, rag, orchestrator, headers = _client_env(monkeypatch, tmp_path)
    try:
        group_resp = client.post("/api/groups", json={"name": "Alpha Project", "color": "#1677ff"})
        assert group_resp.status_code == 200
        project_id = group_resp.json()["id"]

        imported = client.post(
            "/api/documents/url",
            json={"url": "https://example.test/alpha", "group_id": project_id},
        )
        assert imported.status_code == 200
        assert imported.json()["group_id"] == project_id

        projects_payload = client.get("/api/projects").json()
        project = next(item for item in projects_payload["projects"] if item["id"] == project_id)
        assert project["metrics"]["doc_count"] == 1
        assert project["recent_documents"][0]["filename"] == "https://example.test/alpha"

        chat_resp = client.post(
            "/api/chat",
            json={
                "message": "Summarize Alpha",
                "use_rag": True,
                "stream": False,
                "project_id": project_id,
                "active_group_ids": ["wrong_group"],
            },
        )
        assert chat_resp.status_code == 200
        assert rag.search_group_calls[-1] == [project_id]
        assert chat_resp.json()["sources"][0]["group_id"] == project_id
        assert chat_resp.json()["citations"][0]["ref_id"] == "ref-1"

        direct_description = "Create Alpha project brief"
        task_resp = client.post(
            "/api/tasks",
            json={
                "description": direct_description,
                "project_id": project_id,
                "active_group_ids": ["wrong_group"],
            },
        )
        assert task_resp.status_code == 200
        direct_task = await _wait_for_task(store, direct_description)
        direct_request = store.get_task_job_request(direct_task["task_id"])
        assert direct_request["active_group_ids"] == [project_id]
        assert direct_request["context"]["project_id"] == project_id
        assert orchestrator.calls[-1]["active_group_ids"] == [project_id]

        scheduled_description = "Run Alpha weekly review"
        scheduled_resp = client.post(
            "/api/scheduled-tasks",
            headers=headers,
            json={
                "description": scheduled_description,
                "cron": "* * * * *",
                "enabled": True,
                "project_id": project_id,
                "active_group_ids": ["wrong_group"],
            },
        )
        assert scheduled_resp.status_code == 200
        scheduled = scheduled_resp.json()
        assert scheduled["active_group_ids"] == [project_id]

        await scheduled_runner._tick()
        scheduled_task = await _wait_for_task(store, scheduled_description)
        scheduled_request = store.get_task_job_request(scheduled_task["task_id"])
        assert scheduled_request["active_group_ids"] == [project_id]
        assert orchestrator.calls[-1]["context"] == {
            "source": "scheduled_task",
            "scheduled_task_id": scheduled["id"],
        }
        assert orchestrator.calls[-1]["active_group_ids"] == [project_id]

        refreshed_project = client.get(f"/api/projects/{project_id}").json()["project"]
        assert refreshed_project["metrics"]["task_count"] >= 2
        assert refreshed_project["metrics"]["scheduled_task_count"] == 1
    finally:
        scheduled_runner.stop()
        client.close()
        store.close()
