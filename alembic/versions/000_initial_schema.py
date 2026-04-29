"""Initial schema - v0

Revision ID: 0001
Revises:
Create Date: 2026-04-29

Tables:
- chat_sessions: 聊天会话
- chat_messages: 会话消息
- task_history: 任务历史
- worker_token_usage: Worker Token 使用统计
- scheduled_tasks: 定时任务
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # chat_sessions
    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("archived", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_sessions_updated", "chat_sessions", ["updated_at"])

    # chat_messages
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("metadata", sa.Text(), server_default="{}"),
        sa.ForeignKeyConstraint(["session_id"], ["chat_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_messages_session", "chat_messages", ["session_id"])

    # task_history
    op.create_table(
        "task_history",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("result_summary", sa.Text(), server_default=""),
        sa.Column("final_score", sa.Float(), server_default="0.0"),
        sa.Column("iterations", sa.Text(), server_default="[]"),
        sa.Column("mail_log", sa.Text(), server_default=""),
        sa.Column("shared_dir", sa.Text(), server_default=""),
        sa.Column("suggestions", sa.Text(), server_default="[]"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("completed_at", sa.Text()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_task_created", "task_history", ["created_at"])

    # worker_token_usage
    op.create_table(
        "worker_token_usage",
        sa.Column("worker_id", sa.Text(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("call_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("worker_id"),
    )

    # scheduled_tasks
    op.create_table(
        "scheduled_tasks",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("cron", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("active_group_ids", sa.Text(), server_default=""),
        sa.Column("source_session_id", sa.Text(), server_default=""),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("last_run_at", sa.Text(), server_default=""),
        sa.Column("next_run_at", sa.Text(), server_default=""),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_sched_enabled", "scheduled_tasks", ["enabled"])


def downgrade() -> None:
    op.drop_table("scheduled_tasks")
    op.drop_table("worker_token_usage")
    op.drop_table("task_history")
    op.drop_table("chat_messages")
    op.drop_table("chat_sessions")
