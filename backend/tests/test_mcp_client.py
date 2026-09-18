"""MCP stdio 客户端服务测试：用真实子进程验证握手、工具发现与错误分类。"""

import asyncio
import os
import sys
import time
from pathlib import Path

import pytest

from app.services.mcp_client import (
    ERROR_TYPE_COMMAND_NOT_FOUND,
    ERROR_TYPE_PROTOCOL,
    ERROR_TYPE_SERVER,
    ERROR_TYPE_START,
    ERROR_TYPE_TIMEOUT,
    McpSession,
    McpTestError,
    _get_mcp_loop,
    _resolve_launch,
    discover_tools,
    redact,
)

# 假 MCP Server：argv[1] 区分行为；按 MCP 规范回应 initialize 与 tools/list
STDIO_SERVER_SCRIPT = '''
import json
import sys

sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")
mode = sys.argv[1] if len(sys.argv) > 1 else "ok"

WEATHER_SCHEMA = {
    "type": "object",
    "properties": {
        "city": {"type": "string", "description": "城市名"},
        "days": {"type": "integer", "description": "天数"},
    },
    "required": ["city"],
}

READ_FILE_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "文件路径"},
        "force": {"type": "boolean", "description": "强制覆盖"},
    },
    "required": ["path"],
}


def tool(name, description, schema):
    return {"name": name, "description": description, "inputSchema": schema}


def send(obj):
    print(json.dumps(obj, ensure_ascii=False), flush=True)


for raw in sys.stdin:
    raw = raw.strip()
    if not raw:
        continue
    try:
        message = json.loads(raw)
    except ValueError:
        continue
    method = message.get("method")
    request_id = message.get("id")
    if method == "initialize":
        if mode == "protocol":
            send({"jsonrpc": "2.0", "id": request_id,
                  "result": {"serverInfo": {"name": "fake"}}})
        elif mode == "server_error":
            send({"jsonrpc": "2.0", "id": request_id,
                  "error": {"code": -32601, "message": "initialize not supported"}})
        elif mode == "garbage":
            print("这不是 JSON 输出", flush=True)
        else:
            send({"jsonrpc": "2.0", "id": request_id,
                  "result": {"protocolVersion": "2025-06-18", "capabilities": {},
                             "serverInfo": {"name": "fake", "version": "1.0"}}})
    elif method == "tools/list":
        params = message.get("params") or {}
        if mode == "paginated" and "cursor" not in params:
            send({"jsonrpc": "2.0", "id": request_id,
                  "result": {"tools": [tool("tool_a", "查询天气", WEATHER_SCHEMA)],
                             "nextCursor": "page-2"}})
        elif mode == "paginated":
            send({"jsonrpc": "2.0", "id": request_id,
                  "result": {"tools": [tool("tool_b", "读取文件", READ_FILE_SCHEMA)]}})
        elif mode == "empty":
            send({"jsonrpc": "2.0", "id": request_id, "result": {"tools": []}})
        else:
            send({"jsonrpc": "2.0", "id": request_id,
                  "result": {"tools": [tool("tool_a", "查询天气", WEATHER_SCHEMA),
                                       tool("tool_b", "读取文件", READ_FILE_SCHEMA)]}})
    elif method == "tools/call":
        params = message.get("params") or {}
        arguments = params.get("arguments") or {}
        send({"jsonrpc": "2.0", "id": request_id,
              "result": {"content": [{"type": "text",
                                      "text": "工具 " + str(params.get("name")) + " 执行完成："
                                              + json.dumps(arguments, ensure_ascii=False)}]}})
'''

# 收到请求也不回应，用于验证超时路径
SILENT_SCRIPT = '''
import time

time.sleep(60)
'''

# 启动即退出并打印环境变量到 stderr，用于验证退出码归类与密钥脱敏
NOISY_EXIT_SCRIPT = '''
import os
import sys

sys.stderr.write("boom: token=" + os.environ.get("FAKE_TOKEN", "") + "\\n")
sys.exit(7)
'''

EXIT_IMMEDIATELY_SCRIPT = '''
import sys

sys.exit(3)
'''


def python_command(tmp_path: Path, name: str, body: str, *args: str) -> list[str]:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return [sys.executable, str(path), *args]


class TestDiscoverTools:
    def test_success_discovers_tools_with_params(self, tmp_path: Path) -> None:
        command = python_command(tmp_path, "server.py", STDIO_SERVER_SCRIPT, "ok")

        tools = discover_tools(command[0], command[1:], {})

        assert [t["name"] for t in tools] == ["tool_a", "tool_b"]
        weather = tools[0]
        assert weather["description"] == "查询天气"
        assert weather["params"][0] == {
            "name": "city",
            "type": "string",
            "required": True,
            "description": "城市名",
        }
        assert weather["params"][1]["required"] is False

    def test_paginated_tools_collected_in_order(self, tmp_path: Path) -> None:
        command = python_command(tmp_path, "server.py", STDIO_SERVER_SCRIPT, "paginated")

        tools = discover_tools(command[0], command[1:], {})

        assert [t["name"] for t in tools] == ["tool_a", "tool_b"]

    def test_server_without_tools_returns_empty_list(self, tmp_path: Path) -> None:
        command = python_command(tmp_path, "server.py", STDIO_SERVER_SCRIPT, "empty")

        assert discover_tools(command[0], command[1:], {}) == []

    def test_command_not_found(self) -> None:
        with pytest.raises(McpTestError) as excinfo:
            discover_tools("no-such-mcp-exe-xyz-981", [], {})

        assert excinfo.value.error_type == ERROR_TYPE_COMMAND_NOT_FOUND
        assert "启动命令不存在" in excinfo.value.message

    def test_process_exits_before_handshake(self, tmp_path: Path) -> None:
        command = python_command(tmp_path, "exit.py", EXIT_IMMEDIATELY_SCRIPT)

        with pytest.raises(McpTestError) as excinfo:
            discover_tools(command[0], command[1:], {})

        assert excinfo.value.error_type == ERROR_TYPE_START
        assert "退出码 3" in excinfo.value.message

    def test_stderr_secret_is_redacted(self, tmp_path: Path) -> None:
        command = python_command(tmp_path, "noisy.py", NOISY_EXIT_SCRIPT)

        with pytest.raises(McpTestError) as excinfo:
            discover_tools(command[0], command[1:], {"FAKE_TOKEN": "super-secret-token-123"})

        message = excinfo.value.message
        assert excinfo.value.error_type == ERROR_TYPE_START
        assert "super-secret-token-123" not in message
        assert "***" in message

    def test_garbage_output_is_protocol_error(self, tmp_path: Path) -> None:
        command = python_command(tmp_path, "server.py", STDIO_SERVER_SCRIPT, "garbage")

        with pytest.raises(McpTestError) as excinfo:
            discover_tools(command[0], command[1:], {})

        assert excinfo.value.error_type == ERROR_TYPE_PROTOCOL

    def test_initialize_error_is_server_error(self, tmp_path: Path) -> None:
        command = python_command(tmp_path, "server.py", STDIO_SERVER_SCRIPT, "server_error")

        with pytest.raises(McpTestError) as excinfo:
            discover_tools(command[0], command[1:], {})

        assert excinfo.value.error_type == ERROR_TYPE_SERVER
        assert "initialize not supported" in excinfo.value.message

    def test_missing_protocol_version_is_protocol_error(self, tmp_path: Path) -> None:
        command = python_command(tmp_path, "server.py", STDIO_SERVER_SCRIPT, "protocol")

        with pytest.raises(McpTestError) as excinfo:
            discover_tools(command[0], command[1:], {})

        assert excinfo.value.error_type == ERROR_TYPE_PROTOCOL
        assert "protocolVersion" in excinfo.value.message

    def test_timeout_and_process_cleanup(self, tmp_path: Path) -> None:
        command = python_command(tmp_path, "silent.py", SILENT_SCRIPT)

        started = time.monotonic()
        with pytest.raises(McpTestError) as excinfo:
            discover_tools(command[0], command[1:], {}, timeout=1.0)
        elapsed = time.monotonic() - started

        assert excinfo.value.error_type == ERROR_TYPE_TIMEOUT
        # 超时后应在宽限与强杀时间内完成清理，而不是一直挂住
        assert elapsed < 15


class TestWindowsCommandWrappers:
    """Windows 上 npx / uvx 这类 .cmd 包装脚本要能直接启动。"""

    def test_cmd_wrapper_launched_via_cmd_exe(self, tmp_path: Path) -> None:
        if os.name != "nt":
            pytest.skip("仅 Windows 需要 cmd.exe 包装 .cmd 脚本")
        script = tmp_path / "server.py"
        script.write_text(STDIO_SERVER_SCRIPT, encoding="utf-8")
        wrapper = tmp_path / "fake-mcp.cmd"
        wrapper.write_text(
            f'@echo off\r\n"{sys.executable}" "{script}" ok\r\n', encoding="ascii"
        )

        tools = discover_tools(str(wrapper), [], {})

        assert [t["name"] for t in tools] == ["tool_a", "tool_b"]

    def test_plain_executable_started_as_is(self) -> None:
        command, args = _resolve_launch(sys.executable, ["-c", "pass"])

        assert command == sys.executable
        assert args == ["-c", "pass"]

    def test_cmd_wrapper_resolved_to_comspec(self, tmp_path: Path) -> None:
        if os.name != "nt":
            pytest.skip("仅 Windows 会包装 .cmd")
        wrapper = tmp_path / "fake-mcp.cmd"
        wrapper.write_text("@echo off\r\n", encoding="ascii")

        command, args = _resolve_launch(str(wrapper), ["-y", "mcp-server-fetch"])

        assert Path(command).name.lower() == "cmd.exe"
        assert args == ["/c", str(wrapper), "-y", "mcp-server-fetch"]

    def test_missing_command_kept_for_error_reporting(self) -> None:
        command, args = _resolve_launch("no-such-mcp-exe-xyz-981", ["--flag"])

        assert command == "no-such-mcp-exe-xyz-981"
        assert args == ["--flag"]


class TestLoopIndependence:
    """子进程 I/O 必须跑在 MCP 专用循环上，不能依赖调用方所处的循环。

    Windows 上 uvicorn 以 --reload / --workers 启动时给的是 SelectorEventLoop，
    它不支持 asyncio 的子进程传输，直接在其中 create_subprocess_exec 会抛
    NotImplementedError —— 这正是「给 Agent 配了 MCP 后一聊天就报错」的原因。
    """

    def test_mcp_loop_supports_subprocesses(self) -> None:
        loop = _get_mcp_loop()

        assert not loop.is_closed()
        if os.name == "nt":
            assert isinstance(loop, asyncio.ProactorEventLoop)

    def test_session_starts_inside_selector_event_loop(self, tmp_path: Path) -> None:
        if os.name != "nt":
            pytest.skip("SelectorEventLoop 不支持子进程是 Windows 特有的问题")
        command = python_command(tmp_path, "server.py", STDIO_SERVER_SCRIPT, "ok")

        async def scenario() -> tuple[list[str], str]:
            session = McpSession(command[0], command[1:], {})
            try:
                tools = await session.start()
                result = await session.call_tool("tool_a", {"city": "北京"})
                return [tool["name"] for tool in tools], str(result["text"])
            finally:
                await session.close()

        loop = asyncio.SelectorEventLoop()  # 与 uvicorn --reload 在 Windows 上等价
        try:
            names, text = loop.run_until_complete(scenario())
        finally:
            loop.close()

        assert names == ["tool_a", "tool_b"]
        assert "工具 tool_a 执行完成" in text

    def test_discover_tools_inside_selector_event_loop(self, tmp_path: Path) -> None:
        if os.name != "nt":
            pytest.skip("SelectorEventLoop 不支持子进程是 Windows 特有的问题")
        command = python_command(tmp_path, "server.py", STDIO_SERVER_SCRIPT, "empty")

        async def scenario() -> list[dict]:
            return await asyncio.to_thread(discover_tools, command[0], command[1:], {})

        loop = asyncio.SelectorEventLoop()
        try:
            tools = loop.run_until_complete(scenario())
        finally:
            loop.close()

        assert tools == []

    def test_cancel_mid_handshake_releases_process(self, tmp_path: Path) -> None:
        """运行被中断（用户点停止）时，握手中的子进程也要被回收。"""
        command = python_command(tmp_path, "silent.py", SILENT_SCRIPT)

        async def scenario() -> asyncio.subprocess.Process | None:
            session = McpSession(command[0], command[1:], {}, timeout=30)
            task = asyncio.ensure_future(session.start())
            await asyncio.sleep(1.0)
            spawned = session._core._proc
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            await session.close()  # 收尾幂等：取消后仍可正常关闭（已在后台清理时立即返回）
            # 关进程先关 stdin 等它自行退出（宽限期），再 terminate/kill，因此这里轮询等待回收
            deadline = time.monotonic() + 15
            while spawned is not None and spawned.returncode is None and time.monotonic() < deadline:
                await asyncio.sleep(0.2)
            return spawned

        loop = asyncio.SelectorEventLoop()
        try:
            spawned = loop.run_until_complete(scenario())
        finally:
            loop.close()

        assert spawned is not None  # 确认确实启动过子进程
        assert spawned.returncode is not None  # 且已经退出，没有残留


class TestRedact:
    def test_replaces_all_occurrences(self) -> None:
        assert redact("cmd --key abc and abc", ["abc"]) == "cmd --key *** and ***"

    def test_longer_secret_replaced_first(self) -> None:
        assert redact("abc abcdef", ["abc", "abcdef"]) == "*** ***"

    def test_empty_secret_ignored(self) -> None:
        assert redact("plain text", [""]) == "plain text"
