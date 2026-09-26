"""model_client 上游调用与错误分类单元测试（MockTransport，不发真实网络请求）。"""

import json

import httpx
import pytest

from app.services import model_client
from app.services.model_client import (
    ERROR_TYPE_AUTH,
    ERROR_TYPE_CONTEXT,
    ERROR_TYPE_ENDPOINT,
    ERROR_TYPE_FORMAT,
    ERROR_TYPE_HTTP,
    ERROR_TYPE_MODEL,
    ERROR_TYPE_NETWORK,
    ERROR_TYPE_SERVER,
    ERROR_TYPE_TIMEOUT,
    ModelCallError,
    chat_completion,
    stream_chat_completion_events,
)

BASE_URL = "https://api.example.com/v1"
MODEL = "demo-model"
API_KEY = "sk-secret-abcdef"


def chat_response(content: str) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def run(handler) -> str:
    original = model_client._create_client
    model_client._create_client = lambda timeout: httpx.Client(
        transport=httpx.MockTransport(handler), timeout=timeout
    )
    try:
        return chat_completion(BASE_URL, MODEL, API_KEY, max_tokens=64)
    finally:
        model_client._create_client = original


def expect_error(handler, error_type: str) -> ModelCallError:
    with pytest.raises(ModelCallError) as exc_info:
        run(handler)
    assert exc_info.value.error_type == error_type
    return exc_info.value


def test_success_returns_reply_and_sends_expected_request() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=chat_response("连接成功"))

    reply = run(handler)

    assert reply == "连接成功"
    assert captured["url"] == f"{BASE_URL}/chat/completions"
    assert captured["auth"] == f"Bearer {API_KEY}"
    assert captured["body"]["model"] == MODEL
    assert captured["body"]["stream"] is False
    assert 0 < captured["body"]["max_tokens"] <= 64


def test_success_without_api_key_sends_no_auth_header() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json=chat_response("ok"))

    original = model_client._create_client
    model_client._create_client = lambda timeout: httpx.Client(
        transport=httpx.MockTransport(handler), timeout=timeout
    )
    try:
        assert chat_completion(BASE_URL, MODEL, None, max_tokens=64) == "ok"
    finally:
        model_client._create_client = original

    assert captured["auth"] is None


def test_unauthorized_is_auth_error() -> None:
    error = expect_error(
        lambda request: httpx.Response(401, json={"error": {"message": "invalid api key"}}),
        ERROR_TYPE_AUTH,
    )
    assert "密钥" in error.message
    assert API_KEY not in error.message + error.hint


def test_forbidden_is_auth_error() -> None:
    expect_error(lambda request: httpx.Response(403, json={}), ERROR_TYPE_AUTH)


def test_model_not_found_on_404() -> None:
    error = expect_error(
        lambda request: httpx.Response(
            404, json={"error": {"message": "The model 'demo-model' does not exist"}}
        ),
        ERROR_TYPE_MODEL,
    )
    assert "模型标识" in error.message


def test_model_not_found_on_400() -> None:
    expect_error(
        lambda request: httpx.Response(
            400, json={"error": {"message": "model not found: demo-model"}}
        ),
        ERROR_TYPE_MODEL,
    )


def test_404_without_model_mention_is_endpoint_error() -> None:
    error = expect_error(lambda request: httpx.Response(404, text="Not Found"), ERROR_TYPE_ENDPOINT)
    assert "服务地址" in error.message


def test_400_other_reason_is_http_error() -> None:
    error = expect_error(
        lambda request: httpx.Response(400, json={"error": {"message": "max_tokens too large"}}),
        ERROR_TYPE_HTTP,
    )
    assert "HTTP 400" in error.message
    assert "max_tokens too large" in error.message


def test_rate_limited_is_http_error() -> None:
    expect_error(lambda request: httpx.Response(429, json={}), ERROR_TYPE_HTTP)


def test_server_error_is_reported_with_status() -> None:
    error = expect_error(
        lambda request: httpx.Response(502, json={"error": {"message": "bad gateway"}}),
        ERROR_TYPE_SERVER,
    )
    assert "HTTP 502" in error.message


def test_upstream_error_message_is_redacted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": f"key {API_KEY} is broken"}})

    error = expect_error(handler, ERROR_TYPE_SERVER)
    assert API_KEY not in error.message
    assert "***" in error.message


def test_reply_content_is_redacted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=chat_response(f"你的密钥是 {API_KEY}"))

    original = model_client._create_client
    model_client._create_client = lambda timeout: httpx.Client(
        transport=httpx.MockTransport(handler), timeout=timeout
    )
    try:
        reply = chat_completion(BASE_URL, MODEL, API_KEY, max_tokens=64)
    finally:
        model_client._create_client = original

    assert API_KEY not in reply
    assert "***" in reply


def test_non_json_success_body_is_format_error() -> None:
    expect_error(lambda request: httpx.Response(200, text="<html>hi</html>"), ERROR_TYPE_FORMAT)


def test_missing_choices_is_format_error() -> None:
    expect_error(lambda request: httpx.Response(200, json={"data": []}), ERROR_TYPE_FORMAT)


def test_error_field_in_success_body_is_server_error() -> None:
    expect_error(
        lambda request: httpx.Response(200, json={"error": {"message": "upstream down"}}),
        ERROR_TYPE_SERVER,
    )


def test_empty_content_is_format_error() -> None:
    expect_error(lambda request: httpx.Response(200, json=chat_response("  ")), ERROR_TYPE_FORMAT)


def test_connect_timeout_is_timeout_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    expect_error(handler, ERROR_TYPE_TIMEOUT)


def test_read_timeout_is_timeout_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    expect_error(handler, ERROR_TYPE_TIMEOUT)


def test_connect_error_is_network_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    error = expect_error(handler, ERROR_TYPE_NETWORK)
    assert "无法连接" in error.message


# ---- 流式接口 stream_chat_completion_events ----


def sse_line(payload: object) -> bytes:
    return f"data: {json.dumps(payload)}\n\n".encode("utf-8")


def delta_line(text: str) -> bytes:
    return sse_line({"choices": [{"delta": {"content": text}}]})


def tool_call_line(index: int, *, id: str | None = None, name: str | None = None, arguments: str | None = None) -> bytes:
    function: dict = {}
    if name is not None:
        function["name"] = name
    if arguments is not None:
        function["arguments"] = arguments
    piece: dict = {"index": index, "type": "function"}
    if id is not None:
        piece["id"] = id
    piece["function"] = function
    return sse_line({"choices": [{"delta": {"tool_calls": [piece]}}]})


def usage_line(usage: dict) -> bytes:
    return sse_line({"choices": [], "usage": usage})


def finish_line(reason: str) -> bytes:
    return sse_line({"choices": [{"delta": {}, "finish_reason": reason}]})


DONE_LINE = b"data: [DONE]\n\n"


class StreamTransport(httpx.AsyncBaseTransport):
    """把 SSE 字节块按流下发的模拟传输。"""

    def __init__(self, chunks: list[bytes], status: int = 200, error_json: dict | None = None) -> None:
        self.chunks = chunks
        self.status = status
        self.error_json = error_json
        self.captured: dict = {}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.captured = {
            "url": str(request.url),
            "auth": request.headers.get("Authorization"),
            "body": json.loads(request.content),
        }
        if self.status != 200:
            return httpx.Response(self.status, json=self.error_json)

        async def body():
            for chunk in self.chunks:
                yield chunk

        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=body()
        )


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def run_stream(transport: StreamTransport, **kwargs: object) -> list[dict]:
    """收集流式事件；还原全局钩子，避免影响其他测试。"""
    original = model_client._create_async_client
    model_client._create_async_client = lambda timeout: httpx.AsyncClient(
        transport=transport, timeout=timeout
    )
    try:
        return [
            event
            async for event in stream_chat_completion_events(
                BASE_URL,
                MODEL,
                API_KEY,
                messages=kwargs.pop("messages", [{"role": "user", "content": "你好"}]),
                max_tokens=kwargs.pop("max_tokens", 64),
                temperature=kwargs.pop("temperature", 0.7),
                **kwargs,
            )
        ]
    finally:
        model_client._create_async_client = original


def text_of(events: list[dict]) -> str:
    return "".join(event["text"] for event in events if event["type"] == "text")


def finish_of(events: list[dict]) -> dict:
    return next(event for event in events if event["type"] == "finish")


async def expect_stream_error(transport: StreamTransport, error_type: str) -> ModelCallError:
    with pytest.raises(ModelCallError) as exc_info:
        await run_stream(transport)
    assert exc_info.value.error_type == error_type
    return exc_info.value


@pytest.mark.anyio
async def test_stream_yields_deltas_in_order_and_sends_expected_request() -> None:
    transport = StreamTransport([delta_line("你"), delta_line("好"), delta_line("！"), DONE_LINE])

    events = await run_stream(transport)

    assert text_of(events) == "你好！"
    assert [event["type"] for event in events] == ["text", "text", "text", "finish"]
    finish = finish_of(events)
    assert finish["tool_calls"] == []
    assert finish["usage"] is None
    assert transport.captured["url"] == f"{BASE_URL}/chat/completions"
    assert transport.captured["auth"] == f"Bearer {API_KEY}"
    assert transport.captured["body"]["stream"] is True
    assert transport.captured["body"]["messages"] == [{"role": "user", "content": "你好"}]
    assert transport.captured["body"]["max_tokens"] == 64
    assert transport.captured["body"]["temperature"] == 0.7
    assert "tools" not in transport.captured["body"]


@pytest.mark.anyio
async def test_stream_yields_reasoning_deltas_in_order() -> None:
    # 思考增量兼容两种上游字段名：reasoning_content（DeepSeek 风格）与 reasoning
    transport = StreamTransport(
        [
            sse_line({"choices": [{"delta": {"reasoning_content": "先想"}}]}),
            sse_line({"choices": [{"delta": {"reasoning": "一想"}}]}),
            delta_line("回答"),
            DONE_LINE,
        ]
    )

    events = await run_stream(transport)

    assert [event["type"] for event in events] == ["reasoning", "reasoning", "text", "finish"]
    reasoning = "".join(event["text"] for event in events if event["type"] == "reasoning")
    assert reasoning == "先想一想"
    assert text_of(events) == "回答"


@pytest.mark.anyio
async def test_stream_accumulates_tool_calls_and_usage() -> None:
    transport = StreamTransport(
        [
            delta_line("先查一下"),
            tool_call_line(0, id="call_1", name="builtin_current_time", arguments='{"time'),
            tool_call_line(0, arguments='zone": "Asia/Shanghai"}'),
            tool_call_line(1, id="call_2", name="builtin_shell", arguments="{}"),
            finish_line("tool_calls"),
            usage_line({"prompt_tokens": 12, "completion_tokens": 34, "total_tokens": 46}),
            DONE_LINE,
        ]
    )

    events = await run_stream(transport, tools=[{"type": "function", "function": {"name": "x"}}])

    assert text_of(events) == "先查一下"
    finish = finish_of(events)
    assert finish["finish_reason"] == "tool_calls"
    assert finish["tool_calls"] == [
        {"id": "call_1", "name": "builtin_current_time", "arguments": '{"timezone": "Asia/Shanghai"}'},
        {"id": "call_2", "name": "builtin_shell", "arguments": "{}"},
    ]
    assert finish["usage"] == {"promptTokens": 12, "completionTokens": 34, "totalTokens": 46, "cachedTokens": None}
    assert transport.captured["body"]["tools"] == [{"type": "function", "function": {"name": "x"}}]


@pytest.mark.anyio
async def test_stream_stops_at_done_and_ignores_noise_lines() -> None:
    transport = StreamTransport(
        [
            b": keep-alive\n\n",
            b"event: ping\n\n",
            b"data: not-json\n\n",
            delta_line("正文"),
            sse_line({"choices": [{"delta": {"role": "assistant"}}]}),
            DONE_LINE,
            delta_line("不应出现"),
        ]
    )

    events = await run_stream(transport)

    assert text_of(events) == "正文"


@pytest.mark.anyio
async def test_stream_without_done_marker_still_ends() -> None:
    # 部分兼容服务不发 [DONE]，流自然结束即可
    transport = StreamTransport([delta_line("没有结束符"), delta_line("也能收到")])

    assert text_of(await run_stream(transport)) == "没有结束符也能收到"


@pytest.mark.anyio
async def test_stream_http_401_is_auth_error() -> None:
    await expect_stream_error(
        StreamTransport([], status=401, error_json={"error": {"message": "bad key"}}),
        ERROR_TYPE_AUTH,
    )


@pytest.mark.anyio
async def test_stream_context_overflow_is_context_error() -> None:
    error = await expect_stream_error(
        StreamTransport(
            [],
            status=400,
            error_json={"error": {"message": "maximum context length is 4096 tokens"}},
        ),
        ERROR_TYPE_CONTEXT,
    )
    assert "上下文容量" in error.message


@pytest.mark.anyio
async def test_stream_error_event_mid_body_is_server_error_and_redacted() -> None:
    transport = StreamTransport([delta_line("部分"), sse_line({"error": {"message": f"boom {API_KEY}"}})])

    error = await expect_stream_error(transport, ERROR_TYPE_SERVER)
    assert API_KEY not in error.message
    assert "***" in error.message
