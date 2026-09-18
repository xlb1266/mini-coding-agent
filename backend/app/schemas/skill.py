"""Skills 管理的请求/响应模型。

与模型管理一致，接口层使用驼峰命名；updated_at 取 SKILL.md 的修改时间，
数据库存 naive UTC，序列化时补上时区标记供前端转本地时间。
"""

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, field_serializer
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class SkillOut(CamelModel):
    id: int
    dir_name: str
    name: str
    description: str
    enabled: bool
    created_at: datetime
    updated_at: datetime

    @field_serializer("created_at", "updated_at")
    def serialize_datetime(self, value: datetime) -> str:
        # 数据库存的是 naive UTC；补上 UTC 标记，前端 new Date 可正确转本地时间
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()


class SkillDetailOut(SkillOut):
    """单个 Skill 的完整内容：instructions 为 SKILL.md 中 frontmatter 之后的正文。"""

    instructions: str


class SkillUpdateRequest(CamelModel):
    """编辑保存：名称与说明写入 frontmatter，指令为 Markdown 正文。"""

    name: str
    description: str
    instructions: str


class SkillEnabledRequest(CamelModel):
    enabled: bool


# Pydantic 类型/缺参错误 → 字段级中文提示（key 为驼峰字段名，与前端表单字段一致）
FIELD_ERROR_MESSAGES: dict[str, str] = {
    "name": "请填写名称",
    "description": "请填写用途说明",
    "instructions": "请填写详细指令",
    "file": "请选择 ZIP 压缩包",
}
