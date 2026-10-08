"""Agent 评测业务表：数据集、测试用例、评测任务与逐用例结果（阶段十二）。

评测复用统一 AgentRuntime：每条用例把用户问题交给目标 Agent 运行一次，
并按评测来源关联一条 Run（agent_runs.source = evaluation）；评分模型走
统一的模型调用基础设施（非流式），但不执行 Agent 工具循环。

快照约定（阶段十二的关键不变量）：
- 任务创建时把每条用例的问题 / 期望答案 / 评分要求复制进结果行，
  后续编辑数据集不改变已有任务的材料；
- 任务创建时保存 Agent 配置快照（名称、提示词版本、系统提示词、轮数、
  实际生效的工具 / Skill / MCP 集合与模型信息），便于解释评测结果；
- 评分调用的耗时、Token 用量与错误保存在结果行自己的字段里，不混入
  目标 Agent Run 的性能指标。

用例状态：pending / executing / scoring / passed（通过）/ failed（未通过）/
exec_failed（执行失败）/ scoring_failed（评分失败）；
任务状态：pending（待运行）/ running（运行中）/ completed（已完成）/
interrupted（已中断，服务重启后无法继续）。
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base

# ---- 数据集稳定标识：示例数据集用它保证初始化幂等 ----
EXAMPLE_DATASET_KEY = "builtin-eval-basics"

# ---- 任务状态 ----
TASK_STATUS_PENDING = "pending"
TASK_STATUS_RUNNING = "running"
TASK_STATUS_COMPLETED = "completed"
TASK_STATUS_INTERRUPTED = "interrupted"
TASK_STATUSES = (
    TASK_STATUS_PENDING,
    TASK_STATUS_RUNNING,
    TASK_STATUS_COMPLETED,
    TASK_STATUS_INTERRUPTED,
)

# ---- 用例状态 ----
CASE_STATUS_PENDING = "pending"
CASE_STATUS_EXECUTING = "executing"
CASE_STATUS_SCORING = "scoring"
CASE_STATUS_PASSED = "passed"
CASE_STATUS_FAILED = "failed"
CASE_STATUS_EXEC_FAILED = "exec_failed"
CASE_STATUS_SCORING_FAILED = "scoring_failed"
# 终态：计入"已完成数"
CASE_TERMINAL_STATUSES = (
    CASE_STATUS_PASSED,
    CASE_STATUS_FAILED,
    CASE_STATUS_EXEC_FAILED,
    CASE_STATUS_SCORING_FAILED,
)

# agent_runs.source 的评测来源标记
RUN_SOURCE_EVALUATION = "evaluation"


class EvalDataset(Base):
    """一个评测数据集：名称、说明与一组测试用例。

    key 为可选的稳定标识（示例数据集专用），普通数据集为 NULL；
    唯一约束保证按 key 的幂等种子不会重复创建。
    """

    __tablename__ = "eval_datasets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    key: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    cases: Mapped[list["EvalCase"]] = relationship(
        back_populates="dataset",
        cascade="all, delete-orphan",
        order_by="EvalCase.id",
    )


class EvalCase(Base):
    """一条测试用例：用户问题必填，期望答案与评分要求至少填一项。"""

    __tablename__ = "eval_cases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dataset_id: Mapped[int] = mapped_column(
        ForeignKey("eval_datasets.id"), nullable=False, index=True
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    expected_answer: Mapped[str] = mapped_column(Text, nullable=False, default="")
    scoring_requirements: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    dataset: Mapped["EvalDataset"] = relationship(back_populates="cases")


class EvalTask(Base):
    """一次评测任务：配置快照 + 逐用例结果。

    dataset_id 仅作关联参考；删除保护保证被任务引用的数据集行不删除。
    评分模型快照（名称与标识）在创建时保存，改名或删除不影响历史任务。
    """

    __tablename__ = "eval_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dataset_id: Mapped[int] = mapped_column(
        ForeignKey("eval_datasets.id"), nullable=False, index=True
    )
    dataset_name: Mapped[str] = mapped_column(String(64), nullable=False)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"), nullable=False, index=True)
    agent_name: Mapped[str] = mapped_column(String(64), nullable=False)
    scorer_model_id: Mapped[int] = mapped_column(
        ForeignKey("model_configs.id"), nullable=False, index=True
    )
    scorer_model_name: Mapped[str] = mapped_column(String(64), nullable=False)
    scorer_model_identifier: Mapped[str] = mapped_column(String(128), nullable=False)
    # 0-100 的整数；得分 >= 该值记为通过
    pass_score: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    # 关闭时不向目标 Agent 提供工具定义，运行时也拒绝工具执行
    allow_tools: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Agent 配置快照（JSON）：名称、提示词版本、系统提示词、轮数、
    # 实际生效的工具 / Skill / MCP 集合与模型信息
    agent_config_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=TASK_STATUS_PENDING)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    results: Mapped[list["EvalTaskResult"]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
        order_by="EvalTaskResult.case_order",
    )


class EvalTaskResult(Base):
    """任务内一条用例的快照与结果（任务创建时逐条生成）。"""

    __tablename__ = "eval_task_results"
    __table_args__ = (
        UniqueConstraint("task_id", "case_order", name="uq_eval_results_task_order"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("eval_tasks.id"), nullable=False, index=True
    )
    # 原用例行；数据集编辑不受快照影响，仅作追溯参考
    case_id: Mapped[int] = mapped_column(Integer, nullable=False)
    # 用例在任务内的顺序（创建时按数据集用例顺序编号，从 1 开始）
    case_order: Mapped[int] = mapped_column(Integer, nullable=False)
    # ---- 用例内容快照 ----
    question: Mapped[str] = mapped_column(Text, nullable=False)
    expected_answer: Mapped[str] = mapped_column(Text, nullable=False, default="")
    scoring_requirements: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # ---- 执行与评分 ----
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=CASE_STATUS_PENDING)
    # 目标 Agent 的实际回答（未完成的运行也保留已有输出，但不算正常答案）
    actual_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 过程评分（阶段十五）：LLM Judge 对执行过程合理性的打分（0-100）与理由；
    # 未启用 / 未产出时为 NULL
    process_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    process_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 关联的目标 Agent Run（agent_runs.id 与 run_uid）
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    run_uid: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Runtime 结束原因（model_finished / max_rounds / cancelled / ...）
    exec_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # 执行失败原因（脱敏、可读）
    exec_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 评分失败原因（校验失败两次或请求错误）
    score_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # ---- 评分调用自身开销（与目标 Agent Run 指标分开记录） ----
    scoring_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scoring_total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scoring_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    task: Mapped["EvalTask"] = relationship(back_populates="results")
