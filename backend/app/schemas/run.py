"""运行记录模块的请求/响应模型（阶段十一）。

列表只读取 agent_runs 行上的快照与汇总字段，不加载事件、模型输入或
工具结果；事件时间线与详细载荷由详情端点按需返回。
"""

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_serializer
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class RunUsageOut(CamelModel):
    """Token 用量汇总；各项 NULL 表示该用量未知（不填 0）。

    cachedTokens 为输入 Token 中缓存命中的部分（已包含在 promptTokens
    内，不重复相加）。
    """

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cached_tokens: int | None = None


class RunItemOut(CamelModel):
    """一条运行记录（列表与详情共用的摘要结构）。

    agentName / modelName / modelIdentifier 为运行时快照，Agent 或模型
    后续改名、删除不影响历史记录展示。
    """

    id: int
    run_uid: str
    session_id: int | None = None
    # 来源：chat（聊天页）/ evaluation（评测任务执行用例）
    source: str = "chat"
    agent_id: int | None = None
    agent_name: str
    model_name: str
    model_identifier: str
    # running / completed / partial / cancelled / failed（与结束原因分开）
    status: str
    finish_reason: str | None = None
    error_summary: str | None = None
    user_input_preview: str
    started_at: datetime
    finished_at: datetime | None = None
    duration_ms: int | None = None
    # 实际发出的模型请求次数（含失败请求与摘要生成请求）
    model_calls: int
    # 实际进入工具执行入口的次数；校验被拒绝的单独计
    tool_calls: int
    rejected_tool_calls: int
    # None 表示本次运行没有正文输出
    first_output_ms: int | None = None
    # None 表示上游从未返回用量（未知，不填 0）
    token_usage: RunUsageOut | None = None
    # 并非全部模型请求都返回了用量（展示"统计不完整"）
    tokens_incomplete: bool

    @field_serializer("started_at", "finished_at")
    def serialize_datetime(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()


class RunListOut(CamelModel):
    """运行列表：按开始时间倒序的一页。"""

    items: list[RunItemOut] = []
    total: int
    page: int
    page_size: int


class RunEventOut(CamelModel):
    """时间线里的一个安全事件（payload 为脱敏后的 Runtime 事件原样）。"""

    id: int
    seq: int
    event_type: str
    payload: dict[str, Any]
    # 是否存在可按需加载的详细载荷（工具完整结果 / 最终正文片段）
    has_detail: bool


class RunDetailOut(CamelModel):
    """运行详情：摘要 + 按真实事件顺序排列的时间线（默认不含详细载荷）。"""

    run: RunItemOut
    events: list[RunEventOut] = []


class RunEventDetailOut(CamelModel):
    """事件的详细载荷（受控读取入口；同样经过脱敏）。"""

    event_id: int
    seq: int
    event_type: str
    payload: dict[str, Any]


class RunListQuery(CamelModel):
    """列表查询参数（查询字符串解析用）。"""

    status: str | None = None
    agent_id: int | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
