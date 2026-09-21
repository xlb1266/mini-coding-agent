"""工具执行层的公共类型：参数规格、统一结果与受控异常。

统一执行入口（executor.execute_tool）把所有异常转换成 ToolExecutionResult，
调用方（路由 / 后续 Agent Loop）不需要也无法让后端进程因某个工具而崩溃。
"""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ToolContext:
    """一次工具执行的运行环境；由调用方构造（Runtime 按 Agent 工作区，
    手动测试端点用全局默认目录），handler 里 None 时回落 settings。

    workspace_root 决定文件类工具的沙箱根与 Shell 命令的工作目录。
    """

    workspace_root: Path


@dataclass(frozen=True)
class ToolParamSpec:
    """一个工具参数的规格，用于执行前校验与详情页的参数说明。"""

    name: str
    # "string" | "integer" | "boolean"
    type: str
    required: bool
    description: str
    example: str | None = None


# 错误类型（error.type），调用方可据此做差异化处理
ERROR_INVALID_PARAMS = "invalid_params"  # 参数缺失/类型错误/取值不合法
ERROR_BLOCKED = "blocked"                # 命中危险操作拦截（Shell 工具）
ERROR_TIMEOUT = "timeout"                # 执行超时
ERROR_DISABLED = "disabled"              # 工具已停用
ERROR_FAILED = "failed"                  # 执行失败（文件不存在、进程出错等）


@dataclass
class ToolExecutionResult:
    """统一的工具执行结果：ok 为 False 时 error_* 字段说明原因。"""

    ok: bool
    data: dict | None = None
    error_type: str | None = None
    message: str | None = None
    hint: str | None = None
    # 参数级错误原因（仅 invalid_params 时），形如 {"timezone": "…"}
    fields: dict[str, str] = field(default_factory=dict)

    @classmethod
    def failure(
        cls,
        error_type: str,
        message: str,
        hint: str | None = None,
        fields: dict[str, str] | None = None,
    ) -> "ToolExecutionResult":
        return cls(ok=False, error_type=error_type, message=message, hint=hint, fields=fields or {})


class ToolParamError(Exception):
    """参数不合法：message 为整体提示，fields 为逐参数原因。"""

    def __init__(self, message: str, fields: dict[str, str] | None = None, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.fields = fields or {}
        self.hint = hint


class ToolBlockedError(Exception):
    """命中危险操作拦截，reason 说明拦截原因，写进返回结果。"""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ToolTimeoutError(Exception):
    """工具执行超时被终止。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ToolExecutionError(Exception):
    """执行失败但原因明确（文件不存在、写入失败等），信息可直接展示。"""

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint
