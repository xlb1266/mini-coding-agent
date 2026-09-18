"""上下文压缩单元测试：容量估算、自动压缩、增量边界、失败回退与取消。

模型服务用 ScriptedTransport 按请求次序模拟；全部直接调用
AgentRuntime.run()。数字断言使用与实现相同的估算口径（估算值），
不与模型接口返回的实际用量混用。
"""

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app.core.secret_box import SecretBox
from app.db.base import Base
from app.services import agent_runtime
from app.services.agent_runtime import (
    CONTEXT_COMPACTION_END,
    CONTEXT_COMPACTION_START,
    CONTEXT_TRIM,
    REASON_CONTEXT_OVERFLOW,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_ERROR,
    AgentRuntime,
    RuntimeRequest,
)
from app.services.context_compaction import (
    CompactionSettings,
    compute_capacity,
    effective_summary_target,
    group_history_turns,
    run_tool_group_ranges,
)
from app.services.token_estimation import estimate_messages_tokens
from tests.test_agent_runtime import (
    DONE_CHUNK,
    ScriptedTransport,
    install,
    seed_agent,
    seed_skill,
    text_chunk,
    tool_call_chunk,
    finish_chunk,
)

# 每条历史消息约 100 个 CJK 字符 → 约 104 token（含固定开销）
TURN_TEXT = "前" * 100


def history_turns(count: int, start_seq: int = 1) -> list[dict]:
    """构造 count 轮历史（user + assistant 各一条，带递增 seq）。"""
    items: list[dict] = []
    for index in range(count):
        seq = start_seq + index * 2
        items.append({"role": "user", "content": f"问题{index}：{TURN_TEXT}", "seq": seq})
        items.append(
            {"role": "assistant", "content": f"回答{index}：{TURN_TEXT}", "seq": seq + 1}
        )
    return items


def run_runtime(
    runtime: AgentRuntime,
    agent_id: int,
    *,
    user_input: str = "继续",
    history: list[dict] | None = None,
    session_summary: str = "",
    summary_boundary: int | None = None,
    persist_summary=None,
    cancel_event: asyncio.Event | None = None,
) -> tuple:
    events: list[dict] = []
    request = RuntimeRequest(
        agent_id=agent_id,
        user_input=user_input,
        history=history or [],
        session_summary=session_summary,
        summary_boundary_seq=summary_boundary,
        persist_summary=persist_summary,
    )
    result = asyncio.run(runtime.run(request, cancel_event=cancel_event, on_event=events.append))
    return result, events


def event_of(events: list[dict], event_type: str) -> dict:
    matched = [e for e in events if e["type"] == event_type]
    assert matched, f"缺少事件 {event_type}：{[e['type'] for e in events]}"
    return matched[0]


# ---- 夹具（与 test_agent_runtime 相同的独立内存库） ----


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
    from sqlalchemy.orm import sessionmaker

    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture()
def box(tmp_path: Path) -> SecretBox:
    return SecretBox(tmp_path / "runtime-secret.key")


@pytest.fixture()
def skills_dir(tmp_path: Path) -> Path:
    return tmp_path / "skills"


@pytest.fixture()
def runtime(factory, box, skills_dir):
    return AgentRuntime(session_factory=factory, secret_box=box, skills_dir=skills_dir)


# ---- 容量与纯逻辑 ----


class TestCapacityLogic:
    def test_available_input_reserves_output_and_margin(self) -> None:
        capacity = compute_capacity(CompactionSettings(True, 80, 4, 1000), 32768, 512)
        # 安全余量 = 32768 // 20 = 1638；可用输入 = 上下文 - 输出预留 - 余量
        assert capacity.safety_margin == 1638
        assert capacity.available_input == 32768 - 512 - 1638
        assert capacity.trigger == capacity.available_input * 80 // 100

    def test_margin_has_floor(self) -> None:
        capacity = compute_capacity(CompactionSettings(True, 80, 4, 1000), 200, 100)
        assert capacity.safety_margin == 64

    def test_summary_target_capped_by_capacity(self) -> None:
        # 小窗口模型：摘要目标被压到可用输入的 1/4，不能占满上下文
        settings = CompactionSettings(True, 80, 4, 1000)
        capacity = compute_capacity(settings, 2000, 1024)
        expected = min(1000, capacity.available_input // 4, 1024)
        assert expected < 1000
        assert effective_summary_target(settings, capacity) == expected

    def test_group_history_turns_and_tool_ranges(self) -> None:
        history = [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
            {"role": "assistant", "content": "b2"},
            {"role": "user", "content": "c"},
        ]
        turns = group_history_turns(history)
        assert [len(t) for t in turns] == [3, 1]

        run_messages = [
            {"role": "user", "content": "u"},
            {"role": "assistant", "content": "a1", "tool_calls": [{"id": "1"}]},
            {"role": "tool", "tool_call_id": "1", "content": "r1"},
            {"role": "tool", "tool_call_id": "1", "content": "r2"},
            {"role": "assistant", "content": "a2", "tool_calls": [{"id": "2"}]},
            {"role": "tool", "tool_call_id": "2", "content": "r3"},
            {"role": "assistant", "content": "final"},
        ]
        assert run_tool_group_ranges(run_messages) == [(1, 4), (4, 6)]

    def test_estimate_messages_includes_tools_payload(self) -> None:
        messages = [{"role": "system", "content": "你是助手"}]
        without_tools = estimate_messages_tokens(messages)
        with_tools = estimate_messages_tokens(
            messages, [{"type": "function", "function": {"name": "t", "parameters": {}}}]
        )
        assert with_tools > without_tools


# ---- 自动压缩主流程 ----


class TestCompaction:
    def test_compaction_replaces_history_with_summary_and_persists(
        self, runtime: AgentRuntime, factory, box, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 容量：2000 - 400(输出预留) - 100(安全余量) = 1500；触发阈值 80% = 1200
        agent_id = seed_agent(
            factory, box, context_length=2000, max_output_tokens=400,
            compact_keep_rounds=2, compact_summary_tokens=1000,
        )
        # 6 轮历史 ≈ 1250 token ≥ 触发阈值；可压缩 4 轮（seq 1..8）、保留最近 2 轮
        history = history_turns(6)
        persisted: list[tuple[str, int]] = []
        transport = ScriptedTransport(
            [
                [text_chunk("会话要点：用户在做压缩测试。"), DONE_CHUNK],
                [text_chunk("好的"), DONE_CHUNK],
            ]
        )
        install(monkeypatch, transport)

        result, events = run_runtime(
            runtime, agent_id, history=history,
            persist_summary=lambda summary, boundary: persisted.append((summary, boundary)),
        )

        assert result.status == STATUS_COMPLETED
        assert result.model_calls == 2  # 摘要 1 次 + 回答 1 次
        # 摘要与边界一致保存：可压缩的 4 轮（seq 1..8）折叠进摘要
        assert persisted == [("会话要点：用户在做压缩测试。", 8)]

        # 第一个请求是摘要请求：包含待压缩原文与生成指令，不带工具定义
        summary_body = transport.bodies[0]
        assert summary_body["messages"][0]["role"] == "system"
        assert "摘要" in summary_body["messages"][0]["content"]
        assert "tools" not in summary_body
        assert summary_body["max_tokens"] <= 400
        contents = [m["content"] for m in summary_body["messages"]]
        assert "问题0：" + TURN_TEXT in contents
        assert "问题3：" + TURN_TEXT in contents

        # 第二个请求：摘要 + 保留的最近 2 轮 + 本次输入；原文不再重复出现
        final_body = transport.bodies[1]
        messages = final_body["messages"]
        assert messages[0] == {"role": "system", "content": "你是测试助手"}
        assert messages[1]["role"] == "system"
        assert "会话背景摘要" in messages[1]["content"]
        assert "会话要点：用户在做压缩测试。" in messages[1]["content"]
        assert [m["content"] for m in messages[2:]] == [
            "问题4：" + TURN_TEXT,
            "回答4：" + TURN_TEXT,
            "问题5：" + TURN_TEXT,
            "回答5：" + TURN_TEXT,
            "继续",
        ]
        assert "问题0：" + TURN_TEXT not in [m["content"] for m in messages]

        # 事件：压缩起止 + 摘要请求带 purpose 标记 + 计入模型调用次数
        start = event_of(events, CONTEXT_COMPACTION_START)
        assert start["compactableMessages"] == 8
        assert start["estimatedTokens"] >= start["thresholdTokens"]
        assert "估算" in start["reason"]
        end = event_of(events, CONTEXT_COMPACTION_END)
        assert end["status"] == "success"
        assert end["processedMessages"] == 8
        assert end["boundarySeq"] == 8
        assert end["modelCalls"] == 1
        assert end["estimatedTokensAfter"] < end["estimatedTokensBefore"]
        purposes = [e for e in events if e["type"] == "model_request_start" and e.get("purpose")]
        assert [e["callId"] for e in purposes] == ["cmp-1-1"]
        run_end = event_of(events, "run_end")
        assert run_end["modelCalls"] == 2

    def test_second_compaction_updates_existing_summary_incrementally(
        self, runtime: AgentRuntime, factory, box, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent_id = seed_agent(
            factory, box, context_length=2000, max_output_tokens=400,
            compact_keep_rounds=2, compact_summary_tokens=1000,
        )
        # 已有摘要覆盖到 seq 8；历史是 seq 9 之后新增的 6 轮 → 可压缩 4 轮（seq 9..16）
        history = history_turns(6, start_seq=9)
        persisted: list[tuple[str, int]] = []
        transport = ScriptedTransport(
            [
                [text_chunk("更新后的摘要"), DONE_CHUNK],
                [text_chunk("完成"), DONE_CHUNK],
            ]
        )
        install(monkeypatch, transport)

        result, events = run_runtime(
            runtime, agent_id, history=history,
            session_summary="旧摘要：此前已经整理过。",
            summary_boundary=8,
            persist_summary=lambda summary, boundary: persisted.append((summary, boundary)),
        )

        assert result.status == STATUS_COMPLETED
        assert persisted == [("更新后的摘要", 16)]
        # 摘要请求以旧摘要为更新基础，只带边界之后新增的待压缩消息
        summary_contents = [m["content"] for m in transport.bodies[0]["messages"]]
        assert any("旧摘要：此前已经整理过。" in c for c in summary_contents)
        assert "问题0：" + TURN_TEXT in summary_contents  # seq 9 的新消息
        # 最终请求保留更新后的摘要与最近 2 轮（seq 17..20）
        final_contents = [m["content"] for m in transport.bodies[1]["messages"]]
        assert any("更新后的摘要" in c for c in final_contents)
        assert "问题4：" + TURN_TEXT in final_contents  # seq 17
        assert "问题0：" + TURN_TEXT not in final_contents

    def test_no_compaction_below_threshold(
        self, runtime: AgentRuntime, factory, box, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent_id = seed_agent(factory, box)
        transport = ScriptedTransport([[text_chunk("好的"), DONE_CHUNK]])
        install(monkeypatch, transport)

        result, events = run_runtime(runtime, agent_id, history=history_turns(2))

        assert result.status == STATUS_COMPLETED
        assert [e["type"] for e in events].count(CONTEXT_COMPACTION_START) == 0
        assert [e["type"] for e in events].count(CONTEXT_TRIM) == 0
        # 普通请求不携带 purpose 标记
        assert all("purpose" not in e for e in events if e["type"] == "model_request_start")

    def test_batching_splits_oversized_history(
        self, runtime: AgentRuntime, factory, box, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 容量 2000：批预算约 1100 token；每轮约 690 token → 一轮一批
        agent_id = seed_agent(
            factory, box, context_length=2000, max_output_tokens=400,
            compact_keep_rounds=1, compact_summary_tokens=1000,
        )
        big = "数" * 340  # 每条约 344 token
        history = []
        for i in range(4):
            history.append({"role": "user", "content": f"问题{i}：{big}", "seq": i * 2 + 1})
            history.append({"role": "assistant", "content": f"回答{i}：{big}", "seq": i * 2 + 2})
        transport = ScriptedTransport(
            [
                [text_chunk("摘要一"), DONE_CHUNK],
                [text_chunk("摘要二"), DONE_CHUNK],
                [text_chunk("摘要三"), DONE_CHUNK],
                [text_chunk("完成"), DONE_CHUNK],
            ]
        )
        install(monkeypatch, transport)

        result, events = run_runtime(runtime, agent_id, history=history)

        assert result.status == STATUS_COMPLETED
        # 3 个摘要请求 + 1 个回答请求
        assert len(transport.bodies) == 4
        end = event_of(events, CONTEXT_COMPACTION_END)
        assert end["batches"] == 3
        assert end["boundarySeq"] == 6
        # 每个摘要请求只带一轮（2 条）待压缩消息，不一次性提交全部历史
        for body in transport.bodies[:3]:
            user_contents = [m["content"] for m in body["messages"] if m["role"] == "user"]
            assert len([c for c in user_contents if c.startswith("问题")]) == 1
        # 后一批以前一批的摘要为基础
        assert any("摘要一" in m["content"] for m in transport.bodies[1]["messages"])

    def test_model_call_limit_stops_at_partial(
        self, runtime: AgentRuntime, factory, box, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 容量 2100 - 400 - 105 = 1595；4 轮 ≈ 2780 token，可压缩 3 轮、每轮一批
        agent_id = seed_agent(
            factory, box, context_length=2100, max_output_tokens=400,
            compact_keep_rounds=1, compact_summary_tokens=1000,
        )
        big = "数" * 340
        history = []
        for i in range(4):
            history.append({"role": "user", "content": f"问题{i}：{big}", "seq": i * 2 + 1})
            history.append({"role": "assistant", "content": f"回答{i}：{big}", "seq": i * 2 + 2})
        # 压缩次数独立限制：达到上限后停止（partial），未压缩的轮保留在上下文
        monkeypatch.setattr(agent_runtime, "MAX_COMPACTION_MODEL_CALLS", 2)
        transport = ScriptedTransport(
            [
                [text_chunk("摘要一"), DONE_CHUNK],
                [text_chunk("摘要二"), DONE_CHUNK],
                [text_chunk("完成"), DONE_CHUNK],
            ]
        )
        install(monkeypatch, transport)

        result, events = run_runtime(runtime, agent_id, history=history)

        assert result.status == STATUS_COMPLETED
        end = event_of(events, CONTEXT_COMPACTION_END)
        assert end["status"] == "partial"
        assert end["batches"] == 2
        assert end["boundarySeq"] == 4
        # 未压缩的第 3 轮（seq 5、6）保留在上下文中
        final_contents = [m["content"] for m in transport.bodies[2]["messages"]]
        assert any("问题2：" in c for c in final_contents)


# ---- 失败与备用处理 ----


class TestCompactionFailures:
    def test_model_error_falls_back_to_trim_without_advancing_boundary(
        self, runtime: AgentRuntime, factory, box, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 容量 1500；8 轮 ≈ 1670 token：摘要失败后备用裁剪从最早轮开始减少
        agent_id = seed_agent(
            factory, box, context_length=2000, max_output_tokens=400, compact_keep_rounds=2
        )
        persisted: list[tuple[str, int]] = []
        transport = _FailingFirstTransport()
        install(monkeypatch, transport)

        result, events = run_runtime(
            runtime, agent_id, history=history_turns(8),
            persist_summary=lambda summary, boundary: persisted.append((summary, boundary)),
        )

        assert result.status == STATUS_COMPLETED
        assert persisted == []  # 边界不推进
        compaction_end = event_of(events, CONTEXT_COMPACTION_END)
        assert compaction_end["status"] == "error"
        # 备用裁剪：从最早历史轮开始减少，只影响本次请求
        trim = event_of(events, CONTEXT_TRIM)
        assert trim["droppedHistoryTurns"] >= 1
        assert trim["estimatedTokensAfter"] <= trim["availableTokens"]
        # 最终请求保留最近对话，没有摘要消息
        final_contents = [m["content"] for m in transport.bodies[-1]["messages"]]
        assert "问题7：" + TURN_TEXT in final_contents
        assert "问题0：" + TURN_TEXT not in final_contents

    def test_empty_summary_falls_back_to_trim(
        self, runtime: AgentRuntime, factory, box, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent_id = seed_agent(
            factory, box, context_length=2000, max_output_tokens=400, compact_keep_rounds=2
        )
        persisted: list[tuple[str, int]] = []
        transport = ScriptedTransport(
            [
                [DONE_CHUNK],  # 摘要为空 → 失败
                [text_chunk("好的"), DONE_CHUNK],
            ]
        )
        install(monkeypatch, transport)

        result, events = run_runtime(
            runtime, agent_id, history=history_turns(8),
            persist_summary=lambda summary, boundary: persisted.append((summary, boundary)),
        )

        assert result.status == STATUS_COMPLETED
        assert persisted == []
        end = event_of(events, CONTEXT_COMPACTION_END)
        assert end["status"] == "error"
        assert "空内容" in end["error"]["message"]
        assert event_of(events, CONTEXT_TRIM)["droppedHistoryTurns"] >= 1

    def test_persist_failure_keeps_previous_summary(
        self, runtime: AgentRuntime, factory, box, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent_id = seed_agent(
            factory, box, context_length=2000, max_output_tokens=400, compact_keep_rounds=2
        )

        def failing_persist(summary: str, boundary: int) -> None:
            raise RuntimeError("数据库不可用")

        transport = ScriptedTransport(
            [
                [text_chunk("新摘要一"), DONE_CHUNK],
                [text_chunk("新摘要二"), DONE_CHUNK],
                [text_chunk("好的"), DONE_CHUNK],
            ]
        )
        install(monkeypatch, transport)

        result, events = run_runtime(
            runtime, agent_id, history=history_turns(10),
            session_summary="旧摘要",
            summary_boundary=4,
            persist_summary=failing_persist,
        )

        assert result.status == STATUS_COMPLETED
        end = event_of(events, CONTEXT_COMPACTION_END)
        assert end["status"] == "error"
        assert "保存失败" in end["error"]["message"]
        # 保留上一份有效摘要：请求里仍是旧摘要，新摘要不进入上下文
        final_contents = [m["content"] for m in transport.bodies[-1]["messages"]]
        assert any("旧摘要" in c for c in final_contents)
        assert not any("新摘要" in c for c in final_contents)
        assert event_of(events, CONTEXT_TRIM)["droppedHistoryTurns"] >= 1

    def test_auto_compact_disabled_over_capacity_errors(
        self, runtime: AgentRuntime, factory, box, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 容量 1000 - 200 - 64 = 736；6 轮 ≈ 1250 token 超容量且已关闭自动压缩
        agent_id = seed_agent(
            factory, box, context_length=1000, max_output_tokens=200, auto_compact=False
        )
        transport = ScriptedTransport([[text_chunk("不应该被请求"), DONE_CHUNK]])
        install(monkeypatch, transport)

        result, events = run_runtime(runtime, agent_id, history=history_turns(6))

        assert result.status == STATUS_ERROR
        assert result.reason == REASON_CONTEXT_OVERFLOW
        assert "自动压缩" in result.error_hint
        # 不发任何模型请求、不裁剪、不静默丢弃历史
        assert transport.bodies == []
        assert [e["type"] for e in events].count(CONTEXT_TRIM) == 0
        run_end = event_of(events, "run_end")
        assert run_end["modelCalls"] == 0

    def test_must_keep_overflow_ends_run(
        self, runtime: AgentRuntime, factory, box, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 系统提示词本身就超过可用输入容量（1000 - 800 - 64 = 136）
        agent_id = seed_agent(
            factory, box,
            context_length=1000, max_output_tokens=800,
            system_prompt="长" * 200,
        )
        transport = ScriptedTransport([[text_chunk("不应该被请求"), DONE_CHUNK]])
        install(monkeypatch, transport)

        result, events = run_runtime(runtime, agent_id)

        assert result.status == STATUS_ERROR
        assert result.reason == REASON_CONTEXT_OVERFLOW
        assert "系统提示词" in result.error_message
        assert transport.bodies == []

    def test_midrun_tool_result_overflow_trims_complete_groups(
        self, runtime: AgentRuntime, factory, box, skills_dir, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 工具结果（巨型 Skill 指令）把估算推过容量：第二轮请求前整组裁掉
        agent_id = seed_agent(
            factory, box,
            context_length=2000, max_output_tokens=400,
            skill_dirs=("huge-skill",),
        )
        seed_skill(
            factory, skills_dir, "huge-skill", name="巨型技能",
            body="步骤" * 2500,  # 约 5000 token 的 Skill 指令
        )
        transport = ScriptedTransport(
            [
                [
                    tool_call_chunk(0, "call-1", "load_skill", json.dumps({"skill_id": "huge-skill"})),
                    finish_chunk("tool_calls"),
                    DONE_CHUNK,
                ],
                [text_chunk("已按指令处理"), DONE_CHUNK],
            ]
        )
        install(monkeypatch, transport)

        result, events = run_runtime(runtime, agent_id, user_input="请按技能执行")

        assert result.status == STATUS_COMPLETED
        trim = event_of(events, CONTEXT_TRIM)
        assert trim["droppedToolGroups"] == 1
        assert trim["droppedMessages"] == 2
        assert trim["estimatedTokensAfter"] <= trim["availableTokens"]
        # 第二轮请求回到 [system, user]：没有孤立的工具调用或结果
        round2_messages = transport.bodies[1]["messages"]
        assert [m["role"] for m in round2_messages] == ["system", "user"]
        assert all(m.get("role") != "tool" for m in round2_messages)

    def test_cancel_during_compaction_keeps_previous_state(
        self, runtime: AgentRuntime, factory, box, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent_id = seed_agent(
            factory, box, context_length=2000, max_output_tokens=400, compact_keep_rounds=2
        )
        persisted: list[tuple[str, int]] = []
        transport = _SlowSummaryTransport()
        install(monkeypatch, transport)

        cancel_event = asyncio.Event()
        events: list[dict] = []
        request = RuntimeRequest(
            agent_id=agent_id,
            user_input="继续",
            history=history_turns(6),
            persist_summary=lambda summary, boundary: persisted.append((summary, boundary)),
        )

        async def scenario():
            task = asyncio.create_task(
                runtime.run(request, cancel_event=cancel_event, on_event=events.append)
            )
            await asyncio.sleep(0.05)
            cancel_event.set()
            return await task

        result = asyncio.run(scenario())

        assert result.status == STATUS_CANCELLED
        assert persisted == []  # 取消时不持久化
        end = event_of(events, CONTEXT_COMPACTION_END)
        assert end["status"] == "cancelled"
        purposes = [e for e in events if e["type"] == "model_request_end" and e.get("purpose")]
        assert purposes[-1]["status"] == "cancelled"


class _FailingFirstTransport(httpx.AsyncBaseTransport):
    """第一个请求（摘要）返回 500，之后的请求返回正常回复。"""

    def __init__(self) -> None:
        self.bodies: list[dict] = []
        self._responses: list[list[bytes]] = [[text_chunk("好的"), DONE_CHUNK]]

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.bodies.append(json.loads(request.content))
        if len(self.bodies) == 1:
            return httpx.Response(500, json={"error": {"message": "上游服务出错"}})

        chunks = self._responses.pop(0)

        async def body():
            for chunk in chunks:
                yield chunk

        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body())


class _SlowSummaryTransport(httpx.AsyncBaseTransport):
    """摘要请求缓慢产出，供取消测试中断。"""

    def __init__(self) -> None:
        self.bodies: list[dict] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.bodies.append(json.loads(request.content))

        async def body():
            for _ in range(20):
                await asyncio.sleep(0.02)
                yield text_chunk("摘要")

        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body())
