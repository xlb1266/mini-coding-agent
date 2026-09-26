"""OpenAI Chat Completions 兼容接口的最小调用客户端。

包含两类入口：
- chat_completion：非流式，仅用于"测试连接"发起一次最小真实请求；
- stream_chat_completion_events：流式，供 Agent Runtime 使用，逐段产出
  正文增量，并在流结束时给出完整工具调用、结束原因与 Token 用量。

两者按响应状态码与报文把失败原因归类为密钥错误 / 模型标识错误 /
服务地址不可用 / 超时 / 上下文超限 / 返回格式异常等，所有错误文案都
经过密钥脱敏，保证不回显 API Key。
"""

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

# 测试请求的超时与输出上限，够验证连通性即可
DEFAULT_TIMEOUT_SECONDS = 20.0
CONNECT_TIMEOUT_SECONDS = 10.0
MAX_TEST_TOKENS = 64

# 流式请求的超时：读超时作用于相邻两个数据块之间，生成慢不至于误杀
STREAM_TIMEOUT_SECONDS = 300.0

TEST_PROMPT = "连接测试，请用不超过 20 个字回复：连接成功。"

# 错误类型 → 展示名，前端可据此给出差异化提示
ERROR_TYPE_AUTH = "auth"
ERROR_TYPE_MODEL = "model"
ERROR_TYPE_ENDPOINT = "endpoint"
ERROR_TYPE_NETWORK = "network"
ERROR_TYPE_TIMEOUT = "timeout"
ERROR_TYPE_FORMAT = "format"
ERROR_TYPE_SERVER = "server"
ERROR_TYPE_HTTP = "http"
ERROR_TYPE_CONTEXT = "context"


class ModelCallError(Exception):
    """调用模型服务失败，携带归类的错误类型、用户文案与排查建议。"""

    def __init__(self, error_type: str, message: str, hint: str) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.message = message
        self.hint = hint


def redact(text: str, api_key: str | None) -> str:
    """把文本中意外出现的密钥替换为 ***，防止任何回显泄露密钥。"""
    if api_key and api_key in text:
        return text.replace(api_key, "***")
    return text


def _snippet(text: str, limit: int = 200) -> str:
    text = text.strip().replace("\n", " ")
    return text[:limit] + ("…" if len(text) > limit else "")


def _upstream_error_message(body: Any) -> str | None:
    """尽力从兼容接口的错误报文里取出 message 字段。"""
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"]
        if isinstance(error, str):
            return error
        if isinstance(body.get("message"), str):
            return body["message"]
    return None


def _mentions_model(message: str) -> bool:
    lowered = message.lower()
    return "model" in lowered


def _mentions_context_limit(message: str) -> bool:
    """从上游报文判断是否是"输入超过上下文容量"类错误。"""
    lowered = message.lower()
    return any(
        keyword in lowered
        for keyword in (
            "context length",
            "context window",
            "context_length",
            "maximum context",
            "token limit",
            "too many tokens",
            "input too long",
            "exceeds the maximum",
        )
    )


def _classify_http_error(status_code: int, body: Any, api_key: str | None) -> ModelCallError:
    """按状态码与报文内容归类 HTTP 错误，非流式与流式共用。"""
    if status_code in (401, 403):
        raise ModelCallError(
            ERROR_TYPE_AUTH,
            "密钥无效或没有访问权限",
            "请检查 API Key 是否正确、账号是否有权限或余额",
        )
    if status_code == 404:
        message = _upstream_error_message(body) or ""
        if body is not None and _mentions_model(message):
            raise ModelCallError(
                ERROR_TYPE_MODEL,
                "模型标识不存在或不被该服务支持",
                "请核对模型标识与服务商提供的是否完全一致",
            )
        raise ModelCallError(
            ERROR_TYPE_ENDPOINT,
            "服务地址或接口路径不存在（HTTP 404）",
            "服务地址一般以 /v1 结尾，且不需要包含 /chat/completions，请核对服务商文档",
        )
    if status_code == 400:
        message = _upstream_error_message(body)
        if message and _mentions_context_limit(message):
            raise ModelCallError(
                ERROR_TYPE_CONTEXT,
                "历史消息过长，超过了该模型的上下文容量",
                "请缩短输入内容，或新建会话重新开始对话",
            )
        if message and _mentions_model(message):
            raise ModelCallError(
                ERROR_TYPE_MODEL,
                "模型标识不存在或不被该服务支持",
                "请核对模型标识与服务商提供的是否完全一致",
            )
        raise ModelCallError(
            ERROR_TYPE_HTTP,
            "服务拒绝了请求（HTTP 400）" + (f"：{_snippet(redact(message, api_key))}" if message else ""),
            "请检查模型标识、参数取值是否符合服务商要求",
        )
    if status_code == 429:
        raise ModelCallError(
            ERROR_TYPE_HTTP,
            "请求过于频繁或额度受限（HTTP 429）",
            "请稍后重试，或检查服务商控制台的额度与限流设置",
        )
    if status_code >= 500:
        message = _upstream_error_message(body)
        raise ModelCallError(
            ERROR_TYPE_SERVER,
            "模型服务返回了服务端错误（HTTP "
            f"{status_code}）" + (f"：{_snippet(redact(message, api_key))}" if message else ""),
            "服务可能暂时不可用，请稍后重试",
        )
    raise ModelCallError(
        ERROR_TYPE_HTTP,
        f"服务返回了未预期的状态码（HTTP {status_code}）",
        "请确认地址为 OpenAI Chat Completions 兼容接口，或查看服务商文档",
    )


def _create_client(timeout: float) -> httpx.Client:
    """独立成函数便于测试时替换为 MockTransport。"""
    return httpx.Client(
        timeout=httpx.Timeout(timeout, connect=CONNECT_TIMEOUT_SECONDS),
    )


def chat_completion(
    base_url: str,
    model_identifier: str,
    api_key: str | None,
    max_tokens: int,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """向 {base_url}/chat/completions 发一次最小请求，成功返回模型回复文本。"""
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": model_identifier,
        "messages": [{"role": "user", "content": TEST_PROMPT}],
        "max_tokens": max(1, min(max_tokens, MAX_TEST_TOKENS)),
        "stream": False,
    }

    try:
        with _create_client(timeout) as client:
            response = client.post(url, headers=headers, json=payload)
    except httpx.TimeoutException:
        raise ModelCallError(
            ERROR_TYPE_TIMEOUT,
            "请求超时，服务在限定时间内没有响应",
            "请检查服务地址是否可达、本机网络或代理是否正常，稍后重试",
        ) from None
    except httpx.HTTPError:
        raise ModelCallError(
            ERROR_TYPE_NETWORK,
            "无法连接到服务地址",
            "请核对服务地址与端口是否正确、服务是否已启动，以及网络/代理设置",
        ) from None

    if response.status_code != 200:
        # 404 的提示里可能带原始报文，保持与原实现一致用 text 兜底
        body = _safe_json(response)
        if response.status_code == 404:
            message = _upstream_error_message(body) or response.text
            if body is not None and _mentions_model(message):
                raise ModelCallError(
                    ERROR_TYPE_MODEL,
                    "模型标识不存在或不被该服务支持",
                    "请核对模型标识与服务商提供的是否完全一致",
                )
        _classify_http_error(response.status_code, body, api_key)

    body = _safe_json(response)
    if body is None:
        raise ModelCallError(
            ERROR_TYPE_FORMAT,
            "服务返回了无法解析的内容",
            "请确认该地址是 OpenAI Chat Completions 兼容接口而不是网页或其他服务",
        )
    if isinstance(body, dict) and "error" in body:
        message = _upstream_error_message(body)
        raise ModelCallError(
            ERROR_TYPE_SERVER,
            "服务返回了错误" + (f"：{_snippet(redact(message, api_key))}" if message else ""),
            "请查看服务商控制台或文档中的错误说明",
        )
    try:
        content = body["choices"][0]["message"]["content"]  # type: ignore[index]
    except (KeyError, IndexError, TypeError):
        raise ModelCallError(
            ERROR_TYPE_FORMAT,
            "服务返回的报文缺少预期的字段",
            "请确认该服务兼容 OpenAI Chat Completions 格式（choices[0].message.content）",
        ) from None
    if not isinstance(content, str) or not content.strip():
        raise ModelCallError(
            ERROR_TYPE_FORMAT,
            "服务返回了空回复",
            "可能是 max_tokens 过小或服务兼容性问题，可尝试保存后重新测试",
        )
    return _snippet(redact(content, api_key), 120)


def _safe_json(response: httpx.Response) -> Any | None:
    try:
        return response.json()
    except (json.JSONDecodeError, ValueError):
        return None


def _create_async_client(timeout: float) -> httpx.AsyncClient:
    """独立成函数便于测试时替换为 MockTransport。"""
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, connect=CONNECT_TIMEOUT_SECONDS),
    )


# 通用非流式请求的默认超时与输出上限（评分等结构化调用使用）
GENERAL_TIMEOUT_SECONDS = 120.0
GENERAL_MAX_TOKENS_LIMIT = 8192


async def chat_completion_text(
    base_url: str,
    model_identifier: str,
    api_key: str | None,
    messages: list[dict[str, Any]],
    max_tokens: int = 1024,
    temperature: float = 0.0,
    timeout: float = GENERAL_TIMEOUT_SECONDS,
) -> tuple[str, dict[str, int] | None]:
    """通用非流式调用：返回 (完整回复文本, 上游用量或 None)。

    与流式入口共用同一套错误归类与密钥脱敏；不提供工具参数——评分等
    结构化调用不应让模型执行工具循环。用量缺失时为 None（未知，不填 0）。
    """
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload: dict[str, Any] = {
        "model": model_identifier,
        "messages": messages,
        "max_tokens": max(1, min(max_tokens, GENERAL_MAX_TOKENS_LIMIT)),
        "temperature": temperature,
        "stream": False,
    }

    try:
        async with _create_async_client(timeout) as client:
            response = await client.post(url, headers=headers, json=payload)
    except httpx.TimeoutException:
        raise ModelCallError(
            ERROR_TYPE_TIMEOUT,
            "请求超时，服务在限定时间内没有响应",
            "请检查服务地址是否可达、本机网络或代理是否正常，稍后重试",
        ) from None
    except httpx.HTTPError:
        raise ModelCallError(
            ERROR_TYPE_NETWORK,
            "无法连接到服务地址",
            "请核对服务地址与端口是否正确、服务是否已启动，以及网络/代理设置",
        ) from None

    if response.status_code != 200:
        _classify_http_error(response.status_code, _safe_json(response), api_key)

    body = _safe_json(response)
    if body is None:
        raise ModelCallError(
            ERROR_TYPE_FORMAT,
            "服务返回了无法解析的内容",
            "请确认该地址是 OpenAI Chat Completions 兼容接口而不是网页或其他服务",
        )
    if isinstance(body, dict) and "error" in body:
        message = _upstream_error_message(body)
        raise ModelCallError(
            ERROR_TYPE_SERVER,
            "服务返回了错误" + (f"：{_snippet(redact(message or '', api_key))}" if message else ""),
            "请查看服务商控制台或文档中的错误说明",
        )
    try:
        content = body["choices"][0]["message"]["content"]  # type: ignore[index]
    except (KeyError, IndexError, TypeError):
        raise ModelCallError(
            ERROR_TYPE_FORMAT,
            "服务返回的报文缺少预期的字段",
            "请确认该服务兼容 OpenAI Chat Completions 格式（choices[0].message.content）",
        ) from None
    if not isinstance(content, str) or not content.strip():
        raise ModelCallError(
            ERROR_TYPE_FORMAT,
            "服务返回了空回复",
            "可能是 max_tokens 过小或服务兼容性问题，可尝试重新发起",
        )
    usage_raw = body.get("usage") if isinstance(body, dict) else None
    usage = _normalize_usage(usage_raw)
    return redact(content, api_key), usage


async def stream_chat_completion_events(
    base_url: str,
    model_identifier: str,
    api_key: str | None,
    messages: list[dict[str, Any]],
    max_tokens: int,
    temperature: float,
    tools: list[dict[str, Any]] | None = None,
    timeout: float = STREAM_TIMEOUT_SECONDS,
) -> AsyncIterator[dict[str, Any]]:
    """向 {base_url}/chat/completions 发起流式请求，产出统一的事件序列。

    事件（dict）：
    - {"type": "text", "text": str}                     正文增量（按序产出）
    - {"type": "reasoning", "text": str}                思考增量（推理模型的
                                                         reasoning_content /
                                                         reasoning 增量，仅
                                                         展示用，不进入对话
                                                         上下文）
    - {"type": "finish", "finish_reason": str | None,   流结束：完整的工具调用列表、
      "tool_calls": [...], "usage": {...} | None}       结束原因与上游返回的用量

    工具调用增量（delta.tool_calls）按 index 累积，在 finish 事件中一次性给出；
    usage 取上游任一分块中实际返回的值，上游未返回时为 None（表示未知）。
    连接失败、超时、上游报错时抛出归类后的 ModelCallError；调用方取消
    （asyncio.CancelledError）原样向上传播。
    """
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload: dict[str, Any] = {
        "model": model_identifier,
        "messages": messages,
        "max_tokens": max(1, max_tokens),
        "temperature": temperature,
        "stream": True,
    }
    if tools:
        payload["tools"] = tools

    try:
        async with _create_async_client(timeout) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as response:
                if response.status_code != 200:
                    # 流式响应的错误体需要读完才能解析
                    await response.aread()
                    _classify_http_error(response.status_code, _safe_json(response), api_key)

                tool_calls = _ToolCallAccumulator()
                finish_reason: str | None = None
                usage: dict[str, Any] | None = None
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data:
                        continue
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if not isinstance(obj, dict):
                        continue
                    # 部分兼容服务会在 200 后把错误作为事件下发
                    if "error" in obj:
                        message = _upstream_error_message(obj)
                        raise ModelCallError(
                            ERROR_TYPE_SERVER,
                            "模型服务中断了输出" + (f"：{_snippet(redact(message or '', api_key))}" if message else ""),
                            "请稍后重试；若持续出现请查看服务商控制台",
                        )
                    chunk_usage = obj.get("usage")
                    if isinstance(chunk_usage, dict):
                        usage = chunk_usage
                    choice = _first_choice(obj)
                    if choice is None:
                        continue
                    if isinstance(choice.get("finish_reason"), str):
                        finish_reason = choice["finish_reason"]
                    delta = choice.get("delta")
                    if not isinstance(delta, dict):
                        continue
                    content = delta.get("content")
                    if isinstance(content, str) and content:
                        yield {"type": "text", "text": content}
                    reasoning = delta.get("reasoning_content")
                    if not isinstance(reasoning, str) or not reasoning:
                        # OpenAI 兼容服务对思考增量有两种常见字段名
                        reasoning = delta.get("reasoning")
                    if isinstance(reasoning, str) and reasoning:
                        yield {"type": "reasoning", "text": reasoning}
                    tool_calls.feed(delta.get("tool_calls"))

                yield {
                    "type": "finish",
                    "finish_reason": finish_reason,
                    "tool_calls": tool_calls.completed(),
                    "usage": _normalize_usage(usage),
                }
    except httpx.TimeoutException:
        raise ModelCallError(
            ERROR_TYPE_TIMEOUT,
            "请求超时，模型服务长时间没有返回新内容",
            "请检查网络或代理是否正常，然后重试",
        ) from None
    except httpx.HTTPError:
        raise ModelCallError(
            ERROR_TYPE_NETWORK,
            "与模型服务的连接中断",
            "请核对服务地址与端口是否正确、网络/代理是否正常，然后重试",
        ) from None


def _first_choice(obj: dict[str, Any]) -> dict[str, Any] | None:
    choices = obj.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        return choices[0]
    return None


def _normalize_usage(usage: dict[str, Any] | None) -> dict[str, int] | None:
    """把上游 usage 字段归一化；缺失或非数值的项记为 None（未知）。

    cachedTokens 为输入 Token 中缓存命中的部分（OpenAI 口径：已包含在
    prompt_tokens 内，展示与汇总时不再重复相加）。兼容两种上游写法：
    prompt_tokens_details.cached_tokens 与顶层 cached_tokens。
    """

    def _int(value: Any) -> int | None:
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    if not isinstance(usage, dict):
        return None
    cached: int | None = None
    details = usage.get("prompt_tokens_details")
    if isinstance(details, dict):
        cached = _int(details.get("cached_tokens"))
    if cached is None:
        cached = _int(usage.get("cached_tokens"))
    normalized = {
        "promptTokens": _int(usage.get("prompt_tokens")),
        "completionTokens": _int(usage.get("completion_tokens")),
        "totalTokens": _int(usage.get("total_tokens")),
        "cachedTokens": cached,
    }
    if all(value is None for value in normalized.values()):
        return None
    return normalized


class _ToolCallAccumulator:
    """把流式 tool_calls 增量按 index 合并为完整调用列表。"""

    def __init__(self) -> None:
        self._calls: dict[int, dict[str, str]] = {}

    def feed(self, pieces: Any) -> None:
        """合并一个分块中的 tool_calls 增量；格式异常的分块静默跳过。"""
        if not isinstance(pieces, list):
            return
        for piece in pieces:
            if not isinstance(piece, dict):
                continue
            index = piece.get("index")
            if not isinstance(index, int) or isinstance(index, bool):
                continue
            call = self._calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
            if isinstance(piece.get("id"), str):
                call["id"] = piece["id"]
            function = piece.get("function")
            if isinstance(function, dict):
                if isinstance(function.get("name"), str):
                    call["name"] += function["name"]
                if isinstance(function.get("arguments"), str):
                    call["arguments"] += function["arguments"]

    def completed(self) -> list[dict[str, str]]:
        return [self._calls[index] for index in sorted(self._calls)]
