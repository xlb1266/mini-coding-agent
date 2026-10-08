"""统一 Agent Runtime：Agent Loop 的唯一实现。

聊天、后续评测等调用方都通过 AgentRuntime.run() 发起一次运行：

- 输入：Agent 标识、用户输入、历史消息、会话压缩状态（摘要与边界）、
  运行级能力限制（只能收窄 Agent 已有权限）、取消信号（asyncio.Event）；
- 职责：加载 Agent 配置（模型 / 系统提示词 / 工具与 Skill / MCP 绑定 /
  上下文压缩配置）、组装上下文、每轮模型请求前管理上下文容量（估算、
  自动压缩与备用裁剪）、调用模型、按需执行工具循环（含 load_skill 加载
  Skill 指令）、处理取消与资源清理（MCP 子进程等）、输出统一运行事件；
- 输出：运行事件流（经 on_event 回调逐个送达）与最终 RunResult。

Runtime 不依赖前端页面、HTTP 请求对象或浏览器连接；取消信号由调用方
传入，Runtime 内部把信号置位转换为对运行任务的取消，与任务被直接取消
走同一条收尾路径：发出 run_end（每次运行恰好一次）并清理全部资源。

上下文容量管理（阶段十）：可用输入容量 = 上下文长度 - 输出预留 - 安全
余量；估算输入达到触发比例时，把边界之后、保留窗口之外的较早历史交给
当前模型分批整理成会话摘要（摘要请求不占用执行轮数，也不递归触发压缩），
成功后摘要与边界一致保存到会话；压缩失败或仍超容量时按完整消息组从最
早内容开始备用裁剪（只影响本次请求）。所有 Token 数字均为估算值，与模
型接口返回的实际用量（usage）区分标注。

工具标识与映射规则（避免名称冲突，全部经统一执行入口处理）：
- 内置工具：模型可见名即 tools.key（builtin_ 前缀）；
- MCP 工具：mcp{server_id}_{工具名}（非法字符替换为 _，超长截断，冲突加后缀）；
- Skill 加载：固定为 load_skill，仅当存在已绑定且启用的 Skill 时提供。

事件类型（camelCase 字段，每个事件都带 runId 与运行内递增 seq）：
- run_start / run_end：一次运行的起止；run_start 带开始时间（startedAt），
  run_end 携带最终状态、结束原因、累计的模型调用 / 工具调用（进入执行
  入口）/ 校验拒绝次数、总耗时（durationMs）、首个正文耗时（firstOutputMs，
  无正文为 null）与 Token 用量汇总（tokenUsage；上游从未返回为 null，
  并标注 incomplete 表示统计不完整；cachedTokens 为输入中缓存命中部分，
  已含在 promptTokens 内不重复相加）；
- model_request_start / model_request_end：模型请求起止，用 callId 配对，
  结束事件带状态、耗时与上游实际返回的 Token 用量（未知则为 null）；
  摘要生成请求带 purpose=context_compaction，不占用执行轮数；
- text_delta：正文增量（工具调用前后按序保留，进入下一轮不覆盖不清空）；
- reasoning_delta：思考增量（推理模型经 reasoning_content / reasoning 下发，
  仅用于前端展示思考卡片，不累计进回复正文，也不进入对话上下文）；
- tool_call_start / tool_call_end：工具调用起止（含 Skill 加载，toolType
  标记 builtin / mcp / skill），用调用标识配对；开始事件带整理后的输入
  参数（脱敏、超长截断）与 MCP Server 名称，结束事件带状态（success /
  error / cancelled，取消时也给在执行的工具一个明确终态）、耗时、脱敏
  摘要与整理后的结果文本（脱敏、截断标注，失败时为可读原因而非堆栈）；
- approval_request / approval_result：工具执行审批（模块二，仅当调用方
  提供审批钩子且调用命中审批范围）的请求与决定，用 requestId 标识；
  请求带整理后的参数，决定为 approved / rejected / timeout / cancelled，
  超时与用户拒绝同样把拒绝结果回给模型（不进入执行入口）；
- context_compaction_start / context_compaction_end：自动压缩起止（触发
  原因、压缩前后估算 Token、处理与保留的消息数量、耗时、失败原因）；
- context_trim：备用裁剪（触发原因、前后估算 Token、裁掉与保留的数量）。

日志与事件摘要都经过脱敏（密钥与绝对路径）并截断；完整上下文、Skill
指令正文、会话摘要正文与未处理的工具结果只保存在本次运行的模型上下文
中，不写入日志或展示事件。
"""

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.secret_box import SecretBox, SecretBoxError
from app.db.models.agent import (
    Agent,
    AgentBinding,
    APPROVAL_LEVELS,
    APPROVAL_OFF,
    APPROVAL_STANDARD,
    APPROVAL_STRICT,
    AUTONOMY_MODES,
    AUTONOMY_STAGE_CONFIRM,
    CAPABILITY_MCP,
    CAPABILITY_SKILL,
    CAPABILITY_TOOL,
)
from app.db.models.mcp_server import McpServer
from app.db.models.model_config import ModelConfig
from app.db.models.skill import Skill
from app.db.models.tool import Tool
from app.services.approvals import (
    DECISION_APPROVED,
    DECISION_CANCELLED,
    DECISION_TIMEOUT,
)
from app.services.context_compaction import (
    Capacity,
    CompactionSettings,
    build_summary_batches,
    compaction_instruction,
    compute_capacity,
    effective_summary_target,
    group_history_turns,
    run_tool_group_ranges,
    split_compactable,
    summary_frame_message,
    summary_max_output_tokens,
    summary_request_fixed_tokens,
    summary_request_messages,
)
from app.services.mcp_client import McpSession, McpTestError, redact as redact_secrets
from app.services.model_client import (
    ERROR_TYPE_CONTEXT,
    ERROR_TYPE_FORMAT,
    ERROR_TYPE_SERVER,
    STREAM_TIMEOUT_SECONDS,
    ModelCallError,
    redact,
    stream_chat_completion_events,
)
from app.services.skill_store import (
    INSTRUCTIONS_MAX_LENGTH,
    find_skill_file,
    parse_skill_markdown,
)
from app.services.token_estimation import (
    estimate_messages_tokens,
    estimate_tokens,
)
from app.services.tools import shell_jobs
from app.services.tools.base import ToolContext, ToolExecutionResult
from app.services.tools.executor import execute_tool
from app.services.tools.registry import get_definition

logger = logging.getLogger(__name__)

# ---- 事件类型 ----
RUN_START = "run_start"
MODEL_REQUEST_START = "model_request_start"
TEXT_DELTA = "text_delta"
REASONING_DELTA = "reasoning_delta"
MODEL_REQUEST_END = "model_request_end"
TOOL_CALL_START = "tool_call_start"
TOOL_CALL_END = "tool_call_end"
APPROVAL_REQUEST = "approval_request"
APPROVAL_RESULT = "approval_result"
CONTEXT_COMPACTION_START = "context_compaction_start"
CONTEXT_COMPACTION_END = "context_compaction_end"
CONTEXT_TRIM = "context_trim"
RUN_END = "run_end"

# ---- 运行终态 ----
STATUS_COMPLETED = "completed"
STATUS_ROUNDS_EXHAUSTED = "rounds_exhausted"
STATUS_CANCELLED = "cancelled"
STATUS_ERROR = "error"
# 阶段确认模式（阶段十五）：模型调用 finish_stage 后主动结束本次运行，
# 等待用户验收；与 completed 一样属于"正常收尾"，聊天侧保存完整回复
STATUS_STAGE_COMPLETE = "stage_complete"
# 阶段化开发 v2：模型调用 submit_stage_plan 上报阶段计划后主动结束本次
# 运行，等待用户确认开始；聊天侧据此创建阶段项目
STATUS_PLAN_SUBMITTED = "plan_submitted"

# ---- 结束原因 ----
REASON_MODEL_FINISHED = "model_finished"
REASON_MAX_ROUNDS = "max_rounds"
REASON_CANCELLED = "cancelled"
REASON_MODEL_ERROR = "model_error"
REASON_CONFIG_ERROR = "config_error"
REASON_MCP_START_FAILED = "mcp_start_failed"
REASON_RUNTIME_ERROR = "runtime_error"
REASON_CONTEXT_OVERFLOW = "context_overflow"
REASON_STAGE_FINISHED = "stage_finished"
REASON_PLAN_SUBMITTED = "plan_submitted"

# finish_stage 是 Runtime 在阶段确认模式下提供的收尾工具，不属于 tools 表
FINISH_STAGE_TOOL_NAME = "finish_stage"
# submit_stage_plan 是 Runtime 在阶段确认模式下提供的规划工具（阶段化开发 v2）：
# 只在"从 0 到 1 的开发需求"规划时调用，上报阶段计划后本次运行结束
SUBMIT_STAGE_PLAN_TOOL_NAME = "submit_stage_plan"
# 阶段计划允许的阶段数量范围
PLAN_STAGES_MIN = 2
PLAN_STAGES_MAX = 8
# finish_stage 结果卡片里交接文档内容的展示上限
HANDOFF_CONTENT_MAX_LENGTH = 4000

# ---- 工具类型 ----
TOOL_TYPE_BUILTIN = "builtin"
TOOL_TYPE_MCP = "mcp"
TOOL_TYPE_SKILL = "skill"

# load_skill 是 Runtime 提供的内置能力，不属于 tools 表
LOAD_SKILL_TOOL_NAME = "load_skill"

# 模型请求事件的用途标记：普通对话不携带，摘要生成标记为上下文压缩
PURPOSE_COMPACTION = "context_compaction"

# 一次运行内摘要生成（上下文压缩）的独立限制：不占用执行轮数，
# 但模型调用次数与总耗时都有上限，避免反复压缩无法结束
MAX_COMPACTION_MODEL_CALLS = 8
COMPACTION_TOTAL_TIMEOUT_SECONDS = 180.0

# 模型可见工具名的长度上限（OpenAI function name 规范为 64 字符）
TOOL_NAME_MAX_LENGTH = 64

# 展示事件与日志使用的摘要上限；完整工具结果只进入本次运行的模型上下文
SUMMARY_MAX_LENGTH = 200

# 展示事件里输入参数的整理上限：单值截断长度、集合条数与嵌套深度
ARGUMENT_VALUE_MAX_LENGTH = 500
ARGUMENT_COLLECTION_LIMIT = 50
ARGUMENT_MAX_DEPTH = 6

# 展示事件里整理结果文本的长度上限（超长截断并标注原始长度）
RESULT_DISPLAY_MAX_LENGTH = 4000

EventSink = Callable[[dict[str, Any]], None]

# 轮数达到上限但模型仍在请求工具时的错误归类
ERROR_TYPE_ROUNDS_EXHAUSTED = "rounds_exhausted"

# ---- 展示与持久化文本的路径脱敏 ----
# Windows 盘符绝对路径（D:\a\b.txt）与 Unix 用户目录（/home/u/x、/Users/u/x）
_WINDOWS_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z]:[\\/](?:[^\s\\/:*?\"<>|,:;)\]]+[\\/])*([^\s\\/:*?\"<>|,:;)\]]+)"
)
_UNIX_HOME_RE = re.compile(
    r"(?<![\w./])/(?:home|Users|root)/(?:[^\s/,:;)'\"\]]+/)*([^\s/,:;)'\"\]]+)"
)


def redact_paths(text: str) -> str:
    """把绝对路径替换为「…/<文件名>」：展示事件与持久化记录不含设备完整路径。

    只影响展示与持久化文本；进入模型上下文的工具结果保持原样（执行任务
    需要真实路径）。
    """
    if not text:
        return text
    text = _WINDOWS_PATH_RE.sub(r"…/\1", text)
    return _UNIX_HOME_RE.sub(r"…/\1", text)


def sanitize_display(text: str, secrets: list[str]) -> str:
    """展示/持久化文本的统一脱敏：先密钥后绝对路径，两层都要过。"""
    return redact_paths(redact_secrets(text, secrets))


def _sum_usage(usage_known: list[dict[str, int | None]]) -> dict[str, int | None] | None:
    """合计多次摘要请求上游实际返回的用量；一次都没返回时为 None。

    各项单独求和：只有部分请求返回某项时，只累计已返回的值（前端对
    缺失项显示未知）。cachedTokens 是输入 Token 的子集，不重复相加。
    """
    if not usage_known:
        return None

    def _total(key: str) -> int | None:
        values = [u.get(key) for u in usage_known if isinstance(u.get(key), int)]
        return sum(values) if values else None

    return {
        "promptTokens": _total("promptTokens"),
        "completionTokens": _total("completionTokens"),
        "totalTokens": _total("totalTokens"),
        "cachedTokens": _total("cachedTokens"),
    }


def run_usage_payload(
    usage_known: list[dict[str, int | None]], model_calls: int
) -> dict[str, Any] | None:
    """整次运行的 Token 用量汇总；一次都没返回时为 None（未知，不填 0）。

    incomplete 表示并非全部模型请求都返回了用量（统计不完整）。
    """
    if not usage_known:
        return None
    payload = _sum_usage(usage_known)
    assert payload is not None
    payload["incomplete"] = len(usage_known) < model_calls
    return payload


# ---------------------------------------------------------------------------
# 请求与结果
# ---------------------------------------------------------------------------


@dataclass
class RunLimits:
    """运行级能力限制：各项只能收窄 Agent 已有权限，不能额外授予。

    字段为 None 表示不额外限制；给出集合时与 Agent 绑定取交集，
    max_rounds 只允许把 Agent 配置的轮数调小。
    """

    max_rounds: int | None = None
    tool_keys: set[str] | None = None
    skill_dir_names: set[str] | None = None
    mcp_server_ids: set[int] | None = None


@dataclass
class ApprovalHooks:
    """工具执行审批钩子（模块二）：由需要人工把关的调用方提供。

    - open_request(info)：登记挂起审批请求并等待用户决议，返回
      approved / rejected（info 为 approval_request 事件载荷，含 requestId）；
    - settle_request(request_id, decision)：等待超时或运行取消时把注册表
      里的挂起请求标记终态并清理（幂等）；
    - timeout_seconds：等待上限，超时按拒绝处理。

    评测路径不提供钩子：RuntimeRequest.approval 为 None 时一切照旧。
    """

    timeout_seconds: float
    open_request: Callable[[dict[str, Any]], Awaitable[str]]
    settle_request: Callable[[str, str], None]


@dataclass
class RuntimeRequest:
    """一次运行的全部输入。

    history 为 [{"role", "content", "seq"?}]，按顺序排列，只包含会话
    压缩边界之后的消息（调用方过滤）；seq 缺失时 Runtime 不做边界过滤，
    适用于没有会话摘要机制的调用方（如评测）。

    会话压缩状态：session_summary 为当前有效摘要（空串表示没有），
    summary_boundary_seq 为已压缩边界；压缩成功后 Runtime 通过
    persist_summary(summary, boundary) 把新摘要与边界一致保存到会话，
    未提供回调时压缩结果只保留在本次运行内。

    approval 为审批钩子（见 ApprovalHooks）：提供后，命中审批范围的
    工具调用（Shell 任意命令、文件写入、edit_file）会先请求人工审批。

    workspace_dir 为运行级工作区覆盖（阶段化开发 v2）：项目下的阶段与
    汇总会话创建时把项目钉住的目录写上会话，调用方传入后 Runtime 优先
    使用（文件沙箱根与 Shell 工作目录跟随），保证多会话操作同一工作区；
    空串表示回落 Agent 配置的工作区。
    """

    agent_id: int
    user_input: str
    history: list[dict[str, Any]] = field(default_factory=list)
    limits: RunLimits | None = None
    session_summary: str = ""
    summary_boundary_seq: int | None = None
    persist_summary: Callable[[str, int], None] | None = None
    approval: ApprovalHooks | None = None
    workspace_dir: str = ""


@dataclass
class RunResult:
    """一次运行的最终结果；status 取四种终态之一。

    指标口径（阶段十一）：
    - model_calls：实际发出的模型请求次数（含失败请求与摘要生成请求）；
    - tool_calls：实际进入工具执行入口的次数（校验/权限被拒绝的不计）；
    - rejected_tool_calls：权限或参数校验阶段被拒绝（未执行）的次数；
    - token_usage：各次请求返回用量的汇总（None 表示上游从未返回）；
    - first_output_ms：Run 开始到首段可展示正文的耗时（None 表示无正文）。
    """

    run_id: str
    status: str
    reason: str
    content: str
    error_type: str | None = None
    error_message: str | None = None
    error_hint: str | None = None
    rounds_used: int = 0
    model_calls: int = 0
    tool_calls: int = 0
    rejected_tool_calls: int = 0
    duration_ms: int = 0
    first_output_ms: int | None = None
    token_usage: dict[str, Any] | None = None


class _ConfigError(Exception):
    """运行配置不可用（Agent / 模型 / 密钥 / 环境变量问题）。"""

    def __init__(self, message: str, agent_name: str = "", hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.agent_name = agent_name
        self.hint = hint


# ---------------------------------------------------------------------------
# 运行配置（数据库加载 + 限制收窄）
# ---------------------------------------------------------------------------


@dataclass
class _SkillMeta:
    """进入目录的 Skill：稳定标识（目录名）与展示信息。"""

    dir_name: str
    name: str
    description: str


@dataclass
class _McpServerConfig:
    """进入运行的 MCP Server：连接参数与脱敏用的明文环境变量。"""

    server_id: int
    name: str
    command: str
    args: list[str]
    env: dict[str, str]


@dataclass
class _LoadedConfig:
    """运行开始时从数据库加载并收窄后的完整配置。"""

    agent_id: int
    agent_name: str
    system_prompt: str
    model_base_url: str
    model_identifier: str
    context_length: int
    max_output_tokens: int
    temperature: float
    api_key: str | None
    max_rounds: int
    tool_rows: dict[str, Tool]
    skills: list[_SkillMeta]
    mcp_servers: list[_McpServerConfig]
    compaction: CompactionSettings
    capacity: Capacity
    # ---- 自主档位与执行治理（阶段十五）----
    # stage_confirm=阶段确认（提供 finish_stage）；autonomous=高度自主
    autonomy_mode: str = AUTONOMY_STAGE_CONFIRM
    # 审批档位：off / standard / strict；命中白名单的 Shell 命令免审批
    approval_level: str = APPROVAL_OFF
    # Shell 命令白名单（已解析为前缀列表）；黑名单仍然生效
    shell_whitelist: list[str] = field(default_factory=list)
    # 运行级工具上下文：Agent 配置了工作区目录时构造，未配置为 None
    #（执行入口与 handler 回落到全局默认目录）
    tool_context: ToolContext | None = None


# ---------------------------------------------------------------------------
# 工具注册表（运行内）
# ---------------------------------------------------------------------------


@dataclass
class _ToolEntry:
    """暴露给模型的一个工具：模型可见名 → 实际执行方式的映射。"""

    name: str                 # 模型可见名（运行内全局唯一）
    tool_type: str            # builtin / mcp / skill
    display_name: str         # 展示用名称
    descriptor: dict[str, Any]
    # builtin：tools.key；skill：dir_name；mcp：server_id + remote_name
    ref_key: str = ""
    server_id: int | None = None
    remote_name: str = ""
    server_name: str = ""
    session: McpSession | None = None


@dataclass
class _ToolOutcome:
    """一次工具执行的结果：给模型的完整文本 + 给展示/日志的脱敏摘要。

    display 是展开卡片用的整理后结果文本（脱敏；失败时为可读原因，
    不含程序异常堆栈），经 RESULT_DISPLAY_MAX_LENGTH 截断后下发。
    """

    ok: bool
    context_text: str
    summary: str
    display: str = ""


def _sanitize_tool_name(raw: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "_", raw).strip("_")
    return cleaned or "tool"


def _unique_name(base: str, taken: set[str]) -> str:
    name = base[:TOOL_NAME_MAX_LENGTH]
    suffix = 2
    while name in taken:
        tail = f"_{suffix}"
        name = base[: TOOL_NAME_MAX_LENGTH - len(tail)] + tail
        suffix += 1
    taken.add(name)
    return name


def _builtin_parameters(specs: list) -> dict[str, Any]:
    """把内置工具参数规格转成 OpenAI function 的 JSON Schema。"""
    properties = {
        spec.name: {"type": spec.type, "description": spec.description}
        for spec in specs
    }
    required = [spec.name for spec in specs if spec.required]
    return {"type": "object", "properties": properties, "required": required}


def _builtin_summary(key: str, data: dict[str, Any]) -> str:
    """内置工具成功结果的展示摘要（不含完整输出）。"""
    if key == "builtin_current_time":
        return f"{data.get('datetime', '')}（{data.get('timezone', '')}）"
    if key == "builtin_shell":
        action = str(data.get("action") or "run")
        if action == "list":
            return f"列出后台任务，共 {data.get('totalJobs', 0)} 个"
        if action == "stop":
            return f"停止后台任务 {data.get('taskId', '')}：{data.get('status', '')}"
        if data.get("taskId") and (data.get("autoBackgrounded") or action == "background"):
            label = "后台启动" if action == "background" else "已自动转后台"
            return f"{label}：{data.get('command', '')}（taskId {data.get('taskId')}，运行中）"
        if data.get("timedOut"):
            return f"命令超时被终止：{data.get('command', '')}"
        output = str(data.get("stdout") or data.get("stderr") or "").strip()
        summary = f"命令：{data.get('command', '')}，退出码 {data.get('exitCode')}"
        if output:
            summary += f"，输出：{output}"
        return summary
    if key == "builtin_file":
        detail = f"{data.get('action')} {data.get('path', '')}"
        if data.get("action") == "write":
            detail += f"（{data.get('outcome')}，{data.get('sizeBytes')} 字节）"
        return detail
    if key == "builtin_edit_file":
        return f"编辑 {data.get('path', '')}，替换 {data.get('replacements', 0)} 处"
    if key == "builtin_list_dir":
        detail = f"列出 {data.get('path', '')}，共 {data.get('totalEntries', 0)} 个条目"
        if data.get("recursive"):
            detail += "（递归）"
        if data.get("truncated"):
            detail += "，已截断"
        return detail
    if key == "builtin_grep":
        detail = f"搜索「{data.get('pattern', '')}」，命中 {data.get('totalMatches', 0)} 处"
        files = data.get("filesSearched")
        if isinstance(files, int):
            detail += f"（扫描 {files} 个文件）"
        if data.get("truncated"):
            detail += "，已截断"
        return detail
    return json.dumps(data, ensure_ascii=False)


def _clip_summary(text: str) -> str:
    text = " ".join(text.split())
    return text[:SUMMARY_MAX_LENGTH] + ("…" if len(text) > SUMMARY_MAX_LENGTH else "")


def _clip_text(text: str, limit: int) -> tuple[str, bool]:
    """按长度截断并标注原始长度；未超长时原样返回。"""
    if len(text) <= limit:
        return text, False
    return text[:limit] + f"…（已截断，原始长度 {len(text)} 字符）", True


def _display_arguments(value: Any, secrets: list[str], depth: int = 0) -> Any:
    """把工具参数整理成展示用结构：字符串脱敏并截断，集合限长防大事件。"""
    if isinstance(value, str):
        clipped, _ = _clip_text(sanitize_display(value, secrets), ARGUMENT_VALUE_MAX_LENGTH)
        return clipped
    if value is None or isinstance(value, bool) or isinstance(value, (int, float)):
        return value
    if depth >= ARGUMENT_MAX_DEPTH:
        return "…（层级过深，已省略）"
    if isinstance(value, list):
        items = [_display_arguments(item, secrets, depth + 1) for item in value[:ARGUMENT_COLLECTION_LIMIT]]
        if len(value) > ARGUMENT_COLLECTION_LIMIT:
            items.append(f"…（共 {len(value)} 项，其余省略）")
        return items
    if isinstance(value, dict):
        shown = {
            str(key): _display_arguments(item, secrets, depth + 1)
            for key, item in list(value.items())[:ARGUMENT_COLLECTION_LIMIT]
        }
        if len(value) > ARGUMENT_COLLECTION_LIMIT:
            shown["…"] = f"（共 {len(value)} 项，其余省略）"
        return shown
    return str(value)


def _result_payload(text: str, secrets: list[str]) -> dict[str, Any]:
    """整理结果文本为展示载荷：脱敏（密钥 + 绝对路径）、截断标注（截断时保留原始长度）。"""
    safe = sanitize_display(text, secrets)
    clipped, truncated = _clip_text(safe, RESULT_DISPLAY_MAX_LENGTH)
    return {"text": clipped, "truncated": truncated, "totalLength": len(safe)}


def _stored_args(row: McpServer) -> list[str]:
    try:
        args = json.loads(row.args_json)
    except (json.JSONDecodeError, TypeError):
        return []
    return [str(arg) for arg in args] if isinstance(args, list) else []


def _skill_catalog_section(skills: list[_SkillMeta]) -> str:
    """系统提示词中的 Skill 目录：只给标识、名称与说明，不预置详细指令。"""
    lines = [
        "## 可用 Skills",
        "",
        "当任务符合某个 Skill 的用途时，先调用 load_skill 工具加载该 Skill 的完整指令，"
        "再按照指令继续执行任务；在加载指令之前不要凭空猜测其内容。"
        "可用 Skill 如下（skill_id 为调用 load_skill 时使用的稳定标识）：",
        "",
    ]
    for meta in skills:
        lines.append(f"- skill_id: {meta.dir_name}")
        lines.append(f"  名称：{meta.name}")
        lines.append(f"  说明：{meta.description}")
    return "\n".join(lines)


def parse_shell_whitelist(text: str | None) -> list[str]:
    """解析 Shell 命令白名单：按行 / 逗号分隔的命令前缀，去空去重保持顺序。"""
    if not text:
        return []
    items: list[str] = []
    for chunk in str(text).replace("\r\n", "\n").replace(",", "\n").split("\n"):
        prefix = chunk.strip()
        if prefix and prefix not in items:
            items.append(prefix)
    return items


def command_matches_whitelist(command: str, whitelist: list[str]) -> bool:
    """Shell 命令是否命中白名单：命令（压缩空白、小写）以前缀开头即命中。"""
    normalized = " ".join(command.split()).lower()
    if not normalized:
        return False
    for prefix in whitelist:
        target = " ".join(prefix.split()).lower()
        if target and normalized.startswith(target):
            return True
    return False


def _stage_protocol_section() -> str:
    """阶段确认模式附加到系统提示词的工作流协议（阶段化开发 v2）。

    内容分四节：意图路由（决定走规划还是普通流程）、规划会话（提交
    计划后停等）、阶段会话（交接文档 + finish_stage 收尾）、汇总任务。
    """
    return "\n".join([
        "## 阶段化开发工作流",
        "",
        "### 意图路由（收到任务先判断，再决定怎么处理）",
        "",
        "- 从 0 到 1 的开发需求（新建项目、从零搭建整套系统等）：不要直接动手"
        "写代码。先分析需求，拆解成 2–8 个阶段的开发计划，然后调用"
        " submit_stage_plan 工具提交计划（每个阶段给出名称与目标）。提交后"
        "本次运行结束，等待用户确认；确认后系统会为每个阶段创建独立会话，"
        "逐阶段推进。",
        "- 存量项目的增量开发、Bug 修复、问答与咨询：走普通对话流程，直接"
        "处理任务，不要调用 submit_stage_plan。",
        "- 拿不准是全新开发还是增量改动时：先用一句话向用户确认，再按上面"
        "两条路由处理。",
        "",
        "### 规划会话（提交计划之后）",
        "",
        "submit_stage_plan 调用成功后本次运行结束，等待用户确认计划；"
        "不要开始执行任何阶段的工作，也不要重复提交计划。",
        "",
        "### 阶段会话（按阶段推进开发）",
        "",
        "你按阶段推进开发：一次运行只完成一个阶段，完成后停下等待用户验收，"
        "不要在一个运行里连续完成多个阶段。",
        "",
        "每个阶段按以下顺序收尾：",
        "1. 完成开发与自检（跑测试 / 启动验证等，尽量接近真实验收）；",
        "2. 用文件写入工具在工作区内生成本阶段的交接文档（Markdown），"
        "文件名建议 `handoff-阶段N.md`，内容必须包含六个部分："
        "背景、进度、已做决策、文件位置、下一步、验证方式；",
        "3. 调用 finish_stage 工具（stageName 填阶段名，summary 概述本阶段成果，"
        "handoffPath 填交接文档的相对路径，nextStep 填下一阶段建议），调用后"
        "本次运行结束，交接文档内容会展示给用户；",
        "4. 等待用户验收。用户批准后系统会开新会话进入下一阶段，也可能提出"
        "修改意见，不要自行猜测验收结果。",
        "",
        "### 汇总任务",
        "",
        "汇总项目完成情况时：基于任务里给出的项目计划与各阶段交接信息，"
        "输出整体汇报（做了什么、验证结果、遗留事项与使用说明），"
        "不要修改代码，不要调用 finish_stage 或 submit_stage_plan。",
    ])


@dataclass
class _ContextPlan:
    """一次运行发给模型的消息布局。

    系统消息（Agent 系统提示词 + Skills 目录）、会话摘要与本次用户
    消息是必须保留的内容；压缩只移除 history 中被摘要覆盖的较早轮，
    备用裁剪只按完整消息组从最早内容开始减少 history 与 run_messages
    中已完成的工具交互组。
    """

    system_message: dict[str, Any] | None
    summary_message: dict[str, Any] | None
    # 原始摘要文本（不带包装）；空串表示当前没有摘要
    summary_text: str = ""
    # 已压缩边界：history 中 seq 不大于它的消息已被摘要覆盖
    boundary_seq: int | None = None
    history: list[dict[str, Any]] = field(default_factory=list)
    run_messages: list[dict[str, Any]] = field(default_factory=list)

    def request_messages(self) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        if self.system_message is not None:
            messages.append(self.system_message)
        if self.summary_message is not None:
            messages.append(self.summary_message)
        messages.extend({"role": m["role"], "content": m["content"]} for m in self.history)
        messages.extend(self.run_messages)
        return messages


@dataclass
class _RunState:
    """一次运行的内部状态：事件序号、累计正文、工具注册表与资源。"""

    run_id: str
    on_event: EventSink | None
    agent_name: str = ""
    started: bool = False
    finished: bool = False
    seq: int = 0
    content: str = ""
    round: int = 0
    model_calls: int = 0
    # 工具调用计数口径：tool_calls 只数进入执行入口的；校验被拒绝的单独计
    tool_calls: int = 0
    rejected_tool_calls: int = 0
    # 各次模型请求上游实际返回的用量（仅统计返回了的请求）
    usage_known: list[dict[str, int | None]] = field(default_factory=list)
    # Run 开始的时钟（duration / first_output 都基于它）
    started_monotonic: float = field(default_factory=time.monotonic)
    # 首段可展示正文（非空 text_delta）的耗时；None 表示尚无正文
    first_output_ms: int | None = None
    # 摘要生成（上下文压缩）的运行级预算：次数与累计耗时
    compaction_calls: int = 0
    compaction_time_used: float = 0.0
    compaction_count: int = 0
    config: _LoadedConfig | None = None
    registry: dict[str, _ToolEntry] = field(default_factory=dict)
    sessions: list[McpSession] = field(default_factory=list)
    secrets: list[str] = field(default_factory=list)
    # 工具执行审批钩子（模块二）：None 表示本运行不审批（评测路径或未开启）
    approval: ApprovalHooks | None = None
    approval_count: int = 0
    # 阶段收尾信息（阶段十五）：模型调用 finish_stage 后记录，本次运行以
    # stage_complete 终态结束；结构 {stageName, summary, handoffPath, nextStep}
    stage_result: dict[str, str] | None = None
    # 阶段计划（阶段化开发 v2）：模型调用 submit_stage_plan 后记录，本次
    # 运行以 plan_submitted 终态结束；结构 {"stages": [{"name", "goal"}]}
    plan_result: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------


class AgentRuntime:
    """加载配置、执行 Agent Loop 并输出运行事件的无状态服务。

    实例本身不保存运行状态（状态在每次 run 的局部 _RunState 中），
    可以长期复用，也允许并发运行。
    """

    def __init__(
        self,
        session_factory: sessionmaker,
        secret_box: SecretBox,
        skills_dir: Path,
    ) -> None:
        self._session_factory = session_factory
        self._secret_box = secret_box
        self._skills_dir = skills_dir

    async def run(
        self,
        request: RuntimeRequest,
        cancel_event: asyncio.Event | None = None,
        on_event: EventSink | None = None,
        run_id: str | None = None,
    ) -> RunResult:
        """执行一次运行；cancel_event 置位会尽快中止模型请求、工具与循环。

        run_id 由调用方预生成（运行记录在 Runtime 启动前就要落库）；
        缺省时 Runtime 自行生成。
        """
        state = _RunState(run_id=run_id or uuid.uuid4().hex, on_event=on_event)
        state.approval = request.approval
        run_task = asyncio.current_task()
        watcher: asyncio.Task | None = None
        if cancel_event is not None and run_task is not None:
            watcher = asyncio.create_task(_watch_cancel(cancel_event, run_task))

        try:
            try:
                config = self._load_config(request)
            except _ConfigError as error:
                state.agent_name = error.agent_name
                logger.warning(
                    "运行配置不可用 run_id=%s agent_id=%s: %s", run_id, request.agent_id, error.message
                )
                return self._finish(
                    state,
                    status=STATUS_ERROR,
                    reason=REASON_CONFIG_ERROR,
                    error=ModelCallError(ERROR_TYPE_SERVER, error.message, error.hint or "请检查 Agent 与模型配置"),
                )

            state.agent_name = config.agent_name
            state.config = config
            state.secrets = [config.api_key] if config.api_key else []
            self._emit_start(state)
            logger.info(
                "运行开始 run_id=%s agent_id=%s model=%s rounds=%d tools=%d skills=%d mcp=%d",
                run_id,
                config.agent_id,
                config.model_identifier,
                config.max_rounds,
                len(config.tool_rows),
                len(config.skills),
                len(config.mcp_servers),
            )

            try:
                registry = await self._build_registry(state, config)
            except McpTestError as error:
                logger.warning("MCP Server 启动失败 run_id=%s: %s", run_id, error.message)
                return self._finish(
                    state,
                    status=STATUS_ERROR,
                    reason=REASON_MCP_START_FAILED,
                    error=ModelCallError(error.error_type, error.message, error.hint),
                )

            return await self._run_loop(state, request, config, registry)
        except asyncio.CancelledError:
            logger.info("运行被取消 run_id=%s", run_id)
            return self._finish(state, status=STATUS_CANCELLED, reason=REASON_CANCELLED)
        except Exception:
            logger.exception("运行出现未预期异常 run_id=%s", run_id)
            return self._finish(
                state,
                status=STATUS_ERROR,
                reason=REASON_RUNTIME_ERROR,
                error=ModelCallError(
                    ERROR_TYPE_SERVER,
                    "运行过程中出现未预期的错误",
                    "请重试；若持续出现请查看后端日志",
                ),
            )
        finally:
            if watcher is not None:
                watcher.cancel()
                try:
                    await watcher
                except asyncio.CancelledError:
                    pass
            await self._cleanup(state)

    # ---- 事件与收尾 ----

    def _emit(self, state: _RunState, event: dict[str, Any]) -> None:
        state.seq += 1
        payload = {"runId": state.run_id, "seq": state.seq, **event}
        if state.on_event is None:
            return
        try:
            state.on_event(payload)
        except Exception:
            logger.exception("运行事件回调失败 run_id=%s event=%s", state.run_id, event.get("type"))

    def _emit_start(self, state: _RunState) -> None:
        if state.started:
            return
        state.started = True
        self._emit(
            state,
            {
                "type": RUN_START,
                "agentName": state.agent_name,
                # Run 开始的墙上时间（epoch 毫秒），运行记录用它对齐开始时间
                "startedAt": int(time.time() * 1000),
            },
        )

    def _finish(
        self,
        state: _RunState,
        *,
        status: str,
        reason: str,
        error: ModelCallError | None = None,
    ) -> RunResult:
        self._emit_start(state)
        duration_ms = int((time.monotonic() - state.started_monotonic) * 1000)
        token_usage = run_usage_payload(state.usage_known, state.model_calls)
        result = RunResult(
            run_id=state.run_id,
            status=status,
            reason=reason,
            content=state.content,
            error_type=error.error_type if error else None,
            error_message=error.message if error else None,
            error_hint=error.hint if error else None,
            rounds_used=state.round,
            model_calls=state.model_calls,
            tool_calls=state.tool_calls,
            rejected_tool_calls=state.rejected_tool_calls,
            duration_ms=duration_ms,
            first_output_ms=state.first_output_ms,
            token_usage=token_usage,
        )
        if state.finished:
            return result
        state.finished = True
        # 阶段收尾信息（finish_stage 参数，模型产出）：脱敏后随 run_end 下发
        stage_payload: dict[str, str] | None = None
        if state.stage_result is not None:
            stage_payload = {
                key: sanitize_display(value, state.secrets) for key, value in state.stage_result.items()
            }
        # 阶段计划（submit_stage_plan 参数，模型产出）：脱敏后随 run_end 下发
        plan_payload: dict[str, Any] | None = None
        if state.plan_result is not None:
            plan_payload = {
                "title": sanitize_display(str(state.plan_result.get("title") or ""), state.secrets),
                "stages": [
                    {
                        "name": sanitize_display(str(item.get("name") or ""), state.secrets),
                        "goal": sanitize_display(str(item.get("goal") or ""), state.secrets),
                    }
                    for item in state.plan_result.get("stages", [])
                ]
            }
        self._emit(
            state,
            {
                "type": RUN_END,
                "status": status,
                "reason": reason,
                "roundsUsed": state.round,
                "modelCalls": state.model_calls,
                "toolCalls": state.tool_calls,
                "rejectedToolCalls": state.rejected_tool_calls,
                "durationMs": duration_ms,
                "firstOutputMs": state.first_output_ms,
                "tokenUsage": token_usage,
                "content": state.content,
                "stage": stage_payload,
                "plan": plan_payload,
                "error": {"type": error.error_type, "message": error.message, "hint": error.hint}
                if error
                else None,
            },
        )
        logger.info(
            "运行结束 run_id=%s status=%s reason=%s rounds=%d model_calls=%d tool_calls=%d "
            "rejected=%d duration_ms=%d first_output_ms=%s",
            state.run_id,
            status,
            reason,
            state.round,
            state.model_calls,
            state.tool_calls,
            state.rejected_tool_calls,
            duration_ms,
            state.first_output_ms,
        )
        return result

    async def _cleanup(self, state: _RunState) -> None:
        """关闭本次运行创建的全部 MCP 连接；覆盖所有退出路径，可重复调用。"""
        for session in state.sessions:
            try:
                await asyncio.shield(session.close())
            except asyncio.CancelledError:
                # 收尾期间被再次取消：close 仍在后台继续，不中断其余清理
                logger.warning("清理 MCP 连接时被再次取消 run_id=%s", state.run_id)
            except Exception:
                logger.exception("清理 MCP 连接失败 run_id=%s", state.run_id)
        state.sessions.clear()

    # ---- 配置加载 ----

    def _load_config(self, request: RuntimeRequest) -> _LoadedConfig:
        """加载 Agent 配置并应用运行级限制（只收窄，不授予）。"""
        db = self._session_factory()
        try:
            agent = db.get(Agent, request.agent_id)
            if agent is None:
                raise _ConfigError(f"Agent（id={request.agent_id}）不存在或已被删除")
            model = db.get(ModelConfig, agent.model_id)
            if model is None:
                raise _ConfigError(
                    f"Agent「{agent.name}」配置的模型已被删除",
                    agent_name=agent.name,
                    hint="请先更换模型或切换 Agent",
                )

            api_key: str | None = None
            if model.secret is not None:
                try:
                    api_key = self._secret_box.decrypt(model.secret.key_nonce, model.secret.key_blob)
                except SecretBoxError:
                    raise _ConfigError(
                        "模型 API Key 无法解密",
                        agent_name=agent.name,
                        hint="请到模型管理重新填写密钥",
                    ) from None

            limits = request.limits or RunLimits()

            tool_keys = {b.ref_key for b in agent.bindings if b.capability_type == CAPABILITY_TOOL}
            if limits.tool_keys is not None:
                tool_keys &= set(limits.tool_keys)
            skill_names = {b.ref_key for b in agent.bindings if b.capability_type == CAPABILITY_SKILL}
            if limits.skill_dir_names is not None:
                skill_names &= set(limits.skill_dir_names)
            mcp_ids = {
                int(ref) for ref in (b.ref_key for b in agent.bindings if b.capability_type == CAPABILITY_MCP) if str(ref).isdigit()
            }
            if limits.mcp_server_ids is not None:
                mcp_ids &= set(limits.mcp_server_ids)

            max_rounds = agent.max_rounds
            if limits.max_rounds is not None:
                max_rounds = min(max_rounds, max(1, int(limits.max_rounds)))

            tool_rows: dict[str, Tool] = {}
            if tool_keys:
                rows = (
                    db.query(Tool)
                    .filter(Tool.key.in_(tool_keys), Tool.enabled.is_(True))
                    .order_by(Tool.id)
                    .all()
                )
                tool_rows = {row.key: row for row in rows}

            skills: list[_SkillMeta] = []
            if skill_names:
                for row in (
                    db.query(Skill)
                    .filter(Skill.dir_name.in_(skill_names), Skill.enabled.is_(True))
                    .order_by(Skill.dir_name)
                    .all()
                ):
                    skills.append(_SkillMeta(dir_name=row.dir_name, name=row.name, description=row.description))

            mcp_servers: list[_McpServerConfig] = []
            if mcp_ids:
                for row in (
                    db.query(McpServer)
                    .filter(McpServer.id.in_(mcp_ids), McpServer.enabled.is_(True))
                    .order_by(McpServer.id)
                    .all()
                ):
                    env: dict[str, str] = {}
                    for var in row.env_vars:
                        try:
                            env[var.key] = self._secret_box.decrypt(var.value_nonce, var.value_blob)
                        except SecretBoxError:
                            raise _ConfigError(
                                f"MCP Server「{row.name}」的环境变量无法解密",
                                agent_name=agent.name,
                                hint="请到 MCP 管理重新填写变量值",
                            ) from None
                    mcp_servers.append(
                        _McpServerConfig(
                            server_id=row.id,
                            name=row.name,
                            command=row.command,
                            args=_stored_args(row),
                            env=env,
                        )
                    )

            compaction = CompactionSettings(
                enabled=agent.auto_compact,
                threshold_percent=agent.compact_threshold,
                keep_rounds=agent.compact_keep_rounds,
                summary_target_tokens=agent.compact_summary_tokens,
            )
            # Agent 配置了工作区目录时构造运行级 ToolContext：文件沙箱根与
            # Shell 工作目录跟随 Agent 工作区；未配置为 None，回落全局默认目录。
            # 运行级覆盖（阶段化开发 v2）优先：项目下的会话把项目钉住的目录
            # 写上会话，调用方经 RuntimeRequest.workspace_dir 传入
            workspace_dir = (request.workspace_dir or "").strip() or (agent.workspace_dir or "").strip()
            tool_context = ToolContext(workspace_root=Path(workspace_dir)) if workspace_dir else None
            approval_level = agent.approval_level if agent.approval_level in APPROVAL_LEVELS else APPROVAL_OFF
            autonomy_mode = agent.autonomy_mode if agent.autonomy_mode in AUTONOMY_MODES else AUTONOMY_STAGE_CONFIRM
            return _LoadedConfig(
                agent_id=agent.id,
                agent_name=agent.name,
                system_prompt=agent.system_prompt,
                model_base_url=model.base_url,
                model_identifier=model.model_identifier,
                context_length=model.context_length,
                max_output_tokens=model.max_output_tokens,
                temperature=model.temperature,
                api_key=api_key,
                max_rounds=max_rounds,
                tool_rows=tool_rows,
                skills=skills,
                mcp_servers=mcp_servers,
                compaction=compaction,
                capacity=compute_capacity(compaction, model.context_length, model.max_output_tokens),
                autonomy_mode=autonomy_mode,
                approval_level=approval_level,
                shell_whitelist=parse_shell_whitelist(agent.shell_whitelist),
                tool_context=tool_context,
            )
        finally:
            db.close()

    # ---- 工具注册表 ----

    async def _build_registry(
        self, state: _RunState, config: _LoadedConfig
    ) -> dict[str, _ToolEntry]:
        """构建模型可见的工具注册表；同时启动 MCP 会话并发现其工具。"""
        registry: dict[str, _ToolEntry] = {}
        taken: set[str] = set()

        for key, row in config.tool_rows.items():
            definition = get_definition(key)
            if definition is None:
                logger.warning("跳过没有执行器的内置工具 run_id=%s key=%s", state.run_id, key)
                continue
            name = _unique_name(_sanitize_tool_name(key), taken)
            registry[name] = _ToolEntry(
                name=name,
                tool_type=TOOL_TYPE_BUILTIN,
                display_name=row.name,
                descriptor={
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": definition.description,
                        "parameters": _builtin_parameters(definition.params),
                    },
                },
                ref_key=key,
            )

        if config.skills:
            name = _unique_name(LOAD_SKILL_TOOL_NAME, taken)
            registry[name] = _ToolEntry(
                name=name,
                tool_type=TOOL_TYPE_SKILL,
                display_name="加载 Skill",
                descriptor={
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": (
                            "加载指定 Skill 的完整指令。当任务符合某个 Skill 的用途时，先调用本工具"
                            "加载其完整指令，再严格按照指令执行任务。skill_id 必须使用系统提示词中"
                            "「可用 Skills」列出的稳定标识，不能填写任意文件路径。"
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "skill_id": {
                                    "type": "string",
                                    "description": "Skill 的稳定标识，见系统提示词中的可用 Skills 列表",
                                }
                            },
                            "required": ["skill_id"],
                        },
                    },
                },
            )

        if config.autonomy_mode == AUTONOMY_STAGE_CONFIRM:
            # 阶段确认模式（阶段化开发 v2）：提供 submit_stage_plan 规划工具。
            # 只用于"从 0 到 1 的开发需求"的意图路由；调用后本次运行以
            # plan_submitted 结束，聊天侧据此创建阶段项目，不进入执行入口。
            name = _unique_name(SUBMIT_STAGE_PLAN_TOOL_NAME, taken)
            registry[name] = _ToolEntry(
                name=name,
                tool_type=TOOL_TYPE_BUILTIN,
                display_name="提交阶段计划",
                ref_key=SUBMIT_STAGE_PLAN_TOOL_NAME,
                descriptor={
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": (
                            "提交阶段化开发计划（仅用于从 0 到 1 的开发需求：分析需求后"
                            "先提交计划，不要直接开始写代码）。提交后本次运行结束，等待"
                            "用户确认；存量项目的增量开发与问答不要调用本工具。"
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "title": {
                                    "type": "string",
                                    "description": "项目标题，例如「待办清单 Web 应用」",
                                },
                                "stages": {
                                    "type": "array",
                                    "description": "阶段清单，2–8 项，按实施顺序排列",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "name": {
                                                "type": "string",
                                                "description": "阶段名称，例如「阶段一：项目骨架」",
                                            },
                                            "goal": {
                                                "type": "string",
                                                "description": "该阶段的完成目标（做成什么样算完成）",
                                            },
                                        },
                                        "required": ["name", "goal"],
                                    },
                                },
                            },
                            "required": ["title", "stages"],
                        },
                    },
                },
            )
            # 阶段确认模式（阶段十五）：提供 finish_stage 收尾工具。
            # 调用后本次运行以 stage_complete 结束，等待用户验收，不进入执行入口。
            name = _unique_name(FINISH_STAGE_TOOL_NAME, taken)
            registry[name] = _ToolEntry(
                name=name,
                tool_type=TOOL_TYPE_BUILTIN,
                display_name="结束当前阶段",
                ref_key=FINISH_STAGE_TOOL_NAME,
                descriptor={
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": (
                            "结束当前开发阶段（仅在完成交接文档并自检后调用）。调用后本次运行结束，"
                            "等待用户验收；验收通过后用户会让你继续下一阶段。"
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "stageName": {
                                    "type": "string",
                                    "description": "本阶段名称，例如「阶段一：项目骨架」",
                                },
                                "summary": {
                                    "type": "string",
                                    "description": "本阶段成果概述（做了什么、验证结果）",
                                },
                                "handoffPath": {
                                    "type": "string",
                                    "description": "交接文档在工作区内的相对路径，例如 handoff-阶段一.md",
                                },
                                "nextStep": {
                                    "type": "string",
                                    "description": "下一阶段的建议内容",
                                },
                            },
                            "required": ["stageName", "summary", "handoffPath", "nextStep"],
                        },
                    },
                },
            )

        for server_cfg in config.mcp_servers:
            state.secrets.extend(value for value in server_cfg.env.values() if value)
            session = McpSession(server_cfg.command, server_cfg.args, server_cfg.env)
            state.sessions.append(session)
            raw_tools = await session.start()
            logger.info(
                "MCP Server 已连接 run_id=%s server=%s tools=%d",
                state.run_id,
                server_cfg.name,
                len(raw_tools),
            )
            prefix = f"mcp{server_cfg.server_id}_"
            for item in raw_tools:
                remote_name = str(item.get("name"))
                schema = item.get("inputSchema")
                if not isinstance(schema, dict):
                    schema = {"type": "object", "properties": {}}
                description = item.get("description")
                name = _unique_name(
                    _sanitize_tool_name(prefix + _sanitize_tool_name(remote_name)), taken
                )
                registry[name] = _ToolEntry(
                    name=name,
                    tool_type=TOOL_TYPE_MCP,
                    # 展示名只放工具名；Server 名称单独随事件下发（serverName）
                    display_name=remote_name,
                    descriptor={
                        "type": "function",
                        "function": {
                            "name": name,
                            "description": description if isinstance(description, str) else "",
                            "parameters": schema,
                        },
                    },
                    server_id=server_cfg.server_id,
                    remote_name=remote_name,
                    server_name=server_cfg.name,
                    session=session,
                )

        state.registry = registry
        return registry

    # ---- 主循环 ----

    def _build_context_plan(self, request: RuntimeRequest, config: _LoadedConfig) -> _ContextPlan:
        """构建消息布局：系统消息 + 会话摘要 + 边界之后的历史 + 本次用户消息。

        防御性过滤：即使调用方传入了边界之前的消息（或消息缺少 seq），
        也只让有效消息进入上下文；摘要以背景消息的形式单独一条，不与
        Agent 系统提示词混合。
        """
        system = config.system_prompt
        if config.skills:
            section = _skill_catalog_section(config.skills)
            system = f"{system.rstrip()}\n\n{section}" if system.strip() else section
        if config.autonomy_mode == AUTONOMY_STAGE_CONFIRM:
            # 阶段确认模式：附加阶段化工作流协议（交接文档 + finish_stage 收尾）
            section = _stage_protocol_section()
            system = f"{system.rstrip()}\n\n{section}" if system.strip() else section
        boundary = request.summary_boundary_seq
        history: list[dict[str, Any]] = []
        for item in request.history:
            role = item.get("role")
            content = item.get("content")
            if role not in ("user", "assistant") or not isinstance(content, str) or not content.strip():
                continue
            seq = item.get("seq")
            if isinstance(seq, int) and boundary is not None and seq <= boundary:
                # 已被会话摘要覆盖：原文不再进入上下文
                continue
            history.append({"role": role, "content": content, "seq": seq if isinstance(seq, int) else None})
        return _ContextPlan(
            system_message={"role": "system", "content": system} if system.strip() else None,
            summary_message=summary_frame_message(request.session_summary)
            if request.session_summary.strip()
            else None,
            summary_text=request.session_summary,
            boundary_seq=boundary,
            history=history,
            run_messages=[{"role": "user", "content": request.user_input}],
        )

    async def _run_loop(
        self,
        state: _RunState,
        request: RuntimeRequest,
        config: _LoadedConfig,
        registry: dict[str, _ToolEntry],
    ) -> RunResult:
        plan = self._build_context_plan(request, config)
        tools_payload = [entry.descriptor for entry in registry.values()]

        for round_no in range(1, config.max_rounds + 1):
            state.round = round_no
            call_id = f"mr-{round_no}"
            # 每轮模型请求前管理上下文容量：估算 → 必要时压缩 → 备用裁剪
            overflow = await self._manage_context(
                state, config, request, plan, tools_payload, round_no
            )
            if overflow is not None:
                logger.warning(
                    "上下文容量不足，结束运行 run_id=%s round=%d 估算=%s 可用=%d",
                    state.run_id,
                    round_no,
                    overflow.message,
                    config.capacity.available_input,
                )
                return self._finish(
                    state, status=STATUS_ERROR, reason=REASON_CONTEXT_OVERFLOW, error=overflow
                )
            # 容量管理之后、模型请求之前注入后台任务退出通知
            self._inject_job_notifications(state, config, plan)
            messages = plan.request_messages()
            try:
                turn_text, tool_calls, usage = await self._model_turn(
                    state, config, messages, tools_payload, round_no, call_id
                )
            except ModelCallError as error:
                logger.warning(
                    "模型请求失败 run_id=%s round=%d call_id=%s status=error type=%s",
                    state.run_id,
                    round_no,
                    call_id,
                    error.error_type,
                )
                return self._finish(
                    state, status=STATUS_ERROR, reason=REASON_MODEL_ERROR, error=error
                )

            assistant_message: dict[str, Any] = {"role": "assistant", "content": turn_text}
            if tool_calls:
                assistant_message["tool_calls"] = [
                    {
                        "id": call.get("id") or f"call-{round_no}-{index}",
                        "type": "function",
                        "function": {
                            "name": call.get("name") or "",
                            "arguments": call.get("arguments") or "",
                        },
                    }
                    for index, call in enumerate(tool_calls, start=1)
                ]
            plan.run_messages.append(assistant_message)

            if not tool_calls:
                if not turn_text.strip():
                    return self._finish(
                        state,
                        status=STATUS_ERROR,
                        reason=REASON_MODEL_ERROR,
                        error=ModelCallError(
                            ERROR_TYPE_FORMAT,
                            "模型返回了空回复",
                            "可能是模型服务或参数配置问题，请重试或检查模型设置",
                        ),
                    )
                return self._finish(
                    state, status=STATUS_COMPLETED, reason=REASON_MODEL_FINISHED
                )

            if round_no == config.max_rounds:
                # 不再执行最后一轮的工具请求：没有后续模型轮次能使用其结果
                return self._finish(
                    state,
                    status=STATUS_ROUNDS_EXHAUSTED,
                    reason=REASON_MAX_ROUNDS,
                    error=ModelCallError(
                        ERROR_TYPE_ROUNDS_EXHAUSTED,
                        f"模型连续请求工具，已达到最大执行轮数（{config.max_rounds} 轮），生成已停止",
                        "可在 Agent 管理中调大该 Agent 的最大执行轮数后重试",
                    ),
                )

            for index, call in enumerate(tool_calls, start=1):
                await self._execute_and_record(state, config, plan, round_no, index, call)
                if state.plan_result is not None:
                    # submit_stage_plan 已提交计划（阶段化开发 v2）：本次运行
                    # 以计划已上报结束，等待用户确认开始
                    return self._finish(
                        state, status=STATUS_PLAN_SUBMITTED, reason=REASON_PLAN_SUBMITTED
                    )
                if state.stage_result is not None:
                    # finish_stage 已收尾（阶段十五）：本次运行以阶段完成结束，
                    # 同轮剩余工具调用不再执行，等待用户验收
                    return self._finish(
                        state, status=STATUS_STAGE_COMPLETE, reason=REASON_STAGE_FINISHED
                    )

        # 防御：正常情况下循环内必然提前返回
        return self._finish(
            state,
            status=STATUS_ROUNDS_EXHAUSTED,
            reason=REASON_MAX_ROUNDS,
            error=ModelCallError(
                ERROR_TYPE_ROUNDS_EXHAUSTED,
                f"已达到最大执行轮数（{config.max_rounds} 轮），生成已停止",
                "可在 Agent 管理中调大该 Agent 的最大执行轮数后重试",
            ),
        )

    def _inject_job_notifications(self, state: _RunState, config: _LoadedConfig, plan: _ContextPlan) -> None:
        """后台任务退出通知：每轮模型调用前取走本工作区的待发通知并注入。

        通知以 system 消息进入本轮请求上下文（不落聊天记录），模型无需
        轮询即可知道后台服务崩没崩、退出码是多少。
        """
        tool_context = config.tool_context
        workspace_root = (
            tool_context.workspace_root if tool_context is not None else settings.tool_workspace_dir
        )
        notifications = shell_jobs.SHELL_JOBS.drain_notifications(workspace_root)
        if not notifications:
            return
        lines = "\n".join(
            f"- 任务 {item.get('taskId')}（{item.get('command')}）已退出，"
            f"退出码 {item.get('exitCode')}；输出文件：{item.get('outputFile')}"
            for item in notifications
        )
        plan.run_messages.append(
            {
                "role": "system",
                "content": (
                    "【后台任务通知】以下后台任务状态有变化，请结合当前任务目标"
                    f"决定是否需要查看输出或重新处理：\n{lines}"
                ),
            }
        )
        logger.info(
            "注入后台任务通知 run_id=%s count=%d", state.run_id, len(notifications)
        )

    # ---- 上下文容量管理 ----

    async def _manage_context(
        self,
        state: _RunState,
        config: _LoadedConfig,
        request: RuntimeRequest,
        plan: _ContextPlan,
        tools_payload: list[dict[str, Any]],
        round_no: int,
    ) -> ModelCallError | None:
        """每轮模型请求前的容量管理；返回 None 表示可以继续请求。

        顺序：估算（含系统提示词、Skills、工具定义、摘要、历史与本次
        运行新增消息）→ 达到触发比例时先自动压缩较早历史 → 仍超容量时
        备用裁剪 → 必须保留的内容本身超容量则结束运行。
        """
        capacity = config.capacity
        if capacity.available_input <= 0:
            return ModelCallError(
                ERROR_TYPE_CONTEXT,
                (
                    f"模型上下文长度（{capacity.context_length}）在预留输出"
                    f"（{capacity.max_output_tokens}）与安全余量后没有可用的输入容量"
                ),
                "请调小该模型的最大输出 Token，或更换上下文长度更大的模型",
            )
        estimated = estimate_messages_tokens(plan.request_messages(), tools_payload)
        if estimated < capacity.trigger:
            return None

        # 接近触发阈值：优先压缩较早历史（自动压缩开启且存在可压缩内容）
        if config.compaction.enabled:
            await self._compact_history(
                state, config, request, plan, tools_payload, round_no, estimated
            )
            estimated = estimate_messages_tokens(plan.request_messages(), tools_payload)

        if estimated <= capacity.available_input:
            return None

        if not config.compaction.enabled:
            # 关闭自动压缩：不生成摘要也不静默丢弃历史，明确提示
            return ModelCallError(
                ERROR_TYPE_CONTEXT,
                (
                    f"估算输入约 {estimated} token，已超过模型的可用输入容量"
                    f"（约 {capacity.available_input} token）；当前 Agent 已关闭自动压缩，"
                    "不会丢弃历史消息"
                ),
                "请缩短输入内容、在 Agent 管理中开启自动压缩，或新建会话",
            )

        # 备用裁剪：按完整消息组从最早内容开始减少，只影响本次请求
        estimated = self._fallback_trim(state, plan, capacity, tools_payload, round_no, estimated)
        if estimated <= capacity.available_input:
            return None
        return ModelCallError(
            ERROR_TYPE_CONTEXT,
            (
                f"系统提示词、Skills、工具定义与必须保留的内容合计约 {estimated} token，"
                f"已超过模型的可用输入容量（约 {capacity.available_input} token）"
            ),
            "请精简系统提示词、减少绑定的工具与 Skill 或缩短输入，也可以新建会话",
        )

    async def _compact_history(
        self,
        state: _RunState,
        config: _LoadedConfig,
        request: RuntimeRequest,
        plan: _ContextPlan,
        tools_payload: list[dict[str, Any]],
        round_no: int,
        estimated_before: int,
    ) -> None:
        """把边界之后、保留窗口之外的较早历史分批整理进会话摘要。

        已有摘要作为更新基础，与新增待压缩消息一起生成新摘要；成功后
        摘要与边界一致保存（经 persist_summary 回调），上下文改为"摘要 +
        边界之后的消息"。任何失败都保留上一份有效摘要与边界，由调用方
        决定是否备用裁剪。摘要请求是独立的模型调用，不占用执行轮数，
        也不会递归触发压缩。
        """
        compactable, kept_turns = split_compactable(plan.history, config.compaction.keep_rounds)
        if not compactable:
            return
        # 运行级压缩预算耗尽：不再发起摘要请求，交给备用裁剪
        if (
            state.compaction_calls >= MAX_COMPACTION_MODEL_CALLS
            or state.compaction_time_used >= COMPACTION_TOTAL_TIMEOUT_SECONDS
        ):
            logger.info(
                "压缩预算已用尽，跳过自动压缩 run_id=%s calls=%d time=%.1fs",
                state.run_id,
                state.compaction_calls,
                state.compaction_time_used,
            )
            return

        capacity = config.capacity
        settings = config.compaction
        state.compaction_count += 1
        compaction_id = f"cmp-{state.compaction_count}"
        started = time.monotonic()
        compactable_messages = sum(len(turn) for turn in compactable)

        target = effective_summary_target(settings, capacity)
        instruction = compaction_instruction(target)
        summary_max_tokens = summary_max_output_tokens(target, capacity)
        # 摘要请求自身的容量：固定部分（指令 + 已有摘要 + 收尾）+ 输出预留
        batch_budget = (
            capacity.context_length
            - capacity.safety_margin
            - summary_max_tokens
            - summary_request_fixed_tokens(instruction, plan.summary_text)
        )
        batches = build_summary_batches(compactable, batch_budget)

        self._emit(
            state,
            {
                "type": CONTEXT_COMPACTION_START,
                "round": round_no,
                "compactionId": compaction_id,
                "reason": (
                    f"估算输入约 {estimated_before} token，达到可用输入容量"
                    f"（约 {capacity.available_input} token）的 {settings.threshold_percent}%"
                ),
                "estimatedTokens": estimated_before,
                "thresholdTokens": capacity.trigger,
                "availableTokens": capacity.available_input,
                "compactableMessages": compactable_messages,
                "summaryTokens": estimate_tokens(plan.summary_text),
                "boundarySeq": plan.boundary_seq,
                "summaryTargetTokens": target,
            },
        )
        logger.info(
            "上下文压缩开始 run_id=%s compaction_id=%s 估算输入=%d 触发阈值=%d 可用=%d "
            "待压缩消息=%d 批次=%d",
            state.run_id,
            compaction_id,
            estimated_before,
            capacity.trigger,
            capacity.available_input,
            compactable_messages,
            len(batches),
        )

        summary_base = plan.summary_text
        boundary = plan.boundary_seq
        processed_messages = 0
        processed_turns = 0
        completed_batches = 0
        calls_used = 0
        usage_known: list[dict[str, int]] = []
        status = "success"
        failure: ModelCallError | None = None

        for batch in batches:
            if state.compaction_calls >= MAX_COMPACTION_MODEL_CALLS:
                status = "partial"
                break
            remaining = COMPACTION_TOTAL_TIMEOUT_SECONDS - state.compaction_time_used
            if remaining <= 0:
                status = "partial"
                break
            batch_started = time.monotonic()
            try:
                text, usage = await self._summary_call(
                    state, config, summary_base, batch, instruction, summary_max_tokens,
                    compaction_id, round_no, remaining,
                )
            except asyncio.CancelledError:
                self._emit_compaction_end(
                    state, round_no, compaction_id, "cancelled", started, estimated_before,
                    processed_messages, processed_turns, len(compactable), len(kept_turns),
                    plan, completed_batches, calls_used, usage_known, None, tools_payload,
                )
                raise
            except ModelCallError as error:
                failure = error
                status = "error" if completed_batches == 0 else "partial"
                break
            state.compaction_time_used += time.monotonic() - batch_started
            calls_used += 1
            if usage is not None:
                usage_known.append(usage)
            if not text.strip():
                failure = ModelCallError(
                    ERROR_TYPE_FORMAT,
                    "摘要生成返回了空内容",
                    "本次请求将改用备用裁剪；请重试，若持续出现请检查模型配置",
                )
                status = "error" if completed_batches == 0 else "partial"
                break
            summary_base = text
            # 边界推进到本批最后一条消息；无 seq 的调用方（如评测）不推进
            last_seq = batch[-1][-1]["seq"]
            if isinstance(last_seq, int):
                boundary = last_seq
            processed_messages += sum(len(turn) for turn in batch)
            processed_turns += len(batch)
            completed_batches += 1

        if failure is not None and completed_batches == 0:
            # 没有任何一批成功：保留原摘要与边界，不做持久化
            logger.warning(
                "上下文压缩失败 run_id=%s compaction_id=%s type=%s: %s",
                state.run_id, compaction_id, failure.error_type, failure.message,
            )
            self._emit_compaction_end(
                state, round_no, compaction_id, "error", started, estimated_before,
                0, 0, len(compactable), len(kept_turns), plan, 0, calls_used,
                usage_known, failure, tools_payload,
            )
            return

        # 至少一批成功：摘要与边界一致保存；保存失败则回退到原状态。
        # 边界无法推进（历史消息缺少 seq）或调用方未提供回调时不持久化，
        # 压缩结果只保留在本次运行内。
        if request.persist_summary is not None and boundary is not None:
            try:
                await asyncio.to_thread(request.persist_summary, summary_base, boundary)
            except asyncio.CancelledError:
                self._emit_compaction_end(
                    state, round_no, compaction_id, "cancelled", started, estimated_before,
                    processed_messages, processed_turns, len(compactable), len(kept_turns),
                    plan, completed_batches, calls_used, usage_known, None, tools_payload,
                )
                raise
            except Exception:
                logger.exception("保存会话摘要失败 run_id=%s compaction_id=%s", state.run_id, compaction_id)
                self._emit_compaction_end(
                    state, round_no, compaction_id, "error", started, estimated_before,
                    0, 0, len(compactable), len(kept_turns), plan, 0, calls_used,
                    usage_known,
                    ModelCallError(
                        ERROR_TYPE_SERVER,
                        "会话摘要保存失败",
                        "本次请求将改用备用裁剪；摘要与边界保持上一次的有效值",
                    ),
                    tools_payload,
                )
                return

        # 应用到本次运行的上下文：摘要 + 边界之后的消息
        plan.summary_text = summary_base
        plan.summary_message = summary_frame_message(summary_base)
        plan.boundary_seq = boundary
        plan.history = [m for turn in compactable[processed_turns:] for m in turn] + [
            m for turn in kept_turns for m in turn
        ]
        logger.info(
            "上下文压缩结束 run_id=%s compaction_id=%s status=%s 处理消息=%d 批次=%d 调用=%d 边界=%s",
            state.run_id, compaction_id, status, processed_messages,
            completed_batches, calls_used, boundary,
        )
        self._emit_compaction_end(
            state, round_no, compaction_id, status, started, estimated_before,
            processed_messages, processed_turns, len(compactable), len(kept_turns),
            plan, completed_batches, calls_used, usage_known, failure, tools_payload,
        )

    def _emit_compaction_end(
        self,
        state: _RunState,
        round_no: int,
        compaction_id: str,
        status: str,
        started: float,
        estimated_before: int,
        processed_messages: int,
        processed_turns: int,
        total_compactable_turns: int,
        kept_turn_count: int,
        plan: _ContextPlan,
        completed_batches: int,
        calls_used: int,
        usage_known: list[dict[str, int]],
        failure: ModelCallError | None,
        tools_payload: list[dict[str, Any]],
    ) -> None:
        """发压缩结束事件；数字均为估算口径，摘要正文不进入事件或日志。"""
        kept_messages = len(plan.history) + len(plan.run_messages)
        payload: dict[str, Any] = {
            "type": CONTEXT_COMPACTION_END,
            "round": round_no,
            "compactionId": compaction_id,
            "status": status,
            "durationMs": int((time.monotonic() - started) * 1000),
            "estimatedTokensBefore": estimated_before,
            "estimatedTokensAfter": estimate_messages_tokens(plan.request_messages(), tools_payload),
            "processedMessages": processed_messages,
            "keptMessages": kept_messages,
            "batches": completed_batches,
            "modelCalls": calls_used,
            "summaryTokens": estimate_tokens(plan.summary_text),
            "boundarySeq": plan.boundary_seq,
            "usage": _sum_usage(usage_known),
            "error": {"type": failure.error_type, "message": failure.message, "hint": failure.hint}
            if failure is not None
            else None,
        }
        self._emit(state, payload)

    async def _summary_call(
        self,
        state: _RunState,
        config: _LoadedConfig,
        summary_base: str,
        batch_turns: list[list[dict[str, Any]]],
        instruction: str,
        max_tokens: int,
        compaction_id: str,
        round_no: int,
        timeout_remaining: float,
    ) -> tuple[str, dict[str, int] | None]:
        """发起一次摘要生成的模型请求（真实调用，计入模型调用次数）。

        事件用 purpose=context_compaction 标记用途；不转发正文增量，
        摘要正文不进入展示事件或日志。
        """
        call_no = state.compaction_calls + 1
        call_id = f"{compaction_id}-{call_no}"
        self._emit(
            state,
            {
                "type": MODEL_REQUEST_START,
                "round": round_no,
                "callId": call_id,
                "purpose": PURPOSE_COMPACTION,
            },
        )
        state.model_calls += 1
        state.compaction_calls += 1
        started = time.monotonic()
        text = ""
        usage: dict[str, int] | None = None

        def _end_payload(status: str) -> dict[str, Any]:
            return {
                "type": MODEL_REQUEST_END,
                "round": round_no,
                "callId": call_id,
                "purpose": PURPOSE_COMPACTION,
                "status": status,
                "durationMs": int((time.monotonic() - started) * 1000),
                "usage": usage,
            }

        try:
            async for event in stream_chat_completion_events(
                base_url=config.model_base_url,
                model_identifier=config.model_identifier,
                api_key=config.api_key,
                messages=summary_request_messages(instruction, summary_base, batch_turns),
                max_tokens=max_tokens,
                temperature=config.temperature,
                tools=None,
                timeout=max(5.0, min(STREAM_TIMEOUT_SECONDS, timeout_remaining)),
            ):
                if event["type"] == "text":
                    text += redact(event["text"], config.api_key)
                elif event["type"] == "finish":
                    usage = event.get("usage") if isinstance(event.get("usage"), dict) else None
                    if usage is not None:
                        state.usage_known.append(usage)
        except asyncio.CancelledError:
            self._emit(state, _end_payload("cancelled"))
            raise
        except ModelCallError:
            self._emit(state, _end_payload("error"))
            raise

        self._emit(state, _end_payload("success"))
        return text, usage

    def _fallback_trim(
        self,
        state: _RunState,
        plan: _ContextPlan,
        capacity: Capacity,
        tools_payload: list[dict[str, Any]],
        round_no: int,
        estimated_before: int,
    ) -> int:
        """备用裁剪：容量不足时按完整消息组从最早内容开始减少。

        顺序：先裁最早的历史轮（完整对话组），再裁本次运行中已完成的
        工具交互组（assistant 调用与配对结果整组删除）。系统消息、摘要、
        本次用户消息与尚未配对的交互不会被裁掉。备用裁剪只影响本次
        请求：不更新压缩边界，也不删除会话历史。
        """
        estimated = estimated_before
        dropped_turns = 0
        dropped_groups = 0
        dropped_messages = 0

        while estimated > capacity.available_input and plan.history:
            turns = group_history_turns(plan.history)
            if not turns:
                break
            drop = turns[0]
            plan.history = plan.history[len(drop) :]
            dropped_turns += 1
            dropped_messages += len(drop)
            estimated = estimate_messages_tokens(plan.request_messages(), tools_payload)

        while estimated > capacity.available_input:
            ranges = run_tool_group_ranges(plan.run_messages)
            if not ranges:
                break
            start, end = ranges[0]
            dropped_groups += 1
            dropped_messages += end - start
            del plan.run_messages[start:end]
            estimated = estimate_messages_tokens(plan.request_messages(), tools_payload)

        if dropped_messages == 0:
            return estimated
        self._emit(
            state,
            {
                "type": CONTEXT_TRIM,
                "round": round_no,
                "reason": (
                    "容量不足，已按完整消息组从最早内容开始裁剪"
                    "（仅影响本次请求，不改动会话历史与压缩边界）"
                ),
                "estimatedTokensBefore": estimated_before,
                "estimatedTokensAfter": estimated,
                "availableTokens": capacity.available_input,
                "droppedMessages": dropped_messages,
                "droppedHistoryTurns": dropped_turns,
                "droppedToolGroups": dropped_groups,
                "keptMessages": len(plan.history) + len(plan.run_messages),
            },
        )
        logger.info(
            "备用裁剪 run_id=%s round=%d 估算 %d → %d 裁掉历史轮=%d 工具组=%d 共 %d 条消息",
            state.run_id, round_no, estimated_before, estimated,
            dropped_turns, dropped_groups, dropped_messages,
        )
        return estimated

    async def _model_turn(
        self,
        state: _RunState,
        config: _LoadedConfig,
        messages: list[dict[str, Any]],
        tools_payload: list[dict[str, Any]],
        round_no: int,
        call_id: str,
    ) -> tuple[str, list[dict[str, str]], dict[str, int] | None]:
        """发起一次模型流式请求：转发正文增量，返回整轮文本、工具调用与用量。"""
        self._emit(state, {"type": MODEL_REQUEST_START, "round": round_no, "callId": call_id})
        state.model_calls += 1
        started = time.monotonic()
        turn_text = ""
        tool_calls: list[dict[str, str]] = []
        usage: dict[str, int] | None = None

        def _end_payload(status: str) -> dict[str, Any]:
            duration_ms = int((time.monotonic() - started) * 1000)
            return {
                "type": MODEL_REQUEST_END,
                "round": round_no,
                "callId": call_id,
                "status": status,
                "durationMs": duration_ms,
                "usage": usage,
            }

        try:
            async for event in stream_chat_completion_events(
                base_url=config.model_base_url,
                model_identifier=config.model_identifier,
                api_key=config.api_key,
                messages=messages,
                max_tokens=config.max_output_tokens,
                temperature=config.temperature,
                tools=tools_payload or None,
            ):
                if event["type"] == "text":
                    # 模型可能回显密钥：先脱敏再累计与下发
                    delta = redact(event["text"], config.api_key)
                    if delta and state.first_output_ms is None:
                        # 首段可展示正文：空块与工具参数片段不触发（text 事件只发非空正文）
                        state.first_output_ms = int(
                            (time.monotonic() - state.started_monotonic) * 1000
                        )
                    turn_text += delta
                    state.content += delta
                    self._emit(
                        state,
                        {"type": TEXT_DELTA, "round": round_no, "callId": call_id, "text": delta},
                    )
                elif event["type"] == "reasoning":
                    # 思考增量只做展示，不累计进回复正文与对话上下文
                    reasoning_delta = redact(event["text"], config.api_key)
                    if reasoning_delta:
                        self._emit(
                            state,
                            {
                                "type": REASONING_DELTA,
                                "round": round_no,
                                "callId": call_id,
                                "text": reasoning_delta,
                            },
                        )
                elif event["type"] == "finish":
                    calls = event.get("tool_calls")
                    if isinstance(calls, list):
                        tool_calls = [call for call in calls if isinstance(call, dict)]
                    usage = event.get("usage") if isinstance(event.get("usage"), dict) else None
                    if usage is not None:
                        state.usage_known.append(usage)
        except asyncio.CancelledError:
            self._emit(state, _end_payload("cancelled"))
            raise
        except ModelCallError:
            self._emit(state, _end_payload("error"))
            raise

        payload = _end_payload("success")
        self._emit(state, payload)
        usage_text = (
            f"{usage.get('promptTokens')}/{usage.get('completionTokens')}/{usage.get('totalTokens')}"
            if usage
            else "unknown"
        )
        logger.info(
            "模型请求结束 run_id=%s round=%d call_id=%s status=success duration_ms=%d usage(prompt/completion/total)=%s",
            state.run_id,
            round_no,
            call_id,
            payload["durationMs"],
            usage_text,
        )
        return turn_text, tool_calls, usage

    # ---- 工具执行 ----

    async def _execute_and_record(
        self,
        state: _RunState,
        config: _LoadedConfig,
        plan: _ContextPlan,
        round_no: int,
        index: int,
        call: dict[str, str],
    ) -> None:
        """执行模型请求的一个工具调用：事件配对、权限复检、结果回填上下文。"""
        tc_id = str(call.get("id") or f"call-{round_no}-{index}")
        name = str(call.get("name") or "")
        raw_arguments = str(call.get("arguments") or "")
        entry = state.registry.get(name)
        tool_type = entry.tool_type if entry is not None else "unknown"

        try:
            arguments = json.loads(raw_arguments) if raw_arguments.strip() else {}
            if not isinstance(arguments, dict):
                raise ValueError("not an object")
        except ValueError:
            arguments = None

        self._emit(
            state,
            {
                "type": TOOL_CALL_START,
                "round": round_no,
                "callId": tc_id,
                "toolName": name,
                "toolType": tool_type,
                "displayName": entry.display_name if entry is not None else name,
                "serverName": (entry.server_name or None) if entry is not None else None,
                "arguments": _display_arguments(arguments, state.secrets) if arguments is not None else None,
            },
        )
        started = time.monotonic()

        if entry is None:
            # 未知工具：校验阶段被拒绝，未进入执行入口
            state.rejected_tool_calls += 1
            outcome = _ToolOutcome(
                ok=False,
                context_text=(
                    f"调用被拒绝：工具「{name}」不在当前可用的工具列表中。"
                    "请只使用系统提供的工具，不要自行猜测工具名称。"
                ),
                summary=f"拒绝未知工具：{name}",
                display=f"工具「{name}」不在当前可用的工具列表中，本次调用未执行。",
            )
        elif arguments is None:
            # 参数不是合法 JSON：校验阶段被拒绝，未进入执行入口
            state.rejected_tool_calls += 1
            outcome = _ToolOutcome(
                ok=False,
                context_text=f"调用被拒绝：工具「{name}」的参数不是合法的 JSON 对象，请修正参数后重试。",
                summary=f"参数不是合法 JSON：{name}",
                display="参数不是合法的 JSON 对象，本次调用未执行。",
            )
        else:
            if entry.ref_key == SUBMIT_STAGE_PLAN_TOOL_NAME:
                # 阶段计划工具（阶段化开发 v2）：不进入执行入口、不需要审批，
                # 参数合格即记录计划并结束本次运行
                outcome = self._submit_plan_outcome(state, arguments)
            elif entry.ref_key == FINISH_STAGE_TOOL_NAME:
                # 阶段收尾工具（阶段十五）：不进入执行入口、不需要审批，
                # 参数合格即记录阶段信息（并读取交接文档内容展示）后结束本次运行
                outcome = await self._finish_stage_outcome(state, arguments)
            else:
                try:
                    decision = await self._maybe_request_approval(
                        state, entry, arguments, round_no, tc_id
                    )
                    if decision is not None and decision != DECISION_APPROVED:
                        # 拒绝 / 超时：未进入执行入口，把拒绝结果回给模型
                        state.rejected_tool_calls += 1
                        outcome = self._rejection_outcome(decision, name, state.approval)
                    else:
                        outcome = await self._dispatch(state, config, entry, arguments)
                except asyncio.CancelledError:
                    # 取消时也给卡片一个明确终态，不得停留在“正在执行”
                    self._emit(
                        state,
                        {
                            "type": TOOL_CALL_END,
                            "round": round_no,
                            "callId": tc_id,
                            "toolName": name,
                            "toolType": tool_type,
                            "status": "cancelled",
                            "durationMs": int((time.monotonic() - started) * 1000),
                            "summary": "执行被取消",
                            "result": None,
                        },
                    )
                    raise

        duration_ms = int((time.monotonic() - started) * 1000)
        self._emit(
            state,
            {
                "type": TOOL_CALL_END,
                "round": round_no,
                "callId": tc_id,
                "toolName": name,
                "toolType": tool_type,
                "status": "success" if outcome.ok else "error",
                "durationMs": duration_ms,
                "summary": sanitize_display(outcome.summary, state.secrets),
                "result": _result_payload(outcome.display, state.secrets),
            },
        )
        logger.info(
            "工具调用结束 run_id=%s round=%d call_id=%s tool=%s type=%s status=%s duration_ms=%d summary=%s",
            state.run_id,
            round_no,
            tc_id,
            name,
            tool_type,
            "success" if outcome.ok else "error",
            duration_ms,
            outcome.summary,
        )
        plan.run_messages.append({"role": "tool", "tool_call_id": tc_id, "content": outcome.context_text})

    # ---- 阶段计划与收尾工具（阶段化开发 v2 / 阶段十五） ----

    def _workspace_root(self, state: _RunState) -> Path:
        """本次运行的工作区根目录：项目钉住目录或 Agent 工作区；无则全局默认。"""
        config = state.config
        if config is not None and config.tool_context is not None:
            return config.tool_context.workspace_root
        return settings.tool_workspace_dir

    def _submit_plan_outcome(self, state: _RunState, arguments: dict[str, Any]) -> _ToolOutcome:
        """submit_stage_plan：校验阶段清单后记录计划；本次运行随后以
        plan_submitted 结束。参数不合格按普通失败返回，模型可修正重调。"""
        title = arguments.get("title")
        if not isinstance(title, str) or not title.strip():
            return _ToolOutcome(
                ok=False,
                context_text="调用被拒绝：submit_stage_plan 缺少必填参数 title（非空字符串，项目标题）。请补全后重试。",
                summary="阶段计划参数缺失：title",
                display="缺少必填参数 title，本次调用未生效。",
            )
        stages = arguments.get("stages")
        if not isinstance(stages, list) or not (PLAN_STAGES_MIN <= len(stages) <= PLAN_STAGES_MAX):
            return _ToolOutcome(
                ok=False,
                context_text=(
                    f"调用被拒绝：stages 必须是含 {PLAN_STAGES_MIN}–{PLAN_STAGES_MAX} 个阶段的数组，"
                    "每项为 {\"name\", \"goal\"}（阶段名称与完成目标）。请修正后重试。"
                ),
                summary=f"阶段计划无效：{len(stages) if isinstance(stages, list) else '非数组'} 个阶段",
                display=f"stages 参数无效（需要 {PLAN_STAGES_MIN}–{PLAN_STAGES_MAX} 项），本次调用未生效。",
            )
        cleaned: list[dict[str, str]] = []
        for index, item in enumerate(stages, start=1):
            if not isinstance(item, dict):
                item = {}
            name = item.get("name")
            goal = item.get("goal")
            if not isinstance(name, str) or not name.strip() or not isinstance(goal, str) or not goal.strip():
                return _ToolOutcome(
                    ok=False,
                    context_text=(
                        f"调用被拒绝：stages 第 {index} 项缺少 name 或 goal（非空字符串）。"
                        "请补全每个阶段的名称与目标后重试。"
                    ),
                    summary=f"阶段计划第 {index} 项参数缺失",
                    display=f"第 {index} 个阶段缺少 name 或 goal，本次调用未生效。",
                )
            cleaned.append({"name": name.strip(), "goal": goal.strip()})
        state.plan_result = {"title": title.strip(), "stages": cleaned}
        display = json.dumps(state.plan_result, ensure_ascii=False, indent=2)
        return _ToolOutcome(
            ok=True,
            context_text=(
                f"开发计划「{title.strip()}」已提交，共 {len(cleaned)} 个阶段。"
                "本次运行结束，等待用户确认计划；用户确认后系统会为每个阶段"
                "创建独立会话。不要继续执行任何工具或开始开发。"
            ),
            summary=f"已提交开发计划：{title.strip()}（{len(cleaned)} 个阶段）",
            display=display,
        )

    def _read_handoff_content(self, state: _RunState, handoff_path: str) -> str:
        """读取交接文档内容（结果卡片展示用）；读取失败不阻断收尾。"""
        path = (self._workspace_root(state) / handoff_path).resolve()
        try:
            if not path.is_file():
                return ""
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        return text.strip()

    async def _finish_stage_outcome(self, state: _RunState, arguments: dict[str, Any]) -> _ToolOutcome:
        """finish_stage：校验四个参数后记录阶段信息，并读取工作区内的交接
        文档内容放进结果卡片（阶段化开发 v2）；本次运行随后以 stage_complete
        结束。参数不合格按普通失败返回，模型可修正重调。"""
        values: dict[str, str] = {}
        for key in ("stageName", "summary", "handoffPath", "nextStep"):
            value = arguments.get(key)
            if not isinstance(value, str) or not value.strip():
                return _ToolOutcome(
                    ok=False,
                    context_text=(
                        f"调用被拒绝：finish_stage 缺少必填参数 {key}（非空字符串）。"
                        "请补全阶段名称、成果概述、交接文档路径与下一步建议后重试。"
                    ),
                    summary=f"阶段收尾参数缺失：{key}",
                    display=f"缺少必填参数 {key}，本次调用未生效。",
                )
            values[key] = value.strip()
        # 交接文档内容（结果卡片展示用，不回传模型——文档就是模型刚写的）
        handoff_content = await asyncio.to_thread(
            self._read_handoff_content, state, values["handoffPath"]
        )
        state.stage_result = values
        stage_name = values["stageName"]
        display = json.dumps(values, ensure_ascii=False, indent=2)
        if handoff_content:
            clipped, truncated = _clip_text(handoff_content, HANDOFF_CONTENT_MAX_LENGTH)
            label = "【交接文档内容】" if not truncated else "【交接文档内容（已截断）】"
            display += f"\n\n{label}\n{clipped}"
        return _ToolOutcome(
            ok=True,
            context_text=(
                f"阶段「{stage_name}」已登记完成，本次运行结束，等待用户验收。"
                "不要继续执行任何工具或输出新的计划，等待用户指示。"
            ),
            summary=f"阶段「{stage_name}」完成，等待用户验收",
            display=display,
        )

    # ---- 工具执行审批（模块二 + 阶段十五档位） ----

    def tool_requires_approval(self, state: _RunState, entry: _ToolEntry, arguments: dict[str, Any]) -> bool:
        """本次工具调用是否需要人工审批（黑名单之外的第二层）。

        审批档位（阶段十五）：
        - off：一律不审批；
        - standard：高风险动作审批——Shell 执行（run）/ 后台启动（background）/
          停止任务（stop）需要审批，查列表（list）只读不需要；文件写入与
          edit_file 需要审批；读取、列目录、搜索、时间与 Skill 加载不需要；
        - strict：除时间查询外，全部内置工具调用都需要审批；
        - 命中 Shell 命令白名单的调用免审批（黑名单仍然生效）；finish_stage
          是收尾协议工具，任何档位都不审批；MCP 工具本版不做审批。
        """
        if entry.tool_type != TOOL_TYPE_BUILTIN:
            return False
        if entry.ref_key in (FINISH_STAGE_TOOL_NAME, SUBMIT_STAGE_PLAN_TOOL_NAME):
            # 协议工具（阶段收尾 / 计划提交）：任何档位都不审批
            return False
        config = state.config
        level = config.approval_level if config is not None else APPROVAL_OFF
        if level == APPROVAL_OFF:
            return False
        if entry.ref_key == "builtin_shell":
            if command_matches_whitelist(str(arguments.get("command") or ""), config.shell_whitelist):
                return False
            if level == APPROVAL_STRICT:
                return True
            action = str(arguments.get("action") or "run").strip().lower()
            return action != "list"
        if level == APPROVAL_STRICT and entry.ref_key != "builtin_current_time":
            return True
        if entry.ref_key == "builtin_file":
            return arguments.get("action") == "write"
        if entry.ref_key == "builtin_edit_file":
            return True
        return False

    async def _maybe_request_approval(
        self,
        state: _RunState,
        entry: _ToolEntry,
        arguments: dict[str, Any],
        round_no: int,
        tc_id: str,
    ) -> str | None:
        """命中审批范围时发出 approval_request 并等待用户决议。

        返回决定（approved / rejected / timeout）；None 表示无需审批，
        调用方直接执行。等待超时按拒绝处理；等待期间运行被取消时，
        先把挂起请求与审批事件收尾（cancelled）再向上抛出取消。
        审批事件与工具事件一样进入运行记录，事后可在运行详情回放。
        """
        hooks = state.approval
        if hooks is None or not self.tool_requires_approval(state, entry, arguments):
            return None
        state.approval_count += 1
        request_id = f"apr-{state.approval_count}"
        request_event = {
            "type": APPROVAL_REQUEST,
            "round": round_no,
            "requestId": request_id,
            "callId": tc_id,
            "toolName": entry.name,
            "toolType": entry.tool_type,
            "displayName": entry.display_name,
            "serverName": entry.server_name or None,
            "arguments": _display_arguments(arguments, state.secrets),
        }
        self._emit(state, request_event)
        try:
            decision = await asyncio.wait_for(
                hooks.open_request(dict(request_event)), timeout=hooks.timeout_seconds
            )
        except asyncio.TimeoutError:
            decision = DECISION_TIMEOUT
            hooks.settle_request(request_id, decision)
        except asyncio.CancelledError:
            hooks.settle_request(request_id, DECISION_CANCELLED)
            self._emit(state, self._approval_result_event(entry, round_no, request_id, tc_id, DECISION_CANCELLED))
            raise
        self._emit(state, self._approval_result_event(entry, round_no, request_id, tc_id, decision))
        return decision

    def _approval_result_event(
        self,
        entry: _ToolEntry,
        round_no: int,
        request_id: str,
        tc_id: str,
        decision: str,
    ) -> dict[str, Any]:
        return {
            "type": APPROVAL_RESULT,
            "round": round_no,
            "requestId": request_id,
            "callId": tc_id,
            "toolName": entry.name,
            "toolType": entry.tool_type,
            "decision": decision,
        }

    def _rejection_outcome(
        self, decision: str, name: str, hooks: ApprovalHooks | None
    ) -> _ToolOutcome:
        """拒绝 / 超时的工具结果：明确告知模型被拒，引导调整方案而非原样重试。"""
        if decision == DECISION_TIMEOUT:
            wait_seconds = int(hooks.timeout_seconds) if hooks is not None else 0
            context_text = (
                f"调用被拒绝：工具「{name}」的执行审批等待了 {wait_seconds} 秒无人处理，"
                "已按拒绝处理，本次未执行。请先向用户说明你准备做什么，或调整方案；"
                "不要原样重试同样的调用。"
            )
            summary = f"审批超时：{name}"
            display = f"审批等待超时（{wait_seconds} 秒），本次调用未执行。"
        else:
            context_text = (
                f"调用被拒绝：用户拒绝了工具「{name}」的这次调用，本次未执行。"
                "请理解用户可能不希望这个操作，调整方案后再继续；"
                "不要原样重试同样的调用。"
            )
            summary = f"用户拒绝：{name}"
            display = "用户拒绝了这次工具调用，本次未执行。"
        return _ToolOutcome(ok=False, context_text=context_text, summary=summary, display=display)

    async def _dispatch(
        self, state: _RunState, config: _LoadedConfig, entry: _ToolEntry, arguments: dict[str, Any]
    ) -> _ToolOutcome:
        if entry.tool_type == TOOL_TYPE_BUILTIN:
            return await self._run_builtin(state, entry, arguments)
        if entry.tool_type == TOOL_TYPE_MCP:
            return await self._run_mcp(state, config, entry, arguments)
        return await self._run_load_skill(state, entry, arguments)

    def _failure_outcome(self, message: str, hint: str | None = None, summary: str | None = None) -> _ToolOutcome:
        text = f"工具执行失败：{message}"
        if hint:
            text += f"（提示：{hint}）"
        display = message if not hint else f"{message}\n提示：{hint}"
        return _ToolOutcome(ok=False, context_text=text, summary=summary or _clip_summary(text), display=display)

    async def _run_builtin(
        self, state: _RunState, entry: _ToolEntry, arguments: dict[str, Any]
    ) -> _ToolOutcome:
        """内置工具：线程中复检权限后经统一执行入口 execute_tool 执行。

        权限复检被拒绝的调用未进入执行入口，计入拒绝次数。
        """
        assert state.config is not None
        result, denial = await asyncio.to_thread(
            self._builtin_sync, state.config.agent_id, entry, arguments, state.config.tool_context
        )
        if denial is not None:
            state.rejected_tool_calls += 1
            return self._failure_outcome(denial, summary=_clip_summary(denial))
        assert result is not None
        state.tool_calls += 1
        if result.ok:
            summary = _clip_summary(sanitize_display(_builtin_summary(entry.ref_key, result.data or {}), state.secrets))
            display = json.dumps(result.data or {}, ensure_ascii=False, indent=2, default=str)
            return _ToolOutcome(
                ok=True,
                context_text=json.dumps(result.data, ensure_ascii=False),
                summary=summary,
                display=display,
            )
        text = f"工具执行失败（{result.error_type}）：{result.message}"
        if result.hint:
            text += f"（提示：{result.hint}）"
        if result.fields:
            text += "；参数问题：" + "；".join(f"{k}: {v}" for k, v in result.fields.items())
        display_lines = [result.message or "工具执行失败"]
        if result.hint:
            display_lines.append(f"提示：{result.hint}")
        if result.fields:
            display_lines.append("参数问题：" + "；".join(f"{k}: {v}" for k, v in result.fields.items()))
        return _ToolOutcome(
            ok=False,
            context_text=text,
            summary=_clip_summary(sanitize_display(result.message or "", state.secrets)),
            display="\n".join(display_lines),
        )

    def _builtin_sync(
        self,
        agent_id: int,
        entry: _ToolEntry,
        arguments: dict[str, Any],
        context: ToolContext | None = None,
    ) -> tuple[ToolExecutionResult | None, str | None]:
        """权限复检 + 执行（在线程中运行）。返回 (结果, None) 或 (None, 拒绝原因)。"""
        db = self._session_factory()
        try:
            binding = (
                db.query(AgentBinding)
                .filter(
                    AgentBinding.agent_id == agent_id,
                    AgentBinding.capability_type == CAPABILITY_TOOL,
                    AgentBinding.ref_key == entry.ref_key,
                )
                .first()
            )
            if binding is None:
                return None, "工具未绑定到当前 Agent，拒绝执行"
            row = db.query(Tool).filter(Tool.key == entry.ref_key).first()
        finally:
            db.close()
        if row is None:
            return None, "工具不存在或已被删除"
        if not row.enabled:
            return None, f"工具「{row.name}」已停用，拒绝执行"
        return execute_tool(row, arguments, context), None

    async def _run_mcp(
        self, state: _RunState, config: _LoadedConfig, entry: _ToolEntry, arguments: dict[str, Any]
    ) -> _ToolOutcome:
        """MCP 工具：复检绑定与启用状态后经会话调用，结果脱敏后进入上下文。

        绑定/启用复检被拒绝或连接不可用的调用未进入远程执行，计入拒绝次数。
        """
        assert state.config is not None
        label = f"{entry.server_name}·{entry.remote_name}"
        denial = await asyncio.to_thread(self._mcp_recheck, state.config.agent_id, entry)
        if denial is not None:
            state.rejected_tool_calls += 1
            return self._failure_outcome(denial, summary=_clip_summary(denial))
        session = entry.session
        if session is None:
            state.rejected_tool_calls += 1
            return self._failure_outcome("MCP 连接不可用", summary=f"MCP 连接不可用：{label}")
        state.tool_calls += 1
        try:
            result = await session.call_tool(entry.remote_name, arguments)
        except McpTestError as error:
            message = sanitize_display(error.message, state.secrets)
            return self._failure_outcome(
                f"MCP 工具「{entry.remote_name}」调用失败：{message}",
                hint=error.hint,
                summary=_clip_summary(f"{label}：{message}"),
            )
        text = redact_secrets(str(result.get("text") or ""), state.secrets)
        if result.get("isError"):
            return self._failure_outcome(
                f"MCP 工具「{entry.remote_name}」返回错误：{text}",
                summary=_clip_summary(f"{label}：{text}"),
            )
        return _ToolOutcome(
            ok=True,
            context_text=text,
            summary=_clip_summary(f"{label}：{text}" if text else label),
            display=text,
        )

    def _mcp_recheck(self, agent_id: int, entry: _ToolEntry) -> str | None:
        db = self._session_factory()
        try:
            binding = (
                db.query(AgentBinding)
                .filter(
                    AgentBinding.agent_id == agent_id,
                    AgentBinding.capability_type == CAPABILITY_MCP,
                    AgentBinding.ref_key == str(entry.server_id),
                )
                .first()
            )
            if binding is None:
                return "MCP Server 未绑定到当前 Agent，拒绝调用"
            server = db.get(McpServer, entry.server_id)
        finally:
            db.close()
        if server is None:
            return "MCP Server 不存在或已被删除"
        if not server.enabled:
            return f"MCP Server「{server.name}」已停用，拒绝调用"
        return None

    async def _run_load_skill(
        self, state: _RunState, entry: _ToolEntry, arguments: dict[str, Any]
    ) -> _ToolOutcome:
        """load_skill：执行前再次校验绑定与启用状态，只读取授权 Skill 的指令。

        参数或绑定校验被拒绝的调用未进入执行入口；开始读取文件后（即使
        读取失败）计入已执行。
        """
        skill_id = arguments.get("skill_id")
        if not isinstance(skill_id, str) or not skill_id.strip():
            state.rejected_tool_calls += 1
            return self._failure_outcome(
                "调用被拒绝：load_skill 需要 skill_id 参数（字符串），"
                "请使用系统提示词「可用 Skills」中列出的稳定标识。",
                summary="参数缺失：skill_id",
            )
        skill_id = skill_id.strip()
        assert state.config is not None
        instructions, skill_name, denial, entered = await asyncio.to_thread(
            self._load_skill_sync, state.config.agent_id, skill_id
        )
        if entered:
            state.tool_calls += 1
        else:
            state.rejected_tool_calls += 1
        if denial is not None:
            return self._failure_outcome(denial, summary=_clip_summary(f"加载失败：{denial}"))
        assert instructions is not None
        return _ToolOutcome(
            ok=True,
            context_text=instructions,
            summary=f"已加载 Skill「{skill_name}」（{len(instructions)} 字符），请按指令继续执行任务",
            display=(
                f"已加载 Skill「{skill_name}」的完整指令（{len(instructions)} 字符）。"
                "指令只进入本次运行的模型上下文，不在卡片中展示。"
            ),
        )

    def _load_skill_sync(
        self, agent_id: int, skill_id: str
    ) -> tuple[str | None, str, str | None, bool]:
        """校验并读取 Skill 指令（在线程中运行）。

        返回 (指令, 名称, 错误, 是否已进入执行)：校验（绑定 / 存在 / 启用）
        通过后开始读取文件，即使读取失败也算已进入执行入口。
        """
        db = self._session_factory()
        try:
            binding = (
                db.query(AgentBinding)
                .filter(
                    AgentBinding.agent_id == agent_id,
                    AgentBinding.capability_type == CAPABILITY_SKILL,
                    AgentBinding.ref_key == skill_id,
                )
                .first()
            )
            if binding is None:
                return None, "", "Skill 未绑定到当前 Agent，拒绝加载", False
            row = db.query(Skill).filter(Skill.dir_name == skill_id).first()
        finally:
            db.close()
        if row is None:
            return None, "", "Skill 不存在或已被删除", False
        if not row.enabled:
            return None, "", f"Skill「{row.name}」已停用，拒绝加载", False

        # 校验通过：以下失败均属于执行阶段
        skill_file = find_skill_file(self._skills_dir / row.dir_name)
        if skill_file is None:
            return None, "", f"Skill「{row.name}」的文件缺失（未找到 SKILL.md），请到 Skills 管理检查", True

        try:
            raw = skill_file.read_bytes()
        except OSError:
            return None, "", f"Skill「{row.name}」的文件无法读取，请检查文件权限", True
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                text = raw.decode("gbk")
            except UnicodeDecodeError:
                return None, "", f"Skill「{row.name}」的文件编码无法识别，请使用 UTF-8 编码保存", True

        instructions = parse_skill_markdown(text).body.strip()
        if not instructions:
            return None, "", f"Skill「{row.name}」的指令内容为空", True
        if len(instructions) > INSTRUCTIONS_MAX_LENGTH:
            return None, "", (
                f"Skill「{row.name}」的指令过长（{len(instructions)} 字符，"
                f"上限 {INSTRUCTIONS_MAX_LENGTH}），拒绝加载；请精简指令内容"
            ), True
        return instructions, row.name, None, True


async def _watch_cancel(cancel_event: asyncio.Event, run_task: asyncio.Task) -> None:
    """等待外部取消信号；置位后取消正在运行的任务，统一走取消收尾路径。"""
    try:
        await cancel_event.wait()
    except asyncio.CancelledError:
        return
    run_task.cancel()
