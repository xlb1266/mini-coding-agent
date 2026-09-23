"""聊天路由：会话列表、新建、消息、SSE 事件流、停止生成、切换 Agent。

一致性约定：
- 会话摘要接口不返回消息正文；正文只在打开会话与运行事件中下发；
- 发送消息的顺序：先校验（生成中 / Agent 与模型可用 / 密钥可解密 /
  上下文容量），通过后保存用户消息并启动后台运行；Agent Loop 由统一
  的 AgentRuntime 执行，本路由只接收请求与转发事件，不维护模型与工具
  循环，任务与连接解耦；
- 上下文容量为估算口径：可用输入容量 = 上下文长度 - 输出预留 - 安全
  余量。开启自动压缩时超出的历史交给 Runtime 压缩；关闭自动压缩或
  必须保留的内容本身超容量时直接拒绝，不静默丢弃历史。历史只带会话
  压缩边界之后的消息，被摘要覆盖的原文不重复进入上下文；
- 停止生成、关闭页面或浏览器断开会触发取消，已生成的部分正文保存为
  未完成；无运行任务时停止幂等返回 not_running；
- 同一会话生成中禁止重复发送与切换 Agent（409）；
- 工具执行审批（模块二）：开启审批的 Agent 执行高风险工具前经 SSE 下发
  审批请求并挂起等待；决议走 /approvals/{request_id}/decision，查询挂起
  走 /approvals；未开启审批的 Agent 与评测路径不触发审批；
"""

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, sessionmaker

from app.core.secret_box import SecretBox, SecretBoxError, get_secret_box
from app.db.models.agent import Agent
from app.db.models.chat import ChatMessage, ChatSession, ROLE_USER, STATUS_COMPLETED
from app.db.models.model_config import ModelConfig
from app.db.session import get_db, get_session_factory
from app.schemas.chat import (
    DEFAULT_SESSION_TITLE,
    ChatAgentSelectRequest,
    ChatApprovalDecisionRequest,
    ChatApprovalDecisionResultOut,
    ChatApprovalListOut,
    ChatApprovalOut,
    ChatGenerationOut,
    ChatMessageOut,
    ChatMessagesOut,
    ChatSendRequest,
    ChatSendResultOut,
    ChatSessionCreateRequest,
    ChatSessionOut,
    ChatStopResultOut,
)
from app.api.routes.skills import get_skills_dir
from app.services.approvals import approval_registry
from app.services.chat_generation import (
    TERMINAL_EVENTS,
    estimate_context_tokens,
    estimate_tokens,
    generation_manager,
    load_effective_history,
    make_session_title,
    message_to_out,
    next_seq,
    utcnow,
)
from app.services.token_estimation import available_input_tokens

router = APIRouter(prefix="/chat", tags=["chat"])

FIELD_ERROR_STATUS = 422
CONFLICT_STATUS = 409

# SSE 空闲心跳间隔：代理与浏览器都不会因此断开连接
SSE_HEARTBEAT_SECONDS = 15.0


def _get_session_or_404(db: Session, session_id: int) -> ChatSession:
    session = db.get(ChatSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在或已被删除")
    return session


def _agent_names(db: Session, agent_ids: set[int]) -> dict[int, str]:
    """批量取 Agent 名称；Agent 已删除的会话显示为空。"""
    if not agent_ids:
        return {}
    rows = db.query(Agent.id, Agent.name).filter(Agent.id.in_(agent_ids)).all()
    return {agent_id: name for agent_id, name in rows}


def _session_out(db: Session, session: ChatSession) -> ChatSessionOut:
    agent_name: str | None = None
    if session.agent_id is not None:
        agent = db.get(Agent, session.agent_id)
        agent_name = agent.name if agent is not None else None
    return ChatSessionOut(
        id=session.id,
        title=session.title,
        agent_id=session.agent_id,
        agent_name=agent_name,
        is_generating=generation_manager.is_generating(session.id),
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


@router.get("/sessions")
def list_sessions(db: Session = Depends(get_db)) -> list[ChatSessionOut]:
    """会话摘要按最后更新时间倒序；仅查看会话不改变排序。"""
    sessions = db.query(ChatSession).order_by(ChatSession.updated_at.desc(), ChatSession.id.desc()).all()
    agent_names = _agent_names(db, {s.agent_id for s in sessions if s.agent_id is not None})
    generating = generation_manager.generating_session_ids()
    return [
        ChatSessionOut(
            id=s.id,
            title=s.title,
            agent_id=s.agent_id,
            agent_name=agent_names.get(s.agent_id) if s.agent_id is not None else None,
            is_generating=s.id in generating,
            created_at=s.created_at,
            updated_at=s.updated_at,
        )
        for s in sessions
    ]


@router.post("/sessions", status_code=201)
def create_session(
    payload: ChatSessionCreateRequest,
    db: Session = Depends(get_db),
) -> ChatSessionOut:
    """新建会话；不指定 agentId 时使用当前默认 Agent。"""
    agent: Agent | None = None
    if payload.agent_id is not None:
        agent = db.get(Agent, payload.agent_id)
        if agent is None:
            raise HTTPException(
                status_code=FIELD_ERROR_STATUS,
                detail={"message": "所选 Agent 不存在或已被删除", "fields": {"agentId": "所选 Agent 不存在或已被删除"}},
            )
    else:
        agent = db.query(Agent).filter(Agent.is_default.is_(True)).order_by(Agent.id).first()
        if agent is None:
            raise HTTPException(
                status_code=CONFLICT_STATUS,
                detail="还没有可用 Agent，请先到 Agent 管理创建一个 Agent 再开始聊天",
            )

    now = utcnow()
    session = ChatSession(
        title=DEFAULT_SESSION_TITLE,
        agent_id=agent.id,
        created_at=now,
        updated_at=now,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return _session_out(db, session)


@router.get("/sessions/{session_id}/messages")
def get_messages(session_id: int, db: Session = Depends(get_db)) -> ChatMessagesOut:
    """打开会话：摘要 + 全部消息 + 进行中的生成状态（含已生成部分）。"""
    session = _get_session_or_404(db, session_id)
    messages = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.seq)
        .all()
    )
    generation: ChatGenerationOut | None = None
    run = generation_manager.get_run(session_id)
    if run is not None:
        generation = ChatGenerationOut(
            phase=run.phase,
            content=run.content,
            agent_name=run.agent_name,
            tool_name=run.tool_name,
            timeline=[dict(item) for item in run.timeline],
            pending_approvals=[
                approval.payload()
                for approval in approval_registry.pending_for_run(run.run_id)
            ],
        )
    return ChatMessagesOut(
        session=_session_out(db, session),
        messages=[message_to_out(m) for m in messages],
        generation=generation,
    )


@router.put("/sessions/{session_id}/agent")
def select_session_agent(
    session_id: int,
    payload: ChatAgentSelectRequest,
    db: Session = Depends(get_db),
) -> ChatSessionOut:
    """切换会话的 Agent，只影响后续请求；生成中不允许切换。"""
    session = _get_session_or_404(db, session_id)
    if generation_manager.is_generating(session_id):
        raise HTTPException(status_code=CONFLICT_STATUS, detail="当前会话正在生成回复，请等待完成或先停止生成")

    agent = db.get(Agent, payload.agent_id)
    if agent is None:
        raise HTTPException(
            status_code=FIELD_ERROR_STATUS,
            detail={"message": "所选 Agent 不存在或已被删除", "fields": {"agentId": "所选 Agent 不存在或已被删除"}},
        )

    session.agent_id = agent.id
    db.commit()
    db.refresh(session)
    return _session_out(db, session)


@router.post("/sessions/{session_id}/messages", status_code=201)
async def send_message(
    session_id: int,
    payload: ChatSendRequest,
    db: Session = Depends(get_db),
    session_factory: sessionmaker = Depends(get_session_factory),
    box: SecretBox = Depends(get_secret_box),
    skills_dir: Path = Depends(get_skills_dir),
) -> ChatSendResultOut:
    """保存用户消息并启动后台运行；校验失败时不保存、不请求模型。"""
    session = _get_session_or_404(db, session_id)
    content = payload.content.strip()
    if not content:
        raise HTTPException(
            status_code=FIELD_ERROR_STATUS,
            detail={"message": "消息内容不能为空", "fields": {"content": "消息内容不能为空"}},
        )

    if generation_manager.is_generating(session_id):
        raise HTTPException(status_code=CONFLICT_STATUS, detail="当前会话正在生成回复，请等待完成或先停止生成")

    # ---- 请求模型的前置条件全部检查通过后才保存用户消息 ----
    agent: Agent | None = None
    if session.agent_id is not None:
        agent = db.get(Agent, session.agent_id)
    if agent is None:
        raise HTTPException(
            status_code=CONFLICT_STATUS,
            detail="会话当前选择的 Agent 不存在或已被删除，请先选择一个可用 Agent",
        )
    model = db.get(ModelConfig, agent.model_id)
    if model is None:
        raise HTTPException(
            status_code=CONFLICT_STATUS,
            detail=f"Agent「{agent.name}」配置的模型已被删除，请先更换模型或切换 Agent",
        )

    api_key: str | None = None
    if model.secret is not None:
        try:
            api_key = box.decrypt(model.secret.key_nonce, model.secret.key_blob)
        except SecretBoxError:
            raise HTTPException(
                status_code=CONFLICT_STATUS,
                detail="模型 API Key 无法解密，请到模型管理重新填写密钥后再发送",
            ) from None

    # ---- 上下文容量预估（估算值）：通过校验后才保存用户消息 ----
    # 可用输入容量 = 上下文长度 - 输出预留 - 安全余量；开启自动压缩时
    # 超出的历史交给 Runtime 压缩处理，这里只拦下压缩也救不回的情况。
    history_rows = load_effective_history(db, session_id)
    summary_text = session.summary_text or ""
    available = available_input_tokens(model.context_length, model.max_output_tokens)
    summary_tokens = estimate_tokens(summary_text)
    # 必须保留的内容：系统提示词 + 会话摘要 + 本次输入
    base_estimated = (
        estimate_context_tokens(agent.system_prompt, [], content) + summary_tokens
    )
    full_estimated = (
        estimate_context_tokens(agent.system_prompt, history_rows, content) + summary_tokens
    )
    if available <= 0 or base_estimated > available:
        raise HTTPException(
            status_code=CONFLICT_STATUS,
            detail=(
                "系统提示词与本次输入的估算长度已超过模型的上下文容量"
                "（可用输入容量不足），请缩短内容或更换上下文长度更大的模型"
            ),
        )
    if full_estimated > available and not agent.auto_compact:
        raise HTTPException(
            status_code=CONFLICT_STATUS,
            detail=(
                "历史消息过长，已超过模型的上下文容量：请缩短输入内容、"
                "在 Agent 管理中开启自动压缩，或新建会话"
            ),
        )

    # ---- 保存用户消息（首条用户消息同时生成会话标题） ----
    now = utcnow()
    user_message = ChatMessage(
        session_id=session_id,
        role=ROLE_USER,
        content=content,
        status=STATUS_COMPLETED,
        seq=next_seq(db, session_id),
        created_at=now,
    )
    db.add(user_message)
    if session.title == DEFAULT_SESSION_TITLE:
        session.title = make_session_title(content)
    session.updated_at = now
    db.commit()
    db.refresh(user_message)
    db.refresh(session)

    # ---- 启动后台运行（上下文含刚保存的这条用户消息，只计一次）----
    # 历史只带压缩边界之后的消息；被摘要覆盖的原文不再重复进入上下文
    boundary = session.summary_boundary_seq
    history = [
        {"role": item.role, "content": item.content, "seq": item.seq}
        for item in history_rows
        if boundary is None or item.seq > boundary
    ]
    generation_manager.start(
        session_id=session_id,
        agent_id=agent.id,
        agent_name=agent.name,
        user_input=content,
        history=history,
        api_key=api_key,
        session_factory=session_factory,
        secret_box=box,
        skills_dir=skills_dir,
        session_summary=summary_text,
        summary_boundary=boundary,
        model_id=model.id,
        model_name=model.display_name,
        model_identifier=model.model_identifier,
        require_approval=agent.require_approval,
    )

    return ChatSendResultOut(
        session=_session_out(db, session),
        user_message=message_to_out(user_message),
        generation=ChatGenerationOut(phase="thinking", content="", agent_name=agent.name),
    )


@router.post("/sessions/{session_id}/stop")
async def stop_generation(session_id: int, db: Session = Depends(get_db)) -> ChatStopResultOut:
    """停止当前会话的运行；等待收尾后返回结果。"""
    _get_session_or_404(db, session_id)
    outcome = await generation_manager.stop(session_id)
    if outcome is None:
        return ChatStopResultOut(outcome="not_running")

    status = outcome.get("status")
    result = ChatStopResultOut(
        outcome={"completed": "done", "cancelled": "cancelled"}.get(status, "error"),
        content=outcome.get("content", ""),
    )
    message = outcome.get("message")
    if isinstance(message, dict):
        result.message = ChatMessageOut.model_validate(message)
    error = outcome.get("error")
    if isinstance(error, dict):
        result.error_message = error.get("message")
        result.hint = error.get("hint")
        result.error_type = error.get("type")
    return result


@router.get("/sessions/{session_id}/approvals")
def list_pending_approvals(
    session_id: int, db: Session = Depends(get_db)
) -> ChatApprovalListOut:
    """查询当前挂起的工具执行审批请求；没有生成任务时返回空列表。"""
    _get_session_or_404(db, session_id)
    approvals = [
        ChatApprovalOut(
            request_id=approval.request_id,
            tool_name=approval.tool_name,
            display_name=approval.display_name,
            tool_type=approval.tool_type,
            arguments=approval.arguments,
            created_at=approval.created_at,
        )
        for approval in approval_registry.pending_for_session(session_id)
    ]
    return ChatApprovalListOut(approvals=approvals)


@router.post("/sessions/{session_id}/approvals/{request_id}/decision")
async def decide_approval(
    session_id: int,
    request_id: str,
    payload: ChatApprovalDecisionRequest,
    db: Session = Depends(get_db),
) -> ChatApprovalDecisionResultOut:
    """对一次挂起的审批做出批准/拒绝决议，Runtime 随即继续或把拒绝回给模型。

    只有会话处于生成中才允许决议：会话不存在 404，未在生成 409，
    审批请求不存在或已被处理 404。
    """
    _get_session_or_404(db, session_id)
    if not generation_manager.is_generating(session_id):
        raise HTTPException(status_code=CONFLICT_STATUS, detail="当前会话没有进行中的生成任务，无需审批")
    if payload.action not in ("approve", "reject"):
        raise HTTPException(
            status_code=FIELD_ERROR_STATUS,
            detail={"message": "审批决议只能是 approve 或 reject", "fields": {"action": "审批决议只能是 approve 或 reject"}},
        )
    approval = approval_registry.get(request_id)
    if approval is None or approval.session_id != session_id:
        raise HTTPException(status_code=404, detail="审批请求不存在或已被处理")
    approval_registry.resolve(request_id, payload.approved)
    return ChatApprovalDecisionResultOut(
        request_id=request_id,
        action=payload.action,
        outcome="approved" if payload.approved else "rejected",
    )


@router.get("/sessions/{session_id}/events")
async def session_events(session_id: int, db: Session = Depends(get_db)) -> StreamingResponse:
    """SSE 事件流：先补发快照/最近终态，再实时转发 Runtime 运行事件。

    本阶段约定：页面关闭或浏览器连接断开（订阅全部离开）会取消运行，
    已生成的部分正文保存为未完成；重新连接时通过快照与最近终态补齐。
    """
    _get_session_or_404(db, session_id)

    def sse(payload: dict) -> str:
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    async def event_stream():
        queue = generation_manager.subscribe(session_id)
        try:
            run = generation_manager.get_run(session_id)
            if run is None:
                # 订阅前任务已结束（或从未开始）：补发最近终态或空闲状态
                outcome = generation_manager.last_outcome(session_id)
                yield sse(outcome if outcome is not None else {"type": "idle"})
                return

            yield sse(generation_manager.snapshot_payload(run))
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=SSE_HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                yield sse(event)
                if event.get("type") in TERMINAL_EVENTS:
                    return
        finally:
            generation_manager.unsubscribe(session_id, queue)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
