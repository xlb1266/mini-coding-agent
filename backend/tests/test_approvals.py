"""工具执行审批（模块二）单元测试：审批范围、批准/拒绝/超时/取消流程与评测路径跳过。

模型服务复用 test_agent_runtime 的 ScriptedTransport 按请求次序模拟；
审批钩子直接构造 ApprovalHooks，验证 Runtime 的事件、等待与收尾行为。
"""

import asyncio
import json

import httpx
import pytest

from app.services import agent_runtime as agent_runtime_module
from app.services import model_client
from app.services.agent_runtime import (
    APPROVAL_REQUEST,
    APPROVAL_RESULT,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    TOOL_CALL_END,
    RUN_END,
    AgentRuntime,
    ApprovalHooks,
    RuntimeRequest,
)
from app.services.approvals import (
    DECISION_APPROVED,
    DECISION_CANCELLED,
    DECISION_TIMEOUT,
    ApprovalRegistry,
)
from test_agent_runtime import (
    DONE_CHUNK,
    ScriptedTransport,
    box,
    engine,
    factory,
    finish_chunk,
    install,
    runtime,
    seed_agent,
    skills_dir,
    text_chunk,
    tool_call_chunk,
)

# ---- 审批范围 ----


def builtin_entry(key: str) -> agent_runtime_module._ToolEntry:
    return agent_runtime_module._ToolEntry(
        name=key, tool_type="builtin", display_name=key, descriptor={}, ref_key=key
    )


def make_state(
    approval_level: str = "standard", shell_whitelist: str = ""
) -> agent_runtime_module._RunState:
    """构造审批判定用的运行状态：只有档位与白名单是变量。"""
    from app.services.context_compaction import CompactionSettings, compute_capacity

    compaction = CompactionSettings(
        enabled=True, threshold_percent=80, keep_rounds=4, summary_target_tokens=1000
    )
    state = agent_runtime_module._RunState(run_id="t", on_event=None)
    state.config = agent_runtime_module._LoadedConfig(
        agent_id=1,
        agent_name="x",
        system_prompt="",
        model_base_url="",
        model_identifier="m",
        context_length=32768,
        max_output_tokens=512,
        temperature=0.0,
        api_key=None,
        max_rounds=5,
        tool_rows={},
        skills=[],
        mcp_servers=[],
        compaction=compaction,
        capacity=compute_capacity(compaction, 32768, 512),
        approval_level=approval_level,
        shell_whitelist=agent_runtime_module.parse_shell_whitelist(shell_whitelist),
    )
    return state


class TestRequiresApprovalScope:
    def test_standard_level_high_risk_actions(self, runtime: AgentRuntime) -> None:
        state = make_state("standard")
        assert runtime.tool_requires_approval(state, builtin_entry("builtin_shell"), {"command": "ls"})
        assert runtime.tool_requires_approval(
            state, builtin_entry("builtin_file"), {"action": "write", "path": "a.txt"}
        )
        assert runtime.tool_requires_approval(state, builtin_entry("builtin_edit_file"), {"path": "a.txt"})
        # 只读动作照旧免审
        assert not runtime.tool_requires_approval(state, builtin_entry("builtin_shell"), {"action": "list"})

    def test_off_level_never_requires_approval(self, runtime: AgentRuntime) -> None:
        state = make_state("off")
        assert not runtime.tool_requires_approval(state, builtin_entry("builtin_shell"), {"command": "ls"})
        assert not runtime.tool_requires_approval(
            state, builtin_entry("builtin_file"), {"action": "write", "path": "a.txt"}
        )

    def test_strict_level_covers_all_but_time(self, runtime: AgentRuntime) -> None:
        state = make_state("strict")
        assert runtime.tool_requires_approval(state, builtin_entry("builtin_shell"), {"action": "list"})
        assert runtime.tool_requires_approval(
            state, builtin_entry("builtin_file"), {"action": "read", "path": "a.txt"}
        )
        assert runtime.tool_requires_approval(state, builtin_entry("builtin_list_dir"), {"path": "."})
        assert runtime.tool_requires_approval(state, builtin_entry("builtin_grep"), {"pattern": "x"})
        assert not runtime.tool_requires_approval(state, builtin_entry("builtin_current_time"), {})

    def test_shell_whitelist_skips_approval(self, runtime: AgentRuntime) -> None:
        state = make_state("standard", shell_whitelist="uv run pytest\ngit status")
        assert not runtime.tool_requires_approval(
            state, builtin_entry("builtin_shell"), {"command": "uv run pytest -q"}
        )
        assert not runtime.tool_requires_approval(
            state, builtin_entry("builtin_shell"), {"command": "git status --short"}
        )
        # 未命中白名单的命令照旧审批；白名单不影响文件写入审批
        assert runtime.tool_requires_approval(state, builtin_entry("builtin_shell"), {"command": "npm test"})
        assert runtime.tool_requires_approval(
            state, builtin_entry("builtin_file"), {"action": "write", "path": "a.txt"}
        )

    def test_read_only_tools_do_not_require_approval(self, runtime: AgentRuntime) -> None:
        state = make_state("standard")
        assert not runtime.tool_requires_approval(
            state, builtin_entry("builtin_file"), {"action": "read", "path": "a.txt"}
        )
        assert not runtime.tool_requires_approval(state, builtin_entry("builtin_list_dir"), {"path": "."})
        assert not runtime.tool_requires_approval(state, builtin_entry("builtin_grep"), {"pattern": "x"})
        assert not runtime.tool_requires_approval(state, builtin_entry("builtin_current_time"), {})

    def test_mcp_and_skill_do_not_require_approval(self, runtime: AgentRuntime) -> None:
        state = make_state("standard")
        mcp = agent_runtime_module._ToolEntry(
            name="mcp1_x", tool_type="mcp", display_name="x", descriptor={},
            server_id=1, remote_name="x",
        )
        skill = agent_runtime_module._ToolEntry(
            name="load_skill", tool_type="skill", display_name="加载 Skill", descriptor={}
        )
        assert not runtime.tool_requires_approval(state, mcp, {})
        assert not runtime.tool_requires_approval(state, skill, {"skill_id": "demo"})

    def test_finish_stage_never_requires_approval(self, runtime: AgentRuntime) -> None:
        state = make_state("strict")
        entry = builtin_entry("finish_stage")
        assert not runtime.tool_requires_approval(state, entry, {"stageName": "阶段一"})


# ---- 运行内审批流程 ----


def shell_responses() -> list[list[bytes]]:
    """第一轮请求执行 shell（echo hi），第二轮模型给出最终回复。"""
    return [
        [
            tool_call_chunk(0, "call-1", "builtin_shell", json.dumps({"command": "echo hi"})),
            finish_chunk("tool_calls"),
            DONE_CHUNK,
        ],
        [text_chunk("已完成"), finish_chunk("stop"), DONE_CHUNK],
    ]


def events_of_type(events: list[dict], event_type: str) -> list[dict]:
    return [e for e in events if e["type"] == event_type]


class TestApprovalFlow:
    def test_approved_call_executes(
        self, runtime: AgentRuntime, factory, box, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        agent_id = seed_agent(
            factory, box, tool_keys=("builtin_shell",), workspace_dir=str(tmp_path), approval_level="standard"
        )
        requests: list[dict] = []

        async def approve(info: dict) -> str:
            requests.append(info)
            return DECISION_APPROVED

        settle_calls: list[tuple] = []
        hooks = ApprovalHooks(
            timeout_seconds=5.0,
            open_request=approve,
            settle_request=lambda rid, decision: settle_calls.append((rid, decision)),
        )
        transport = ScriptedTransport(shell_responses())
        install(monkeypatch, transport)

        events: list[dict] = []
        result = asyncio.run(
            runtime.run(
                RuntimeRequest(agent_id=agent_id, user_input="run", approval=hooks),
                on_event=events.append,
            )
        )

        assert result.status == STATUS_COMPLETED
        # 审批请求事件带请求标识、工具名与整理后的参数
        request_events = events_of_type(events, APPROVAL_REQUEST)
        assert len(request_events) == 1
        assert request_events[0]["requestId"] == "apr-1"
        assert request_events[0]["callId"] == "call-1"
        assert request_events[0]["toolName"] == "builtin_shell"
        assert request_events[0]["arguments"]["command"] == "echo hi"
        # 决定事件为 approved，工具照常执行
        result_events = events_of_type(events, APPROVAL_RESULT)
        assert result_events[0]["decision"] == DECISION_APPROVED
        end = events_of_type(events, TOOL_CALL_END)[-1]
        assert end["status"] == "success"
        # 超时收尾钩子不应被调用
        assert settle_calls == []

    def test_rejected_call_returns_rejection_to_model(
        self, runtime: AgentRuntime, factory, box, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        agent_id = seed_agent(
            factory, box, tool_keys=("builtin_shell",), workspace_dir=str(tmp_path), approval_level="standard"
        )

        async def reject(info: dict) -> str:
            return "rejected"

        hooks = ApprovalHooks(timeout_seconds=5.0, open_request=reject, settle_request=lambda *_: None)
        transport = ScriptedTransport(shell_responses())
        install(monkeypatch, transport)

        events: list[dict] = []
        result = asyncio.run(
            runtime.run(
                RuntimeRequest(agent_id=agent_id, user_input="run", approval=hooks),
                on_event=events.append,
            )
        )

        assert result.status == STATUS_COMPLETED
        assert events_of_type(events, APPROVAL_RESULT)[0]["decision"] == "rejected"
        # 被拒的调用未执行：工具卡片以失败终态收尾
        end = events_of_type(events, TOOL_CALL_END)[-1]
        assert end["status"] == "error"
        # 拒绝结果回给模型，提示调整方案而不是原样重试
        tool_message = transport.bodies[1]["messages"][-1]
        assert tool_message["role"] == "tool"
        assert "拒绝" in tool_message["content"]
        assert "重试" in tool_message["content"]
        # 计入拒绝次数
        run_end = events_of_type(events, RUN_END)[0]
        assert run_end["rejectedToolCalls"] == 1
        assert run_end["toolCalls"] == 0

    def test_timeout_is_treated_as_rejection(
        self, runtime: AgentRuntime, factory, box, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        agent_id = seed_agent(
            factory, box, tool_keys=("builtin_shell",), workspace_dir=str(tmp_path), approval_level="standard"
        )

        async def hang(info: dict) -> str:
            await asyncio.sleep(10)
            return DECISION_APPROVED

        settle_calls: list[tuple] = []
        hooks = ApprovalHooks(
            timeout_seconds=0.05,
            open_request=hang,
            settle_request=lambda rid, decision: settle_calls.append((rid, decision)),
        )
        transport = ScriptedTransport(shell_responses())
        install(monkeypatch, transport)

        events: list[dict] = []
        result = asyncio.run(
            runtime.run(
                RuntimeRequest(agent_id=agent_id, user_input="run", approval=hooks),
                on_event=events.append,
            )
        )

        assert result.status == STATUS_COMPLETED
        assert events_of_type(events, APPROVAL_RESULT)[0]["decision"] == DECISION_TIMEOUT
        assert settle_calls == [("apr-1", DECISION_TIMEOUT)]
        end = events_of_type(events, TOOL_CALL_END)[-1]
        assert end["status"] == "error"

    def test_cancel_while_waiting_settles_and_cancels_run(
        self, runtime: AgentRuntime, factory, box, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        agent_id = seed_agent(
            factory, box, tool_keys=("builtin_shell",), workspace_dir=str(tmp_path), approval_level="standard"
        )

        async def hang(info: dict) -> str:
            await asyncio.sleep(10)
            return DECISION_APPROVED

        settle_calls: list[tuple] = []
        hooks = ApprovalHooks(
            timeout_seconds=30.0,
            open_request=hang,
            settle_request=lambda rid, decision: settle_calls.append((rid, decision)),
        )
        transport = ScriptedTransport(shell_responses())
        install(monkeypatch, transport)

        events: list[dict] = []
        cancel_event = asyncio.Event()

        async def scenario() -> object:
            task = asyncio.ensure_future(
                runtime.run(
                    RuntimeRequest(agent_id=agent_id, user_input="run", approval=hooks),
                    cancel_event=cancel_event,
                    on_event=events.append,
                )
            )
            await asyncio.sleep(0.2)
            cancel_event.set()
            return await task

        result = asyncio.run(scenario())
        assert isinstance(result, object)
        assert getattr(result, "status") == STATUS_CANCELLED
        assert events_of_type(events, APPROVAL_RESULT)[-1]["decision"] == DECISION_CANCELLED
        assert settle_calls[-1] == ("apr-1", DECISION_CANCELLED)
        end = events_of_type(events, TOOL_CALL_END)[-1]
        assert end["status"] == "cancelled"

    def test_without_hooks_tools_execute_directly(
        self, runtime: AgentRuntime, factory, box, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """评测路径（无审批钩子）：同样的高风险调用自动执行，不产生审批事件。"""
        agent_id = seed_agent(
            factory, box, tool_keys=("builtin_shell",), workspace_dir=str(tmp_path), approval_level="standard"
        )
        transport = ScriptedTransport(shell_responses())
        install(monkeypatch, transport)

        events: list[dict] = []
        result = asyncio.run(
            runtime.run(RuntimeRequest(agent_id=agent_id, user_input="run"), on_event=events.append)
        )

        assert result.status == STATUS_COMPLETED
        assert events_of_type(events, APPROVAL_REQUEST) == []
        assert events_of_type(events, APPROVAL_RESULT) == []
        end = events_of_type(events, TOOL_CALL_END)[-1]
        assert end["status"] == "success"

    def test_read_only_tool_skips_approval_even_with_hooks(
        self, runtime: AgentRuntime, factory, box, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        agent_id = seed_agent(
            factory, box, tool_keys=("builtin_list_dir",), workspace_dir=str(tmp_path), approval_level="standard"
        )
        calls: list[dict] = []

        async def approve(info: dict) -> str:
            calls.append(info)
            return DECISION_APPROVED

        hooks = ApprovalHooks(timeout_seconds=5.0, open_request=approve, settle_request=lambda *_: None)
        transport = ScriptedTransport(
            [
                [
                    tool_call_chunk(0, "call-1", "builtin_list_dir", json.dumps({"path": "."})),
                    finish_chunk("tool_calls"),
                    DONE_CHUNK,
                ],
                [text_chunk("完成"), finish_chunk("stop"), DONE_CHUNK],
            ]
        )
        install(monkeypatch, transport)

        events: list[dict] = []
        result = asyncio.run(
            runtime.run(
                RuntimeRequest(agent_id=agent_id, user_input="run", approval=hooks),
                on_event=events.append,
            )
        )

        assert result.status == STATUS_COMPLETED
        assert calls == []
        assert events_of_type(events, APPROVAL_REQUEST) == []
        assert events_of_type(events, TOOL_CALL_END)[-1]["status"] == "success"


# ---- 注册表 ----


class TestApprovalRegistry:
    def test_create_resolve_and_idempotence(self) -> None:
        registry = ApprovalRegistry()

        async def scenario() -> None:
            approval = registry.create(
                1, "run-1", {"requestId": "apr-1", "toolName": "builtin_shell", "arguments": {"command": "x"}}
            )
            assert registry.get("apr-1") is approval
            assert registry.pending_for_session(1) == [approval]
            assert approval.payload()["requestId"] == "apr-1"
            resolved = registry.resolve("apr-1", True)
            assert resolved is approval
            assert approval.future.result() == "approved"
            # 已处理的请求再次决议返回 None
            assert registry.resolve("apr-1", False) is None

        asyncio.run(scenario())

    def test_settle_marks_timeout_and_is_idempotent(self) -> None:
        registry = ApprovalRegistry()

        async def scenario() -> None:
            approval = registry.create(1, "run-1", {"requestId": "apr-2", "toolName": "builtin_shell"})
            assert registry.settle("apr-2", DECISION_TIMEOUT) is approval
            assert approval.future.result() == DECISION_TIMEOUT
            # 已移除：重复 settle 与未知请求都安全
            assert registry.settle("apr-2", DECISION_CANCELLED) is None
            assert registry.settle("missing", DECISION_TIMEOUT) is None

        asyncio.run(scenario())

    def test_cancel_for_session_and_run(self) -> None:
        registry = ApprovalRegistry()

        async def scenario() -> None:
            a1 = registry.create(1, "run-1", {"requestId": "apr-1"})
            a2 = registry.create(1, "run-2", {"requestId": "apr-2"})
            other = registry.create(2, "run-1", {"requestId": "apr-3"})
            assert registry.cancel_for_run("run-1") == 2
            assert a1.future.result() == DECISION_CANCELLED
            assert other.future.result() == DECISION_CANCELLED
            assert registry.pending_for_session(1) == [a2]
            assert registry.cancel_for_session(1) == 1
            assert registry.pending_for_session(1) == []
            assert registry.cancel_for_run(None) == 0

        asyncio.run(scenario())
