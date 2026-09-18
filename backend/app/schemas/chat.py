"""聊天模块的请求/响应模型。

接口层统一驼峰命名；会话摘要不包含消息正文，消息正文只在
/sessions/{id}/messages 与生成事件中返回。
"""

from datetime import datetime, timezone
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_serializer
from pydantic.alias_generators import to_camel

# 会话标题：初始标题与首条用户消息截取长度
DEFAULT_SESSION_TITLE = "新会话"
SESSION_TITLE_MAX_LENGTH = 128
SESSION_TITLE_SNIPPET_LENGTH = 30

# 消息正文长度上限（与 Agent 系统提示词一致）
MESSAGE_CONTENT_MAX_LENGTH = 32000


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class ChatSessionCreateRequest(CamelModel):
    """新建会话；不指定 agentId 时使用默认 Agent。"""

    agent_id: int | None = None


class ChatAgentSelectRequest(CamelModel):
    """切换会话当前选择的 Agent。"""

    agent_id: int


class ChatSendRequest(CamelModel):
    """发送一条用户消息并开始生成回复。"""

    content: Annotated[str, Field(max_length=MESSAGE_CONTENT_MAX_LENGTH)]


class ChatMessageOut(CamelModel):
    """一条聊天消息；assistant 消息带生成时的 Agent 快照。"""

    id: int
    session_id: int
    role: str
    content: str
    status: str
    agent_id: int | None = None
    agent_name: str | None = None
    seq: int
    created_at: datetime

    @field_serializer("created_at")
    def serialize_datetime(self, value: datetime) -> str:
        # 数据库存的是 naive UTC；补上 UTC 标记，前端 new Date 可正确转本地时间
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()


class ChatSessionOut(CamelModel):
    """会话摘要；agentName 在 Agent 被删除后为 null。"""

    id: int
    title: str
    agent_id: int | None = None
    agent_name: str | None = None
    is_generating: bool
    created_at: datetime
    updated_at: datetime

    @field_serializer("created_at", "updated_at")
    def serialize_datetime(self, value: datetime) -> str:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()


class ChatGenerationOut(CamelModel):
    """当前会话正在生成的状态快照（thinking 尚无正文，generating 有部分正文，tooling 工具执行中）。

    timeline 为展示时间线（正文段与工具卡片段按事件顺序交错），供前端
    恢复工具卡片与消息顺序；运行刚启动或无内容时为空列表。
    """

    phase: str
    content: str
    agent_name: str
    # 正在执行的工具展示名（仅 tooling 阶段）
    tool_name: str | None = None
    timeline: list[dict[str, Any]] = []


class ChatMessagesOut(CamelModel):
    """打开会话时的一次性数据：摘要 + 全部消息 + 进行中的生成状态。"""

    session: ChatSessionOut
    messages: list[ChatMessageOut] = []
    generation: ChatGenerationOut | None = None


class ChatSendResultOut(CamelModel):
    """发送成功的结果：用户消息已保存，回复生成已在后台开始。"""

    session: ChatSessionOut
    user_message: ChatMessageOut
    generation: ChatGenerationOut


class ChatStopResultOut(CamelModel):
    """停止生成的结果；outcome 为 cancelled / done / error / not_running。"""

    outcome: str
    message: ChatMessageOut | None = None
    content: str = ""
    error_message: str | None = None
    hint: str | None = None
    error_type: str | None = None


# Pydantic 类型/约束错误 → 字段级中文提示（key 为驼峰字段名，与前端一致）
FIELD_ERROR_MESSAGES: dict[str, str] = {
    "content": f"消息内容不能超过 {MESSAGE_CONTENT_MAX_LENGTH} 个字符",
    "agentId": "请选择一个已存在的 Agent",
}
