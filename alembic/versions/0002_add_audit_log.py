"""Add audit_log table

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-29

Tables:
- audit_log: 审计日志
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("timestamp", sa.Text(), nullable=False),
        sa.Column("username", sa.Text(), nullable=False, server_default=""),
        sa.Column("user_role", sa.Text(), nullable=False, server_default=""),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("resource", sa.Text(), nullable=False),
        sa.Column("resource_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("details", sa.Text(), server_default="{}"),
        sa.Column("ip_address", sa.Text(), nullable=False, server_default=""),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_audit_timestamp", "audit_log", [sa.text("timestamp DESC")])
    op.create_index("idx_audit_resource", "audit_log", ["resource", "resource_id"])


def downgrade() -> None:
    op.drop_table("audit_log")
