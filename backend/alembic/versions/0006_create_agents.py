"""阶段六：创建 Agent 管理两张表 agents 与 agent_bindings。

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-11

"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agents",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "model_id",
            sa.Integer(),
            sa.ForeignKey("model_configs.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("max_rounds", sa.Integer(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "agent_bindings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "agent_id",
            sa.Integer(),
            sa.ForeignKey("agents.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("capability_type", sa.String(length=16), nullable=False),
        sa.Column("ref_key", sa.String(length=128), nullable=False),
        sa.UniqueConstraint("agent_id", "capability_type", "ref_key", name="uq_agent_binding_ref"),
    )


def downgrade() -> None:
    op.drop_table("agent_bindings")
    op.drop_table("agents")
