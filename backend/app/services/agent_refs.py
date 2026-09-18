"""Agent 引用检查：模型 / Skill / MCP Server 的删除保护共用。

被任意 Agent 引用的资源不允许删除；这里只负责查出引用方的 Agent
名称列表，由各管理路由据此返回 409 与面向用户的提示。引用检查一律
在后端执行，前端禁用按钮只是辅助。
"""

from sqlalchemy.orm import Session

from app.db.models.agent import Agent, AgentBinding, CAPABILITY_MCP, CAPABILITY_SKILL


def agent_names_using_model(db: Session, model_id: int) -> list[str]:
    return [
        name
        for (name,) in db.query(Agent.name).filter(Agent.model_id == model_id).order_by(Agent.id)
    ]


def agent_names_using_skill(db: Session, dir_name: str) -> list[str]:
    return _agent_names_by_binding(db, CAPABILITY_SKILL, dir_name)


def agent_names_using_mcp_server(db: Session, server_id: int) -> list[str]:
    # MCP Server 绑定保存的是十进制 id 字符串，见 app/db/models/agent.py
    return _agent_names_by_binding(db, CAPABILITY_MCP, str(server_id))


def _agent_names_by_binding(db: Session, capability_type: str, ref_key: str) -> list[str]:
    return [
        name
        for (name,) in db.query(Agent.name)
        .join(AgentBinding, AgentBinding.agent_id == Agent.id)
        .filter(
            AgentBinding.capability_type == capability_type,
            AgentBinding.ref_key == ref_key,
        )
        .order_by(Agent.id)
    ]
