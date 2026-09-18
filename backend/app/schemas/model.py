"""模型管理的请求/响应模型。

接口层统一使用驼峰命名（alias_generator 生成），前端直接收发 camelCase；
密钥只以 hasApiKey 布尔值出现，任何响应不携带密钥正文。
"""

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, field_serializer
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class ModelCreateRequest(CamelModel):
    display_name: str
    model_identifier: str
    base_url: str
    api_key: str | None = None
    context_length: int
    max_output_tokens: int
    temperature: float = 0.7
    input_price: float | None = None
    output_price: float | None = None
    cached_input_price: float | None = None


class ModelUpdateRequest(CamelModel):
    """全量更新；api_key 为 None 表示保留原密钥，提供非空字符串时替换。"""

    display_name: str
    model_identifier: str
    base_url: str
    api_key: str | None = None
    context_length: int
    max_output_tokens: int
    temperature: float
    input_price: float | None = None
    output_price: float | None = None
    cached_input_price: float | None = None


class ModelOut(CamelModel):
    id: int
    display_name: str
    model_identifier: str
    base_url: str
    context_length: int
    max_output_tokens: int
    temperature: float
    input_price: float | None
    output_price: float | None
    cached_input_price: float | None
    is_default: bool
    has_api_key: bool
    created_at: datetime
    updated_at: datetime

    @field_serializer("created_at", "updated_at")
    def serialize_datetime(self, value: datetime) -> str:
        # 数据库存的是 naive UTC；补上 UTC 标记，前端 new Date 可正确转本地时间
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()


class TestConnectionOut(CamelModel):
    ok: bool
    reply: str | None = None
    error_type: str | None = None
    message: str | None = None
    hint: str | None = None


# Pydantic 类型/约束错误 → 字段级中文提示（key 为驼峰字段名，与前端表单字段一致）
FIELD_ERROR_MESSAGES: dict[str, str] = {
    "displayName": "请填写显示名称",
    "modelIdentifier": "请填写模型标识",
    "baseUrl": "请填写以 http:// 或 https:// 开头的完整服务地址",
    "apiKey": "API Key 格式不正确",
    "contextLength": "上下文长度必须为正整数",
    "maxOutputTokens": "最大输出长度必须为正整数",
    "temperature": "请填写 0–2 之间的温度数字",
    "inputPrice": "输入单价不能为负数",
    "outputPrice": "输出单价不能为负数",
    "cachedInputPrice": "缓存命中输入单价不能为负数",
}
