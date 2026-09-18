"""阶段十：Agent 上下文压缩配置 + 会话摘要与压缩边界。

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-13

"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # agents：上下文压缩配置，默认开启（自动压缩 / 触发比例 80% / 保留 4 轮 / 摘要约 1000 token）
    op.add_column("agents", sa.Column("auto_compact", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("agents", sa.Column("compact_threshold", sa.Integer(), nullable=False, server_default="80"))
    op.add_column("agents", sa.Column("compact_keep_rounds", sa.Integer(), nullable=False, server_default="4"))
    op.add_column("agents", sa.Column("compact_summary_tokens", sa.Integer(), nullable=False, server_default="1000"))
    # chat_sessions：会话摘要与"已压缩到哪条消息"的边界（NULL 表示尚未压缩）
    op.add_column("chat_sessions", sa.Column("summary_text", sa.Text(), nullable=True))
    op.add_column("chat_sessions", sa.Column("summary_boundary_seq", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("chat_sessions", "summary_boundary_seq")
    op.drop_column("chat_sessions", "summary_text")
    op.drop_column("agents", "compact_summary_tokens")
    op.drop_column("agents", "compact_keep_rounds")
    op.drop_column("agents", "compact_threshold")
    op.drop_column("agents", "auto_compact")
