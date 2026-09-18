"""评测执行器与评分服务测试（阶段十二）。

模型服务统一用可分流的假传输替换：流式请求（目标 Agent）与非流式请求
（评分模型）分别处理，并捕获全部请求体供断言。全部直接调用
EvaluationManager / score_case，不经 HTTP 接口；HTTP 层行为在
test_evaluations_api.py 中覆盖。
"""

import asyncio
import json
from datetime import datetime, timezone

import httpx
import pytest

from app.core.secret_box import SecretBox
from app.db.base import Base
from app.db.models.agent import Agent, AgentBinding
from app.db.models.evaluation import (
    CASE_STATUS_EXEC_FAILED,
    CASE_STATUS_FAILED,
    CASE_STATUS_PASSED,
    CASE_STATUS_PENDING,
    CASE_STATUS_SCORING_FAILED,
    TASK_STATUS_COMPLETED,
    TASK_STATUS_INTERRUPTED,
    TASK_STATUS_RUNNING,
    EvalCase,
    EvalDataset,
    EvalTask,
    EvalTaskResult,
)
from app.db.models.model_config import ModelConfig, ModelSecret
from app.db.models.run_record import AgentRun
from app.db.models.tool import Tool
from app.services import model_client
from app.services.eval_runner import EvaluationManager, _exec_error_text
from app.services.eval_scorer import (
    ScoringRequestError,
    ScoringValidationError,
    extract_json_object,
    score_case,
    validate_score_payload,
)
from app.services.model_client import ModelCallError
from app.services.tools.registry import builtin_tool_rows

API_KEY = "sk-eval-test"
SCORER_KEY = "sk-scorer-test"


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---- 种子数据 ----


@pytest.fixture()
def engine():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

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
def box(tmp_path):
    return SecretBox(tmp_path / "eval-secret.key")


@pytest.fixture()
def skills_dir(tmp_path):
    return tmp_path / "skills"


@pytest.fixture()
def manager():
    return EvaluationManager()


def seed_tools(factory) -> None:
    db = factory()
    try:
        for row in builtin_tool_rows():
            db.add(Tool(**row))
        db.commit()
    finally:
        db.close()


def seed_models_and_agent(
    factory,
    box,
    *,
    tool_keys: tuple = (),
    max_rounds: int = 5,
) -> int:
    """一个绑定演示模型的 Agent；返回 agent_id。"""
    seed_tools(factory)
    db = factory()
    try:
        now = utcnow()
        nonce, blob = box.encrypt(API_KEY)
        model = ModelConfig(
            display_name="目标模型",
            model_identifier="target-model",
            base_url="https://target.example.com/v1",
            context_length=32768,
            max_output_tokens=512,
            temperature=0.7,
            created_at=now,
            updated_at=now,
        )
        model.secret = ModelSecret(key_nonce=nonce, key_blob=blob, updated_at=now)
        scorer_nonce, scorer_blob = box.encrypt(SCORER_KEY)
        scorer = ModelConfig(
            display_name="评分模型",
            model_identifier="scorer-model",
            base_url="https://scorer.example.com/v1",
            context_length=32768,
            max_output_tokens=512,
            temperature=0.7,
            created_at=now,
            updated_at=now,
        )
        scorer.secret = ModelSecret(key_nonce=scorer_nonce, key_blob=scorer_blob, updated_at=now)
        db.add_all([model, scorer])
        db.flush()
        agent = Agent(
            name="评测对象",
            description="",
            system_prompt="你是测试助手",
            prompt_version=1,
            model_id=model.id,
            max_rounds=max_rounds,
            is_default=True,
            created_at=now,
            updated_at=now,
        )
        db.add(agent)
        db.flush()
        for key in tool_keys:
            db.add(AgentBinding(agent_id=agent.id, capability_type="tool", ref_key=key))
        db.commit()
        return agent.id
    finally:
        db.close()


def seed_dataset_with_cases(factory, questions: list[str]) -> int:
    db = factory()
    try:
        now = utcnow()
        dataset = EvalDataset(
            name="测试数据集",
            description="",
            key=None,
            created_at=now,
            updated_at=now,
        )
        db.add(dataset)
        db.flush()
        for index, question in enumerate(questions, start=1):
            db.add(
                EvalCase(
                    dataset_id=dataset.id,
                    question=question,
                    expected_answer=f"期望答案{index}",
                    scoring_requirements=f"要求{index}",
                    created_at=now,
                    updated_at=now,
                )
            )
        db.commit()
        return dataset.id
    finally:
        db.close()


def seed_task(
    factory,
    agent_id: int,
    dataset_id: int,
    *,
    pass_score: int = 60,
    allow_tools: bool = False,
) -> int:
    """按真实创建路径构造任务与结果快照（不走 HTTP）。"""
    from app.services.eval_runner import build_agent_config_snapshot

    db = factory()
    try:
        now = utcnow()
        agent = db.get(Agent, agent_id)
        dataset = db.get(EvalDataset, dataset_id)
        scorer = db.query(ModelConfig).filter(ModelConfig.model_identifier == "scorer-model").first()
        task = EvalTask(
            dataset_id=dataset_id,
            dataset_name=dataset.name,
            agent_id=agent_id,
            agent_name=agent.name,
            scorer_model_id=scorer.id,
            scorer_model_name=scorer.display_name,
            scorer_model_identifier=scorer.model_identifier,
            pass_score=pass_score,
            allow_tools=allow_tools,
            agent_config_json=build_agent_config_snapshot(db, agent_id, allow_tools=allow_tools),
            created_at=now,
            updated_at=now,
        )
        db.add(task)
        db.flush()
        for order, case in enumerate(dataset.cases, start=1):
            db.add(
                EvalTaskResult(
                    task_id=task.id,
                    case_id=case.id,
                    case_order=order,
                    question=case.question,
                    expected_answer=case.expected_answer,
                    scoring_requirements=case.scoring_requirements,
                    created_at=now,
                    updated_at=now,
                )
            )
        db.commit()
        return task.id
    finally:
        db.close()


def get_results(factory, task_id: int) -> list[EvalTaskResult]:
    db = factory()
    try:
        return (
            db.query(EvalTaskResult)
            .filter(EvalTaskResult.task_id == task_id)
            .order_by(EvalTaskResult.case_order)
            .all()
        )
    finally:
        db.close()


def get_task(factory, task_id: int) -> EvalTask:
    db = factory()
    try:
        return db.get(EvalTask, task_id)
    finally:
        db.close()


# ---- 模拟传输 ----


def text_chunk(text: str) -> bytes:
    return f"data: {json.dumps({'choices': [{'delta': {'content': text}}]})}\n\n".encode("utf-8")


def tool_call_chunk(index: int, call_id: str, name: str, arguments: str) -> bytes:
    return f"data: {json.dumps({'choices': [{'delta': {'tool_calls': [{'index': index, 'id': call_id, 'type': 'function', 'function': {'name': name, 'arguments': arguments}}]}}]})}\n\n".encode(
        "utf-8"
    )


def finish_chunk(reason: str) -> bytes:
    return f"data: {json.dumps({'choices': [{'delta': {}, 'finish_reason': reason}]})}\n\n".encode("utf-8")


DONE_CHUNK = b"data: [DONE]\n\n"


class EvalTransport(httpx.AsyncBaseTransport):
    """分流假传输：流式（目标 Agent）与非流式（评分）分别按脚本响应。

    agent_scripts 逐个流式请求消费；脚本项为 None 表示该请求返回 500
    （模拟目标 Agent 服务错误），列表耗尽后默认正常空流。scorer 同理：
    payload 项为字符串时按纯文本返回（模拟格式错误）。
    """

    def __init__(
        self,
        agent_scripts: list[list[bytes] | None] | None = None,
        scorer_payloads: list[dict | str] | None = None,
        *,
        scorer_error_status: int | None = None,
    ) -> None:
        self.agent_scripts = list(agent_scripts or [])
        self.scorer_payloads = list(scorer_payloads or [])
        self.scorer_error_status = scorer_error_status
        self.bodies: list[dict] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.bodies.append(body)
        if body.get("stream"):
            script = self.agent_scripts.pop(0) if self.agent_scripts else []
            if script is None:
                return httpx.Response(500, json={"error": {"message": "目标服务不可用"}})
            chunks = script or [DONE_CHUNK]

            async def stream_body():
                for chunk in chunks:
                    yield chunk

            return httpx.Response(
                200, headers={"content-type": "text/event-stream"}, content=stream_body()
            )
        # 非流式：评分调用
        if self.scorer_error_status is not None:
            return httpx.Response(
                self.scorer_error_status,
                json={"error": {"message": "评分服务不可用"}},
            )
        payload = (
            self.scorer_payloads.pop(0)
            if self.scorer_payloads
            else {"score": 90, "reason": "回答正确"}
        )
        if isinstance(payload, str):
            content = payload
        else:
            content = json.dumps(payload, ensure_ascii=False)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
            },
        )


def install(monkeypatch: pytest.MonkeyPatch, transport: httpx.AsyncBaseTransport) -> None:
    monkeypatch.setattr(
        model_client,
        "_create_async_client",
        lambda timeout: httpx.AsyncClient(transport=transport, timeout=timeout),
    )


def stream_bodies(transport: EvalTransport) -> list[dict]:
    return [body for body in transport.bodies if body.get("stream")]


def scorer_bodies(transport: EvalTransport) -> list[dict]:
    return [body for body in transport.bodies if not body.get("stream")]


def run_task(manager: EvaluationManager, task_id: int, factory, box, skills_dir) -> None:
    """启动任务并等待其执行完毕（含收尾）。"""

    async def scenario() -> None:
        manager.start(task_id, factory, box, skills_dir)
        task = manager._tasks.get(task_id)
        assert task is not None
        await task

    asyncio.run(scenario())


# ---- 评分服务 ----


class TestScorerValidation:
    def test_extract_json_object(self) -> None:
        assert extract_json_object('{"score": 85, "reason": "好"}') == {"score": 85, "reason": "好"}
        assert extract_json_object('说明\n```json\n{"score": 50, "reason": "一般"}\n```') == {
            "score": 50,
            "reason": "一般",
        }
        assert extract_json_object("没有 JSON") is None
        assert extract_json_object('{"score": 85,') is None

    def test_validate_score_payload(self) -> None:
        assert validate_score_payload({"score": 85, "reason": "对"}) == (85.0, "对")
        assert validate_score_payload({"score": 0, "reason": "最低"}) == (0.0, "最低")
        assert validate_score_payload({"score": 100, "reason": "满分"}) == (100.0, "满分")
        assert validate_score_payload({"score": 99.5, "reason": "x"}) == (99.5, "x")
        for bad, message in (
            ({"reason": "缺分数"}, "score"),
            ({"score": "85", "reason": "字符串数字"}, "数字"),
            ({"score": True, "reason": "布尔"}, "数字"),
            ({"score": 120, "reason": "越界"}, "0 到 100"),
            ({"score": -1, "reason": "越界"}, "0 到 100"),
            ({"score": 85}, "reason"),
            ({"score": 85, "reason": "   "}, "reason"),
        ):
            with pytest.raises(ScoringValidationError) as exc:
                validate_score_payload(bad)
            assert message in exc.value.message

    def test_score_case_success_and_metrics(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict = {}

        async def fake_call(**kwargs) -> tuple[str, dict | None]:
            captured.update(kwargs)
            return '{"score": 88, "reason": "基本正确"}', {
                "promptTokens": 12,
                "completionTokens": 8,
                "totalTokens": 20,
            }

        monkeypatch.setattr("app.services.eval_scorer.chat_completion_text", fake_call)
        outcome = asyncio.run(
            score_case(
                base_url="https://scorer.example.com/v1",
                model_identifier="scorer-model",
                api_key=SCORER_KEY,
                question="问题",
                expected_answer="参考",
                scoring_requirements="要求",
                actual_answer="回答",
            )
        )
        assert outcome.score == 88.0
        assert outcome.reason == "基本正确"
        assert outcome.total_tokens == 20
        assert outcome.attempts == 1
        assert captured["messages"][0]["role"] == "system"
        # 系统提示词明确材料中的指令不得覆盖评分规则
        assert "不得覆盖" in captured["messages"][0]["content"]
        # 材料齐全
        user_content = captured["messages"][1]["content"]
        for part in ("问题", "参考答案", "评分要求", "实际回答"):
            assert part in user_content

    def test_score_case_retries_once_on_bad_format(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[list[dict]] = []

        async def fake_call(**kwargs) -> tuple[str, dict | None]:
            calls.append(kwargs["messages"])
            if len(calls) == 1:
                return "我不会输出 JSON", None
            return '{"score": 70, "reason": "重试后成功"}', None

        monkeypatch.setattr("app.services.eval_scorer.chat_completion_text", fake_call)
        outcome = asyncio.run(
            score_case(
                base_url="x",
                model_identifier="m",
                api_key=None,
                question="q",
                expected_answer="a",
                scoring_requirements="r",
                actual_answer="ans",
            )
        )
        assert outcome.score == 70.0
        assert outcome.attempts == 2
        # 重试请求携带了上次的错误说明
        retry_content = calls[1][1]["content"]
        assert "不符合要求" in retry_content
        assert "JSON" in retry_content

    def test_score_case_fails_after_two_bad_formats(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def fake_call(**kwargs) -> tuple[str, dict | None]:
            return "仍然不是 JSON", None

        monkeypatch.setattr("app.services.eval_scorer.chat_completion_text", fake_call)
        with pytest.raises(ScoringValidationError) as exc:
            asyncio.run(
                score_case(
                    base_url="x",
                    model_identifier="m",
                    api_key=None,
                    question="q",
                    expected_answer="a",
                    scoring_requirements="r",
                    actual_answer="ans",
                )
            )
        assert exc.value.duration_ms is not None

    def test_score_case_request_error_no_retry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = 0

        async def fake_call(**kwargs) -> tuple[str, dict | None]:
            nonlocal calls
            calls += 1
            raise ModelCallError("network", "无法连接到服务地址", "请检查网络")

        monkeypatch.setattr("app.services.eval_scorer.chat_completion_text", fake_call)
        with pytest.raises(ScoringRequestError):
            asyncio.run(
                score_case(
                    base_url="x",
                    model_identifier="m",
                    api_key=None,
                    question="q",
                    expected_answer="a",
                    scoring_requirements="r",
                    actual_answer="ans",
                )
            )
        # 请求错误不做重试
        assert calls == 1


# ---- 任务执行 ----


class TestTaskExecution:
    def test_normal_flow_pass_and_fail(
        self, manager, factory, box, skills_dir, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent_id = seed_models_and_agent(factory, box)
        dataset_id = seed_dataset_with_cases(factory, ["第一题", "第二题"])
        task_id = seed_task(factory, agent_id, dataset_id, pass_score=60)
        transport = EvalTransport(
            agent_scripts=[
                [text_chunk("答案"), text_chunk("一"), finish_chunk("stop"), DONE_CHUNK],
                [text_chunk("答案二"), finish_chunk("stop"), DONE_CHUNK],
            ],
            scorer_payloads=[
                {"score": 85, "reason": "正确且简洁"},
                {"score": 40, "reason": "内容不完整"},
            ],
        )
        install(monkeypatch, transport)

        run_task(manager, task_id, factory, box, skills_dir)

        task = get_task(factory, task_id)
        assert task.status == TASK_STATUS_COMPLETED
        assert task.finished_at is not None
        results = get_results(factory, task_id)
        assert [r.status for r in results] == [CASE_STATUS_PASSED, CASE_STATUS_FAILED]
        assert results[0].score == 85.0
        assert results[0].reason == "正确且简洁"
        assert results[0].actual_answer == "答案一"
        assert results[1].score == 40.0
        assert results[1].actual_answer == "答案二"
        # 评分开销单独记录
        assert results[0].scoring_duration_ms is not None
        assert results[0].scoring_total_tokens == 20
        assert results[0].scoring_attempts == 1
        # 每条用例关联一条来源为评测的 Run
        db = factory()
        try:
            runs = db.query(AgentRun).order_by(AgentRun.id).all()
        finally:
            db.close()
        assert len(runs) == 2
        assert all(run.source == "evaluation" for run in runs)
        assert [r.run_id for r in results] == [run.id for run in runs]
        assert [r.run_uid for r in results] == [run.run_uid for run in runs]

    def test_pass_score_boundary(
        self, manager, factory, box, skills_dir, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent_id = seed_models_and_agent(factory, box)
        dataset_id = seed_dataset_with_cases(factory, ["刚好达到", "差一分"])
        task_id = seed_task(factory, agent_id, dataset_id, pass_score=60)
        transport = EvalTransport(
            agent_scripts=[
                [text_chunk("a"), finish_chunk("stop"), DONE_CHUNK],
                [text_chunk("b"), finish_chunk("stop"), DONE_CHUNK],
            ],
            scorer_payloads=[
                {"score": 60, "reason": "恰好等于通过分数"},
                {"score": 59.5, "reason": "略低"},
            ],
        )
        install(monkeypatch, transport)

        run_task(manager, task_id, factory, box, skills_dir)
        results = get_results(factory, task_id)
        # 得分等于通过分数记为通过；低于则未通过（不四舍五入）
        assert [r.status for r in results] == [CASE_STATUS_PASSED, CASE_STATUS_FAILED]
        assert results[1].score == 59.5

    def test_agent_error_continues_to_next_case(
        self, manager, factory, box, skills_dir, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent_id = seed_models_and_agent(factory, box)
        dataset_id = seed_dataset_with_cases(factory, ["会失败的一题", "正常的一题"])
        task_id = seed_task(factory, agent_id, dataset_id)
        transport = EvalTransport(
            agent_scripts=[
                None,  # 第一条：目标服务错误 → 执行失败
                [text_chunk("正常"), finish_chunk("stop"), DONE_CHUNK],
            ],
            scorer_payloads=[{"score": 90, "reason": "好"}],
        )
        install(monkeypatch, transport)

        run_task(manager, task_id, factory, box, skills_dir)

        task = get_task(factory, task_id)
        assert task.status == TASK_STATUS_COMPLETED
        results = get_results(factory, task_id)
        # 第一条执行失败（保留错误原因），第二条仍正常执行并评分
        assert results[0].status == CASE_STATUS_EXEC_FAILED
        assert results[0].exec_error is not None
        assert "不可用" in results[0].exec_error
        assert results[0].score is None
        assert results[0].run_id is not None
        assert results[1].status == CASE_STATUS_PASSED
        assert results[1].score == 90.0

    def test_rounds_exhausted_marks_exec_failed(
        self, manager, factory, box, skills_dir, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent_id = seed_models_and_agent(factory, box, max_rounds=1)
        dataset_id = seed_dataset_with_cases(factory, ["一直要工具"])
        task_id = seed_task(factory, agent_id, dataset_id, allow_tools=True)
        transport = EvalTransport(
            agent_scripts=[
                [tool_call_chunk(0, "call-1", "builtin_current_time", "{}"), finish_chunk("tool_calls"), DONE_CHUNK],
            ],
        )
        install(monkeypatch, transport)

        run_task(manager, task_id, factory, box, skills_dir)
        results = get_results(factory, task_id)
        assert results[0].status == CASE_STATUS_EXEC_FAILED
        # 保留具体结束原因与 Run
        assert results[0].exec_reason == "max_rounds"
        assert "轮数" in results[0].exec_error
        assert results[0].run_id is not None
        assert results[0].score is None

    def test_scoring_failure_after_retry(
        self, manager, factory, box, skills_dir, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent_id = seed_models_and_agent(factory, box)
        dataset_id = seed_dataset_with_cases(factory, ["只有一题"])
        task_id = seed_task(factory, agent_id, dataset_id)
        transport = EvalTransport(
            agent_scripts=[[text_chunk("回答"), finish_chunk("stop"), DONE_CHUNK]],
            scorer_payloads=["第一次不是 JSON", {"reason": "缺 score 字段"}],
        )
        install(monkeypatch, transport)

        run_task(manager, task_id, factory, box, skills_dir)
        results = get_results(factory, task_id)
        assert results[0].status == CASE_STATUS_SCORING_FAILED
        assert results[0].score is None
        assert "score" in results[0].score_error
        # 两次评分调用（首次 + 重试）
        assert len(scorer_bodies(transport)) == 2

    def test_scoring_request_error_recorded(
        self, manager, factory, box, skills_dir, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent_id = seed_models_and_agent(factory, box)
        dataset_id = seed_dataset_with_cases(factory, ["只有一题"])
        task_id = seed_task(factory, agent_id, dataset_id)
        transport = EvalTransport(
            agent_scripts=[[text_chunk("回答"), finish_chunk("stop"), DONE_CHUNK]],
            scorer_error_status=500,
        )
        install(monkeypatch, transport)

        run_task(manager, task_id, factory, box, skills_dir)
        results = get_results(factory, task_id)
        assert results[0].status == CASE_STATUS_SCORING_FAILED
        assert "评分请求失败" in results[0].score_error
        # 请求错误不重试
        assert len(scorer_bodies(transport)) == 1

    def test_average_ignores_unscored_cases(self, manager, factory, box, skills_dir, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.api.routes.evaluations import _stats_out

        agent_id = seed_models_and_agent(factory, box)
        dataset_id = seed_dataset_with_cases(factory, ["一", "二", "三"])
        task_id = seed_task(factory, agent_id, dataset_id)
        transport = EvalTransport(
            agent_scripts=[
                [text_chunk("1"), finish_chunk("stop"), DONE_CHUNK],
                None,  # 第二条执行失败
                [text_chunk("3"), finish_chunk("stop"), DONE_CHUNK],
            ],
            scorer_payloads=[{"score": 80, "reason": "对"}, {"score": 60, "reason": "及格"}],
        )
        install(monkeypatch, transport)

        run_task(manager, task_id, factory, box, skills_dir)
        results = get_results(factory, task_id)
        stats = _stats_out(results)
        assert stats.total_cases == 3
        assert stats.completed_cases == 3
        assert stats.passed_count == 2
        assert stats.exec_failed_count == 1
        # 平均分只统计获得合法评分的用例，不把执行失败按零分混入
        assert stats.average_score == pytest.approx((80.0 + 60.0) / 2)
        assert stats.failed_count == 0
        assert stats.scoring_failed_count == 0

    def test_average_none_without_valid_scores(self, manager, factory, box, skills_dir, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.api.routes.evaluations import _stats_out

        agent_id = seed_models_and_agent(factory, box)
        dataset_id = seed_dataset_with_cases(factory, ["一", "二"])
        task_id = seed_task(factory, agent_id, dataset_id)
        transport = EvalTransport(agent_scripts=[None, None])
        install(monkeypatch, transport)

        run_task(manager, task_id, factory, box, skills_dir)
        results = get_results(factory, task_id)
        stats = _stats_out(results)
        assert stats.average_score is None
        assert stats.exec_failed_count == 2

    def test_tools_disabled_by_default(
        self, manager, factory, box, skills_dir, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent_id = seed_models_and_agent(factory, box, tool_keys=("builtin_current_time",))
        dataset_id = seed_dataset_with_cases(factory, ["不用工具的一题"])
        task_id = seed_task(factory, agent_id, dataset_id, allow_tools=False)
        # 模型仍尝试调用工具：应被拒绝，随后给出文本回答
        transport = EvalTransport(
            agent_scripts=[
                [tool_call_chunk(0, "call-1", "builtin_current_time", "{}"), finish_chunk("tool_calls"), DONE_CHUNK],
                [text_chunk("直接回答"), finish_chunk("stop"), DONE_CHUNK],
            ],
            scorer_payloads=[{"score": 75, "reason": "正确"}],
        )
        install(monkeypatch, transport)

        run_task(manager, task_id, factory, box, skills_dir)

        first_stream = stream_bodies(transport)[0]
        # 不提供工具定义（未绑定 Skill，因此没有 load_skill）
        assert "tools" not in first_stream
        results = get_results(factory, task_id)
        assert results[0].status == CASE_STATUS_PASSED
        db = factory()
        try:
            run = db.query(AgentRun).order_by(AgentRun.id.desc()).first()
        finally:
            db.close()
        # 工具调用在运行时被拒绝
        assert run.rejected_tool_calls >= 1
        assert run.tool_calls == 0

    def test_tools_enabled_uses_agent_bindings(
        self, manager, factory, box, skills_dir, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent_id = seed_models_and_agent(factory, box, tool_keys=("builtin_current_time",))
        dataset_id = seed_dataset_with_cases(factory, ["用工具的一题"])
        task_id = seed_task(factory, agent_id, dataset_id, allow_tools=True)
        transport = EvalTransport(
            agent_scripts=[[text_chunk("完成"), finish_chunk("stop"), DONE_CHUNK]],
            scorer_payloads=[{"score": 95, "reason": "正确"}],
        )
        install(monkeypatch, transport)

        run_task(manager, task_id, factory, box, skills_dir)
        first_stream = stream_bodies(transport)[0]
        tool_names = [t["function"]["name"] for t in first_stream["tools"]]
        assert "builtin_current_time" in tool_names
        results = get_results(factory, task_id)
        assert results[0].status == CASE_STATUS_PASSED

    def test_cases_are_isolated_and_materials_not_exposed(
        self, manager, factory, box, skills_dir, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent_id = seed_models_and_agent(factory, box)
        dataset_id = seed_dataset_with_cases(factory, ["第一题的问题", "第二题的问题"])
        task_id = seed_task(factory, agent_id, dataset_id)
        transport = EvalTransport(
            agent_scripts=[
                [text_chunk("回答一"), finish_chunk("stop"), DONE_CHUNK],
                [text_chunk("回答二"), finish_chunk("stop"), DONE_CHUNK],
            ],
            scorer_payloads=[
                {"score": 80, "reason": "一"},
                {"score": 80, "reason": "二"},
            ],
        )
        install(monkeypatch, transport)

        run_task(manager, task_id, factory, box, skills_dir)

        streams = stream_bodies(transport)
        assert len(streams) == 2
        # 每条用例独立上下文：用户消息只有本用例的问题，不出现其他用例内容
        first_user = [m for m in streams[0]["messages"] if m["role"] == "user"]
        second_user = [m for m in streams[1]["messages"] if m["role"] == "user"]
        assert len(first_user) == 1 and first_user[0]["content"] == "第一题的问题"
        assert len(second_user) == 1 and second_user[0]["content"] == "第二题的问题"
        assert "第二题的问题" not in json.dumps(streams[0], ensure_ascii=False)
        assert "第一题的问题" not in json.dumps(streams[1], ensure_ascii=False)
        # 期望答案与评分要求不暴露给目标 Agent
        all_agent_text = json.dumps(streams, ensure_ascii=False)
        assert "期望答案1" not in all_agent_text
        assert "要求1" not in all_agent_text
        # 评分请求包含期望答案与评分要求
        scorers = scorer_bodies(transport)
        assert "期望答案1" in scorers[0]["messages"][1]["content"]
        assert "要求1" in scorers[0]["messages"][1]["content"]

    def test_cannot_start_twice(
        self, manager, factory, box, skills_dir, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent_id = seed_models_and_agent(factory, box)
        dataset_id = seed_dataset_with_cases(factory, ["一题"])
        task_id = seed_task(factory, agent_id, dataset_id)
        transport = EvalTransport(
            agent_scripts=[[text_chunk("a"), finish_chunk("stop"), DONE_CHUNK]],
            scorer_payloads=[{"score": 90, "reason": "好"}],
        )
        install(monkeypatch, transport)

        async def scenario() -> None:
            manager.start(task_id, factory, box, skills_dir)
            with pytest.raises(Exception) as exc:
                manager.start(task_id, factory, box, skills_dir)
            assert "重复启动" in str(exc.value) or "运行中" in str(exc.value)
            await manager._tasks[task_id]

        asyncio.run(scenario())
        # 已完成任务不能再次启动
        with pytest.raises(Exception) as exc:
            manager.start(task_id, factory, box, skills_dir)
        assert "完成" in str(exc.value)

    def test_exec_error_text_mapping(self) -> None:
        from app.services.agent_runtime import RunResult

        cancelled = RunResult(run_id="r", status="cancelled", reason="cancelled", content="部分")
        assert _exec_error_text(cancelled) == "运行被取消"
        error = RunResult(
            run_id="r",
            status="error",
            reason="model_error",
            content="",
            error_message="模型请求失败",
            error_hint="请重试",
        )
        assert "模型请求失败" in _exec_error_text(error)
        assert "请重试" in _exec_error_text(error)


# ---- 服务重启恢复 ----


class TestRecovery:
    def test_recover_interrupted_tasks(
        self, manager, factory, box, skills_dir, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent_id = seed_models_and_agent(factory, box)
        dataset_id = seed_dataset_with_cases(factory, ["一", "二", "三"])
        task_id = seed_task(factory, agent_id, dataset_id)
        # 模拟重启前的遗留状态：任务 running，一条执行中、一条评分中、一条未开始
        db = factory()
        try:
            task = db.get(EvalTask, task_id)
            task.status = TASK_STATUS_RUNNING
            task.started_at = utcnow()
            results = task.results
            results[0].status = "executing"
            results[1].status = "scoring"
            db.commit()
        finally:
            db.close()

        recovered = manager.recover_interrupted(factory)
        assert recovered == 1

        task = get_task(factory, task_id)
        assert task.status == TASK_STATUS_INTERRUPTED
        assert task.finished_at is not None
        results = get_results(factory, task_id)
        assert results[0].status == CASE_STATUS_EXEC_FAILED
        assert "重启" in results[0].exec_error
        assert results[1].status == CASE_STATUS_SCORING_FAILED
        assert "重启" in results[1].score_error
        assert results[2].status == CASE_STATUS_PENDING
        # 中断的任务不能再次启动
        with pytest.raises(Exception):
            manager.start(task_id, factory, box, skills_dir)

    def test_recovery_keeps_completed_results(
        self, manager, factory, box, skills_dir
    ) -> None:
        agent_id = seed_models_and_agent(factory, box)
        dataset_id = seed_dataset_with_cases(factory, ["一", "二"])
        task_id = seed_task(factory, agent_id, dataset_id)
        db = factory()
        try:
            task = db.get(EvalTask, task_id)
            task.status = TASK_STATUS_RUNNING
            results = task.results
            results[0].status = CASE_STATUS_PASSED
            results[0].score = 88.0
            results[0].reason = "已完成的结果"
            results[1].status = "executing"
            db.commit()
        finally:
            db.close()

        manager.recover_interrupted(factory)
        results = get_results(factory, task_id)
        # 已有结果保留，不被覆盖
        assert results[0].status == CASE_STATUS_PASSED
        assert results[0].score == 88.0
        assert results[1].status == CASE_STATUS_EXEC_FAILED

    def test_recovery_noop_when_clean(self, manager, factory, box, skills_dir) -> None:
        assert manager.recover_interrupted(factory) == 0


# ---- 示例数据集幂等 ----


class TestExampleDatasetSeed:
    def test_seed_is_idempotent_and_never_overwrites(self, factory) -> None:
        from app.services.eval_seed import ensure_example_dataset

        assert ensure_example_dataset(factory) is True
        assert ensure_example_dataset(factory) is False

        db = factory()
        try:
            datasets = db.query(EvalDataset).all()
            assert len(datasets) == 1
            assert len(datasets[0].cases) == 3
            # 模拟用户修改示例内容
            datasets[0].name = "用户改名后的示例"
            datasets[0].cases[0].question = "用户改过的问题"
            db.commit()
        finally:
            db.close()

        # 重复补种不覆盖用户修改
        assert ensure_example_dataset(factory) is False
        db = factory()
        try:
            dataset = db.query(EvalDataset).first()
            assert dataset.name == "用户改名后的示例"
            assert dataset.cases[0].question == "用户改过的问题"
        finally:
            db.close()

    def test_example_cases_content(self) -> None:
        from app.services.eval_seed import example_case_rows

        rows = example_case_rows()
        assert len(rows) == 3
        assert rows[0]["question"] == "计算 23 乘以 47。"
        assert rows[0]["expected_answer"] == "1081"
        assert rows[2]["question"].startswith("小明有 5 个苹果")
