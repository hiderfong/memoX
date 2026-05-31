"""Regression tests for the production monitoring probe."""

from __future__ import annotations

from typing import Any

from scripts.production_monitor_check import Thresholds, build_markdown_summary, evaluate_snapshot, overall_status


def _healthy_snapshot(**overrides: dict[str, Any]) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "public_health": {"status": "healthy"},
        "system_health": {
            "ok": True,
            "status": "ok",
            "checks": [
                {"name": "config", "status": "ok"},
                {"name": "disk", "status": "ok"},
                {"name": "latest_backup", "status": "ok"},
            ],
            "ops": {"task_jobs": {"needs_intervention": 0, "manual_retryable": 0}},
        },
        "media_jobs": {"runtime_pending": 0, "persisted_queued": 0, "persisted_running": 0},
        "error_events": {"total": 0, "count": 0},
        "warning_events": {"total": 0, "count": 0},
        "tool_errors": {"total": 0, "count": 0},
        "tool_rejections": {"total": 0, "count": 0},
    }
    snapshot.update(overrides)
    return snapshot


def test_overall_status_ranks_errors_above_warnings() -> None:
    assert overall_status([]) == "ok"
    assert overall_status([{"status": "ok"}, {"status": "warning"}]) == "warning"
    assert overall_status([{"status": "warning"}, {"status": "error"}]) == "error"


def test_evaluate_snapshot_accepts_clean_production_state() -> None:
    result = evaluate_snapshot(_healthy_snapshot(), Thresholds())

    assert result["ok"] is True
    assert result["status"] == "ok"
    assert {check["name"] for check in result["checks"]} >= {
        "public_health",
        "system_health",
        "readiness_checks",
        "task_jobs",
        "media_jobs",
        "ops_error_events",
        "ops_warning_events",
        "tool_errors",
        "tool_rejections",
    }


def test_evaluate_snapshot_reports_operator_warnings_without_failing() -> None:
    system_health = {
        "ok": True,
        "status": "warning",
        "checks": [
            {"name": "latest_backup", "status": "warning"},
            {"name": "archive_mirror", "status": "ok"},
        ],
        "ops": {"task_jobs": {"needs_intervention": 0, "manual_retryable": 2}},
    }
    result = evaluate_snapshot(
        _healthy_snapshot(
            system_health=system_health,
            media_jobs={"runtime_pending": 0, "persisted_queued": 21, "persisted_running": 0},
            warning_events={"total": 1, "count": 1},
            tool_rejections={"total": 21, "count": 21},
        ),
        Thresholds(),
    )

    assert result["ok"] is True
    assert result["status"] == "warning"
    assert {
        check["name"]
        for check in result["checks"]
        if check["status"] == "warning"
    } >= {"system_health", "readiness_checks", "task_jobs", "media_jobs", "ops_warning_events", "tool_rejections"}


def test_evaluate_snapshot_fails_on_service_or_task_errors() -> None:
    result = evaluate_snapshot(
        _healthy_snapshot(
            public_health={"status": "unhealthy"},
            system_health={
                "ok": False,
                "status": "error",
                "checks": [{"name": "sqlite", "status": "error"}],
                "ops": {"task_jobs": {"needs_intervention": 1, "manual_retryable": 3}},
            },
            error_events={"total": 2, "count": 2},
        ),
        Thresholds(),
    )

    assert result["ok"] is False
    assert result["status"] == "error"
    assert {
        check["name"]
        for check in result["checks"]
        if check["status"] == "error"
    } >= {"public_health", "system_health", "readiness_checks", "task_jobs", "ops_error_events"}


def test_markdown_summary_highlights_metrics_and_attention_items() -> None:
    snapshot = _healthy_snapshot(
        media_jobs={"runtime_pending": 2, "persisted_queued": 21, "persisted_running": "not-a-number"},
        warning_events={"total": 1, "count": 1},
        tool_rejections={"total": 21, "count": 21},
    )
    evaluation = evaluate_snapshot(snapshot, Thresholds())
    summary = build_markdown_summary({"base_url": "https://memo.example", **evaluation, "snapshot": snapshot})

    assert "## MemoX Production Monitor" in summary
    assert "Status: **WARNING**" in summary
    assert "| Persisted queued media jobs | 21 |" in summary
    assert "| Recent tool rejections | 21 |" in summary
    assert "### Attention" in summary
    assert "`media_jobs` is `warning`" in summary
    assert "Bearer" not in summary
    assert "sk-" not in summary
