"""工具管理的请求/响应模型。

接口层统一使用驼峰命名（alias_generator 生成），前端直接收发 camelCase；
执行入口返回统一的 {ok, result, error} 结构，执行失败的原因都在 error 里。
"""

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_serializer
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class ToolParamOut(CamelModel):
    """一个工具参数的说明，用于详情页展示与试运行表单。"""

    name: str
    type: str
    required: bool
    description: str
    example: str | None = None


class ToolOut(CamelModel):
    id: int
    key: str
    name: str
    description: str
    parameters: list[ToolParamOut]
    enabled: bool
    is_builtin: bool
    created_at: datetime
    updated_at: datetime

    @field_serializer("created_at", "updated_at")
    def serialize_datetime(self, value: datetime) -> str:
        # 数据库存的是 naive UTC；补上 UTC 标记，前端 new Date 可正确转本地时间
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()


class ToolUpdateRequest(CamelModel):
    """启停与（非内置工具的）改名改说明；None 表示不修改该字段。"""

    enabled: bool | None = None
    name: str | None = None
    description: str | None = None


class ToolExecuteRequest(CamelModel):
    params: dict[str, object] = Field(default_factory=dict)


class ToolErrorOut(CamelModel):
    type: str
    message: str
    hint: str | None = None
    fields: dict[str, str] | None = None


class ToolExecuteOut(CamelModel):
    ok: bool
    result: dict[str, object] | None = None
    error: ToolErrorOut | None = None
