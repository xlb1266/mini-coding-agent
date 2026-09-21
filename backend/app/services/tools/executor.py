"""统一的本地工具执行入口。

后续 Agent Loop 调用任何本地工具都必须经过 execute_tool：
执行前检查工具是否启用、参数是否合法，执行时把一切异常
（包括未预期异常）转换为统一的 ToolExecutionResult，
保证失败信息清楚可读，且后端进程不会因工具执行而崩溃。
"""

from typing import Mapping

from app.db.models.tool import Tool
from app.services.tools.base import (
    ERROR_BLOCKED,
    ERROR_DISABLED,
    ERROR_FAILED,
    ERROR_INVALID_PARAMS,
    ERROR_TIMEOUT,
    ToolBlockedError,
    ToolContext,
    ToolExecutionError,
    ToolExecutionResult,
    ToolParamError,
    ToolParamSpec,
    ToolTimeoutError,
)
from app.services.tools.registry import get_definition

# 参数类型 → 中文说明，用于类型错误的提示文案
_TYPE_LABELS = {"string": "字符串", "integer": "整数", "boolean": "布尔值（true/false）"}


def execute_tool(
    tool: Tool,
    params: Mapping[str, object],
    context: ToolContext | None = None,
) -> ToolExecutionResult:
    """执行一个工具并返回统一结果。参数 params 的取值来自 JSON 请求体。

    context 携带运行级工作区（Agent 工作区或全局默认目录）；None 时
    handler 内部回落 settings 的默认目录。
    """
    if not tool.enabled:
        return ToolExecutionResult.failure(
            ERROR_DISABLED,
            f"工具「{tool.name}」已停用，请先在工具管理中启用",
        )

    definition = get_definition(tool.key)
    if definition is None:
        return ToolExecutionResult.failure(
            ERROR_FAILED,
            f"工具「{tool.key}」没有可用的执行器",
            hint="内置工具的执行器缺失通常是安装不完整，请重新部署后端",
        )

    try:
        validated = _validate_params(params, definition.params)
    except ToolParamError as error:
        return ToolExecutionResult.failure(ERROR_INVALID_PARAMS, error.message, error.hint, error.fields)

    try:
        data = definition.handler(validated, context)
    except ToolParamError as error:
        return ToolExecutionResult.failure(ERROR_INVALID_PARAMS, error.message, error.hint, error.fields)
    except ToolBlockedError as error:
        return ToolExecutionResult.failure(
            ERROR_BLOCKED,
            f"操作已被安全拦截：{error.reason}",
            hint="如确需执行该操作，请手动在终端完成；工具不会放开此类限制",
        )
    except ToolTimeoutError as error:
        return ToolExecutionResult.failure(ERROR_TIMEOUT, error.message)
    except ToolExecutionError as error:
        return ToolExecutionResult.failure(ERROR_FAILED, error.message, error.hint)
    except Exception as error:  # 兜底：任何未预期异常都不允许冲垮后端
        return ToolExecutionResult.failure(
            ERROR_FAILED,
            f"工具执行出现意外错误：{error}",
            hint="请检查参数与运行环境；若反复出现，请查看后端日志",
        )

    return ToolExecutionResult(ok=True, data=data)


def _validate_params(params: Mapping[str, object], specs: list[ToolParamSpec]) -> dict:
    """按参数规格做通用校验（缺失/类型/未知参数），返回干净的参数字典。"""
    errors: dict[str, str] = {}
    known = {spec.name for spec in specs}

    for key in params:
        if key not in known:
            errors[key] = "不支持的工具参数"

    validated: dict = {}
    for spec in specs:
        value = params.get(spec.name)
        if value is None:
            if spec.required:
                errors[spec.name] = f"缺少必填参数 {spec.name}"
            continue
        if not _check_type(value, spec.type):
            errors[spec.name] = f"参数 {spec.name} 应为{_TYPE_LABELS.get(spec.type, spec.type)}"
        else:
            validated[spec.name] = value

    if errors:
        raise ToolParamError("工具参数校验未通过", fields=errors)
    return validated


def _check_type(value: object, type_name: str) -> bool:
    if type_name == "string":
        return isinstance(value, str)
    if type_name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "boolean":
        return isinstance(value, bool)
    return False
