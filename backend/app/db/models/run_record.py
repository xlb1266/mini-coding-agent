"""运行记录业务表：Run 主记录、安全事件与详细载荷（阶段十一）。

一次 Run 对应"用户发送一条消息并启动 Agent"的一次运行（不是整个会话，
也不是每轮模型调用）。Agent Loop 由统一 AgentRuntime 执行，本表保存的
状态与指标全部来自 Runtime 发出的真实运行事件，不另行模拟。

Run 状态与结束原因分开保存：
- status：running（运行中）/ completed（成功）/ partial（部分完成，如
  轮数耗尽）/ cancelled（用户取消）/ failed（模型服务错误或内部异常）；
- finish_reason：Runtime 的结束原因（model_finished / max_rounds /
  cancelled / model_error / config_error / mcp_start_failed /
  runtime_error / context_overflow / interrupted）。
单个工具失败不把 Run 标为失败（Agent 可能继续完成）；模型失败前已输出
的部分正文保存在聊天消息里（未完成状态），Run 仍标记为 failed。

Agent 与模型名称保留运行时快照：agent_name / model_name /
model_identifier 直接来自运行开始时的配置，后续改名或删除不改变历史
记录；agent_id / model_id 仅为关联参考，列表展示不回查这两张表。

Token 用量（prompt/completion/total/cached）为各次模型请求返回用量的
累加，全部为 NULL 表示上游未返回（未知），不填 0。cached 为输入 Token
中缓存命中的部分（OpenAI 口径：已包含在 prompt_tokens 内，不重复相加）。

事件表（agent_run_events）保存脱敏后的安全事件（去掉大段详细载荷），
run_uid + seq 唯一，重复投递不重复插入；详细载荷（工具完整结果、最终
正文片段）单独存放于 agent_run_event_details，读取入口受控、按需加载。
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base

# ---- Run 状态 ----
RUN_STATUS_RUNNING = "running"
RUN_STATUS_COMPLETED = "completed"
RUN_STATUS_PARTIAL = "partial"
RUN_STATUS_CANCELLED = "cancelled"
RUN_STATUS_FAILED = "failed"
RUN_STATUSES = (
    RUN_STATUS_RUNNING,
    RUN_STATUS_COMPLETED,
    RUN_STATUS_PARTIAL,
    RUN_STATUS_CANCELLED,
    RUN_STATUS_FAILED,
)

# 后端重启 / 异常退出导致的遗留终态原因
REASON_INTERRUPTED = "interrupted"

# ---- Run 来源 ----
RUN_SOURCE_CHAT = "chat"
RUN_SOURCE_EVALUATION = "evaluation"
RUN_SOURCES = (RUN_SOURCE_CHAT, RUN_SOURCE_EVALUATION)


class AgentRun(Base):
    """一次 Agent 运行的主记录（运行开始时创建，结束或中断时更新终态）。"""

    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 来源：chat（聊天页发送消息）/ evaluation（评测任务执行用例）
    source: Mapped[str] = mapped_column(String(16), nullable=False, default=RUN_SOURCE_CHAT)
    # Runtime 的运行标识（uuid hex），事件表按它关联
    run_uid: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    # 来源会话；非聊天入口（如后续评测）可为空
    session_id: Mapped[int | None] = mapped_column(
        ForeignKey("chat_sessions.id"), nullable=True, index=True
    )
    # ---- Agent 快照（改名 / 删除不影响历史记录展示） ----
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True, index=True)
    agent_name: Mapped[str] = mapped_column(String(64), nullable=False)
    # ---- 模型快照 ----
    model_id: Mapped[int | None] = mapped_column(
        ForeignKey("model_configs.id"), nullable=True, index=True
    )
    model_name: Mapped[str] = mapped_column(String(64), nullable=False)
    model_identifier: Mapped[str] = mapped_column(String(128), nullable=False)
    # ---- 状态（与结束原因分开保存） ----
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=RUN_STATUS_RUNNING)
    finish_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # 错误摘要（脱敏、截断）；正常完成为 NULL
    error_summary: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # 用户输入预览（脱敏、截断），仅供列表与详情辨认，不保存全文
    user_input_preview: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    # ---- 指标（结束事件一次性写入，不由事件逐条累加） ----
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 实际发出的模型请求次数（含失败请求与摘要生成请求）
    model_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 实际进入工具执行入口的次数（权限 / 参数校验被拒绝的不计入）
    tool_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 权限或参数校验阶段被拒绝的工具调用次数（单独记录）
    rejected_tool_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Token 用量：NULL 表示上游未返回（未知），不填 0
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cached_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 并非全部模型请求都返回了用量（展示为"统计不完整"）
    tokens_incomplete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # 首段可展示正文的耗时（毫秒）；NULL 表示本次运行没有正文输出
    first_output_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class AgentRunEvent(Base):
    """一次运行内的安全事件（Runtime 真实事件，脱敏并剔除大载荷）。"""

    __tablename__ = "agent_run_events"
    __table_args__ = (
        UniqueConstraint("run_uid", "seq", name="uq_agent_run_events_uid_seq"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_uid: Mapped[str] = mapped_column(
        String(32), ForeignKey("agent_runs.run_uid"), nullable=False, index=True
    )
    # 运行内递增序号（Runtime 事件自带），稳定顺序字段
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class AgentRunEventDetail(Base):
    """事件的详细载荷（工具完整结果、最终正文片段）；按需单独加载。"""

    __tablename__ = "agent_run_event_details"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("agent_run_events.id"), nullable=False, unique=True, index=True
    )
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
