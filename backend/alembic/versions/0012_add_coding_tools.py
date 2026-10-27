"""模块一：写入三个内置编码工具（edit_file / list_dir / grep）。

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-21

"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

from app.services.tools.registry import builtin_tool_rows

# revision identifiers, used by Alembic.
revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 本次迁移新增的内置工具 key（0005 已插入的三个不重复插入）
NEW_TOOL_KEYS = ("builtin_edit_file", "builtin_list_dir", "builtin_grep")


def upgrade() -> None:
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
    rows = [row for row in builtin_tool_rows() if row["key"] in NEW_TOOL_KEYS]
    op.bulk_insert(tools_table, rows)


def downgrade() -> None:
    # 先清掉引用这些工具的 Agent 绑定，再删工具行
    keys = ", ".join(f"'{key}'" for key in NEW_TOOL_KEYS)
    op.execute(
        f"DELETE FROM agent_bindings WHERE capability_type = 'tool' AND ref_key IN ({keys})"
    )
    op.execute(f"DELETE FROM tools WHERE key IN ({keys})")
