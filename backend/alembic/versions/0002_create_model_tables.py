"""阶段二：创建模型管理两张表 model_configs 与 model_secrets。

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-11

"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "model_configs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("display_name", sa.String(length=64), nullable=False),
        sa.Column("model_identifier", sa.String(length=128), nullable=False),
        sa.Column("base_url", sa.String(length=512), nullable=False),
        sa.Column("context_length", sa.Integer(), nullable=False),
        sa.Column("max_output_tokens", sa.Integer(), nullable=False),
        sa.Column("temperature", sa.Float(), nullable=False),
        sa.Column("input_price", sa.Float(), nullable=True),
        sa.Column("output_price", sa.Float(), nullable=True),
        sa.Column("cached_input_price", sa.Float(), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "model_secrets",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("model_id", sa.Integer(), sa.ForeignKey("model_configs.id"), nullable=False, unique=True),
        sa.Column("key_nonce", sa.String(length=64), nullable=False),
        sa.Column("key_blob", sa.String(length=512), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("model_secrets")
    op.drop_table("model_configs")
