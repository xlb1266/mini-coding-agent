"""阶段五：创建 MCP 管理两张表 mcp_servers 与 mcp_env_vars。

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-11

"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mcp_servers",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=64), nullable=False, unique=True),
        sa.Column("description", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("command", sa.String(length=512), nullable=False),
        sa.Column("args_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_test_status", sa.String(length=16), nullable=True),
        sa.Column("last_test_at", sa.DateTime(), nullable=True),
        sa.Column("last_test_error_type", sa.String(length=24), nullable=True),
        sa.Column("last_test_error", sa.Text(), nullable=True),
        sa.Column("last_test_tools_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "mcp_env_vars",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "server_id",
            sa.Integer(),
            sa.ForeignKey("mcp_servers.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("value_nonce", sa.String(length=64), nullable=False),
        sa.Column("value_blob", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("mcp_env_vars")
    op.drop_table("mcp_servers")
