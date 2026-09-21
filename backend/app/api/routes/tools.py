"""工具管理路由：列表、详情、启停、删除与统一执行入口。

一致性约定：
- 系统内置工具（is_builtin）允许启停，不允许删除，也不允许修改名称或用途；
- 统一执行入口 POST /tools/{id}/execute 在执行前检查工具存在性与启用状态、
  校验参数，执行结果统一为 {ok, result, error} 结构；工具不存在返回 404，
  其余执行结果（含被拦截、超时、失败）都以 HTTP 200 返回，方便调用方统一处理。
"""

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models.tool import Tool
from app.db.session import get_db
from app.schemas.tool import (
    ToolErrorOut,
    ToolExecuteOut,
    ToolExecuteRequest,
    ToolOut,
    ToolParamOut,
    ToolUpdateRequest,
)
from app.services.tools.base import ToolContext
from app.services.tools.executor import execute_tool

router = APIRouter(prefix="/tools", tags=["tools"])

# 内置工具受保护操作的响应状态码
BUILTIN_PROTECTED_STATUS = 403


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _get_or_404(db: Session, tool_id: int) -> Tool:
    tool = db.get(Tool, tool_id)
    if tool is None:
        raise HTTPException(status_code=404, detail="工具不存在或已被删除")
    return tool


def _parse_parameters(row: Tool) -> list[ToolParamOut]:
    """把 tools.parameters_json 解析成参数说明列表，坏数据不致接口 500。"""
    try:
        raw = json.loads(row.parameters_json)
    except json.JSONDecodeError:
        return []
    if not isinstance(raw, list):
        return []
    params: list[ToolParamOut] = []
    for item in raw:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            params.append(
                ToolParamOut(
                    name=item["name"],
                    type=str(item.get("type") or "string"),
                    required=bool(item.get("required") or False),
                    description=str(item.get("description") or ""),
                    example=item.get("example") if isinstance(item.get("example"), str) else None,
                )
            )
    return params


def _to_out(tool: Tool) -> ToolOut:
    return ToolOut(
        id=tool.id,
        key=tool.key,
        name=tool.name,
        description=tool.description,
        parameters=_parse_parameters(tool),
        enabled=tool.enabled,
        is_builtin=tool.is_builtin,
        created_at=tool.created_at,
        updated_at=tool.updated_at,
    )


@router.get("")
def list_tools(db: Session = Depends(get_db)) -> list[ToolOut]:
    """内置工具在前，其余按添加顺序排列。"""
    tools = db.query(Tool).order_by(Tool.is_builtin.desc(), Tool.id.asc()).all()
    return [_to_out(tool) for tool in tools]


@router.get("/{tool_id}")
def get_tool(tool_id: int, db: Session = Depends(get_db)) -> ToolOut:
    return _to_out(_get_or_404(db, tool_id))


@router.put("/{tool_id}")
def update_tool(tool_id: int, payload: ToolUpdateRequest, db: Session = Depends(get_db)) -> ToolOut:
    tool = _get_or_404(db, tool_id)

    if payload.name is not None and not payload.name.strip():
        raise HTTPException(status_code=422, detail="工具名称不能为空")
    if payload.description is not None and not payload.description.strip():
        raise HTTPException(status_code=422, detail="工具用途说明不能为空")

    if tool.is_builtin:
        # 内置工具只允许启停：名称与用途必须保持原值
        renames = payload.name is not None and payload.name.strip() != tool.name
        redescribes = payload.description is not None and payload.description.strip() != tool.description
        if renames or redescribes:
            raise HTTPException(
                status_code=BUILTIN_PROTECTED_STATUS,
                detail="系统内置工具不允许修改名称或用途，只能启用或停用",
            )
    else:
        if payload.name is not None:
            tool.name = payload.name.strip()
        if payload.description is not None:
            tool.description = payload.description.strip()

    if payload.enabled is not None:
        tool.enabled = payload.enabled

    tool.updated_at = _utcnow()
    db.commit()
    db.refresh(tool)
    return _to_out(tool)


@router.delete("/{tool_id}", status_code=204)
def delete_tool(tool_id: int, db: Session = Depends(get_db)) -> None:
    tool = _get_or_404(db, tool_id)
    if tool.is_builtin:
        raise HTTPException(status_code=BUILTIN_PROTECTED_STATUS, detail="系统内置工具不允许删除")
    db.delete(tool)
    db.commit()


@router.post("/{tool_id}/execute")
def execute_tool_endpoint(
    tool_id: int,
    payload: ToolExecuteRequest,
    db: Session = Depends(get_db),
) -> ToolExecuteOut:
    """统一执行入口：存在性检查 → 启停检查 → 参数校验 → 执行 → 统一结果。"""
    tool = _get_or_404(db, tool_id)
    # 手动测试端点没有 Agent 上下文，用全局默认工作区构造 ToolContext
    result = execute_tool(tool, payload.params, ToolContext(workspace_root=settings.tool_workspace_dir))

    if result.ok:
        return ToolExecuteOut(ok=True, result=result.data)
    return ToolExecuteOut(
        ok=False,
        error=ToolErrorOut(
            type=result.error_type or "failed",
            message=result.message or "工具执行失败",
            hint=result.hint,
            fields=result.fields or None,
        ),
    )
