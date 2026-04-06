"""SQLite 持久化存储 - 会话与任务历史"""

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

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
                updated_at TEXT NOT NULL
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

            CREATE INDEX IF NOT EXISTS idx_messages_session ON chat_messages(session_id);
            CREATE INDEX IF NOT EXISTS idx_task_created ON task_history(created_at);
        """)
        self._conn.commit()

    # ==================== 会话 ====================

    def save_session(self, session_id: str, title: str = "") -> None:
        """创建或更新会话"""
        now = datetime.now().isoformat()
        self._conn.execute(
            """INSERT INTO chat_sessions (id, title, created_at, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET title=excluded.title, updated_at=excluded.updated_at""",
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
        """更新会话标题"""
        now = datetime.now().isoformat()
        self._conn.execute(
            "UPDATE chat_sessions SET title=?, updated_at=? WHERE id=?",
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

    def list_sessions(self, limit: int = 50) -> list[dict]:
        """列出最近的会话"""
        rows = self._conn.execute(
            """SELECT s.id, s.title, s.created_at, s.updated_at,
                      (SELECT COUNT(*) FROM chat_messages WHERE session_id=s.id) as message_count
               FROM chat_sessions s
               ORDER BY s.updated_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

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

    def close(self) -> None:
        """关闭数据库连接"""
        self._conn.close()


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
