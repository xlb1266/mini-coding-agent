"""Token 估算与上下文容量计算。

本项目不对接具体模型的 tokenizer：这里提供一套稳定的估算方法
（CJK 每字 1 token、其余每 4 字符 1 token，宁可高估不低估），
在每次模型请求前估算上下文占用。所有数字都是"估算值"，与模型
接口返回的实际 Token 用量（usage）不是一回事：代码、页面与运行
事件中使用时必须标注"估算"，不得与实际用量混用。

可用输入容量 = 模型上下文长度 - 输出预留（模型配置的最大输出
Token）- 安全余量（吸收估算误差）。不能把整个上下文窗口都用于
输入。
"""

import json
from typing import Any

# 每条消息的固定开销估算（角色标记等 JSON 包装）
MESSAGE_OVERHEAD_TOKENS = 4

# 每个工具定义的固定开销估算
TOOL_OVERHEAD_TOKENS = 4

# 安全余量：上下文长度的 5%，下限 64 token
SAFETY_MARGIN_RATIO = 20
SAFETY_MARGIN_MIN_TOKENS = 64


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数：CJK 每字 1 token，其余每 4 字符 1 token。

    宁可高估不低估：提前触发压缩好过把超限请求发出去再等报错。
    """
    cjk = sum(
        1
        for ch in text
        if "\u2e80" <= ch <= "\u9fff" or "\u3000" <= ch <= "\u303f" or "\uff00" <= ch <= "\uffef"
    )
    other = len(text) - cjk
    return cjk + (other + 3) // 4


def estimate_messages_tokens(
    messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
) -> int:
    """估算一次模型请求的输入占用：消息正文、工具调用结构、工具定义。"""
    total = 0
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif content is not None:
            total += estimate_tokens(json.dumps(content, ensure_ascii=False))
        for call in message.get("tool_calls") or []:
            total += estimate_tokens(json.dumps(call, ensure_ascii=False))
        total += MESSAGE_OVERHEAD_TOKENS
    if tools:
        total += estimate_tokens(json.dumps(tools, ensure_ascii=False))
        total += len(tools) * TOOL_OVERHEAD_TOKENS
    return total


def safety_margin_tokens(context_length: int) -> int:
    """安全余量：上下文长度的 5%（至少 64 token），吸收估算误差。"""
    return max(SAFETY_MARGIN_MIN_TOKENS, context_length // SAFETY_MARGIN_RATIO)


def available_input_tokens(context_length: int, max_output_tokens: int) -> int:
    """可用输入容量 = 上下文长度 - 输出预留 - 安全余量。

    结果可能为 0 或负数（模型配置的输出预留过大），调用方必须检查后
    再使用，不能直接当成可用容量。
    """
    return context_length - max_output_tokens - safety_margin_tokens(context_length)


def trigger_tokens(available_input: int, threshold_percent: int) -> int:
    """压缩触发阈值：可用输入容量 × 触发比例（百分比整数）。"""
    return available_input * threshold_percent // 100
