"""MCP 管理的请求/响应模型。

与模型管理一致，接口层统一使用驼峰命名；环境变量的值任何响应中
只以掩码 ENV_VALUE_MASK 出现，编辑时回传掩码表示保留已保存的原值。
"""

import json
from datetime import datetime, timezone
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_serializer
from pydantic.alias_generators import to_camel

# 已保存变量值的掩码展示；更新时回传该值表示“保留原值”
ENV_VALUE_MASK = "••••••••"

# 最近一次测试的状态值；数据库里为 NULL 表示尚未测试
TEST_STATUS_UNTESTED = "untested"
TEST_STATUS_SUCCESS = "success"
TEST_STATUS_FAILED = "failed"
TEST_STATUS_STALE = "stale"


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class McpServerCreateRequest(CamelModel):
    name: Annotated[str, Field(max_length=64)]
    description: Annotated[str, Field(default="", max_length=512)] = ""
    command: Annotated[str, Field(max_length=512)]
    args: Annotated[list[str], Field(max_length=64)] = []
    env: dict[Annotated[str, Field(max_length=128)], Annotated[str, Field(max_length=4096)]] = {}
    enabled: bool = False


class McpServerUpdateRequest(CamelModel):
    """全量更新；env 值为掩码时表示保留该变量已保存的原值。"""

    name: Annotated[str, Field(max_length=64)]
    description: Annotated[str, Field(max_length=512)]
    command: Annotated[str, Field(max_length=512)]
    args: Annotated[list[str], Field(max_length=64)] = []
    env: dict[Annotated[str, Field(max_length=128)], Annotated[str, Field(max_length=4096)]] = {}


class McpServerEnabledRequest(CamelModel):
    enabled: bool


class McpToolParamOut(CamelModel):
    name: str
    type: str
    required: bool
    description: str


class McpToolOut(CamelModel):
    name: str
    description: str
    params: list[McpToolParamOut]


class McpServerOut(CamelModel):
    id: int
    name: str
    description: str
    command: str
    args: list[str]
    # 值一律为掩码，永不携带原文
    env: dict[str, str]
    enabled: bool
    last_test_status: str
    last_test_at: datetime | None = None
    last_test_error_type: str | None = None
    last_test_error: str | None = None
    last_test_tool_count: int | None = None
    last_test_tools: list[McpToolOut] | None = None
    created_at: datetime
    updated_at: datetime

    @field_serializer("last_test_at", "created_at", "updated_at")
    def serialize_datetime(self, value: datetime | None) -> str | None:
        # 数据库存的是 naive UTC；补上 UTC 标记，前端 new Date 可正确转本地时间
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()


class McpTestOut(CamelModel):
    """一次测试连接的结果；无论成功失败都已落库，刷新后仍可查看。"""

    status: str
    tested_at: datetime
    tool_count: int = 0
    tools: list[McpToolOut] = []
    error_type: str | None = None
    message: str | None = None
    hint: str | None = None


# Pydantic 类型/约束错误 → 字段级中文提示（key 为驼峰字段名，与前端表单字段一致）
FIELD_ERROR_MESSAGES: dict[str, str] = {
    "name": "请填写名称",
    "description": "说明格式不正确",
    "command": "请填写启动命令",
    "args": "启动参数格式不正确，每一项应为一段参数文本",
    "env": "环境变量格式不正确，应为 键: 值 形式",
    "enabled": "启用状态格式不正确",
}


def parse_tools_json(raw: str | None) -> list[dict[str, Any]]:
    """把落库的工具列表 JSON 还原为字典列表；损坏时按未测试处理。"""
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []
