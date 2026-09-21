"""内置工具的具体实现：当前时间、Shell 命令执行、文件读写与编码工具。

工具说明只是给模型和用户的参考，危险操作的限制在这里（执行层）落实：
- Shell：正则黑名单 + 分段词法分析双重拦截危险命令，并强制超时与输出上限；
- 文件：所有路径先解析再校验必须落在授权工作区内，限制单次读写大小。

工作区跟随（模块一）：所有 handler 接收 ToolContext，文件类工具的沙箱根
与 Shell 的工作目录取 context.workspace_root；context 为 None 时回落
settings.tool_workspace_dir（手动测试端点与旧调用方）。

拦截规则是尽力而为的黑名单，不等于安全沙箱；如需强隔离，
应把后端整体放在容器或受限账号中运行。
"""

import difflib
import fnmatch
import locale
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.config import settings
from app.services.tools.base import (
    ToolBlockedError,
    ToolContext,
    ToolExecutionError,
    ToolParamError,
    ToolTimeoutError,
)

_WEEKDAYS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")

_TIMEZONE_HINT = "请使用 IANA 时区名称，例如 Asia/Shanghai、America/New_York、Etc/UTC；不要使用 CST 等含义不明确的缩写"


# ---------------------------------------------------------------------------
# 当前时间
# ---------------------------------------------------------------------------


def run_current_time(params: dict, context: ToolContext | None = None) -> dict:
    """返回指定时区（缺省用系统配置默认时区）的当前日期和时间。"""
    raw = str(params.get("timezone") or "").strip() or settings.default_timezone
    try:
        tz = ZoneInfo(raw)
    except (ValueError, ZoneInfoNotFoundError):
        raise ToolParamError(
            f"无法识别的时区「{raw}」",
            fields={"timezone": _TIMEZONE_HINT},
        ) from None

    now = datetime.now(tz)
    return {
        "timezone": tz.key,
        "datetime": now.isoformat(timespec="seconds"),
        "utcOffset": _format_offset(now.utcoffset()),
        "weekday": _WEEKDAYS[now.weekday()],
        "unixTimestamp": int(now.timestamp()),
    }


def _format_offset(offset) -> str:
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    hours, minutes = divmod(abs(total_minutes), 60)
    return f"{sign}{hours:02d}:{minutes:02d}"


# ---------------------------------------------------------------------------
# Shell 命令执行
# ---------------------------------------------------------------------------

# 受保护的系统目录（规范化后的首段路径名）：递归删除这些目录本身（或其下
# 全部内容）、修改其内部权限都会被拦截；删除其中具体子路径不在拦截范围。
_PROTECTED_DIR_NAMES = frozenset({
    "windows", "system32", "program files", "program files (x86)", "users",
    "etc", "usr", "var", "bin", "sbin", "lib", "lib64", "boot", "root",
    "home", "dev", "proc", "sys", "opt",
})

# 对整条命令做正则匹配的黑名单：命中即拦截，reason 写进返回结果
_DENIED_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # 格式化磁盘 / 修改分区 / 直接写磁盘设备
    (re.compile(r"\bformat(\.com)?\s+[a-z]:"), "格式化磁盘"),
    (re.compile(r"\bmkfs"), "格式化磁盘"),
    (re.compile(r"\b(fdisk|sfdisk|cfdisk|parted|diskpart)\b"), "修改磁盘分区"),
    (re.compile(r"\bdd\b[^|;&]*of=/dev/(sd|nvme|hd|vd|disk)"), "直接写入磁盘设备"),
    (re.compile(r">\s*/dev/(sd|nvme|hd)"), "直接写入磁盘设备"),
    # 关闭安全防护
    (re.compile(r"set-mppreference[^|;&]*disable"), "关闭 Windows Defender 防护"),
    (re.compile(r"add-mppreference[^|;&]*exclusion"), "添加 Defender 排除项"),
    (
        re.compile(r"\bnet\s+stop\b[^|;&]*\b(windefend|defender|firewalld|firewall|mssense|wdsvc|securityhealthservice)\b"),
        "停止安全防护服务",
    ),
    (
        re.compile(r"\bsc(\.exe)?\s+(stop|config|delete)\b[^|;&]*\b(windefend|defender|firewalld|firewall|mssense|wdsvc|securityhealthservice)\b"),
        "停止安全防护服务",
    ),
    (re.compile(r"taskkill[^|;&]*\b(msmpeng|msense|windefend)\b"), "结束安全防护进程"),
    (re.compile(r"stop-process[^|;&]*\b(msmpeng|windefend)\b"), "结束安全防护进程"),
    (re.compile(r"\bsystemctl\s+(stop|disable|mask)\b[^|;&]*\b(firewalld|firewall|ufw|iptables|apparmor|selinux)\b"), "关闭防火墙或安全模块"),
    (re.compile(r"\bufw\s+disable\b"), "关闭防火墙"),
    (re.compile(r"\biptables\s+(-f|-x|-z)\b"), "清空防火墙规则"),
    # 读取 / 外传密钥
    (re.compile(r"\.ssh\b|\bid_rsa\b|\bid_ed25519\b|\bid_ecdsa\b"), "读取 SSH 密钥"),
    (re.compile(r"\.aws\b|\.netrc\b|\.gnupg\b|\.npmrc\b|\.kube\b"), "读取凭证文件"),
    (re.compile(r"\bsecret\.key\b"), "读取本地主密钥"),
    (re.compile(r"(?<![\w.])\.env(?![\w.])"), "读取 .env 密钥文件"),
    (re.compile(r"\bprintenv\b"), "导出环境变量（可能包含密钥）"),
    # 未经检查直接执行远程下载的脚本
    (re.compile(r"\|\s*(sudo\s+)?(ba|z|da|fi|k)?sh\b"), "通过管道执行脚本"),
    (re.compile(r"\|\s*python[23.]?\b"), "通过管道执行脚本"),
    (re.compile(r"\|\s*(powershell(\.exe)?|pwsh)\b"), "通过管道执行脚本"),
    (re.compile(r"\binvoke-expression\b|\biex\b"), "动态执行字符串命令"),
    (re.compile(r"\bdownloadstring\b"), "下载并执行远程脚本"),
    (re.compile(r"<\s*\(\s*(curl|wget)\b"), "进程替换执行远程脚本"),
    # 其他明确破坏性操作
    (re.compile(r"--no-preserve-root"), "递归删除根目录"),
    (re.compile(r"\b(shutdown|poweroff|halt|reboot)\b(\.exe)?\b"), "关闭或重启系统"),
    (re.compile(r":\(?\)\s*\{\s*:\|:&\s*\};:"), "Fork 炸弹"),
]

# 序列/管道分隔：把一条命令拆成多段分别分析
_SEQUENCE_SPLIT = re.compile(r"&&|\|\||;|\||&")

_SUDO_PREFIX = frozenset({"sudo", "doas"})
_SHELL_WRAPPERS = frozenset({"sh", "bash", "zsh", "dash", "cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh"})
_INNER_EXEC_FLAGS = frozenset({"-c", "/c", "-command", "--command"})
_DELETE_CMDS = frozenset({"rm", "rmdir", "rd", "del", "erase"})
_PERM_CMDS = frozenset({"chmod", "chown", "chgrp"})
_PERMISSION_TOOLS = frozenset({"icacls", "takeown", "cacls"})


def assert_shell_command_allowed(command: str, depth: int = 0) -> None:
    """拦截危险命令；通过则返回，命中则抛出 ToolBlockedError。"""
    if depth > 3:  # 嵌套包装命令最多向内看 3 层，避免无限递归
        return
    normalized = " ".join(command.split()).lower()

    for pattern, reason in _DENIED_PATTERNS:
        if pattern.search(normalized):
            raise ToolBlockedError(reason)

    for segment in _SEQUENCE_SPLIT.split(normalized):
        _assert_segment_allowed(segment, depth)


def _assert_segment_allowed(segment: str, depth: int) -> None:
    tokens = [t for t in segment.split() if t]
    while tokens and tokens[0].strip("\"'") in _SUDO_PREFIX:
        tokens = tokens[1:]
    if not tokens:
        return

    head = tokens[0].strip("\"'")
    head = head.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    args = tokens[1:]

    if head in _PERMISSION_TOOLS:
        raise ToolBlockedError("修改系统权限的命令（icacls/takeown/cacls）已被禁止执行")
    if head == "env" and not args:
        raise ToolBlockedError("导出环境变量可能泄露密钥，已被禁止执行")
    if head in _DELETE_CMDS:
        _assert_delete_allowed(head, args)
    elif head in _PERM_CMDS:
        _assert_permission_allowed(args)

    # 包装命令（cmd /c …、bash -c "…"）向内分析真正的命令
    if head in _SHELL_WRAPPERS and depth <= 3:
        inner_flags = [i for i, token in enumerate(args) if token.strip("\"'") in _INNER_EXEC_FLAGS]
        if inner_flags:
            inner = " ".join(args[inner_flags[0] + 1:]).strip("\"'")
            if inner:
                assert_shell_command_allowed(inner, depth + 1)


def _assert_delete_allowed(head: str, args: list[str]) -> None:
    """递归删除必须目标安全：禁止删除根目录、系统目录本身或其下全部内容。"""
    windows_style = head in {"rd", "rmdir", "del", "erase"}
    if windows_style:
        recursive = any(re.fullmatch(r"/s[a-z]*", flag.lower()) for flag in args)
    else:
        recursive = any(_is_recursive_flag(flag) for flag in args)
    if not recursive:
        return  # 非递归删除影响面有限，不做拦截

    for target in args:
        if not _looks_like_flag(target, windows_style):
            if _is_protected_target(target, include_children=False):
                raise ToolBlockedError(f"递归删除根目录或系统目录已被禁止：{target}")


def _assert_permission_allowed(args: list[str]) -> None:
    """禁止修改受保护系统目录（含其内部文件）的权限。"""
    for target in args:
        if not _looks_like_flag(target, windows_style=False):
            if _is_protected_target(target, include_children=True):
                raise ToolBlockedError(f"修改系统目录的权限已被禁止：{target}")


def _is_recursive_flag(flag: str) -> bool:
    if flag == "--recursive":
        return True
    # 短旗标中含 r/R 即视为递归：-r、-rf、-fr、-R …
    return flag.startswith("-") and not flag.startswith("--") and "r" in flag.lower()


def _looks_like_flag(token: str, windows_style: bool) -> bool:
    if windows_style:
        # cmd 的开关形如 /s /q；目标路径不会只有斜杠加字母
        return re.fullmatch(r"/[a-z?]+", token) is not None
    return token.startswith("-")


def _is_protected_target(target: str, include_children: bool) -> bool:
    normalized = _normalize_target(target)
    if normalized in {"", "*", ".", "..", "~", "~/*"}:
        return True
    first = normalized.split("/", 1)[0]
    if first in _PROTECTED_DIR_NAMES:
        # rm：仅拦截删除目录本身或其下全部内容（usr、usr/*）；chmod/chown：目录内任何位置都拦截
        return include_children or normalized == first or normalized == f"{first}/*"
    return False


def _normalize_target(target: str) -> str:
    text = target.strip().strip("\"'").replace("\\", "/").lower()
    text = re.sub(r"^[a-z]:", "", text)  # 去掉 Windows 盘符，c:\windows → /windows
    return text.lstrip("/").rstrip("/")


def run_shell(params: dict, context: ToolContext | None = None) -> dict:
    """执行 shell 命令，返回退出码与输出；危险命令、超时在这里被强制落实。

    命令的工作目录（cwd）落在 context 指定的工作区根目录
    （context 为 None 时用全局默认目录）。
    """
    command = str(params.get("command") or "").strip()
    if not command:
        raise ToolParamError("请填写要执行的命令", fields={"command": "命令不能为空"})

    timeout = params.get("timeout", settings.tool_shell_timeout_seconds)
    max_timeout = settings.tool_shell_timeout_max_seconds
    if not 1 <= timeout <= max_timeout:
        raise ToolParamError(
            "超时时间超出允许范围",
            fields={"timeout": f"超时时间须为 1–{max_timeout} 秒的整数"},
        )

    assert_shell_command_allowed(command)

    workspace_root = _workspace_root(context)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command, shell=True, capture_output=True, timeout=timeout, cwd=workspace_root
        )
    except subprocess.TimeoutExpired as expired:
        partial = _decode(expired.stdout or b"").strip()
        detail = f"，已产生的输出：{partial[:200]}" if partial else ""
        raise ToolTimeoutError(f"命令执行超过 {timeout} 秒，已被终止{detail}") from None

    duration_ms = int((time.monotonic() - started) * 1000)
    stdout, stdout_clipped = _clip(_decode(completed.stdout or b""))
    stderr, stderr_clipped = _clip(_decode(completed.stderr or b""))
    return {
        "command": command,
        "exitCode": completed.returncode,
        "success": completed.returncode == 0,
        "stdout": stdout,
        "stderr": stderr,
        "durationMs": duration_ms,
        "truncated": stdout_clipped or stderr_clipped,
    }


# ---------------------------------------------------------------------------
# 文件读写
# ---------------------------------------------------------------------------

_WRITE_MODES = ("overwrite", "create", "append")


def _workspace_root(context: ToolContext | None = None) -> Path:
    """本次执行的工作区根目录：context 优先，None 回落全局默认；不存在则创建。"""
    root = context.workspace_root if context is not None else settings.tool_workspace_dir
    root.mkdir(parents=True, exist_ok=True)
    return root


def _resolve_in_workspace(raw_path: str, context: ToolContext | None = None) -> Path:
    """把输入路径解析为绝对路径并确认落在授权工作区内，否则视为非法参数。

    沙箱根取 context.workspace_root（context 为 None 时用全局默认目录）。
    """
    text = raw_path.strip()
    if not text:
        raise ToolParamError("请填写文件路径", fields={"path": "文件路径不能为空"})
    if "\x00" in text:
        raise ToolParamError("文件路径包含非法字符", fields={"path": "文件路径包含非法字符"})

    root = _workspace_root(context).resolve()
    candidate = Path(text)
    target = candidate if candidate.is_absolute() else root / candidate
    resolved = target.resolve()
    if resolved != root and root not in resolved.parents:
        raise ToolParamError(
            "文件路径超出授权目录",
            fields={"path": f"只允许读写授权工作区内的文件（{root}）"},
            hint="path 可填写相对路径（相对工作区根目录）或工作区内的绝对路径",
        )
    return resolved


def run_file(params: dict, context: ToolContext | None = None) -> dict:
    action = str(params.get("action") or "").strip().lower()
    if action not in {"read", "write"}:
        raise ToolParamError("action 必须是 read 或 write", fields={"action": "请填写 read（读取）或 write（写入）"})

    raw_path = str(params.get("path") or "")
    target = _resolve_in_workspace(raw_path, context)

    if action == "read":
        return _read_file(target, raw_path.strip())

    if params.get("content") is None:
        raise ToolParamError("写入时必须提供内容", fields={"content": "action=write 时必须提供 content 参数"})
    content = str(params["content"])
    mode = str(params.get("mode") or "overwrite").strip().lower()
    if mode not in _WRITE_MODES:
        raise ToolParamError(
            "写入模式不合法",
            fields={"mode": "mode 必须是 overwrite（覆盖）、create（仅新建）或 append（追加）"},
        )
    return _write_file(target, raw_path.strip(), content, mode)


def _read_file(target: Path, raw_path: str) -> dict:
    if not target.exists():
        raise ToolExecutionError(
            f"文件不存在：{target}",
            hint="读取前文件必须已存在；请检查路径拼写，路径相对授权工作区根目录解析",
        )
    if target.is_dir():
        raise ToolExecutionError(
            f"目标是目录而不是文件：{target}",
            hint="文件读写工具只能操作文件；查看目录内容请使用 Shell 工具执行 dir 或 ls",
        )
    size = target.stat().st_size
    limit = settings.tool_file_size_limit
    if size > limit:
        raise ToolExecutionError(
            f"文件过大（{size} 字节），超过单次读取上限 {limit} 字节",
            hint="请改用 Shell 工具配合 head、tail 等命令分段查看大文件",
        )
    return {
        "action": "read",
        "path": raw_path,
        "absolutePath": str(target),
        "sizeBytes": size,
        "content": _decode(target.read_bytes()),
    }


def _write_file(target: Path, raw_path: str, content: str, mode: str) -> dict:
    data = content.encode("utf-8")
    limit = settings.tool_file_size_limit
    if len(data) > limit:
        raise ToolExecutionError(f"写入内容过大（{len(data)} 字节），超过单次写入上限 {limit} 字节")
    if target.is_dir():
        raise ToolExecutionError(f"路径已经是目录，无法写入文件：{target}")
    if mode == "create" and target.exists():
        raise ToolExecutionError(
            f"文件已存在：{target}",
            hint="create 模式只在文件不存在时创建；要覆盖已有文件请使用 mode=overwrite，要追加请使用 mode=append",
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    existed = target.exists()
    if mode == "append":
        with open(target, "ab") as handle:
            handle.write(data)
        outcome = "appended"
    else:
        target.write_bytes(data)
        outcome = "overwritten" if existed else "created"

    return {
        "action": "write",
        "mode": mode,
        "path": raw_path,
        "absolutePath": str(target),
        "sizeBytes": len(data),
        "outcome": outcome,
    }


# ---------------------------------------------------------------------------
# 文件编辑 / 目录列表 / 内容搜索（模块一）
# ---------------------------------------------------------------------------

# edit_file 返回的 unified diff 最大字符数（超长截断并标注）
_DIFF_MAX_LENGTH = 4000
# list_dir 的遍历限制：最大深度与最大条目数
_LIST_DIR_MAX_DEPTH = 3
_LIST_DIR_MAX_ENTRIES = 500
# grep 的默认与最大返回条数
_GREP_DEFAULT_MAX_RESULTS = 50
_GREP_MAX_RESULTS_LIMIT = 200


def run_edit_file(params: dict, context: ToolContext | None = None) -> dict:
    """精确替换文件中的一段内容并返回统一 diff 与替换次数。"""
    raw_path = str(params.get("path") or "")
    old_string = params.get("old_string")
    if not isinstance(old_string, str) or not old_string:
        raise ToolParamError(
            "请填写要替换的原内容",
            fields={"old_string": "old_string 不能为空（为空无法定位替换位置）"},
        )
    new_string = params.get("new_string")
    if not isinstance(new_string, str):
        raise ToolParamError("请填写替换后的内容", fields={"new_string": "new_string 必须是字符串（删除可传空串）"})
    replace_all = bool(params.get("replace_all") or False)

    target = _resolve_in_workspace(raw_path, context)
    if not target.exists():
        raise ToolExecutionError(
            f"文件不存在：{target}",
            hint="编辑前文件必须已存在；请检查路径拼写，路径相对授权工作区根目录解析",
        )
    if target.is_dir():
        raise ToolExecutionError(
            f"目标是目录而不是文件：{target}",
            hint="文件编辑工具只能操作文件；查看目录内容请使用 list_dir 工具",
        )
    size = target.stat().st_size
    limit = settings.tool_file_size_limit
    if size > limit:
        raise ToolExecutionError(
            f"文件过大（{size} 字节），超过单次编辑上限 {limit} 字节",
            hint="请先精简文件，或改用 Shell 工具分段处理大文件",
        )

    original = _decode(target.read_bytes())
    count = original.count(old_string)
    if count == 0:
        raise ToolExecutionError(
            "未找到要替换的内容：old_string 在文件中没有出现",
            hint="请复制文件中的原文（注意空格与缩进），或提供更长更唯一的上下文",
        )
    if count > 1 and not replace_all:
        raise ToolExecutionError(
            f"要替换的内容出现了多处（共 {count} 处），无法确定替换哪一处",
            hint="请提供更长更唯一的上下文，或设置 replace_all=true 一次替换全部",
        )
    replacements = count if replace_all else 1
    updated = original.replace(old_string, new_string) if replace_all else original.replace(old_string, new_string, 1)

    data = updated.encode("utf-8")
    if len(data) > limit:
        raise ToolExecutionError(f"替换后内容过大（{len(data)} 字节），超过单次写入上限 {limit} 字节")
    target.write_bytes(data)

    # 统一 diff：标签用相对路径（不泄露绝对路径），超长截断
    display_path = raw_path.strip()
    diff_text = "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            updated.splitlines(keepends=True),
            fromfile=f"a/{display_path}",
            tofile=f"b/{display_path}",
        )
    )
    if len(diff_text) > _DIFF_MAX_LENGTH:
        diff_text = diff_text[:_DIFF_MAX_LENGTH] + f"…[diff 过长，已截断：原始长度 {len(diff_text)} 字符]"
        diff_truncated = True
    else:
        diff_truncated = False

    return {
        "path": display_path,
        "absolutePath": str(target),
        "replacements": replacements,
        "sizeBytes": len(data),
        "diff": diff_text,
        "diffTruncated": diff_truncated,
    }


def _list_dir_entry(item: Path, base: Path) -> dict:
    """目录条目：相对 base 的路径、类型与大小（目录大小记 0）。"""
    try:
        rel = item.relative_to(base).as_posix()
    except ValueError:
        rel = item.name
    return {
        "name": rel,
        "type": "directory" if item.is_dir() else "file",
        "sizeBytes": 0 if item.is_dir() else item.stat().st_size,
    }


def _list_dir_sorted(items) -> list[Path]:
    """目录在前，其余按名称排序，保证返回顺序稳定。"""
    return sorted(items, key=lambda p: (not p.is_dir(), p.name))


def run_list_dir(params: dict, context: ToolContext | None = None) -> dict:
    """列出工作区内一个目录的内容；recursive=true 时按深度与条目数封顶。"""
    raw_path = str(params.get("path") or "").strip()
    recursive = bool(params.get("recursive") or False)

    root = _workspace_root(context).resolve()
    base = root if not raw_path else _resolve_in_workspace(raw_path, context)
    if not base.exists():
        raise ToolExecutionError(
            f"目录不存在：{base}",
            hint="请检查路径拼写，路径相对授权工作区根目录解析",
        )
    if not base.is_dir():
        raise ToolExecutionError(
            f"目标是文件而不是目录：{base}",
            hint="list_dir 只能列出目录内容；读取文件请使用文件读写工具",
        )

    entries: list[dict] = []
    truncated = False

    def _collect(directory: Path, depth: int) -> None:
        nonlocal truncated
        if depth > _LIST_DIR_MAX_DEPTH:
            return
        for item in _list_dir_sorted(directory.iterdir()):
            if len(entries) >= _LIST_DIR_MAX_ENTRIES:
                truncated = True
                return
            entries.append(_list_dir_entry(item, base))
            if item.is_dir():
                _collect(item, depth + 1)
                if len(entries) >= _LIST_DIR_MAX_ENTRIES:
                    truncated = True
                    return

    if recursive:
        _collect(base, 1)
    else:
        for item in _list_dir_sorted(base.iterdir()):
            if len(entries) >= _LIST_DIR_MAX_ENTRIES:
                truncated = True
                break
            entries.append(_list_dir_entry(item, base))

    return {
        "path": raw_path or ".",
        "recursive": recursive,
        "entries": entries,
        "totalEntries": len(entries),
        "truncated": truncated,
    }


def run_grep(params: dict, context: ToolContext | None = None) -> dict:
    """在工作区内按正则搜索文件内容，跳过二进制与超限文件。"""
    raw_pattern = params.get("pattern")
    if not isinstance(raw_pattern, str) or not raw_pattern.strip():
        raise ToolParamError("请填写搜索内容", fields={"pattern": "pattern 不能为空"})
    case_insensitive = bool(params.get("case_insensitive") or False)
    try:
        regex = re.compile(raw_pattern, re.IGNORECASE if case_insensitive else 0)
    except re.error:
        raise ToolParamError(
            f"无法识别的正则表达式「{raw_pattern}」",
            fields={"pattern": "pattern 必须是合法的正则表达式，例如 def\\s+\\w+\\("},
        ) from None

    max_results = params.get("max_results", _GREP_DEFAULT_MAX_RESULTS)
    if not isinstance(max_results, int) or isinstance(max_results, bool) or not 1 <= max_results <= _GREP_MAX_RESULTS_LIMIT:
        raise ToolParamError(
            "返回条数超出允许范围",
            fields={"max_results": f"max_results 须为 1–{_GREP_MAX_RESULTS_LIMIT} 的整数，默认 {_GREP_DEFAULT_MAX_RESULTS}"},
        )

    raw_path = str(params.get("path") or "").strip()
    raw_glob = params.get("glob")
    if raw_glob is not None and not isinstance(raw_glob, str):
        raise ToolParamError("glob 必须是字符串", fields={"glob": "glob 必须是字符串，例如 *.py"})
    glob = (raw_glob or "").strip() or None

    root = _workspace_root(context).resolve()
    base = root if not raw_path else _resolve_in_workspace(raw_path, context)
    if not base.exists():
        raise ToolExecutionError(
            f"搜索目录不存在：{base}",
            hint="请检查路径拼写，路径相对授权工作区根目录解析",
        )
    if not base.is_dir():
        raise ToolExecutionError(
            f"目标是文件而不是目录：{base}",
            hint="grep 在目录内递归搜索；读取单个文件请使用文件读写工具",
        )

    matches: list[dict] = []
    total = 0
    files_searched = 0
    for item in sorted(base.rglob("*")):
        if not item.is_file():
            continue
        if glob and not fnmatch.fnmatch(item.name, glob):
            continue
        size = item.stat().st_size
        if size > settings.tool_file_size_limit:
            continue  # 超限文件跳过，不报错中断整个搜索
        raw = item.read_bytes()
        if b"\x00" in raw:
            continue  # 二进制文件跳过
        files_searched += 1
        text = _decode(raw)
        rel = item.relative_to(base).as_posix()
        for line_no, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                total += 1
                if len(matches) < max_results:
                    matches.append({"path": rel, "line": line_no, "text": line.strip()})

    return {
        "pattern": raw_pattern,
        "path": raw_path or ".",
        "glob": glob,
        "matches": matches,
        "totalMatches": total,
        "returnedMatches": len(matches),
        "truncated": total > len(matches),
        "filesSearched": files_searched,
    }


# ---------------------------------------------------------------------------
# 共用：输出解码与截断
# ---------------------------------------------------------------------------


def _decode(raw: bytes) -> str:
    """优先 UTF-8，其次系统本地编码（中文 Windows 为 GBK），兜底逐字节替换。"""
    for encoding in ("utf-8", locale.getpreferredencoding(False)):
        if not encoding:
            continue
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _clip(text: str) -> tuple[str, bool]:
    limit = settings.tool_shell_output_limit
    if len(text) <= limit:
        return text, False
    return text[:limit] + f"\n…[输出过长，已截断：原始长度 {len(text)} 字符]", True
