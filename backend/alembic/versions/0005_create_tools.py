"""阶段六：创建工具管理表 tools 并写入三个内置工具。

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-11

"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

from app.services.tools.registry import builtin_tool_rows

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 本迁移落库时注册表里已有的三个工具；后续新增的内置工具由各自的迁移插入，
# 这里固定 key，避免注册表扩充后重放本迁移时重复插入
ORIGINAL_TOOL_KEYS = ("builtin_current_time", "builtin_shell", "builtin_file")


def upgrade() -> None:
    op.create_table(
        "tools",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("key", sa.String(length=64), nullable=False, unique=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("parameters_json", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_builtin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    # 种子数据与 app/services/tools/registry.py 同源，避免定义漂移
    tools_table = sa.table(
        "tools",
        sa.column("key"),
        sa.column("name"),
        sa.column("description"),
        sa.column("parameters_json"),
        sa.column("enabled"),
        sa.column("is_builtin"),
        sa.column("created_at"),
        sa.column("updated_at"),
    )
    rows = [row for row in builtin_tool_rows() if row["key"] in ORIGINAL_TOOL_KEYS]
    op.bulk_insert(tools_table, rows)


def downgrade() -> None:
    op.drop_table("tools")
