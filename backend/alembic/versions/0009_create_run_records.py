"""阶段十一：运行记录三张表（Run 主记录 / 安全事件 / 详细载荷）。

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-14

"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Run 主记录：状态与结束原因分开；Agent / 模型为运行时快照，列表不回查
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_uid", sa.String(32), nullable=False),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("chat_sessions.id"), nullable=True),
        sa.Column("agent_id", sa.Integer(), sa.ForeignKey("agents.id"), nullable=True),
        sa.Column("agent_name", sa.String(64), nullable=False),
        sa.Column("model_id", sa.Integer(), sa.ForeignKey("model_configs.id"), nullable=True),
        sa.Column("model_name", sa.String(64), nullable=False),
        sa.Column("model_identifier", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("finish_reason", sa.String(32), nullable=True),
        sa.Column("error_summary", sa.String(200), nullable=True),
        sa.Column("user_input_preview", sa.String(200), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("model_calls", sa.Integer(), nullable=False),
        sa.Column("tool_calls", sa.Integer(), nullable=False),
        sa.Column("rejected_tool_calls", sa.Integer(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("cached_tokens", sa.Integer(), nullable=True),
        sa.Column("tokens_incomplete", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("first_output_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_agent_runs_run_uid", "agent_runs", ["run_uid"], unique=True)
    op.create_index("ix_agent_runs_session_id", "agent_runs", ["session_id"])
    op.create_index("ix_agent_runs_agent_id", "agent_runs", ["agent_id"])
    op.create_index("ix_agent_runs_model_id", "agent_runs", ["model_id"])
    op.create_index("ix_agent_runs_started_at", "agent_runs", ["started_at"])

    # 安全事件：run_uid + seq 唯一，重复投递不重复插入
    op.create_table(
        "agent_run_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "run_uid", sa.String(32), sa.ForeignKey("agent_runs.run_uid"), nullable=False
        ),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("run_uid", "seq", name="uq_agent_run_events_uid_seq"),
    )
    op.create_index("ix_agent_run_events_run_uid", "agent_run_events", ["run_uid"])
    op.create_index("ix_agent_run_events_event_type", "agent_run_events", ["event_type"])

    # 详细载荷：一行事件至多一份，按需单独加载
    op.create_table(
        "agent_run_event_details",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("agent_run_events.id"), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_agent_run_event_details_event_id", "agent_run_event_details", ["event_id"], unique=True
    )


def downgrade() -> None:
    op.drop_table("agent_run_event_details")
    op.drop_table("agent_run_events")
    op.drop_table("agent_runs")
