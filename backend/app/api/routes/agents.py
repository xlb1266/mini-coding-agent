"""Agent 管理路由：列表、详情、新增、编辑、删除、设为默认。

一致性约定：
- 有 Agent 时必须且只有一个默认 Agent（保存第一个 Agent 或当前无默认
  时自动设为默认；切换与删除在同一事务内完成）；
- 系统提示词首次保存为版本 1，之后仅当提示词内容变化时递增；修改名称、
  模型或能力绑定不影响版本号；
- 只有启用的工具、Skills 和 MCP Server 可以新增到 Agent 配置；已绑定
  的能力后来被停用时保留原绑定，编辑时可手动移除，也可保留后保存其他
  修改；绑定数量按实际保存的绑定统计，包含已停用与已失效的能力；
- 删除普通 Agent 需前端确认；删除默认 Agent 且还有其他 Agent 时，必须
  先指定新的默认 Agent（409）。删除 Agent 不阻止其被聊天会话引用：会话
  与历史消息保留，仅解除引用，见 delete_agent。
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, selectinload

from app.db.models.agent import (
    Agent,
    AgentBinding,
    CAPABILITY_MCP,
    CAPABILITY_SKILL,
    CAPABILITY_TOOL,
)
from app.db.models.chat import ChatMessage, ChatSession
from app.db.models.mcp_server import McpServer
from app.db.models.model_config import ModelConfig
from app.db.models.skill import Skill
from app.db.models.tool import Tool
from app.db.session import get_db
from app.schemas.agent import (
    AgentBindingOut,
    AgentCreateRequest,
    AgentDetailOut,
    AgentModelOut,
    AgentOut,
    AgentUpdateRequest,
)

router = APIRouter(prefix="/agents", tags=["agents"])

# 校验失败时返回 {"detail": {"message", "fields"}}，前端据此在字段旁显示原因
FIELD_ERROR_STATUS = 422
DEFAULT_CONFLICT_STATUS = 409


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _field_error(errors: dict[str, str], message: str = "表单校验未通过，请检查填写内容") -> HTTPException:
    return HTTPException(status_code=FIELD_ERROR_STATUS, detail={"message": message, "fields": errors})


def _dedupe(values: list[str]) -> list[str]:
    """去掉重复项并保持原顺序。"""
    return list(dict.fromkeys(values))


def _get_or_404(db: Session, agent_id: int) -> Agent:
    agent = db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent 不存在或已被删除")
    return agent


def _model_out(model: ModelConfig | None) -> AgentModelOut | None:
    if model is None:
        return None
    return AgentModelOut(id=model.id, display_name=model.display_name, model_identifier=model.model_identifier)


def _binding_outs(db: Session, capability_type: str, rows: list[AgentBinding]) -> list[AgentBindingOut]:
    """把绑定行解析为展示信息，逐一核对资源当前的存在与启用状态。"""
    ref_keys = [row.ref_key for row in rows]
    resources: dict[str, Tool | Skill | McpServer] = {}
    if ref_keys:
        if capability_type == CAPABILITY_TOOL:
            found = db.query(Tool).filter(Tool.key.in_(ref_keys)).all()
            resources = {item.key: item for item in found}
        elif capability_type == CAPABILITY_SKILL:
            found = db.query(Skill).filter(Skill.dir_name.in_(ref_keys)).all()
            resources = {item.dir_name: item for item in found}
        else:
            ids = [int(key) for key in ref_keys if key.isdigit()]
            found = db.query(McpServer).filter(McpServer.id.in_(ids)).all() if ids else []
            resources = {str(item.id): item for item in found}

    outs: list[AgentBindingOut] = []
    for row in rows:
        item = resources.get(row.ref_key)
        if item is None:
            # 资源已不存在（如 Skill 目录被手动删除后扫描清理）：保留绑定，
            # 展示为失效项，由用户手动移除
            outs.append(AgentBindingOut(ref_key=row.ref_key, available=False))
        else:
            outs.append(
                AgentBindingOut(
                    ref_key=row.ref_key,
                    name=item.name,
                    description=item.description,
                    enabled=item.enabled,
                    available=item.enabled,
                )
            )
    return outs


def _to_out(agent: Agent) -> AgentOut:
    bindings = agent.bindings
    return AgentOut(
        id=agent.id,
        name=agent.name,
        description=agent.description,
        model=_model_out(agent.model),
        prompt_version=agent.prompt_version,
        max_rounds=agent.max_rounds,
        auto_compact=agent.auto_compact,
        compact_threshold=agent.compact_threshold,
        compact_keep_rounds=agent.compact_keep_rounds,
        compact_summary_tokens=agent.compact_summary_tokens,
        is_default=agent.is_default,
        tool_count=sum(1 for b in bindings if b.capability_type == CAPABILITY_TOOL),
        skill_count=sum(1 for b in bindings if b.capability_type == CAPABILITY_SKILL),
        mcp_count=sum(1 for b in bindings if b.capability_type == CAPABILITY_MCP),
        created_at=agent.created_at,
        updated_at=agent.updated_at,
    )


def _to_detail_out(db: Session, agent: Agent) -> AgentDetailOut:
    by_type = {
        CAPABILITY_TOOL: [],
        CAPABILITY_SKILL: [],
        CAPABILITY_MCP: [],
    }
    for row in agent.bindings:
        if row.capability_type in by_type:
            by_type[row.capability_type].append(row)
    base = _to_out(agent)
    return AgentDetailOut(
        **base.model_dump(),
        system_prompt=agent.system_prompt,
        tool_bindings=_binding_outs(db, CAPABILITY_TOOL, by_type[CAPABILITY_TOOL]),
        skill_bindings=_binding_outs(db, CAPABILITY_SKILL, by_type[CAPABILITY_SKILL]),
        mcp_bindings=_binding_outs(db, CAPABILITY_MCP, by_type[CAPABILITY_MCP]),
    )


def _binding_error(label: str, missing: list[str], disabled_new: list[str]) -> str:
    """合并一类绑定的错误原因；missing 为标识本身，disabled 为资源名称。"""
    parts: list[str] = []
    if missing:
        parts.append(f"不存在或已被删除：{'、'.join(missing)}")
    if disabled_new:
        parts.append(f"只能添加已启用的{label}：{'、'.join(disabled_new)}")
    return "；".join(parts)


def _validate_bindings(
    db: Session,
    tool_keys: list[str],
    skill_dirs: list[str],
    mcp_ids: list[int],
    existing: set[tuple[str, str]],
) -> tuple[dict[str, str], dict[str, list[str]]]:
    """校验能力绑定，返回 (错误, {capability_type: ref_key 列表})。

    已绑定但后来被停用的能力（existing 命中）允许保留；只有新增的绑定
    要求资源处于启用状态。
    """
    errors: dict[str, str] = {}

    tool_rows = db.query(Tool).filter(Tool.key.in_(tool_keys)).all() if tool_keys else []
    tool_map = {item.key: item for item in tool_rows}
    missing = [key for key in tool_keys if key not in tool_map]
    disabled_new = [
        tool_map[key].name
        for key in tool_keys
        if key in tool_map and not tool_map[key].enabled and (CAPABILITY_TOOL, key) not in existing
    ]
    if missing or disabled_new:
        errors["toolKeys"] = _binding_error("工具", missing, disabled_new)

    skill_rows = db.query(Skill).filter(Skill.dir_name.in_(skill_dirs)).all() if skill_dirs else []
    skill_map = {item.dir_name: item for item in skill_rows}
    missing = [key for key in skill_dirs if key not in skill_map]
    disabled_new = [
        skill_map[key].name
        for key in skill_dirs
        if key in skill_map and not skill_map[key].enabled and (CAPABILITY_SKILL, key) not in existing
    ]
    if missing or disabled_new:
        errors["skillDirNames"] = _binding_error("Skill", missing, disabled_new)

    mcp_rows = db.query(McpServer).filter(McpServer.id.in_(mcp_ids)).all() if mcp_ids else []
    mcp_map = {item.id: item for item in mcp_rows}
    missing = [str(i) for i in mcp_ids if i not in mcp_map]
    disabled_new = [
        mcp_map[i].name
        for i in mcp_ids
        if i in mcp_map and not mcp_map[i].enabled and (CAPABILITY_MCP, str(i)) not in existing
    ]
    if missing or disabled_new:
        errors["mcpServerIds"] = _binding_error("MCP Server", missing, disabled_new)

    bindings = {
        CAPABILITY_TOOL: tool_keys,
        CAPABILITY_SKILL: skill_dirs,
        CAPABILITY_MCP: [str(i) for i in mcp_ids],
    }
    return errors, bindings


def _validate_payload(
    db: Session,
    payload: AgentCreateRequest | AgentUpdateRequest,
    agent_id: int | None = None,
) -> tuple[dict[str, str], dict[str, list[str]]]:
    """跨字段与格式校验，返回 (错误, {capability_type: ref_key 列表})。"""
    errors: dict[str, str] = {}

    if not payload.name.strip():
        errors["name"] = "请填写名称"

    if db.get(ModelConfig, payload.model_id) is None:
        errors["modelId"] = "所选模型不存在或已被删除"

    existing: set[tuple[str, str]] = set()
    if agent_id is not None:
        agent = db.get(Agent, agent_id)
        if agent is not None:
            existing = {(b.capability_type, b.ref_key) for b in agent.bindings}

    binding_errors, bindings = _validate_bindings(
        db,
        _dedupe(payload.tool_keys),
        _dedupe(payload.skill_dir_names),
        list(dict.fromkeys(payload.mcp_server_ids)),
        existing,
    )
    errors.update(binding_errors)
    return errors, bindings


def _replace_bindings(agent: Agent, bindings: dict[str, list[str]]) -> None:
    """把绑定集合替换为请求列表：保留未变化的行，删除移除的行，新增缺少的行。

    保留未变化的原行，避免同一主键/唯一约束在单次 flush 中先插后删引发冲突。
    """
    desired = {(cap, key) for cap, keys in bindings.items() for key in keys}
    agent.bindings = [b for b in agent.bindings if (b.capability_type, b.ref_key) in desired]
    kept = {(b.capability_type, b.ref_key) for b in agent.bindings}
    for cap in (CAPABILITY_TOOL, CAPABILITY_SKILL, CAPABILITY_MCP):
        for key in bindings[cap]:
            if (cap, key) not in kept:
                agent.bindings.append(AgentBinding(capability_type=cap, ref_key=key))


@router.get("")
def list_agents(db: Session = Depends(get_db)) -> list[AgentOut]:
    """默认 Agent 排在最前，其余按最近更新倒序。"""
    agents = (
        db.query(Agent)
        .options(selectinload(Agent.bindings), selectinload(Agent.model))
        .order_by(Agent.is_default.desc(), Agent.updated_at.desc())
        .all()
    )
    return [_to_out(agent) for agent in agents]


@router.get("/{agent_id}")
def get_agent(agent_id: int, db: Session = Depends(get_db)) -> AgentDetailOut:
    agent = _get_or_404(db, agent_id)
    return _to_detail_out(db, agent)


@router.post("", status_code=201)
def create_agent(payload: AgentCreateRequest, db: Session = Depends(get_db)) -> AgentOut:
    errors, bindings = _validate_payload(db, payload)
    if errors:
        raise _field_error(errors)

    agent = Agent(
        name=payload.name.strip(),
        description=payload.description.strip(),
        system_prompt=payload.system_prompt,
        prompt_version=1,
        model_id=payload.model_id,
        max_rounds=payload.max_rounds,
        auto_compact=payload.auto_compact,
        compact_threshold=payload.compact_threshold,
        compact_keep_rounds=payload.compact_keep_rounds,
        compact_summary_tokens=payload.compact_summary_tokens,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    _replace_bindings(agent, bindings)

    # 一致性：无默认 Agent 时（包括保存第一个 Agent），自动把新 Agent 设为默认
    has_default = db.query(Agent.id).filter(Agent.is_default.is_(True)).first() is not None
    agent.is_default = not has_default

    db.add(agent)
    db.commit()
    db.refresh(agent)
    return _to_out(agent)


@router.put("/{agent_id}")
def update_agent(
    agent_id: int,
    payload: AgentUpdateRequest,
    db: Session = Depends(get_db),
) -> AgentOut:
    agent = _get_or_404(db, agent_id)

    errors, bindings = _validate_payload(db, payload, agent_id=agent.id)
    if errors:
        raise _field_error(errors)

    # 版本号只在提示词内容变化时递增；改名称、模型或绑定不影响
    if payload.system_prompt != agent.system_prompt:
        agent.prompt_version += 1
        agent.system_prompt = payload.system_prompt
    agent.name = payload.name.strip()
    agent.description = payload.description.strip()
    agent.model_id = payload.model_id
    agent.max_rounds = payload.max_rounds
    agent.auto_compact = payload.auto_compact
    agent.compact_threshold = payload.compact_threshold
    agent.compact_keep_rounds = payload.compact_keep_rounds
    agent.compact_summary_tokens = payload.compact_summary_tokens
    _replace_bindings(agent, bindings)
    agent.updated_at = _utcnow()

    db.commit()
    db.refresh(agent)
    return _to_out(agent)


@router.put("/{agent_id}/default")
def set_default_agent(agent_id: int, db: Session = Depends(get_db)) -> AgentOut:
    agent = _get_or_404(db, agent_id)

    # 同一事务内完成切换，保证任意时刻至多一个默认 Agent
    db.query(Agent).filter(Agent.is_default.is_(True)).update({"is_default": False})
    agent.is_default = True

    db.commit()
    db.refresh(agent)
    return _to_out(agent)


@router.delete("/{agent_id}", status_code=204)
def delete_agent(
    agent_id: int,
    new_default_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
) -> None:
    agent = _get_or_404(db, agent_id)
    others_exist = db.query(Agent.id).filter(Agent.id != agent_id).first() is not None

    new_default: Agent | None = None
    if agent.is_default and others_exist:
        # 删除默认 Agent 且还有其他 Agent 时，必须先指定新的默认 Agent
        if new_default_id is None:
            raise HTTPException(
                status_code=DEFAULT_CONFLICT_STATUS,
                detail="该 Agent 是默认 Agent，请先选择新的默认 Agent 再删除",
            )
        if new_default_id == agent_id:
            raise HTTPException(status_code=FIELD_ERROR_STATUS, detail="新的默认 Agent 不能是被删除的 Agent 本身")
        new_default = db.get(Agent, new_default_id)
        if new_default is None:
            raise HTTPException(status_code=FIELD_ERROR_STATUS, detail="所选的新默认 Agent 不存在")

    # 能力绑定随 ORM 级联删除；聊天引用（会话当前选择与消息归属）只解除
    # 外键，会话与历史消息保留——旧回复继续显示生成时的 Agent 名称快照，
    # 原会话继续发送前需要重新选择可用 Agent；切换默认与删除在同一事务提交
    db.query(ChatSession).filter(ChatSession.agent_id == agent_id).update(
        {"agent_id": None}, synchronize_session=False
    )
    db.query(ChatMessage).filter(ChatMessage.agent_id == agent_id).update(
        {"agent_id": None}, synchronize_session=False
    )
    db.delete(agent)
    if new_default is not None:
        new_default.is_default = True
    db.commit()
