"""聊天接口测试：会话管理、流式生成、停止、上下文构造与异常路径。

模型服务用 MockTransport 模拟（不发真实网络请求）：普通场景一次性把
SSE 字节块按序下发；停止/生成中场景加 chunk_delay 让生成过程足够长，
主线程通过轮询 /messages 观察生成状态后介入。
"""

import asyncio
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app.services import model_client
from app.services.chat_generation import generation_manager, make_session_title

API_KEY = "sk-test-123456"


# ---- 种子数据 ----


def model_payload(**overrides: object) -> dict:
    payload: dict = {
        "displayName": "DeepSeek-V3",
        "modelIdentifier": "deepseek-chat",
        "baseUrl": "https://api.deepseek.com/v1",
        "apiKey": API_KEY,
        "contextLength": 32768,
        "maxOutputTokens": 1024,
        "temperature": 0.7,
    }
    payload.update(overrides)
    return payload


def create_model(client: TestClient, **overrides: object) -> dict:
    response = client.post("/api/models", json=model_payload(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


def create_agent(client: TestClient, model_id: int, **overrides: object) -> dict:
    payload: dict = {
        "name": "测试助手",
        "description": "",
        "systemPrompt": "你是测试助手",
        "modelId": model_id,
        "maxRounds": 25,
        "toolKeys": [],
        "skillDirNames": [],
        "mcpServerIds": [],
    }
    payload.update(overrides)
    response = client.post("/api/agents", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def seed_default_agent(client: TestClient, **model_overrides: object) -> dict:
    model = create_model(client, **model_overrides)
    return create_agent(client, model["id"])


def create_session(client: TestClient, agent_id: int | None = None) -> dict:
    body = {} if agent_id is None else {"agentId": agent_id}
    response = client.post("/api/chat/sessions", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def send_message(client: TestClient, session_id: int, content: str):
    return client.post(f"/api/chat/sessions/{session_id}/messages", json={"content": content})


def get_messages(client: TestClient, session_id: int) -> dict:
    response = client.get(f"/api/chat/sessions/{session_id}/messages")
    assert response.status_code == 200, response.text
    return response.json()


# ---- 模拟流式响应 ----


def sse_chunk(payload: dict) -> bytes:
    return f"data: {json.dumps(payload)}\n\n".encode("utf-8")


def delta_chunk(text: str) -> bytes:
    return sse_chunk({"choices": [{"delta": {"content": text}}]})


DONE_CHUNK = b"data: [DONE]\n\n"


class StreamTransport(httpx.AsyncBaseTransport):
    """按序下发 SSE 字节块；delay>0 时每块之间让出事件循环，模拟慢生成。"""

    def __init__(
        self,
        chunks: list[bytes],
        status: int = 200,
        delay: float = 0.0,
        capture: list | None = None,
        raise_after: bool = False,
    ) -> None:
        self.chunks = chunks
        self.status = status
        self.delay = delay
        self.capture = capture
        self.raise_after = raise_after

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self.capture is not None:
            self.capture.append(
                {
                    "url": str(request.url),
                    "auth": request.headers.get("Authorization"),
                    "body": json.loads(request.content),
                }
            )
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"message": "上游服务出错"}})

        async def body():
            for chunk in self.chunks:
                if self.delay:
                    await asyncio.sleep(self.delay)
                yield chunk
            if self.raise_after:
                raise httpx.ReadError("connection reset by peer")

        return httpx.Response(
            self.status, headers={"content-type": "text/event-stream"}, content=body()
        )


def install_stream(monkeypatch: pytest.MonkeyPatch, transport: StreamTransport) -> None:
    """把后台生成任务用的 AsyncClient 换成模拟传输。"""

    def factory(timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport, timeout=timeout)

    monkeypatch.setattr(model_client, "_create_async_client", factory)


def collect_events(client: TestClient, session_id: int) -> list[dict]:
    """读完一条 SSE 事件流（终态事件或空闲后服务端会主动结束）。"""
    events: list[dict] = []
    with client.stream("GET", f"/api/chat/sessions/{session_id}/events") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            line = line.strip()
            if line.startswith("data:"):
                events.append(json.loads(line[5:].strip()))
    return events


def wait_for(
    client: TestClient,
    session_id: int,
    predicate,
    timeout: float = 5.0,
    what: str = "条件",
) -> dict:
    """轮询 /messages 直到满足条件，避免对生成时序做脆弱假设。"""
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        last = get_messages(client, session_id)
        if predicate(last):
            return last
        time.sleep(0.02)
    raise AssertionError(f"等待{what}超时：{json.dumps(last, ensure_ascii=False)[:500]}")


def wait_generation_done(client: TestClient, session_id: int, timeout: float = 5.0) -> dict:
    return wait_for(
        client,
        session_id,
        lambda data: data["session"]["isGenerating"] is False,
        timeout=timeout,
        what="生成结束",
    )


@pytest.fixture(autouse=True)
def clean_generation_state(client: TestClient):
    """清空跨测试的生成任务与终态缓存；会话 id 在每个测试的独立库里都从 1 开始。"""
    generation_manager._runs.clear()
    generation_manager._last_outcomes.clear()
    yield
    generation_manager._runs.clear()
    generation_manager._last_outcomes.clear()


# ---- 会话管理 ----


class TestSessions:
    def test_create_without_agent_is_conflict(self, client: TestClient) -> None:
        response = client.post("/api/chat/sessions", json={})

        assert response.status_code == 409
        assert "Agent" in response.json()["detail"]

    def test_create_uses_default_agent(self, client: TestClient) -> None:
        agent = seed_default_agent(client)

        session = create_session(client)

        assert session["title"] == "新会话"
        assert session["agentId"] == agent["id"]
        assert session["agentName"] == "测试助手"
        assert session["isGenerating"] is False

    def test_create_with_explicit_agent(self, client: TestClient) -> None:
        model = create_model(client)
        create_agent(client, model["id"])  # 默认
        second = create_agent(client, model["id"], name="第二个")

        session = create_session(client, agent_id=second["id"])

        assert session["agentId"] == second["id"]
        assert session["agentName"] == "第二个"

    def test_create_with_invalid_agent_is_field_error(self, client: TestClient) -> None:
        agent = seed_default_agent(client)

        response = client.post("/api/chat/sessions", json={"agentId": agent["id"] + 100})

        assert response.status_code == 422
        assert "不存在" in response.json()["detail"]["message"]

    def test_list_orders_by_updated_at_desc(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seed_default_agent(client)
        first = create_session(client)
        second = create_session(client)

        assert [s["id"] for s in client.get("/api/chat/sessions").json()] == [
            second["id"],
            first["id"],
        ]

        # 在旧会话里产生新消息后，排序随之变化
        install_stream(
            monkeypatch,
            StreamTransport(chunks=[delta_chunk("回复"), DONE_CHUNK]),
        )
        assert send_message(client, first["id"], "你好").status_code == 201
        wait_generation_done(client, first["id"])

        sessions = client.get("/api/chat/sessions").json()
        assert [s["id"] for s in sessions] == [first["id"], second["id"]]

    def test_messages_endpoint_returns_empty_initially(self, client: TestClient) -> None:
        agent = seed_default_agent(client)
        session = create_session(client, agent_id=agent["id"])

        data = get_messages(client, session["id"])

        assert data["messages"] == []
        assert data["generation"] is None
        assert data["session"]["id"] == session["id"]

    def test_missing_session_is_404(self, client: TestClient) -> None:
        seed_default_agent(client)

        assert client.get("/api/chat/sessions/999/messages").status_code == 404
        assert client.get("/api/chat/sessions/999/events").status_code == 404
        assert send_message(client, 999, "你好").status_code == 404
        assert client.post("/api/chat/sessions/999/stop").status_code == 404

    def test_switch_agent(self, client: TestClient) -> None:
        model = create_model(client)
        create_agent(client, model["id"])
        other = create_agent(client, model["id"], name="另一个")
        session = create_session(client)

        response = client.put(
            f"/api/chat/sessions/{session['id']}/agent", json={"agentId": other["id"]}
        )

        assert response.status_code == 200
        body = response.json()
        assert body["agentId"] == other["id"]
        assert body["agentName"] == "另一个"

        invalid = client.put(
            f"/api/chat/sessions/{session['id']}/agent", json={"agentId": 999}
        )
        assert invalid.status_code == 422


# ---- 发送消息与流式回复 ----


class TestSendAndStream:
    def test_send_success_stream_and_save(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent = seed_default_agent(client)
        session = create_session(client, agent_id=agent["id"])
        install_stream(
            monkeypatch,
            StreamTransport(
                chunks=[delta_chunk("你好"), delta_chunk("，"), delta_chunk("世界"), DONE_CHUNK],
                delay=0.02,
            ),
        )

        response = send_message(client, session["id"], "  打个招呼  ")

        assert response.status_code == 201, response.text
        body = response.json()
        # 用户消息已保存，正文去除首尾空白
        assert body["userMessage"]["role"] == "user"
        assert body["userMessage"]["content"] == "打个招呼"
        assert body["userMessage"]["seq"] == 1
        assert body["userMessage"]["status"] == "completed"
        assert body["generation"]["phase"] == "thinking"
        assert body["generation"]["agentName"] == "测试助手"

        events = collect_events(client, session["id"])
        types = [e["type"] for e in events]
        assert types[-1] == "run_end"
        assert events[-1]["status"] == "completed"

        # 流式增量不丢不重：快照正文 + 实收增量 == 最终保存的完整回复
        snapshot_content = next(
            (e["content"] for e in events if e["type"] == "snapshot"), ""
        )
        deltas = "".join(e["text"] for e in events if e["type"] == "text_delta")
        done = events[-1]
        if any(e["type"] == "snapshot" for e in events):
            assert snapshot_content + deltas == "你好，世界"
        assert done["message"]["content"] == "你好，世界"
        assert done["message"]["status"] == "completed"
        assert done["message"]["agentName"] == "测试助手"
        assert done["message"]["role"] == "assistant"

        data = get_messages(client, session["id"])
        assert data["session"]["isGenerating"] is False
        assert [m["role"] for m in data["messages"]] == ["user", "assistant"]
        assert data["messages"][1]["content"] == "你好，世界"
        assert data["messages"][1]["seq"] == 2

    def test_request_payload_has_system_prompt_and_key(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent = seed_default_agent(client)
        session = create_session(client, agent_id=agent["id"])
        capture: list[dict] = []
        install_stream(
            monkeypatch,
            StreamTransport(chunks=[delta_chunk("好的"), DONE_CHUNK], capture=capture),
        )

        assert send_message(client, session["id"], "你好").status_code == 201
        wait_generation_done(client, session["id"])

        assert len(capture) == 1
        assert capture[0]["url"] == "https://api.deepseek.com/v1/chat/completions"
        assert capture[0]["auth"] == f"Bearer {API_KEY}"
        # 密钥只在后端读取，请求体不携带任何密钥字段
        assert "apiKey" not in capture[0]["body"]
        assert capture[0]["body"]["stream"] is True
        assert capture[0]["body"]["messages"] == [
            {"role": "system", "content": "你是测试助手"},
            {"role": "user", "content": "你好"},
        ]

    def test_generation_completes_without_any_subscriber(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """后台生成不依赖 SSE 连接：没有订阅者也照常完成并保存。"""
        agent = seed_default_agent(client)
        session = create_session(client, agent_id=agent["id"])
        install_stream(
            monkeypatch, StreamTransport(chunks=[delta_chunk("后台生成"), DONE_CHUNK])
        )

        assert send_message(client, session["id"], "你好").status_code == 201
        data = wait_generation_done(client, session["id"])

        assert [m["role"] for m in data["messages"]] == ["user", "assistant"]
        assert data["messages"][1]["content"] == "后台生成"

    def test_multi_turn_context_in_order(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent = seed_default_agent(client)
        session = create_session(client, agent_id=agent["id"])
        capture: list[dict] = []
        install_stream(
            monkeypatch, StreamTransport(chunks=[delta_chunk("第一回合回复"), DONE_CHUNK], capture=capture)
        )
        assert send_message(client, session["id"], "第一问").status_code == 201
        wait_generation_done(client, session["id"])

        install_stream(
            monkeypatch, StreamTransport(chunks=[delta_chunk("第二回合回复"), DONE_CHUNK], capture=capture)
        )
        assert send_message(client, session["id"], "第二问").status_code == 201
        wait_generation_done(client, session["id"])

        second_request = capture[1]["body"]
        # 本次用户消息只出现一次，完整回复进入上下文
        assert second_request["messages"] == [
            {"role": "system", "content": "你是测试助手"},
            {"role": "user", "content": "第一问"},
            {"role": "assistant", "content": "第一回合回复"},
            {"role": "user", "content": "第二问"},
        ]

    def test_sessions_have_isolated_history(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent = seed_default_agent(client)
        first = create_session(client, agent_id=agent["id"])
        second = create_session(client, agent_id=agent["id"])
        capture: list[dict] = []
        install_stream(
            monkeypatch, StreamTransport(chunks=[delta_chunk("回复一"), DONE_CHUNK], capture=capture)
        )
        send_message(client, first["id"], "会话一的问题")
        wait_generation_done(client, first["id"])

        install_stream(
            monkeypatch, StreamTransport(chunks=[delta_chunk("回复二"), DONE_CHUNK], capture=capture)
        )
        send_message(client, second["id"], "会话二的问题")
        wait_generation_done(client, second["id"])

        # 第二个会话的请求上下文不包含第一个会话的任何消息
        assert capture[1]["body"]["messages"] == [
            {"role": "system", "content": "你是测试助手"},
            {"role": "user", "content": "会话二的问题"},
        ]

    def test_first_message_becomes_title(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        agent = seed_default_agent(client)
        session = create_session(client, agent_id=agent["id"])
        install_stream(monkeypatch, StreamTransport(chunks=[delta_chunk("好的"), DONE_CHUNK]))

        content = "这是一条很长很长的首条消息，用来生成会话标题\n第二行内容"
        assert send_message(client, session["id"], content).status_code == 201

        sessions = client.get("/api/chat/sessions").json()
        assert sessions[0]["title"] == make_session_title(content)
        assert "这是" in sessions[0]["title"]

        # 第二条消息不再改标题
        assert send_message(client, session["id"], "第二条消息换个完全不同的主题").status_code == 201
        sessions = client.get("/api/chat/sessions").json()
        assert sessions[0]["title"] == make_session_title(content)

    def test_whitespace_content_rejected_without_saving(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent = seed_default_agent(client)
        session = create_session(client, agent_id=agent["id"])
        install_stream(monkeypatch, StreamTransport(chunks=[delta_chunk("不应该出现"), DONE_CHUNK]))

        response = send_message(client, session["id"], "   \n  ")

        assert response.status_code == 422
        assert get_messages(client, session["id"])["messages"] == []

    def test_content_over_limit_rejected(self, client: TestClient) -> None:
        agent = seed_default_agent(client)
        session = create_session(client, agent_id=agent["id"])

        response = send_message(client, session["id"], "长" * 32001)

        assert response.status_code == 422
        assert get_messages(client, session["id"])["messages"] == []

    def test_duplicate_send_while_generating_is_conflict(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent = seed_default_agent(client)
        session = create_session(client, agent_id=agent["id"])
        install_stream(
            monkeypatch,
            StreamTransport(chunks=[delta_chunk("慢")] * 60 + [DONE_CHUNK], delay=0.03),
        )
        assert send_message(client, session["id"], "第一问").status_code == 201

        duplicate = send_message(client, session["id"], "重复发送")
        assert duplicate.status_code == 409
        assert "正在生成" in duplicate.json()["detail"]

        # 生成中也不允许切换 Agent
        model = client.get("/api/models").json()[0]
        other = create_agent(client, model["id"], name="切换目标")
        switch = client.put(
            f"/api/chat/sessions/{session['id']}/agent", json={"agentId": other["id"]}
        )
        assert switch.status_code == 409

        client.post(f"/api/chat/sessions/{session['id']}/stop")
        wait_generation_done(client, session["id"])

    def test_messages_endpoint_exposes_live_generation_state(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent = seed_default_agent(client)
        session = create_session(client, agent_id=agent["id"])
        install_stream(
            monkeypatch,
            StreamTransport(chunks=[delta_chunk("部分")] * 80 + [DONE_CHUNK], delay=0.03),
        )
        send_message(client, session["id"], "你好")

        data = wait_for(
            client,
            session["id"],
            lambda d: d["generation"] is not None and d["generation"]["content"],
            what="生成中出现部分正文",
        )

        assert data["generation"]["phase"] == "generating"
        assert "部分" in data["generation"]["content"]
        assert data["session"]["isGenerating"] is True

        client.post(f"/api/chat/sessions/{session['id']}/stop")


# ---- 停止生成 ----


class TestStop:
    def test_stop_saves_partial_content_as_incomplete(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent = seed_default_agent(client)
        session = create_session(client, agent_id=agent["id"])
        install_stream(
            monkeypatch,
            StreamTransport(chunks=[delta_chunk("已经生成的部分正文")] * 200, delay=0.02),
        )
        send_message(client, session["id"], "讲个长故事")

        data = wait_for(
            client,
            session["id"],
            lambda d: d["generation"] is not None and d["generation"]["content"],
            what="生成中出现部分正文",
        )
        partial = data["generation"]["content"]

        stop = client.post(f"/api/chat/sessions/{session['id']}/stop")

        assert stop.status_code == 200
        body = stop.json()
        assert body["outcome"] == "cancelled"
        assert body["message"]["status"] == "incomplete"
        # 停止生效前可能又收到增量：保存的正文以轮询到的部分为前缀、只多不丢
        assert body["message"]["content"].startswith(partial)
        assert body["content"].startswith(partial)

        after = get_messages(client, session["id"])
        assert after["session"]["isGenerating"] is False
        roles = [(m["role"], m["status"]) for m in after["messages"]]
        assert roles == [("user", "completed"), ("assistant", "incomplete")]

        # 再次停止：幂等返回 not_running
        again = client.post(f"/api/chat/sessions/{session['id']}/stop")
        assert again.json()["outcome"] == "not_running"

    def test_stop_without_content_creates_no_message(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent = seed_default_agent(client)
        session = create_session(client, agent_id=agent["id"])
        # 思考阶段（连接后先等待，没有任何正文）停止
        install_stream(monkeypatch, StreamTransport(chunks=[delta_chunk("正文")], delay=5.0))
        send_message(client, session["id"], "你好")

        stop = client.post(f"/api/chat/sessions/{session['id']}/stop")

        assert stop.json()["outcome"] == "cancelled"
        assert stop.json()["message"] is None
        data = get_messages(client, session["id"])
        assert [m["role"] for m in data["messages"]] == ["user"]

    def test_incomplete_reply_excluded_from_next_context(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent = seed_default_agent(client)
        session = create_session(client, agent_id=agent["id"])
        capture: list[dict] = []
        # 第一回合：生成一段后被用户停止
        install_stream(
            monkeypatch,
            StreamTransport(
                chunks=[delta_chunk("被打断的部分回复")] * 200, delay=0.02, capture=capture
            ),
        )
        send_message(client, session["id"], "第一问")
        wait_for(
            client,
            session["id"],
            lambda d: d["generation"] is not None and d["generation"]["content"],
            what="生成中出现部分正文",
        )
        client.post(f"/api/chat/sessions/{session['id']}/stop")

        # 第二回合：未完成回复不进入上下文
        install_stream(
            monkeypatch,
            StreamTransport(chunks=[delta_chunk("第二回合回复"), DONE_CHUNK], capture=capture),
        )
        send_message(client, session["id"], "第二问")
        wait_generation_done(client, session["id"])

        assert capture[1]["body"]["messages"] == [
            {"role": "system", "content": "你是测试助手"},
            {"role": "user", "content": "第一问"},
            {"role": "user", "content": "第二问"},
        ]


# ---- 异常路径 ----


class TestErrors:
    def test_upstream_http_error_reports_and_keeps_user_message(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent = seed_default_agent(client)
        session = create_session(client, agent_id=agent["id"])
        install_stream(monkeypatch, StreamTransport(chunks=[], status=500))

        assert send_message(client, session["id"], "你好").status_code == 201
        data = wait_generation_done(client, session["id"])

        # 用户消息保留，没有创建空白的 assistant 回复
        assert [m["role"] for m in data["messages"]] == ["user"]

        events = collect_events(client, session["id"])
        assert [e["type"] for e in events] == ["run_end"]
        assert events[0]["status"] == "error"
        assert "HTTP 500" in events[0]["error"]["message"]
        assert events[0]["content"] == ""
        assert events[0]["message"] is None

    def test_empty_reply_is_error_without_saving(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent = seed_default_agent(client)
        session = create_session(client, agent_id=agent["id"])
        install_stream(monkeypatch, StreamTransport(chunks=[DONE_CHUNK]))

        send_message(client, session["id"], "你好")
        wait_generation_done(client, session["id"])

        events = collect_events(client, session["id"])
        assert events[0]["type"] == "run_end"
        assert events[0]["error"]["message"] == "模型返回了空回复"
        data = get_messages(client, session["id"])
        assert [m["role"] for m in data["messages"]] == ["user"]

    def test_stream_interrupted_saves_partial_as_incomplete(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent = seed_default_agent(client)
        session = create_session(client, agent_id=agent["id"])
        install_stream(
            monkeypatch,
            StreamTransport(chunks=[delta_chunk("生成到一半")], raise_after=True),
        )

        send_message(client, session["id"], "你好")
        data = wait_generation_done(client, session["id"])

        # 错误提示不是聊天内容；已生成部分保存为未完成
        roles = [(m["role"], m["status"]) for m in data["messages"]]
        assert roles == [("user", "completed"), ("assistant", "incomplete")]
        assert data["messages"][1]["content"] == "生成到一半"

        events = collect_events(client, session["id"])
        assert events[0]["type"] == "run_end"
        assert events[0]["error"]["type"] == "network"
        assert events[0]["content"] == "生成到一半"
        assert events[0]["message"]["status"] == "incomplete"

    def test_context_overflow_rejected_before_saving(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 上下文容量 200，输出预留 100：150 token 的系统提示词 + 输入必然超限
        model = create_model(client, contextLength=200, maxOutputTokens=100)
        create_agent(client, model["id"], systemPrompt="长" * 150)
        session = create_session(client)

        install_stream(monkeypatch, StreamTransport(chunks=[delta_chunk("不应该被请求"), DONE_CHUNK]))
        response = send_message(client, session["id"], "这是一段注定超限的提问")

        assert response.status_code == 409
        assert "上下文容量" in response.json()["detail"]
        # 校验失败时不保存用户消息、不请求模型
        assert get_messages(client, session["id"])["messages"] == []

    def test_upstream_context_error_is_classified(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent = seed_default_agent(client)
        session = create_session(client, agent_id=agent["id"])
        # 400 + context length 报文：归类为上下文超限而不是普通 HTTP 错误
        class ContextErrorTransport(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                return httpx.Response(
                    400,
                    json={"error": {"message": "This model's maximum context length is 4096 tokens."}},
                )

        monkeypatch.setattr(
            model_client,
            "_create_async_client",
            lambda timeout: httpx.AsyncClient(
                transport=ContextErrorTransport(), timeout=timeout
            ),
        )

        send_message(client, session["id"], "你好")
        wait_generation_done(client, session["id"])

        events = collect_events(client, session["id"])
        assert events[0]["type"] == "run_end"
        assert events[0]["error"]["type"] == "context"
        assert "上下文容量" in events[0]["error"]["message"]

    def test_api_key_never_leaks_in_events_or_saved_content(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent = seed_default_agent(client)
        session = create_session(client, agent_id=agent["id"])
        install_stream(
            monkeypatch,
            StreamTransport(chunks=[delta_chunk(f"泄露 {API_KEY} 了"), DONE_CHUNK]),
        )

        send_message(client, session["id"], "你好")
        wait_generation_done(client, session["id"])

        data = get_messages(client, session["id"])
        assert API_KEY not in data["messages"][1]["content"]
        assert "***" in data["messages"][1]["content"]

        events = collect_events(client, session["id"])
        assert API_KEY not in json.dumps(events, ensure_ascii=False)


# ---- Agent 删除与切换 ----


class TestAgentLifecycle:
    def test_delete_agent_keeps_session_and_messages(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        model = create_model(client)
        first = create_agent(client, model["id"], name="旧助手")
        second = create_agent(client, model["id"], name="新助手")
        session = create_session(client, agent_id=first["id"])
        install_stream(monkeypatch, StreamTransport(chunks=[delta_chunk("旧助手的回复"), DONE_CHUNK]))
        send_message(client, session["id"], "你好")
        wait_generation_done(client, session["id"])

        # 删除默认 Agent 需要指定新默认
        response = client.delete(f"/api/agents/{first['id']}?new_default_id={second['id']}")
        assert response.status_code == 204

        # 会话保留，Agent 引用被解除；旧回复仍显示生成时的 Agent 名称
        data = get_messages(client, session["id"])
        assert data["session"]["agentId"] is None
        assert data["session"]["agentName"] is None
        assert data["messages"][1]["agentName"] == "旧助手"

        # 继续发送前需要选择可用 Agent
        blocked = send_message(client, session["id"], "再问一句")
        assert blocked.status_code == 409
        assert "选择" in blocked.json()["detail"]

        # 切换到新 Agent 后可继续，且回复归属新 Agent
        client.put(f"/api/chat/sessions/{session['id']}/agent", json={"agentId": second["id"]})
        install_stream(monkeypatch, StreamTransport(chunks=[delta_chunk("新助手的回复"), DONE_CHUNK]))
        assert send_message(client, session["id"], "再问一句").status_code == 201
        data = wait_generation_done(client, session["id"])
        assert data["messages"][3]["agentName"] == "新助手"
        assert data["messages"][3]["content"] == "新助手的回复"

    def test_switch_agent_changes_model_and_prompt(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        first_model = create_model(client, displayName="模型一")
        second_model = create_model(
            client, displayName="模型二", baseUrl="https://api.other.com/v1"
        )
        first_agent = create_agent(client, first_model["id"], name="助手一", systemPrompt="提示词一")
        second_agent = create_agent(
            client, second_model["id"], name="助手二", systemPrompt="提示词二"
        )
        session = create_session(client, agent_id=first_agent["id"])
        capture: list[dict] = []
        install_stream(
            monkeypatch,
            StreamTransport(chunks=[delta_chunk("回复一"), DONE_CHUNK], capture=capture),
        )
        send_message(client, session["id"], "第一问")
        wait_generation_done(client, session["id"])

        client.put(f"/api/chat/sessions/{session['id']}/agent", json={"agentId": second_agent["id"]})
        install_stream(
            monkeypatch,
            StreamTransport(chunks=[delta_chunk("回复二"), DONE_CHUNK], capture=capture),
        )
        send_message(client, session["id"], "第二问")
        wait_generation_done(client, session["id"])

        # 第二次请求使用新 Agent 的模型（服务地址）与系统提示词，保留会话历史
        assert capture[1]["url"] == "https://api.other.com/v1/chat/completions"
        assert capture[1]["body"]["messages"][0] == {"role": "system", "content": "提示词二"}
        assert [m["content"] for m in capture[1]["body"]["messages"][1:]] == [
            "第一问",
            "回复一",
            "第二问",
        ]


# ---- 事件流端点 ----


class TestEvents:
    def test_idle_session_emits_idle_event(self, client: TestClient) -> None:
        agent = seed_default_agent(client)
        session = create_session(client, agent_id=agent["id"])

        assert collect_events(client, session["id"]) == [{"type": "idle"}]

    def test_replays_recent_outcome_for_late_subscriber(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent = seed_default_agent(client)
        session = create_session(client, agent_id=agent["id"])
        install_stream(monkeypatch, StreamTransport(chunks=[delta_chunk("完成内容"), DONE_CHUNK]))
        send_message(client, session["id"], "你好")
        wait_generation_done(client, session["id"])

        events = collect_events(client, session["id"])

        # 订阅前任务已结束：补发一次终态事件
        assert [e["type"] for e in events] == ["run_end"]
        assert events[0]["status"] == "completed"
        assert events[0]["message"]["content"] == "完成内容"

# ---- Agent Runtime 工具循环（聊天接口端到端） ----


def tool_call_chunk(call_id: str, name: str, arguments: str) -> bytes:
    return sse_chunk(
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": call_id,
                                "type": "function",
                                "function": {"name": name, "arguments": arguments},
                            }
                        ]
                    }
                }
            ]
        }
    )


def finish_chunk(reason: str) -> bytes:
    return sse_chunk({"choices": [{"delta": {}, "finish_reason": reason}]})


class MultiResponseTransport(httpx.AsyncBaseTransport):
    """按请求次序返回预置响应，并捕获每个请求体（工具循环用）。"""

    def __init__(self, responses: list[list[bytes]], delay: float = 0.0) -> None:
        self.responses = list(responses)
        self.delay = delay
        self.bodies: list[dict] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.bodies.append(json.loads(request.content))
        chunks = self.responses.pop(0) if self.responses else [DONE_CHUNK]

        async def body():
            for chunk in chunks:
                if self.delay:
                    await asyncio.sleep(self.delay)
                yield chunk

        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body())


class TestRuntimeToolLoop:
    def test_chat_runs_tool_loop_end_to_end(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch, seeded_tools
    ) -> None:
        model = create_model(client)
        agent = create_agent(client, model["id"], toolKeys=["builtin_current_time"])
        session = create_session(client, agent_id=agent["id"])
        transport = MultiResponseTransport(
            [
                [
                    delta_chunk("先查时间："),
                    tool_call_chunk("call_1", "builtin_current_time", '{"timezone": "Asia/Shanghai"}'),
                    finish_chunk("tool_calls"),
                    DONE_CHUNK,
                ],
                [delta_chunk("现在是北京时间"), DONE_CHUNK],
            ]
        )

        def factory(timeout: float) -> httpx.AsyncClient:
            return httpx.AsyncClient(transport=transport, timeout=timeout)

        monkeypatch.setattr(model_client, "_create_async_client", factory)

        assert send_message(client, session["id"], "现在几点").status_code == 201
        data = wait_generation_done(client, session["id"])

        # 回复 = 各轮正文按序拼接并保存
        assert [m["role"] for m in data["messages"]] == ["user", "assistant"]
        assert data["messages"][1]["content"] == "先查时间：现在是北京时间"
        assert data["messages"][1]["status"] == "completed"

        # 第二轮请求携带 assistant(tool_calls) 与工具结果
        second = transport.bodies[1]["messages"]
        assert second[2]["role"] == "assistant"
        assert second[2]["tool_calls"][0]["id"] == "call_1"
        assert second[3]["role"] == "tool"
        assert second[3]["tool_call_id"] == "call_1"
        assert "datetime" in second[3]["content"]

        # 事件：run_start → 模型请求 → 工具事件（配对）→ run_end
        events = collect_events(client, session["id"])
        # 迟到订阅者只能补收终态：验证终态事件本身
        assert events[-1]["type"] == "run_end"
        assert events[-1]["status"] == "completed"
        assert events[-1]["message"]["content"] == "先查时间：现在是北京时间"

    def test_agent_without_tools_omits_tools_payload(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent = seed_default_agent(client)
        session = create_session(client, agent_id=agent["id"])
        capture: list[dict] = []
        install_stream(
            monkeypatch, StreamTransport(chunks=[delta_chunk("好的"), DONE_CHUNK], capture=capture)
        )

        assert send_message(client, session["id"], "你好").status_code == 201
        wait_generation_done(client, session["id"])

        assert "tools" not in capture[0]["body"]

    def test_tool_events_carry_display_payloads(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch, seeded_tools
    ) -> None:
        """阶段九：工具事件带整理后的参数与结果，易读名称做主标题。"""
        model = create_model(client)
        agent = create_agent(client, model["id"], toolKeys=["builtin_current_time"])
        session = create_session(client, agent_id=agent["id"])
        transport = MultiResponseTransport(
            [
                [
                    delta_chunk("先查时间："),
                    tool_call_chunk("call_1", "builtin_current_time", '{"timezone": "Asia/Shanghai"}'),
                    finish_chunk("tool_calls"),
                    DONE_CHUNK,
                ],
                [delta_chunk("现在是北京时间"), DONE_CHUNK],
            ],
            delay=0.05,
        )

        def factory(timeout: float) -> httpx.AsyncClient:
            return httpx.AsyncClient(transport=transport, timeout=timeout)

        monkeypatch.setattr(model_client, "_create_async_client", factory)

        assert send_message(client, session["id"], "现在几点").status_code == 201
        events = collect_events(client, session["id"])

        start = next(e for e in events if e["type"] == "tool_call_start")
        assert start["displayName"] == "当前时间"
        assert start["serverName"] is None
        assert start["arguments"] == {"timezone": "Asia/Shanghai"}
        end = next(e for e in events if e["type"] == "tool_call_end")
        assert end["status"] == "success"
        assert end["result"]["truncated"] is False
        assert "datetime" in end["result"]["text"]
        assert events[-1]["type"] == "run_end"
        assert events[-1]["status"] == "completed"

    def test_snapshot_timeline_restores_tool_cards(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch, seeded_tools
    ) -> None:
        """阶段九：重连快照带回交错时间线（正文段 + 工具卡片），恢复顺序与状态。"""
        model = create_model(client)
        agent = create_agent(client, model["id"], toolKeys=["builtin_current_time"])
        session = create_session(client, agent_id=agent["id"])
        transport = MultiResponseTransport(
            [
                [
                    delta_chunk("先查时间："),
                    tool_call_chunk("call_1", "builtin_current_time", '{"timezone": "Asia/Shanghai"}'),
                    finish_chunk("tool_calls"),
                    DONE_CHUNK,
                ],
                # 第二轮放慢，保证订阅发生在运行中段
                [delta_chunk("现在是"), delta_chunk("北京时间"), delta_chunk("，吃了吗"), DONE_CHUNK],
            ],
            delay=0.05,
        )

        def factory(timeout: float) -> httpx.AsyncClient:
            return httpx.AsyncClient(transport=transport, timeout=timeout)

        monkeypatch.setattr(model_client, "_create_async_client", factory)

        assert send_message(client, session["id"], "现在几点").status_code == 201
        # 等工具卡片进入时间线后再订阅：快照必须完整带回，而不是只给正文
        wait_for(
            client,
            session["id"],
            lambda data: any(
                item.get("kind") == "tool" for item in (data.get("generation") or {}).get("timeline", [])
            ),
            what="时间线出现工具卡片",
        )
        events = collect_events(client, session["id"])

        snapshot = events[0]
        assert snapshot["type"] == "snapshot"
        assert [item["kind"] for item in snapshot["timeline"]] == ["text", "tool"]
        assert snapshot["timeline"][0]["text"] == "先查时间："
        tool_item = snapshot["timeline"][1]
        assert tool_item["callId"] == "call_1"
        assert tool_item["displayName"] == "当前时间"
        assert tool_item["status"] in ("running", "success")
        assert tool_item["arguments"] == {"timezone": "Asia/Shanghai"}
        # 订阅后继续实时收到正文增量直到终态
        assert events[-1]["type"] == "run_end"
        assert events[-1]["status"] == "completed"
        assert events[-1]["message"]["content"] == "先查时间：现在是北京时间，吃了吗"


class TestContextCompaction:
    """阶段十：会话摘要过滤、发送前容量校验与压缩结果持久化。"""

    def _session_row(self, db_engine, session_id: int) -> object:
        from sqlalchemy.orm import sessionmaker
        from app.db.models.chat import ChatSession

        db = sessionmaker(bind=db_engine)()
        try:
            return db.get(ChatSession, session_id)
        finally:
            db.close()

    def test_history_after_boundary_excluded_and_summary_injected(
        self, client: TestClient, db_engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent = seed_default_agent(client)
        session = create_session(client, agent_id=agent["id"])
        capture: list[dict] = []
        install_stream(
            monkeypatch, StreamTransport(chunks=[delta_chunk("好的"), DONE_CHUNK], capture=capture)
        )
        send_message(client, session["id"], "第一条：围绕目标的讨论")
        wait_generation_done(client, session["id"])
        send_message(client, session["id"], "第二条：已确认的事实")
        wait_generation_done(client, session["id"])

        # 直接把会话摘要与边界写入会话（模拟此前一次成功压缩）
        from sqlalchemy.orm import sessionmaker
        from app.db.models.chat import ChatSession

        db = sessionmaker(bind=db_engine)()
        try:
            row = db.get(ChatSession, session["id"])
            row.summary_text = "此前对话的摘要内容"
            row.summary_boundary_seq = 2  # 覆盖第一条（seq 1、2）
            db.commit()
        finally:
            db.close()

        assert send_message(client, session["id"], "第三条：新问题").status_code == 201
        wait_generation_done(client, session["id"])

        # 请求 = 系统提示词 + 摘要 + 边界之后的消息 + 本次输入
        messages = capture[2]["body"]["messages"]
        assert messages[0]["content"] == "你是测试助手"
        assert messages[1]["role"] == "system"
        assert "会话背景摘要" in messages[1]["content"]
        assert "此前对话的摘要内容" in messages[1]["content"]
        assert [m["content"] for m in messages[2:]] == [
            "第二条：已确认的事实",
            "好的",
            "第三条：新问题",
        ]

    def test_compaction_disabled_overflow_rejected_before_saving(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 容量 800 - 200 - 64 = 536：第二轮累计约 640 token 超容量且已关闭自动压缩
        model = create_model(client, contextLength=800, maxOutputTokens=200)
        create_agent(client, model["id"], autoCompact=False)
        session = create_session(client)
        install_stream(
            monkeypatch, StreamTransport(chunks=[delta_chunk("好的"), DONE_CHUNK])
        )

        assert send_message(client, session["id"], "长" * 300).status_code == 201
        wait_generation_done(client, session["id"])
        response = send_message(client, session["id"], "长" * 300)

        assert response.status_code == 409
        assert "自动压缩" in response.json()["detail"]
        # 校验失败不保存用户消息
        data = get_messages(client, session["id"])
        assert [m["content"] for m in data["messages"]] == ["长" * 300, "好的"]

    def test_full_compaction_flow_persists_summary(
        self, client: TestClient, db_engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 容量 2000 - 400 - 100 = 1500；触发阈值 1200。保留 1 轮。
        model = create_model(client, contextLength=2000, maxOutputTokens=400)
        create_agent(client, model["id"], compactKeepRounds=1)
        session = create_session(client, agent_id=None)
        # 会话创建时使用默认 Agent（即上面这个）
        capture: list[dict] = []
        install_stream(
            monkeypatch,
            StreamTransport(chunks=[delta_chunk("会话要点摘要"), DONE_CHUNK], capture=capture),
        )

        # 第 3 轮后历史约 957 token；第 4 轮估算约 1276 ≥ 阈值 1200 触发压缩
        model_calls_by_round: dict[int, int] = {}
        for i in range(1, 6):
            assert send_message(client, session["id"], f"第{i}条消息" + "细" * 300).status_code == 201
            wait_generation_done(client, session["id"])
            events = collect_events(client, session["id"])
            assert events[0]["type"] == "run_end"
            model_calls_by_round[i] = events[0]["modelCalls"]
        # 压缩是真实模型调用：第 4 轮计 2 次（摘要 + 回答），其余轮 1 次
        assert model_calls_by_round == {1: 1, 2: 1, 3: 1, 4: 2, 5: 1}

        # 摘要与边界一致保存：第 4 轮把前 2 轮（seq 1..4）折叠进摘要
        row = self._session_row(db_engine, session["id"])
        assert row.summary_text == "会话要点摘要"
        assert row.summary_boundary_seq == 4
        # 原始消息全部保留，页面仍可查看完整历史
        data = get_messages(client, session["id"])
        assert len(data["messages"]) == 10
        assert data["messages"][0]["content"].startswith("第1条消息")

        # 第六轮请求：摘要 + 边界之后的最近对话 + 本次输入
        assert send_message(client, session["id"], "最后一问").status_code == 201
        wait_generation_done(client, session["id"])
        messages = capture[-1]["body"]["messages"]
        assert messages[1]["role"] == "system"
        assert "会话要点摘要" in messages[1]["content"]
        contents = [m["content"] for m in messages]
        assert any(c.startswith("第3条消息") for c in contents)
        assert any(c.startswith("第5条消息") for c in contents)
        assert "最后一问" in contents
        assert not any(c.startswith("第1条消息") for c in contents)
