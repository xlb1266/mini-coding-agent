"""工具管理业务表：本地工具的注册信息与启停状态。

工具的名称、用途、参数说明落库供前端展示与后续 Agent 挑选；
实际执行逻辑在 app/services/tools/ 中按 key 绑定。内置工具由迁移
0005 写入种子数据，只允许启停，不允许删除或修改用途。
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class Tool(Base):
    """一个可被 Agent 调用的本地工具。"""

    __tablename__ = "tools"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 稳定标识，执行层用它绑定具体实现（如 builtin_current_time）
    key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    # 参数说明列表的 JSON 序列化：[{name, type, required, description, example}]
    parameters_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
