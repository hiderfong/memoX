"""Lifecycle cleanup tests."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.ops.lifecycle import LifecyclePolicy, run_lifecycle_cleanup
from src.storage.persistence import PersistenceStore


def _iso_days_ago(days: int) -> str:
    return (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)).isoformat()


def _write_diagnostic(path: Path, *, days_ago: int, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    ts = (datetime.now() - timedelta(days=days_ago)).timestamp()
    os.utime(path, (ts, ts))
    return path


def test_lifecycle_cleanup_dry_run_reports_without_deleting(tmp_path: Path) -> None:
    store = PersistenceStore(tmp_path / "memox.db")
    old_event = store.record_ops_event("diagnostics_export", "ok", "exported", "old event")
    fresh_event = store.record_ops_event("diagnostics_export", "ok", "exported", "fresh event")
    store._conn.execute("UPDATE ops_events SET created_at=? WHERE id=?", (_iso_days_ago(45), old_event["id"]))
    store._conn.execute("UPDATE ops_events SET created_at=? WHERE id=?", (_iso_days_ago(1), fresh_event["id"]))
    store.log_audit_event("old", "system", username="admin")
    store.log_audit_event("fresh", "system", username="admin")
    store._conn.execute("UPDATE audit_log SET timestamp=? WHERE action=?", (_iso_days_ago(120), "old"))
    store._conn.execute("UPDATE audit_log SET timestamp=? WHERE action=?", (_iso_days_ago(1), "fresh"))
    store.save_session("user-session", "Important")
    store.set_session_archived("user-session", True)
    store._conn.commit()

    old_diag = _write_diagnostic(
        tmp_path / "diagnostics" / "memox-diagnostics-old.zip",
        days_ago=40,
        content="old",
    )
    fresh_diag = _write_diagnostic(
        tmp_path / "diagnostics" / "memox-diagnostics-fresh.zip",
        days_ago=1,
        content="fresh",
    )

    result = run_lifecycle_cleanup(
        root=tmp_path,
        store=store,
        policy=LifecyclePolicy(
            ops_event_retention_days=30,
            audit_log_retention_days=90,
            diagnostic_retention_days=30,
            max_diagnostic_bundles=20,
        ),
        dry_run=True,
    )

    table_results = {item["name"]: item for item in result["tables"]}
    assert result["dry_run"] is True
    assert result["action"] == "dry_run"
    assert result["summary"]["eligible_records"] == 2
    assert result["summary"]["core_user_data_deleted"] is False
    assert table_results["ops_events"]["eligible_count"] == 1
    assert table_results["audit_log"]["eligible_count"] == 1
    assert result["diagnostics"]["eligible_count"] == 1
    assert old_diag.exists()
    assert fresh_diag.exists()
    assert store.list_ops_events(limit=10)
    assert store.list_audit_events(limit=10)
    assert store.list_sessions(archived=True)[0]["id"] == "user-session"
    store.close()


def test_lifecycle_cleanup_execute_deletes_only_operational_expired_data(tmp_path: Path) -> None:
    mirror_dir = tmp_path / "mirror"
    store = PersistenceStore(tmp_path / "memox.db")
    old_event = store.record_ops_event("diagnostics_export", "ok", "exported", "old event")
    fresh_event = store.record_ops_event("diagnostics_export", "ok", "exported", "fresh event")
    store._conn.execute("UPDATE ops_events SET created_at=? WHERE id=?", (_iso_days_ago(45), old_event["id"]))
    store._conn.execute("UPDATE ops_events SET created_at=? WHERE id=?", (_iso_days_ago(1), fresh_event["id"]))
    store.log_audit_event("old", "system", username="admin")
    store.log_audit_event("fresh", "system", username="admin")
    store._conn.execute("UPDATE audit_log SET timestamp=? WHERE action=?", (_iso_days_ago(120), "old"))
    store._conn.execute("UPDATE audit_log SET timestamp=? WHERE action=?", (_iso_days_ago(1), "fresh"))
    store.save_session("user-session", "Important")
    store.set_session_archived("user-session", True)
    store._conn.commit()

    old_diag = _write_diagnostic(
        mirror_dir / "diagnostics" / "memox-diagnostics-old.zip",
        days_ago=40,
        content="old",
    )
    fresh_diag = _write_diagnostic(
        mirror_dir / "diagnostics" / "memox-diagnostics-fresh.zip",
        days_ago=1,
        content="fresh",
    )

    result = run_lifecycle_cleanup(
        root=tmp_path,
        store=store,
        policy=LifecyclePolicy(
            ops_event_retention_days=30,
            audit_log_retention_days=90,
            diagnostic_retention_days=30,
            max_diagnostic_bundles=20,
        ),
        archive_mirror_dir=mirror_dir,
        dry_run=False,
    )

    table_results = {item["name"]: item for item in result["tables"]}
    assert result["dry_run"] is False
    assert result["action"] == "executed"
    assert table_results["ops_events"]["deleted_count"] == 1
    assert table_results["audit_log"]["deleted_count"] == 1
    assert result["diagnostics"]["deleted_count"] == 1
    assert not old_diag.exists()
    assert fresh_diag.exists()
    assert [event["message"] for event in store.list_ops_events(limit=10)] == ["fresh event"]
    assert [event["action"] for event in store.list_audit_events(limit=10)] == ["fresh"]
    assert store.list_sessions(archived=True)[0]["id"] == "user-session"
    store.close()


def test_lifecycle_cleanup_enforces_diagnostic_count_limit(tmp_path: Path) -> None:
    store = PersistenceStore(tmp_path / "memox.db")
    oldest = _write_diagnostic(
        tmp_path / "diagnostics" / "memox-diagnostics-1.zip",
        days_ago=3,
        content="oldest",
    )
    _write_diagnostic(tmp_path / "diagnostics" / "memox-diagnostics-2.zip", days_ago=2, content="middle")
    newest = _write_diagnostic(
        tmp_path / "diagnostics" / "memox-diagnostics-3.zip",
        days_ago=1,
        content="newest",
    )

    result = run_lifecycle_cleanup(
        root=tmp_path,
        store=store,
        policy=LifecyclePolicy(
            ops_event_retention_days=0,
            audit_log_retention_days=0,
            diagnostic_retention_days=0,
            max_diagnostic_bundles=2,
        ),
        dry_run=False,
    )

    assert result["diagnostics"]["deleted_count"] == 1
    assert not oldest.exists()
    assert newest.exists()
    store.close()
