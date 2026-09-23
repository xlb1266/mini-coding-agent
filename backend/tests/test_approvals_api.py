"""工具执行审批接口测试：决议的归属与状态校验、挂起查询。

决议端点要求会话正在生成，测试直接往 generation_manager 注入一个
GenerationRun（不真正调用模型），在同一个事件循环里经 ASGI 传输发起
HTTP 请求，保证注册表里的 Future 与端点在同一循环上。
"""

import asyncio
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient  # noqa: F401  （保持与其它 API 测试一致的导入习惯）
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.secret_box import SecretBox, get_secret_box
from app.db.base import Base
from app.db.models.agent import Agent
from app.db.models.model_config import ModelConfig
from app.db.session import get_db, get_session_factory
from app.main import app
from app.services.approvals import approval_registry
from app.services.chat_generation import GenerationRun, generation_manager
from app.api.routes.skills import get_skills_dir


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture()
def api_env(db_engine: object, tmp_path):
    """独立的数据库依赖覆盖 + 种子数据（模型 + Agent）；返回 (factory, agent_id, secret_box)。"""
    factory = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    box = SecretBox(tmp_path / "approval-api-secret.key")

    def override_get_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_session_factory] = lambda: factory
    app.dependency_overrides[get_secret_box] = lambda: box
    app.dependency_overrides[get_skills_dir] = lambda: tmp_path / "skills"

    db = factory()
    try:
        now = utcnow()
        model = ModelConfig(
            display_name="模型",
            model_identifier="demo-model",
            base_url="https://api.example.com/v1",
            context_length=32768,
            max_output_tokens=512,
            temperature=0.7,
            created_at=now,
            updated_at=now,
        )
        db.add(model)
        db.flush()
        agent = Agent(
            name="审批助手",
            description="",
            system_prompt="你是测试助手",
            prompt_version=1,
            model_id=model.id,
            max_rounds=5,
            require_approval=True,
            is_default=False,
            created_at=now,
            updated_at=now,
        )
        db.add(agent)
        db.commit()
        agent_id = agent.id
    finally:
        db.close()

    yield factory, agent_id, box
    app.dependency_overrides.clear()


def make_run(session_id: int, agent_id: int, factory, box: SecretBox, tmp_path) -> GenerationRun:
    """注入 generation_manager 的最小运行对象：只为让会话处于“生成中”。"""
    return GenerationRun(
        session_id=session_id,
        agent_id=agent_id,
        agent_name="审批助手",
        user_input="做点事",
        history=[],
        api_key=None,
        session_factory=factory,
        secret_box=box,
        skills_dir=tmp_path / "skills",
    )


def test_decision_and_listing_flow(api_env, tmp_path) -> None:
    factory, agent_id, box = api_env

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post("/api/chat/sessions", json={"agentId": agent_id})
            assert created.status_code == 201, created.text
            session_id = created.json()["id"]

            # 未在生成的会话不允许决议
            response = await client.post(
                f"/api/chat/sessions/{session_id}/approvals/apr-1/decision",
                json={"action": "approve"},
            )
            assert response.status_code == 409

            # 会话进入“生成中”：注入运行对象
            run = make_run(session_id, agent_id, factory, box, tmp_path)
            generation_manager._runs[session_id] = run
            try:
                # 查询：暂无挂起审批
                response = await client.get(f"/api/chat/sessions/{session_id}/approvals")
                assert response.status_code == 200
                assert response.json() == {"approvals": []}

                # 登记挂起审批后可查询
                approval = approval_registry.create(
                    session_id,
                    None,
                    {
                        "requestId": "apr-1",
                        "toolName": "builtin_shell",
                        "displayName": "Shell 命令",
                        "toolType": "builtin",
                        "arguments": {"command": "echo hi"},
                    },
                )
                response = await client.get(f"/api/chat/sessions/{session_id}/approvals")
                approvals = response.json()["approvals"]
                assert len(approvals) == 1
                assert approvals[0]["requestId"] == "apr-1"
                assert approvals[0]["toolName"] == "builtin_shell"

                # 非法 action → 422；未知请求 → 404
                response = await client.post(
                    f"/api/chat/sessions/{session_id}/approvals/apr-1/decision",
                    json={"action": "maybe"},
                )
                assert response.status_code == 422
                response = await client.post(
                    f"/api/chat/sessions/{session_id}/approvals/apr-999/decision",
                    json={"action": "approve"},
                )
                assert response.status_code == 404

                # 其他会话登记的审批，不能通过本会话决议（归属校验）
                approval_registry.create(
                    999, None, {"requestId": "apr-other", "toolName": "builtin_shell"}
                )
                response = await client.post(
                    f"/api/chat/sessions/{session_id}/approvals/apr-other/decision",
                    json={"action": "reject"},
                )
                assert response.status_code == 404

                # 正常批准：Runtime 等待中的 Future 得到决定
                response = await client.post(
                    f"/api/chat/sessions/{session_id}/approvals/apr-1/decision",
                    json={"action": "approve"},
                )
                assert response.status_code == 200
                body = response.json()
                assert body == {"requestId": "apr-1", "action": "approve", "outcome": "approved"}
                assert approval.future.result() == "approved"

                # 已处理的请求再次决议 → 404
                response = await client.post(
                    f"/api/chat/sessions/{session_id}/approvals/apr-1/decision",
                    json={"action": "approve"},
                )
                assert response.status_code == 404
            finally:
                generation_manager._runs.pop(session_id, None)
                approval_registry.cancel_for_session(session_id)
                approval_registry.cancel_for_session(999)

    asyncio.run(scenario())
