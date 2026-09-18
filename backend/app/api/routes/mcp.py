"""MCP 管理路由：列表、新增、编辑、删除、启用/停用、测试连接。

一致性约定：
- 启动命令与参数分开保存，直接传给子进程，不拼接成 Shell 命令执行；
- 环境变量的值经 SecretBox 加密落 mcp_env_vars，任何响应只以掩码出现，
  编辑时回传掩码表示保留原值，掩码不会作为实际值保存；
- 修改启动命令、参数或环境变量后，最近一次测试结果标记为 stale
  （配置已变更，待重新测试），避免旧结果被误认为当前配置的验证结果；
- 测试连接是独立操作，停用状态下也可测试，测试成功不会自动启用；
- 同一个 Server 不允许同时进行多次测试。
"""

import json
import threading
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.secret_box import SecretBox, SecretBoxError, get_secret_box
from app.db.models.mcp_server import McpEnvVar, McpServer
from app.db.session import get_db
from app.schemas.mcp import (
    ENV_VALUE_MASK,
    TEST_STATUS_FAILED,
    TEST_STATUS_STALE,
    TEST_STATUS_SUCCESS,
    TEST_STATUS_UNTESTED,
    McpServerCreateRequest,
    McpServerEnabledRequest,
    McpServerOut,
    McpServerUpdateRequest,
    McpTestOut,
    McpToolOut,
    parse_tools_json,
)
from app.services.agent_refs import agent_names_using_mcp_server
from app.services.mcp_client import McpTestError, discover_tools

router = APIRouter(prefix="/mcp-servers", tags=["mcp"])

# 校验失败时返回 {"detail": {"message", "fields"}}，前端据此在字段旁显示原因
FIELD_ERROR_STATUS = 422
TEST_CONFLICT_STATUS = 409
# Server 仍被 Agent 引用时禁止删除（引用检查在后端执行）
REFERENCE_CONFLICT_STATUS = 409

# 进程内守卫：路由在线程池中同步运行，用锁保护“同一 Server 只允许一次测试”
_testing_ids: set[int] = set()
_testing_guard = threading.Lock()

# 无法确定原因的未知错误类型（与服务层保持一致）
ERROR_TYPE_UNKNOWN = "unknown"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _field_error(errors: dict[str, str], message: str = "表单校验未通过，请检查填写内容") -> HTTPException:
    return HTTPException(status_code=FIELD_ERROR_STATUS, detail={"message": message, "fields": errors})


def _get_or_404(db: Session, server_id: int) -> McpServer:
    server = db.get(McpServer, server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="MCP Server 不存在或已被删除")
    return server


def _clean_args(args: list[str]) -> list[str]:
    """去掉首尾空白并丢弃空项，保持参数顺序。"""
    return [arg.strip() for arg in args if arg.strip()]


def _clean_env(env: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    """校验环境变量键值，返回 (整理后的明文, {字段: 原因})。"""
    cleaned: dict[str, str] = {}
    errors: dict[str, str] = {}
    for raw_key, value in env.items():
        key = raw_key.strip()
        if not key:
            errors.setdefault("env", "环境变量名不能为空")
            continue
        if "=" in key or any(char.isspace() for char in key):
            errors.setdefault("env", f"环境变量名 {key} 不能包含空格或等号")
            continue
        if not value.strip():
            errors.setdefault("env", f"环境变量 {key} 的值不能为空，不需要的变量请删除该行")
            continue
        if key in cleaned:
            errors.setdefault("env", f"环境变量 {key} 重复")
            continue
        cleaned[key] = value.strip()
    return cleaned, errors


def _validate_payload(
    payload: McpServerCreateRequest | McpServerUpdateRequest,
    db: Session,
    exclude_id: int | None = None,
) -> dict[str, str]:
    """跨字段与格式校验，返回 {驼峰字段名: 原因}。"""
    errors: dict[str, str] = {}
    if not payload.name.strip():
        errors["name"] = "请填写名称"
    if not payload.command.strip():
        errors["command"] = "请填写启动命令"
    if "name" in errors:
        return errors

    name_query = db.query(McpServer.id).filter(McpServer.name.ilike(payload.name.strip()))
    if exclude_id is not None:
        name_query = name_query.filter(McpServer.id != exclude_id)
    if name_query.first() is not None:
        errors["name"] = "该名称已被其他 MCP Server 使用"
    return errors


def _decrypt_env_rows(rows: list[McpEnvVar], box: SecretBox) -> tuple[dict[str, str], list[str]]:
    """解密已保存的环境变量，返回 (明文, 解密失败的 key 列表）。"""
    plain: dict[str, str] = {}
    broken: list[str] = []
    for row in rows:
        try:
            plain[row.key] = box.decrypt(row.value_nonce, row.value_blob)
        except SecretBoxError:
            broken.append(row.key)
    return plain, broken


def _resolve_env(
    clean_env: dict[str, str],
    existing_plain: dict[str, str],
    broken_keys: list[str],
    errors: dict[str, str],
) -> dict[str, str]:
    """把请求中的 env 值解析为最终明文；掩码表示保留已保存的原值。"""
    final: dict[str, str] = {}
    for key, value in clean_env.items():
        if value != ENV_VALUE_MASK:
            final[key] = value
        elif key in existing_plain:
            final[key] = existing_plain[key]
        elif key in broken_keys:
            errors.setdefault("env", f"环境变量 {key} 已保存的值无法解密，请直接填写新值")
        else:
            errors.setdefault("env", f"环境变量 {key} 填写的是占位掩码，请直接填写实际值")
    return final


def _apply_env(server: McpServer, final_env: dict[str, str], existing_plain: dict[str, str], box: SecretBox) -> None:
    """把最终明文写入加密行：原值未变的行保留原密文，其余重新加密。"""
    kept_keys: set[str] = set()
    for row in list(server.env_vars):
        if row.key in final_env and existing_plain.get(row.key) == final_env[row.key]:
            kept_keys.add(row.key)
        else:
            server.env_vars.remove(row)  # delete-orphan 级联删除该行
    for key, value in final_env.items():
        if key in kept_keys:
            continue
        nonce, blob = box.encrypt(value)
        server.env_vars.append(McpEnvVar(key=key, value_nonce=nonce, value_blob=blob))


def _stored_args(server: McpServer) -> list[str]:
    try:
        args = json.loads(server.args_json)
    except (json.JSONDecodeError, TypeError):
        return []
    return [str(arg) for arg in args] if isinstance(args, list) else []


def _to_out(server: McpServer) -> McpServerOut:
    tools = [McpToolOut.model_validate(item) for item in parse_tools_json(server.last_test_tools_json)]
    tested = server.last_test_status == TEST_STATUS_SUCCESS
    return McpServerOut(
        id=server.id,
        name=server.name,
        description=server.description,
        command=server.command,
        args=_stored_args(server),
        env={row.key: ENV_VALUE_MASK for row in server.env_vars},
        enabled=server.enabled,
        last_test_status=server.last_test_status or TEST_STATUS_UNTESTED,
        last_test_at=server.last_test_at,
        last_test_error_type=server.last_test_error_type,
        last_test_error=server.last_test_error,
        last_test_tool_count=len(tools) if tested else None,
        last_test_tools=tools if tested else None,
        created_at=server.created_at,
        updated_at=server.updated_at,
    )


@router.get("")
def list_servers(db: Session = Depends(get_db)) -> list[McpServerOut]:
    return [_to_out(server) for server in db.query(McpServer).order_by(McpServer.id).all()]


@router.get("/{server_id}")
def get_server(server_id: int, db: Session = Depends(get_db)) -> McpServerOut:
    return _to_out(_get_or_404(db, server_id))


@router.post("", status_code=201)
def create_server(
    payload: McpServerCreateRequest,
    db: Session = Depends(get_db),
    box: SecretBox = Depends(get_secret_box),
) -> McpServerOut:
    errors = _validate_payload(payload, db)
    clean_env, env_errors = _clean_env(payload.env)
    errors.update(env_errors)
    if errors:
        raise _field_error(errors)

    server = McpServer(
        name=payload.name.strip(),
        description=payload.description.strip(),
        command=payload.command.strip(),
        args_json=json.dumps(_clean_args(payload.args), ensure_ascii=False),
        enabled=payload.enabled,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    # 新建时不存在“保留原值”，掩码一律视为占位并报错
    final_env = _resolve_env(clean_env, {}, [], errors)
    if errors:
        raise _field_error(errors)
    _apply_env(server, final_env, {}, box)

    db.add(server)
    db.commit()
    db.expire(server)
    return _to_out(server)


@router.put("/{server_id}")
def update_server(
    server_id: int,
    payload: McpServerUpdateRequest,
    db: Session = Depends(get_db),
    box: SecretBox = Depends(get_secret_box),
) -> McpServerOut:
    server = _get_or_404(db, server_id)

    errors = _validate_payload(payload, db, exclude_id=server_id)
    clean_args = _clean_args(payload.args)
    clean_env, env_errors = _clean_env(payload.env)
    errors.update(env_errors)
    existing_plain, broken_keys = _decrypt_env_rows(server.env_vars, box)
    final_env = _resolve_env(clean_env, existing_plain, broken_keys, errors)
    if errors:
        raise _field_error(errors)

    command = payload.command.strip()
    config_changed = (
        server.command != command
        or _stored_args(server) != clean_args
        or existing_plain != final_env
    )
    if config_changed and server.last_test_status in (TEST_STATUS_SUCCESS, TEST_STATUS_FAILED):
        server.last_test_status = TEST_STATUS_STALE

    server.name = payload.name.strip()
    server.description = payload.description.strip()
    server.command = command
    server.args_json = json.dumps(clean_args, ensure_ascii=False)
    _apply_env(server, final_env, existing_plain, box)
    server.updated_at = _utcnow()

    db.commit()
    db.expire(server)
    return _to_out(server)


@router.put("/{server_id}/enabled")
def set_server_enabled(
    server_id: int,
    payload: McpServerEnabledRequest,
    db: Session = Depends(get_db),
) -> McpServerOut:
    server = _get_or_404(db, server_id)
    server.enabled = payload.enabled
    server.updated_at = _utcnow()
    db.commit()
    db.expire(server)
    return _to_out(server)


@router.delete("/{server_id}", status_code=204)
def delete_server(server_id: int, db: Session = Depends(get_db)) -> None:
    server = _get_or_404(db, server_id)

    ref_names = agent_names_using_mcp_server(db, server_id)
    if ref_names:
        raise HTTPException(
            status_code=REFERENCE_CONFLICT_STATUS,
            detail=f"该 MCP Server 正在被 Agent 使用：{'、'.join(f'「{name}」' for name in ref_names)}。"
            "请先在对应 Agent 中移除该 Server，或删除相关 Agent 后再删除",
        )

    db.delete(server)  # 环境变量行随 ORM 级联一并清理
    db.commit()


@router.post("/{server_id}/test")
def test_server(
    server_id: int,
    db: Session = Depends(get_db),
    box: SecretBox = Depends(get_secret_box),
) -> McpTestOut:
    server = _get_or_404(db, server_id)

    with _testing_guard:
        if server_id in _testing_ids:
            raise HTTPException(
                status_code=TEST_CONFLICT_STATUS,
                detail="该 Server 正在测试中，请等待本次测试完成",
            )
        _testing_ids.add(server_id)

    try:
        env_plain, broken_keys = _decrypt_env_rows(server.env_vars, box)
        if broken_keys:
            raise _undecryptable_env_error()
        # discover_tools 无论成功、失败还是超时，内部都会清理子进程
        try:
            tools = discover_tools(server.command, _stored_args(server), env_plain)
        except McpTestError as error:
            return _record_failed_test(db, server, error)

        server.last_test_status = TEST_STATUS_SUCCESS
        server.last_test_at = _utcnow()
        server.last_test_error_type = None
        server.last_test_error = None
        server.last_test_tools_json = json.dumps(tools, ensure_ascii=False)
        db.commit()
        return McpTestOut(
            status=TEST_STATUS_SUCCESS,
            tested_at=server.last_test_at,
            tool_count=len(tools),
            tools=[McpToolOut.model_validate(item) for item in tools],
        )
    except McpTestError as error:
        return _record_failed_test(db, server, error)
    finally:
        with _testing_guard:
            _testing_ids.discard(server_id)


def _undecryptable_env_error() -> McpTestError:
    return McpTestError(
        ERROR_TYPE_UNKNOWN,
        "本地保存的环境变量无法解密，请重新编辑该 Server 并重新填写变量值",
        "主密钥文件可能被移动或重建；重新保存变量值后即可恢复测试",
    )


def _record_failed_test(db: Session, server: McpServer, error: McpTestError) -> McpTestOut:
    server.last_test_status = TEST_STATUS_FAILED
    server.last_test_at = _utcnow()
    server.last_test_error_type = error.error_type
    server.last_test_error = error.message
    server.last_test_tools_json = None
    db.commit()
    return McpTestOut(
        status=TEST_STATUS_FAILED,
        tested_at=server.last_test_at,
        error_type=error.error_type,
        message=error.message,
        hint=error.hint,
    )
