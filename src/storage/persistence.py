"""SQLite 持久化存储 - 会话与任务历史"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from loguru import logger


class PersistenceStore:
    """SQLite 持久化存储"""

    def __init__(self, db_path: str | Path = "data/memox.db"):
        self._db_path = str(db_path)
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self) -> None:
        """初始化数据库表"""
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS chat_sessions (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                archived INTEGER NOT NULL DEFAULT 0
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

            CREATE INDEX IF NOT EXISTS idx_messages_session ON chat_messages(session_id);
            CREATE INDEX IF NOT EXISTS idx_task_created ON task_history(created_at);

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
        """)
        # 旧库迁移：为 chat_sessions 补齐 archived 列
        cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(chat_sessions)").fetchall()}
        if "archived" not in cols:
            self._conn.execute("ALTER TABLE chat_sessions ADD COLUMN archived INTEGER NOT NULL DEFAULT 0")
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

    def get_session_messages(self, session_id: str) -> list[dict]:
        """获取会话的所有消息"""
        rows = self._conn.execute(
            "SELECT role, content, created_at, metadata FROM chat_messages WHERE session_id=? ORDER BY id",
            (session_id,),
        ).fetchall()
        return [
            {
                "role": r["role"],
                "content": r["content"],
                "created_at": r["created_at"],
                "metadata": json.loads(r["metadata"]),
            }
            for r in rows
        ]

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
        self._conn.execute(
            """INSERT INTO task_history (id, description, status, result_summary, final_score,
                                         iterations, mail_log, shared_dir, suggestions, created_at, completed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   status=excluded.status, result_summary=excluded.result_summary,
                   final_score=excluded.final_score, iterations=excluded.iterations,
                   mail_log=excluded.mail_log, suggestions=excluded.suggestions,
                   completed_at=excluded.completed_at""",
            (
                task_data.get("task_id", ""),
                task_data.get("description", ""),
                task_data.get("status", "completed"),
                task_data.get("result", ""),
                task_data.get("final_score", 0.0),
                json.dumps(task_data.get("iterations", []), ensure_ascii=False),
                task_data.get("mail_log", ""),
                task_data.get("shared_dir", ""),
                json.dumps(task_data.get("suggestions", []), ensure_ascii=False),
                now,
                now,
            ),
        )
        self._conn.commit()

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

    def close(self) -> None:
        """关闭数据库连接"""
        self._conn.close()


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
        limit: int = 100,
    ) -> list[dict]:
        """查询审计日志（倒序）"""
        conditions = []
        params: list = []
        if resource:
            conditions.append("resource = ?")
            params.append(resource)
        if action:
            conditions.append("action = ?")
            params.append(action)
        if username:
            conditions.append("username = ?")
            params.append(username)
        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        rows = self._conn.execute(
            f"""SELECT * FROM audit_log {where}
                ORDER BY timestamp DESC LIMIT ?""",
            [*params, limit],
        ).fetchall()
        return [dict(r) for r in rows]


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
