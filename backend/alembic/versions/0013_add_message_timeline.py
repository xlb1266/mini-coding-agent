"""聊天回复的过程轨迹（思考段与工具调用卡片）随消息入库。

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-24

"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 展示时间线 JSON（正文段 / 思考段 / 工具卡片段按事件顺序交错）；
    # 没有过程轨迹的回复为 NULL，不影响原有读取
    op.add_column("chat_messages", sa.Column("timeline_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("chat_messages", "timeline_json")
