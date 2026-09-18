"""运行记录服务：把统一 Runtime 的真实运行与事件落库（阶段十一）。

不重复创建记录、不另写模拟日志：一次运行（用户发送一条消息启动 Agent）
恰好一条 agent_runs 行；事件全部来自 AgentRuntime 的 on_event 回调（与
聊天页 SSE 转发的是同一批事件、同一套名称 / 状态 / 摘要规则）。

分工：
- 调用方（聊天生成任务、后续评测）在启动 Runtime 前创建 RunRecorder，
  预生成 run_uid 并 start() 落一条 running 记录（Agent / 模型为当时快照）；
- Runtime 的每个事件经 record_event() 持久化（text_delta 流式增量除外，
  正文保存在聊天消息里）；run_uid + seq 唯一，重复投递不重复插入；
- run_end 经 finish() 与 Run 终态更新放在同一事务：状态、指标汇总与事件
  保存保持一致，Run 不会已结束而事件缺失（反之亦然）；
- 大段详细载荷（工具完整结果、最终正文片段）单独存放在
  agent_run_event_details，事件行只保留安全摘要；详细载荷同样先脱敏
  （密钥与绝对路径）再入库，读取入口只有详情端点，按需加载。

记录失败不打断运行本身：写库异常记录日志并停用该 recorder，遗留的
running 记录由应用启动时的 mark_interrupted_runs() 兜底标记。
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import sessionmaker

from app.db.models.run_record import (
    REASON_INTERRUPTED,
    RUN_SOURCE_CHAT,
    AgentRun,
    AgentRunEvent,
    AgentRunEventDetail,
    RUN_STATUS_CANCELLED,
    RUN_STATUS_COMPLETED,
    RUN_STATUS_FAILED,
    RUN_STATUS_PARTIAL,
    RUN_STATUS_RUNNING,
)
from app.services.agent_runtime import (
    RUN_END,
    TEXT_DELTA,
    TOOL_CALL_END,
    sanitize_display,
)

logger = logging.getLogger(__name__)

# Runtime 终态 → Run 状态（rounds_exhausted 属于部分完成，不是失败）
RUNTIME_STATUS_TO_RUN: dict[str, str] = {
    "completed": RUN_STATUS_COMPLETED,
    "rounds_exhausted": RUN_STATUS_PARTIAL,
    "cancelled": RUN_STATUS_CANCELLED,
    "error": RUN_STATUS_FAILED,
}

# 用户输入与最终正文的预览长度（保存前先脱敏）
PREVIEW_MAX_LENGTH = 160
# 详细载荷中最终正文片段的上限（与工具结果展示上限同量级）
RUN_CONTENT_DETAIL_MAX_LENGTH = 4000

# 后端重启 / 异常退出导致运行无法继续时的错误摘要
INTERRUPTED_ERROR_SUMMARY = "运行中断：后端服务重启或异常退出，本次运行未能完成"


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _clip_preview(text: str) -> str:
    text = " ".join(text.split())
    return text[:PREVIEW_MAX_LENGTH] + ("…" if len(text) > PREVIEW_MAX_LENGTH else "")


class RunRecorder:
    """一次运行的记录器：start 落主记录，record_event 落事件，finish 落终态。"""

    def __init__(
        self,
        session_factory: sessionmaker,
        run_uid: str,
        *,
        session_id: int | None,
        agent_id: int | None,
        agent_name: str,
        model_id: int | None,
        model_name: str,
        model_identifier: str,
        user_input: str,
        api_key: str | None = None,
        source: str = RUN_SOURCE_CHAT,
    ) -> None:
        self._session_factory = session_factory
        self.run_uid = run_uid
        self._session_id = session_id
        self._source = source
        self._agent_id = agent_id
        self._agent_name = agent_name
        self._model_id = model_id
        self._model_name = model_name
        self._model_identifier = model_identifier
        self._user_input = user_input
        self._secrets = [api_key] if api_key else []
        self._enabled = True

    # ---- 启动 ----

    def start(self) -> None:
        """创建 running 记录；失败时停用本记录器（运行本身继续）。"""
        if not self._enabled:
            return
        now = utcnow()
        db = self._session_factory()
        try:
            db.add(
                AgentRun(
                    run_uid=self.run_uid,
                    session_id=self._session_id,
                    source=self._source,
                    agent_id=self._agent_id,
                    agent_name=self._agent_name,
                    model_id=self._model_id,
                    model_name=self._model_name,
                    model_identifier=self._model_identifier,
                    status=RUN_STATUS_RUNNING,
                    user_input_preview=_clip_preview(sanitize_display(self._user_input, self._secrets)),
                    started_at=now,
                    created_at=now,
                )
            )
            db.commit()
        except Exception:
            db.rollback()
            self._enabled = False
            logger.exception("创建运行记录失败 run_uid=%s", self.run_uid)
        finally:
            db.close()

    # ---- 事件 ----

    def record_event(self, event: dict[str, Any]) -> None:
        """持久化一个运行事件（text_delta 除外）；重复 seq 直接跳过。"""
        event_type = event.get("type")
        if event_type == TEXT_DELTA or not self._enabled:
            return
        seq = event.get("seq")
        if not isinstance(seq, int):
            return
        payload, detail = self._split_payload(event)
        db = self._session_factory()
        try:
            if self._event_exists(db, seq):
                return
            row = AgentRunEvent(
                run_uid=self.run_uid,
                seq=seq,
                event_type=str(event_type),
                payload_json=json.dumps(payload, ensure_ascii=False),
                created_at=utcnow(),
            )
            db.add(row)
            db.flush()
            if detail is not None:
                db.add(
                    AgentRunEventDetail(
                        event_id=row.id,
                        payload_json=json.dumps(detail, ensure_ascii=False),
                        created_at=utcnow(),
                    )
                )
            db.commit()
        except Exception:
            db.rollback()
            logger.exception(
                "保存运行事件失败 run_uid=%s seq=%s type=%s", self.run_uid, seq, event_type
            )
        finally:
            db.close()

    def finish(self, event: dict[str, Any]) -> None:
        """处理 run_end：同一事务里保存事件并更新 Run 终态（幂等）。"""
        if not self._enabled or event.get("type") != RUN_END:
            return
        seq = event.get("seq")
        runtime_status = str(event.get("status") or "")
        run_status = RUNTIME_STATUS_TO_RUN.get(runtime_status, RUN_STATUS_FAILED)
        error = event.get("error") if isinstance(event.get("error"), dict) else None
        error_summary = (
            _clip_error_summary(sanitize_display(str(error.get("message") or ""), self._secrets))
            if error
            else None
        )
        token_usage = event.get("tokenUsage") if isinstance(event.get("tokenUsage"), dict) else None

        db = self._session_factory()
        try:
            if isinstance(seq, int) and not self._event_exists(db, seq):
                payload, detail = self._split_payload(event)
                row = AgentRunEvent(
                    run_uid=self.run_uid,
                    seq=seq,
                    event_type=RUN_END,
                    payload_json=json.dumps(payload, ensure_ascii=False),
                    created_at=utcnow(),
                )
                db.add(row)
                db.flush()
                if detail is not None:
                    db.add(
                        AgentRunEventDetail(
                            event_id=row.id,
                            payload_json=json.dumps(detail, ensure_ascii=False),
                            created_at=utcnow(),
                        )
                    )
            run = db.query(AgentRun).filter(AgentRun.run_uid == self.run_uid).first()
            if run is not None and run.status == RUN_STATUS_RUNNING:
                run.status = run_status
                run.finish_reason = str(event.get("reason") or "") or None
                run.error_summary = error_summary
                run.finished_at = utcnow()
                run.duration_ms = _int_or_none(event.get("durationMs"))
                run.model_calls = _int_or_zero(event.get("modelCalls"))
                run.tool_calls = _int_or_zero(event.get("toolCalls"))
                run.rejected_tool_calls = _int_or_zero(event.get("rejectedToolCalls"))
                run.first_output_ms = _int_or_none(event.get("firstOutputMs"))
                if token_usage is None:
                    run.prompt_tokens = None
                    run.completion_tokens = None
                    run.total_tokens = None
                    run.cached_tokens = None
                    run.tokens_incomplete = False
                else:
                    run.prompt_tokens = _int_or_none(token_usage.get("promptTokens"))
                    run.completion_tokens = _int_or_none(token_usage.get("completionTokens"))
                    run.total_tokens = _int_or_none(token_usage.get("totalTokens"))
                    run.cached_tokens = _int_or_none(token_usage.get("cachedTokens"))
                    run.tokens_incomplete = bool(token_usage.get("incomplete"))
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("保存运行终态失败 run_uid=%s", self.run_uid)
        finally:
            db.close()

    # ---- 内部 ----

    def _event_exists(self, db: Any, seq: int) -> bool:
        return (
            db.query(AgentRunEvent.id)
            .filter(AgentRunEvent.run_uid == self.run_uid, AgentRunEvent.seq == seq)
            .first()
            is not None
        )

    def _split_payload(self, event: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """拆分安全事件与大段详细载荷；返回 (安全事件, 详细载荷或 None)。"""
        payload = dict(event)
        detail: dict[str, Any] | None = None
        event_type = event.get("type")

        if event_type == TOOL_CALL_END and payload.get("result") is not None:
            # 完整工具结果只进详细载荷；事件行保留摘要（summary）
            detail = {"result": payload["result"]}
            payload["result"] = None
            payload["hasDetail"] = True

        if event_type == RUN_END:
            # 已保存的聊天消息对象不重复入库（正文在聊天消息里）
            payload["message"] = None
            content = str(payload.get("content") or "")
            if content:
                safe = sanitize_display(content, self._secrets)
                clipped = safe[:RUN_CONTENT_DETAIL_MAX_LENGTH]
                detail = {"content": clipped, "truncated": len(safe) > len(clipped)}
                payload["content"] = None
                payload["contentPreview"] = _clip_preview(safe)
                payload["hasDetail"] = True
            else:
                payload["content"] = None
        return payload, detail


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _int_or_zero(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _clip_error_summary(text: str) -> str:
    text = " ".join(text.split())
    return text[:200] + ("…" if len(text) > 200 else "")


def mark_interrupted_runs(session_factory: sessionmaker) -> int:
    """应用启动时调用：把遗留的 running 记录标记为失败并注明运行中断。

    后端重启或异常退出后运行无法继续，这些记录不能永久停留在运行中。
    """
    db = session_factory()
    interrupted = 0
    try:
        rows = db.query(AgentRun).filter(AgentRun.status == RUN_STATUS_RUNNING).all()
        now = utcnow()
        for run in rows:
            run.status = RUN_STATUS_FAILED
            run.finish_reason = REASON_INTERRUPTED
            run.error_summary = INTERRUPTED_ERROR_SUMMARY
            run.finished_at = now
            interrupted += 1
        if rows:
            db.commit()
            logger.warning("已把 %d 条遗留运行标记为中断", interrupted)
    except Exception:
        db.rollback()
        logger.exception("标记遗留运行失败")
    finally:
        db.close()
    return interrupted
