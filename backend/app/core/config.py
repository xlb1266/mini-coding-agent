"""应用配置：环境变量优先（前缀 MINI_AGENT_），其余使用默认值。"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/ 目录
BASE_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    app_name: str = "mini-agent"
    app_version: str = "0.1.0"
    environment: str = "development"

    # SQLite 是本项目唯一数据库，文件固定放在 backend/ 目录
    database_url: str = f"sqlite:///{BASE_DIR / 'mini_agent.db'}"

    # Skill 文件目录：每个 Skill 一个子目录，内含 SKILL.md（frontmatter + Markdown 正文）
    skills_dir: Path = BASE_DIR.parent / "workspace" / "skills"

    # 模型 API Key 加密用的主密钥文件；自动生成，不入版本库
    master_key_path: Path = BASE_DIR / "secret.key"

    # ---- 内置工具限制（工具说明与执行层共用，见 app/services/tools/） ----
    # 时间工具在未指定时区时使用的默认时区（IANA 名称）
    default_timezone: str = "Asia/Shanghai"
    # 文件读写工具的授权目录：只允许读写该目录内的文件
    tool_workspace_dir: Path = BASE_DIR / "tool_workspace"
    # Shell 工具：默认与最大超时秒数、stdout/stderr 各自保留的最大字符数
    tool_shell_timeout_seconds: int = 30
    tool_shell_timeout_max_seconds: int = 120
    tool_shell_output_limit: int = 10000
    # 文件读写工具：单次读取/写入的最大字节数
    tool_file_size_limit: int = 512 * 1024

    # MCP 工具在 Agent 运行期间的单次调用超时秒数
    mcp_tool_call_timeout_seconds: float = 60.0

    # 前端开发服务器地址；日常开发走 Vite 代理（同源），此配置仅作直连兜底
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="MINI_AGENT_",
        extra="ignore",
    )


settings = Settings()
