"""SQLite 持久化存储测试"""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from src.ops.readiness import _sqlite_quick_check
from storage.persistence import SCHEMA_VERSION, PersistenceStore, SchemaMigrationError


def test_session_create_and_list(tmp_path):
    store = PersistenceStore(tmp_path / "test.db")
    store.save_session("s1", "测试会话")
    sessions = store.list_sessions()
    assert len(sessions) == 1
    assert sessions[0]["id"] == "s1"
    assert sessions[0]["title"] == "测试会话"
    store.close()


def test_message_save_and_retrieve(tmp_path):
    store = PersistenceStore(tmp_path / "test.db")
    store.save_message("s1", "user", "你好")
    store.save_message("s1", "assistant", "你好！有什么可以帮你的？")

    messages = store.get_session_messages("s1")
    assert len(messages) == 2
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "你好"
    assert messages[1]["role"] == "assistant"
    store.close()


def test_message_auto_creates_session(tmp_path):
    store = PersistenceStore(tmp_path / "test.db")
    store.save_message("auto_s", "user", "消息")
    sessions = store.list_sessions()
    assert any(s["id"] == "auto_s" for s in sessions)
    store.close()


def test_delete_session(tmp_path):
    store = PersistenceStore(tmp_path / "test.db")
    store.save_message("s1", "user", "msg")
    assert store.delete_session("s1") is True
    assert store.get_session_messages("s1") == []
    assert store.delete_session("nonexistent") is False
    store.close()


def test_task_save_and_retrieve(tmp_path):
    store = PersistenceStore(tmp_path / "test.db")
    task_data = {
        "task_id": "task_001",
        "description": "编写计算器",
        "result": "已完成",
        "final_score": 0.85,
        "iterations": [{"iteration": 0, "score": 0.85, "improvements": []}],
        "mail_log": "=== 邮件日志 ===",
        "shared_dir": "/tmp/shared",
        "suggestions": [{"type": "code_quality", "title": "改进"}],
    }
    store.save_task(task_data)

    retrieved = store.get_task("task_001")
    assert retrieved is not None
    assert retrieved["task_id"] == "task_001"
    assert retrieved["description"] == "编写计算器"
    assert retrieved["final_score"] == 0.85
    assert len(retrieved["iterations"]) == 1
    assert retrieved["mail_log"] == "=== 邮件日志 ==="
    store.close()


def test_task_list_ordered_by_time(tmp_path):
    store = PersistenceStore(tmp_path / "test.db")
    for i in range(3):
        store.save_task({"task_id": f"task_{i}", "description": f"任务{i}", "result": "", "final_score": 0.5})

    tasks = store.list_tasks()
    assert len(tasks) == 3
    # 最近的排在前面
    assert tasks[0]["task_id"] == "task_2"
    store.close()


def test_task_upsert(tmp_path):
    store = PersistenceStore(tmp_path / "test.db")
    store.save_task({"task_id": "t1", "description": "v1", "result": "", "final_score": 0.3})
    store.save_task({"task_id": "t1", "description": "v1", "result": "更新", "final_score": 0.9})

    task = store.get_task("t1")
    assert task["final_score"] == 0.9
    assert task["result"] == "更新"
    # 应该只有 1 条记录
    assert len(store.list_tasks()) == 1
    store.close()


def test_running_task_keeps_completed_at_empty(tmp_path):
    store = PersistenceStore(tmp_path / "test.db")
    store.save_task({"task_id": "running_1", "description": "run", "status": "running"})

    task = store.get_task("running_1")
    assert task["status"] == "running"
    assert task["completed_at"] is None
    store.close()


def test_mark_incomplete_tasks_interrupted(tmp_path):
    store = PersistenceStore(tmp_path / "test.db")
    store.save_task({"task_id": "running_1", "description": "run", "status": "running"})
    store.save_task({"task_id": "done_1", "description": "done", "status": "completed", "result": "ok"})

    count = store.mark_incomplete_tasks_interrupted()

    assert count == 1
    interrupted = store.get_task("running_1")
    assert interrupted["status"] == "failed"
    assert "中断" in interrupted["result"]
    assert store.get_task("done_1")["status"] == "completed"
    store.close()


def test_recoverable_task_job_is_not_marked_interrupted(tmp_path):
    store = PersistenceStore(tmp_path / "test.db")
    store.save_task({"task_id": "running_1", "description": "run", "status": "running"})
    store.save_task_job_request("running_1", "run", context={"x": 1}, active_group_ids=["g1"], timeout_seconds=30)

    count = store.mark_incomplete_tasks_interrupted()

    assert count == 0
    assert store.get_task("running_1")["status"] == "running"
    recoverable = store.list_recoverable_task_jobs()
    assert len(recoverable) == 1
    assert recoverable[0]["task_id"] == "running_1"
    assert recoverable[0]["context"] == {"x": 1}
    assert recoverable[0]["active_group_ids"] == ["g1"]
    assert recoverable[0]["timeout_seconds"] == 30
    store.close()


def test_task_events_round_trip(tmp_path):
    store = PersistenceStore(tmp_path / "test.db")
    store.save_task({"task_id": "task_events", "description": "events", "status": "queued"})
    store.add_task_event("task_events", "queued", "入队", {"recoverable": True})
    store.add_task_event("task_events", "running", "开始")

    events = store.list_task_events("task_events")

    assert [event["event_type"] for event in events] == ["queued", "running"]
    assert events[0]["message"] == "入队"
    assert events[0]["details"] == {"recoverable": True}
    store.close()


def test_worker_logs_round_trip_and_filters(tmp_path):
    store = PersistenceStore(tmp_path / "test.db")
    store.add_worker_log(
        "researcher",
        "info",
        "LLM 调用完成",
        {"task_id": "task_1", "subtask_id": "sub_1", "input_tokens": 10, "output_tokens": 5},
        created_at="2026-05-28T10:00:00",
    )
    store.add_worker_log(
        "writer",
        "error",
        "工具失败",
        {"task_id": "task_1", "subtask_id": "sub_2"},
        created_at="2026-05-28T10:01:00",
    )

    task_logs = store.list_worker_logs(task_id="task_1")
    researcher_logs = store.list_worker_logs(worker_id="researcher")
    subtask_logs = store.list_worker_logs(subtask_id="sub_2", level="error")

    assert [log["worker_id"] for log in task_logs] == ["writer", "researcher"]
    assert researcher_logs[0]["meta"]["input_tokens"] == 10
    assert subtask_logs[0]["message"] == "工具失败"
    assert store.delete_worker_logs("researcher") == 1
    assert [log["worker_id"] for log in store.list_worker_logs(task_id="task_1")] == ["writer"]
    store.close()


def test_task_job_auto_retry_schedule_round_trip(tmp_path):
    store = PersistenceStore(tmp_path / "test.db")
    store.save_task({"task_id": "retry_1", "description": "retry", "status": "timeout"})
    store.save_task_job_request("retry_1", "retry")

    scheduled = store.schedule_task_job_auto_retry("retry_1", "2026-05-21T10:00:00", max_attempts=2)

    assert scheduled is not None
    assert scheduled["auto_retry_count"] == 1
    assert scheduled["next_retry_at"] == "2026-05-21T10:00:00"
    retries = store.list_scheduled_task_job_retries()
    assert [retry["task_id"] for retry in retries] == ["retry_1"]
    stats = store.get_task_job_stats()
    assert stats["scheduled_retries"] == 1
    assert stats["timeout"] == 1
    assert stats["retryable_failures"] == 1
    assert stats["manual_retryable"] == 0
    assert stats["needs_intervention"] == 0

    assert store.clear_task_job_auto_retry("retry_1") is True
    assert store.get_task_job_request("retry_1")["next_retry_at"] == ""
    assert store.list_scheduled_task_job_retries() == []
    store.close()


def test_task_job_stats_classifies_failures_and_intervention(tmp_path):
    store = PersistenceStore(tmp_path / "test.db")
    created_at = "2026-05-21T10:00:00"

    store.save_task({"task_id": "task_active", "description": "active", "status": "running", "created_at": created_at})
    store.save_task_job_request("task_active", "active", created_at=created_at)

    store.save_task({"task_id": "task_retryable", "description": "retryable", "status": "failed"})
    store.save_task_job_request("task_retryable", "retryable")
    store.add_task_event(
        "task_retryable",
        "failed_retryable",
        "临时错误",
        {"failure_type": "retryable_exception", "retryable": True},
    )

    store.save_task({"task_id": "task_fixed", "description": "fixed", "status": "failed"})
    store.save_task_job_request("task_fixed", "fixed")
    store.add_task_event(
        "task_fixed",
        "failed_non_retryable",
        "配置错误",
        {"failure_type": "non_retryable_exception", "retryable": False},
    )

    store.save_task({"task_id": "task_exhausted", "description": "exhausted", "status": "failed"})
    store.save_task_job_request("task_exhausted", "exhausted")
    store.add_task_event(
        "task_exhausted",
        "auto_retry_exhausted",
        "自动重试已耗尽",
        {"failure_type": "retryable_exception", "retryable": True},
    )

    stats = store.get_task_job_stats()

    assert stats["active"] == 1
    assert stats["running"] == 1
    assert stats["failed"] == 3
    assert stats["retryable_failures"] == 2
    assert stats["non_retryable_failures"] == 1
    assert stats["manual_retryable"] == 2
    assert stats["needs_intervention"] == 2
    assert stats["auto_retry_exhausted"] == 1
    assert stats["oldest_active_created_at"] == created_at
    assert isinstance(stats["oldest_active_age_seconds"], int)
    assert stats["last_job_updated_at"]
    store.close()


def test_task_checkpoint_round_trip(tmp_path):
    store = PersistenceStore(tmp_path / "test.db")
    checkpoint = {
        "task_id": "task_cp",
        "status": "running",
        "sub_tasks": [
            {
                "id": "sub_1",
                "status": "completed",
                "attempts": 1,
            }
        ],
    }

    store.save_task_checkpoint("task_cp", checkpoint)
    loaded = store.get_task_checkpoint("task_cp")

    assert loaded["task_id"] == "task_cp"
    assert loaded["sub_tasks"][0]["status"] == "completed"
    assert loaded["sub_tasks"][0]["attempts"] == 1
    assert "updated_at" in loaded
    store.close()


def test_get_nonexistent_task(tmp_path):
    store = PersistenceStore(tmp_path / "test.db")
    assert store.get_task("nonexistent") is None
    store.close()


def test_update_session_title(tmp_path):
    store = PersistenceStore(tmp_path / "test.db")
    store.save_session("s1")
    store.update_session_title("s1", "新标题")
    sessions = store.list_sessions()
    assert sessions[0]["title"] == "新标题"
    store.close()


def test_runtime_schema_matches_memory_and_summary_migrations(tmp_path):
    store = PersistenceStore(tmp_path / "test.db")

    assert store.schema_version() == SCHEMA_VERSION
    assert [item["version"] for item in store.applied_migrations()] == [1, 2, 3, 4, 5, 6, 7]

    session_cols = {
        r["name"] for r in store._conn.execute("PRAGMA table_info(chat_sessions)").fetchall()
    }
    assert "summary" in session_cols
    task_job_cols = {
        r["name"] for r in store._conn.execute("PRAGMA table_info(task_jobs)").fetchall()
    }
    assert {
        "lease_owner",
        "lease_expires_at",
        "recovery_count",
        "last_recovered_at",
        "auto_retry_count",
        "next_retry_at",
    }.issubset(task_job_cols)
    worker_log_cols = {
        r["name"] for r in store._conn.execute("PRAGMA table_info(worker_logs)").fetchall()
    }
    assert {
        "worker_id",
        "level",
        "message",
        "task_id",
        "subtask_id",
        "meta",
        "created_at",
    }.issubset(worker_log_cols)

    tables = {
        r["name"]
        for r in store._conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'trigger')").fetchall()
    }
    assert "memories_fts" in tables
    assert {"memories_ai", "memories_ad", "memories_au"}.issubset(tables)

    store.save_memory("m1", "Python memory", category="fact")
    fts_rows = store._conn.execute(
        "SELECT rowid, content FROM memories_fts WHERE memories_fts MATCH ?",
        ("Python",),
    ).fetchall()
    assert len(fts_rows) == 1

    store.save_memory("m1", "Rust memory", category="fact")
    assert store._conn.execute(
        "SELECT rowid FROM memories_fts WHERE memories_fts MATCH ?",
        ("Python",),
    ).fetchall() == []
    assert len(store._conn.execute(
        "SELECT rowid FROM memories_fts WHERE memories_fts MATCH ?",
        ("Rust",),
    ).fetchall()) == 1
    store.close()


def test_legacy_database_is_migrated_without_losing_sessions(tmp_path):
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE chat_sessions (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute(
        "INSERT INTO chat_sessions (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
        ("legacy-session", "Legacy", "2024-01-01T00:00:00", "2024-01-01T00:00:00"),
    )
    conn.commit()
    conn.close()

    store = PersistenceStore(db_path)

    cols = {r["name"] for r in store._conn.execute("PRAGMA table_info(chat_sessions)").fetchall()}
    row = store._conn.execute("SELECT id, title, archived, summary FROM chat_sessions").fetchone()
    assert {"archived", "summary"}.issubset(cols)
    assert dict(row) == {
        "id": "legacy-session",
        "title": "Legacy",
        "archived": 0,
        "summary": "",
    }
    assert store.schema_version() == SCHEMA_VERSION
    assert [item["version"] for item in store.applied_migrations()] == [1, 2, 3, 4, 5, 6, 7]
    store.close()

    reopened = PersistenceStore(db_path)
    assert [item["version"] for item in reopened.applied_migrations()] == [1, 2, 3, 4, 5, 6, 7]
    reopened.close()


def test_legacy_task_jobs_table_gains_lease_columns(tmp_path):
    db_path = tmp_path / "legacy_jobs.db"
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA user_version = 3")
    conn.execute("""
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
    """)
    conn.executemany(
        "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
        [
            (1, "chat_sessions_archive_summary", "2026-01-01T00:00:00"),
            (2, "memories_table", "2026-01-01T00:00:00"),
            (3, "memories_fts", "2026-01-01T00:00:00"),
        ],
    )
    conn.execute("""
        CREATE TABLE task_jobs (
            id TEXT PRIMARY KEY,
            description TEXT NOT NULL,
            context TEXT DEFAULT '{}',
            generate_suggestions INTEGER NOT NULL DEFAULT 1,
            active_group_ids TEXT DEFAULT 'null',
            timeout_seconds INTEGER DEFAULT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute(
        """INSERT INTO task_jobs
           (id, description, context, generate_suggestions, active_group_ids, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("task_legacy", "Legacy job", "{}", 1, "null", "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
    )
    conn.commit()
    conn.close()

    store = PersistenceStore(db_path)

    cols = {r["name"] for r in store._conn.execute("PRAGMA table_info(task_jobs)").fetchall()}
    assert {
        "lease_owner",
        "lease_expires_at",
        "recovery_count",
        "last_recovered_at",
        "auto_retry_count",
        "next_retry_at",
    }.issubset(cols)
    request = store.get_task_job_request("task_legacy")
    assert request["lease_owner"] == ""
    assert request["lease_expires_at"] == ""
    assert request["recovery_count"] == 0
    assert request["last_recovered_at"] == ""
    assert request["auto_retry_count"] == 0
    assert request["next_retry_at"] == ""
    worker_log_cols = {
        r["name"] for r in store._conn.execute("PRAGMA table_info(worker_logs)").fetchall()
    }
    assert {"worker_id", "task_id", "subtask_id", "meta"}.issubset(worker_log_cols)
    assert [item["version"] for item in store.applied_migrations()] == [1, 2, 3, 4, 5, 6, 7]
    store.close()


def test_sqlite_health_reports_schema_version_and_migrations(tmp_path):
    db_path = tmp_path / "test.db"
    store = PersistenceStore(db_path)
    store.close()

    result = _sqlite_quick_check(db_path)

    assert result["status"] == "ok"
    assert result["schema_version"] == SCHEMA_VERSION
    assert [item["version"] for item in result["migrations"]] == [1, 2, 3, 4, 5, 6, 7]


def test_future_database_schema_is_rejected(tmp_path):
    db_path = tmp_path / "future.db"
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA user_version = 999")
    conn.execute("""
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
    """)
    conn.execute(
        "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
        (999, "future_schema", "2030-01-01T00:00:00"),
    )
    conn.commit()
    conn.close()

    with pytest.raises(SchemaMigrationError, match="newer MemoX schema"):
        PersistenceStore(db_path)


def test_ops_event_record_and_latest(tmp_path):
    store = PersistenceStore(tmp_path / "test.db")

    first = store.record_ops_event(
        event_type="backup_maintenance",
        status="ok",
        action="created",
        message="created backup",
        details={"archive": "backups/one.tar.gz"},
    )
    second = store.record_ops_event(
        event_type="backup_maintenance",
        status="error",
        action="error",
        message="backup failed",
        details={"reason": "disk full"},
    )

    assert first["id"] != second["id"]
    assert store.get_latest_ops_event("backup_maintenance")["id"] == second["id"]
    events = store.list_ops_events("backup_maintenance")
    assert [event["message"] for event in events] == ["backup failed", "created backup"]
    assert events[0]["details"]["reason"] == "disk full"
    store.record_ops_event(
        event_type="restore_drill",
        status="ok",
        action="verified",
        message="restore drill passed",
        details={"name": "memox-backup-test.tar.gz"},
    )
    assert store.count_ops_events() == 3
    assert store.count_ops_events(event_type="backup_maintenance", status="ok") == 1
    assert [event["event_type"] for event in store.list_ops_events(limit=2)] == [
        "restore_drill",
        "backup_maintenance",
    ]
    assert store.list_ops_events(limit=1, offset=1)[0]["message"] == "backup failed"
    assert store.list_ops_events(event_type="backup_maintenance", status="error")[0]["message"] == "backup failed"
    store.close()


def test_audit_event_filters_parse_details(tmp_path):
    store = PersistenceStore(tmp_path / "test.db")
    store.log_audit_event(
        action="tool_call",
        resource="tool",
        resource_id="web_fetch",
        username="researcher",
        user_role="worker",
        details={
            "status": "success",
            "worker_id": "researcher",
            "task_id": "task_1",
            "arguments": {"url": "https://example.com"},
        },
    )
    store.log_audit_event(
        action="tool_call",
        resource="tool",
        resource_id="database_query",
        username="analyst",
        user_role="worker",
        details={
            "status": "rejected",
            "worker_id": "analyst",
            "task_id": "task_2",
            "error": "Write SQL requires access_mode='write'",
        },
    )
    store.log_audit_event(
        action="delete",
        resource="worker",
        resource_id="old-worker",
        username="admin",
        details={"status": "success"},
    )

    rejected = store.list_audit_events(resource="tool", action="tool_call", status="rejected")

    assert len(rejected) == 1
    assert rejected[0]["resource_id"] == "database_query"
    assert rejected[0]["details"]["worker_id"] == "analyst"
    assert store.count_audit_events(resource="tool", action="tool_call") == 2
    assert store.count_audit_events(resource="tool", action="tool_call", worker_id="researcher") == 1
    assert store.count_audit_events(resource="tool", action="tool_call", task_id="task_2") == 1
    assert store.count_audit_events(resource="tool", action="tool_call", resource_id="missing") == 0
    store.close()


# ==================== 定时任务 ====================


def test_scheduled_task_crud(tmp_path):
    """创建、读取、更新、删除定时任务"""
    store = PersistenceStore(tmp_path / "test.db")

    # create
    store.create_scheduled_task(
        task_id="task_1",
        description="每日报告",
        cron="0 9 * * *",
        active_group_ids=["g1", "g2"],
        source_session_id="sess_abc",
        next_run_at="2024-07-16T09:00",
        enabled=True,
    )

    # get
    t = store.get_scheduled_task("task_1")
    assert t is not None
    assert t["description"] == "每日报告"
    assert t["cron"] == "0 9 * * *"
    assert t["enabled"] == 1
    assert "g1" in t["active_group_ids"]

    # list
    all_tasks = store.list_scheduled_tasks()
    assert len(all_tasks) == 1

    # update
    store.update_scheduled_task(
        task_id="task_1",
        description="每周报告",
        cron="0 10 * * 1",
    )
    t2 = store.get_scheduled_task("task_1")
    assert t2["description"] == "每周报告"
    assert t2["cron"] == "0 10 * * 1"

    # delete
    store.delete_scheduled_task("task_1")
    assert store.get_scheduled_task("task_1") is None

    store.close()


def test_scheduled_task_list_enabled_only(tmp_path):
    """list_scheduled_tasks(enabled_only=True) 只返回启用的任务"""
    store = PersistenceStore(tmp_path / "test.db")

    store.create_scheduled_task("t1", "启用的", "*/5 * * * *", enabled=True)
    store.create_scheduled_task("t2", "禁用的", "* * * * *", enabled=False)

    assert len(store.list_scheduled_tasks()) == 2
    assert len(store.list_scheduled_tasks(enabled_only=True)) == 1
    assert store.list_scheduled_tasks(enabled_only=True)[0]["description"] == "启用的"

    store.close()


def test_scheduled_task_mark_run_and_next(tmp_path):
    """mark_scheduled_task_run 和 set_scheduled_task_next_run"""
    store = PersistenceStore(tmp_path / "test.db")

    store.create_scheduled_task("t_r", "run test", "* * * * *", enabled=True)
    store.mark_scheduled_task_run("t_r", "2024-07-15T10:05")
    store.set_scheduled_task_next_run("t_r", "2024-07-15T10:06")

    t = store.get_scheduled_task("t_r")
    assert t["last_run_at"] == "2024-07-15T10:05"
    assert t["next_run_at"] == "2024-07-15T10:06"

    store.close()


def test_scheduled_task_not_found(tmp_path):
    """get/update/delete 不存在的任务"""
    store = PersistenceStore(tmp_path / "test.db")

    assert store.get_scheduled_task("nonexistent") is None
    assert store.delete_scheduled_task("nonexistent") is False
    assert store.update_scheduled_task("nonexistent", description="x") is False

    store.close()
