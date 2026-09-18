"""本机 stdio MCP Server 的最小客户端。

两部分能力：
- discover_tools：一次性握手 + 工具发现，供「测试连接」使用，结束后即清理进程；
- McpSession：一次 Agent 运行期间保持存活的会话，启动后可多次调用工具，
  无论成功、失败还是取消都由调用方 close() 收尾，不留后台子进程。

按 MCP 规范通过 stdio 收发换行分隔的 JSON-RPC 2.0 报文。失败原因被归类为：
启动命令不存在 / 程序启动失败或提前退出 / 超时 / 协议版本或响应格式不兼容 /
Server 主动返回错误 / 未知错误。所有面向用户的文案都经过环境变量值脱敏，
防止密钥意外回显。

子进程 I/O 固定在本模块自己的事件循环上执行（Windows 下为 ProactorEventLoop）：
uvicorn 在 Windows 上以 --reload / --workers 启动时给的是 SelectorEventLoop，
而它不支持 asyncio 的子进程传输（create_subprocess_exec 会抛 NotImplementedError），
所以这里不能依赖调用方所处的循环。
"""

import asyncio
import json
import os
import shutil
import sys
import threading
from collections import deque
from collections.abc import Coroutine
from typing import Any, TypeVar

from app.core.config import settings

# MCP stdio 传输：每行一个 JSON-RPC 报文
PROTOCOL_VERSION = "2025-06-18"

# initialize / tools/list / tools/call 单步等待上限；首次运行可能需要下载依赖，给足时间
DEFAULT_TIMEOUT_SECONDS = 20.0
TOOL_CALL_TIMEOUT_SECONDS = 60.0
# 会话结束后等待进程随 stdin 关闭自行退出的宽限期，超时则 terminate/kill
STDIN_GRACE_SECONDS = 1.5
TERMINATE_TIMEOUT_SECONDS = 3.0
# 诊断信息中保留的 stderr 行数上限
MAX_STDERR_LINES = 8

ERROR_TYPE_COMMAND_NOT_FOUND = "command_not_found"
ERROR_TYPE_START = "start_failed"
ERROR_TYPE_TIMEOUT = "timeout"
ERROR_TYPE_PROTOCOL = "protocol"
ERROR_TYPE_SERVER = "server"
ERROR_TYPE_UNKNOWN = "unknown"

HINT_COMMAND_NOT_FOUND = "请确认程序已安装，或填写完整可执行文件路径；Windows 下注意写清 .exe 的位置，或把所在目录加入 PATH（npx、uvx 这类 .cmd 包装脚本会自动经 cmd.exe 启动）。"
HINT_START = "请确认命令与参数能在终端直接运行，且该程序是 MCP Server；可先手动执行一遍查看报错。"
HINT_TIMEOUT = "程序可能启动较慢或未按 MCP 协议通过 stdio 通信；首次运行需要下载依赖时可稍后重试。"
HINT_PROTOCOL = "请确认该程序是 MCP Server（stdio 传输），且协议实现与当前版本兼容。"
HINT_SERVER = "错误信息来自 MCP Server 本身，请查看它的文档或日志。"
HINT_UNKNOWN = "未能确定具体原因，下方附有经过脱敏的诊断信息，可先检查命令与参数。"


class McpTestError(Exception):
    """MCP 交互失败，携带归类的错误类型、用户文案与排查建议。"""

    def __init__(self, error_type: str, message: str, hint: str) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.message = message
        self.hint = hint


def redact(text: str, secrets: list[str]) -> str:
    """把文本中意外出现的环境变量值替换为 ***，先替换更长的值避免残留片段。"""
    for secret in sorted({s for s in secrets if s}, key=len, reverse=True):
        if secret in text:
            text = text.replace(secret, "***")
    return text


def _snippet(text: str, limit: int = 300) -> str:
    text = " ".join(text.split())
    return text[:limit] + ("…" if len(text) > limit else "")


def _resolve_launch(command: str, args: list[str]) -> tuple[str, list[str]]:
    """解析实际启动的可执行文件。

    Windows 上 npm / uv 安装的 npx、uvx 等是 .cmd 批处理包装，CreateProcess
    无法直接执行，需要经 cmd.exe 启动；参数仍逐项作为 argv 传入，不做字符串拼接。
    非 Windows 或目标是普通可执行文件时按解析到的完整路径启动。
    """
    if os.name != "nt":
        return command, list(args)
    resolved = shutil.which(command) or command
    if resolved.lower().endswith((".cmd", ".bat")):
        comspec = os.environ.get("COMSPEC") or "cmd.exe"
        return comspec, ["/c", resolved, *args]
    return resolved, list(args)


# ---- MCP 专用事件循环：所有子进程 I/O 的执行地 ----

_mcp_loop: asyncio.AbstractEventLoop | None = None
_mcp_loop_thread: threading.Thread | None = None
_mcp_loop_lock = threading.Lock()

_T = TypeVar("_T")


def _new_subprocess_loop() -> asyncio.AbstractEventLoop:
    if sys.platform == "win32":
        # Windows 上只有 Proactor 循环支持子进程
        return asyncio.ProactorEventLoop()
    return asyncio.new_event_loop()


def _run_mcp_loop(loop: asyncio.AbstractEventLoop) -> None:
    asyncio.set_event_loop(loop)
    loop.run_forever()


def _get_mcp_loop() -> asyncio.AbstractEventLoop:
    """取得（首次调用时创建）承载 MCP 子进程 I/O 的专用事件循环。"""
    global _mcp_loop, _mcp_loop_thread
    with _mcp_loop_lock:
        if _mcp_loop is None or _mcp_loop.is_closed():
            loop = _new_subprocess_loop()
            started = threading.Event()
            loop.call_soon(started.set)
            thread = threading.Thread(
                target=_run_mcp_loop, args=(loop,), name="mcp-subprocess-loop", daemon=True
            )
            thread.start()
            if not started.wait(5.0):
                loop.close()
                raise McpTestError(
                    ERROR_TYPE_UNKNOWN,
                    "MCP 专用事件循环启动失败，无法启动 MCP Server",
                    HINT_UNKNOWN,
                )
            _mcp_loop, _mcp_loop_thread = loop, thread
        return _mcp_loop


async def _run_on_mcp_loop(coro: Coroutine[Any, Any, _T]) -> _T:
    """把协程调度到 MCP 专用循环上执行，调用方所处的循环不参与子进程 I/O。"""
    loop = _get_mcp_loop()
    if asyncio.get_running_loop() is loop:  # 防御性：已在专用循环上就直接执行
        return await coro
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    try:
        return await asyncio.wrap_future(future)
    except asyncio.CancelledError:
        # 调用方被取消（如用户中断本次运行）：取消专用循环上的任务，
        # 让它的 finally 照常清理子进程
        future.cancel()
        raise


def discover_tools(
    command: str,
    args: list[str],
    env: dict[str, str],
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> list[dict[str, Any]]:
    """启动 MCP Server 并完成握手，返回工具列表；失败抛 McpTestError。

    同步入口：内部用独立事件循环驱动子进程收发，方便在 FastAPI 的
    线程池路由中直接调用；无论成功、失败还是超时都会清理子进程。
    """
    try:
        raw_tools = asyncio.run(_discover_once(command, list(args), dict(env), timeout))
    except McpTestError:
        raise
    except (OSError, RuntimeError) as exc:  # 进程收尾阶段的系统级意外
        raise McpTestError(
            ERROR_TYPE_UNKNOWN,
            f"测试过程中发生未知错误：{_snippet(redact(str(exc), list(env.values())))}",
            HINT_UNKNOWN,
        ) from None
    return [_format_tool(item) for item in raw_tools]


async def _discover_once(
    command: str, args: list[str], env: dict[str, str], timeout: float
) -> list[dict[str, Any]]:
    session = McpSession(command, args, env, timeout)
    try:
        return await session.start()
    finally:
        await session.close()


def _validate_initialize(result: Any) -> None:
    if not isinstance(result, dict) or not isinstance(result.get("protocolVersion"), str):
        raise McpTestError(
            ERROR_TYPE_PROTOCOL,
            "initialize 响应格式不兼容（缺少 protocolVersion 字段）",
            HINT_PROTOCOL,
        )


def _format_tool(item: dict[str, Any]) -> dict[str, Any]:
    description = item.get("description")
    schema = item.get("inputSchema")
    params: list[dict[str, Any]] = []
    if isinstance(schema, dict) and isinstance(schema.get("properties"), dict):
        required = schema.get("required")
        required_names = {name for name in required if isinstance(name, str)} if isinstance(
            required, list
        ) else set()
        for name, spec in schema["properties"].items():
            if not isinstance(name, str):
                continue
            spec = spec if isinstance(spec, dict) else {}
            param_description = spec.get("description")
            params.append(
                {
                    "name": name,
                    "type": str(spec.get("type") or "any"),
                    "required": name in required_names,
                    "description": (
                        param_description if isinstance(param_description, str) else ""
                    ),
                }
            )
    return {
        "name": item["name"],
        "description": description if isinstance(description, str) else "",
        "params": params,
    }


class _McpSessionCore:
    """会话实现：只在本模块的 MCP 专用循环上运行，不直接对外使用。"""

    def __init__(
        self,
        command: str,
        args: list[str],
        env: dict[str, str],
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._command = command
        self._args = list(args)
        self._env = dict(env)
        self._timeout = timeout
        self._secrets = list(env.values())
        self._merged_env = {**os.environ, **env}
        self._proc: asyncio.subprocess.Process | None = None
        self._stderr_tail: deque[str] = deque(maxlen=MAX_STDERR_LINES)
        self._stderr_task: asyncio.Task | None = None
        self._request_id = 0

    # ---- 生命周期 ----

    async def start(self) -> list[dict[str, Any]]:
        """启动子进程并完成握手，返回原始工具定义列表（含 inputSchema）。"""
        try:
            await self._spawn()
            assert self._proc is not None  # _spawn 保证
            await self._send(
                {
                    "jsonrpc": "2.0",
                    "id": self._next_id(),
                    "method": "initialize",
                    "params": {
                        "protocolVersion": PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "mini-agent", "version": settings.app_version},
                    },
                }
            )
            result = await self._read_result(self._timeout)
            _validate_initialize(result)

            # initialized 是通知，不携带 id，也不等待响应
            await self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            return await self._list_tools()
        except BaseException:
            # 启动失败/被取消也要收尾，避免留下半初始化的子进程。
            # 用 shield 让清理独立于当前任务：即使收尾期间再被取消一次，
            # 关进程的流程也会在专用循环上继续跑完。
            await asyncio.shield(self.close())
            raise

    async def call_tool(self, name: str, arguments: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
        """调用一个工具，返回 {"isError": bool, "text": str}。

        协议层失败（超时、Server 报错、连接中断）抛 McpTestError；
        Server 正常返回的 isError 结果原样带回，由调用方决定如何呈现。
        """
        if timeout is None:
            timeout = settings.mcp_tool_call_timeout_seconds
        await self._send(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
        result = await self._read_result(timeout)
        return _extract_tool_result(result, self._secrets)

    async def close(self) -> None:
        """先关 stdin 让进程自然退出，超时再强杀；可重复调用。

        若启动过程刚被取消，清理可能已在后台进行，此时本方法立即返回（不会重复关进程）；
        子进程会在数秒内被回收。
        """
        proc = self._proc
        self._proc = None
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            try:
                await self._stderr_task
            except (asyncio.CancelledError, OSError):
                pass
            self._stderr_task = None
        if proc is not None:
            await _terminate(proc)

    # ---- 协议内部 ----

    async def _spawn(self) -> None:
        # 原始 command 只用于报错文案；实际启动用解析后的可执行文件与参数
        launch_command, launch_args = _resolve_launch(self._command, self._args)
        try:
            self._proc = await asyncio.create_subprocess_exec(
                launch_command,
                *launch_args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._merged_env,
            )
        except FileNotFoundError:
            raise McpTestError(
                ERROR_TYPE_COMMAND_NOT_FOUND,
                f"启动命令不存在：{_snippet(redact(self._command, self._secrets), 120)}",
                HINT_COMMAND_NOT_FOUND,
            ) from None
        except NotImplementedError:
            # 理论上不会发生（子进程 I/O 一律走 MCP 专用循环）；万一循环不支持子进程，
            # 也给出归类后的提示而不是把 NotImplementedError 漏给上层
            raise McpTestError(
                ERROR_TYPE_START,
                "当前事件循环不支持启动子进程，无法启动 MCP Server",
                HINT_START,
            ) from None
        except OSError as exc:
            raise McpTestError(
                ERROR_TYPE_START,
                f"程序无法启动：{_snippet(redact(str(exc), self._secrets))}",
                HINT_START,
            ) from None
        self._stderr_task = asyncio.create_task(_drain_stderr(self._proc.stderr, self._stderr_tail))

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def _list_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            params: dict[str, str] = {"cursor": cursor} if cursor else {}
            await self._send(
                {"jsonrpc": "2.0", "id": self._next_id(), "method": "tools/list", "params": params}
            )
            result = await self._read_result(self._timeout)
            if not isinstance(result, dict) or not isinstance(result.get("tools"), list):
                raise McpTestError(
                    ERROR_TYPE_PROTOCOL,
                    "tools/list 响应格式不兼容（缺少 tools 列表）",
                    HINT_PROTOCOL,
                )
            for item in result["tools"]:
                if isinstance(item, dict) and isinstance(item.get("name"), str):
                    tools.append(item)
            next_cursor = result.get("nextCursor")
            if not isinstance(next_cursor, str) or not next_cursor:
                return tools
            cursor = next_cursor

    async def _send(self, payload: dict[str, Any]) -> None:
        data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise McpTestError(
                ERROR_TYPE_START,
                "进程的标准输入不可用，无法发送协议消息" + self._stderr_suffix(),
                HINT_START,
            )
        try:
            proc.stdin.write(data)
            await proc.stdin.drain()
        except (ConnectionResetError, BrokenPipeError, OSError):
            raise McpTestError(
                ERROR_TYPE_START,
                "无法向进程发送协议消息，程序可能已提前退出" + self._stderr_suffix(),
                HINT_START,
            ) from None

    async def _read_result(self, timeout: float) -> Any:
        """读取属于最近一个请求 id 的响应；通知与其他报文跳过。"""
        request_id = self._request_id
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise McpTestError(
                    ERROR_TYPE_TIMEOUT,
                    f"连接超时：{timeout:g} 秒内未收到响应",
                    HINT_TIMEOUT,
                )
            try:
                line = await asyncio.wait_for(self._readline(), remaining)
            except asyncio.TimeoutError:
                raise McpTestError(
                    ERROR_TYPE_TIMEOUT,
                    f"连接超时：{timeout:g} 秒内未收到响应",
                    HINT_TIMEOUT,
                ) from None
            if not line:
                # 进程已关闭 stdout；稍等一拍让 stderr 读取任务把最后几行收进来
                await asyncio.sleep(0.1)
                self._raise_connection_closed()
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            try:
                message = json.loads(text)
            except json.JSONDecodeError:
                raise McpTestError(
                    ERROR_TYPE_PROTOCOL,
                    "返回内容不是有效的 JSON-RPC 报文：" + _snippet(redact(text, self._secrets)),
                    HINT_PROTOCOL,
                ) from None
            if not isinstance(message, dict) or message.get("id") != request_id:
                continue
            error = message.get("error")
            if error is not None:
                detail = error.get("message") if isinstance(error, dict) else str(error)
                raise McpTestError(
                    ERROR_TYPE_SERVER,
                    "Server 返回错误：" + _snippet(redact(str(detail), self._secrets)),
                    HINT_SERVER,
                )
            if "result" not in message:
                raise McpTestError(
                    ERROR_TYPE_PROTOCOL,
                    "响应缺少 result 字段：" + _snippet(redact(text, self._secrets), 200),
                    HINT_PROTOCOL,
                )
            return message["result"]

    async def _readline(self) -> bytes:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return b""
        return await proc.stdout.readline()

    def _raise_connection_closed(self) -> None:
        proc = self._proc
        if proc is not None and proc.returncode is not None:
            raise McpTestError(
                ERROR_TYPE_START,
                f"程序在完成请求前退出（退出码 {proc.returncode}）" + self._stderr_suffix(),
                HINT_START,
            )
        raise McpTestError(
            ERROR_TYPE_PROTOCOL,
            "连接在完成请求前被对方关闭" + self._stderr_suffix(),
            HINT_PROTOCOL,
        )

    def _stderr_suffix(self) -> str:
        if not self._stderr_tail:
            return ""
        joined = _snippet(redact(" ".join(self._stderr_tail), self._secrets))
        return f"，程序输出：{joined}"


class McpSession:
    """与一个 MCP Server 的 stdio 会话，供一次 Agent 运行使用。

    对外是普通异步接口（start / call_tool / close），可以放心在任意事件循环里
    调用；每个操作都会被调度到 MCP 专用循环上执行，因此即使服务进程跑的是
    Windows 的 SelectorEventLoop（uvicorn --reload / --workers），也能正常启动
    子进程。start 可多次 call_tool，最后由调用方 close 收尾。
    """

    def __init__(
        self,
        command: str,
        args: list[str],
        env: dict[str, str],
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._core = _McpSessionCore(command, args, env, timeout)

    async def start(self) -> list[dict[str, Any]]:
        """启动子进程并完成握手，返回原始工具定义列表（含 inputSchema）。"""
        return await _run_on_mcp_loop(self._core.start())

    async def call_tool(
        self, name: str, arguments: dict[str, Any], timeout: float | None = None
    ) -> dict[str, Any]:
        """调用一个工具，返回 {"isError": bool, "text": str}。"""
        return await _run_on_mcp_loop(self._core.call_tool(name, arguments, timeout))

    async def close(self) -> None:
        """关闭连接并清理子进程；可重复调用。"""
        await _run_on_mcp_loop(self._core.close())


def _extract_tool_result(result: Any, secrets: list[str]) -> dict[str, Any]:
    """把 tools/call 的 result 转成 {"isError", "text"}；结构异常按协议错误处理。"""
    if not isinstance(result, dict):
        raise McpTestError(
            ERROR_TYPE_PROTOCOL,
            "tools/call 响应格式不兼容（result 不是对象）",
            HINT_PROTOCOL,
        )
    parts: list[str] = []
    content = result.get("content")
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str):
                parts.append(item["text"])
            elif isinstance(item, dict) and isinstance(item.get("type"), str):
                # 二进制内容不展示正文/Base64，只提示类型与大小
                size = len(str(item.get("data") or ""))
                size_note = f"，约 {size} 字符 Base64 数据" if size else ""
                parts.append(f"[非文本内容：{item['type']}{size_note}]")
    text = "\n".join(parts).strip()
    if not text and isinstance(result.get("structuredContent"), dict):
        text = json.dumps(result["structuredContent"], ensure_ascii=False)
    limit = 200_000
    if len(text) > limit:
        # 不静默截断：明确标记哪些内容没有进入结果
        text = text[:limit] + f"\n…[结果过长，已截断：原始长度约 {len(text)} 字符]"
    is_error = result.get("isError") is True
    return {"isError": is_error, "text": redact(text, secrets)}


async def _drain_stderr(stream: asyncio.StreamReader | None, tail: deque[str]) -> None:
    if stream is None:
        return
    try:
        while True:
            line = await stream.readline()
            if not line:
                return
            text = line.decode("utf-8", errors="replace").strip()
            if text:
                tail.append(text)
    except (OSError, ValueError):
        return


async def _terminate(proc: asyncio.subprocess.Process) -> None:
    """先关 stdin 让进程自然退出，超时再强杀，确保结束后不留后台进程。"""
    if proc.returncode is not None:
        return
    try:
        if proc.stdin is not None:
            proc.stdin.close()
    except OSError:
        pass
    try:
        await asyncio.wait_for(proc.wait(), STDIN_GRACE_SECONDS)
        return
    except asyncio.TimeoutError:
        pass
    try:
        proc.terminate()
    except (ProcessLookupError, OSError):
        return
    try:
        await asyncio.wait_for(proc.wait(), TERMINATE_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except (ProcessLookupError, OSError):
            pass
        await proc.wait()
