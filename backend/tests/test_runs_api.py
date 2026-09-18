"""运行记录与可观察性测试（阶段十一）。

模型服务用 MockTransport 模拟（与 test_chat_api 相同模式，不发真实网络
请求）。覆盖：Run 生命周期各状态（成功 / 工具失败后完成 / 模型失败 /
取消 / 轮数耗尽 / 后台完成 / 异常中断恢复）、指标口径（调用次数、Token
汇总与缓存命中、首个输出耗时）、事件配对与去重、聊天与详情状态一致、
敏感信息脱敏、列表查询次数与分页筛选。
"""

import asyncio
import json
import time
import uuid
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from app.db.models.run_record import (
    AgentRun,
    REASON_INTERRUPTED,
    RUN_STATUS_FAILED,
    RUN_STATUS_RUNNING,
)
from app.services import model_client
from app.services.chat_generation import generation_manager
from app.services.run_recorder import RunRecorder, mark_interrupted_runs

API_KEY = "sk-test-123456"


# ---- 种子与流模拟 helper（与 test_chat_api 同模式） ----


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


def seed_default_agent(client: TestClient, **model_overrides: object) -> dict:
    created = client.post("/api/models", json=model_payload(**model_overrides))
    assert created.status_code == 201, created.text
    model = created.json()
    payload = {
        "name": "测试助手",
        "description": "",
        "systemPrompt": "你是测试助手",
        "modelId": model["id"],
        "maxRounds": 25,
        "toolKeys": [],
        "skillDirNames": [],
        "mcpServerIds": [],
    }
    response = client.post("/api/agents", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def create_session(client: TestClient, agent_id: int | None = None) -> dict:
    body = {} if agent_id is None else {"agentId": agent_id}
    response = client.post("/api/chat/sessions", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def sse_chunk(payload: dict) -> bytes:
    return f"data: {json.dumps(payload)}\n\n".encode("utf-8")


def delta_chunk(text: str) -> bytes:
    return sse_chunk({"choices": [{"delta": {"content": text}}]})


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


def usage_chunk(
    prompt: int = 10, completion: int = 5, total: int = 15, cached: int | None = None
) -> bytes:
    """带 usage 的分块；cached 给出时用 OpenAI 的 prompt_tokens_details 口径。"""
    usage: dict = {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
    }
    if cached is not None:
        usage["prompt_tokens_details"] = {"cached_tokens": cached}
    return sse_chunk({"choices": [{"delta": {}}], "usage": usage})


DONE_CHUNK = b"data: [DONE]\n\n"


class StreamTransport(httpx.AsyncBaseTransport):
    """单次响应按序下发 SSE 字节块；delay>0 模拟慢生成。"""

    def __init__(self, chunks: list[bytes], status: int = 200, delay: float = 0.0) -> None:
        self.chunks = chunks
        self.status = status
        self.delay = delay

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"message": "上游服务出错"}})

        async def body():
            for chunk in self.chunks:
                if self.delay:
                    await asyncio.sleep(self.delay)
                yield chunk

        return httpx.Response(
            self.status, headers={"content-type": "text/event-stream"}, content=body()
        )


class MultiResponseTransport(httpx.AsyncBaseTransport):
    """按请求次序返回预置响应（工具循环多轮用）。"""

    def __init__(self, responses: list[list[bytes]], delay: float = 0.0) -> None:
        self.responses = list(responses)
        self.delay = delay

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        chunks = self.responses.pop(0) if self.responses else [DONE_CHUNK]

        async def body():
            for chunk in chunks:
                if self.delay:
                    await asyncio.sleep(self.delay)
                yield chunk

        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body())


def install_transport(monkeypatch: pytest.MonkeyPatch, transport: httpx.AsyncBaseTransport) -> None:
    def factory(timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport, timeout=timeout)

    monkeypatch.setattr(model_client, "_create_async_client", factory)


def send_message(client: TestClient, session_id: int, content: str):
    return client.post(f"/api/chat/sessions/{session_id}/messages", json={"content": content})


def wait_done(client: TestClient, session_id: int, timeout: float = 5.0) -> None:
    """轮询直到会话不再生成（后台任务写完 Run 终态后才结束）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = client.get(f"/api/chat/sessions/{session_id}/messages").json()
        if data["session"]["isGenerating"] is False:
            return
        time.sleep(0.02)
    raise AssertionError("等待运行结束超时")


def list_runs(client: TestClient, **params: object) -> dict:
    response = client.get("/api/runs", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def get_run_detail(client: TestClient, run_id: int) -> dict:
    response = client.get(f"/api/runs/{run_id}")
    assert response.status_code == 200, response.text
    return response.json()


def first_run(client: TestClient) -> dict:
    data = list_runs(client)
    assert data["items"], "应至少有一条运行记录"
    return data["items"][0]


def collect_events(client: TestClient, session_id: int) -> list[dict]:
    """读完一条 SSE 事件流（断开后按上一阶段机制取消运行）。"""
    events: list[dict] = []
    with client.stream("GET", f"/api/chat/sessions/{session_id}/events") as response:
        assert response.status_code == 200
        for line in response.iter_lines():
            line = line.strip()
            if line.startswith("data:"):
                events.append(json.loads(line[5:].strip()))
    return events


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def make_run_row(db_session, **overrides: object) -> AgentRun:
    """直接插入一条 Run 记录（列表 / 筛选 / 查询次数测试用）。"""
    now = utcnow()
    values: dict = {
        "run_uid": uuid.uuid4().hex,
        "session_id": None,
        "agent_id": None,
        "agent_name": "快照Agent",
        "model_id": None,
        "model_name": "快照模型",
        "model_identifier": "snap-model",
        "status": "completed",
        "finish_reason": "model_finished",
        "user_input_preview": "测试输入",
        "started_at": now,
        "model_calls": 1,
        "tool_calls": 0,
        "rejected_tool_calls": 0,
        "tokens_incomplete": False,
        "created_at": now,
    }
    values.update(overrides)
    row = AgentRun(**values)
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture(autouse=True)
def clean_generation_state(client: TestClient):
    """清空跨测试的生成任务与终态缓存。"""
    generation_manager._runs.clear()
    generation_manager._last_outcomes.clear()
    yield
    generation_manager._runs.clear()
    generation_manager._last_outcomes.clear()


# ---- 正常成功与后台运行 ----


class TestSuccessfulRun:
    def test_run_record_for_plain_completion(self, client: TestClient, monkeypatch) -> None:
        """一次发送消息对应一条 Run：后台（无订阅者）完成后写入成功终态。"""
        agent = seed_default_agent(client)
        session = create_session(client, agent_id=agent["id"])
        install_transport(
            monkeypatch,
            StreamTransport(chunks=[delta_chunk("你好，我是回复"), usage_chunk(10, 5, 15), DONE_CHUNK]),
        )

        assert send_message(client, session["id"], "打个招呼").status_code == 201
        wait_done(client, session["id"])  # 期间不订阅 SSE：后台运行结束仍写入原 Run

        run = first_run(client)
        assert run["status"] == "completed"
        assert run["finishReason"] == "model_finished"
        assert run["agentName"] == "测试助手"
        assert run["modelName"] == "DeepSeek-V3"
        assert run["modelIdentifier"] == "deepseek-chat"
        assert run["sessionId"] == session["id"]
        assert run["modelCalls"] == 1
        assert run["toolCalls"] == 0
        assert run["rejectedToolCalls"] == 0
        assert run["durationMs"] is not None and run["durationMs"] >= 0
        assert run["firstOutputMs"] is not None
        assert run["errorSummary"] is None
        assert run["tokenUsage"] == {
            "promptTokens": 10,
            "completionTokens": 5,
            "totalTokens": 15,
            "cachedTokens": None,
        }
        assert run["tokensIncomplete"] is False

    def test_events_timeline_for_plain_completion(self, client: TestClient, monkeypatch) -> None:
        agent = seed_default_agent(client)
        session = create_session(client, agent_id=agent["id"])
        install_transport(monkeypatch, StreamTransport(chunks=[delta_chunk("回复"), DONE_CHUNK]))

        assert send_message(client, session["id"], "hi").status_code == 201
        wait_done(client, session["id"])

        detail = get_run_detail(client, first_run(client)["id"])
        types = [e["eventType"] for e in detail["events"]]
        # text_delta 流式增量不落库；时间线按真实事件顺序（seq）
        assert types == ["run_start", "model_request_start", "model_request_end", "run_end"]
        assert [e["seq"] for e in detail["events"]] == sorted(e["seq"] for e in detail["events"])
        # 模型步骤：开始 / 结束用 callId 配对，带耗时
        model_end = detail["events"][2]
        assert model_end["payload"]["callId"] == detail["events"][1]["payload"]["callId"]
        assert model_end["payload"]["status"] == "success"
        assert model_end["payload"]["durationMs"] >= 0

    def test_tool_loop_metrics_and_pairing(
        self, client: TestClient, monkeypatch, seeded_tools
    ) -> None:
        """工具循环：两轮模型请求 + 一次工具执行；事件按 callId 配对。"""
        agent = seed_default_agent(client)
        created = client.post(
            "/api/agents",
            json={
                "name": "工具助手",
                "description": "",
                "systemPrompt": "你是工具助手",
                "modelId": agent["model"]["id"],
                "maxRounds": 25,
                "toolKeys": ["builtin_current_time"],
                "skillDirNames": [],
                "mcpServerIds": [],
            },
        )
        assert created.status_code == 201
        session = create_session(client, agent_id=created.json()["id"])
        install_transport(
            monkeypatch,
            MultiResponseTransport(
                [
                    [
                        delta_chunk("先查时间："),
                        tool_call_chunk("call_1", "builtin_current_time", '{"timezone": "Asia/Shanghai"}'),
                        finish_chunk("tool_calls"),
                        DONE_CHUNK,
                    ],
                    [delta_chunk("现在是中午"), DONE_CHUNK],
                ]
            ),
        )

        assert send_message(client, session["id"], "现在几点").status_code == 201
        wait_done(client, session["id"])

        run = first_run(client)
        assert run["status"] == "completed"
        assert run["modelCalls"] == 2
        assert run["toolCalls"] == 1
        assert run["firstOutputMs"] is not None

        detail = get_run_detail(client, run["id"])
        tool_start = next(e for e in detail["events"] if e["eventType"] == "tool_call_start")
        tool_end = next(e for e in detail["events"] if e["eventType"] == "tool_call_end")
        assert tool_start["payload"]["callId"] == tool_end["payload"]["callId"] == "call_1"
        assert tool_end["payload"]["status"] == "success"
        assert tool_end["payload"]["summary"]
        # 完整工具结果不在事件里（详细载荷单独存），摘要保留
        assert tool_end["payload"]["result"] is None
        assert tool_end["hasDetail"] is True


# ---- 失败、取消与轮数耗尽 ----


class TestRunOutcomes:
    def test_model_error_marks_run_failed_and_keeps_partial_content(
        self, client: TestClient, monkeypatch
    ) -> None:
        """模型失败前已输出部分文字：Run 标记失败，正文保留为未完成消息。"""
        agent = seed_default_agent(client)
        session = create_session(client, agent_id=agent["id"])
        # 第一次输出部分正文并请求工具（触发第二轮），第二次上游 500
        class FlakyTransport(httpx.AsyncBaseTransport):
            def __init__(self) -> None:
                self.calls = 0

            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                self.calls += 1
                if self.calls == 1:
                    return await StreamTransport(
                        chunks=[
                            delta_chunk("写了一半"),
                            tool_call_chunk("call_1", "builtin_current_time", "{}"),
                            finish_chunk("tool_calls"),
                            DONE_CHUNK,
                        ]
                    ).handle_async_request(request)
                return httpx.Response(500, json={"error": {"message": "上游服务出错"}})

        install_transport(monkeypatch, FlakyTransport())

        assert send_message(client, session["id"], "继续写").status_code == 201
        wait_done(client, session["id"])

        run = first_run(client)
        assert run["status"] == "failed"
        assert run["finishReason"] == "model_error"
        assert run["errorSummary"]  # 失败原因可见，无需翻原始日志
        assert run["modelCalls"] == 2  # 失败请求也计入模型调用次数

        # 未完成内容保留：会话里保存了 incomplete 消息
        messages = client.get(f"/api/chat/sessions/{session['id']}/messages").json()["messages"]
        assistant = [m for m in messages if m["role"] == "assistant"]
        assert assistant and assistant[-1]["status"] == "incomplete"
        assert assistant[-1]["content"] == "写了一半"

        # run_end 事件的正文片段保存在详细载荷，事件里只有预览
        detail = get_run_detail(client, run["id"])
        run_end = detail["events"][-1]
        assert run_end["payload"]["content"] is None
        assert "写了一半" in run_end["payload"]["contentPreview"]
        detail_response = client.get(f"/api/runs/{run['id']}/events/{run_end['id']}/detail")
        assert detail_response.status_code == 200
        assert "写了一半" in detail_response.json()["payload"]["content"]

    def test_tool_failure_does_not_fail_run(
        self, client: TestClient, monkeypatch, seeded_tools
    ) -> None:
        """单个工具失败后 Agent 继续完成任务：Run 仍为成功，步骤标失败。"""
        agent = seed_default_agent(client)
        created = client.post(
            "/api/agents",
            json={
                "name": "文件助手",
                "description": "",
                "systemPrompt": "你是文件助手",
                "modelId": agent["model"]["id"],
                "maxRounds": 25,
                "toolKeys": ["builtin_file"],
                "skillDirNames": [],
                "mcpServerIds": [],
            },
        )
        assert created.status_code == 201
        session = create_session(client, agent_id=created.json()["id"])
        install_transport(
            monkeypatch,
            MultiResponseTransport(
                [
                    [
                        tool_call_chunk(
                            "call_1", "builtin_file", '{"action": "read", "path": "不存在的文件.txt"}'
                        ),
                        finish_chunk("tool_calls"),
                        DONE_CHUNK,
                    ],
                    [delta_chunk("文件不存在，但我可以继续回答"), DONE_CHUNK],
                ]
            ),
        )

        assert send_message(client, session["id"], "读一下文件").status_code == 201
        wait_done(client, session["id"])

        run = first_run(client)
        assert run["status"] == "completed"  # 工具失败不等于 Run 失败
        assert run["toolCalls"] == 1  # 进入执行入口后失败，计入执行次数
        detail = get_run_detail(client, run["id"])
        tool_end = next(e for e in detail["events"] if e["eventType"] == "tool_call_end")
        assert tool_end["payload"]["status"] == "error"
        assert tool_end["payload"]["summary"]

    def test_rounds_exhausted_is_partial(self, client: TestClient, monkeypatch, seeded_tools) -> None:
        """轮数耗尽：部分完成，不是失败；状态与结束原因分开保存。"""
        agent = seed_default_agent(client)
        created = client.post(
            "/api/agents",
            json={
                "name": "循环助手",
                "description": "",
                "systemPrompt": "你是循环助手",
                "modelId": agent["model"]["id"],
                "maxRounds": 1,
                "toolKeys": ["builtin_current_time"],
                "skillDirNames": [],
                "mcpServerIds": [],
            },
        )
        assert created.status_code == 201
        session = create_session(client, agent_id=created.json()["id"])
        install_transport(
            monkeypatch,
            MultiResponseTransport(
                [
                    [
                        tool_call_chunk("call_1", "builtin_current_time", "{}"),
                        finish_chunk("tool_calls"),
                        DONE_CHUNK,
                    ]
                ]
            ),
        )

        assert send_message(client, session["id"], "不停查时间").status_code == 201
        wait_done(client, session["id"])

        run = first_run(client)
        assert run["status"] == "partial"
        assert run["finishReason"] == "max_rounds"
        assert run["errorSummary"]  # 提示达到最大轮数
        assert run["firstOutputMs"] is None  # 没有正文输出

    def test_user_stop_cancels_run(self, client: TestClient, monkeypatch) -> None:
        agent = seed_default_agent(client)
        session = create_session(client, agent_id=agent["id"])
        install_transport(
            monkeypatch,
            StreamTransport(chunks=[delta_chunk("慢慢"), DONE_CHUNK], delay=0.05),
        )

        assert send_message(client, session["id"], "写长一点").status_code == 201
        # 等运行真正开始（消息已保存），然后请求停止并等待收尾
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            data = client.get(f"/api/chat/sessions/{session['id']}/messages").json()
            if data["generation"] and data["generation"]["content"]:
                break
            time.sleep(0.02)
        stopped = client.post(f"/api/chat/sessions/{session['id']}/stop").json()
        assert stopped["outcome"] == "cancelled"
        wait_done(client, session["id"])

        run = first_run(client)
        assert run["status"] == "cancelled"
        assert run["finishReason"] == "cancelled"
        # 取消时未完成的工具 / 模型步骤在详情事件里有明确终态
        detail = get_run_detail(client, run["id"])
        assert detail["events"][-1]["eventType"] == "run_end"
        assert detail["events"][-1]["payload"]["status"] == "cancelled"

    def test_browser_disconnect_cancels_and_records(
        self, client: TestClient, monkeypatch
    ) -> None:
        """浏览器断开：沿用上一阶段机制（断连即取消），终态仍写入原 Run。"""
        agent = seed_default_agent(client)
        session = create_session(client, agent_id=agent["id"])
        install_transport(
            monkeypatch,
            StreamTransport(chunks=[delta_chunk("正在"), DONE_CHUNK], delay=0.05),
        )

        assert send_message(client, session["id"], "开始").status_code == 201
        events = collect_events(client, session["id"])  # 读到流结束即断开订阅
        wait_done(client, session["id"])

        run = first_run(client)
        assert run["status"] in ("cancelled", "completed")  # 取消发生在收尾前后均可能
        if run["status"] == "cancelled":
            assert run["finishReason"] == "cancelled"
        # 终态事件与 Run 状态一致（聊天页与运行记录同一批事件）
        run_end = next(e for e in events if e["type"] == "run_end")
        assert run_end["runId"] == run["runUid"]


class TestInterruptedRecovery:
    def test_mark_interrupted_runs(self, db_session) -> None:
        """后端重启：遗留 running 记录标记为失败并注明运行中断。"""
        running = make_run_row(db_session, status=RUN_STATUS_RUNNING, finish_reason=None)
        finished = make_run_row(db_session, status="completed", finish_reason="model_finished")

        from app.db.session import get_session_factory  # noqa: F401  仅为说明；直接传工厂

        factory = db_session.get_bind()
        from sqlalchemy.orm import sessionmaker

        interrupted = mark_interrupted_runs(sessionmaker(bind=factory, expire_on_commit=False))

        assert interrupted == 1
        db_session.expire_all()
        assert running.status == RUN_STATUS_FAILED
        assert running.finish_reason == REASON_INTERRUPTED
        assert "运行中断" in (running.error_summary or "")
        assert running.finished_at is not None
        # 已结束的记录不受影响
        assert finished.status == "completed"

    def test_startup_marks_stale_running_rows(
        self, client: TestClient, db_session
    ) -> None:
        """应用启动（lifespan）也会执行中断恢复：running 不会永久停留。"""
        stale = make_run_row(db_session, status=RUN_STATUS_RUNNING, finish_reason=None)
        # 重新进入 with 块触发一次新的 lifespan 启动
        from app.main import app

        with TestClient(app) as another:
            assert another.get("/api/health").status_code == 200

        db_session.expire_all()
        assert stale.status == RUN_STATUS_FAILED
        assert stale.finish_reason == REASON_INTERRUPTED


# ---- 指标口径 ----


class TestMetrics:
    def _tool_loop_agent(self, client: TestClient) -> dict:
        agent = seed_default_agent(client)
        created = client.post(
            "/api/agents",
            json={
                "name": "计数助手",
                "description": "",
                "systemPrompt": "你是计数助手",
                "modelId": agent["model"]["id"],
                "maxRounds": 25,
                "toolKeys": ["builtin_current_time"],
                "skillDirNames": [],
                "mcpServerIds": [],
            },
        )
        assert created.status_code == 201
        return created.json()

    def test_token_usage_aggregates_with_cache(
        self, client: TestClient, monkeypatch, seeded_tools
    ) -> None:
        """Token 汇总：各次请求用量相加；缓存命中是输入的子集，单独标注。"""
        agent = self._tool_loop_agent(client)
        session = create_session(client, agent_id=agent["id"])
        install_transport(
            monkeypatch,
            MultiResponseTransport(
                [
                    [
                        delta_chunk("第一段"),
                        tool_call_chunk("call_1", "builtin_current_time", "{}"),
                        usage_chunk(100, 40, 140, cached=60),
                        finish_chunk("tool_calls"),
                        DONE_CHUNK,
                    ],
                    [delta_chunk("第二段"), usage_chunk(50, 10, 60, cached=8), DONE_CHUNK],
                ]
            ),
        )

        assert send_message(client, session["id"], "写两段").status_code == 201
        wait_done(client, session["id"])

        run = first_run(client)
        assert run["modelCalls"] == 2
        assert run["toolCalls"] == 1
        assert run["tokenUsage"] == {
            "promptTokens": 150,
            "completionTokens": 50,
            "totalTokens": 200,
            "cachedTokens": 68,  # 缓存命中单独累计，不加进输入
        }
        assert run["tokensIncomplete"] is False

    def test_token_usage_null_when_never_returned(self, client: TestClient, monkeypatch) -> None:
        """上游从未返回用量：显示未知（null），不填 0。"""
        agent = seed_default_agent(client)
        session = create_session(client, agent_id=agent["id"])
        install_transport(monkeypatch, StreamTransport(chunks=[delta_chunk("回复"), DONE_CHUNK]))

        assert send_message(client, session["id"], "你好").status_code == 201
        wait_done(client, session["id"])

        run = first_run(client)
        assert run["tokenUsage"] is None
        assert run["tokensIncomplete"] is False

    def test_token_usage_incomplete_flag(
        self, client: TestClient, monkeypatch, seeded_tools
    ) -> None:
        """部分请求未返回用量：统计不完整。"""
        agent = self._tool_loop_agent(client)
        session = create_session(client, agent_id=agent["id"])
        install_transport(
            monkeypatch,
            MultiResponseTransport(
                [
                    [
                        tool_call_chunk("call_1", "builtin_current_time", "{}"),
                        usage_chunk(10, 5, 15),
                        finish_chunk("tool_calls"),
                        DONE_CHUNK,
                    ],
                    [delta_chunk("二"), DONE_CHUNK],  # 第二次不返回 usage
                ]
            ),
        )

        assert send_message(client, session["id"], "两轮").status_code == 201
        wait_done(client, session["id"])

        run = first_run(client)
        assert run["modelCalls"] == 2
        assert run["tokenUsage"]["promptTokens"] == 10
        assert run["tokensIncomplete"] is True

    def test_rejected_tool_calls_counted_separately(
        self, client: TestClient, monkeypatch, seeded_tools
    ) -> None:
        """校验阶段被拒绝的工具调用单独计数，不计为已执行。"""
        agent = seed_default_agent(client)
        created = client.post(
            "/api/agents",
            json={
                "name": "拒绝助手",
                "description": "",
                "systemPrompt": "你是拒绝助手",
                "modelId": agent["model"]["id"],
                "maxRounds": 25,
                "toolKeys": ["builtin_current_time"],
                "skillDirNames": [],
                "mcpServerIds": [],
            },
        )
        assert created.status_code == 201
        session = create_session(client, agent_id=created.json()["id"])
        install_transport(
            monkeypatch,
            MultiResponseTransport(
                [
                    [
                        tool_call_chunk("call_x", "not_a_real_tool", "{}"),  # 未知工具
                        finish_chunk("tool_calls"),
                        DONE_CHUNK,
                    ],
                    [delta_chunk("已纠正"), DONE_CHUNK],
                ]
            ),
        )

        assert send_message(client, session["id"], "乱调工具").status_code == 201
        wait_done(client, session["id"])

        run = first_run(client)
        assert run["status"] == "completed"
        assert run["toolCalls"] == 0  # 拒绝的调用没有进入执行入口
        assert run["rejectedToolCalls"] == 1
        detail = get_run_detail(client, run["id"])
        tool_end = next(e for e in detail["events"] if e["eventType"] == "tool_call_end")
        assert tool_end["payload"]["status"] == "error"
        assert "拒绝" in tool_end["payload"]["summary"]


# ---- 事件配对、去重与一致性 ----


class TestEventIntegrity:
    def test_duplicate_event_not_inserted_twice(self, db_session) -> None:
        """事件重复投递：run_uid + seq 唯一，不重复插入步骤。"""
        from sqlalchemy.orm import sessionmaker

        factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
        recorder = RunRecorder(
            factory,
            "run-dup-1",
            session_id=None,
            agent_id=None,
            agent_name="A",
            model_id=None,
            model_name="M",
            model_identifier="m",
            user_input="输入",
        )
        recorder.start()
        event = {"type": "run_start", "runId": "run-dup-1", "seq": 1, "agentName": "A"}
        recorder.record_event(event)
        recorder.record_event(event)  # 重复投递
        recorder.record_event(dict(event))  # 再次重放

        rows = db_session.query(AgentRun).filter(AgentRun.run_uid == "run-dup-1").all()
        assert len(rows) == 1  # Run 记录也只有一条
        from app.db.models.run_record import AgentRunEvent

        events = (
            db_session.query(AgentRunEvent)
            .filter(AgentRunEvent.run_uid == "run-dup-1")
            .all()
        )
        assert len(events) == 1
        assert events[0].seq == 1

    def test_chat_and_detail_status_consistent(
        self, client: TestClient, monkeypatch, seeded_tools
    ) -> None:
        """聊天页收到的 run_end 与运行记录终态来自同一批事件，状态一致。"""
        agent = seed_default_agent(client)
        created = client.post(
            "/api/agents",
            json={
                "name": "一致助手",
                "description": "",
                "systemPrompt": "你是一致助手",
                "modelId": agent["model"]["id"],
                "maxRounds": 25,
                "toolKeys": ["builtin_current_time"],
                "skillDirNames": [],
                "mcpServerIds": [],
            },
        )
        assert created.status_code == 201
        session = create_session(client, agent_id=created.json()["id"])
        install_transport(
            monkeypatch,
            MultiResponseTransport(
                [
                    [
                        tool_call_chunk("call_1", "builtin_current_time", "{}"),
                        finish_chunk("tool_calls"),
                        DONE_CHUNK,
                    ],
                    [delta_chunk("完成"), DONE_CHUNK],
                ]
            ),
        )

        assert send_message(client, session["id"], "查时间").status_code == 201
        wait_done(client, session["id"])

        # 迟到订阅者补收的最近终态（聊天页视角）
        events = collect_events(client, session["id"])
        run_end = events[0]
        assert run_end["type"] == "run_end"

        run = first_run(client)
        detail = get_run_detail(client, run["id"])
        stored_end = detail["events"][-1]["payload"]
        # 同一 runUid；状态 / 原因 / 调用次数与指标完全一致
        assert run_end["runId"] == run["runUid"] == stored_end["runId"]
        assert run_end["status"] == stored_end["status"] == "completed"
        assert run_end["reason"] == stored_end["reason"]
        assert run_end["modelCalls"] == stored_end["modelCalls"] == run["modelCalls"]
        assert run_end["toolCalls"] == stored_end["toolCalls"] == run["toolCalls"]


# ---- 敏感信息与详细载荷 ----


class TestRedaction:
    def test_api_key_never_persisted(self, client: TestClient, monkeypatch) -> None:
        """模型回显密钥：Run 记录与事件持久化前统一脱敏。"""
        agent = seed_default_agent(client)
        session = create_session(client, agent_id=agent["id"])
        install_transport(
            monkeypatch,
            StreamTransport(chunks=[delta_chunk(f"密钥是{API_KEY}请保密"), DONE_CHUNK]),
        )

        assert send_message(client, session["id"], "告诉我密钥").status_code == 201
        wait_done(client, session["id"])

        run = first_run(client)
        detail = get_run_detail(client, run["id"])
        for event in detail["events"]:
            assert API_KEY not in json.dumps(event["payload"], ensure_ascii=False)
        # 详细载荷（正文片段）同样脱敏
        run_end = detail["events"][-1]
        if run_end["hasDetail"]:
            payload = client.get(f"/api/runs/{run['id']}/events/{run_end['id']}/detail").json()
            assert API_KEY not in json.dumps(payload, ensure_ascii=False)

    def test_absolute_paths_redacted_in_events(
        self, client: TestClient, monkeypatch, seeded_tools, workspace
    ) -> None:
        """用户设备绝对路径不进入持久化事件：以 …/<文件名> 相对展示。"""
        agent = seed_default_agent(client)
        created = client.post(
            "/api/agents",
            json={
                "name": "路径助手",
                "description": "",
                "systemPrompt": "你是路径助手",
                "modelId": agent["model"]["id"],
                "maxRounds": 25,
                "toolKeys": ["builtin_file"],
                "skillDirNames": [],
                "mcpServerIds": [],
            },
        )
        assert created.status_code == 201
        session = create_session(client, agent_id=created.json()["id"])
        # 模型传入工作区内的绝对路径（Windows 盘符形态）
        absolute = str((workspace / "note.txt").resolve())
        install_transport(
            monkeypatch,
            MultiResponseTransport(
                [
                    [
                        tool_call_chunk(
                            "call_1", "builtin_file", json.dumps({"action": "read", "path": absolute})
                        ),
                        finish_chunk("tool_calls"),
                        DONE_CHUNK,
                    ],
                    [delta_chunk("读到了"), DONE_CHUNK],
                ]
            ),
        )

        assert send_message(client, session["id"], "读文件").status_code == 201
        wait_done(client, session["id"])

        detail = get_run_detail(client, first_run(client)["id"])
        start = next(e for e in detail["events"] if e["eventType"] == "tool_call_start")
        rendered = json.dumps(start["payload"], ensure_ascii=False)
        assert str(workspace).lower() not in rendered.lower()  # 完整路径不出现
        assert "note.txt" in rendered  # 文件名保留可读
        assert "…/note.txt" in rendered

    def test_detail_payload_requires_matching_run(self, client: TestClient, db_session) -> None:
        """详细载荷读取受控：事件必须属于该运行，否则 404。"""
        from app.db.models.run_record import AgentRunEvent, AgentRunEventDetail

        first_row = make_run_row(db_session)
        second_row = make_run_row(db_session)
        event = AgentRunEvent(
            run_uid=first_row.run_uid, seq=1, event_type="tool_call_end",
            payload_json="{}", created_at=utcnow(),
        )
        db_session.add(event)
        db_session.commit()
        db_session.refresh(event)
        db_session.add(
            AgentRunEventDetail(event_id=event.id, payload_json='{"result": {}}', created_at=utcnow())
        )
        db_session.commit()

        # 正常读取
        ok = client.get(f"/api/runs/{first_row.id}/events/{event.id}/detail")
        assert ok.status_code == 200
        assert ok.json()["payload"] == {"result": {}}
        # 跨运行访问 → 404
        cross = client.get(f"/api/runs/{second_row.id}/events/{event.id}/detail")
        assert cross.status_code == 404
        # 不存在的详细载荷 → 404
        empty = AgentRunEvent(
            run_uid=first_row.run_uid, seq=2, event_type="model_request_start",
            payload_json="{}", created_at=utcnow(),
        )
        db_session.add(empty)
        db_session.commit()
        db_session.refresh(empty)
        missing = client.get(f"/api/runs/{first_row.id}/events/{empty.id}/detail")
        assert missing.status_code == 404


# ---- 列表：分页、筛选、快照与查询次数 ----


class TestListApi:
    def test_pagination_order_and_filters(self, client: TestClient, db_session) -> None:
        """倒序分页 + 按状态与 Agent 筛选；只读摘要字段。"""
        for index in range(5):
            make_run_row(
                db_session,
                status="completed" if index % 2 == 0 else "failed",
                agent_id=(None if index < 3 else 7),
                started_at=datetime(2026, 9, 1, 0, index, tzinfo=timezone.utc).replace(tzinfo=None),
            )

        data = list_runs(client)
        assert data["total"] == 5
        assert len(data["items"]) == 5
        # 按开始时间倒序
        started = [item["startedAt"] for item in data["items"]]
        assert started == sorted(started, reverse=True)

        page = list_runs(client, page=1, pageSize=2)
        assert page["total"] == 5
        assert len(page["items"]) == 2
        assert page["items"][0]["startedAt"] >= page["items"][1]["startedAt"]
        page2 = list_runs(client, page=3, pageSize=2)
        assert len(page2["items"]) == 1

        failed = list_runs(client, status="failed")
        assert failed["total"] == 2
        assert all(item["status"] == "failed" for item in failed["items"])

        by_agent = list_runs(client, agentId=7)
        assert by_agent["total"] == 2
        assert all(item["agentId"] == 7 for item in by_agent["items"])

    def test_invalid_status_rejected(self, client: TestClient) -> None:
        assert client.get("/api/runs", params={"status": "bogus"}).status_code == 422

    def test_snapshot_survives_agent_rename(
        self, client: TestClient, monkeypatch
    ) -> None:
        """Agent 改名后历史记录仍显示运行时的名称快照。"""
        agent = seed_default_agent(client)
        session = create_session(client, agent_id=agent["id"])
        install_transport(monkeypatch, StreamTransport(chunks=[delta_chunk("回复"), DONE_CHUNK]))

        assert send_message(client, session["id"], "你好").status_code == 201
        wait_done(client, session["id"])
        assert first_run(client)["agentName"] == "测试助手"

        renamed = client.put(f"/api/agents/{agent['id']}", json={
            "name": "改名助手",
            "description": "",
            "systemPrompt": "你是测试助手",
            "modelId": agent["model"]["id"],
            "maxRounds": 25,
            "toolKeys": [],
            "skillDirNames": [],
            "mcpServerIds": [],
        })
        assert renamed.status_code == 200, renamed.text

        run = first_run(client)
        assert run["agentName"] == "测试助手"  # 快照不变

    def test_query_count_independent_of_rows(self, client: TestClient, db_engine) -> None:
        """相同分页条件下 SELECT 次数不随行数增长（无逐行回查）。"""
        from sqlalchemy.orm import sessionmaker

        factory = sessionmaker(bind=db_engine, expire_on_commit=False)
        seed = factory()
        try:
            for _ in range(3):
                make_run_row(seed)
        finally:
            seed.close()

        selects = {"count": 0}

        def before_execute(conn, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith("SELECT"):
                selects["count"] += 1

        event.listen(db_engine, "before_cursor_execute", before_execute)
        try:
            list_runs(client, pageSize=10)
            baseline = selects["count"]
            assert baseline > 0

            more = factory()
            try:
                for _ in range(10):
                    make_run_row(more)
            finally:
                more.close()

            selects["count"] = 0
            list_runs(client, pageSize=10)
            assert selects["count"] == baseline  # 行数增加 10，查询次数不变
        finally:
            event.remove(db_engine, "before_cursor_execute", before_execute)

    def test_detail_not_loaded_in_list(self, client: TestClient, monkeypatch, seeded_tools):
        """列表接口不返回事件与详细载荷（详情才读）。"""
        agent = seed_default_agent(client)
        created = client.post(
            "/api/agents",
            json={
                "name": "工具助手",
                "description": "",
                "systemPrompt": "你是工具助手",
                "modelId": agent["model"]["id"],
                "maxRounds": 25,
                "toolKeys": ["builtin_current_time"],
                "skillDirNames": [],
                "mcpServerIds": [],
            },
        )
        assert created.status_code == 201
        session = create_session(client, agent_id=created.json()["id"])
        install_transport(
            monkeypatch,
            MultiResponseTransport(
                [
                    [
                        tool_call_chunk("call_1", "builtin_current_time", "{}"),
                        finish_chunk("tool_calls"),
                        DONE_CHUNK,
                    ],
                    [delta_chunk("完成"), DONE_CHUNK],
                ]
            ),
        )
        assert send_message(client, session["id"], "查时间").status_code == 201
        wait_done(client, session["id"])

        item = first_run(client)
        assert "events" not in item
        detail = get_run_detail(client, item["id"])
        assert any(e["eventType"] == "tool_call_end" for e in detail["events"])
