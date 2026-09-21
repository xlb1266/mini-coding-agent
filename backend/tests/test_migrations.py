"""Alembic 迁移链测试：在临时数据库上执行 upgrade/downgrade 往返。"""

import os
import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]


def run_alembic(db_url: str, *args: str) -> None:
    env = {
        **os.environ,
        "MINI_AGENT_DATABASE_URL": db_url,
        "PYTHONIOENCODING": "utf-8",
    }
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode == 0, f"alembic {' '.join(args)} 失败：\n{result.stdout}\n{result.stderr}"


def table_names(db_path: Path) -> list[str]:
    import sqlite3

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return sorted(row[0] for row in rows)


def column_names(db_path: Path, table: str) -> list[str]:
    import sqlite3

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return sorted(row[1] for row in rows)


def seed_tool_keys(db_path: Path) -> list[str]:
    import sqlite3

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT key FROM tools").fetchall()
    return sorted(row[0] for row in rows)


def test_migration_upgrade_downgrade_roundtrip(tmp_path: Path) -> None:
    db_path = tmp_path / "migration-test.db"
    db_url = f"sqlite:///{db_path.as_posix()}"

    run_alembic(db_url, "upgrade", "head")
    tables = table_names(db_path)
    assert "model_configs" in tables
    assert "model_secrets" in tables
    assert "skills" in tables
    assert "mcp_servers" in tables
    assert "mcp_env_vars" in tables
    assert "agents" in tables
    assert "agent_bindings" in tables
    # 阶段十：上下文压缩配置与会话摘要/边界
    assert {"auto_compact", "compact_threshold", "compact_keep_rounds", "compact_summary_tokens"} <= set(
        column_names(db_path, "agents")
    )
    assert {"summary_text", "summary_boundary_seq"} <= set(column_names(db_path, "chat_sessions"))
    # 模块一：Agent 工作区目录与审批开关；三个内置编码工具种子
    assert {"workspace_dir", "require_approval"} <= set(column_names(db_path, "agents"))
    assert set(seed_tool_keys(db_path)) >= {
        "builtin_current_time",
        "builtin_shell",
        "builtin_file",
        "builtin_edit_file",
        "builtin_list_dir",
        "builtin_grep",
    }

    run_alembic(db_url, "downgrade", "base")
    assert "model_configs" not in table_names(db_path)
    assert "model_secrets" not in table_names(db_path)
    assert "skills" not in table_names(db_path)
    assert "mcp_servers" not in table_names(db_path)
    assert "mcp_env_vars" not in table_names(db_path)
    assert "agents" not in table_names(db_path)
    assert "agent_bindings" not in table_names(db_path)

    run_alembic(db_url, "upgrade", "head")
    assert "model_configs" in table_names(db_path)
    assert "skills" in table_names(db_path)
    assert "mcp_servers" in table_names(db_path)


def _run_python(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def test_app_and_models_import_in_any_order() -> None:
    """uvicorn 以 app.main 为起点加载；任何模型模块先导入也不能出现循环导入。"""
    for code in (
        "import app.main",
        "import app.db.models.tool",
        "import app.db.models.skill",
        "import app.db.models.model_config",
    ):
        result = _run_python(code)
        assert result.returncode == 0, f"导入 {code!r} 失败：\n{result.stderr}"
