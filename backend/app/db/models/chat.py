"""聊天业务表：会话与消息。

会话（chat_sessions）记录标题与当前选择的 Agent；Agent 被删除时由
删除路由把 agent_id 置空，会话本身与消息都保留，继续发送前需要重新
选择可用 Agent。

消息（chat_messages）按会话内递增的 seq 排序，不依赖时间戳；assistant
消息保存生成时的 agent_id 与 agent_name 快照，后续 Agent 改名或删除
不影响旧回复的归属展示。status 区分完成与未完成（停止/中断时已生成
部分正文会保存为 incomplete），未完成回复不加入后续请求的上下文。

上下文压缩状态（阶段十）保存在会话上：summary_text 为已生成的会话
摘要，summary_boundary_seq 为"已压缩到哪条消息"的边界（消息 seq）。
后续请求使用摘要 + 边界之后的消息；原始消息不被删除或改写，页面仍
展示完整历史。摘要与边界必须一致更新，失败时保留上一份有效值。
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base

# 消息角色与完成状态
ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
MESSAGE_ROLES = (ROLE_USER, ROLE_ASSISTANT)

STATUS_COMPLETED = "completed"
STATUS_INCOMPLETE = "incomplete"
MESSAGE_STATUSES = (STATUS_COMPLETED, STATUS_INCOMPLETE)

class ChatSession(Base):
    """一次多轮对话会话。"""

    __tablename__ = "chat_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(128), nullable=False, default="新会话")
    # 当前选择的 Agent；Agent 删除后置空，会话保留
    agent_id: Mapped[int | None] = mapped_column(
        ForeignKey("agents.id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    # ---- 上下文压缩状态（None 表示尚未压缩过） ----
    # 当前有效的会话摘要（较早对话的整理结果，作为背景进入模型上下文）
    summary_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 已压缩边界：seq 不大于该值的消息已被摘要覆盖，不再原文进入上下文
    summary_boundary_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # ---- 阶段化开发（阶段十五）----
    # True 表示上一阶段已完成（模型调用了 finish_stage），等待用户验收后继续
    awaiting_acceptance: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # 上一阶段的交接摘要 JSON（stageName / summary / handoffPath / nextStep）；无则为 NULL
    stage_summary_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # ---- 阶段化开发 v2（阶段十六）：项目下的会话归属 ----
    # 所属阶段项目；普通会话为 NULL
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("stage_projects.id"), nullable=True, index=True
    )
    # 会话在项目中的角色：plan（规划）/ stage（阶段）/ summary（汇总）；普通会话为 NULL
    project_role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # 本会话负责的项目阶段序号（1-based）；规划与汇总会话为 NULL
    stage_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 项目钉住的工作区目录：项目下的会话创建时继承，Runtime 以本目录优先
    # 执行（保证多会话操作同一工作区）；普通会话为 NULL，跟随 Agent 配置
    workspace_dir: Mapped[str | None] = mapped_column(String(512), nullable=True)

    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="ChatMessage.seq",
    )


class ChatMessage(Base):
    """会话内的一条消息（用户输入或 Agent 回复）。"""

    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("chat_sessions.id"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # assistant 消息生成时的 Agent 快照；改名/删除不影响旧回复展示
    agent_id: Mapped[int | None] = mapped_column(
        ForeignKey("agents.id"), nullable=True, index=True
    )
    agent_name: Mapped[str] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=STATUS_COMPLETED)
    # 会话内稳定顺序号，从 1 递增；读取按 (session_id, seq) 排序
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    # 过程轨迹（正文段 / 思考段 / 工具卡片段交错）的 JSON；纯文字回复为 NULL。
    # 只作展示用：刷新页面后据此恢复思考行与工具卡片，不参与模型上下文。
    timeline_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    session: Mapped["ChatSession"] = relationship(back_populates="messages")
