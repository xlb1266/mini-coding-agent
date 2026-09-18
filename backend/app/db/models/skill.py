"""Skills 管理业务表：workspace/skills 目录下 Skill 的登记与启用状态。

Skill 的正文（SKILL.md）以文件为唯一数据源，本表只保存扫描时缓存
的名称/说明/更新时间，以及独立于文件的启用开关；目录被手动删除后，
下次扫描会同步清理对应记录。
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class Skill(Base):
    """一个位于 workspace/skills/<dir_name>/SKILL.md 的技能说明。"""

    __tablename__ = "skills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 目录名是 Skill 的稳定标识；编辑名称只改 frontmatter，不重命名目录
    dir_name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(String(512), nullable=False)
    # 启用状态只存数据库：文件保留在磁盘上，停用后不再提供给 Agent 使用
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    # 取 SKILL.md 的修改时间（naive UTC），手动改文件后刷新即可看到
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
