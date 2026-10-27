"""模块一新增内置工具与工作区跟随的单元测试。

覆盖：edit_file / list_dir / grep 三个 handler 的各场景、
Shell 命令的工作目录落在 ToolContext 工作区、
自定义 context 的沙箱隔离与未传 context 时回落全局默认目录。
"""

from pathlib import Path

import pytest

from app.core.config import settings
from app.db.models.tool import Tool
from app.services.tools.base import (
    ERROR_FAILED,
    ERROR_INVALID_PARAMS,
    ToolContext,
)
from app.services.tools.executor import execute_tool
from app.services.tools.registry import get_definition


def make_tool(key: str) -> Tool:
    definition = get_definition(key)
    return Tool(key=key, name=definition.name, description="d", parameters_json="[]", enabled=True)


def result_for(key: str, params: dict, context: ToolContext | None = None):
    return execute_tool(make_tool(key), params, context)


@pytest.fixture()
def ws(tmp_path: Path) -> Path:
    """自定义工作区目录（模拟 Agent 配置的 workspace_dir）。"""
    target = tmp_path / "agent-ws"
    target.mkdir()
    return target


@pytest.fixture()
def ctx(ws: Path) -> ToolContext:
    return ToolContext(workspace_root=ws)


class TestEditFile:
    def test_replace_once_and_diff(self, ws: Path, ctx: ToolContext) -> None:
        (ws / "code.py").write_text("def hello():\n    return 1\n", encoding="utf-8")

        result = result_for(
            "builtin_edit_file",
            {
                "path": "code.py",
                "old_string": "return 1",
                "new_string": "return 2",
            },
            ctx,
        )

        assert result.ok is True
        assert result.data["replacements"] == 1
        assert (ws / "code.py").read_text(encoding="utf-8") == "def hello():\n    return 2\n"
        diff = result.data["diff"]
        assert "-    return 1" in diff
        assert "+    return 2" in diff

    def test_old_string_not_found(self, ws: Path, ctx: ToolContext) -> None:
        (ws / "a.txt").write_text("hello", encoding="utf-8")

        result = result_for("builtin_edit_file", {"path": "a.txt", "old_string": "world", "new_string": "x"}, ctx)

        assert result.ok is False
        assert result.error_type == ERROR_FAILED
        assert "未找到" in result.message

    def test_multiple_occurrences_require_replace_all(self, ws: Path, ctx: ToolContext) -> None:
        (ws / "a.txt").write_text("x\nx\nx\n", encoding="utf-8")

        result = result_for("builtin_edit_file", {"path": "a.txt", "old_string": "x", "new_string": "y"}, ctx)

        assert result.ok is False
        assert result.error_type == ERROR_FAILED
        assert "多处" in result.message
        assert (ws / "a.txt").read_text(encoding="utf-8") == "x\nx\nx\n"  # 未改动

    def test_replace_all_replaces_every_occurrence(self, ws: Path, ctx: ToolContext) -> None:
        (ws / "a.txt").write_text("x\nx\nx\n", encoding="utf-8")

        result = result_for(
            "builtin_edit_file",
            {"path": "a.txt", "old_string": "x", "new_string": "y", "replace_all": True},
            ctx,
        )

        assert result.ok is True
        assert result.data["replacements"] == 3
        assert (ws / "a.txt").read_text(encoding="utf-8") == "y\ny\ny\n"

    def test_empty_old_string_rejected(self, ctx: ToolContext) -> None:
        result = result_for("builtin_edit_file", {"path": "a.txt", "old_string": "", "new_string": "y"}, ctx)

        assert result.error_type == ERROR_INVALID_PARAMS
        assert "old_string" in result.fields

    def test_missing_file(self, ctx: ToolContext) -> None:
        result = result_for("builtin_edit_file", {"path": "missing.txt", "old_string": "a", "new_string": "b"}, ctx)

        assert result.ok is False
        assert result.error_type == ERROR_FAILED
        assert "文件不存在" in result.message

    def test_path_outside_context_workspace_rejected(
        self, ws: Path, ctx: ToolContext, workspace: Path
    ) -> None:
        # 用绝对路径指向全局默认工作区里的文件：自定义 context 沙箱下被拒绝
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "inside-default.txt").write_text("a", encoding="utf-8")

        result = result_for(
            "builtin_edit_file",
            {"path": str(workspace / "inside-default.txt"), "old_string": "a", "new_string": "b"},
            ctx,
        )

        assert result.error_type == ERROR_INVALID_PARAMS
        assert "path" in result.fields

    def test_edit_respects_size_limit(self, ws: Path, ctx: ToolContext) -> None:
        big = "x" * (settings.tool_file_size_limit + 1)
        (ws / "big.txt").write_text(big, encoding="utf-8")

        result = result_for("builtin_edit_file", {"path": "big.txt", "old_string": "x", "new_string": "y"}, ctx)

        assert result.ok is False
        assert result.error_type == ERROR_FAILED
        assert "上限" in result.message


class TestListDir:
    def test_flat_listing_dirs_first_sorted(self, ws: Path, ctx: ToolContext) -> None:
        (ws / "z-dir").mkdir()
        (ws / "a-dir").mkdir()
        (ws / "b.txt").write_text("hello", encoding="utf-8")
        (ws / "a.txt").write_text("hi", encoding="utf-8")

        result = result_for("builtin_list_dir", {}, ctx)

        assert result.ok is True
        entries = result.data["entries"]
        # 目录在前，同类按名称排序
        assert [e["name"] for e in entries] == ["a-dir", "z-dir", "a.txt", "b.txt"]
        assert [e["type"] for e in entries] == ["directory", "directory", "file", "file"]
        assert next(e for e in entries if e["name"] == "a.txt")["sizeBytes"] == 2
        assert result.data["truncated"] is False

    def test_recursive_respects_depth(self, ws: Path, ctx: ToolContext) -> None:
        # 相对查询目录的层级：l1 为第 1 层，l1/l2/leaf.txt 为第 3 层（含），
        # l1/l2/l3/l4 及更深（第 4 层起）不出现
        (ws / "l1").mkdir()
        (ws / "l1" / "l2").mkdir()
        (ws / "l1" / "l2" / "leaf.txt").write_text("leaf", encoding="utf-8")
        (ws / "l1" / "l2" / "l3").mkdir()
        (ws / "l1" / "l2" / "l3" / "l4").mkdir()
        (ws / "l1" / "l2" / "l3" / "l4" / "deep.txt").write_text("deep", encoding="utf-8")

        result = result_for("builtin_list_dir", {"recursive": True}, ctx)

        names = [e["name"] for e in result.data["entries"]]
        assert "l1/l2/leaf.txt" in names
        assert "l1/l2/l3" in names
        assert all("l4" not in name and "deep" not in name for name in names)

    def test_subdirectory_path(self, ws: Path, ctx: ToolContext) -> None:
        (ws / "src").mkdir()
        (ws / "src" / "main.py").write_text("print(1)", encoding="utf-8")

        result = result_for("builtin_list_dir", {"path": "src"}, ctx)

        assert result.ok is True
        assert [e["name"] for e in result.data["entries"]] == ["main.py"]

    def test_path_outside_workspace_rejected(self, ctx: ToolContext, tmp_path: Path) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()

        result = result_for("builtin_list_dir", {"path": str(outside)}, ctx)

        assert result.error_type == ERROR_INVALID_PARAMS

    def test_file_target_rejected(self, ws: Path, ctx: ToolContext) -> None:
        (ws / "a.txt").write_text("x", encoding="utf-8")

        result = result_for("builtin_list_dir", {"path": "a.txt"}, ctx)

        assert result.ok is False
        assert result.error_type == ERROR_FAILED
        assert "文件" in result.message


class TestGrep:
    def test_matches_with_line_numbers(self, ws: Path, ctx: ToolContext) -> None:
        (ws / "a.py").write_text("def foo():\n    pass\n# TODO fix\n", encoding="utf-8")
        (ws / "b.md").write_text("todo in markdown\n", encoding="utf-8")

        result = result_for("builtin_grep", {"pattern": "todo"}, ctx)

        assert result.ok is True
        matches = result.data["matches"]
        assert {"path": "a.py", "line": 3, "text": "# TODO fix"} not in matches  # 默认区分大小写
        assert {"path": "b.md", "line": 1, "text": "todo in markdown"} in matches
        assert result.data["totalMatches"] == 1
        assert result.data["truncated"] is False

    def test_case_insensitive(self, ws: Path, ctx: ToolContext) -> None:
        (ws / "a.py").write_text("# TODO fix\n", encoding="utf-8")

        result = result_for("builtin_grep", {"pattern": "todo", "case_insensitive": True}, ctx)

        assert result.data["totalMatches"] == 1
        assert result.data["matches"][0]["line"] == 1

    def test_subdirectory_and_glob_filter(self, ws: Path, ctx: ToolContext) -> None:
        (ws / "src").mkdir()
        (ws / "src" / "a.py").write_text("needle\n", encoding="utf-8")
        (ws / "src" / "b.txt").write_text("needle\n", encoding="utf-8")
        (ws / "root.py").write_text("needle\n", encoding="utf-8")

        result = result_for("builtin_grep", {"pattern": "needle", "path": "src", "glob": "*.py"}, ctx)

        assert result.data["filesSearched"] == 1
        assert [m["path"] for m in result.data["matches"]] == ["a.py"]

    def test_invalid_regex_rejected(self, ctx: ToolContext) -> None:
        result = result_for("builtin_grep", {"pattern": "([unclosed"}, ctx)

        assert result.error_type == ERROR_INVALID_PARAMS
        assert "pattern" in result.fields

    def test_max_results_truncates(self, ws: Path, ctx: ToolContext) -> None:
        (ws / "a.txt").write_text("\n".join(f"needle {i}" for i in range(10)) + "\n", encoding="utf-8")

        result = result_for("builtin_grep", {"pattern": "needle", "max_results": 3}, ctx)

        assert result.ok is True
        assert len(result.data["matches"]) == 3
        assert result.data["totalMatches"] == 10
        assert result.data["truncated"] is True

    def test_max_results_range_enforced(self, ctx: ToolContext) -> None:
        for value in (0, 201):
            result = result_for("builtin_grep", {"pattern": "x", "max_results": value}, ctx)
            assert result.error_type == ERROR_INVALID_PARAMS, value
            assert "max_results" in result.fields, value

    def test_binary_and_oversized_files_skipped(self, ws: Path, ctx: ToolContext) -> None:
        (ws / "blob.bin").write_bytes(b"needle\x00needle")
        (ws / "big.txt").write_text("needle" + "x" * (settings.tool_file_size_limit + 1), encoding="utf-8")
        (ws / "ok.txt").write_text("needle\n", encoding="utf-8")

        result = result_for("builtin_grep", {"pattern": "needle"}, ctx)

        assert result.data["filesSearched"] == 1
        assert [m["path"] for m in result.data["matches"]] == ["ok.txt"]


class TestWorkspaceFollow:
    """工作区跟随：context 决定沙箱根与 Shell 工作目录，None 回落全局默认。"""

    def test_shell_cwd_falls_in_context_workspace(self, ws: Path, ctx: ToolContext) -> None:
        # Windows 下 cmd 的 cd 输出当前目录；直接用 python 打印 cwd 更跨平台
        result = result_for(
            "builtin_shell",
            {"command": "python -c \"import os; print(os.getcwd())\""},
            ctx,
        )

        assert result.ok is True
        assert Path(result.data["stdout"].strip()).resolve() == ws.resolve()

    def test_shell_cwd_defaults_to_settings_workspace(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 未传 context：回落全局默认目录（workspace 夹具已把 settings 指到临时目录）
        from app.services.tools.handlers import run_shell

        data = run_shell({"command": "python -c \"import os; print(os.getcwd())\""})
        assert Path(data["stdout"].strip()).resolve() == workspace.resolve()

    def test_file_tool_isolated_to_context_workspace(
        self, ws: Path, ctx: ToolContext, workspace: Path
    ) -> None:
        result = result_for("builtin_file", {"action": "write", "path": "in-agent-ws.txt", "content": "hi"}, ctx)

        assert result.ok is True
        assert (ws / "in-agent-ws.txt").exists()
        assert not (workspace / "in-agent-ws.txt").exists()

    def test_file_tool_defaults_to_settings_workspace(self, workspace: Path) -> None:
        result = result_for("builtin_file", {"action": "write", "path": "in-default-ws.txt", "content": "hi"})

        assert result.ok is True
        assert (workspace / "in-default-ws.txt").exists()
