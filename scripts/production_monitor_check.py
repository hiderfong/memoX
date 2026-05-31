#!/usr/bin/env python3
"""Run read-only production monitoring checks against a MemoX deployment."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx

STATUS_RANK = {"ok": 0, "warning": 1, "error": 2}


@dataclass(frozen=True)
class Thresholds:
    max_media_pending: int = 10
    max_media_persisted_queued: int = 20
    max_media_persisted_running: int = 4
    max_recent_tool_errors: int = 0
    max_recent_tool_rejections: int = 20


def _join_url(base_url: str, path: str) -> str:
    return urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))


def _check(name: str, status: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "message": message,
        "details": details or {},
    }


def overall_status(checks: list[dict[str, Any]]) -> str:
    rank = max((STATUS_RANK.get(check.get("status", "error"), 2) for check in checks), default=0)
    return next(status for status, value in STATUS_RANK.items() if value == rank)


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def evaluate_snapshot(snapshot: dict[str, Any], thresholds: Thresholds) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    public_health = snapshot.get("public_health") or {}
    if public_health.get("status") == "healthy":
        checks.append(_check("public_health", "ok", "Public health endpoint is healthy"))
    else:
        checks.append(_check("public_health", "error", "Public health endpoint is not healthy", {"payload": public_health}))

    system_health = snapshot.get("system_health") or {}
    system_status = str(system_health.get("status") or "error")
    system_ok = bool(system_health.get("ok", False))
    if system_ok and system_status == "ok":
        checks.append(_check("system_health", "ok", "System health is ok"))
    elif system_ok and system_status == "warning":
        checks.append(_check("system_health", "warning", "System health has warnings"))
    else:
        checks.append(
            _check(
                "system_health",
                "error",
                "System health is not ok",
                {"status": system_status, "ok": system_ok},
            )
        )

    readiness_checks = system_health.get("checks") or []
    warning_checks = [item.get("name") for item in readiness_checks if item.get("status") == "warning"]
    error_checks = [item.get("name") for item in readiness_checks if item.get("status") == "error"]
    if error_checks:
        checks.append(_check("readiness_checks", "error", "Readiness checks contain errors", {"errors": error_checks}))
    elif warning_checks:
        checks.append(
            _check("readiness_checks", "warning", "Readiness checks contain warnings", {"warnings": warning_checks})
        )
    else:
        checks.append(_check("readiness_checks", "ok", "Readiness checks are clean"))

    task_jobs = ((system_health.get("ops") or {}).get("task_jobs") or {})
    needs_intervention = _safe_int(task_jobs.get("needs_intervention"))
    manual_retryable = _safe_int(task_jobs.get("manual_retryable"))
    if needs_intervention > 0:
        checks.append(
            _check(
                "task_jobs",
                "error",
                "Background tasks need operator intervention",
                {"needs_intervention": needs_intervention, "manual_retryable": manual_retryable},
            )
        )
    elif manual_retryable > 0:
        checks.append(
            _check(
                "task_jobs",
                "warning",
                "Background tasks are waiting for manual retry",
                {"needs_intervention": needs_intervention, "manual_retryable": manual_retryable},
            )
        )
    else:
        checks.append(_check("task_jobs", "ok", "No background task intervention is required", task_jobs))

    media_jobs = snapshot.get("media_jobs") or {}
    runtime_pending = _safe_int(media_jobs.get("runtime_pending"))
    persisted_queued = _safe_int(media_jobs.get("persisted_queued"))
    persisted_running = _safe_int(media_jobs.get("persisted_running"))
    media_warnings = []
    if runtime_pending > thresholds.max_media_pending:
        media_warnings.append(f"runtime_pending>{thresholds.max_media_pending}")
    if persisted_queued > thresholds.max_media_persisted_queued:
        media_warnings.append(f"persisted_queued>{thresholds.max_media_persisted_queued}")
    if persisted_running > thresholds.max_media_persisted_running:
        media_warnings.append(f"persisted_running>{thresholds.max_media_persisted_running}")
    if media_warnings:
        checks.append(
            _check(
                "media_jobs",
                "warning",
                "Media queue pressure is above threshold",
                {"warnings": media_warnings, **media_jobs},
            )
        )
    else:
        checks.append(_check("media_jobs", "ok", "Media queue pressure is within threshold", media_jobs))

    error_events = snapshot.get("error_events") or {}
    error_event_total = _safe_int(error_events.get("total") or error_events.get("count"))
    if error_event_total > 0:
        checks.append(_check("ops_error_events", "error", "Recent operational error events exist", error_events))
    else:
        checks.append(_check("ops_error_events", "ok", "No recent operational error events"))

    warning_events = snapshot.get("warning_events") or {}
    warning_event_total = _safe_int(warning_events.get("total") or warning_events.get("count"))
    if warning_event_total > 0:
        checks.append(_check("ops_warning_events", "warning", "Recent operational warning events exist", warning_events))
    else:
        checks.append(_check("ops_warning_events", "ok", "No recent operational warning events"))

    tool_errors = snapshot.get("tool_errors") or {}
    tool_error_total = _safe_int(tool_errors.get("total") or tool_errors.get("count"))
    if tool_error_total > thresholds.max_recent_tool_errors:
        checks.append(_check("tool_errors", "warning", "Recent tool errors exceed threshold", tool_errors))
    else:
        checks.append(_check("tool_errors", "ok", "Tool error volume is within threshold", tool_errors))

    tool_rejections = snapshot.get("tool_rejections") or {}
    tool_rejection_total = _safe_int(tool_rejections.get("total") or tool_rejections.get("count"))
    if tool_rejection_total > thresholds.max_recent_tool_rejections:
        checks.append(_check("tool_rejections", "warning", "Recent tool rejections exceed threshold", tool_rejections))
    else:
        checks.append(_check("tool_rejections", "ok", "Tool rejection volume is within threshold", tool_rejections))

    status = overall_status(checks)
    return {
        "ok": status != "error",
        "status": status,
        "checks": checks,
    }


def _markdown_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _counter(payload: dict[str, Any], *names: str) -> int:
    for name in names:
        value = payload.get(name)
        if value is not None:
            return _safe_int(value)
    return 0


def build_markdown_summary(result: dict[str, Any]) -> str:
    checks = [check for check in result.get("checks", []) if isinstance(check, dict)]
    snapshot = result.get("snapshot") if isinstance(result.get("snapshot"), dict) else {}
    media_jobs = snapshot.get("media_jobs") if isinstance(snapshot.get("media_jobs"), dict) else {}
    error_events = snapshot.get("error_events") if isinstance(snapshot.get("error_events"), dict) else {}
    warning_events = snapshot.get("warning_events") if isinstance(snapshot.get("warning_events"), dict) else {}
    tool_errors = snapshot.get("tool_errors") if isinstance(snapshot.get("tool_errors"), dict) else {}
    tool_rejections = snapshot.get("tool_rejections") if isinstance(snapshot.get("tool_rejections"), dict) else {}

    lines = [
        "## MemoX Production Monitor",
        "",
        f"- Base URL: `{result.get('base_url', '')}`",
        f"- Status: **{str(result.get('status', 'error')).upper()}**",
        f"- OK: `{bool(result.get('ok', False))}`",
        "",
        "### Key Metrics",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Runtime pending media jobs | {_counter(media_jobs, 'runtime_pending')} |",
        f"| Persisted queued media jobs | {_counter(media_jobs, 'persisted_queued')} |",
        f"| Persisted running media jobs | {_counter(media_jobs, 'persisted_running')} |",
        f"| Recent operational error events | {_counter(error_events, 'total', 'count')} |",
        f"| Recent operational warning events | {_counter(warning_events, 'total', 'count')} |",
        f"| Recent tool errors | {_counter(tool_errors, 'total', 'count')} |",
        f"| Recent tool rejections | {_counter(tool_rejections, 'total', 'count')} |",
        "",
        "### Checks",
        "",
        "| Check | Status | Message |",
        "|---|---|---|",
    ]

    for check in checks:
        lines.append(
            "| "
            f"{_markdown_cell(check.get('name', 'unknown'))} | "
            f"{_markdown_cell(check.get('status', 'error'))} | "
            f"{_markdown_cell(check.get('message', ''))} |"
        )

    attention_checks = [check for check in checks if check.get("status") != "ok"]
    if attention_checks:
        lines.extend(["", "### Attention", ""])
        for check in attention_checks:
            lines.append(
                f"- `{check.get('name', 'unknown')}` is `{check.get('status', 'error')}`: "
                f"{check.get('message', '')}"
            )
            details = check.get("details")
            if details:
                details_text = json.dumps(details, ensure_ascii=False, sort_keys=True)
                if len(details_text) > 800:
                    details_text = details_text[:797] + "..."
                lines.append(f"  - Details: `{details_text}`")

    return "\n".join(lines).rstrip() + "\n"


def _request_json(client: httpx.Client, method: str, url: str, *, token: str | None = None, **kwargs: Any) -> dict[str, Any]:
    headers = kwargs.pop("headers", {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    response = client.request(method, url, headers=headers, **kwargs)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError(f"Expected JSON object from {url}")
    return payload


def login(client: httpx.Client, base_url: str, username: str, password: str) -> str:
    payload = _request_json(
        client,
        "POST",
        _join_url(base_url, "/api/auth/login"),
        json={"username": username, "password": password},
    )
    token = payload.get("token")
    if not isinstance(token, str) or not token:
        raise RuntimeError("Login response did not include a token")
    return token


def collect_snapshot(
    *,
    base_url: str,
    timeout: float,
    token: str | None,
    username: str,
    password: str | None,
    limit: int,
) -> dict[str, Any]:
    with httpx.Client(timeout=timeout) as client:
        public_health = _request_json(client, "GET", _join_url(base_url, "/api/health"))
        auth_token = token or (login(client, base_url, username, password) if password else None)
        if not auth_token:
            raise RuntimeError("Provide MEMOX_TOKEN or MEMOX_ADMIN_PASSWORD for authenticated monitoring checks")

        return {
            "public_health": public_health,
            "system_health": _request_json(client, "GET", _join_url(base_url, "/api/system/health"), token=auth_token),
            "media_jobs": _request_json(client, "GET", _join_url(base_url, "/api/videos/jobs/status"), token=auth_token),
            "error_events": _request_json(
                client,
                "GET",
                _join_url(base_url, f"/api/system/events?status=error&limit={limit}"),
                token=auth_token,
            ),
            "warning_events": _request_json(
                client,
                "GET",
                _join_url(base_url, f"/api/system/events?status=warning&limit={limit}"),
                token=auth_token,
            ),
            "tool_errors": _request_json(
                client,
                "GET",
                _join_url(base_url, f"/api/system/tool-audit?status=error&limit={limit}"),
                token=auth_token,
            ),
            "tool_rejections": _request_json(
                client,
                "GET",
                _join_url(base_url, f"/api/system/tool-audit?status=rejected&limit={limit}"),
                token=auth_token,
            ),
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.environ.get("MEMOX_URL", "http://127.0.0.1:8080"))
    parser.add_argument("--username", default=os.environ.get("MEMOX_USERNAME", "admin"))
    parser.add_argument("--password", default=os.environ.get("MEMOX_ADMIN_PASSWORD"))
    parser.add_argument("--token", default=os.environ.get("MEMOX_TOKEN"))
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--strict", action="store_true", help="Exit non-zero on warnings as well as errors")
    parser.add_argument("--output", help="Write the full JSON result to this path")
    parser.add_argument("--summary-output", help="Write a Markdown summary to this path")
    parser.add_argument("--max-media-pending", type=int, default=10)
    parser.add_argument("--max-media-persisted-queued", type=int, default=20)
    parser.add_argument("--max-media-persisted-running", type=int, default=4)
    parser.add_argument("--max-recent-tool-errors", type=int, default=0)
    parser.add_argument("--max-recent-tool-rejections", type=int, default=20)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    thresholds = Thresholds(
        max_media_pending=args.max_media_pending,
        max_media_persisted_queued=args.max_media_persisted_queued,
        max_media_persisted_running=args.max_media_persisted_running,
        max_recent_tool_errors=args.max_recent_tool_errors,
        max_recent_tool_rejections=args.max_recent_tool_rejections,
    )
    try:
        snapshot = collect_snapshot(
            base_url=args.base_url,
            timeout=args.timeout,
            token=args.token,
            username=args.username,
            password=args.password,
            limit=args.limit,
        )
        evaluation = evaluate_snapshot(snapshot, thresholds)
        result = {"base_url": args.base_url, **evaluation, "snapshot": snapshot}
    except Exception as exc:
        result = {
            "base_url": args.base_url,
            "ok": False,
            "status": "error",
            "checks": [
                _check(
                    "monitor_collection",
                    "error",
                    "Failed to collect production monitoring snapshot",
                    {"error_type": type(exc).__name__, "error": str(exc)},
                )
            ],
        }

    json_result = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(json_result + "\n", encoding="utf-8")
    if args.summary_output:
        Path(args.summary_output).write_text(build_markdown_summary(result), encoding="utf-8")

    print(json_result)
    if result["status"] == "error":
        return 1
    if args.strict and result["status"] == "warning":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
