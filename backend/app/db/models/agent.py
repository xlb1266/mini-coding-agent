"""Agent 管理业务表：Agent 配置、能力绑定与系统提示词版本。

Agent 把模型、系统提示词与三类能力（内置工具、Skill、MCP Server）
组合成面向具体任务的配置。能力绑定保存在 agent_bindings：
- 工具按 tools.key 绑定（执行层按 key 绑定具体实现，删除保护之外的
  工具被删后绑定保留为失效项，由页面提示用户手动移除）；
- Skill 按 skills.dir_name 绑定（目录名是 Skill 的稳定标识，行 id
  在目录删除重建后会变化）；
- MCP Server 按 mcp_servers.id 绑定（被 Agent 引用的 Server 删除被
  阻止，id 在引用期间保持稳定）。

系统提示词只保存当前内容与版本号：首次保存为版本 1，之后仅当提示词
内容变化时递增；版本历史与比较/恢复属于后续阶段。

上下文压缩配置（阶段十）：auto_compact 控制长会话是否自动整理摘要；
compact_threshold 为触发比例（百分比整数，估算输入达到可用输入容量的
该比例时触发）；compact_keep_rounds 为保留最近对话轮数；
compact_summary_tokens 为摘要目标长度（估算 token，运行时还会结合模型
容量上限收窄）。
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base

# 能力绑定类型
CAPABILITY_TOOL = "tool"
CAPABILITY_SKILL = "skill"
CAPABILITY_MCP = "mcp"
CAPABILITY_TYPES = (CAPABILITY_TOOL, CAPABILITY_SKILL, CAPABILITY_MCP)


class Agent(Base):
    """一个面向具体任务的 Agent 配置。"""

    __tablename__ = "agents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    # 系统提示词正文与当前版本号；版本号从 1 开始，仅内容变化时递增
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    prompt_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    model_id: Mapped[int] = mapped_column(
        ForeignKey("model_configs.id"), nullable=False, index=True
    )
    # Agent Loop 的最大执行轮数
    max_rounds: Mapped[int] = mapped_column(Integer, nullable=False)
    # ---- 工作区与审批（模块一）----
    # 工作区目录：空串 = 使用全局默认目录（settings.tool_workspace_dir）；
    # 填写时必须是已存在的绝对路径目录（保存时校验，不自动创建）。
    workspace_dir: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    # 工具调用是否需要人工审批；本列模块一仅落库，审批逻辑在模块二实现
    require_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # ---- 上下文压缩配置（默认开启；关闭后不生成摘要，超容量直接提示） ----
    auto_compact: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # 触发比例：估算输入达到可用输入容量的百分之多少时触发（10–95）
    compact_threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=80)
    # 保留最近对话轮数：一轮为用户消息及其回复（1–50）
    compact_keep_rounds: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    # 摘要目标长度（估算 token，100–32000；运行时结合可用容量收窄）
    compact_summary_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=1000)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    # 引用的模型；模型删除保护保证引用期间模型行不会被删除
    model: Mapped["ModelConfig"] = relationship()
    # 删除 Agent 时由 ORM 级联删除全部能力绑定行
    bindings: Mapped[list["AgentBinding"]] = relationship(
        back_populates="agent",
        cascade="all, delete-orphan",
        order_by="AgentBinding.id",
    )


class AgentBinding(Base):
    """Agent 与一项能力（工具 / Skill / MCP Server）的绑定。"""

    __tablename__ = "agent_bindings"
    __table_args__ = (
        UniqueConstraint("agent_id", "capability_type", "ref_key", name="uq_agent_binding_ref"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[int] = mapped_column(
        ForeignKey("agents.id"), nullable=False, index=True
    )
    capability_type: Mapped[str] = mapped_column(String(16), nullable=False)
    # 工具存 tools.key，Skill 存 dir_name，MCP Server 存十进制 id 字符串
    ref_key: Mapped[str] = mapped_column(String(128), nullable=False)

    agent: Mapped["Agent"] = relationship(back_populates="bindings")
