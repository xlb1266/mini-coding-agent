"""阶段十六：阶段化开发 v2（项目级多会话）——阶段项目表与会话项目归属。

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-06

"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 阶段项目表：规划、各阶段与汇总会话的项目归属与推进状态
    op.create_table(
        "stage_projects",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("title", sa.String(length=128), nullable=False, server_default="开发项目"),
        sa.Column("agent_id", sa.Integer(), nullable=True),
        sa.Column("agent_name", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("workspace_dir", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("plan_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("current_stage", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="awaiting_start"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], name="fk_stage_projects_agent_id"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_stage_projects_agent_id", "stage_projects", ["agent_id"])

    # chat_sessions：项目归属（角色 / 阶段序号）与项目钉住的工作区目录。
    # SQLite 不支持 ALTER 直接加外键，用 batch 模式重建表完成约束添加
    with op.batch_alter_table("chat_sessions") as batch:
        batch.add_column(sa.Column("project_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("project_role", sa.String(length=16), nullable=True))
        batch.add_column(sa.Column("stage_index", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("workspace_dir", sa.String(length=512), nullable=True))
        batch.create_foreign_key(
            "fk_chat_sessions_project_id", "stage_projects", ["project_id"], ["id"]
        )
        batch.create_index("ix_chat_sessions_project_id", ["project_id"])


def downgrade() -> None:
    with op.batch_alter_table("chat_sessions") as batch:
        batch.drop_index("ix_chat_sessions_project_id")
        batch.drop_constraint("fk_chat_sessions_project_id", type_="foreignkey")
        batch.drop_column("workspace_dir")
        batch.drop_column("stage_index")
        batch.drop_column("project_role")
        batch.drop_column("project_id")
    op.drop_index("ix_stage_projects_agent_id", table_name="stage_projects")
    op.drop_table("stage_projects")
