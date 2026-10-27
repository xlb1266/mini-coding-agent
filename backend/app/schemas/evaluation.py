"""Agent 评测的请求/响应模型。

与其它模块一致，接口层统一使用驼峰命名。数据集与用例的校验规则：
- 数据集名称必填；用例问题必填；
- 期望答案与评分要求至少填写一项（后端在创建 / 编辑用例时校验）；
- 任务创建要求 passScore 为 0-100 的整数（默认 60），数据集至少有一条
  用例（空数据集不能启动评测）。
"""

from datetime import datetime, timezone
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_serializer
from pydantic.alias_generators import to_camel

# ---- 字段长度（与数据库列宽一致） ----
NAME_MAX_LENGTH = 64
DESCRIPTION_MAX_LENGTH = 512
CASE_TEXT_MAX_LENGTH = 8000

# ---- 任务配置 ----
PASS_SCORE_MIN = 0
PASS_SCORE_MAX = 100
DEFAULT_PASS_SCORE = 60


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


# ---- 数据集 ----


class DatasetCreateRequest(CamelModel):
    name: Annotated[str, Field(max_length=NAME_MAX_LENGTH)]
    description: Annotated[str, Field(default="", max_length=DESCRIPTION_MAX_LENGTH)] = ""


class DatasetUpdateRequest(CamelModel):
    name: Annotated[str, Field(max_length=NAME_MAX_LENGTH)]
    description: Annotated[str, Field(default="", max_length=DESCRIPTION_MAX_LENGTH)] = ""


class DatasetOut(CamelModel):
    id: int
    name: str
    description: str
    case_count: int
    # 是否为内置示例数据集（按稳定标识判断，展示用）
    is_example: bool = False
    created_at: datetime
    updated_at: datetime

    @field_serializer("created_at", "updated_at")
    def serialize_datetime(self, value: datetime) -> str:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()


class EvalCaseCreateRequest(CamelModel):
    question: Annotated[str, Field(min_length=1, max_length=CASE_TEXT_MAX_LENGTH)]
    expected_answer: Annotated[str, Field(default="", max_length=CASE_TEXT_MAX_LENGTH)] = ""
    scoring_requirements: Annotated[str, Field(default="", max_length=CASE_TEXT_MAX_LENGTH)] = ""


class EvalCaseUpdateRequest(EvalCaseCreateRequest):
    """全量更新；校验规则与创建一致。"""


class EvalCaseOut(CamelModel):
    id: int
    dataset_id: int
    question: str
    expected_answer: str
    scoring_requirements: str
    created_at: datetime
    updated_at: datetime

    @field_serializer("created_at", "updated_at")
    def serialize_datetime(self, value: datetime) -> str:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()


class DatasetDetailOut(DatasetOut):
    cases: list[EvalCaseOut] = []


# ---- 任务 ----


class TaskCreateRequest(CamelModel):
    agent_id: int
    dataset_id: int
    scorer_model_id: int
    pass_score: Annotated[
        int, Field(ge=PASS_SCORE_MIN, le=PASS_SCORE_MAX)
    ] = DEFAULT_PASS_SCORE
    allow_tools: bool = False


class TaskStatsOut(CamelModel):
    """任务统计：已完成数含失败用例；平均分只统计获得合法评分的用例。"""

    total_cases: int = 0
    completed_cases: int = 0
    passed_count: int = 0
    failed_count: int = 0
    exec_failed_count: int = 0
    scoring_failed_count: int = 0
    pending_cases: int = 0
    executing_cases: int = 0
    scoring_cases: int = 0
    average_score: float | None = None
    # 过程评分均值（阶段十五）：只统计获得过程分的用例
    average_process_score: float | None = None


class TaskOut(CamelModel):
    id: int
    dataset_id: int
    dataset_name: str
    agent_id: int
    agent_name: str
    scorer_model_id: int
    scorer_model_name: str
    scorer_model_identifier: str
    pass_score: int
    allow_tools: bool
    status: str
    stats: TaskStatsOut
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @field_serializer("created_at", "started_at", "finished_at")
    def serialize_datetime(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()


class CaseResultOut(CamelModel):
    """一条用例的快照与结果（含关联 Run 与评分开销）。"""

    id: int
    case_order: int
    question: str
    expected_answer: str
    scoring_requirements: str
    status: str
    actual_answer: str | None = None
    score: float | None = None
    reason: str | None = None
    process_score: float | None = None
    process_reason: str | None = None
    run_id: int | None = None
    run_uid: str | None = None
    exec_reason: str | None = None
    exec_error: str | None = None
    score_error: str | None = None
    scoring_duration_ms: int | None = None
    scoring_total_tokens: int | None = None
    scoring_attempts: int = 0


class TaskDetailOut(TaskOut):
    agent_config: dict[str, Any] = {}
    results: list[CaseResultOut] = []


class CompareCaseOut(CamelModel):
    """回归对比中一行用例的两侧得分（按用例顺序对齐）。"""

    case_order: int
    question: str
    base_status: str
    target_status: str
    base_score: float | None = None
    target_score: float | None = None
    base_process_score: float | None = None
    target_process_score: float | None = None


class TaskCompareOut(CamelModel):
    """两个评测任务的回归对比（阶段十五）：聚合指标差值 + 逐用例得分对照。"""

    base_task_id: int
    target_task_id: int
    base_stats: TaskStatsOut
    target_stats: TaskStatsOut
    # 差值 = target - base（None 表示一侧没有数据，无法比较）
    average_score_delta: float | None = None
    average_process_score_delta: float | None = None
    pass_rate_delta: float | None = None
    cases: list[CompareCaseOut] = []


# Pydantic 类型/约束错误 → 字段级中文提示（key 为驼峰字段名）
FIELD_ERROR_MESSAGES: dict[str, str] = {
    "name": "请填写名称（不超过 64 个字符）",
    "description": "说明不能超过 512 个字符",
    "question": "请填写用户问题（不超过 8000 个字符）",
    "expectedAnswer": "期望答案不能超过 8000 个字符",
    "scoringRequirements": "评分要求不能超过 8000 个字符",
    "agentId": "请选择待评测的 Agent",
    "datasetId": "请选择评测数据集",
    "scorerModelId": "请选择评分模型",
    "passScore": "通过分数必须为 0–100 的整数",
    "allowTools": "允许工具只能是开启或关闭",
}
