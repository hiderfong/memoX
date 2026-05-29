"""SQLite 持久化存储 - 会话与任务历史"""

import contextlib
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from loguru import logger

SCHEMA_VERSION = 9


class SchemaMigrationError(RuntimeError):
    """Raised when a SQLite database cannot be safely migrated."""


class PersistenceStore:
    """SQLite 持久化存储"""

    def __init__(self, db_path: str | Path = "data/memox.db"):
        self._db_path = str(db_path)
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._guard_supported_schema_version()
        self._init_tables()

    def close(self) -> None:
        """关闭数据库连接"""
        if self._conn:
            self._conn.close()
            self._conn = None

    def __del__(self) -> None:
        with contextlib.suppress(Exception):
            self.close()

    def _guard_supported_schema_version(self) -> None:
        row = self._conn.execute("PRAGMA user_version").fetchone()
        user_version = int(row[0]) if row else 0
        future_versions: list[int] = []
        has_migrations = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        ).fetchone()
        if has_migrations:
            future_versions = [
                int(r["version"])
                for r in self._conn.execute(
                    "SELECT version FROM schema_migrations WHERE version > ? ORDER BY version",
                    (SCHEMA_VERSION,),
                ).fetchall()
            ]
        if user_version > SCHEMA_VERSION or future_versions:
            future_label = ", ".join(str(v) for v in future_versions) or str(user_version)
            raise SchemaMigrationError(
                "SQLite database was created by a newer MemoX schema "
                f"(current={SCHEMA_VERSION}, found={future_label}); upgrade MemoX before opening it."
            )

    def _init_tables(self) -> None:
        """初始化数据库表"""
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS chat_sessions (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                archived INTEGER NOT NULL DEFAULT 0,
                summary TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                metadata TEXT DEFAULT '{}',
                FOREIGN KEY (session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS task_history (
                id TEXT PRIMARY KEY,
                description TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                result_summary TEXT DEFAULT '',
                final_score REAL DEFAULT 0.0,
                iterations TEXT DEFAULT '[]',
                mail_log TEXT DEFAULT '',
                shared_dir TEXT DEFAULT '',
                suggestions TEXT DEFAULT '[]',
                created_at TEXT NOT NULL,
                completed_at TEXT DEFAULT NULL
            );

            CREATE TABLE IF NOT EXISTS worker_token_usage (
                worker_id TEXT PRIMARY KEY,
                input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                call_count INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS worker_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                worker_id TEXT NOT NULL,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                task_id TEXT DEFAULT '',
                subtask_id TEXT DEFAULT '',
                meta TEXT DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_worker_logs_worker_created ON worker_logs(worker_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_worker_logs_task_created ON worker_logs(task_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_worker_logs_subtask_created ON worker_logs(subtask_id, created_at DESC);

            CREATE INDEX IF NOT EXISTS idx_messages_session ON chat_messages(session_id);
            CREATE INDEX IF NOT EXISTS idx_task_created ON task_history(created_at);

            CREATE TABLE IF NOT EXISTS task_jobs (
                id TEXT PRIMARY KEY,
                description TEXT NOT NULL,
                context TEXT DEFAULT '{}',
                generate_suggestions INTEGER NOT NULL DEFAULT 1,
                active_group_ids TEXT DEFAULT 'null',
                timeout_seconds INTEGER DEFAULT NULL,
                lease_owner TEXT DEFAULT '',
                lease_expires_at TEXT DEFAULT '',
                recovery_count INTEGER NOT NULL DEFAULT 0,
                last_recovered_at TEXT DEFAULT '',
                auto_retry_count INTEGER NOT NULL DEFAULT 0,
                next_retry_at TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_task_jobs_created ON task_jobs(created_at);

            CREATE TABLE IF NOT EXISTS task_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                message TEXT DEFAULT '',
                details TEXT DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_task_events_task_created ON task_events(task_id, created_at);

            CREATE TABLE IF NOT EXISTS task_checkpoints (
                task_id TEXT PRIMARY KEY,
                checkpoint TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS scheduled_tasks (
                id TEXT PRIMARY KEY,
                description TEXT NOT NULL,
                cron TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                active_group_ids TEXT DEFAULT '',
                source_session_id TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_run_at TEXT DEFAULT '',
                next_run_at TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_sched_enabled ON scheduled_tasks(enabled);

            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                username TEXT NOT NULL DEFAULT '',
                user_role TEXT NOT NULL DEFAULT '',
                action TEXT NOT NULL,
                resource TEXT NOT NULL,
                resource_id TEXT NOT NULL DEFAULT '',
                details TEXT DEFAULT '{}',
                ip_address TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp DESC);
            CREATE INDEX IF NOT EXISTS idx_audit_resource ON audit_log(resource, resource_id);

            CREATE TABLE IF NOT EXISTS ops_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                status TEXT NOT NULL,
                action TEXT NOT NULL DEFAULT '',
                message TEXT NOT NULL DEFAULT '',
                details TEXT DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ops_events_created ON ops_events(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_ops_events_type_created ON ops_events(event_type, created_at DESC);

            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                user_id TEXT,
                content TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'general',
                importance INTEGER NOT NULL DEFAULT 3,
                source_session_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_accessed_at TEXT NOT NULL,
                metadata TEXT DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_memories_user ON memories(user_id);
            CREATE INDEX IF NOT EXISTS idx_memories_category ON memories(category);
            CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at DESC);

            CREATE TABLE IF NOT EXISTS knowledge_graph_review_decisions (
                candidate_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                details TEXT DEFAULT '{}',
                username TEXT NOT NULL DEFAULT '',
                user_role TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_kg_review_status_updated
                ON knowledge_graph_review_decisions(status, updated_at DESC);

            CREATE TABLE IF NOT EXISTS knowledge_graph_quality_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                health_score INTEGER NOT NULL DEFAULT 0,
                risk_level TEXT NOT NULL DEFAULT 'low',
                relation_count INTEGER NOT NULL DEFAULT 0,
                entity_count INTEGER NOT NULL DEFAULT 0,
                source_doc_count INTEGER NOT NULL DEFAULT 0,
                source_chunk_count INTEGER NOT NULL DEFAULT 0,
                candidate_count INTEGER NOT NULL DEFAULT 0,
                open_candidate_count INTEGER NOT NULL DEFAULT 0,
                decided_candidate_count INTEGER NOT NULL DEFAULT 0,
                low_confidence_ratio REAL NOT NULL DEFAULT 0.0,
                isolated_relation_ratio REAL NOT NULL DEFAULT 0.0,
                open_review_backlog_ratio REAL NOT NULL DEFAULT 0.0,
                metrics TEXT DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_kg_quality_snapshots_created
                ON knowledge_graph_quality_snapshots(created_at DESC);
        """)
        self._run_migrations()
        self._conn.commit()

    def _column_names(self, table: str) -> set[str]:
        return {r["name"] for r in self._conn.execute(f"PRAGMA table_info({table})").fetchall()}

    def _run_migrations(self) -> None:
        migrations = [
            (1, "chat_sessions_archive_summary", self._migration_chat_sessions_archive_summary),
            (2, "memories_table", self._migration_memories_table),
            (3, "memories_fts", self._migration_memories_fts),
            (4, "task_job_leases", self._migration_task_job_leases),
            (5, "task_job_recovery_metadata", self._migration_task_job_recovery_metadata),
            (6, "task_job_auto_retry_metadata", self._migration_task_job_auto_retry_metadata),
            (7, "worker_logs_table", self._migration_worker_logs_table),
            (8, "knowledge_graph_review_decisions", self._migration_knowledge_graph_review_decisions),
            (9, "knowledge_graph_quality_snapshots", self._migration_knowledge_graph_quality_snapshots),
        ]
        applied = {
            row["version"]
            for row in self._conn.execute("SELECT version FROM schema_migrations").fetchall()
        }
        for version, name, migration in migrations:
            if version in applied:
                continue
            try:
                migration()
                self._conn.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                    (version, name, datetime.now().isoformat()),
                )
                self._conn.execute(f"PRAGMA user_version = {version}")
                self._conn.commit()
                logger.info(f"[PersistenceStore] Applied schema migration {version}: {name}")
            except Exception:
                self._conn.rollback()
                raise
        self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def _migration_chat_sessions_archive_summary(self) -> None:
        cols = self._column_names("chat_sessions")
        if "archived" not in cols:
            self._conn.execute("ALTER TABLE chat_sessions ADD COLUMN archived INTEGER NOT NULL DEFAULT 0")
        if "summary" not in cols:
            self._conn.execute("ALTER TABLE chat_sessions ADD COLUMN summary TEXT DEFAULT ''")

    def _migration_memories_table(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                user_id TEXT,
                content TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'general',
                importance INTEGER NOT NULL DEFAULT 3,
                source_session_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_accessed_at TEXT NOT NULL,
                metadata TEXT DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_memories_user ON memories(user_id);
            CREATE INDEX IF NOT EXISTS idx_memories_category ON memories(category);
            CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at DESC);
        """)

    def _migration_memories_fts(self) -> None:
        self._ensure_memories_fts()

    def _migration_task_job_leases(self) -> None:
        cols = self._column_names("task_jobs")
        if "lease_owner" not in cols:
            self._conn.execute("ALTER TABLE task_jobs ADD COLUMN lease_owner TEXT DEFAULT ''")
        if "lease_expires_at" not in cols:
            self._conn.execute("ALTER TABLE task_jobs ADD COLUMN lease_expires_at TEXT DEFAULT ''")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_task_jobs_lease ON task_jobs(lease_expires_at)")

    def _migration_task_job_recovery_metadata(self) -> None:
        cols = self._column_names("task_jobs")
        if "recovery_count" not in cols:
            self._conn.execute("ALTER TABLE task_jobs ADD COLUMN recovery_count INTEGER NOT NULL DEFAULT 0")
        if "last_recovered_at" not in cols:
            self._conn.execute("ALTER TABLE task_jobs ADD COLUMN last_recovered_at TEXT DEFAULT ''")

    def _migration_task_job_auto_retry_metadata(self) -> None:
        cols = self._column_names("task_jobs")
        if "auto_retry_count" not in cols:
            self._conn.execute("ALTER TABLE task_jobs ADD COLUMN auto_retry_count INTEGER NOT NULL DEFAULT 0")
        if "next_retry_at" not in cols:
            self._conn.execute("ALTER TABLE task_jobs ADD COLUMN next_retry_at TEXT DEFAULT ''")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_task_jobs_next_retry ON task_jobs(next_retry_at)")

    def _migration_worker_logs_table(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS worker_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                worker_id TEXT NOT NULL,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                task_id TEXT DEFAULT '',
                subtask_id TEXT DEFAULT '',
                meta TEXT DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_worker_logs_worker_created ON worker_logs(worker_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_worker_logs_task_created ON worker_logs(task_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_worker_logs_subtask_created ON worker_logs(subtask_id, created_at DESC);
        """)

    def _migration_knowledge_graph_review_decisions(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS knowledge_graph_review_decisions (
                candidate_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                details TEXT DEFAULT '{}',
                username TEXT NOT NULL DEFAULT '',
                user_role TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_kg_review_status_updated
                ON knowledge_graph_review_decisions(status, updated_at DESC);
        """)

    def _migration_knowledge_graph_quality_snapshots(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS knowledge_graph_quality_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                health_score INTEGER NOT NULL DEFAULT 0,
                risk_level TEXT NOT NULL DEFAULT 'low',
                relation_count INTEGER NOT NULL DEFAULT 0,
                entity_count INTEGER NOT NULL DEFAULT 0,
                source_doc_count INTEGER NOT NULL DEFAULT 0,
                source_chunk_count INTEGER NOT NULL DEFAULT 0,
                candidate_count INTEGER NOT NULL DEFAULT 0,
                open_candidate_count INTEGER NOT NULL DEFAULT 0,
                decided_candidate_count INTEGER NOT NULL DEFAULT 0,
                low_confidence_ratio REAL NOT NULL DEFAULT 0.0,
                isolated_relation_ratio REAL NOT NULL DEFAULT 0.0,
                open_review_backlog_ratio REAL NOT NULL DEFAULT 0.0,
                metrics TEXT DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_kg_quality_snapshots_created
                ON knowledge_graph_quality_snapshots(created_at DESC);
        """)

    def schema_version(self) -> int:
        row = self._conn.execute("PRAGMA user_version").fetchone()
        return int(row[0]) if row else 0

    def applied_migrations(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT version, name, applied_at FROM schema_migrations ORDER BY version"
        ).fetchall()
        return [dict(row) for row in rows]

    def _ensure_memories_fts(self) -> None:
        """Keep runtime-created memory schema aligned with Alembic's FTS setup."""
        try:
            self._conn.executescript("""
                CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                    content,
                    content='memories',
                    content_rowid='rowid'
                );

                CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
                    INSERT INTO memories_fts(rowid, content) VALUES (NEW.rowid, NEW.content);
                END;

                CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
                    INSERT INTO memories_fts(memories_fts, rowid, content)
                    VALUES('delete', OLD.rowid, OLD.content);
                END;

                CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
                    INSERT INTO memories_fts(memories_fts, rowid, content)
                    VALUES('delete', OLD.rowid, OLD.content);
                    INSERT INTO memories_fts(rowid, content) VALUES (NEW.rowid, NEW.content);
                END;
            """)
            self._conn.execute("""
                INSERT INTO memories_fts(rowid, content)
                SELECT rowid, content FROM memories
                WHERE rowid NOT IN (SELECT rowid FROM memories_fts)
            """)
        except sqlite3.OperationalError as e:
            logger.warning(f"[PersistenceStore] FTS5 memory index unavailable: {e}")

    def save_session_summary(self, session_id: str, summary: str) -> None:
        """保存会话摘要（记忆压缩）。如果会话不存在，先创建它。"""
        existing = self._conn.execute(
            "SELECT id FROM chat_sessions WHERE id=?", (session_id,)
        ).fetchone()
        if existing:
            self._conn.execute(
                "UPDATE chat_sessions SET summary=? WHERE id=?",
                (summary, session_id),
            )
        else:
            # 会话不存在（只有消息没有会话记录），先创建再更新摘要
            self._conn.execute(
                "INSERT INTO chat_sessions (id, title, created_at, updated_at, summary, archived) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, "", "", "", summary, 0),
            )
        self._conn.commit()

    def get_session_summary(self, session_id: str) -> str:
        """获取会话摘要"""
        row = self._conn.execute(
            "SELECT summary FROM chat_sessions WHERE id=?",
            (session_id,),
        ).fetchone()
        return row["summary"] if row else ""

    def archive_messages(self, session_id: str, before_message_id: int) -> None:
        """将会话中指定消息标记为归档（已包含在摘要中）"""
        self._conn.execute(
            "UPDATE chat_messages SET metadata=json_set(metadata, '$.archived', 1) "
            "WHERE session_id=? AND id <= ?",
            (session_id, before_message_id),
        )
        self._conn.commit()

    def clear_archived_messages(self, session_id: str) -> None:
        """清除会话的所有归档标记（重置为未归档）"""
        self._conn.execute(
            "UPDATE chat_messages SET metadata=json_set(metadata, '$.archived', NULL) "
            "WHERE session_id=?",
            (session_id,),
        )
        self._conn.commit()

    # ==================== 会话 ====================

    def save_session(self, session_id: str, title: str = "") -> None:
        """创建或更新会话。冲突时只刷新 updated_at，避免覆盖用户已设置的标题。"""
        now = datetime.now().isoformat()
        self._conn.execute(
            """INSERT INTO chat_sessions (id, title, created_at, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at""",
            (session_id, title, now, now),
        )
        self._conn.commit()

    def save_message(self, session_id: str, role: str, content: str, metadata: dict | None = None) -> None:
        """保存聊天消息"""
        now = datetime.now().isoformat()
        # 确保 session 存在
        self.save_session(session_id)
        self._conn.execute(
            "INSERT INTO chat_messages (session_id, role, content, created_at, metadata) VALUES (?, ?, ?, ?, ?)",
            (session_id, role, content, now, json.dumps(metadata or {}, ensure_ascii=False)),
        )
        # 更新 session updated_at
        self._conn.execute(
            "UPDATE chat_sessions SET updated_at=? WHERE id=?", (now, session_id)
        )
        self._conn.commit()

    def update_session_title(self, session_id: str, title: str) -> None:
        """自动更新会话标题：仅当当前标题为空时生效，避免覆盖用户手动重命名。"""
        now = datetime.now().isoformat()
        self._conn.execute(
            "UPDATE chat_sessions SET title=?, updated_at=? WHERE id=? AND (title IS NULL OR title='')",
            (title, now, session_id),
        )
        self._conn.commit()

    def get_session_messages(self, session_id: str, include_archived: bool = True) -> list[dict]:
        """获取会话的所有消息

        Args:
            session_id: 会话 ID
            include_archived: 是否包含已归档消息（False 时过滤掉带 $.archived=true 的消息）
        """
        rows = self._conn.execute(
            "SELECT role, content, created_at, metadata FROM chat_messages WHERE session_id=? ORDER BY id",
            (session_id,),
        ).fetchall()
        messages = [
            {
                "role": r["role"],
                "content": r["content"],
                "created_at": r["created_at"],
                "metadata": json.loads(r["metadata"]),
            }
            for r in rows
        ]
        if not include_archived:
            messages = [m for m in messages if not m["metadata"].get("archived", False)]
        return messages

    def list_sessions(self, limit: int = 50, archived: bool | None = False) -> list[dict]:
        """列出最近的会话

        archived:
            - False: 仅返回未归档（默认）
            - True:  仅返回已归档
            - None:  全部
        """
        where = ""
        params: tuple = ()
        if archived is True:
            where = "WHERE s.archived = 1"
        elif archived is False:
            where = "WHERE s.archived = 0"

        rows = self._conn.execute(
            f"""SELECT s.id, s.title, s.created_at, s.updated_at, s.archived,
                       (SELECT COUNT(*) FROM chat_messages WHERE session_id=s.id) as message_count
                FROM chat_sessions s
                {where}
                ORDER BY s.updated_at DESC LIMIT ?""",
            (*params, limit),
        ).fetchall()
        return [
            {**dict(r), "archived": bool(r["archived"])}
            for r in rows
        ]

    def set_session_archived(self, session_id: str, archived: bool) -> bool:
        """归档 / 取消归档"""
        now = datetime.now().isoformat()
        cursor = self._conn.execute(
            "UPDATE chat_sessions SET archived=?, updated_at=? WHERE id=?",
            (1 if archived else 0, now, session_id),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def rename_session(self, session_id: str, title: str) -> bool:
        """重命名会话（update_session_title 的 rowcount 版本）"""
        now = datetime.now().isoformat()
        cursor = self._conn.execute(
            "UPDATE chat_sessions SET title=?, updated_at=? WHERE id=?",
            (title, now, session_id),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def delete_session(self, session_id: str) -> bool:
        """删除会话及其消息"""
        self._conn.execute("DELETE FROM chat_messages WHERE session_id=?", (session_id,))
        cursor = self._conn.execute("DELETE FROM chat_sessions WHERE id=?", (session_id,))
        self._conn.commit()
        return cursor.rowcount > 0

    # ==================== 任务 ====================

    def save_task(self, task_data: dict) -> None:
        """保存任务结果"""
        now = datetime.now().isoformat()
        task_id = task_data.get("task_id", "")
        if not task_id:
            raise ValueError("task_id is required")

        existing = self._conn.execute(
            "SELECT created_at FROM task_history WHERE id=?", (task_id,)
        ).fetchone()
        created_at = task_data.get("created_at") or (existing["created_at"] if existing else now)
        status = task_data.get("status", "completed")
        if "completed_at" in task_data:
            completed_at = task_data.get("completed_at")
        else:
            completed_at = now if status in {"completed", "failed", "cancelled", "timeout"} else None

        self._conn.execute(
            """INSERT INTO task_history (id, description, status, result_summary, final_score,
                                         iterations, mail_log, shared_dir, suggestions, created_at, completed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   description=excluded.description,
                   status=excluded.status, result_summary=excluded.result_summary,
                   final_score=excluded.final_score, iterations=excluded.iterations,
                   mail_log=excluded.mail_log, shared_dir=excluded.shared_dir,
                   suggestions=excluded.suggestions,
                   completed_at=excluded.completed_at""",
            (
                task_id,
                task_data.get("description", ""),
                status,
                task_data.get("result", ""),
                task_data.get("final_score", 0.0),
                json.dumps(task_data.get("iterations", []), ensure_ascii=False),
                task_data.get("mail_log", ""),
                task_data.get("shared_dir", ""),
                json.dumps(task_data.get("suggestions", []), ensure_ascii=False),
                created_at,
                completed_at,
            ),
        )
        self._conn.commit()

    def mark_incomplete_tasks_interrupted(self) -> int:
        """Mark tasks that were active before process restart as interrupted."""
        now = datetime.now().isoformat()
        rows = self._conn.execute(
            """SELECT id FROM task_history
               WHERE status IN ('queued', 'pending', 'running')
                 AND id NOT IN (SELECT id FROM task_jobs)"""
        ).fetchall()
        cursor = self._conn.execute(
            """UPDATE task_history
               SET status='failed',
                   result_summary=CASE
                       WHEN result_summary IS NULL OR result_summary=''
                       THEN '(服务重启前任务未完成，已标记为中断)'
                       ELSE result_summary
                   END,
                   completed_at=?
               WHERE status IN ('queued', 'pending', 'running')
                 AND id NOT IN (SELECT id FROM task_jobs)""",
            (now,),
        )
        for row in rows:
            self.add_task_event(
                row["id"],
                "interrupted",
                "服务重启前任务未完成，且缺少可恢复请求，已标记为中断",
            )
        self._conn.commit()
        return cursor.rowcount

    def get_task(self, task_id: str) -> dict | None:
        """获取任务详情"""
        row = self._conn.execute(
            "SELECT * FROM task_history WHERE id=?", (task_id,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_task(row)

    def list_tasks(self, limit: int = 50) -> list[dict]:
        """列出最近的任务"""
        rows = self._conn.execute(
            "SELECT * FROM task_history ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._row_to_task(r) for r in rows]

    def _row_to_task(self, row: sqlite3.Row) -> dict:
        """将数据库行转为任务字典"""
        return {
            "task_id": row["id"],
            "description": row["description"],
            "status": row["status"],
            "result": row["result_summary"],
            "final_score": row["final_score"],
            "iterations": json.loads(row["iterations"]),
            "mail_log": row["mail_log"],
            "shared_dir": row["shared_dir"],
            "suggestions": json.loads(row["suggestions"]),
            "created_at": row["created_at"],
            "completed_at": row["completed_at"],
        }

    def save_task_job_request(
        self,
        task_id: str,
        description: str,
        context: dict | None = None,
        generate_suggestions: bool = True,
        active_group_ids: list[str] | None = None,
        timeout_seconds: int | None = None,
        created_at: str | None = None,
    ) -> None:
        """Persist enough input data to recover an unfinished background task."""
        now = datetime.now().isoformat()
        created = created_at or now
        self._conn.execute(
            """INSERT INTO task_jobs
               (id, description, context, generate_suggestions, active_group_ids,
                timeout_seconds, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   description=excluded.description,
                   context=excluded.context,
                   generate_suggestions=excluded.generate_suggestions,
                   active_group_ids=excluded.active_group_ids,
                   timeout_seconds=excluded.timeout_seconds,
                   updated_at=excluded.updated_at""",
            (
                task_id,
                description,
                json.dumps(context or {}, ensure_ascii=False),
                1 if generate_suggestions else 0,
                json.dumps(active_group_ids, ensure_ascii=False),
                timeout_seconds,
                created,
                now,
            ),
        )
        self._conn.commit()

    def acquire_task_job_lease(self, task_id: str, owner_id: str, lease_seconds: float) -> bool:
        """Atomically claim a recoverable task job for one runner process."""
        now = datetime.now()
        now_iso = now.isoformat()
        expires_at = (now + timedelta(seconds=lease_seconds)).isoformat()
        cursor = self._conn.execute(
            """UPDATE task_jobs
               SET lease_owner=?,
                   lease_expires_at=?,
                   updated_at=?
               WHERE id=?
                 AND (
                    lease_owner IS NULL OR lease_owner='' OR lease_owner=?
                    OR lease_expires_at IS NULL OR lease_expires_at='' OR lease_expires_at<=?
                 )""",
            (owner_id, expires_at, now_iso, task_id, owner_id, now_iso),
        )
        self._conn.commit()
        return cursor.rowcount == 1

    def refresh_task_job_lease(self, task_id: str, owner_id: str, lease_seconds: float) -> bool:
        now = datetime.now()
        expires_at = (now + timedelta(seconds=lease_seconds)).isoformat()
        cursor = self._conn.execute(
            """UPDATE task_jobs
               SET lease_expires_at=?,
                   updated_at=?
               WHERE id=?
                 AND lease_owner=?""",
            (expires_at, now.isoformat(), task_id, owner_id),
        )
        self._conn.commit()
        return cursor.rowcount == 1

    def release_task_job_lease(self, task_id: str, owner_id: str) -> bool:
        cursor = self._conn.execute(
            """UPDATE task_jobs
               SET lease_owner='',
                   lease_expires_at='',
                   updated_at=?
               WHERE id=?
                 AND lease_owner=?""",
            (datetime.now().isoformat(), task_id, owner_id),
        )
        self._conn.commit()
        return cursor.rowcount == 1

    def schedule_task_job_auto_retry(
        self,
        task_id: str,
        next_retry_at: str,
        max_attempts: int,
    ) -> dict | None:
        """Record the next automatic retry if the job still has retry budget."""
        if max_attempts < 1:
            return None
        now = datetime.now().isoformat()
        cursor = self._conn.execute(
            """UPDATE task_jobs
               SET auto_retry_count=COALESCE(auto_retry_count, 0) + 1,
                   next_retry_at=?,
                   updated_at=?
               WHERE id=?
                 AND COALESCE(auto_retry_count, 0) < ?""",
            (next_retry_at, now, task_id, max_attempts),
        )
        self._conn.commit()
        return self.get_task_job_request(task_id) if cursor.rowcount == 1 else None

    def clear_task_job_auto_retry(self, task_id: str) -> bool:
        cursor = self._conn.execute(
            """UPDATE task_jobs
               SET next_retry_at='',
                   updated_at=?
               WHERE id=?""",
            (datetime.now().isoformat(), task_id),
        )
        self._conn.commit()
        return cursor.rowcount == 1

    def list_scheduled_task_job_retries(self, limit: int = 100) -> list[dict]:
        rows = self._conn.execute(
            """SELECT j.* FROM task_jobs j
               JOIN task_history h ON h.id = j.id
               WHERE h.status IN ('failed', 'timeout')
                 AND j.next_retry_at IS NOT NULL
                 AND j.next_retry_at != ''
               ORDER BY j.next_retry_at ASC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [self._row_to_task_job(row) for row in rows]

    def mark_task_job_recovered(self, task_id: str) -> dict | None:
        """Record that a persisted job was picked up by startup recovery."""
        now = datetime.now().isoformat()
        self._conn.execute(
            """UPDATE task_jobs
               SET recovery_count=COALESCE(recovery_count, 0) + 1,
                   last_recovered_at=?,
                   updated_at=?
               WHERE id=?""",
            (now, now, task_id),
        )
        self._conn.commit()
        return self.get_task_job_request(task_id)

    def get_task_job_request(self, task_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM task_jobs WHERE id=?", (task_id,)
        ).fetchone()
        return self._row_to_task_job(row) if row else None

    def list_recoverable_task_jobs(self, limit: int = 100) -> list[dict]:
        rows = self._conn.execute(
            """SELECT j.* FROM task_jobs j
               JOIN task_history h ON h.id = j.id
               WHERE h.status IN ('queued', 'pending', 'running')
               ORDER BY j.created_at ASC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [self._row_to_task_job(row) for row in rows]

    def get_task_job_stats(self) -> dict:
        now_dt = datetime.now()
        now = now_dt.isoformat()
        terminal_statuses = ("completed", "failed", "cancelled", "timeout")
        active_statuses = ("queued", "pending", "running")
        total = self._conn.execute("SELECT COUNT(*) AS count FROM task_jobs").fetchone()["count"]
        status_counts = {
            row["status"]: row["count"]
            for row in self._conn.execute(
                """SELECT h.status, COUNT(*) AS count FROM task_jobs j
                   JOIN task_history h ON h.id=j.id
                   GROUP BY h.status"""
            ).fetchall()
        }
        active = self._conn.execute(
            """SELECT COUNT(*) AS count FROM task_jobs j
               JOIN task_history h ON h.id=j.id
               WHERE h.status IN (?, ?, ?)""",
            active_statuses,
        ).fetchone()["count"]
        terminal = self._conn.execute(
            """SELECT COUNT(*) AS count FROM task_jobs j
               JOIN task_history h ON h.id=j.id
               WHERE h.status IN (?, ?, ?, ?)""",
            terminal_statuses,
        ).fetchone()["count"]
        leased_active = self._conn.execute(
            """SELECT COUNT(*) AS count FROM task_jobs
               WHERE lease_owner IS NOT NULL AND lease_owner!=''
                 AND lease_expires_at IS NOT NULL AND lease_expires_at!=''
                 AND lease_expires_at>?""",
            (now,),
        ).fetchone()["count"]
        expired_leases = self._conn.execute(
            """SELECT COUNT(*) AS count FROM task_jobs
               WHERE lease_owner IS NOT NULL AND lease_owner!=''
                 AND lease_expires_at IS NOT NULL AND lease_expires_at!=''
                 AND lease_expires_at<=?""",
            (now,),
        ).fetchone()["count"]
        scheduled_retries = self._conn.execute(
            """SELECT COUNT(*) AS count FROM task_jobs
               WHERE next_retry_at IS NOT NULL AND next_retry_at!=''"""
        ).fetchone()["count"]
        recovered_jobs = self._conn.execute(
            "SELECT COUNT(*) AS count FROM task_jobs WHERE recovery_count>0"
        ).fetchone()["count"]
        recovery_count_total = self._conn.execute(
            "SELECT COALESCE(SUM(recovery_count), 0) AS count FROM task_jobs"
        ).fetchone()["count"]
        oldest_active_row = self._conn.execute(
            """SELECT MIN(h.created_at) AS created_at FROM task_jobs j
               JOIN task_history h ON h.id=j.id
               WHERE h.status IN (?, ?, ?)""",
            active_statuses,
        ).fetchone()
        oldest_active_created_at = oldest_active_row["created_at"] if oldest_active_row else ""
        last_updated_row = self._conn.execute(
            "SELECT MAX(updated_at) AS updated_at FROM task_jobs"
        ).fetchone()
        last_job_updated_at = last_updated_row["updated_at"] if last_updated_row else ""

        def _age_seconds(value: str | None) -> int | None:
            if not value:
                return None
            try:
                return max(0, int((now_dt - datetime.fromisoformat(value)).total_seconds()))
            except ValueError:
                return None

        failure_rows = self._conn.execute(
            """SELECT h.status, j.next_retry_at, e.event_type, e.details
               FROM task_jobs j
               JOIN task_history h ON h.id=j.id
               LEFT JOIN task_events e ON e.id = (
                   SELECT te.id FROM task_events te
                   WHERE te.task_id=j.id
                     AND (
                       te.event_type IN (
                         'failed_retryable',
                         'failed_non_retryable',
                         'timeout',
                         'lease_lost',
                         'lease_lost_stopped',
                         'auto_retry_exhausted'
                       )
                       OR te.details LIKE '%failure_type%'
                     )
                   ORDER BY te.id DESC
                   LIMIT 1
               )
               WHERE h.status IN ('failed', 'timeout')"""
        ).fetchall()
        retryable_failures = 0
        non_retryable_failures = 0
        auto_retry_exhausted = 0
        manual_retryable = 0
        needs_intervention = 0
        retryable_failure_types = {"timeout", "lease_lost", "retryable_exception"}
        for row in failure_rows:
            try:
                details = json.loads(row["details"] or "{}")
            except json.JSONDecodeError:
                details = {}
            failure_type = details.get("failure_type")
            if not failure_type and row["status"] == "timeout":
                failure_type = "timeout"
            retryable = bool(
                details.get("retryable")
                or failure_type in retryable_failure_types
            )
            exhausted = row["event_type"] == "auto_retry_exhausted"
            if exhausted:
                auto_retry_exhausted += 1
            if retryable:
                retryable_failures += 1
            else:
                non_retryable_failures += 1
            if retryable and not row["next_retry_at"]:
                manual_retryable += 1
            if exhausted or not retryable:
                needs_intervention += 1

        return {
            "total": total,
            "active": active,
            "terminal": terminal,
            "queued": status_counts.get("queued", 0),
            "pending": status_counts.get("pending", 0),
            "running": status_counts.get("running", 0),
            "completed": status_counts.get("completed", 0),
            "failed": status_counts.get("failed", 0),
            "cancelled": status_counts.get("cancelled", 0),
            "timeout": status_counts.get("timeout", 0),
            "leased_active": leased_active,
            "expired_leases": expired_leases,
            "scheduled_retries": scheduled_retries,
            "retryable_failures": retryable_failures,
            "non_retryable_failures": non_retryable_failures,
            "auto_retry_exhausted": auto_retry_exhausted,
            "manual_retryable": manual_retryable,
            "needs_intervention": needs_intervention,
            "recovered_jobs": recovered_jobs,
            "recovery_count_total": recovery_count_total,
            "oldest_active_created_at": oldest_active_created_at or "",
            "oldest_active_age_seconds": _age_seconds(oldest_active_created_at),
            "last_job_updated_at": last_job_updated_at or "",
        }

    def count_terminal_task_jobs_before(self, cutoff_iso: str | None) -> int:
        if not cutoff_iso:
            return 0
        row = self._conn.execute(
            """SELECT COUNT(*) AS count FROM task_jobs j
               JOIN task_history h ON h.id=j.id
               WHERE h.status IN ('completed', 'failed', 'cancelled', 'timeout')
                 AND COALESCE(h.completed_at, j.updated_at, j.created_at) < ?""",
            (cutoff_iso,),
        ).fetchone()
        return int(row["count"]) if row else 0

    def delete_terminal_task_jobs_before(self, cutoff_iso: str | None) -> int:
        if not cutoff_iso:
            return 0
        cursor = self._conn.execute(
            """DELETE FROM task_jobs
               WHERE id IN (
                   SELECT j.id FROM task_jobs j
                   JOIN task_history h ON h.id=j.id
                   WHERE h.status IN ('completed', 'failed', 'cancelled', 'timeout')
                     AND COALESCE(h.completed_at, j.updated_at, j.created_at) < ?
               )""",
            (cutoff_iso,),
        )
        self._conn.commit()
        return cursor.rowcount

    def _row_to_task_job(self, row: sqlite3.Row) -> dict:
        return {
            "task_id": row["id"],
            "description": row["description"],
            "context": json.loads(row["context"] or "{}"),
            "generate_suggestions": bool(row["generate_suggestions"]),
            "active_group_ids": json.loads(row["active_group_ids"] or "null"),
            "timeout_seconds": row["timeout_seconds"],
            "lease_owner": row["lease_owner"],
            "lease_expires_at": row["lease_expires_at"],
            "recovery_count": row["recovery_count"],
            "last_recovered_at": row["last_recovered_at"],
            "auto_retry_count": row["auto_retry_count"],
            "next_retry_at": row["next_retry_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def add_task_event(
        self,
        task_id: str,
        event_type: str,
        message: str = "",
        details: dict | None = None,
        created_at: str | None = None,
    ) -> None:
        self._conn.execute(
            """INSERT INTO task_events (task_id, event_type, message, details, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (
                task_id,
                event_type,
                message,
                json.dumps(details or {}, ensure_ascii=False),
                created_at or datetime.now().isoformat(),
            ),
        )
        self._conn.commit()

    def list_task_events(self, task_id: str, limit: int = 200) -> list[dict]:
        rows = self._conn.execute(
            """SELECT id, task_id, event_type, message, details, created_at
               FROM task_events
               WHERE task_id=?
               ORDER BY id ASC
               LIMIT ?""",
            (task_id, limit),
        ).fetchall()
        return [
            {
                "id": row["id"],
                "task_id": row["task_id"],
                "event_type": row["event_type"],
                "message": row["message"],
                "details": json.loads(row["details"] or "{}"),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def save_task_checkpoint(self, task_id: str, checkpoint: dict) -> None:
        now = datetime.now().isoformat()
        self._conn.execute(
            """INSERT INTO task_checkpoints (task_id, checkpoint, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(task_id) DO UPDATE SET
                   checkpoint=excluded.checkpoint,
                   updated_at=excluded.updated_at""",
            (task_id, json.dumps(checkpoint, ensure_ascii=False), now),
        )
        self._conn.commit()

    def get_task_checkpoint(self, task_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT checkpoint, updated_at FROM task_checkpoints WHERE task_id=?",
            (task_id,),
        ).fetchone()
        if not row:
            return None
        checkpoint = json.loads(row["checkpoint"] or "{}")
        checkpoint["updated_at"] = row["updated_at"]
        return checkpoint

    # ==================== 定时任务 ====================

    def create_scheduled_task(
        self,
        task_id: str,
        description: str,
        cron: str,
        active_group_ids: list[str] | None = None,
        source_session_id: str = "",
        next_run_at: str = "",
        enabled: bool = True,
    ) -> None:
        now = datetime.now().isoformat()
        self._conn.execute(
            """INSERT INTO scheduled_tasks
               (id, description, cron, enabled, active_group_ids, source_session_id,
                created_at, updated_at, last_run_at, next_run_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', ?)""",
            (
                task_id,
                description,
                cron,
                1 if enabled else 0,
                json.dumps(active_group_ids or [], ensure_ascii=False),
                source_session_id,
                now,
                now,
                next_run_at,
            ),
        )
        self._conn.commit()

    def list_scheduled_tasks(self, enabled_only: bool = False) -> list[dict]:
        where = "WHERE enabled = 1" if enabled_only else ""
        rows = self._conn.execute(
            f"SELECT * FROM scheduled_tasks {where} ORDER BY created_at DESC"
        ).fetchall()
        return [self._row_to_scheduled(r) for r in rows]

    def get_scheduled_task(self, task_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM scheduled_tasks WHERE id=?", (task_id,)
        ).fetchone()
        return self._row_to_scheduled(row) if row else None

    def update_scheduled_task(
        self,
        task_id: str,
        description: str | None = None,
        cron: str | None = None,
        enabled: bool | None = None,
        active_group_ids: list[str] | None = None,
        next_run_at: str | None = None,
    ) -> bool:
        fields = []
        params: list = []
        if description is not None:
            fields.append("description=?")
            params.append(description)
        if cron is not None:
            fields.append("cron=?")
            params.append(cron)
        if enabled is not None:
            fields.append("enabled=?")
            params.append(1 if enabled else 0)
        if active_group_ids is not None:
            fields.append("active_group_ids=?")
            params.append(json.dumps(active_group_ids, ensure_ascii=False))
        if next_run_at is not None:
            fields.append("next_run_at=?")
            params.append(next_run_at)
        if not fields:
            return False
        fields.append("updated_at=?")
        params.append(datetime.now().isoformat())
        params.append(task_id)
        cursor = self._conn.execute(
            f"UPDATE scheduled_tasks SET {', '.join(fields)} WHERE id=?",
            tuple(params),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def delete_scheduled_task(self, task_id: str) -> bool:
        cursor = self._conn.execute("DELETE FROM scheduled_tasks WHERE id=?", (task_id,))
        self._conn.commit()
        return cursor.rowcount > 0

    def mark_scheduled_task_run(self, task_id: str, when_iso: str) -> None:
        self._conn.execute(
            "UPDATE scheduled_tasks SET last_run_at=? WHERE id=?", (when_iso, task_id)
        )
        self._conn.commit()

    def set_scheduled_task_next_run(self, task_id: str, when_iso: str | None) -> None:
        self._conn.execute(
            "UPDATE scheduled_tasks SET next_run_at=? WHERE id=?", (when_iso or "", task_id)
        )
        self._conn.commit()

    def _row_to_scheduled(self, row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "description": row["description"],
            "cron": row["cron"],
            "enabled": bool(row["enabled"]),
            "active_group_ids": row["active_group_ids"] or "[]",
            "source_session_id": row["source_session_id"] or "",
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "last_run_at": row["last_run_at"] or "",
            "next_run_at": row["next_run_at"] or "",
        }

    # ==================== Worker Token 用量 ====================

    def get_worker_token_usage(self, worker_id: str) -> dict:
        """获取 Worker 的 token 用量"""
        row = self._conn.execute(
            "SELECT input_tokens, output_tokens, call_count FROM worker_token_usage WHERE worker_id = ?",
            (worker_id,),
        ).fetchone()
        if row:
            return {"input_tokens": row[0], "output_tokens": row[1], "call_count": row[2]}
        return {"input_tokens": 0, "output_tokens": 0, "call_count": 0}

    def get_all_worker_token_usage(self) -> dict[str, dict]:
        """获取所有 Worker 的 token 用量"""
        rows = self._conn.execute(
            "SELECT worker_id, input_tokens, output_tokens, call_count FROM worker_token_usage"
        ).fetchall()
        return {
            row[0]: {"input_tokens": row[1], "output_tokens": row[2], "call_count": row[3]}
            for row in rows
        }

    def increment_worker_token_usage(self, worker_id: str, input_tokens: int, output_tokens: int) -> None:
        """累加 Worker 的 token 用量"""
        now = datetime.now().isoformat()
        self._conn.execute(
            """INSERT INTO worker_token_usage (worker_id, input_tokens, output_tokens, call_count, updated_at)
               VALUES (?, ?, ?, 1, ?)
               ON CONFLICT(worker_id) DO UPDATE SET
                   input_tokens = input_tokens + excluded.input_tokens,
                   output_tokens = output_tokens + excluded.output_tokens,
                   call_count = call_count + 1,
                   updated_at = excluded.updated_at""",
            (worker_id, input_tokens, output_tokens, now),
        )
        self._conn.commit()

    def reset_worker_token_usage(self, worker_id: str) -> None:
        """重置 Worker 的 token 用量"""
        self._conn.execute("DELETE FROM worker_token_usage WHERE worker_id = ?", (worker_id,))
        self._conn.commit()

    # ==================== Worker 日志 ====================

    @staticmethod
    def _worker_log_row_to_dict(row: sqlite3.Row) -> dict:
        try:
            meta = json.loads(row["meta"] or "{}")
        except json.JSONDecodeError:
            meta = {}
        return {
            "id": row["id"],
            "worker_id": row["worker_id"],
            "level": row["level"],
            "message": row["message"],
            "task_id": row["task_id"] or "",
            "subtask_id": row["subtask_id"] or "",
            "meta": meta,
            "created_at": row["created_at"],
        }

    def add_worker_log(
        self,
        worker_id: str,
        level: str,
        message: str,
        meta: dict | None = None,
        created_at: str | None = None,
    ) -> dict:
        """持久化一条 Worker 日志，便于任务 trace 历史排障。"""
        meta = meta or {}
        task_id = str(meta.get("task_id") or "")
        subtask_id = str(meta.get("subtask_id") or "")
        cursor = self._conn.execute(
            """INSERT INTO worker_logs
               (worker_id, level, message, task_id, subtask_id, meta, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                worker_id,
                level,
                message,
                task_id,
                subtask_id,
                json.dumps(meta, ensure_ascii=False),
                created_at or datetime.now().isoformat(),
            ),
        )
        self._conn.commit()
        row = self._conn.execute("SELECT * FROM worker_logs WHERE id=?", (cursor.lastrowid,)).fetchone()
        return self._worker_log_row_to_dict(row)

    def list_worker_logs(
        self,
        worker_id: str | None = None,
        task_id: str | None = None,
        subtask_id: str | None = None,
        level: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        """分页查询 Worker 日志。"""
        query = "SELECT * FROM worker_logs WHERE 1=1"
        params: list = []
        if worker_id:
            query += " AND worker_id=?"
            params.append(worker_id)
        if task_id:
            query += " AND task_id=?"
            params.append(task_id)
        if subtask_id:
            query += " AND subtask_id=?"
            params.append(subtask_id)
        if level:
            query += " AND level=?"
            params.append(level)
        query += " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = self._conn.execute(query, params).fetchall()
        return [self._worker_log_row_to_dict(row) for row in rows]

    def delete_worker_logs(self, worker_id: str | None = None) -> int:
        """删除 Worker 日志。未指定 worker_id 时删除全部。"""
        if worker_id:
            cursor = self._conn.execute("DELETE FROM worker_logs WHERE worker_id=?", (worker_id,))
        else:
            cursor = self._conn.execute("DELETE FROM worker_logs")
        self._conn.commit()
        return cursor.rowcount

    # ── 审计日志 ──────────────────────────────────────────────

    def log_audit_event(
        self,
        action: str,
        resource: str,
        resource_id: str = "",
        username: str = "",
        user_role: str = "",
        details: dict | None = None,
        ip_address: str = "",
    ) -> None:
        """记录一条审计日志"""
        now = datetime.now().isoformat()
        self._conn.execute(
            """INSERT INTO audit_log
               (timestamp, username, user_role, action, resource, resource_id, details, ip_address)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (now, username, user_role, action, resource, resource_id,
             json.dumps(details or {}, ensure_ascii=False), ip_address),
        )
        self._conn.commit()

    def list_audit_events(
        self,
        resource: str | None = None,
        action: str | None = None,
        username: str | None = None,
        resource_id: str | None = None,
        status: str | None = None,
        worker_id: str | None = None,
        task_id: str | None = None,
        timestamp_from: str | None = None,
        timestamp_to: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        """分页查询审计日志"""
        query = "SELECT * FROM audit_log WHERE 1=1"
        params: list = []
        if resource:
            query += " AND resource=?"
            params.append(resource)
        if action:
            query += " AND action=?"
            params.append(action)
        if username:
            query += " AND username=?"
            params.append(username)
        if resource_id:
            query += " AND resource_id=?"
            params.append(resource_id)
        if status:
            query += " AND json_extract(details, '$.status')=?"
            params.append(status)
        if worker_id:
            query += " AND json_extract(details, '$.worker_id')=?"
            params.append(worker_id)
        if task_id:
            query += " AND json_extract(details, '$.task_id')=?"
            params.append(task_id)
        if timestamp_from:
            query += " AND timestamp>=?"
            params.append(timestamp_from)
        if timestamp_to:
            query += " AND timestamp<=?"
            params.append(timestamp_to)
        query += " ORDER BY timestamp DESC, id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = self._conn.execute(query, params).fetchall()
        return [self._audit_event_row_to_dict(r) for r in rows]

    def count_audit_events(
        self,
        resource: str | None = None,
        action: str | None = None,
        username: str | None = None,
        resource_id: str | None = None,
        status: str | None = None,
        worker_id: str | None = None,
        task_id: str | None = None,
        timestamp_from: str | None = None,
        timestamp_to: str | None = None,
    ) -> int:
        """统计审计日志数量。"""
        query = "SELECT COUNT(*) FROM audit_log WHERE 1=1"
        params: list = []
        if resource:
            query += " AND resource=?"
            params.append(resource)
        if action:
            query += " AND action=?"
            params.append(action)
        if username:
            query += " AND username=?"
            params.append(username)
        if resource_id:
            query += " AND resource_id=?"
            params.append(resource_id)
        if status:
            query += " AND json_extract(details, '$.status')=?"
            params.append(status)
        if worker_id:
            query += " AND json_extract(details, '$.worker_id')=?"
            params.append(worker_id)
        if task_id:
            query += " AND json_extract(details, '$.task_id')=?"
            params.append(task_id)
        if timestamp_from:
            query += " AND timestamp>=?"
            params.append(timestamp_from)
        if timestamp_to:
            query += " AND timestamp<=?"
            params.append(timestamp_to)
        row = self._conn.execute(query, params).fetchone()
        return int(row[0]) if row else 0

    @staticmethod
    def _audit_event_row_to_dict(row: sqlite3.Row) -> dict:
        event = dict(row)
        try:
            event["details"] = json.loads(event["details"]) if event.get("details") else {}
        except json.JSONDecodeError:
            event["details"] = {}
        return event

    def count_audit_events_before(self, cutoff_iso: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE timestamp < ?",
            (cutoff_iso,),
        ).fetchone()
        return int(row[0]) if row else 0

    def delete_audit_events_before(self, cutoff_iso: str) -> int:
        cursor = self._conn.execute("DELETE FROM audit_log WHERE timestamp < ?", (cutoff_iso,))
        self._conn.commit()
        return cursor.rowcount

    # ── 运维事件 ──────────────────────────────────────────────

    @staticmethod
    def _ops_event_row_to_dict(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "event_type": row["event_type"],
            "status": row["status"],
            "action": row["action"],
            "message": row["message"],
            "details": json.loads(row["details"]) if row["details"] else {},
            "created_at": row["created_at"],
        }

    def record_ops_event(
        self,
        event_type: str,
        status: str,
        action: str = "",
        message: str = "",
        details: dict | None = None,
    ) -> dict:
        """记录后台运维事件，并返回新记录。"""
        now = datetime.now().isoformat()
        cursor = self._conn.execute(
            """INSERT INTO ops_events
               (event_type, status, action, message, details, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (event_type, status, action, message, json.dumps(details or {}, ensure_ascii=False), now),
        )
        self._conn.commit()
        row = self._conn.execute("SELECT * FROM ops_events WHERE id=?", (cursor.lastrowid,)).fetchone()
        return self._ops_event_row_to_dict(row)

    def _ops_event_where_clause(
        self,
        *,
        event_type: str | None = None,
        status: str | None = None,
    ) -> tuple[str, list]:
        filters = []
        params: list = []
        if event_type:
            filters.append("event_type=?")
            params.append(event_type)
        if status:
            filters.append("status=?")
            params.append(status)
        if not filters:
            return "", params
        return " WHERE " + " AND ".join(filters), params

    def count_ops_events(self, event_type: str | None = None, status: str | None = None) -> int:
        where, params = self._ops_event_where_clause(event_type=event_type, status=status)
        row = self._conn.execute(f"SELECT COUNT(*) FROM ops_events{where}", params).fetchone()
        return int(row[0]) if row else 0

    def list_ops_events(
        self,
        event_type: str | None = None,
        limit: int = 20,
        offset: int = 0,
        status: str | None = None,
    ) -> list[dict]:
        query = "SELECT * FROM ops_events"
        where, params = self._ops_event_where_clause(event_type=event_type, status=status)
        query += where
        query += " ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(limit)
        if offset > 0:
            query += " OFFSET ?"
            params.append(offset)
        rows = self._conn.execute(query, params).fetchall()
        return [self._ops_event_row_to_dict(row) for row in rows]

    def get_latest_ops_event(self, event_type: str | None = None) -> dict | None:
        events = self.list_ops_events(event_type=event_type, limit=1)
        return events[0] if events else None

    def count_ops_events_before(self, cutoff_iso: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM ops_events WHERE created_at < ?",
            (cutoff_iso,),
        ).fetchone()
        return int(row[0]) if row else 0

    def delete_ops_events_before(self, cutoff_iso: str) -> int:
        cursor = self._conn.execute("DELETE FROM ops_events WHERE created_at < ?", (cutoff_iso,))
        self._conn.commit()
        return cursor.rowcount

    # ── 知识图谱审核决策 ───────────────────────────────────────

    @staticmethod
    def _kg_review_decision_row_to_dict(row: sqlite3.Row) -> dict:
        return {
            "candidate_id": row["candidate_id"],
            "status": row["status"],
            "note": row["note"],
            "details": json.loads(row["details"]) if row["details"] else {},
            "username": row["username"],
            "user_role": row["user_role"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def set_knowledge_graph_review_decision(
        self,
        candidate_id: str,
        status: str,
        *,
        note: str = "",
        details: dict | None = None,
        username: str = "",
        user_role: str = "",
    ) -> dict:
        """Create or update a knowledge-graph review decision."""
        candidate_id = str(candidate_id or "").strip()
        status = str(status or "").strip()
        if not candidate_id:
            raise ValueError("candidate_id is required")
        if status not in {"accepted", "ignored", "snoozed", "open"}:
            raise ValueError("invalid review decision status")

        now = datetime.now().isoformat()
        existing = self._conn.execute(
            "SELECT created_at FROM knowledge_graph_review_decisions WHERE candidate_id=?",
            (candidate_id,),
        ).fetchone()
        created_at = existing["created_at"] if existing else now
        self._conn.execute(
            """INSERT INTO knowledge_graph_review_decisions
               (candidate_id, status, note, details, username, user_role, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(candidate_id) DO UPDATE SET
                   status=excluded.status,
                   note=excluded.note,
                   details=excluded.details,
                   username=excluded.username,
                   user_role=excluded.user_role,
                   updated_at=excluded.updated_at""",
            (
                candidate_id,
                status,
                note,
                json.dumps(details or {}, ensure_ascii=False),
                username,
                user_role,
                created_at,
                now,
            ),
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT * FROM knowledge_graph_review_decisions WHERE candidate_id=?",
            (candidate_id,),
        ).fetchone()
        return self._kg_review_decision_row_to_dict(row)

    def get_knowledge_graph_review_decision(self, candidate_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM knowledge_graph_review_decisions WHERE candidate_id=?",
            (candidate_id,),
        ).fetchone()
        return self._kg_review_decision_row_to_dict(row) if row else None

    def list_knowledge_graph_review_decisions(self, status: str | None = None) -> list[dict]:
        query = "SELECT * FROM knowledge_graph_review_decisions"
        params: list = []
        if status:
            query += " WHERE status=?"
            params.append(status)
        query += " ORDER BY updated_at DESC"
        rows = self._conn.execute(query, params).fetchall()
        return [self._kg_review_decision_row_to_dict(row) for row in rows]

    def delete_knowledge_graph_review_decision(self, candidate_id: str) -> bool:
        cursor = self._conn.execute(
            "DELETE FROM knowledge_graph_review_decisions WHERE candidate_id=?",
            (candidate_id,),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    # ── 知识图谱质量指标快照 ───────────────────────────────────

    @staticmethod
    def _kg_quality_snapshot_row_to_dict(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "health_score": row["health_score"],
            "risk_level": row["risk_level"],
            "relation_count": row["relation_count"],
            "entity_count": row["entity_count"],
            "source_doc_count": row["source_doc_count"],
            "source_chunk_count": row["source_chunk_count"],
            "candidate_count": row["candidate_count"],
            "open_candidate_count": row["open_candidate_count"],
            "decided_candidate_count": row["decided_candidate_count"],
            "low_confidence_ratio": row["low_confidence_ratio"],
            "isolated_relation_ratio": row["isolated_relation_ratio"],
            "open_review_backlog_ratio": row["open_review_backlog_ratio"],
            "metrics": json.loads(row["metrics"]) if row["metrics"] else {},
            "created_at": row["created_at"],
        }

    def save_knowledge_graph_quality_snapshot(self, metrics: dict, *, created_at: str | None = None) -> dict:
        """Persist one knowledge-graph quality metrics snapshot."""
        metrics = dict(metrics or {})
        created_at = created_at or datetime.now().isoformat()
        cursor = self._conn.execute(
            """INSERT INTO knowledge_graph_quality_snapshots
               (health_score, risk_level, relation_count, entity_count, source_doc_count,
                source_chunk_count, candidate_count, open_candidate_count, decided_candidate_count,
                low_confidence_ratio, isolated_relation_ratio, open_review_backlog_ratio,
                metrics, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                int(metrics.get("health_score") or 0),
                str(metrics.get("risk_level") or "low"),
                int(metrics.get("relation_count") or 0),
                int(metrics.get("entity_count") or 0),
                int(metrics.get("source_doc_count") or 0),
                int(metrics.get("source_chunk_count") or 0),
                int(metrics.get("candidate_count") or 0),
                int(metrics.get("open_candidate_count") or 0),
                int(metrics.get("decided_candidate_count") or 0),
                float(metrics.get("low_confidence_ratio") or 0.0),
                float(metrics.get("isolated_relation_ratio") or 0.0),
                float(metrics.get("open_review_backlog_ratio") or 0.0),
                json.dumps(metrics, ensure_ascii=False),
                created_at,
            ),
        )
        self._conn.execute(
            """DELETE FROM knowledge_graph_quality_snapshots
               WHERE id NOT IN (
                   SELECT id FROM knowledge_graph_quality_snapshots
                   ORDER BY created_at DESC, id DESC
                   LIMIT 500
               )"""
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT * FROM knowledge_graph_quality_snapshots WHERE id=?",
            (cursor.lastrowid,),
        ).fetchone()
        return self._kg_quality_snapshot_row_to_dict(row)

    def list_knowledge_graph_quality_snapshots(self, limit: int = 30) -> list[dict]:
        safe_limit = max(1, min(int(limit), 500))
        rows = self._conn.execute(
            """SELECT * FROM knowledge_graph_quality_snapshots
               ORDER BY created_at DESC, id DESC
               LIMIT ?""",
            (safe_limit,),
        ).fetchall()
        snapshots = [self._kg_quality_snapshot_row_to_dict(row) for row in rows]
        return list(reversed(snapshots))

    # ==================== 跨会话记忆（memories）====================

    @staticmethod
    def _memory_row_to_dict(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "user_id": row["user_id"],
            "content": row["content"],
            "category": row["category"],
            "importance": row["importance"],
            "source_session_id": row["source_session_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "last_accessed_at": row["last_accessed_at"],
            "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
        }

    def save_memory(
        self,
        memory_id: str,
        content: str,
        user_id: str | None = None,
        category: str = "general",
        importance: int = 3,
        source_session_id: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        """保存或更新一条跨会话记忆"""
        now = datetime.now().isoformat()
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
        self._conn.execute(
            """INSERT INTO memories
               (id, user_id, content, category, importance, source_session_id,
                created_at, updated_at, last_accessed_at, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   user_id=excluded.user_id,
                   content=excluded.content,
                   category=excluded.category,
                   importance=excluded.importance,
                   source_session_id=excluded.source_session_id,
                   updated_at=excluded.updated_at,
                   last_accessed_at=excluded.last_accessed_at,
                   metadata=excluded.metadata""",
            (memory_id, user_id, content, category, importance, source_session_id,
             now, now, now, metadata_json),
        )
        self._conn.commit()

    def get_memory(self, memory_id: str) -> dict | None:
        """获取单条记忆，同时更新 last_accessed_at"""
        row = self._conn.execute(
            "SELECT * FROM memories WHERE id=?", (memory_id,)
        ).fetchone()
        if not row:
            return None
        self._conn.execute(
            "UPDATE memories SET last_accessed_at=? WHERE id=?",
            (datetime.now().isoformat(), memory_id),
        )
        self._conn.commit()
        return self._memory_row_to_dict(row)

    def update_memory(self, memory_id: str, updates: dict) -> bool:
        """更新记忆字段（content, category, importance, metadata）"""
        allowed = {"content", "category", "importance", "metadata"}
        set_clauses, params = [], []
        for key in allowed:
            if key in updates:
                val = json.dumps(updates[key]) if key == "metadata" else updates[key]
                set_clauses.append(f"{key}=?")
                params.append(val)
        if not set_clauses:
            return False
        set_clauses.append("updated_at=?")
        params.append(datetime.now().isoformat())
        params.append(memory_id)
        cursor = self._conn.execute(
            f"UPDATE memories SET {', '.join(set_clauses)} WHERE id=?",
            params,
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def delete_memory(self, memory_id: str) -> bool:
        """删除一条记忆"""
        cursor = self._conn.execute("DELETE FROM memories WHERE id=?", (memory_id,))
        self._conn.commit()
        return cursor.rowcount > 0

    def search_memories(
        self,
        query: str,
        user_id: str | None = None,
        category: str | None = None,
        limit: int = 10,
    ) -> list[dict]:
        """基于关键词的模糊记忆搜索（LIKE + relevance sort）"""
        sql = "SELECT * FROM memories WHERE content LIKE ?"
        params: list = [f"%{query}%"]
        if user_id is not None:
            sql += " AND (user_id IS NULL OR user_id=?)"
            params.append(user_id)
        if category:
            sql += " AND category=?"
            params.append(category)
        sql += " ORDER BY importance DESC, last_accessed_at DESC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(sql, params).fetchall()
        return [self._memory_row_to_dict(r) for r in rows]

    def list_memories(
        self,
        user_id: str | None = None,
        category: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """列出记忆，可按用户和分类过滤"""
        sql = "SELECT * FROM memories WHERE 1=1"
        params: list = []
        if user_id is not None:
            sql += " AND (user_id IS NULL OR user_id=?)"
            params.append(user_id)
        if category:
            sql += " AND category=?"
            params.append(category)
        sql += " ORDER BY importance DESC, created_at DESC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(sql, params).fetchall()
        return [self._memory_row_to_dict(r) for r in rows]


# ==================== 全局实例 ====================


# ==================== 全局实例 ====================

_store: PersistenceStore | None = None


def init_store(db_path: str | Path = "data/memox.db") -> PersistenceStore:
    """初始化持久化存储"""
    global _store
    _store = PersistenceStore(db_path)
    logger.info(f"[Persistence] SQLite 已初始化: {db_path}")
    return _store


def get_store() -> PersistenceStore | None:
    """获取持久化存储实例"""
    return _store
