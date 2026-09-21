"""模块一：Agent 工作区目录与工具调用审批开关。

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-21

"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # agents：工作区目录（空串 = 使用全局默认目录）；审批开关本模块只落列，逻辑在模块二实现
    op.add_column("agents", sa.Column("workspace_dir", sa.String(length=512), nullable=False, server_default=""))
    op.add_column("agents", sa.Column("require_approval", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("agents", "require_approval")
    op.drop_column("agents", "workspace_dir")
