"""Projects router — productized workspace views backed by knowledge groups."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException

from web.state import get_store as _gs

router = APIRouter(prefix="/api/projects", tags=["projects"])


ACTIVE_TASK_STATUSES = {"queued", "pending", "running"}
FAILED_TASK_STATUSES = {"failed", "timeout", "cancelled"}


def _parse_group_ids(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, list):
        return [str(item) for item in value if item]
    if isinstance(value, str):
        text = value.strip()
        if not text or text == "null":
            return None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return [part.strip() for part in text.split(",") if part.strip()]
        if parsed is None:
            return None
        if isinstance(parsed, list):
            return [str(item) for item in parsed if item]
    return []


def _applies_to_group(active_group_ids: list[str] | None, group_id: str) -> bool:
    if active_group_ids is None:
        return True
    return group_id in active_group_ids


def _safe_score(score: Any) -> float | None:
    try:
        value = float(score)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, value if value <= 1 else value / 100))


def _task_brief(task: dict[str, Any], job: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "task_id": task.get("task_id", ""),
        "description": task.get("description", ""),
        "status": task.get("status", ""),
        "final_score": _safe_score(task.get("final_score")),
        "created_at": task.get("created_at", ""),
        "completed_at": task.get("completed_at", ""),
        "next_retry_at": (job or {}).get("next_retry_at", ""),
        "active_group_ids": _parse_group_ids((job or {}).get("active_group_ids")),
    }


def _project_health(metrics: dict[str, Any]) -> str:
    if metrics["active_task_count"]:
        return "active"
    if metrics["failed_task_count"] or metrics["waiting_retry_count"]:
        return "attention"
    if metrics["doc_count"] == 0:
        return "empty"
    return "healthy"


def _load_project_payload(project_id: str | None = None) -> dict[str, Any]:
    from web.api import _group_store, _rag_engine

    store = _gs()
    groups = _group_store.list_groups()
    if project_id and not _group_store.get_group(project_id):
        raise HTTPException(status_code=404, detail="Project not found")

    docs = _rag_engine.list_documents()
    tasks = store.list_tasks(limit=500) if store else []
    scheduled = store.list_scheduled_tasks() if store else []

    task_jobs = {
        task.get("task_id"): store.get_task_job_request(task.get("task_id", ""))
        for task in tasks
        if store and task.get("task_id")
    }

    projects: list[dict[str, Any]] = []
    for group in groups:
        if project_id and group.id != project_id:
            continue

        group_docs = [doc for doc in docs if (doc.group_id or "ungrouped") == group.id]
        project_tasks: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
        for task in tasks:
            job = task_jobs.get(task.get("task_id"))
            active_group_ids = _parse_group_ids((job or {}).get("active_group_ids"))
            if _applies_to_group(active_group_ids, group.id):
                project_tasks.append((task, job))

        project_scheduled = []
        for item in scheduled:
            active_group_ids = _parse_group_ids(item.get("active_group_ids"))
            if _applies_to_group(active_group_ids, group.id):
                project_scheduled.append(item)

        task_status_counts: dict[str, int] = {}
        for task, _job in project_tasks:
            status = str(task.get("status") or "unknown")
            task_status_counts[status] = task_status_counts.get(status, 0) + 1

        active_task_count = sum(task_status_counts.get(status, 0) for status in ACTIVE_TASK_STATUSES)
        failed_task_count = sum(task_status_counts.get(status, 0) for status in FAILED_TASK_STATUSES)
        waiting_retry_count = sum(
            1
            for _task, job in project_tasks
            if (job or {}).get("next_retry_at")
        )
        completed_scores = [
            score
            for task, _job in project_tasks
            if task.get("status") == "completed"
            and (score := _safe_score(task.get("final_score"))) is not None
        ]
        average_score = round(sum(completed_scores) / len(completed_scores), 4) if completed_scores else None

        metrics = {
            "doc_count": len(group_docs),
            "chunk_count": sum(int(getattr(doc, "chunk_count", 0) or 0) for doc in group_docs),
            "task_count": len(project_tasks),
            "active_task_count": active_task_count,
            "failed_task_count": failed_task_count,
            "waiting_retry_count": waiting_retry_count,
            "scheduled_task_count": len(project_scheduled),
            "enabled_scheduled_task_count": sum(1 for item in project_scheduled if item.get("enabled")),
            "average_score": average_score,
            "task_status_counts": task_status_counts,
        }
        recent_tasks = [
            _task_brief(task, job)
            for task, job in sorted(project_tasks, key=lambda item: item[0].get("created_at", ""), reverse=True)[:5]
        ]
        recent_documents = [
            {
                "id": doc.id,
                "filename": doc.filename,
                "type": doc.type,
                "chunk_count": doc.chunk_count,
                "created_at": doc.created_at,
            }
            for doc in sorted(group_docs, key=lambda item: item.created_at, reverse=True)[:5]
        ]
        next_scheduled = sorted(
            [item for item in project_scheduled if item.get("enabled") and item.get("next_run_at")],
            key=lambda item: item.get("next_run_at", ""),
        )[:3]
        projects.append({
            "id": group.id,
            "name": group.name,
            "color": group.color,
            "created_at": group.created_at,
            "source": "knowledge_group",
            "health": _project_health(metrics),
            "metrics": metrics,
            "recent_tasks": recent_tasks,
            "recent_documents": recent_documents,
            "next_scheduled_tasks": next_scheduled,
        })

    projects.sort(
        key=lambda item: (
            item["health"] != "active",
            item["health"] != "attention",
            -item["metrics"]["task_count"],
            -item["metrics"]["doc_count"],
            item["name"],
        )
    )
    totals = {
        "project_count": len(projects),
        "doc_count": sum(item["metrics"]["doc_count"] for item in projects),
        "task_count": sum(item["metrics"]["task_count"] for item in projects),
        "active_task_count": sum(item["metrics"]["active_task_count"] for item in projects),
        "failed_task_count": sum(item["metrics"]["failed_task_count"] for item in projects),
        "waiting_retry_count": sum(item["metrics"]["waiting_retry_count"] for item in projects),
        "scheduled_task_count": sum(item["metrics"]["scheduled_task_count"] for item in projects),
    }
    return {"projects": projects, "summary": totals}


@router.get("")
async def list_projects() -> dict:
    """List projectized knowledge-group workspaces with operational metrics."""
    return _load_project_payload()


@router.get("/{project_id}")
async def get_project(project_id: str) -> dict:
    """Return one project workspace summary."""
    payload = _load_project_payload(project_id)
    if not payload["projects"]:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"project": payload["projects"][0]}
