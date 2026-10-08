"""阶段化开发 v2（项目级多会话）测试。

覆盖：
- submit_stage_plan 工具：阶段确认模式注入注册表与系统提示词（意图路由）；
  调用后以 plan_submitted 终态结束并携带阶段计划；阶段数不合法按失败返回；
  高度自主模式不提供该工具；
- 运行级工作区覆盖：RuntimeRequest.workspace_dir 优先于 Agent 配置；
- finish_stage 增强：读取工作区交接文档内容放进结果卡片；
- 项目全生命周期（API 级）：plan_submitted 建项目（会话挂 role=plan、
  工作区钉住）→ start 开阶段一会话 → 阶段完成推进 → next 进入阶段二 →
  全部完成转 awaiting_summary → summary 汇总完成 → completed；
  以及状态不允许时的 409 分支与 404。

模型服务复用 test_agent_runtime 的 ScriptedTransport（Runtime 级）与
test_chat_api 的 StreamTransport / 接口 helper（API 级），不发真实网络请求。
"""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.db.models.agent import Agent
from app.db.models.chat import ChatSession
from app.db.models.model_config import ModelConfig
from app.db.models.stage_project import StageProject
from app.services.agent_runtime import (
    STATUS_PLAN_SUBMITTED,
    STATUS_STAGE_COMPLETE,
    AgentRuntime,
    RuntimeRequest,
)
from app.services.chat_generation import generation_manager

from test_agent_runtime import (
    DONE_CHUNK,
    ScriptedTransport,
    box,
    engine,  # noqa: F401  # factory 与 engine 必须来自同一模块，否则是两个内存库
    factory,
    finish_chunk,
    install,
    runtime,
    seed_agent,
    skills_dir,
    text_chunk,
    tool_call_chunk,
)
from test_chat_api import (
    create_agent,
    create_model,
    create_session,
    get_messages,
    install_stream,
    send_message,
    wait_generation_done,
)


def events_of_type(events: list[dict], event_type: str) -> list[dict]:
    return [e for e in events if e["type"] == event_type]


def sse(payload: dict) -> bytes:
    return f"data: {json.dumps(payload)}\n\n".encode("utf-8")


def sse_tool_call(index: int, call_id: str, name: str, arguments: str) -> bytes:
    return sse(
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": index,
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


SSE_DONE = b"data: [DONE]\n\n"


class SequenceTransport(httpx.AsyncBaseTransport):
    """API 级多轮生成的模拟传输：每个模型请求按序消费一份 SSE 块列表。"""

    def __init__(self, responses: list[list[bytes]]) -> None:
        self.responses = list(responses)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        chunks = self.responses.pop(0) if self.responses else [SSE_DONE]

        async def body():
            for chunk in chunks:
                yield chunk

        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body())


def plan_arguments(stages: list[dict[str, str]] | None = None) -> str:
    return json.dumps(
        {
            "title": "待办清单应用",
            "stages": stages
            if stages is not None
            else [
                {"name": "阶段一：项目骨架", "goal": "搭好目录与基础配置"},
                {"name": "阶段二：核心功能", "goal": "完成增删改查"},
            ],
        },
        ensure_ascii=False,
    )


# ---------------------------------------------------------------------------
# Runtime 级：submit_stage_plan 工具与意图路由提示词
# ---------------------------------------------------------------------------


class TestSubmitStagePlan:
    def test_plan_tool_registered_and_prompt(
        self, runtime: AgentRuntime, factory, box, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        agent_id = seed_agent(
            factory, box, workspace_dir=str(tmp_path), autonomy_mode="stage_confirm"
        )
        transport = ScriptedTransport([[text_chunk("收到"), finish_chunk("stop"), DONE_CHUNK]])
        install(monkeypatch, transport)

        asyncio.run(
            runtime.run(
                RuntimeRequest(agent_id=agent_id, user_input="从零做一个待办应用"),
                on_event=lambda event: None,
            )
        )
        body = transport.bodies[0]
        # 系统提示词附加意图路由；工具定义同时含计划与收尾工具
        assert "意图路由" in body["messages"][0]["content"]
        tool_names = [t["function"]["name"] for t in body["tools"]]
        assert "submit_stage_plan" in tool_names
        assert "finish_stage" in tool_names

    def test_submit_plan_ends_run_with_plan_payload(
        self, runtime: AgentRuntime, factory, box, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        agent_id = seed_agent(factory, box, workspace_dir=str(tmp_path), autonomy_mode="stage_confirm")
        transport = ScriptedTransport(
            [
                [
                    tool_call_chunk(0, "call-1", "submit_stage_plan", plan_arguments()),
                    finish_chunk("tool_calls"),
                    DONE_CHUNK,
                ]
            ]
        )
        install(monkeypatch, transport)

        events: list[dict] = []
        result = asyncio.run(
            runtime.run(
                RuntimeRequest(agent_id=agent_id, user_input="从零做一个待办应用"),
                on_event=events.append,
            )
        )
        assert result.status == STATUS_PLAN_SUBMITTED
        end = events_of_type(events, "run_end")[0]
        assert end["status"] == STATUS_PLAN_SUBMITTED
        assert end["plan"]["stages"] == [
            {"name": "阶段一：项目骨架", "goal": "搭好目录与基础配置"},
            {"name": "阶段二：核心功能", "goal": "完成增删改查"},
        ]
        tool_end = events_of_type(events, "tool_call_end")[-1]
        assert tool_end["status"] == "success"

    def test_submit_plan_invalid_stages_fails_without_ending(
        self, runtime: AgentRuntime, factory, box, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        agent_id = seed_agent(factory, box, workspace_dir=str(tmp_path), autonomy_mode="stage_confirm")
        transport = ScriptedTransport(
            [
                [
                    tool_call_chunk(
                        0,
                        "call-1",
                        "submit_stage_plan",
                        plan_arguments(stages=[{"name": "只有一个阶段", "goal": "x"}]),
                    ),
                    finish_chunk("tool_calls"),
                    DONE_CHUNK,
                ],
                [text_chunk("已修正"), finish_chunk("stop"), DONE_CHUNK],
            ]
        )
        install(monkeypatch, transport)

        events: list[dict] = []
        result = asyncio.run(
            runtime.run(
                RuntimeRequest(agent_id=agent_id, user_input="从零做一个待办应用"),
                on_event=events.append,
            )
        )
        # 阶段数不合法按失败处理，运行不结束，模型得到修正提示
        assert result.status == "completed"
        tool_end = events_of_type(events, "tool_call_end")[-1]
        assert tool_end["status"] == "error"

    def test_autonomous_mode_has_no_plan_tool(
        self, runtime: AgentRuntime, factory, box, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        agent_id = seed_agent(factory, box, workspace_dir=str(tmp_path), autonomy_mode="autonomous")
        transport = ScriptedTransport([[text_chunk("收到"), finish_chunk("stop"), DONE_CHUNK]])
        install(monkeypatch, transport)

        asyncio.run(
            runtime.run(
                RuntimeRequest(agent_id=agent_id, user_input="从零做一个待办应用"),
                on_event=lambda event: None,
            )
        )
        body = transport.bodies[0]
        assert "意图路由" not in body["messages"][0]["content"]
        assert "tools" not in body  # 没有绑定任何工具，也没有计划与收尾工具


# ---------------------------------------------------------------------------
# Runtime 级：工作区覆盖优先级与 finish_stage 读交接文档
# ---------------------------------------------------------------------------


class TestWorkspaceOverrideAndHandoff:
    def test_request_workspace_beats_agent_workspace(
        self, runtime: AgentRuntime, factory, box, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        agent_workspace = tmp_path / "agent-ws"
        agent_workspace.mkdir()
        run_workspace = tmp_path / "project-ws"
        run_workspace.mkdir()
        agent_id = seed_agent(
            factory, box, tool_keys=("builtin_file",), workspace_dir=str(agent_workspace)
        )
        transport = ScriptedTransport(
            [
                [
                    tool_call_chunk(
                        0,
                        "call-1",
                        "builtin_file",
                        json.dumps({"action": "write", "path": "ov.txt", "content": "hello"}),
                    ),
                    finish_chunk("tool_calls"),
                    DONE_CHUNK,
                ],
                [text_chunk("写好了"), finish_chunk("stop"), DONE_CHUNK],
            ]
        )
        install(monkeypatch, transport)

        asyncio.run(
            runtime.run(
                RuntimeRequest(
                    agent_id=agent_id,
                    user_input="写一个文件",
                    workspace_dir=str(run_workspace),
                ),
                on_event=lambda event: None,
            )
        )
        # 文件落在运行级覆盖目录，而不是 Agent 配置的工作区
        assert (run_workspace / "ov.txt").is_file()
        assert not (agent_workspace / "ov.txt").exists()

    def test_finish_stage_includes_handoff_content(
        self, runtime: AgentRuntime, factory, box, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "handoff-阶段一.md").write_text(
            "## 背景\n这是交接文档内容", encoding="utf-8"
        )
        agent_id = seed_agent(factory, box, workspace_dir=str(workspace), autonomy_mode="stage_confirm")
        transport = ScriptedTransport(
            [
                [
                    tool_call_chunk(
                        0,
                        "call-1",
                        "finish_stage",
                        json.dumps(
                            {
                                "stageName": "阶段一：项目骨架",
                                "summary": "搭好骨架",
                                "handoffPath": "handoff-阶段一.md",
                                "nextStep": "实现核心页面",
                            },
                            ensure_ascii=False,
                        ),
                    ),
                    finish_chunk("tool_calls"),
                    DONE_CHUNK,
                ]
            ]
        )
        install(monkeypatch, transport)

        events: list[dict] = []
        result = asyncio.run(
            runtime.run(
                RuntimeRequest(agent_id=agent_id, user_input="开始阶段一"),
                on_event=events.append,
            )
        )
        assert result.status == STATUS_STAGE_COMPLETE
        tool_end = events_of_type(events, "tool_call_end")[-1]
        assert tool_end["status"] == "success"
        # 交接文档内容进入结果卡片（展示文本），供用户直接查看
        result_text = tool_end["result"]["text"]
        assert "这是交接文档内容" in result_text
        assert "handoff-阶段一.md" in result_text


# ---------------------------------------------------------------------------
# API 级：项目全生命周期状态机
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clean_generation_state():
    """清空跨测试的生成任务与终态缓存；会话 id 在每个测试的独立库里都从 1 开始。"""
    generation_manager._runs.clear()
    generation_manager._last_outcomes.clear()
    yield
    generation_manager._runs.clear()
    generation_manager._last_outcomes.clear()


class TestProjectLifecycle:
    def _stage_finish_chunks(self, stage_name: str, handoff_path: str) -> list[bytes]:
        return [
            sse_tool_call(
                0,
                "call-finish",
                "finish_stage",
                json.dumps(
                    {
                        "stageName": stage_name,
                        "summary": "完成并自检通过",
                        "handoffPath": handoff_path,
                        "nextStep": "继续下一阶段",
                    },
                    ensure_ascii=False,
                ),
            ),
            b"data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}).encode() + b"\n\n",
            SSE_DONE,
        ]

    def test_full_lifecycle(self, client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        workspace = tmp_path / "proj"
        workspace.mkdir()
        model = create_model(client)
        agent = create_agent(
            client, model["id"], autonomyMode="stage_confirm", workspaceDir=str(workspace)
        )
        plan_session = create_session(client, agent_id=agent["id"])

        # ---- 规划：submit_stage_plan → 项目创建，会话挂 role=plan ----
        install_stream(
            monkeypatch,
            SequenceTransport(
                [
                    [
                        sse_tool_call(0, "call-1", "submit_stage_plan", plan_arguments()),
                        b"data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}).encode() + b"\n\n",
                        SSE_DONE,
                    ]
                ]
            ),
        )
        sent = send_message(client, plan_session["id"], "从零做一个待办清单应用")
        assert sent.status_code == 201, sent.text
        wait_generation_done(client, plan_session["id"])

        opened = get_messages(client, plan_session["id"])
        assert opened["session"]["projectId"] == 1
        assert opened["session"]["projectRole"] == "plan"

        project = client.get("/api/chat/projects/1")
        assert project.status_code == 200, project.text
        body = project.json()
        assert body["title"] == "待办清单应用"
        assert body["status"] == "awaiting_start"
        assert body["currentStage"] == 0
        assert [stage["name"] for stage in body["stages"]] == ["阶段一：项目骨架", "阶段二：核心功能"]
        assert body["planSessionId"] == plan_session["id"]
        # 工作区钉住：取自 Agent 配置
        assert Path(body["workspaceDir"]) == workspace

        # ---- 状态不允许的分支 ----
        assert client.post("/api/chat/projects/999/start").status_code == 404
        assert client.post("/api/chat/projects/1/next").status_code == 409  # 未开始
        assert client.post("/api/chat/projects/1/summary").status_code == 409  # 阶段未完成

        # ---- 开始阶段一：自动新建会话并发出首条消息 ----
        install_stream(
            monkeypatch,
            SequenceTransport([self._stage_finish_chunks("阶段一：项目骨架", "handoff-阶段一.md")]),
        )
        started = client.post("/api/chat/projects/1/start")
        assert started.status_code == 201, started.text
        op = started.json()
        stage1 = op["session"]
        assert stage1["projectRole"] == "stage"
        assert stage1["stageIndex"] == 1
        assert stage1["agentId"] == agent["id"]
        assert op["project"]["status"] == "in_progress"
        assert op["project"]["currentStage"] == 1
        # 自动首条消息已保存（用户消息）
        first_messages = get_messages(client, stage1["id"])
        assert first_messages["messages"][0]["role"] == "user"
        assert "阶段 1" in first_messages["messages"][0]["content"]
        wait_generation_done(client, stage1["id"])

        # 阶段一完成后：阶段一 done，下一阶段 2；重复 start / 未完成 next 均被拒
        body = client.get("/api/chat/projects/1").json()
        assert body["stages"][0]["status"] == "done"
        assert body["currentStage"] == 2
        assert body["stageSessions"]["1"] == stage1["id"]
        assert client.post("/api/chat/projects/1/start").status_code == 409

        # ---- 进入阶段二：自动新建会话，首条消息带上交接文档路径 ----
        install_stream(
            monkeypatch,
            SequenceTransport([self._stage_finish_chunks("阶段二：核心功能", "handoff-阶段二.md")]),
        )
        nexted = client.post("/api/chat/projects/1/next")
        assert nexted.status_code == 201, nexted.text
        op = nexted.json()
        stage2 = op["session"]
        assert stage2["stageIndex"] == 2
        stage2_messages = get_messages(client, stage2["id"])
        first_user = stage2_messages["messages"][0]["content"]
        assert "阶段 2" in first_user
        assert "handoff-阶段一.md" in first_user  # 上一阶段交接文档路径
        wait_generation_done(client, stage2["id"])

        # 全部阶段完成：转 awaiting_summary
        body = client.get("/api/chat/projects/1").json()
        assert body["status"] == "awaiting_summary"
        assert all(stage["status"] == "done" for stage in body["stages"])
        # 阶段一会话的等待验收状态已清除
        stage1_after = get_messages(client, stage1["id"])
        assert stage1_after["session"]["awaitingAcceptance"] is False

        # ---- 生成项目汇总：独立汇总会话，完成后项目 completed ----
        install_stream(
            monkeypatch,
            SequenceTransport(
                [[sse({"choices": [{"delta": {"content": "项目已完成，全部自检通过"}}]}), SSE_DONE]]
            ),
        )
        summarized = client.post("/api/chat/projects/1/summary")
        assert summarized.status_code == 201, summarized.text
        op = summarized.json()
        summary_session = op["session"]
        assert summary_session["projectRole"] == "summary"
        assert op["project"]["status"] == "summarizing"
        summary_messages = get_messages(client, summary_session["id"])
        assert "整体完成情况汇报" in summary_messages["messages"][0]["content"]
        wait_generation_done(client, summary_session["id"])

        body = client.get("/api/chat/projects/1").json()
        assert body["status"] == "completed"
        assert body["summarySessionId"] == summary_session["id"]

    def test_summary_failure_reverts_to_awaiting(
        self, client: TestClient, db_engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """汇总会话运行失败：项目回退 awaiting_summary，允许重试。"""
        model = create_model(client)
        agent = create_agent(client, model["id"], autonomyMode="stage_confirm")
        plan_session = create_session(client, agent_id=agent["id"])

        install_stream(
            monkeypatch,
            SequenceTransport(
                [
                    [
                        sse_tool_call(0, "call-1", "submit_stage_plan", plan_arguments()),
                        b"data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}).encode() + b"\n\n",
                        SSE_DONE,
                    ]
                ]
            ),
        )
        assert send_message(client, plan_session["id"], "做一个工具").status_code == 201
        wait_generation_done(client, plan_session["id"])

        # 直接在数据库把项目推进到 awaiting_summary（阶段细节不进本测试）
        from sqlalchemy.orm import sessionmaker

        factory = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
        db = factory()
        try:
            project = db.get(StageProject, 1)
            project.status = "awaiting_summary"
            db.commit()
        finally:
            db.close()

        # 汇总运行上游报错
        class FailingTransport(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                return httpx.Response(500, json={"error": {"message": "上游服务出错"}})

        install_stream(monkeypatch, FailingTransport())
        summarized = client.post("/api/chat/projects/1/summary")
        assert summarized.status_code == 201, summarized.text
        summary_session = summarized.json()["session"]
        wait_generation_done(client, summary_session["id"])

        body = client.get("/api/chat/projects/1").json()
        assert body["status"] == "awaiting_summary"

    def test_plan_run_uses_session_workspace(
        self, client: TestClient, db_engine, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """项目下的会话把工作区写上会话行（代码钉住，不靠约定）。"""
        workspace = tmp_path / "pinned"
        workspace.mkdir()
        model = create_model(client)
        agent = create_agent(
            client, model["id"], autonomyMode="stage_confirm", workspaceDir=str(workspace)
        )
        plan_session = create_session(client, agent_id=agent["id"])
        install_stream(
            monkeypatch,
            SequenceTransport(
                [
                    [
                        sse_tool_call(0, "call-1", "submit_stage_plan", plan_arguments()),
                        b"data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}).encode() + b"\n\n",
                        SSE_DONE,
                    ]
                ]
            ),
        )
        assert send_message(client, plan_session["id"], "做一个项目").status_code == 201
        wait_generation_done(client, plan_session["id"])

        install_stream(
            monkeypatch,
            SequenceTransport([self._stage_finish_chunks("阶段一：项目骨架", "handoff-阶段一.md")]),
        )
        started = client.post("/api/chat/projects/1/start")
        assert started.status_code == 201, started.text
        stage1 = started.json()["session"]

        from sqlalchemy.orm import sessionmaker

        factory = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
        db = factory()
        try:
            row = db.get(ChatSession, stage1["id"])
            assert row is not None
            assert Path(row.workspace_dir) == workspace
            assert row.project_id == 1
            project = db.get(StageProject, 1)
            assert Path(project.workspace_dir) == workspace
            assert project.agent_id == agent["id"]
        finally:
            db.close()
