"""评测模块 HTTP API 测试（阶段十二）：数据集 / 用例 / 任务的校验与状态机。

执行细节（Runtime 与评分）在 test_eval_runner.py 覆盖；这里验证接口层
行为：字段校验、空数据集限制、删除保护、重复启动限制、快照隔离、
Run 来源过滤与示例数据集幂等种子（lifespan 触发）。
"""

import time

import httpx
import pytest

from app.db.models.evaluation import (
    EvalTask,
)
from app.services import model_client
from app.services.eval_seed import ensure_example_dataset
from tests.test_eval_runner import EvalTransport

# ---- 辅助 ----


def seed_model(db, name: str = "评分模型", identifier: str = "scorer-model") -> int:
    from datetime import datetime, timezone

    from app.db.models.model_config import ModelConfig

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    model = ModelConfig(
        display_name=name,
        model_identifier=identifier,
        base_url="https://scorer.example.com/v1",
        context_length=32768,
        max_output_tokens=512,
        temperature=0.7,
        created_at=now,
        updated_at=now,
    )
    db.add(model)
    db.commit()
    return model.id


def seed_agent(db, model_id: int, name: str = "被测 Agent") -> int:
    from datetime import datetime, timezone

    from app.db.models.agent import Agent

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    agent = Agent(
        name=name,
        description="",
        system_prompt="测试提示词",
        prompt_version=1,
        model_id=model_id,
        max_rounds=5,
        autonomy_mode="autonomous",
        created_at=now,
        updated_at=now,
    )
    db.add(agent)
    db.commit()
    return agent.id


def create_dataset(client, name: str = "数据集", description: str = "") -> dict:
    response = client.post("/api/evaluations/datasets", json={"name": name, "description": description})
    assert response.status_code == 201
    return response.json()


def add_case(client, dataset_id: int, question: str, expected: str = "期望", requirements: str = "要求") -> dict:
    response = client.post(
        f"/api/evaluations/datasets/{dataset_id}/cases",
        json={"question": question, "expectedAnswer": expected, "scoringRequirements": requirements},
    )
    assert response.status_code == 201
    return response.json()


def wait_task_finished(client, task_id: int, timeout_seconds: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout_seconds
    last: dict = {}
    while time.monotonic() < deadline:
        last = client.get(f"/api/evaluations/tasks/{task_id}").json()
        if last["status"] != "running":
            return last
        time.sleep(0.05)
    return last


# ---- 示例数据集 ----


class TestExampleDataset:
    def test_seeded_by_lifespan(self, client) -> None:
        response = client.get("/api/evaluations/datasets")
        assert response.status_code == 200
        datasets = response.json()
        examples = [item for item in datasets if item["isExample"]]
        assert len(examples) == 1
        example = examples[0]
        assert example["caseCount"] == 3
        detail = client.get(f"/api/evaluations/datasets/{example['id']}").json()
        questions = [case["question"] for case in detail["cases"]]
        assert "计算 23 乘以 47。" in questions

    def test_reseed_never_overwrites_user_edits(self, client, db_session) -> None:
        from sqlalchemy.orm import sessionmaker

        datasets = client.get("/api/evaluations/datasets").json()
        example = next(item for item in datasets if item["isExample"])
        # 用户修改示例数据集
        response = client.put(
            f"/api/evaluations/datasets/{example['id']}",
            json={"name": "改过的示例", "description": "用户改过"},
        )
        assert response.status_code == 200
        # 再次补种：不重复创建、不覆盖修改
        factory = sessionmaker(bind=db_session.get_bind(), autoflush=False, expire_on_commit=False)
        assert ensure_example_dataset(factory) is False
        datasets = client.get("/api/evaluations/datasets").json()
        examples = [item for item in datasets if item["isExample"]]
        assert len(examples) == 1
        assert examples[0]["name"] == "改过的示例"


# ---- 数据集与用例 ----


class TestDatasetApi:
    def test_dataset_crud(self, client) -> None:
        dataset = create_dataset(client, "我的数据集", "说明")
        assert dataset["caseCount"] == 0
        assert dataset["isExample"] is False

        updated = client.put(
            f"/api/evaluations/datasets/{dataset['id']}",
            json={"name": "改名", "description": "新说明"},
        ).json()
        assert updated["name"] == "改名"

        detail = client.get(f"/api/evaluations/datasets/{dataset['id']}").json()
        assert detail["description"] == "新说明"
        assert detail["cases"] == []

        response = client.delete(f"/api/evaluations/datasets/{dataset['id']}")
        assert response.status_code == 204
        assert client.get(f"/api/evaluations/datasets/{dataset['id']}").status_code == 404

    def test_create_dataset_requires_name(self, client) -> None:
        response = client.post("/api/evaluations/datasets", json={"name": "   "})
        assert response.status_code == 422
        assert response.json()["detail"]["fields"]["name"] == "请填写名称"

    def test_case_validation(self, client) -> None:
        dataset = create_dataset(client)
        # 问题必填
        response = client.post(
            f"/api/evaluations/datasets/{dataset['id']}/cases",
            json={"question": "  ", "expectedAnswer": "有期望"},
        )
        assert response.status_code == 422
        assert response.json()["detail"]["fields"]["question"] == "请填写用户问题"

        # 期望答案与评分要求至少一项
        response = client.post(
            f"/api/evaluations/datasets/{dataset['id']}/cases",
            json={"question": "问题", "expectedAnswer": "  ", "scoringRequirements": ""},
        )
        assert response.status_code == 422
        assert "至少填写一项" in response.json()["detail"]["fields"]["expectedAnswer"]

        # 只有评分要求也可以
        response = client.post(
            f"/api/evaluations/datasets/{dataset['id']}/cases",
            json={"question": "问题", "scoringRequirements": "回答要简洁"},
        )
        assert response.status_code == 201
        case = response.json()
        assert case["expectedAnswer"] == ""
        assert case["scoringRequirements"] == "回答要简洁"

        # 编辑与删除
        updated = client.put(
            f"/api/evaluations/datasets/{dataset['id']}/cases/{case['id']}",
            json={"question": "新问题", "expectedAnswer": "新期望"},
        ).json()
        assert updated["question"] == "新问题"
        assert client.delete(
            f"/api/evaluations/datasets/{dataset['id']}/cases/{case['id']}"
        ).status_code == 204
        detail = client.get(f"/api/evaluations/datasets/{dataset['id']}").json()
        assert detail["cases"] == []

    def test_case_text_is_trimmed(self, client) -> None:
        dataset = create_dataset(client)
        case = add_case(client, dataset["id"], "  问题  ", expected=" 期望 ")
        assert case["question"] == "问题"
        assert case["expectedAnswer"] == "期望"


# ---- 任务 ----


class TestTaskApi:
    def test_create_task_validations(self, client, db_session) -> None:
        model_id = seed_model(db_session)
        agent_id = seed_agent(db_session, model_id)
        empty_dataset = create_dataset(client, "空数据集")

        # 空数据集不能创建任务
        response = client.post(
            "/api/evaluations/tasks",
            json={
                "agentId": agent_id,
                "datasetId": empty_dataset["id"],
                "scorerModelId": model_id,
            },
        )
        assert response.status_code == 422
        assert "没有测试用例" in response.json()["detail"]["fields"]["datasetId"]

        # 不存在的 Agent / 模型
        dataset = create_dataset(client, "有用例")
        add_case(client, dataset["id"], "问题一")
        response = client.post(
            "/api/evaluations/tasks",
            json={"agentId": 99999, "datasetId": dataset["id"], "scorerModelId": model_id},
        )
        assert response.status_code == 422
        fields = response.json()["detail"]["fields"]
        assert "不存在" in fields["agentId"]

        # passScore 超范围（Pydantic 约束）
        response = client.post(
            "/api/evaluations/tasks",
            json={
                "agentId": agent_id,
                "datasetId": dataset["id"],
                "scorerModelId": model_id,
                "passScore": 101,
            },
        )
        assert response.status_code == 422

    def test_create_task_snapshots(self, client, db_session) -> None:
        model_id = seed_model(db_session)
        agent_id = seed_agent(db_session, model_id)
        dataset = create_dataset(client, "快照数据集")
        case = add_case(client, dataset["id"], "快照前的问题", expected="快照前的期望")

        response = client.post(
            "/api/evaluations/tasks",
            json={"agentId": agent_id, "datasetId": dataset["id"], "scorerModelId": model_id},
        )
        assert response.status_code == 201
        task = response.json()
        # 默认值：通过分数 60、关闭工具
        assert task["passScore"] == 60
        assert task["allowTools"] is False
        assert task["status"] == "pending"
        assert task["stats"]["totalCases"] == 1

        # 编辑数据集与用例：任务快照不变
        client.put(
            f"/api/evaluations/datasets/{dataset['id']}/cases/{case['id']}",
            json={"question": "修改后的问题", "expectedAnswer": "修改后的期望"},
        )
        client.delete(f"/api/evaluations/datasets/{dataset['id']}/cases/{case['id']}")
        detail = client.get(f"/api/evaluations/tasks/{task['id']}").json()
        assert detail["results"][0]["question"] == "快照前的问题"
        assert detail["results"][0]["expectedAnswer"] == "快照前的期望"
        # Agent 配置快照可读
        assert detail["agentConfig"]["promptVersion"] == 1
        assert detail["agentConfig"]["agentName"] == "被测 Agent"

    def test_dataset_delete_blocked_when_referenced(self, client, db_session, monkeypatch) -> None:
        model_id = seed_model(db_session)
        agent_id = seed_agent(db_session, model_id)
        dataset = create_dataset(client, "被引用的数据集")
        add_case(client, dataset["id"], "问题")
        task = client.post(
            "/api/evaluations/tasks",
            json={"agentId": agent_id, "datasetId": dataset["id"], "scorerModelId": model_id},
        ).json()
        _ = task
        response = client.delete(f"/api/evaluations/datasets/{dataset['id']}")
        assert response.status_code == 409

    def test_start_state_machine(self, client, db_session, monkeypatch) -> None:
        model_id = seed_model(db_session)
        agent_id = seed_agent(db_session, model_id)
        dataset = create_dataset(client, "状态机")
        add_case(client, dataset["id"], "问题一")
        add_case(client, dataset["id"], "问题二")
        task = client.post(
            "/api/evaluations/tasks",
            json={"agentId": agent_id, "datasetId": dataset["id"], "scorerModelId": model_id},
        ).json()
        task_id = task["id"]

        # 目标服务不可用：每条用例快速失败，任务仍会正常跑完
        install_fast_fail(monkeypatch)

        # 启动成功
        response = client.post(f"/api/evaluations/tasks/{task_id}/start")
        assert response.status_code == 200
        # 运行中重复启动被后端拒绝
        response = client.post(f"/api/evaluations/tasks/{task_id}/start")
        assert response.status_code == 409

        finished = wait_task_finished(client, task_id)
        assert finished["status"] == "completed"
        stats = finished["stats"]
        # 全部用例执行失败也计入已完成；平均分为暂无（null）
        assert stats["completedCases"] == 2
        assert stats["execFailedCount"] == 2
        assert stats["averageScore"] is None

        # 已完成任务不能再次启动并覆盖结果
        response = client.post(f"/api/evaluations/tasks/{task_id}/start")
        assert response.status_code == 409
        assert "新建任务" in response.json()["detail"]

        # 已中断任务同样不能再次启动
        db_session.query(EvalTask).filter(EvalTask.id == task_id).update({"status": "interrupted"})
        db_session.commit()
        response = client.post(f"/api/evaluations/tasks/{task_id}/start")
        assert response.status_code == 409

    def test_run_source_recorded_and_filterable(self, client, db_session, monkeypatch) -> None:
        model_id = seed_model(db_session)
        agent_id = seed_agent(db_session, model_id)
        dataset = create_dataset(client, "来源检查")
        add_case(client, dataset["id"], "来源问题")
        task = client.post(
            "/api/evaluations/tasks",
            json={"agentId": agent_id, "datasetId": dataset["id"], "scorerModelId": model_id},
        ).json()

        install_fast_fail(monkeypatch)
        assert client.post(f"/api/evaluations/tasks/{task['id']}/start").status_code == 200
        finished = wait_task_finished(client, task["id"])
        assert finished["status"] == "completed"

        detail = client.get(f"/api/evaluations/tasks/{task['id']}").json()
        result = detail["results"][0]
        assert result["runId"] is not None
        assert result["execError"] is not None

        # 运行记录带来源标记，并可按来源筛选
        runs = client.get("/api/runs", params={"source": "evaluation"}).json()
        assert runs["total"] >= 1
        assert all(item["source"] == "evaluation" for item in runs["items"])
        chat_runs = client.get("/api/runs", params={"source": "chat"}).json()
        assert all(item["source"] == "chat" for item in chat_runs["items"])

    def test_task_list_order_and_stats(self, client, db_session, monkeypatch) -> None:
        model_id = seed_model(db_session)
        agent_id = seed_agent(db_session, model_id)
        dataset = create_dataset(client, "列表")
        add_case(client, dataset["id"], "问题")
        task = client.post(
            "/api/evaluations/tasks",
            json={"agentId": agent_id, "datasetId": dataset["id"], "scorerModelId": model_id},
        ).json()
        tasks = client.get("/api/evaluations/tasks").json()
        assert tasks[0]["id"] == task["id"]
        assert tasks[0]["datasetName"] == "列表"
        assert tasks[0]["scorerModelName"] == "评分模型"
        assert tasks[0]["stats"]["pendingCases"] == 1


def install_fast_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    """目标 Agent 服务立即返回 500，任务快速跑完（不发起真实网络请求）。"""
    transport = EvalTransport(agent_scripts=[None])
    monkeypatch.setattr(
        model_client,
        "_create_async_client",
        lambda timeout: httpx.AsyncClient(transport=transport, timeout=timeout),
    )
