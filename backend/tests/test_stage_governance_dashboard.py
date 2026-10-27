"""阶段十五（阶段化开发 + 执行治理 + 仪表盘）测试。

覆盖：
- finish_stage 工具：阶段确认模式注入注册表与系统提示词；调用后以
  stage_complete 终态结束并携带阶段摘要；参数缺失按失败返回；
- 高度自主模式：不提供 finish_stage，系统提示词不带阶段协议；
- 聊天会话：stage_complete 收尾把验收状态与阶段摘要写上会话；验收
  接口清状态；会话摘要接口带 awaitingAcceptance / stageSummary；
- Agent API：审批档位 / 自主档位 / 命令白名单的创建与取值校验；
- 评测回归对比 /api/evaluations/tasks/{a}/compare/{b}。

模型服务复用 test_agent_runtime 的 ScriptedTransport 模拟，不发真实网络请求。
"""

import asyncio
import json
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from app.db.models.agent import Agent
from app.db.models.chat import ChatSession
from app.db.models.evaluation import EvalDataset, EvalTask, EvalTaskResult
from app.db.models.model_config import ModelConfig
from app.services import model_client
from app.services.agent_runtime import (
    REASON_STAGE_FINISHED,
    STATUS_STAGE_COMPLETE,
    AgentRuntime,
    RuntimeRequest,
)
from app.services.chat_generation import GenerationManager, GenerationRun, generation_manager

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

API_KEY = "sk-test-123456"


# ---------------------------------------------------------------------------
# finish_stage 与自主档位（AgentRuntime 级）
# ---------------------------------------------------------------------------


def events_of_type(events: list[dict], event_type: str) -> list[dict]:
    return [e for e in events if e["type"] == event_type]


def stage_responses() -> list[list[bytes]]:
    """第一轮：模型写完交接文档后调用 finish_stage；不安排后续轮次。"""
    return [
        [
            tool_call_chunk(
                0,
                "call-1",
                "finish_stage",
                json.dumps(
                    {
                        "stageName": "阶段一：项目骨架",
                        "summary": "搭好目录与基础配置",
                        "handoffPath": "handoff-阶段一.md",
                        "nextStep": "实现核心页面",
                    }
                ),
            ),
            finish_chunk("tool_calls"),
            DONE_CHUNK,
        ]
    ]


class TestFinishStage:
    def test_stage_confirm_registry_and_prompt(
        self, runtime: AgentRuntime, factory, box, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        agent_id = seed_agent(
            factory, box, tool_keys=("builtin_file",), workspace_dir=str(tmp_path), autonomy_mode="stage_confirm"
        )
        transport = ScriptedTransport([[text_chunk("开始干活"), finish_chunk("stop"), DONE_CHUNK]])
        install(monkeypatch, transport)

        events: list[dict] = []
        asyncio.run(
            runtime.run(
                RuntimeRequest(agent_id=agent_id, user_input="开始"),
                on_event=events.append,
            )
        )
        body = transport.bodies[0]
        # 系统提示词附加阶段协议；工具定义含 finish_stage
        assert "阶段化开发工作流" in body["messages"][0]["content"]
        tool_names = [t["function"]["name"] for t in body["tools"]]
        assert "finish_stage" in tool_names

    def test_finish_stage_ends_run_with_stage_summary(
        self, runtime: AgentRuntime, factory, box, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        agent_id = seed_agent(factory, box, workspace_dir=str(tmp_path), autonomy_mode="stage_confirm")
        transport = ScriptedTransport(stage_responses())
        install(monkeypatch, transport)

        events: list[dict] = []
        result = asyncio.run(
            runtime.run(
                RuntimeRequest(agent_id=agent_id, user_input="开始阶段一"),
                on_event=events.append,
            )
        )
        assert result.status == STATUS_STAGE_COMPLETE
        assert result.reason == REASON_STAGE_FINISHED
        end = events_of_type(events, "run_end")[0]
        assert end["status"] == STATUS_STAGE_COMPLETE
        assert end["stage"] == {
            "stageName": "阶段一：项目骨架",
            "summary": "搭好目录与基础配置",
            "handoffPath": "handoff-阶段一.md",
            "nextStep": "实现核心页面",
        }
        # finish_stage 卡片以成功收尾
        tool_end = events_of_type(events, "tool_call_end")[-1]
        assert tool_end["status"] == "success"

    def test_finish_stage_missing_param_fails_without_ending(
        self, runtime: AgentRuntime, factory, box, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        agent_id = seed_agent(factory, box, workspace_dir=str(tmp_path), autonomy_mode="stage_confirm")
        transport = ScriptedTransport(
            [
                [
                    tool_call_chunk(0, "call-1", "finish_stage", json.dumps({"stageName": "阶段一"})),
                    finish_chunk("tool_calls"),
                    DONE_CHUNK,
                ],
                [text_chunk("已补全信息"), finish_chunk("stop"), DONE_CHUNK],
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
        # 参数缺失按失败处理，运行不结束，模型得到修正提示
        assert result.status == "completed"
        tool_end = events_of_type(events, "tool_call_end")[-1]
        assert tool_end["status"] == "error"

    def test_autonomous_mode_has_no_stage_tool(
        self, runtime: AgentRuntime, factory, box, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        agent_id = seed_agent(
            factory, box, workspace_dir=str(tmp_path), autonomy_mode="autonomous"
        )
        transport = ScriptedTransport([[text_chunk("开始干活"), finish_chunk("stop"), DONE_CHUNK]])
        install(monkeypatch, transport)

        events: list[dict] = []
        asyncio.run(
            runtime.run(
                RuntimeRequest(agent_id=agent_id, user_input="开始"),
                on_event=events.append,
            )
        )
        body = transport.bodies[0]
        assert "阶段化开发工作流" not in body["messages"][0]["content"]
        assert "tools" not in body  # 没有绑定任何工具，也没有 finish_stage


# ---------------------------------------------------------------------------
# 聊天会话：stage_complete 落库与验收接口
# ---------------------------------------------------------------------------


def make_run(session_id: int, agent_id: int, factory, box, tmp_path) -> GenerationRun:
    return GenerationRun(
        session_id=session_id,
        agent_id=agent_id,
        agent_name="阶段助手",
        user_input="开始阶段",
        history=[],
        api_key=None,
        session_factory=factory,
        secret_box=box,
        skills_dir=tmp_path / "skills",
    )


class TestStageAcceptance:
    def test_stage_complete_marks_session(self, box, tmp_path, db_engine, db_session) -> None:
        # factory 必须与 db_session 同一引擎（engine 夹具各自是独立内存库）
        from sqlalchemy.orm import sessionmaker

        factory = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        model = ModelConfig(
            display_name="模型",
            model_identifier="m",
            base_url="https://x/v1",
            context_length=32768,
            max_output_tokens=512,
            temperature=0.7,
            created_at=now,
            updated_at=now,
        )
        db_session.add(model)
        db_session.flush()
        agent = Agent(
            name="阶段助手",
            system_prompt="",
            prompt_version=1,
            model_id=model.id,
            max_rounds=5,
            created_at=now,
            updated_at=now,
        )
        db_session.add(agent)
        db_session.flush()
        session = ChatSession(title="阶段会话", agent_id=agent.id, created_at=now, updated_at=now)
        db_session.add(session)
        db_session.commit()

        manager = GenerationManager()
        run = make_run(session.id, agent.id, factory, box, tmp_path)
        manager._handle_run_end(
            run,
            {
                "type": "run_end",
                "runId": "r1",
                "status": STATUS_STAGE_COMPLETE,
                "reason": REASON_STAGE_FINISHED,
                "content": "",
                "stage": {"stageName": "阶段一", "summary": "完成", "handoffPath": "h.md", "nextStep": "下一步"},
                "message": None,
                "error": None,
            },
        )
        db_session.expire_all()
        fresh = db_session.get(ChatSession, session.id)
        assert fresh.awaiting_acceptance is True
        assert json.loads(fresh.stage_summary_json)["stageName"] == "阶段一"

    def test_open_session_carries_stage_state(
        self, client: TestClient, db_engine, db_session
    ) -> None:
        from app.core.secret_box import SecretBox
        from app.db.session import get_session_factory

        model = ModelConfig(
            display_name="模型",
            model_identifier="m",
            base_url="https://x/v1",
            context_length=32768,
            max_output_tokens=512,
            temperature=0.7,
            created_at=datetime.now(timezone.utc).replace(tzinfo=None),
            updated_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        db_session.add(model)
        db_session.flush()
        agent = Agent(
            name="阶段助手",
            system_prompt="",
            prompt_version=1,
            model_id=model.id,
            max_rounds=5,
            created_at=datetime.now(timezone.utc).replace(tzinfo=None),
            updated_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        db_session.add(agent)
        db_session.flush()
        session = ChatSession(
            title="验收会话",
            agent_id=agent.id,
            awaiting_acceptance=True,
            stage_summary_json=json.dumps({"stageName": "阶段一", "summary": "完成"}),
            created_at=datetime.now(timezone.utc).replace(tzinfo=None),
            updated_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        db_session.add(session)
        db_session.commit()

        # 打开会话：阶段状态随会话摘要下发；普通会话不带项目归属
        opened = client.get(f"/api/chat/sessions/{session.id}/messages")
        assert opened.status_code == 200
        body = opened.json()
        assert body["session"]["awaitingAcceptance"] is True
        assert body["session"]["stageSummary"]["stageName"] == "阶段一"
        assert body["session"]["projectId"] is None
        assert body["session"]["projectRole"] is None

        # 已删除的会话 404
        assert client.get(f"/api/chat/sessions/{session.id + 999}/messages").status_code == 404


# ---------------------------------------------------------------------------
# Agent API：审批档位 / 自主档位 / 白名单
# ---------------------------------------------------------------------------


def create_model(client: TestClient) -> dict:
    response = client.post(
        "/api/models",
        json={
            "displayName": "模型",
            "modelIdentifier": "m",
            "baseUrl": "https://x/v1",
            "apiKey": API_KEY,
            "contextLength": 32768,
            "maxOutputTokens": 512,
            "temperature": 0.7,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


class TestAgentGovernanceFields:
    def test_create_and_update_governance_fields(self, client: TestClient, seeded_tools) -> None:
        model = create_model(client)
        created = client.post(
            "/api/agents",
            json={
                "name": "治理助手",
                "systemPrompt": "",
                "modelId": model["id"],
                "maxRounds": 5,
                "approvalLevel": "strict",
                "autonomyMode": "stage_confirm",
                "shellWhitelist": "uv run pytest\ngit status",
            },
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["approvalLevel"] == "strict"
        assert body["autonomyMode"] == "stage_confirm"
        assert body["shellWhitelist"] == "uv run pytest\ngit status"

        updated = client.put(
            f"/api/agents/{body['id']}",
            json={
                "name": "治理助手",
                "systemPrompt": "",
                "modelId": model["id"],
                "maxRounds": 5,
                "approvalLevel": "off",
                "autonomyMode": "autonomous",
                "shellWhitelist": "",
            },
        )
        assert updated.status_code == 200
        assert updated.json()["approvalLevel"] == "off"
        assert updated.json()["autonomyMode"] == "autonomous"

    def test_invalid_level_rejected(self, client: TestClient, seeded_tools) -> None:
        model = create_model(client)
        response = client.post(
            "/api/agents",
            json={
                "name": "治理助手",
                "systemPrompt": "",
                "modelId": model["id"],
                "maxRounds": 5,
                "approvalLevel": "everything",
            },
        )
        assert response.status_code == 422
        fields = response.json()["detail"]["fields"]
        assert "approvalLevel" in fields


# ---------------------------------------------------------------------------
# 评测回归对比
# ---------------------------------------------------------------------------


class TestEvalCompare:
    def _seed_two_tasks(self, db_session) -> tuple[int, int]:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        dataset = EvalDataset(name="数据集", created_at=now, updated_at=now)
        db_session.add(dataset)
        db_session.flush()
        model = ModelConfig(
            display_name="模型",
            model_identifier="m",
            base_url="https://x/v1",
            context_length=32768,
            max_output_tokens=512,
            temperature=0.7,
            created_at=now,
            updated_at=now,
        )
        db_session.add(model)
        db_session.flush()
        agent = Agent(
            name="评测助手",
            system_prompt="",
            prompt_version=1,
            model_id=model.id,
            max_rounds=5,
            created_at=now,
            updated_at=now,
        )
        db_session.add(agent)
        db_session.flush()

        def make_task(name: str, scores: list[float | None], process: list[float | None]) -> int:
            task = EvalTask(
                dataset_id=dataset.id,
                dataset_name=dataset.name,
                agent_id=agent.id,
                agent_name=agent.name,
                scorer_model_id=model.id,
                scorer_model_name="模型",
                scorer_model_identifier="m",
                pass_score=60,
                status="completed",
                created_at=now,
                updated_at=now,
            )
            db_session.add(task)
            db_session.flush()
            for order, (score, proc) in enumerate(zip(scores, process), start=1):
                db_session.add(
                    EvalTaskResult(
                        task_id=task.id,
                        case_id=order,
                        case_order=order,
                        question=f"问题{order}",
                        status="passed" if (score or 0) >= 60 else "failed",
                        score=score,
                        reason="r",
                        process_score=proc,
                        process_reason="p",
                        created_at=now,
                        updated_at=now,
                    )
                )
            db_session.commit()
            return task.id

        base_id = make_task("V1", [80.0, 50.0], [70.0, 60.0])
        target_id = make_task("V2", [90.0, 70.0], [80.0, None])
        return base_id, target_id

    def test_compare_aggregates_and_cases(self, client: TestClient, db_session) -> None:
        base_id, target_id = self._seed_two_tasks(db_session)
        response = client.get(f"/api/evaluations/tasks/{base_id}/compare/{target_id}")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["baseTaskId"] == base_id
        assert body["targetTaskId"] == target_id
        assert body["baseStats"]["averageScore"] == 65.0
        assert body["targetStats"]["averageScore"] == 80.0
        assert body["averageScoreDelta"] == 15.0
        # V1 平均过程分 65；V2 只有第一条有过程分 80
        assert body["baseStats"]["averageProcessScore"] == 65.0
        assert body["targetStats"]["averageProcessScore"] == 80.0
        # 通过率：V1 50% → V2 100%，差值 50
        assert body["passRateDelta"] == 50.0
        assert len(body["cases"]) == 2
        first = body["cases"][0]
        assert first["caseOrder"] == 1
        assert first["baseScore"] == 80.0
        assert first["targetScore"] == 90.0
        assert first["targetProcessScore"] == 80.0

    def test_compare_requires_same_dataset(self, client: TestClient, db_session) -> None:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        dataset_a = EvalDataset(name="A", created_at=now, updated_at=now)
        dataset_b = EvalDataset(name="B", created_at=now, updated_at=now)
        db_session.add_all([dataset_a, dataset_b])
        db_session.flush()
        model = ModelConfig(
            display_name="模型",
            model_identifier="m",
            base_url="https://x/v1",
            context_length=32768,
            max_output_tokens=512,
            temperature=0.7,
            created_at=now,
            updated_at=now,
        )
        db_session.add(model)
        db_session.flush()
        agent = Agent(
            name="评测助手",
            system_prompt="",
            prompt_version=1,
            model_id=model.id,
            max_rounds=5,
            created_at=now,
            updated_at=now,
        )
        db_session.add(agent)
        db_session.flush()

        def make_task(dataset) -> int:
            task = EvalTask(
                dataset_id=dataset.id,
                dataset_name=dataset.name,
                agent_id=agent.id,
                agent_name=agent.name,
                scorer_model_id=model.id,
                scorer_model_name="模型",
                scorer_model_identifier="m",
                status="completed",
                created_at=now,
                updated_at=now,
            )
            db_session.add(task)
            db_session.commit()
            return task.id

        a = make_task(dataset_a)
        b = make_task(dataset_b)
        response = client.get(f"/api/evaluations/tasks/{a}/compare/{b}")
        assert response.status_code == 422
