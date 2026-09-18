"""聊天生成的任务管理与运行事件转发。

生成任务的生命周期与 HTTP 请求解耦：发送消息的请求只负责校验、保存
用户消息并启动后台运行；实际的 Agent Loop 由统一的 AgentRuntime 执行
（本模块不维护模型与工具循环），通过 SSE 事件端点把运行事件转发给
当前订阅者。

SSE 事件（data 为 JSON，驼峰字段）：
- {"type":"snapshot","runId","phase","content","agentName","toolName","timeline"}
                                                     连接时的当前快照；
                                                     timeline 为展示时间线
                                                     （正文段与工具卡片段
                                                     按事件顺序交错，供前端
                                                     重连后恢复卡片与顺序）
- {"type":"run_start","runId","seq","agentName"}     运行开始
- {"type":"model_request_start"|"model_request_end"} 模型请求起止（callId 配对）
- {"type":"text_delta","runId","seq","round","callId","text"}   正文增量
- {"type":"tool_call_start",...,"arguments","serverName"}
                                                     工具调用开始（含整理后参数）
- {"type":"tool_call_end",...,"status","summary","result"}
                                                     工具调用结束（status 含
                                                     cancelled；result 为整理
                                                     后的展示文本）
- {"type":"context_compaction_start"|"context_compaction_end",...}
                                                     上下文压缩起止（触发
                                                     原因、前后估算 Token、
                                                     处理/保留消息数、耗时；
                                                     不含摘要正文）
- {"type":"context_trim",...}                        备用裁剪（只影响本次
                                                     请求的估算数字）
- {"type":"run_end",...,"message","error"}           运行结束（终态，恰好一次）
- {"type":"idle"}                                    当前没有运行任务

run_end 的 status：completed（已完成并保存）/ cancelled（已停止，部分正文
保存为未完成）/ error / rounds_exhausted（达到最大轮数）；error 字段为
{"type","message","hint"}，message 字段为已保存回复的完整消息（可能为 null）。
任务结束后保留一份最近终态（进程内），供迟到的订阅者补收。

取消：停止生成、关闭页面或浏览器断开都会触发取消。Runtime 收到取消
信号后尽快中止模型请求、工具执行与循环，已生成的正文保存为未完成。
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session, sessionmaker

from app.core.secret_box import SecretBox
from app.db.models.chat import (
    ChatMessage,
    ChatSession,
    ROLE_ASSISTANT,
    ROLE_USER,
    STATUS_COMPLETED,
    STATUS_INCOMPLETE,
)
from app.db.models.model_config import ModelConfig
from app.schemas.chat import (
    DEFAULT_SESSION_TITLE,
    SESSION_TITLE_SNIPPET_LENGTH,
    ChatMessageOut,
)
from app.services.agent_runtime import (
    CONTEXT_COMPACTION_END,
    CONTEXT_COMPACTION_START,
    CONTEXT_TRIM,
    REASON_CANCELLED,
    RUN_END,
    RUN_START,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    TEXT_DELTA,
    TOOL_CALL_END,
    TOOL_CALL_START,
    AgentRuntime,
    RuntimeRequest,
    redact,
)
from app.services.model_client import ERROR_TYPE_SERVER
from app.services.run_recorder import RunRecorder
from app.services.token_estimation import (
    MESSAGE_OVERHEAD_TOKENS,
    estimate_tokens as _estimate_tokens,
)

logger = logging.getLogger(__name__)

# 生成阶段：thinking 尚无正文；generating 正文流式输出中；tooling 工具执行中；
# compacting 正在压缩上下文（生成长会话摘要）
PHASE_THINKING = "thinking"
PHASE_GENERATING = "generating"
PHASE_TOOLING = "tooling"
PHASE_COMPACTING = "compacting"

# 终态事件类型
EVENT_RUN_END = RUN_END
TERMINAL_EVENTS = (EVENT_RUN_END,)


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def next_seq(db: Session, session_id: int) -> int:
    """会话内下一个顺序号：max(seq)+1，消息读取按 seq 排序。"""
    current = (
        db.query(func.max(ChatMessage.seq)).filter(ChatMessage.session_id == session_id).scalar()
    )
    return (current or 0) + 1


def make_session_title(content: str) -> str:
    """用首条用户消息生成标题：折叠空白后截取前 30 个字符。"""
    collapsed = " ".join(content.split())
    return collapsed[:SESSION_TITLE_SNIPPET_LENGTH] or DEFAULT_SESSION_TITLE


def estimate_tokens(text: str) -> int:
    """Token 估算（自 token_estimation 模块引入，兼容既有引用）。

    CJK 每字 1 token，其余每 4 字符 1 token；宁可高估不低估：提前提示
    "历史过长"好过把超限请求发出去再等报错。所有结果均为估算值。
    """
    return _estimate_tokens(text)


def load_effective_history(db: Session, session_id: int) -> list[ChatMessage]:
    """按 seq 取出构造上下文用的有效消息。

    用户消息全部计入；assistant 消息只计 status=completed 的，未完成
    回复（停止/中断）与错误提示都不进入后续上下文。
    """
    return (
        db.query(ChatMessage)
        .filter(
            ChatMessage.session_id == session_id,
            (ChatMessage.role == ROLE_USER)
            | ((ChatMessage.role == ROLE_ASSISTANT) & (ChatMessage.status == STATUS_COMPLETED)),
        )
        .order_by(ChatMessage.seq)
        .all()
    )


def estimate_context_tokens(system_prompt: str, history: list[ChatMessage], new_content: str) -> int:
    """估算 system + 历史 + 新消息的总 token 数（估算值，见 token_estimation）。"""
    total = _estimate_tokens(system_prompt) + MESSAGE_OVERHEAD_TOKENS
    for item in history:
        total += _estimate_tokens(item.content) + MESSAGE_OVERHEAD_TOKENS
    total += _estimate_tokens(new_content) + MESSAGE_OVERHEAD_TOKENS
    return total


def message_to_out(message: ChatMessage) -> ChatMessageOut:
    return ChatMessageOut(
        id=message.id,
        session_id=message.session_id,
        role=message.role,
        content=message.content,
        status=message.status,
        agent_id=message.agent_id,
        agent_name=message.agent_name,
        seq=message.seq,
        created_at=message.created_at,
    )


def message_event_payload(message: ChatMessage) -> dict[str, Any]:
    """把已保存的消息转成 SSE 事件里使用的驼峰 JSON 字典。"""
    return message_to_out(message).model_dump(by_alias=True, mode="json")


def timeline_append_text(timeline: list[dict[str, Any]], text: str) -> None:
    """正文增量并入时间线：与上一段正文相连时合并，否则在末尾新起一段。"""
    if timeline and timeline[-1].get("kind") == "text":
        timeline[-1]["text"] += text
    else:
        timeline.append({"kind": "text", "text": text})


def timeline_update_tool(timeline: list[dict[str, Any]], event: dict[str, Any]) -> None:
    """用 tool_call_end 更新时间线上对应的工具卡片（callId 配对，运行中优先）。"""
    call_id = event.get("callId")
    target: dict[str, Any] | None = None
    for item in reversed(timeline):
        if item.get("kind") != "tool" or item.get("callId") != call_id:
            continue
        if item.get("status") == "running":
            target = item
            break
        if target is None:
            target = item
    if target is None:
        return
    target["status"] = event.get("status")
    target["durationMs"] = event.get("durationMs")
    target["summary"] = event.get("summary")
    target["result"] = event.get("result")


def timeline_update_compact(timeline: list[dict[str, Any]], event: dict[str, Any]) -> None:
    """用 context_compaction_end 更新时间线上对应的压缩卡片（compactionId 配对）。"""
    compaction_id = event.get("compactionId")
    for item in reversed(timeline):
        if item.get("kind") != "compact" or item.get("compactionId") != compaction_id:
            continue
        item.update(
            {
                "status": event.get("status"),
                "estimatedTokensAfter": event.get("estimatedTokensAfter"),
                "processedMessages": event.get("processedMessages"),
                "keptMessages": event.get("keptMessages"),
                "summaryTokens": event.get("summaryTokens"),
                "batches": event.get("batches"),
                "modelCalls": event.get("modelCalls"),
                "durationMs": event.get("durationMs"),
                "error": event.get("error"),
            }
        )
        return


def timeline_compact_item(event: dict[str, Any]) -> dict[str, Any]:
    """压缩/裁剪事件 → 时间线卡片段（只保留安全的估算数字，不含摘要正文）。"""
    return {
        "kind": "compact",
        "compactionId": event.get("compactionId"),
        "status": event.get("status"),
        "reason": event.get("reason"),
        "estimatedTokensBefore": event.get("estimatedTokensBefore", event.get("estimatedTokens")),
        "estimatedTokensAfter": event.get("estimatedTokensAfter"),
        "processedMessages": event.get("processedMessages"),
        "keptMessages": event.get("keptMessages"),
        "summaryTokens": event.get("summaryTokens"),
        "batches": event.get("batches"),
        "modelCalls": event.get("modelCalls"),
        "durationMs": event.get("durationMs"),
        "droppedMessages": event.get("droppedMessages"),
        "error": event.get("error"),
    }


@dataclass
class GenerationRun:
    """一个会话上正在进行的运行。"""

    session_id: int
    agent_id: int
    agent_name: str
    user_input: str
    # [{"role", "content", "seq"}]，不含本次用户消息，只含压缩边界之后的消息
    history: list[dict[str, Any]]
    api_key: str | None
    session_factory: sessionmaker
    secret_box: SecretBox
    skills_dir: Any
    # 模型快照（运行记录与展示用，后续改名 / 删除不影响历史）
    model_id: int | None = None
    model_name: str = ""
    model_identifier: str = ""
    # 会话压缩状态：当前有效摘要与已压缩边界（None 表示尚未压缩）
    session_summary: str = ""
    summary_boundary: int | None = None
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    run_id: str | None = None
    phase: str = PHASE_THINKING
    content: str = ""
    tool_name: str | None = None
    # 展示时间线：正文段与工具卡片段按事件顺序交错，重连快照用它恢复
    timeline: list[dict[str, Any]] = field(default_factory=list)
    subscribers: list[asyncio.Queue] = field(default_factory=list)
    had_subscribers: bool = False
    finished: bool = False
    task: asyncio.Task | None = None
    # 运行记录器：把本运行的真实事件与终态写入 agent_runs / agent_run_events
    recorder: RunRecorder | None = None


class GenerationManager:
    """进程内单例：管理各会话的运行、订阅队列与最近终态。"""

    def __init__(self) -> None:
        self._runs: dict[int, GenerationRun] = {}
        self._last_outcomes: dict[int, dict[str, Any]] = {}

    # ---- 查询 ----

    def is_generating(self, session_id: int) -> bool:
        return session_id in self._runs

    def get_run(self, session_id: int) -> GenerationRun | None:
        return self._runs.get(session_id)

    def last_outcome(self, session_id: int) -> dict[str, Any] | None:
        return self._last_outcomes.get(session_id)

    def generating_session_ids(self) -> set[int]:
        return set(self._runs)

    # ---- 任务控制 ----

    def start(
        self,
        session_id: int,
        agent_id: int,
        agent_name: str,
        user_input: str,
        history: list[dict[str, Any]],
        api_key: str | None,
        session_factory: sessionmaker,
        secret_box: SecretBox,
        skills_dir: Any,
        session_summary: str = "",
        summary_boundary: int | None = None,
        model_id: int | None = None,
        model_name: str = "",
        model_identifier: str = "",
    ) -> None:
        """启动一次后台运行；同一会话同时只允许一个运行。

        启动时即创建运行记录（running）：一次发送消息对应一条 Run，
        run_uid 预生成后传给 Runtime，事件与终态由 recorder 落库。
        """
        if session_id in self._runs:
            raise RuntimeError(f"会话 {session_id} 已有进行中的生成任务")
        run = GenerationRun(
            session_id=session_id,
            agent_id=agent_id,
            agent_name=agent_name,
            user_input=user_input,
            history=history,
            api_key=api_key,
            session_factory=session_factory,
            secret_box=secret_box,
            skills_dir=skills_dir,
            model_id=model_id,
            model_name=model_name,
            model_identifier=model_identifier,
            session_summary=session_summary,
            summary_boundary=summary_boundary,
        )
        recorder = RunRecorder(
            session_factory,
            uuid.uuid4().hex,
            session_id=session_id,
            agent_id=agent_id,
            agent_name=agent_name,
            model_id=model_id,
            model_name=model_name,
            model_identifier=model_identifier,
            user_input=user_input,
            api_key=api_key,
        )
        recorder.start()
        run.recorder = recorder
        run.run_id = recorder.run_uid
        run.task = asyncio.create_task(self._execute(run))
        # 持引用防 GC；任务结束时在 _finish 里移除
        self._runs[session_id] = run
        self._last_outcomes.pop(session_id, None)

    async def stop(self, session_id: int) -> dict[str, Any] | None:
        """请求停止运行并等待收尾，返回终态事件（无任务时 None）。"""
        run = self._runs.get(session_id)
        if run is None or run.task is None:
            return None
        # 双保险：取消信号走 Runtime 的统一收尾路径，任务取消保证及时中断
        run.cancel_event.set()
        run.task.cancel()
        await asyncio.gather(run.task, return_exceptions=True)
        return self._last_outcomes.get(session_id)

    async def close(self) -> None:
        """应用关闭时取消全部任务，等待收尾避免悬挂协程。"""
        tasks = [run.task for run in self._runs.values() if run.task is not None]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # ---- 订阅 ----

    def subscribe(self, session_id: int) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        run = self._runs.get(session_id)
        if run is not None:
            run.subscribers.append(queue)
            run.had_subscribers = True
        return queue

    def unsubscribe(self, session_id: int, queue: asyncio.Queue) -> None:
        run = self._runs.get(session_id)
        if run is None or queue not in run.subscribers:
            return
        run.subscribers.remove(queue)
        # 页面关闭 / 浏览器断开（本阶段约定断连即取消）：通知 Runtime 收尾
        if not run.subscribers and run.had_subscribers and run.task is not None:
            logger.info("事件订阅全部断开，取消运行 session_id=%s run_id=%s", session_id, run.run_id)
            run.cancel_event.set()
            run.task.cancel()

    # ---- 内部实现 ----

    def _broadcast(self, run: GenerationRun, event: dict[str, Any]) -> None:
        for queue in run.subscribers:
            queue.put_nowait(event)

    def _finish(self, run: GenerationRun, event: dict[str, Any]) -> None:
        """登记最近终态并广播；与广播同步完成，订阅者不会观察到中间态。

        run_end 事件与 Run 终态由 recorder 在同一事务里落库（幂等），
        任何收尾路径（正常结束 / 取消补发 / 异常补发）都经过这里。
        """
        if run.finished:
            return
        run.finished = True
        if run.recorder is not None:
            try:
                run.recorder.finish(event)
            except Exception:
                logger.exception("保存运行终态失败 session_id=%s run_id=%s", run.session_id, run.run_id)
        self._last_outcomes[run.session_id] = event
        self._broadcast(run, event)
        self._runs.pop(run.session_id, None)

    def snapshot_payload(self, run: GenerationRun) -> dict[str, Any]:
        return {
            "type": "snapshot",
            "runId": run.run_id,
            "phase": run.phase,
            "content": run.content,
            "agentName": run.agent_name,
            "toolName": run.tool_name,
            "timeline": [dict(item) for item in run.timeline],
        }

    def _save_assistant_message(self, run: GenerationRun, status: str) -> ChatMessage:
        """任务自己的短数据库会话：保存回复正文并更新会话时间。"""
        db: Session = run.session_factory()
        try:
            content = redact(run.content, run.api_key)
            message = ChatMessage(
                session_id=run.session_id,
                role=ROLE_ASSISTANT,
                content=content,
                agent_id=run.agent_id,
                agent_name=run.agent_name,
                status=status,
                seq=next_seq(db, run.session_id),
                created_at=utcnow(),
            )
            db.add(message)
            session = db.get(ChatSession, run.session_id)
            if session is not None:
                session.updated_at = utcnow()
            db.commit()
            db.refresh(message)
            return message
        finally:
            db.close()

    # ---- 运行事件处理 ----

    def _on_event(self, run: GenerationRun, event: dict[str, Any]) -> None:
        """Runtime 事件回调：更新快照状态、维护时间线、转发事件，终态时收尾。

        事件同时交给运行记录器落库（与 SSE 转发给前端的是同一批事件）；
        run_end 除外——它由 _finish 统一处理，保证与 Run 终态同一事务。
        """
        event_type = event.get("type")
        if event_type != RUN_END and run.recorder is not None:
            run.recorder.record_event(event)
        if event_type == RUN_START:
            run.run_id = event.get("runId")
            self._broadcast(run, event)
            return
        if event_type == TEXT_DELTA:
            run.content += event.get("text", "")
            timeline_append_text(run.timeline, event.get("text", ""))
            if run.phase != PHASE_GENERATING:
                run.phase = PHASE_GENERATING
            self._broadcast(run, event)
            return
        if event_type == TOOL_CALL_START:
            display = str(event.get("displayName") or event.get("toolName") or "")
            server = event.get("serverName")
            run.tool_name = f"{server}·{display}" if server else display
            run.phase = PHASE_TOOLING
            run.timeline.append(
                {
                    "kind": "tool",
                    "callId": event.get("callId"),
                    "toolName": event.get("toolName"),
                    "toolType": event.get("toolType"),
                    "displayName": event.get("displayName"),
                    "serverName": event.get("serverName"),
                    "status": "running",
                    "arguments": event.get("arguments"),
                }
            )
            self._broadcast(run, event)
            return
        if event_type == TOOL_CALL_END:
            run.tool_name = None
            run.phase = PHASE_GENERATING if run.content.strip() else PHASE_THINKING
            timeline_update_tool(run.timeline, event)
            self._broadcast(run, event)
            return
        if event_type == CONTEXT_COMPACTION_START:
            run.phase = PHASE_COMPACTING
            run.timeline.append(timeline_compact_item(event))
            self._broadcast(run, event)
            return
        if event_type == CONTEXT_COMPACTION_END:
            run.phase = PHASE_GENERATING if run.content.strip() else PHASE_THINKING
            timeline_update_compact(run.timeline, event)
            self._broadcast(run, event)
            return
        if event_type == CONTEXT_TRIM:
            run.timeline.append(timeline_compact_item(event))
            self._broadcast(run, event)
            return
        if event_type == RUN_END:
            self._handle_run_end(run, event)
            return
        # model_request_start / model_request_end 等其余事件直接转发
        self._broadcast(run, event)

    def _handle_run_end(self, run: GenerationRun, event: dict[str, Any]) -> None:
        """运行结束：按状态保存回复正文，把已保存消息附加到终态事件。"""
        status = event.get("status")
        final_event = dict(event)
        final_event.setdefault("message", None)

        if run.content.strip():
            db_status = STATUS_COMPLETED if status == STATUS_COMPLETED else STATUS_INCOMPLETE
            try:
                message = self._save_assistant_message(run, db_status)
                final_event["message"] = message_event_payload(message)
            except Exception:
                logger.exception("保存回复正文失败 session_id=%s", run.session_id)
                final_event["error"] = {
                    "type": ERROR_TYPE_SERVER,
                    "message": (
                        "回复已生成，但保存失败，以下内容未入库"
                        if status == STATUS_COMPLETED
                        else "已停止生成，但部分回复保存失败，以下内容未入库"
                    ),
                    "hint": "内容仅在当前页面可见，刷新后会丢失",
                }
        self._finish(run, final_event)

    def _persist_summary(self, run: GenerationRun, summary: str, boundary: int) -> None:
        """压缩成功的持久化回调：新摘要与边界一致写入会话（失败抛异常）。

        在线程中执行（Runtime 经 asyncio.to_thread 调用）；同一会话同时
        只有一个运行，不存在并发写入。
        """
        db: Session = run.session_factory()
        try:
            session = db.get(ChatSession, run.session_id)
            if session is None:
                raise RuntimeError(f"会话 {run.session_id} 不存在或已被删除")
            session.summary_text = summary
            session.summary_boundary_seq = boundary
            db.commit()
        finally:
            db.close()

    async def _execute(self, run: GenerationRun) -> None:
        """后台任务：调用统一 Agent Runtime 执行一次运行。"""
        try:
            runtime = AgentRuntime(
                session_factory=run.session_factory,
                secret_box=run.secret_box,
                skills_dir=run.skills_dir,
            )
            request = RuntimeRequest(
                agent_id=run.agent_id,
                user_input=run.user_input,
                history=run.history,
                session_summary=run.session_summary,
                summary_boundary_seq=run.summary_boundary,
                persist_summary=lambda summary, boundary: self._persist_summary(
                    run, summary, boundary
                ),
            )
            await runtime.run(
                request,
                cancel_event=run.cancel_event,
                on_event=lambda event: self._on_event(run, event),
                run_id=run.run_id,
            )
        except asyncio.CancelledError:
            # 运行内部已尽力收尾；若终态事件缺失（如收尾期间被再次取消），补一次
            self._finish(
                run,
                {
                    "type": EVENT_RUN_END,
                    "runId": run.run_id or "",
                    "status": STATUS_CANCELLED,
                    "reason": REASON_CANCELLED,
                    "content": run.content,
                    "message": None,
                    "error": None,
                },
            )
        except Exception:
            logger.exception("聊天运行出现未预期异常 session_id=%s", run.session_id)
            self._finish(
                run,
                {
                    "type": EVENT_RUN_END,
                    "runId": run.run_id or "",
                    "status": "error",
                    "reason": "runtime_error",
                    "content": run.content,
                    "message": None,
                    "error": {
                        "type": ERROR_TYPE_SERVER,
                        "message": "生成回复时出现未预期的错误",
                        "hint": "请重试；若持续出现请查看后端日志",
                    },
                },
            )


# 进程内共享单例；应用关闭时在 main.py 的 lifespan 里统一收尾
generation_manager = GenerationManager()
