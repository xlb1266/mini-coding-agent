"""运行记录路由：运行列表（分页 / 筛选）、详情时间线与详细载荷。

查询约定：
- 列表只读 agent_runs 行（快照与汇总字段一次查询取回），不加载事件、
  模型输入或工具结果，也不逐行回查 Agent / 模型表；
- 详情返回运行摘要 + 按事件顺序（seq）排列的安全事件；详细载荷（工具
  完整结果、最终正文片段）单独端点按需加载；
- 状态筛选值：running / completed / partial / cancelled / failed。
"""

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.models.run_record import (
    AgentRun,
    AgentRunEvent,
    AgentRunEventDetail,
    RUN_SOURCES,
    RUN_STATUSES,
)
from app.db.session import get_db
from app.schemas.run import (
    RunDetailOut,
    RunEventDetailOut,
    RunEventOut,
    RunItemOut,
    RunListOut,
    RunUsageOut,
)

router = APIRouter(prefix="/runs", tags=["runs"])

NOT_FOUND_STATUS = 404


def _usage_out(run: AgentRun) -> RunUsageOut | None:
    """汇总用量；上游从未返回（全部列为空）时为 None（未知，不填 0）。"""
    if (
        run.prompt_tokens is None
        and run.completion_tokens is None
        and run.total_tokens is None
        and run.cached_tokens is None
    ):
        return None
    return RunUsageOut(
        prompt_tokens=run.prompt_tokens,
        completion_tokens=run.completion_tokens,
        total_tokens=run.total_tokens,
        cached_tokens=run.cached_tokens,
    )


def _item_out(run: AgentRun) -> RunItemOut:
    return RunItemOut(
        id=run.id,
        run_uid=run.run_uid,
        session_id=run.session_id,
        source=run.source,
        agent_id=run.agent_id,
        agent_name=run.agent_name,
        model_name=run.model_name,
        model_identifier=run.model_identifier,
        status=run.status,
        finish_reason=run.finish_reason,
        error_summary=run.error_summary,
        user_input_preview=run.user_input_preview,
        started_at=run.started_at,
        finished_at=run.finished_at,
        duration_ms=run.duration_ms,
        model_calls=run.model_calls,
        tool_calls=run.tool_calls,
        rejected_tool_calls=run.rejected_tool_calls,
        first_output_ms=run.first_output_ms,
        token_usage=_usage_out(run),
        tokens_incomplete=run.tokens_incomplete,
    )


def _get_run_or_404(db: Session, run_id: int) -> AgentRun:
    run = db.get(AgentRun, run_id)
    if run is None:
        raise HTTPException(status_code=NOT_FOUND_STATUS, detail="运行记录不存在或已被删除")
    return run


@router.get("")
def list_runs(
    status: str | None = Query(default=None, description="按状态筛选"),
    source: str | None = Query(default=None, description="按来源筛选（chat / evaluation）"),
    agent_id: int | None = Query(default=None, alias="agentId", description="按 Agent 筛选"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    db: Session = Depends(get_db),
) -> RunListOut:
    """运行列表：按开始时间倒序分页；只读摘要字段，不加载事件与载荷。"""
    if status is not None and status not in RUN_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"无效的运行状态：{status}（可选：{'、'.join(RUN_STATUSES)}）",
        )
    if source is not None and source not in RUN_SOURCES:
        raise HTTPException(
            status_code=422,
            detail=f"无效的运行来源：{source}（可选：{'、'.join(RUN_SOURCES)}）",
        )
    query = db.query(AgentRun)
    if status is not None:
        query = query.filter(AgentRun.status == status)
    if source is not None:
        query = query.filter(AgentRun.source == source)
    if agent_id is not None:
        query = query.filter(AgentRun.agent_id == agent_id)

    total = query.count()
    rows = (
        query.order_by(AgentRun.started_at.desc(), AgentRun.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return RunListOut(
        items=[_item_out(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{run_id}")
def get_run(run_id: int, db: Session = Depends(get_db)) -> RunDetailOut:
    """运行详情：摘要 + 按事件顺序排列的时间线（安全事件，不含详细载荷）。"""
    run = _get_run_or_404(db, run_id)
    # 只 JOIN 详细载荷的主键判断有无，不读取载荷内容
    rows = (
        db.query(AgentRunEvent, AgentRunEventDetail.id)
        .outerjoin(AgentRunEventDetail, AgentRunEventDetail.event_id == AgentRunEvent.id)
        .filter(AgentRunEvent.run_uid == run.run_uid)
        .order_by(AgentRunEvent.seq)
        .all()
    )
    events = [
        RunEventOut(
            id=event.id,
            seq=event.seq,
            event_type=event.event_type,
            payload=json.loads(event.payload_json),
            has_detail=detail_id is not None,
        )
        for event, detail_id in rows
    ]
    return RunDetailOut(run=_item_out(run), events=events)


@router.get("/{run_id}/events/{event_id}/detail")
def get_run_event_detail(run_id: int, event_id: int, db: Session = Depends(get_db)) -> RunEventDetailOut:
    """事件的详细载荷：受控读取入口，按需单独加载（同样经过脱敏）。"""
    run = _get_run_or_404(db, run_id)
    event = db.get(AgentRunEvent, event_id)
    if event is None or event.run_uid != run.run_uid:
        raise HTTPException(status_code=NOT_FOUND_STATUS, detail="事件不存在或不属于该运行")
    detail = db.query(AgentRunEventDetail).filter(AgentRunEventDetail.event_id == event.id).first()
    if detail is None:
        raise HTTPException(status_code=NOT_FOUND_STATUS, detail="该事件没有详细载荷")
    return RunEventDetailOut(
        event_id=event.id,
        seq=event.seq,
        event_type=event.event_type,
        payload=json.loads(detail.payload_json),
    )
