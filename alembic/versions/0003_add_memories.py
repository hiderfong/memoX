"""Add memories table for cross-session memory recall

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-29

Tables:
- memories: 跨会话持久记忆（FTS5 全文搜索）
- memories_fts: FTS5 虚拟表，同步 memories.content
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "memories",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=True),  # NULL = 全局记忆
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False, server_default="general"),
        sa.Column("importance", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("source_session_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("last_accessed_at", sa.Text(), nullable=False),
        sa.Column("metadata", sa.Text(), server_default="{}"),  # JSON
    )

    # FTS5 虚拟表用于全文检索
    op.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
            content,
            content='memories',
            content_rowid='rowid'
        )
    """)

    # 触发器：自动同步 FTS 表
    op.execute("""
        CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
            INSERT INTO memories_fts(rowid, content) VALUES (NEW.rowid, NEW.content);
        END
    """)
    op.execute("""
        CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
            INSERT INTO memories_fts(memories_fts, rowid, content) VALUES('delete', OLD.rowid, OLD.content);
        END
    """)
    op.execute("""
        CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
            INSERT INTO memories_fts(memories_fts, rowid, content) VALUES('delete', OLD.rowid, OLD.content);
            INSERT INTO memories_fts(rowid, content) VALUES (NEW.rowid, NEW.content);
        END
    """)

    op.create_index("idx_memories_user", "memories", ["user_id"])
    op.create_index("idx_memories_category", "memories", ["category"])
    op.create_index("idx_memories_created", "memories", [sa.text("created_at DESC")])


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS memories_au")
    op.execute("DROP TRIGGER IF EXISTS memories_ad")
    op.execute("DROP TRIGGER IF EXISTS memories_ai")
    op.execute("DROP TABLE IF EXISTS memories_fts")
    op.drop_table("memories")
