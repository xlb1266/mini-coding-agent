"""统一执行入口与时间/文件工具的单元测试。

重点验证：执行前校验（启停、参数）、执行后统一结果、
异常兜底（任何失败都变成 failed 结果而不是抛出）。
"""

import types
from pathlib import Path

import pytest

from app.core.config import settings
from app.db.models.tool import Tool
from app.services.tools import executor as executor_module
from app.services.tools.base import (
    ERROR_BLOCKED,
    ERROR_DISABLED,
    ERROR_FAILED,
    ERROR_INVALID_PARAMS,
    ERROR_TIMEOUT,
)
from app.services.tools.executor import execute_tool
from app.services.tools.registry import get_definition


def make_tool(key: str = "builtin_current_time", enabled: bool = True) -> Tool:
    definition = get_definition(key)
    return Tool(key=key, name=definition.name, description="d", parameters_json="[]", enabled=enabled)


def result_for(key: str, params: dict, enabled: bool = True):
    return execute_tool(make_tool(key, enabled), params)


class TestExecutorGuards:
    def test_disabled_tool(self) -> None:
        result = result_for("builtin_current_time", {}, enabled=False)

        assert result.ok is False
        assert result.error_type == ERROR_DISABLED

    def test_unknown_tool_key_has_no_executor(self) -> None:
        tool = Tool(key="user_custom", name="自定义", description="d", parameters_json="[]", enabled=True)

        result = execute_tool(tool, {})

        assert result.ok is False
        assert result.error_type == ERROR_FAILED

    def test_unexpected_exception_is_caught(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(params: dict) -> dict:
            raise RuntimeError("boom")

        fake = types.SimpleNamespace(key="fake", params=[], handler=boom)
        monkeypatch.setattr(executor_module, "get_definition", lambda key: fake)

        tool = Tool(key="fake", name="假工具", description="d", parameters_json="[]", enabled=True)
        result = executor_module.execute_tool(tool, {})

        assert result.ok is False
        assert result.error_type == ERROR_FAILED
        assert "boom" in result.message

    def test_unknown_param_rejected(self) -> None:
        result = result_for("builtin_current_time", {"foo": 1})

        assert result.error_type == ERROR_INVALID_PARAMS
        assert result.fields == {"foo": "不支持的工具参数"}

    def test_wrong_param_type_rejected(self) -> None:
        result = result_for("builtin_shell", {"command": "echo hi", "timeout": "abc"})

        assert result.error_type == ERROR_INVALID_PARAMS
        assert "整数" in result.fields["timeout"]


class TestCurrentTime:
    def test_default_timezone_from_settings(self) -> None:
        result = result_for("builtin_current_time", {})

        assert result.ok is True
        assert result.data["timezone"] == settings.default_timezone
        assert "T" in result.data["datetime"]
        assert result.data["weekday"].startswith("星期")

    def test_utc_offset(self) -> None:
        result = result_for("builtin_current_time", {"timezone": "Etc/UTC"})

        assert result.ok is True
        assert result.data["utcOffset"] == "+00:00"

    def test_ambiguous_abbreviation_rejected(self) -> None:
        result = result_for("builtin_current_time", {"timezone": "CST"})

        assert result.error_type == ERROR_INVALID_PARAMS
        assert "IANA" in result.fields["timezone"]


class TestShell:
    def test_nonzero_exit_is_successful_execution(self) -> None:
        result = result_for("builtin_shell", {"command": 'python -c "import sys; sys.exit(3)"'})

        assert result.ok is True
        assert result.data["exitCode"] == 3
        assert result.data["success"] is False

    def test_missing_command(self) -> None:
        result = result_for("builtin_shell", {"command": "   "})

        assert result.error_type == ERROR_INVALID_PARAMS
        assert "command" in result.fields

    def test_timeout_range_enforced(self) -> None:
        for timeout in (0, -1, settings.tool_shell_timeout_max_seconds + 1):
            result = result_for("builtin_shell", {"command": "echo hi", "timeout": timeout})

            assert result.error_type == ERROR_INVALID_PARAMS
            assert "timeout" in result.fields

    def test_timeout_terminates_command(self) -> None:
        result = result_for("builtin_shell", {"command": 'python -c "import time; time.sleep(5)"', "timeout": 1})

        assert result.ok is False
        assert result.error_type == ERROR_TIMEOUT

    def test_output_truncated(self) -> None:
        result = result_for("builtin_shell", {"command": "python -c \"print('x' * 50000)\""})

        assert result.ok is True
        assert result.data["truncated"] is True
        assert "已截断" in result.data["stdout"]

    def test_blocked_command_never_runs(self) -> None:
        result = result_for("builtin_shell", {"command": "rm -rf /"})

        assert result.ok is False
        assert result.error_type == ERROR_BLOCKED


class TestFile:
    def test_write_creates_parent_directories(self, workspace: Path) -> None:
        result = result_for("builtin_file", {"action": "write", "path": "a/b/c.txt", "content": "hi"})

        assert result.ok is True
        assert result.data["outcome"] == "created"
        assert (workspace / "a" / "b" / "c.txt").read_text(encoding="utf-8") == "hi"

    def test_create_mode_fails_when_exists(self, workspace: Path) -> None:
        result_for("builtin_file", {"action": "write", "path": "a.txt", "content": "v1"})

        result = result_for("builtin_file", {"action": "write", "path": "a.txt", "content": "v2", "mode": "create"})

        assert result.ok is False
        assert result.error_type == ERROR_FAILED
        assert "已存在" in result.message
        assert (workspace / "a.txt").read_text(encoding="utf-8") == "v1"

    def test_overwrite_replaces_content(self, workspace: Path) -> None:
        result_for("builtin_file", {"action": "write", "path": "a.txt", "content": "v1"})

        result = result_for("builtin_file", {"action": "write", "path": "a.txt", "content": "v2"})

        assert result.ok is True
        assert result.data["outcome"] == "overwritten"
        assert (workspace / "a.txt").read_text(encoding="utf-8") == "v2"

    def test_append_keeps_existing_content(self, workspace: Path) -> None:
        result_for("builtin_file", {"action": "write", "path": "log.txt", "content": "one\n"})

        result = result_for("builtin_file", {"action": "write", "path": "log.txt", "content": "two\n", "mode": "append"})

        assert result.ok is True
        assert result.data["outcome"] == "appended"
        assert (workspace / "log.txt").read_text(encoding="utf-8") == "one\ntwo\n"

    def test_append_creates_missing_file(self, workspace: Path) -> None:
        result = result_for("builtin_file", {"action": "write", "path": "new.log", "content": "x", "mode": "append"})

        assert result.ok is True
        assert (workspace / "new.log").exists()

    def test_invalid_write_mode(self) -> None:
        result = result_for("builtin_file", {"action": "write", "path": "a.txt", "content": "x", "mode": "delete"})

        assert result.error_type == ERROR_INVALID_PARAMS
        assert "mode" in result.fields

    def test_write_requires_content(self) -> None:
        result = result_for("builtin_file", {"action": "write", "path": "a.txt"})

        assert result.error_type == ERROR_INVALID_PARAMS
        assert "content" in result.fields

    def test_empty_content_creates_empty_file(self, workspace: Path) -> None:
        result = result_for("builtin_file", {"action": "write", "path": "empty.txt", "content": ""})

        assert result.ok is True
        assert result.data["sizeBytes"] == 0

    def test_read_missing_file(self, workspace: Path) -> None:
        result = result_for("builtin_file", {"action": "read", "path": "missing.txt"})

        assert result.ok is False
        assert result.error_type == ERROR_FAILED
        assert "文件不存在" in result.message

    def test_read_directory_rejected(self, workspace: Path) -> None:
        (workspace / "sub").mkdir(parents=True)

        result = result_for("builtin_file", {"action": "read", "path": "sub"})

        assert result.ok is False
        assert result.error_type == ERROR_FAILED
        assert "目录" in result.message

    def test_path_outside_workspace_rejected(self, workspace: Path, tmp_path: Path) -> None:
        sibling = tmp_path / "outside.txt"

        for path in ("../outside.txt", str(sibling), "sub/../../escape.txt"):
            result = result_for("builtin_file", {"action": "read", "path": path})

            assert result.error_type == ERROR_INVALID_PARAMS, path
            assert "path" in result.fields

    def test_write_size_limit(self, workspace: Path) -> None:
        big = "x" * (settings.tool_file_size_limit + 1)

        result = result_for("builtin_file", {"action": "write", "path": "big.txt", "content": big})

        assert result.ok is False
        assert result.error_type == ERROR_FAILED
        assert "上限" in result.message
