"""阶段一基线：仅建立迁移链，业务表在后续阶段通过新增 revision 创建。

Revision ID: 0001
Revises:
Create Date: 2026-09-11

"""
from collections.abc import Sequence
from typing import Union

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
