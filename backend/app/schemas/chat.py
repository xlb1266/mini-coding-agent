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


class ChatModeSelectRequest(CamelModel):
    """切换会话的执行档位：审批档位与自主档位的预设组合（会话级覆盖）。"""

    approval_level: str
    autonomy_mode: str


class ChatModelSelectRequest(CamelModel):
    """切换会话使用的模型（会话级覆盖）。"""

    model_id: int


class ChatContextCategoryOut(CamelModel):
    """上下文占用的一个分类：估算 Token 与占总上下文长度的百分比。"""

    key: str
    label: str
    tokens: int
    percent: float


class ChatContextUsageOut(CamelModel):
    """会话上下文占用量（估算口径，见 token_estimation）。

    estimatedTokens 为下次请求的估算输入（系统提示词 + 技能目录 + 工具
    定义 + 会话摘要 + 有效历史），contextLength 为当前生效模型的上下文
    长度；百分比以上下文长度为分母。工具定义只含内置工具，未连接的
    MCP 工具定义无法离线估算，不计入。
    """

    estimated_tokens: int
    context_length: int
    available_input_tokens: int
    categories: list[ChatContextCategoryOut] = []
    # Agent 绑定了 MCP Server 时为 True：提示 MCP 工具定义未计入估算
    has_mcp_tools: bool = False


class ChatSendRequest(CamelModel):
    """发送一条用户消息并开始生成回复。"""

    content: Annotated[str, Field(max_length=MESSAGE_CONTENT_MAX_LENGTH)]


class ChatMessageOut(CamelModel):
    """一条聊天消息；assistant 消息带生成时的 Agent 快照与过程轨迹。

    timeline 为该次回复的展示轨迹（正文段 / 思考段 / 工具卡片段按事件顺序
    交错）；纯文字回复为 None。只用于渲染，不参与模型上下文。
    """

    id: int
    session_id: int
    role: str
    content: str
    status: str
    agent_id: int | None = None
    agent_name: str | None = None
    seq: int
    created_at: datetime
    timeline: list[dict[str, Any]] | None = None

    @field_serializer("created_at")
    def serialize_datetime(self, value: datetime) -> str:
        # 数据库存的是 naive UTC；补上 UTC 标记，前端 new Date 可正确转本地时间
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()


class ChatSessionOut(CamelModel):
    """会话摘要；agentName 在 Agent 被删除后为 null。

    awaitingAcceptance / stageSummary 为阶段化开发状态（阶段十五）：
    上一阶段已完成（finish_stage）等待验收时为 True，stageSummary 是
    finish_stage 的阶段摘要（stageName / summary / handoffPath / nextStep）。
    projectId / projectRole / stageIndex 为阶段化开发 v2 的项目归属：
    项目下的会话标明角色（plan / stage / summary）与负责的阶段序号。
    """

    id: int
    title: str
    agent_id: int | None = None
    agent_name: str | None = None
    is_generating: bool
    awaiting_acceptance: bool = False
    stage_summary: dict[str, Any] | None = None
    project_id: int | None = None
    project_role: str | None = None
    stage_index: int | None = None
    # 会话级档位与模型覆盖（聊天输入框切换）；null = 跟随 Agent 配置
    approval_level: str | None = None
    autonomy_mode: str | None = None
    model_id: int | None = None
    created_at: datetime
    updated_at: datetime

    @field_serializer("created_at", "updated_at")
    def serialize_datetime(self, value: datetime) -> str:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()


class StageProjectStageOut(CamelModel):
    """阶段计划里的一项：名称、目标与状态（pending / in_progress / done）。"""

    name: str
    goal: str
    status: str


class StageProjectOut(CamelModel):
    """阶段项目：计划清单、推进状态与项目下各会话的映射。

    stageSessions 为阶段序号（1-based）→ 该阶段会话 id 的映射（跳转用）；
    planSessionId / summarySessionId 为规划与汇总会话。
    """

    id: int
    title: str
    agent_id: int | None = None
    agent_name: str
    workspace_dir: str
    stages: list[StageProjectStageOut] = []
    current_stage: int = 0
    status: str
    stage_sessions: dict[int, int] = {}
    plan_session_id: int | None = None
    summary_session_id: int | None = None
    created_at: datetime
    updated_at: datetime

    @field_serializer("created_at", "updated_at")
    def serialize_datetime(self, value: datetime) -> str:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()


class StageProjectOperationOut(CamelModel):
    """项目推进操作（开始 / 下一阶段 / 生成汇总）的结果：
    后端已创建新会话并自动发出首条消息，前端切换到该会话即可。"""

    project: StageProjectOut
    session: ChatSessionOut


class ChatGenerationOut(CamelModel):
    """当前会话正在生成的状态快照（thinking 尚无正文，generating 有部分正文，tooling 工具执行中，approval 等待审批）。

    timeline 为展示时间线（正文段与思考/工具/审批/压缩卡片段按事件顺序交错），
    供前端恢复卡片与消息顺序；运行刚启动或无内容时为空列表。
    pendingApprovals 为当前仍在等待用户决议的审批请求（刷新后恢复展示）。
    """

    phase: str
    content: str
    agent_name: str
    # 正在执行的工具展示名（仅 tooling 阶段）
    tool_name: str | None = None
    # 运行开始时间（epoch 毫秒）：展示“处理中 Xs”，刷新后仍准确
    started_at: int | None = None
    timeline: list[dict[str, Any]] = []
    pending_approvals: list[dict[str, Any]] = []


class ChatApprovalDecisionRequest(CamelModel):
    """对一次工具执行审批的决议；action 为 approve（批准）或 reject（拒绝）。"""

    action: str

    @property
    def approved(self) -> bool:
        return self.action == "approve"


class ChatApprovalOut(CamelModel):
    """一条挂起的审批请求；arguments 为 Runtime 整理后的展示参数。"""

    request_id: str
    tool_name: str
    display_name: str
    tool_type: str
    arguments: Any = None
    created_at: datetime

    @field_serializer("created_at")
    def serialize_datetime(self, value: datetime) -> str:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()


class ChatApprovalListOut(CamelModel):
    """会话当前挂起的审批请求列表。"""

    approvals: list[ChatApprovalOut] = []


class ChatApprovalDecisionResultOut(CamelModel):
    """决议结果：outcome 为 approved / rejected。"""

    request_id: str
    action: str
    outcome: str


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
