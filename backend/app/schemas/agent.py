"""Agent 管理的请求/响应模型。

与模型管理一致，接口层统一使用驼峰命名；能力绑定在创建/编辑请求中
按类型分列（toolKeys / skillDirNames / mcpServerIds），详情响应中
解析为绑定条目，包含可用状态（已停用或资源已不存在均视为不可用）。
"""

from datetime import datetime, timezone
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_serializer
from pydantic.alias_generators import to_camel

# 字段长度限制（与数据库列宽一致）
NAME_MAX_LENGTH = 64
DESCRIPTION_MAX_LENGTH = 512
SYSTEM_PROMPT_MAX_LENGTH = 32000

# 最大执行轮数范围与默认值；执行限制在 Agent Loop 阶段生效
MAX_ROUNDS_MIN = 1
MAX_ROUNDS_MAX = 100
DEFAULT_MAX_ROUNDS = 25

# 上下文压缩配置的范围与默认值（阶段十）：触发比例为百分比整数，
# 保留轮数一轮 = 用户消息及其回复；摘要目标长度是生成目标（估算值，
# 不保证精确等长），运行时还会结合模型可用容量收窄。
COMPACT_THRESHOLD_MIN = 10
COMPACT_THRESHOLD_MAX = 95
DEFAULT_COMPACT_THRESHOLD = 80
COMPACT_KEEP_ROUNDS_MIN = 1
COMPACT_KEEP_ROUNDS_MAX = 50
DEFAULT_COMPACT_KEEP_ROUNDS = 4
COMPACT_SUMMARY_TOKENS_MIN = 100
COMPACT_SUMMARY_TOKENS_MAX = 32000
DEFAULT_COMPACT_SUMMARY_TOKENS = 1000

# 单个 Agent 绑定的同类能力数量上限，防止误传超长列表
BINDING_ITEMS_MAX = 100


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class _CompactionFieldsMixin(CamelModel):
    """上下文压缩配置字段：创建与更新共用同一组约束、默认值与驼峰别名。"""

    auto_compact: bool = True
    compact_threshold: Annotated[
        int, Field(ge=COMPACT_THRESHOLD_MIN, le=COMPACT_THRESHOLD_MAX)
    ] = DEFAULT_COMPACT_THRESHOLD
    compact_keep_rounds: Annotated[
        int, Field(ge=COMPACT_KEEP_ROUNDS_MIN, le=COMPACT_KEEP_ROUNDS_MAX)
    ] = DEFAULT_COMPACT_KEEP_ROUNDS
    compact_summary_tokens: Annotated[
        int, Field(ge=COMPACT_SUMMARY_TOKENS_MIN, le=COMPACT_SUMMARY_TOKENS_MAX)
    ] = DEFAULT_COMPACT_SUMMARY_TOKENS


class AgentCreateRequest(_CompactionFieldsMixin, CamelModel):
    name: Annotated[str, Field(max_length=NAME_MAX_LENGTH)]
    description: Annotated[str, Field(default="", max_length=DESCRIPTION_MAX_LENGTH)] = ""
    system_prompt: Annotated[str, Field(default="", max_length=SYSTEM_PROMPT_MAX_LENGTH)] = ""
    model_id: int
    max_rounds: Annotated[int, Field(ge=MAX_ROUNDS_MIN, le=MAX_ROUNDS_MAX)] = DEFAULT_MAX_ROUNDS
    tool_keys: Annotated[list[Annotated[str, Field(min_length=1, max_length=128)]], Field(max_length=BINDING_ITEMS_MAX)] = []
    skill_dir_names: Annotated[list[Annotated[str, Field(min_length=1, max_length=128)]], Field(max_length=BINDING_ITEMS_MAX)] = []
    mcp_server_ids: Annotated[list[int], Field(max_length=BINDING_ITEMS_MAX)] = []


class AgentUpdateRequest(_CompactionFieldsMixin, CamelModel):
    """全量更新；能力绑定以请求列表为准（不在列表中的既有绑定会被移除）。"""

    name: Annotated[str, Field(max_length=NAME_MAX_LENGTH)]
    description: Annotated[str, Field(max_length=DESCRIPTION_MAX_LENGTH)] = ""
    system_prompt: Annotated[str, Field(default="", max_length=SYSTEM_PROMPT_MAX_LENGTH)] = ""
    model_id: int
    max_rounds: Annotated[int, Field(ge=MAX_ROUNDS_MIN, le=MAX_ROUNDS_MAX)] = DEFAULT_MAX_ROUNDS
    tool_keys: Annotated[list[Annotated[str, Field(min_length=1, max_length=128)]], Field(max_length=BINDING_ITEMS_MAX)] = []
    skill_dir_names: Annotated[list[Annotated[str, Field(min_length=1, max_length=128)]], Field(max_length=BINDING_ITEMS_MAX)] = []
    mcp_server_ids: Annotated[list[int], Field(max_length=BINDING_ITEMS_MAX)] = []


class AgentModelOut(CamelModel):
    """Agent 引用的模型摘要；列表与编辑选择器都用名称和标识展示。"""

    id: int
    display_name: str
    model_identifier: str


class AgentBindingOut(CamelModel):
    """一条能力绑定的展示信息。

    资源已不存在时 name 为 None、enabled 为 None、available 为 False；
    资源被停用时 available 为 False，名称与说明照常返回。
    """

    ref_key: str
    name: str | None = None
    description: str = ""
    enabled: bool | None = None
    available: bool = False


class AgentOut(CamelModel):
    id: int
    name: str
    description: str
    model: AgentModelOut | None = None
    prompt_version: int
    max_rounds: int
    auto_compact: bool
    compact_threshold: int
    compact_keep_rounds: int
    compact_summary_tokens: int
    is_default: bool
    # 绑定数量按实际保存的绑定统计，包含已停用与已失效的能力
    tool_count: int
    skill_count: int
    mcp_count: int
    created_at: datetime
    updated_at: datetime

    @field_serializer("created_at", "updated_at")
    def serialize_datetime(self, value: datetime) -> str:
        # 数据库存的是 naive UTC；补上 UTC 标记，前端 new Date 可正确转本地时间
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()


class AgentDetailOut(AgentOut):
    """Agent 完整配置：编辑表单用，含系统提示词与逐条绑定信息。"""

    system_prompt: str
    tool_bindings: list[AgentBindingOut] = []
    skill_bindings: list[AgentBindingOut] = []
    mcp_bindings: list[AgentBindingOut] = []


# Pydantic 类型/约束错误 → 字段级中文提示（key 为驼峰字段名，与前端表单字段一致）
FIELD_ERROR_MESSAGES: dict[str, str] = {
    "name": "请填写名称",
    "description": "说明不能超过 512 个字符",
    "systemPrompt": "系统提示词不能超过 32000 个字符",
    "modelId": "请选择一个已配置的模型",
    "maxRounds": "最大执行轮数必须为 1–100 的正整数",
    "autoCompact": "自动压缩只能是开启或关闭",
    "compactThreshold": "触发比例必须为 10–95 的整数（百分比）",
    "compactKeepRounds": "保留最近对话轮数必须为 1–50 的正整数",
    "compactSummaryTokens": "摘要目标长度必须为 100–32000 的整数（Token 估算值）",
    "toolKeys": "工具绑定列表格式不正确",
    "skillDirNames": "Skills 绑定列表格式不正确",
    "mcpServerIds": "MCP Server 绑定列表格式不正确",
}
