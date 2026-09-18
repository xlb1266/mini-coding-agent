"""阶段十二：Agent 评测四张表、agent_runs.source 列与示例数据集种子。

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-15

"""
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Union

import sqlalchemy as sa
from alembic import op

from app.db.models.evaluation import EXAMPLE_DATASET_KEY
from app.services.eval_seed import example_case_rows

# revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 数据集：key 为可选稳定标识（示例数据集专用），唯一约束保证幂等种子
    op.create_table(
        "eval_datasets",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("description", sa.String(512), nullable=False),
        sa.Column("key", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_eval_datasets_key", "eval_datasets", ["key"], unique=True)

    # 用例：问题必填，期望答案与评分要求至少一项由路由层校验
    op.create_table(
        "eval_cases",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "dataset_id", sa.Integer(), sa.ForeignKey("eval_datasets.id"), nullable=False
        ),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("expected_answer", sa.Text(), nullable=False),
        sa.Column("scoring_requirements", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_eval_cases_dataset_id", "eval_cases", ["dataset_id"])

    # 任务：配置快照 + 状态
    op.create_table(
        "eval_tasks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "dataset_id", sa.Integer(), sa.ForeignKey("eval_datasets.id"), nullable=False
        ),
        sa.Column("dataset_name", sa.String(64), nullable=False),
        sa.Column("agent_id", sa.Integer(), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("agent_name", sa.String(64), nullable=False),
        sa.Column(
            "scorer_model_id", sa.Integer(), sa.ForeignKey("model_configs.id"), nullable=False
        ),
        sa.Column("scorer_model_name", sa.String(64), nullable=False),
        sa.Column("scorer_model_identifier", sa.String(128), nullable=False),
        sa.Column("pass_score", sa.Integer(), nullable=False),
        sa.Column("allow_tools", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("agent_config_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_eval_tasks_dataset_id", "eval_tasks", ["dataset_id"])
    op.create_index("ix_eval_tasks_agent_id", "eval_tasks", ["agent_id"])
    op.create_index("ix_eval_tasks_scorer_model_id", "eval_tasks", ["scorer_model_id"])
    op.create_index("ix_eval_tasks_status", "eval_tasks", ["status"])

    # 逐用例结果：任务创建时的内容快照 + 执行 / 评分结果与开销
    op.create_table(
        "eval_task_results",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("eval_tasks.id"), nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("case_order", sa.Integer(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("expected_answer", sa.Text(), nullable=False),
        sa.Column("scoring_requirements", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("actual_answer", sa.Text(), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("run_id", sa.Integer(), nullable=True),
        sa.Column("run_uid", sa.String(32), nullable=True),
        sa.Column("exec_reason", sa.String(32), nullable=True),
        sa.Column("exec_error", sa.Text(), nullable=True),
        sa.Column("score_error", sa.Text(), nullable=True),
        sa.Column("scoring_duration_ms", sa.Integer(), nullable=True),
        sa.Column("scoring_total_tokens", sa.Integer(), nullable=True),
        sa.Column("scoring_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("task_id", "case_order", name="uq_eval_results_task_order"),
    )
    op.create_index("ix_eval_task_results_task_id", "eval_task_results", ["task_id"])
    op.create_index("ix_eval_task_results_run_id", "eval_task_results", ["run_id"])

    # Run 来源：聊天 / 评测（评测用例关联的 Run 记录来源为 evaluation）
    op.add_column(
        "agent_runs",
        sa.Column("source", sa.String(16), nullable=False, server_default="chat"),
    )
    op.create_index("ix_agent_runs_source", "agent_runs", ["source"])

    # 示例数据集种子：按稳定标识幂等（服务端启动还会兜底补种，不覆盖修改）
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    datasets_table = sa.table(
        "eval_datasets",
        sa.column("id"),
        sa.column("name"),
        sa.column("description"),
        sa.column("key"),
        sa.column("created_at"),
        sa.column("updated_at"),
    )
    cases_table = sa.table(
        "eval_cases",
        sa.column("id"),
        sa.column("dataset_id"),
        sa.column("question"),
        sa.column("expected_answer"),
        sa.column("scoring_requirements"),
        sa.column("created_at"),
        sa.column("updated_at"),
    )
    conn = op.get_bind()
    existing_key = conn.execute(
        sa.select(datasets_table.c.id).where(datasets_table.c.key == EXAMPLE_DATASET_KEY)
    ).scalar()
    if existing_key is None:
        conn.execute(
            datasets_table.insert().values(
                name="示例评测集：基础问答",
                description="内置示例：三条基础问答用例，可用于快速体验评测流程（可编辑）。",
                key=EXAMPLE_DATASET_KEY,
                created_at=now,
                updated_at=now,
            )
        )
        dataset_id = conn.execute(
            sa.select(datasets_table.c.id).where(datasets_table.c.key == EXAMPLE_DATASET_KEY)
        ).scalar_one()
        conn.execute(
            cases_table.insert(),
            [
                {
                    "dataset_id": dataset_id,
                    "question": row["question"],
                    "expected_answer": row["expected_answer"],
                    "scoring_requirements": row["scoring_requirements"],
                    "created_at": now,
                    "updated_at": now,
                }
                for row in example_case_rows()
            ],
        )


def downgrade() -> None:
    op.drop_index("ix_agent_runs_source", table_name="agent_runs")
    op.drop_column("agent_runs", "source")
    op.drop_table("eval_task_results")
    op.drop_table("eval_tasks")
    op.drop_table("eval_cases")
    op.drop_table("eval_datasets")
