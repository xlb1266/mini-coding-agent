"""阶段十五：阶段化开发与执行治理——审批档位/自主档位/命令白名单、
会话阶段验收状态、评测过程评分列。

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-04

"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # agents：审批档位（取代布尔开关）、自主档位、Shell 命令白名单
    op.add_column("agents", sa.Column("approval_level", sa.String(length=16), nullable=False, server_default="off"))
    op.add_column("agents", sa.Column("autonomy_mode", sa.String(length=16), nullable=False, server_default="stage_confirm"))
    op.add_column("agents", sa.Column("shell_whitelist", sa.Text(), nullable=False, server_default=""))
    # 回填：原布尔审批开关（0011）→ 审批档位；开=standard，关=off
    op.execute("UPDATE agents SET approval_level = 'standard' WHERE require_approval = 1")
    with op.batch_alter_table("agents") as batch:
        batch.drop_column("require_approval")

    # chat_sessions：阶段验收状态与上一阶段交接摘要
    op.add_column("chat_sessions", sa.Column("awaiting_acceptance", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("chat_sessions", sa.Column("stage_summary_json", sa.Text(), nullable=True))

    # eval_task_results：LLM Judge 过程评分（0-100）与理由；未产出为 NULL
    op.add_column("eval_task_results", sa.Column("process_score", sa.Float(), nullable=True))
    op.add_column("eval_task_results", sa.Column("process_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("eval_task_results", "process_reason")
    op.drop_column("eval_task_results", "process_score")
    op.drop_column("chat_sessions", "stage_summary_json")
    op.drop_column("chat_sessions", "awaiting_acceptance")
    with op.batch_alter_table("agents") as batch:
        batch.add_column(sa.Column("require_approval", sa.Boolean(), nullable=False, server_default=sa.false()))
    # 回退：档位不为 off 的视为开启过审批
    op.execute("UPDATE agents SET require_approval = 1 WHERE approval_level != 'off'")
    op.drop_column("agents", "shell_whitelist")
    op.drop_column("agents", "autonomy_mode")
    op.drop_column("agents", "approval_level")
