"""Agent Runtime 单元测试：工具循环、Skill 加载、权限复检、取消与清理。

模型服务用 ScriptedTransport 按请求次序模拟（不发真实网络请求）；
MCP 会话用 FakeSession 替换，验证工具映射与资源清理。全部直接调用
AgentRuntime.run()，不经聊天接口。
"""

import asyncio
import json
from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.secret_box import SecretBox
from app.db.base import Base
from app.db.models.agent import Agent, AgentBinding
from app.db.models.mcp_server import McpServer
from app.db.models.model_config import ModelConfig, ModelSecret
from app.db.models.skill import Skill
from app.db.models.tool import Tool
from app.services import agent_runtime, model_client
from app.services.agent_runtime import (
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_ERROR,
    STATUS_ROUNDS_EXHAUSTED,
    AgentRuntime,
    RunLimits,
    RuntimeRequest,
)
from app.services.mcp_client import ERROR_TYPE_TIMEOUT, McpTestError
from app.services.skill_store import INSTRUCTIONS_MAX_LENGTH, SKILL_FILE_NAME
from app.services.tools.registry import builtin_tool_rows

API_KEY = "sk-runtime-test"


# ---- 种子数据 ----


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture()
def engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def factory(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture()
def box(tmp_path):
    return SecretBox(tmp_path / "runtime-secret.key")


@pytest.fixture()
def skills_dir(tmp_path):
    return tmp_path / "skills"


@pytest.fixture()
def runtime(factory, box, skills_dir):
    return AgentRuntime(session_factory=factory, secret_box=box, skills_dir=skills_dir)


def seed_tools(factory) -> None:
    db = factory()
    try:
        now = utcnow()
        for row in builtin_tool_rows():
            db.add(Tool(**row))
        db.commit()
    finally:
        db.close()


def seed_agent(
    factory,
    box,
    *,
    tool_keys: tuple = (),
    skill_dirs: tuple = (),
    mcp_server_ids: tuple = (),
    max_rounds: int = 5,
    system_prompt: str = "你是测试助手",
    context_length: int = 32768,
    max_output_tokens: int = 512,
    auto_compact: bool = True,
    compact_threshold: int = 80,
    compact_keep_rounds: int = 4,
    compact_summary_tokens: int = 1000,
    workspace_dir: str = "",
) -> int:
    seed_tools(factory)
    db = factory()
    try:
        now = utcnow()
        nonce, blob = box.encrypt(API_KEY)
        model = ModelConfig(
            display_name="模型",
            model_identifier="demo-model",
            base_url="https://api.example.com/v1",
            context_length=context_length,
            max_output_tokens=max_output_tokens,
            temperature=0.7,
            created_at=now,
            updated_at=now,
        )
        model.secret = ModelSecret(key_nonce=nonce, key_blob=blob, updated_at=now)
        db.add(model)
        db.flush()
        agent = Agent(
            name="运行助手",
            description="",
            system_prompt=system_prompt,
            prompt_version=1,
            model_id=model.id,
            max_rounds=max_rounds,
            auto_compact=auto_compact,
            compact_threshold=compact_threshold,
            compact_keep_rounds=compact_keep_rounds,
            compact_summary_tokens=compact_summary_tokens,
            workspace_dir=workspace_dir,
            is_default=True,
            created_at=now,
            updated_at=now,
        )
        db.add(agent)
        db.flush()
        for key in tool_keys:
            db.add(AgentBinding(agent_id=agent.id, capability_type="tool", ref_key=key))
        for dir_name in skill_dirs:
            db.add(AgentBinding(agent_id=agent.id, capability_type="skill", ref_key=dir_name))
        for server_id in mcp_server_ids:
            db.add(AgentBinding(agent_id=agent.id, capability_type="mcp", ref_key=str(server_id)))
        db.commit()
        return agent.id
    finally:
        db.close()


def seed_skill(factory, skills_dir, dir_name: str, *, name: str = "示例技能", body: str = "第一步：读取文件。", enabled: bool = True) -> None:
    skill_dir = skills_dir / dir_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / SKILL_FILE_NAME).write_text(
        f"---\nname: {name}\ndescription: 用于测试的技能\n---\n\n{body}\n", encoding="utf-8"
    )
    db = factory()
    try:
        now = utcnow()
        db.add(
            Skill(
                dir_name=dir_name,
                name=name,
                description="用于测试的技能",
                enabled=enabled,
                created_at=now,
                updated_at=now,
            )
        )
        db.commit()
    finally:
        db.close()


# ---- 模拟模型流 ----


def text_chunk(text: str) -> bytes:
    return f"data: {json.dumps({'choices': [{'delta': {'content': text}}]})}\n\n".encode("utf-8")


def tool_call_chunk(index: int, call_id: str, name: str, arguments: str) -> bytes:
    return f"data: {json.dumps({'choices': [{'delta': {'tool_calls': [{'index': index, 'id': call_id, 'type': 'function', 'function': {'name': name, 'arguments': arguments}}]}}]})}\n\n".encode(
        "utf-8"
    )


def finish_chunk(reason: str) -> bytes:
    return f"data: {json.dumps({'choices': [{'delta': {}, 'finish_reason': reason}]})}\n\n".encode("utf-8")


def usage_chunk() -> bytes:
    return f"data: {json.dumps({'choices': [], 'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}})}\n\n".encode(
        "utf-8"
    )


DONE_CHUNK = b"data: [DONE]\n\n"


class ScriptedTransport(httpx.AsyncBaseTransport):
    """按请求次序返回预置的 SSE 响应，并捕获每个请求体。"""

    def __init__(self, responses: list[list[bytes]]) -> None:
        self.responses = list(responses)
        self.bodies: list[dict] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.bodies.append(json.loads(request.content))
        chunks = self.responses.pop(0) if self.responses else [DONE_CHUNK]

        async def body():
            for chunk in chunks:
                yield chunk

        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body())


class HookedTransport(ScriptedTransport):
    """在首个请求到达时触发钩子，用于模拟运行中变化的数据状态。"""

    def __init__(self, responses: list[list[bytes]], on_request=None) -> None:
        super().__init__(responses)
        self._on_request = on_request

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self._on_request is not None:
            hook, self._on_request = self._on_request, None
            hook()
        return await super().handle_async_request(request)


class SlowTransport(httpx.AsyncBaseTransport):
    """每个数据块之间让出一段时间，模拟慢生成，供取消测试使用。"""

    def __init__(self, chunks: list[bytes], delay: float = 0.02) -> None:
        self.chunks = chunks
        self.delay = delay

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        async def body():
            for chunk in self.chunks:
                await asyncio.sleep(self.delay)
                yield chunk

        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body())


def install(monkeypatch: pytest.MonkeyPatch, transport: httpx.AsyncBaseTransport) -> None:
    monkeypatch.setattr(
        model_client,
        "_create_async_client",
        lambda timeout: httpx.AsyncClient(transport=transport, timeout=timeout),
    )


def run_runtime(
    runtime: AgentRuntime,
    agent_id: int,
    *,
    user_input: str = "你好",
    history: list[dict] | None = None,
    limits: RunLimits | None = None,
    cancel_event: asyncio.Event | None = None,
) -> tuple:
    events: list[dict] = []
    request = RuntimeRequest(agent_id=agent_id, user_input=user_input, history=history or [], limits=limits)
    result = asyncio.run(runtime.run(request, cancel_event=cancel_event, on_event=events.append))
    return result, events


# ---- 基础运行 ----


class TestPlainCompletion:
    def test_events_result_and_payload(
        self, runtime: AgentRuntime, factory, box, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent_id = seed_agent(factory, box)
        transport = ScriptedTransport([[text_chunk("你"), text_chunk("好"), DONE_CHUNK]])
        install(monkeypatch, transport)

        result, events = run_runtime(runtime, agent_id)

        assert result.status == STATUS_COMPLETED
        assert result.reason == "model_finished"
        assert result.content == "你好"
        assert [e["type"] for e in events] == [
            "run_start",
            "model_request_start",
            "text_delta",
            "text_delta",
            "model_request_end",
            "run_end",
        ]
        run_ids = {e["runId"] for e in events}
        assert len(run_ids) == 1
        assert [e["seq"] for e in events] == sorted(e["seq"] for e in events)
        assert events[0]["agentName"] == "运行助手"
        # 模型请求事件的调用标识配对
        assert events[1]["callId"] == "mr-1"
        assert events[1]["round"] == 1
        assert events[4]["callId"] == "mr-1"
        # 未返回用量时标记为未知（null），不得填零
        assert events[4]["usage"] is None
        assert isinstance(events[4]["durationMs"], int)
        # run_end 恰好一次，带状态与结束原因
        assert events[5]["status"] == STATUS_COMPLETED
        assert events[5]["reason"] == "model_finished"
        assert events[5]["error"] is None
        # 请求体：system + 用户消息；未绑定工具时不携带 tools
        body = transport.bodies[0]
        assert body["messages"] == [
            {"role": "system", "content": "你是测试助手"},
            {"role": "user", "content": "你好"},
        ]
        assert "tools" not in body

    def test_usage_reported_when_upstream_returns(
        self, runtime: AgentRuntime, factory, box, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent_id = seed_agent(factory, box)
        transport = ScriptedTransport([[text_chunk("好的"), usage_chunk(), DONE_CHUNK]])
        install(monkeypatch, transport)

        result, events = run_runtime(runtime, agent_id)

        assert result.status == STATUS_COMPLETED
        end = next(e for e in events if e["type"] == "model_request_end")
        assert end["usage"] == {"promptTokens": 10, "completionTokens": 5, "totalTokens": 15, "cachedTokens": None}

    def test_empty_final_reply_is_error(
        self, runtime: AgentRuntime, factory, box, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent_id = seed_agent(factory, box)
        transport = ScriptedTransport([[DONE_CHUNK]])
        install(monkeypatch, transport)

        result, events = run_runtime(runtime, agent_id)

        assert result.status == STATUS_ERROR
        assert result.error_message == "模型返回了空回复"
        assert events[-1]["status"] == STATUS_ERROR


# ---- 工具循环 ----


class TestToolLoop:
    def test_builtin_tool_executed_and_context_appended(
        self, runtime: AgentRuntime, factory, box, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent_id = seed_agent(factory, box, tool_keys=("builtin_current_time",))
        transport = ScriptedTransport(
            [
                [
                    text_chunk("先看时间："),
                    tool_call_chunk(0, "call_1", "builtin_current_time", '{"timezone": "Asia/Shanghai"}'),
                    finish_chunk("tool_calls"),
                    DONE_CHUNK,
                ],
                [text_chunk("现在是北京时间"), DONE_CHUNK],
            ]
        )
        install(monkeypatch, transport)

        result, events = run_runtime(runtime, agent_id)

        assert result.status == STATUS_COMPLETED
        # 工具调用前后的正文按顺序保留
        assert result.content == "先看时间：现在是北京时间"

        # 第一轮请求携带工具定义
        tools = transport.bodies[0]["tools"]
        assert [t["function"]["name"] for t in tools] == ["builtin_current_time"]
        params = tools[0]["function"]["parameters"]
        assert params["required"] == ["timezone"] or params.get("properties", {}).get("timezone")

        # 第二轮上下文：assistant(tool_calls) + tool(结果) 按序追加
        second_messages = transport.bodies[1]["messages"]
        assert second_messages[2] == {
            "role": "assistant",
            "content": "先看时间：",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "builtin_current_time", "arguments": '{"timezone": "Asia/Shanghai"}'},
                }
            ],
        }
        tool_message = second_messages[3]
        assert tool_message["role"] == "tool"
        assert tool_message["tool_call_id"] == "call_1"
        assert "datetime" in tool_message["content"]

        # 工具事件：开始/结束按调用标识配对，成功带脱敏摘要
        starts = [e for e in events if e["type"] == "tool_call_start"]
        ends = [e for e in events if e["type"] == "tool_call_end"]
        assert len(starts) == len(ends) == 1
        assert starts[0]["callId"] == "call_1"
        assert starts[0]["toolName"] == "builtin_current_time"
        assert starts[0]["toolType"] == "builtin"
        # 开始事件带整理后的输入参数；内置工具没有 MCP Server 名称
        assert starts[0]["arguments"] == {"timezone": "Asia/Shanghai"}
        assert starts[0]["serverName"] is None
        assert ends[0]["callId"] == "call_1"
        assert ends[0]["status"] == "success"
        assert "T" in ends[0]["summary"]
        # 结束事件带整理后的结果文本（结构化、带缩进、未截断）
        payload = ends[0]["result"]
        assert payload["truncated"] is False
        assert json.loads(payload["text"])["timezone"] == "Asia/Shanghai"
        assert "\n" in payload["text"] and "  " in payload["text"]

    def test_unknown_tool_rejected_but_run_continues(
        self, runtime: AgentRuntime, factory, box, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent_id = seed_agent(factory, box, tool_keys=("builtin_current_time",))
        transport = ScriptedTransport(
            [
                [tool_call_chunk(0, "call_9", "made_up_tool", "{}"), finish_chunk("tool_calls"), DONE_CHUNK],
                [text_chunk("好的，不用工具了"), DONE_CHUNK],
            ]
        )
        install(monkeypatch, transport)

        result, events = run_runtime(runtime, agent_id)

        assert result.status == STATUS_COMPLETED
        tool_message = transport.bodies[1]["messages"][3]
        assert "拒绝" in tool_message["content"]
        end = next(e for e in events if e["type"] == "tool_call_end")
        assert end["status"] == "error"

    def test_invalid_arguments_rejected(
        self, runtime: AgentRuntime, factory, box, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent_id = seed_agent(factory, box, tool_keys=("builtin_current_time",))
        transport = ScriptedTransport(
            [
                [tool_call_chunk(0, "call_1", "builtin_current_time", "{not-json"), finish_chunk("tool_calls"), DONE_CHUNK],
                [text_chunk("收到"), DONE_CHUNK],
            ]
        )
        install(monkeypatch, transport)

        result, events = run_runtime(runtime, agent_id)

        assert result.status == STATUS_COMPLETED
        tool_message = transport.bodies[1]["messages"][3]
        assert "JSON" in tool_message["content"]
        assert any(e["type"] == "tool_call_end" and e["status"] == "error" for e in events)

    def test_disabled_tool_rejected_at_execution(
        self, runtime: AgentRuntime, factory, box, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent_id = seed_agent(factory, box, tool_keys=("builtin_current_time",))

        def disable_tool_mid_run() -> None:
            """运行开始后（注册表已建）、工具执行前停用工具：复检必须拒绝。"""
            db = factory()
            try:
                row = db.query(Tool).filter(Tool.key == "builtin_current_time").first()
                row.enabled = False
                db.commit()
            finally:
                db.close()

        transport = HookedTransport(
            [
                [tool_call_chunk(0, "call_1", "builtin_current_time", "{}"), finish_chunk("tool_calls"), DONE_CHUNK],
                [text_chunk("了解"), DONE_CHUNK],
            ],
            on_request=disable_tool_mid_run,
        )
        install(monkeypatch, transport)

        result, events = run_runtime(runtime, agent_id)

        assert result.status == STATUS_COMPLETED
        tool_message = transport.bodies[1]["messages"][3]
        assert "已停用" in tool_message["content"]

    def test_rounds_exhausted_stops_without_executing(
        self, runtime: AgentRuntime, factory, box, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent_id = seed_agent(factory, box, tool_keys=("builtin_current_time",), max_rounds=1)
        transport = ScriptedTransport(
            [[tool_call_chunk(0, "call_1", "builtin_current_time", "{}"), finish_chunk("tool_calls"), DONE_CHUNK]]
        )
        install(monkeypatch, transport)

        result, events = run_runtime(runtime, agent_id)

        assert result.status == STATUS_ROUNDS_EXHAUSTED
        assert result.reason == "max_rounds"
        assert result.error_type == "rounds_exhausted"
        # 最后一轮的工具请求不再执行：没有工具事件，也没有第二轮请求
        assert not [e for e in events if e["type"].startswith("tool_call")]
        assert len(transport.bodies) == 1

    def test_limits_narrow_but_never_expand(
        self, runtime: AgentRuntime, factory, box, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent_id = seed_agent(factory, box, tool_keys=("builtin_current_time", "builtin_shell"))
        transport = ScriptedTransport([[text_chunk("好的"), DONE_CHUNK]])
        install(monkeypatch, transport)

        run_runtime(runtime, agent_id, limits=RunLimits(tool_keys={"builtin_current_time", "builtin_shell", "builtin_file"}))

        tools = transport.bodies[0]["tools"]
        # 限制只能收窄：未绑定的 builtin_file 不会因为出现在限制里而被授予
        assert [t["function"]["name"] for t in tools] == ["builtin_current_time", "builtin_shell"]

    def test_tool_error_result_returned_to_model(
        self, runtime: AgentRuntime, factory, box, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        agent_id = seed_agent(factory, box, tool_keys=("builtin_file",))
        transport = ScriptedTransport(
            [
                [
                    tool_call_chunk(0, "call_1", "builtin_file", '{"action": "read", "path": "missing.txt"}'),
                    finish_chunk("tool_calls"),
                    DONE_CHUNK,
                ],
                [text_chunk("文件确实不存在"), DONE_CHUNK],
            ]
        )
        install(monkeypatch, transport)
        # 把授权目录指到临时空目录，读取必然失败
        from app.core.config import settings

        monkeypatch.setattr(settings, "tool_workspace_dir", tmp_path / "tool-workspace")

        result, events = run_runtime(runtime, agent_id)

        assert result.status == STATUS_COMPLETED
        tool_message = transport.bodies[1]["messages"][3]
        assert "文件不存在" in tool_message["content"]
        end = next(e for e in events if e["type"] == "tool_call_end")
        assert end["status"] == "error"


# ---- Skill 目录与 load_skill ----


class TestLoadSkill:
    def test_catalog_injected_and_instructions_loaded(
        self, runtime: AgentRuntime, factory, box, skills_dir, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seed_skill(factory, skills_dir, "release-notes", name="发布说明", body="第一步：读取变更。第二步：生成摘要。")
        agent_id = seed_agent(factory, box, skill_dirs=("release-notes",))
        transport = ScriptedTransport(
            [
                [
                    tool_call_chunk(0, "call_1", "load_skill", '{"skill_id": "release-notes"}'),
                    finish_chunk("tool_calls"),
                    DONE_CHUNK,
                ],
                [text_chunk("已按指令处理"), DONE_CHUNK],
            ]
        )
        install(monkeypatch, transport)

        result, events = run_runtime(runtime, agent_id)

        assert result.status == STATUS_COMPLETED
        # 系统提示词包含目录说明（标识/名称/用途），不预置详细指令
        system = transport.bodies[0]["messages"][0]["content"]
        assert "## 可用 Skills" in system
        assert "skill_id: release-notes" in system
        assert "发布说明" in system
        assert "第一步：读取变更" not in system
        assert "先调用 load_skill" in system
        # load_skill 进入工具列表
        tool_names = [t["function"]["name"] for t in transport.bodies[0]["tools"]]
        assert tool_names == ["load_skill"]
        # 工具结果 = 完整指令正文
        tool_message = transport.bodies[1]["messages"][3]
        assert tool_message["content"] == "第一步：读取变更。第二步：生成摘要。"
        # 事件带 skill 类型标记
        start = next(e for e in events if e["type"] == "tool_call_start")
        end = next(e for e in events if e["type"] == "tool_call_end")
        assert start["toolType"] == "skill"
        assert end["status"] == "success"
        # 摘要不携带指令正文
        assert "第一步" not in end["summary"]
        assert "release-notes" in end["summary"] or "发布说明" in end["summary"]

    def test_no_skill_tool_without_skills(
        self, runtime: AgentRuntime, factory, box, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent_id = seed_agent(factory, box)
        transport = ScriptedTransport([[text_chunk("你好"), DONE_CHUNK]])
        install(monkeypatch, transport)

        result, _ = run_runtime(runtime, agent_id)

        assert result.status == STATUS_COMPLETED
        assert "tools" not in transport.bodies[0]
        assert "可用 Skills" not in transport.bodies[0]["messages"][0]["content"]

    def test_unbound_skill_rejected(
        self, runtime: AgentRuntime, factory, box, skills_dir, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seed_skill(factory, skills_dir, "bound-one")
        seed_skill(factory, skills_dir, "other-skill")
        agent_id = seed_agent(factory, box, skill_dirs=("bound-one",))
        transport = ScriptedTransport(
            [
                [tool_call_chunk(0, "call_1", "load_skill", '{"skill_id": "other-skill"}'), finish_chunk("tool_calls"), DONE_CHUNK],
                [text_chunk("明白"), DONE_CHUNK],
            ]
        )
        install(monkeypatch, transport)

        result, _ = run_runtime(runtime, agent_id)

        assert result.status == STATUS_COMPLETED
        tool_message = transport.bodies[1]["messages"][3]
        assert "拒绝" in tool_message["content"] or "未绑定" in tool_message["content"]

    def test_disabled_skill_rejected_at_execution(
        self, runtime: AgentRuntime, factory, box, skills_dir, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seed_skill(factory, skills_dir, "off-skill")
        agent_id = seed_agent(factory, box, skill_dirs=("off-skill",))

        def disable_skill_mid_run() -> None:
            """运行开始后、load_skill 执行前停用：执行前复检必须拒绝。"""
            db = factory()
            try:
                row = db.query(Skill).filter(Skill.dir_name == "off-skill").first()
                row.enabled = False
                db.commit()
            finally:
                db.close()

        transport = HookedTransport(
            [
                [tool_call_chunk(0, "call_1", "load_skill", '{"skill_id": "off-skill"}'), finish_chunk("tool_calls"), DONE_CHUNK],
                [text_chunk("明白"), DONE_CHUNK],
            ],
            on_request=disable_skill_mid_run,
        )
        install(monkeypatch, transport)

        result, _ = run_runtime(runtime, agent_id)

        tool_message = transport.bodies[1]["messages"][3]
        assert "已停用" in tool_message["content"]

    def test_disabled_at_start_removes_load_skill(
        self, runtime: AgentRuntime, factory, box, skills_dir, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 启动前已停用：目录为空 → 不提供 load_skill，请求即被拒绝
        seed_skill(factory, skills_dir, "off-skill", enabled=False)
        agent_id = seed_agent(factory, box, skill_dirs=("off-skill",))
        transport = ScriptedTransport([[text_chunk("你好"), DONE_CHUNK]])
        install(monkeypatch, transport)

        result, _ = run_runtime(runtime, agent_id)

        assert result.status == STATUS_COMPLETED
        assert "tools" not in transport.bodies[0]

    def test_missing_skill_file_rejected(
        self, runtime: AgentRuntime, factory, box, skills_dir, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 绑定与登记存在，但磁盘目录被删
        db = factory()
        try:
            now = utcnow()
            db.add(
                Skill(
                    dir_name="ghost",
                    name="幽灵技能",
                    description="",
                    enabled=True,
                    created_at=now,
                    updated_at=now,
                )
            )
            db.commit()
        finally:
            db.close()
        agent_id = seed_agent(factory, box, skill_dirs=("ghost",))
        transport = ScriptedTransport(
            [
                [tool_call_chunk(0, "call_1", "load_skill", '{"skill_id": "ghost"}'), finish_chunk("tool_calls"), DONE_CHUNK],
                [text_chunk("明白"), DONE_CHUNK],
            ]
        )
        install(monkeypatch, transport)

        result, _ = run_runtime(runtime, agent_id)

        tool_message = transport.bodies[1]["messages"][3]
        assert "缺失" in tool_message["content"]

    def test_oversized_instructions_rejected_not_truncated(
        self, runtime: AgentRuntime, factory, box, skills_dir, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seed_skill(factory, skills_dir, "huge", body="长" * (INSTRUCTIONS_MAX_LENGTH + 1))
        agent_id = seed_agent(factory, box, skill_dirs=("huge",))
        transport = ScriptedTransport(
            [
                [tool_call_chunk(0, "call_1", "load_skill", '{"skill_id": "huge"}'), finish_chunk("tool_calls"), DONE_CHUNK],
                [text_chunk("明白"), DONE_CHUNK],
            ]
        )
        install(monkeypatch, transport)

        result, _ = run_runtime(runtime, agent_id)

        tool_message = transport.bodies[1]["messages"][3]
        assert "过长" in tool_message["content"]
        assert "拒绝加载" in tool_message["content"]
        # 明确报错，不是截断后的内容
        assert len(tool_message["content"]) < 500


# ---- 取消 ----


class TestCancellation:
    def test_cancel_event_interrupts_stream_and_finishes(
        self, runtime: AgentRuntime, factory, box, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent_id = seed_agent(factory, box)
        transport = SlowTransport([text_chunk("慢慢生成")] * 200)
        install(monkeypatch, transport)

        events: list[dict] = []
        cancel_event = asyncio.Event()

        async def scenario():
            request = RuntimeRequest(agent_id=agent_id, user_input="你好")
            task = asyncio.create_task(
                runtime.run(request, cancel_event=cancel_event, on_event=events.append)
            )
            # 等到第一段正文到达后再取消
            for _ in range(200):
                if any(e["type"] == "text_delta" for e in events):
                    break
                await asyncio.sleep(0.01)
            assert any(e["type"] == "text_delta" for e in events)
            cancel_event.set()
            return await task

        result = asyncio.run(scenario())

        assert result.status == STATUS_CANCELLED
        assert result.reason == "cancelled"
        assert result.content.startswith("慢慢生成")
        run_end = events[-1]
        assert run_end["type"] == "run_end"
        assert run_end["status"] == STATUS_CANCELLED
        # 被打断的那次模型请求以 cancelled 结束
        model_ends = [e for e in events if e["type"] == "model_request_end"]
        assert model_ends[-1]["status"] == "cancelled"

    def test_config_error_reports_without_run_start_name(
        self, runtime: AgentRuntime, factory, box
    ) -> None:
        result, events = run_runtime(runtime, 999)

        assert result.status == STATUS_ERROR
        assert events[-1]["type"] == "run_end"
        assert events[-1]["error"]["message"]


# ---- MCP 工具 ----


class FakeMcpSession:
    """替换真实子进程会话：记录调用并返回固定工具与结果。"""

    instances: list = []

    def __init__(self, command: str, args: list[str], env: dict[str, str], timeout: float = 20.0) -> None:
        self.command = command
        self.args = args
        self.env = env
        self.closed = False
        self.calls: list[tuple[str, dict]] = []
        FakeMcpSession.instances.append(self)

    async def start(self) -> list[dict]:
        if self.command == "fail":
            raise McpTestError(ERROR_TYPE_TIMEOUT, "连接超时：20 秒内未收到响应", "提示")
        return [
            {
                "name": "get_weather",
                "description": "查询城市天气",
                "inputSchema": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            }
        ]

    async def call_tool(self, name: str, arguments: dict) -> dict:
        self.calls.append((name, arguments))
        return {"isError": False, "text": f"{arguments.get('city')}：晴，25℃"}

    async def close(self) -> None:
        self.closed = True


@pytest.fixture()
def fake_mcp(monkeypatch: pytest.MonkeyPatch):
    FakeMcpSession.instances = []
    monkeypatch.setattr(agent_runtime, "McpSession", FakeMcpSession)
    yield FakeMcpSession
    FakeMcpSession.instances = []


def seed_mcp_server(factory, server_id: int, *, command: str = "fake-server", enabled: bool = True) -> None:
    db = factory()
    try:
        now = utcnow()
        db.add(
            McpServer(
                id=server_id,
                name=f"服务器{server_id}",
                description="",
                command=command,
                args_json='["--mode", "ok"]',
                enabled=enabled,
                created_at=now,
                updated_at=now,
            )
        )
        db.commit()
    finally:
        db.close()


class TestMcpTools:
    def test_mcp_tool_mapping_execution_and_cleanup(
        self, runtime: AgentRuntime, factory, box, monkeypatch: pytest.MonkeyPatch, fake_mcp
    ) -> None:
        seed_mcp_server(factory, 1)
        agent_id = seed_agent(factory, box, mcp_server_ids=(1,))
        transport = ScriptedTransport(
            [
                [tool_call_chunk(0, "call_1", "mcp1_get_weather", '{"city": "北京"}'), finish_chunk("tool_calls"), DONE_CHUNK],
                [text_chunk("天气已查询"), DONE_CHUNK],
            ]
        )
        install(monkeypatch, transport)

        result, events = run_runtime(runtime, agent_id)

        assert result.status == STATUS_COMPLETED
        # 工具名映射：mcp{server_id}_{工具名}
        tool_names = [t["function"]["name"] for t in transport.bodies[0]["tools"]]
        assert tool_names == ["mcp1_get_weather"]
        schema = transport.bodies[0]["tools"][0]["function"]["parameters"]
        assert schema["required"] == ["city"]
        # 调用转发到远端会话
        session = fake_mcp.instances[0]
        assert session.calls == [("get_weather", {"city": "北京"})]
        # 结果进入上下文
        tool_message = transport.bodies[1]["messages"][3]
        assert "北京" in tool_message["content"]
        # 结束后清理连接
        assert session.closed is True
        start = next(e for e in events if e["type"] == "tool_call_start")
        assert start["toolType"] == "mcp"
        # MCP 工具的展示名是工具名本身，Server 名称单独随事件下发
        assert start["displayName"] == "get_weather"
        assert start["serverName"] == "服务器1"
        assert start["arguments"] == {"city": "北京"}
        end = next(e for e in events if e["type"] == "tool_call_end")
        assert end["result"]["text"] == "北京：晴，25℃"
        assert end["result"]["truncated"] is False

    def test_mcp_cleanup_on_error_path(
        self, runtime: AgentRuntime, factory, box, fake_mcp
    ) -> None:
        seed_mcp_server(factory, 1, command="fail")
        agent_id = seed_agent(factory, box, mcp_server_ids=(1,))

        result, events = run_runtime(runtime, agent_id)

        assert result.status == STATUS_ERROR
        assert result.reason == "mcp_start_failed"
        assert events[-1]["error"]["type"] == ERROR_TYPE_TIMEOUT
        assert fake_mcp.instances[0].closed is True

    def test_disabled_mcp_server_not_provided(
        self, runtime: AgentRuntime, factory, box, fake_mcp, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seed_mcp_server(factory, 1, enabled=False)
        agent_id = seed_agent(factory, box, mcp_server_ids=(1,))
        transport = ScriptedTransport([[text_chunk("你好"), DONE_CHUNK]])
        install(monkeypatch, transport)

        result, _ = run_runtime(runtime, agent_id)

        assert result.status == STATUS_COMPLETED
        assert "tools" not in transport.bodies[0]
        assert not fake_mcp.instances

    def test_history_passed_through_in_order(
        self, runtime: AgentRuntime, factory, box, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent_id = seed_agent(factory, box)
        transport = ScriptedTransport([[text_chunk("好的"), DONE_CHUNK]])
        install(monkeypatch, transport)
        history = [
            {"role": "user", "content": "第一问"},
            {"role": "assistant", "content": "第一答"},
        ]

        run_runtime(runtime, agent_id, user_input="第二问", history=history)

        messages = transport.bodies[0]["messages"]
        assert messages == [
            {"role": "system", "content": "你是测试助手"},
            {"role": "user", "content": "第一问"},
            {"role": "assistant", "content": "第一答"},
            {"role": "user", "content": "第二问"},
        ]


# ---- 工具过程展示事件（阶段九） ----


class TestToolDisplayEvents:
    def test_arguments_clipped_and_secret_redacted(
        self, runtime: AgentRuntime, factory, box, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent_id = seed_agent(factory, box, tool_keys=("builtin_file",))
        content = f"密钥是{API_KEY}，" + "x" * 800
        arguments = json.dumps(
            {"action": "write", "path": "a.txt", "content": content}, ensure_ascii=False
        )
        transport = ScriptedTransport(
            [
                [tool_call_chunk(0, "call_1", "builtin_file", arguments), finish_chunk("tool_calls"), DONE_CHUNK],
                [text_chunk("写好了"), DONE_CHUNK],
            ]
        )
        install(monkeypatch, transport)

        result, events = run_runtime(runtime, agent_id)

        assert result.status == STATUS_COMPLETED
        start = next(e for e in events if e["type"] == "tool_call_start")
        args = start["arguments"]
        # 超长参数值截断并标注原始长度；密钥不进入任何展示事件
        assert "已截断" in args["content"]
        assert len(args["content"]) < 600
        assert "***" in args["content"]
        assert API_KEY not in json.dumps(events, ensure_ascii=False)
        end = next(e for e in events if e["type"] == "tool_call_end")
        assert end["status"] == "success"
        payload = end["result"]
        assert payload["truncated"] is False
        assert json.loads(payload["text"])["action"] == "write"

    def test_failure_result_is_readable_reason(
        self, runtime: AgentRuntime, factory, box, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent_id = seed_agent(factory, box, tool_keys=("builtin_current_time",))
        transport = ScriptedTransport(
            [
                [
                    tool_call_chunk(0, "call_1", "builtin_current_time", '{"timezone": "Mars/Olympus"}'),
                    finish_chunk("tool_calls"),
                    DONE_CHUNK,
                ],
                [text_chunk("换个时区再试"), DONE_CHUNK],
            ]
        )
        install(monkeypatch, transport)

        result, events = run_runtime(runtime, agent_id)

        assert result.status == STATUS_COMPLETED
        end = next(e for e in events if e["type"] == "tool_call_end")
        assert end["status"] == "error"
        text = end["result"]["text"]
        # 失败原因是可读文案与参数提示，不含程序异常堆栈
        assert "无法识别" in text
        assert "参数问题" in text
        assert "Traceback" not in text

    def test_long_mcp_result_clipped_with_marker(
        self, runtime: AgentRuntime, factory, box, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class LongTextSession:
            def __init__(self, command: str, args: list, env: dict, timeout: float = 20.0) -> None:
                self.closed = False

            async def start(self) -> list[dict]:
                return [
                    {
                        "name": "get_weather",
                        "description": "查询天气",
                        "inputSchema": {"type": "object", "properties": {"city": {"type": "string"}}},
                    }
                ]

            async def call_tool(self, name: str, arguments: dict) -> dict:
                return {"isError": False, "text": "雨" * 6000}

            async def close(self) -> None:
                self.closed = True

        monkeypatch.setattr(agent_runtime, "McpSession", LongTextSession)
        seed_mcp_server(factory, 1)
        agent_id = seed_agent(factory, box, mcp_server_ids=(1,))
        transport = ScriptedTransport(
            [
                [tool_call_chunk(0, "call_1", "mcp1_get_weather", '{"city": "北京"}'), finish_chunk("tool_calls"), DONE_CHUNK],
                [text_chunk("雨很大"), DONE_CHUNK],
            ]
        )
        install(monkeypatch, transport)

        result, events = run_runtime(runtime, agent_id)

        assert result.status == STATUS_COMPLETED
        payload = next(e for e in events if e["type"] == "tool_call_end")["result"]
        assert payload["truncated"] is True
        assert payload["totalLength"] == 6000
        assert "已截断" in payload["text"]

    def test_cancel_during_tool_emits_cancelled_end(
        self, runtime: AgentRuntime, factory, box, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class SleepingSession:
            instances: list = []

            def __init__(self, command: str, args: list, env: dict, timeout: float = 20.0) -> None:
                self.closed = False
                SleepingSession.instances.append(self)

            async def start(self) -> list[dict]:
                return [
                    {
                        "name": "get_weather",
                        "description": "查询天气",
                        "inputSchema": {"type": "object", "properties": {"city": {"type": "string"}}},
                    }
                ]

            async def call_tool(self, name: str, arguments: dict) -> dict:
                await asyncio.sleep(2.0)
                return {"isError": False, "text": "太慢了"}

            async def close(self) -> None:
                self.closed = True

        monkeypatch.setattr(agent_runtime, "McpSession", SleepingSession)
        seed_mcp_server(factory, 1)
        agent_id = seed_agent(factory, box, mcp_server_ids=(1,))
        transport = ScriptedTransport(
            [
                [tool_call_chunk(0, "call_1", "mcp1_get_weather", '{"city": "北京"}'), finish_chunk("tool_calls"), DONE_CHUNK],
                [text_chunk("不该到达"), DONE_CHUNK],
            ]
        )
        install(monkeypatch, transport)

        events: list[dict] = []
        cancel_event = asyncio.Event()

        async def scenario():
            request = RuntimeRequest(agent_id=agent_id, user_input="天气")
            task = asyncio.create_task(
                runtime.run(request, cancel_event=cancel_event, on_event=events.append)
            )
            # 等工具开始执行后再取消
            for _ in range(200):
                if any(e["type"] == "tool_call_start" for e in events):
                    break
                await asyncio.sleep(0.01)
            assert any(e["type"] == "tool_call_start" for e in events)
            cancel_event.set()
            return await task

        result = asyncio.run(scenario())

        assert result.status == STATUS_CANCELLED
        # 执行中的卡片得到明确的 cancelled 终态，不停留在“正在执行”
        ends = [e for e in events if e["type"] == "tool_call_end"]
        assert len(ends) == 1
        assert ends[0]["callId"] == "call_1"
        assert ends[0]["status"] == "cancelled"
        assert ends[0]["result"] is None
        assert events[-1]["type"] == "run_end"
        assert events[-1]["status"] == STATUS_CANCELLED
        assert SleepingSession.instances[0].closed is True


# ---- Agent 工作区（模块一）----


class TestAgentWorkspace:
    def test_builtin_tool_runs_in_agent_workspace(
        self, runtime: AgentRuntime, factory, box, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        agent_ws = tmp_path / "agent-code-ws"
        agent_ws.mkdir()
        agent_id = seed_agent(factory, box, tool_keys=("builtin_file",), workspace_dir=str(agent_ws))
        transport = ScriptedTransport(
            [
                [
                    tool_call_chunk(
                        0,
                        "call_1",
                        "builtin_file",
                        '{"action": "write", "path": "note.txt", "content": "hi"}',
                    ),
                    finish_chunk("tool_calls"),
                    DONE_CHUNK,
                ],
                [text_chunk("已写入"), DONE_CHUNK],
            ]
        )
        install(monkeypatch, transport)

        result, events = run_runtime(runtime, agent_id)

        assert result.status == STATUS_COMPLETED
        # 文件落在 Agent 配置的工作区，而不是全局默认目录
        assert (agent_ws / "note.txt").read_text(encoding="utf-8") == "hi"
        from app.core.config import settings

        assert not (settings.tool_workspace_dir / "note.txt").exists()
        tool_end = next(e for e in events if e["type"] == "tool_call_end")
        assert tool_end["status"] == "success"

    def test_builtin_tool_falls_back_to_default_workspace(
        self, runtime: AgentRuntime, factory, box, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        default_ws = tmp_path / "default-ws"
        default_ws.mkdir()
        from app.core.config import settings

        monkeypatch.setattr(settings, "tool_workspace_dir", default_ws)
        agent_id = seed_agent(factory, box, tool_keys=("builtin_file",))  # 未配置工作区
        transport = ScriptedTransport(
            [
                [
                    tool_call_chunk(
                        0,
                        "call_1",
                        "builtin_file",
                        '{"action": "write", "path": "note.txt", "content": "hi"}',
                    ),
                    finish_chunk("tool_calls"),
                    DONE_CHUNK,
                ],
                [text_chunk("已写入"), DONE_CHUNK],
            ]
        )
        install(monkeypatch, transport)

        result, _ = run_runtime(runtime, agent_id)

        assert result.status == STATUS_COMPLETED
        assert (default_ws / "note.txt").read_text(encoding="utf-8") == "hi"
