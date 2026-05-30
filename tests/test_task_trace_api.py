import os
import sys

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from storage.persistence import PersistenceStore
from web.routers import tasks as tasks_router


def _client_with_store(monkeypatch, store: PersistenceStore) -> TestClient:
    monkeypatch.setattr(tasks_router, "_gs", lambda: store)
    app = FastAPI()
    app.include_router(tasks_router.router)
    return TestClient(app)


def test_task_trace_groups_events_by_subtask(tmp_path, monkeypatch):
    store = PersistenceStore(tmp_path / "trace.db")
    store.save_task(
        {
            "task_id": "task_trace",
            "description": "生成研究报告",
            "status": "completed",
            "created_at": "2026-05-28T10:00:00",
            "completed_at": "2026-05-28T10:03:00",
        }
    )
    store.save_task_checkpoint(
        "task_trace",
        {
            "task_id": "task_trace",
            "description": "生成研究报告",
            "status": "completed",
            "sub_tasks": [
                {
                    "id": "sub_1",
                    "description": "检索资料",
                    "dependencies": [],
                    "acceptance_criteria": ["给出来源"],
                    "status": "completed",
                    "result": "fallback ok",
                    "error": "",
                    "assigned_agent": "researcher",
                    "attempts": 2,
                    "created_at": "2026-05-28T10:00:01",
                    "started_at": "2026-05-28T10:00:05",
                    "completed_at": "2026-05-28T10:01:30",
                }
            ],
        },
    )
    store.add_task_event("task_trace", "planned", "任务规划已生成", {"subtask_count": 1})
    store.add_task_event(
        "task_trace",
        "llm_usage",
        "子任务 sub_1 LLM 调用完成",
        {
            "subtask_id": "sub_1",
            "worker_id": "researcher",
            "input_tokens": 120,
            "output_tokens": 45,
            "total_tokens": 165,
            "call_count": 1,
        },
    )
    store.add_task_event(
        "task_trace",
        "provider_retry",
        "子任务 sub_1 provider 调用失败，正在重试",
        {
            "subtask_id": "sub_1",
            "provider": "deepseek",
            "model": "deepseek-v4-pro",
            "attempt": 1,
            "error": "HTTP 500",
        },
    )
    store.add_task_event(
        "task_trace",
        "provider_fallback",
        "子任务 sub_1 已切换 fallback provider",
        {"subtask_id": "sub_1", "provider": "dashscope", "model": "qwen3.7"},
    )
    store.add_task_event("task_trace", "completed", "任务完成")
    store.log_audit_event(
        action="tool_call",
        resource="tool",
        resource_id="web_fetch",
        username="researcher",
        user_role="worker",
        details={
            "task_id": "task_trace",
            "subtask_id": "sub_1",
            "worker_id": "researcher",
            "status": "success",
            "arguments": {"url": "https://example.com"},
            "result": {"preview": "ok"},
        },
    )
    store.log_audit_event(
        action="tool_call",
        resource="tool",
        resource_id="database_query",
        username="legacy-worker",
        user_role="worker",
        details={
            "task_id": "sub_1",
            "subtask_id": "sub_1",
            "worker_id": "legacy-worker",
            "status": "rejected",
            "arguments": {"query": {"statement_type": "update"}},
            "result": {"preview": "rejected"},
        },
    )
    store.add_worker_log(
        "researcher",
        "info",
        "准备工具上下文",
        {"task_id": "task_trace", "subtask_id": "sub_1", "phase": "prepare"},
    )

    client = _client_with_store(monkeypatch, store)
    response = client.get("/api/tasks/task_trace/trace")

    assert response.status_code == 200
    payload = response.json()
    assert payload["task_id"] == "task_trace"
    assert payload["summary"]["event_count"] == 8
    assert payload["summary"]["task_event_count"] == 5
    assert payload["summary"]["worker_log_count"] == 1
    assert payload["summary"]["subtask_count"] == 1
    assert payload["summary"]["retry_count"] == 1
    assert payload["summary"]["fallback_count"] == 1
    assert payload["summary"]["tool_call_count"] == 2
    assert payload["summary"]["tool_rejected_count"] == 1
    assert payload["summary"]["llm_usage"] == {
        "input_tokens": 120,
        "output_tokens": 45,
        "total_tokens": 165,
        "call_count": 1,
    }
    assert [event["event_type"] for event in payload["unassigned_events"]] == ["planned", "completed"]
    assert payload["subtasks"][0]["id"] == "sub_1"
    assert payload["subtasks"][0]["assigned_agent"] == "researcher"
    assert [event["event_type"] for event in payload["subtasks"][0]["events"]] == [
        "llm_usage",
        "provider_retry",
        "provider_fallback",
        "tool_call",
        "tool_call",
        "worker_log",
    ]
    assert payload["subtasks"][0]["events"][0]["stage"] == "llm"
    assert payload["subtasks"][0]["events"][1]["stage"] == "provider"
    assert payload["subtasks"][0]["events"][1]["actor"]["provider"] == "deepseek"
    tool_events = [event for event in payload["subtasks"][0]["events"] if event["stage"] == "tool"]
    assert {event["details"]["tool"] for event in tool_events} == {"web_fetch", "database_query"}

    tool_response = client.get("/api/tasks/task_trace/trace", params={"stage": "tool"})
    assert tool_response.status_code == 200
    tool_payload = tool_response.json()
    assert tool_payload["summary"]["event_count"] == 2
    assert tool_payload["summary"]["tool_call_count"] == 2
    assert {event["stage"] for event in tool_payload["timeline"]} == {"tool"}

    worker_response = client.get("/api/tasks/task_trace/trace", params={"worker_id": "legacy-worker"})
    assert worker_response.status_code == 200
    worker_payload = worker_response.json()
    assert worker_payload["summary"]["event_count"] == 1
    assert worker_payload["timeline"][0]["details"]["tool"] == "database_query"

    diagnosis_response = client.get("/api/tasks/task_trace/diagnosis")
    assert diagnosis_response.status_code == 200
    diagnosis = diagnosis_response.json()
    assert diagnosis["task_id"] == "task_trace"
    assert diagnosis["level"] == "warning"
    assert diagnosis["metrics"]["tool_rejected_count"] == 1
    assert diagnosis["metrics"]["fallback_count"] == 1
    assert diagnosis["metrics"]["max_llm_call"]["total_tokens"] == 165
    assert any("工具调用被策略拦截" in item for item in diagnosis["root_causes"])
    assert any("Provider 调用出现波动" in item for item in diagnosis["root_causes"])
    assert diagnosis["evidence"]

    report_response = client.get("/api/tasks/task_trace/diagnosis-report")
    assert report_response.status_code == 200
    report = report_response.json()
    assert report["filename"] == "memox-diagnosis-task_trace.md"
    assert report["content_type"].startswith("text/markdown")
    assert "# MemoX Task Diagnosis Report: task_trace" in report["markdown"]
    assert "## Retry Suggestion" in report["markdown"]
    assert "Tool Rejections: 1" in report["markdown"]
    assert report["share_text"].startswith("# MemoX Task Diagnosis Report")
    store.close()


def test_task_trace_returns_404_for_unknown_task(tmp_path, monkeypatch):
    store = PersistenceStore(tmp_path / "trace.db")
    client = _client_with_store(monkeypatch, store)

    response = client.get("/api/tasks/missing/trace")

    assert response.status_code == 404
    store.close()


def test_task_retry_suggestion_for_retryable_failure(tmp_path, monkeypatch):
    store = PersistenceStore(tmp_path / "trace.db")
    store.save_task({"task_id": "task_retry", "description": "重试任务", "status": "failed"})
    store.save_task_job_request("task_retry", "重试任务")
    store.add_task_event(
        "task_retry",
        "failed_retryable",
        "任务失败，可重试",
        {"failure_type": "retryable_exception", "retryable": True},
    )

    client = _client_with_store(monkeypatch, store)
    response = client.get("/api/tasks/task_retry/retry-suggestion")

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "manual_retry"
    assert payload["retryable"] is True
    assert payload["force_required"] is False
    assert payload["retry_request"] == {
        "enabled": True,
        "method": "POST",
        "path": "/api/tasks/task_retry/retry",
        "body": {"force": False},
    }
    store.close()


def test_task_retry_suggestion_flags_force_after_tool_blocker(tmp_path, monkeypatch):
    store = PersistenceStore(tmp_path / "trace.db")
    store.save_task({"task_id": "task_blocked", "description": "阻塞任务", "status": "failed"})
    store.save_task_job_request("task_blocked", "阻塞任务")
    store.add_task_event(
        "task_blocked",
        "failed_non_retryable",
        "任务失败，需人工处理",
        {"failure_type": "non_retryable_exception", "retryable": False},
    )
    store.log_audit_event(
        action="tool_call",
        resource="tool",
        resource_id="database_query",
        details={
            "task_id": "task_blocked",
            "worker_id": "writer",
            "status": "rejected",
            "arguments": {"query": {"statement_type": "update"}},
        },
    )

    client = _client_with_store(monkeypatch, store)
    response = client.get("/api/tasks/task_blocked/retry-suggestion")

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "manual_force_after_fix"
    assert payload["retryable"] is False
    assert payload["force_required"] is True
    assert payload["retry_request"]["body"] == {"force": True}
    assert any(blocker["type"] == "tool_policy_rejection" for blocker in payload["blockers"])
    store.close()
