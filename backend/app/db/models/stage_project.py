"""阶段化开发 v2 的项目表：一次从 0 到 1 的开发需求对应一个阶段项目。

项目把规划、各阶段与汇总三类会话串起来：
- plan_json 保存 Agent 上报的阶段清单（[{name, goal, status}]，status 为
  pending / in_progress / done），由 chat_generation 在 plan_submitted /
  stage_complete 终态时维护；
- workspace_dir 是项目钉住的工作区目录：项目下的每个阶段会话与汇总
  会话创建时都继承该目录（写入 chat_sessions.workspace_dir），Runtime
  以会话上的目录优先执行，保证多会话操作同一份代码；
- status 状态机：awaiting_start（计划已确认待开始）→ in_progress（阶段
  推进中）→ awaiting_summary（全部阶段完成待汇总）→ summarizing（汇总
  会话生成中）→ completed（汇总完成）。
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base

# 项目状态
PROJECT_AWAITING_START = "awaiting_start"
PROJECT_IN_PROGRESS = "in_progress"
PROJECT_AWAITING_SUMMARY = "awaiting_summary"
PROJECT_SUMMARIZING = "summarizing"
PROJECT_COMPLETED = "completed"
PROJECT_STATUSES = (
    PROJECT_AWAITING_START,
    PROJECT_IN_PROGRESS,
    PROJECT_AWAITING_SUMMARY,
    PROJECT_SUMMARIZING,
    PROJECT_COMPLETED,
)

# 项目下的会话角色
PROJECT_ROLE_PLAN = "plan"
PROJECT_ROLE_STAGE = "stage"
PROJECT_ROLE_SUMMARY = "summary"
PROJECT_ROLES = (PROJECT_ROLE_PLAN, PROJECT_ROLE_STAGE, PROJECT_ROLE_SUMMARY)


class StageProject(Base):
    """一个阶段化开发项目：阶段清单与推进状态。"""

    __tablename__ = "stage_projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(128), nullable=False, default="开发项目")
    # 规划会话使用的 Agent 快照：后续阶段会话沿用同一 Agent
    agent_id: Mapped[int | None] = mapped_column(
        ForeignKey("agents.id"), nullable=True, index=True
    )
    agent_name: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    # 项目钉住的工作区目录（来自规划会话，代码强制各阶段一致）
    workspace_dir: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    # 阶段清单 JSON：[{"name", "goal", "status"}]
    plan_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    # 当前进行中的阶段序号（1-based；0 表示尚未开始）
    current_stage: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=PROJECT_AWAITING_START)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
