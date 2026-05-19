"""Add chat session summary column

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-19
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {
        row[1]
        for row in bind.exec_driver_sql("PRAGMA table_info(chat_sessions)").fetchall()
    }
    if "summary" not in columns:
        op.add_column(
            "chat_sessions",
            sa.Column("summary", sa.Text(), server_default=""),
        )


def downgrade() -> None:
    bind = op.get_bind()
    columns = {
        row[1]
        for row in bind.exec_driver_sql("PRAGMA table_info(chat_sessions)").fetchall()
    }
    if "summary" in columns:
        with op.batch_alter_table("chat_sessions") as batch_op:
            batch_op.drop_column("summary")
