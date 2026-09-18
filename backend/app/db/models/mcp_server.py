"""MCP 管理业务表：本机 stdio MCP Server 配置与测试结果。

启动命令与参数分开保存（args 为 JSON 数组，保持顺序），直接传给子进程，
不拼接成 Shell 命令。环境变量的值可能包含密钥，经 app/core/secret_box.py
加密后存入 mcp_env_vars，任何响应只以掩码出现。
最近一次测试结果（状态、时间、错误、工具列表）直接记录在服务器行上；
修改启动配置后由路由把状态改写为 stale（配置已变更，待重新测试）。
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base


class McpServer(Base):
    """一个通过 stdio 启动的本机 MCP Server 配置。"""

    __tablename__ = "mcp_servers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    # 可执行文件与参数分列保存，不拼接为 Shell 字符串
    command: Mapped[str] = mapped_column(String(512), nullable=False)
    args_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # 最近一次测试结果：untested（NULL）/ success / failed / stale（配置已变更）
    last_test_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_test_error_type: Mapped[str | None] = mapped_column(String(24), nullable=True)
    # 错误信息与工具列表都经过敏感信息脱敏后才落库
    last_test_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_test_tools_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    # 删除 Server 时由 ORM 级联删除加密的环境变量行
    env_vars: Mapped[list["McpEnvVar"]] = relationship(
        back_populates="server",
        cascade="all, delete-orphan",
        order_by="McpEnvVar.id",
    )


class McpEnvVar(Base):
    """MCP Server 环境变量的加密存储，主密钥复用本地 secret.key 文件。"""

    __tablename__ = "mcp_env_vars"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    server_id: Mapped[int] = mapped_column(
        ForeignKey("mcp_servers.id"), nullable=False, index=True
    )
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    value_nonce: Mapped[str] = mapped_column(String(64), nullable=False)
    value_blob: Mapped[str] = mapped_column(Text, nullable=False)

    server: Mapped["McpServer"] = relationship(back_populates="env_vars")
