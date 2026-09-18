"""Agent 管理接口测试：CRUD、默认一致性、提示词版本、停用能力保留与资源引用保护。"""

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models.agent import AgentBinding
from app.db.models.skill import Skill
from app.services.skill_store import write_skill_file

TIME_KEY = "builtin_current_time"
SHELL_KEY = "builtin_shell"


def model_payload(**overrides: object) -> dict:
    payload: dict = {
        "displayName": "DeepSeek-V3",
        "modelIdentifier": "deepseek-chat",
        "baseUrl": "https://api.deepseek.com/v1",
        "apiKey": "sk-test-123456",
        "contextLength": 32768,
        "maxOutputTokens": 4096,
        "temperature": 0.7,
    }
    payload.update(overrides)
    return payload


def create_model(client: TestClient, **overrides: object) -> dict:
    response = client.post("/api/models", json=model_payload(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


def agent_payload(model_id: int, **overrides: object) -> dict:
    payload: dict = {
        "name": "写作助手",
        "description": "润色与改写文章",
        "systemPrompt": "你是一个写作助手",
        "modelId": model_id,
        "maxRounds": 25,
        "toolKeys": [TIME_KEY],
        "skillDirNames": [],
        "mcpServerIds": [],
    }
    payload.update(overrides)
    return payload


def create_agent(client: TestClient, model_id: int, **overrides: object) -> dict:
    response = client.post("/api/agents", json=agent_payload(model_id, **overrides))
    assert response.status_code == 201, response.text
    return response.json()


def disable_tool(client: TestClient, key: str) -> None:
    tool = next(t for t in client.get("/api/tools").json() if t["key"] == key)
    response = client.put(f"/api/tools/{tool['id']}", json={"enabled": False})
    assert response.status_code == 200, response.text


def make_skill(client: TestClient, skills_dir: Path, dir_name: str, name: str, enabled: bool = True) -> None:
    """直接写入 Skill 目录并触发扫描登记，绕过 ZIP 导入，便于构造绑定场景。

    skills_dir 传 conftest 覆盖后的临时目录（tmp_path / "skills"）。
    """
    skill_dir = skills_dir / dir_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    write_skill_file(skill_dir / "SKILL.md", name, f"{name} 的用途", f"{name} 的指令")
    client.get("/api/skills")  # 触发扫描登记
    if not enabled:
        response = client.put(f"/api/skills/{dir_name}/enabled", json={"enabled": False})
        assert response.status_code == 200, response.text


def make_mcp_server(client: TestClient, name: str, enabled: bool = True) -> dict:
    response = client.post(
        "/api/mcp-servers",
        json={"name": name, "command": "uvx", "args": ["mcp-server-time"], "enabled": enabled},
    )
    assert response.status_code == 201, response.text
    return response.json()


class TestCreate:
    def test_first_agent_becomes_default_and_prompt_version_one(self, client: TestClient, seeded_tools: None) -> None:
        model = create_model(client)
        body = create_agent(client, model["id"])

        assert body["isDefault"] is True
        assert body["promptVersion"] == 1
        assert body["maxRounds"] == 25
        assert body["model"]["displayName"] == "DeepSeek-V3"
        assert body["model"]["modelIdentifier"] == "deepseek-chat"
        assert body["toolCount"] == 1

    def test_second_agent_not_default(self, client: TestClient, seeded_tools: None) -> None:
        model = create_model(client)
        create_agent(client, model["id"])
        body = create_agent(client, model["id"], name="翻译助手")

        assert body["isDefault"] is False

    def test_list_orders_default_first(self, client: TestClient, seeded_tools: None) -> None:
        model = create_model(client)
        first = create_agent(client, model["id"])
        second = create_agent(client, model["id"], name="翻译助手")
        client.put(f"/api/agents/{second['id']}/default")

        response = client.get("/api/agents")

        assert [item["id"] for item in response.json()] == [second["id"], first["id"]]

    def test_validation_errors(self, client: TestClient, seeded_tools: None) -> None:
        model = create_model(client)

        cases = [
            (agent_payload(model["id"], name="   "), "name"),
            (agent_payload(model["id"] + 999), "modelId"),
            (agent_payload(model["id"], maxRounds=0), "maxRounds"),
            (agent_payload(model["id"], maxRounds=101), "maxRounds"),
        ]
        for payload, field in cases:
            response = client.post("/api/agents", json=payload)
            assert response.status_code == 422, payload
            assert field in response.json()["detail"]["fields"], payload

    def test_missing_agent_404(self, client: TestClient) -> None:
        assert client.get("/api/agents/999").status_code == 404


class TestBindings:
    def test_detail_resolves_bindings(self, client: TestClient, seeded_tools: None, tmp_path: Path) -> None:
        model = create_model(client)
        make_skill(client, tmp_path / "skills", "write-guide", "写作指南")
        server = make_mcp_server(client, "time-server")
        agent = create_agent(
            client,
            model["id"],
            toolKeys=[TIME_KEY],
            skillDirNames=["write-guide"],
            mcpServerIds=[server["id"]],
        )

        detail = client.get(f"/api/agents/{agent['id']}").json()

        assert detail["systemPrompt"] == "你是一个写作助手"
        assert detail["toolBindings"][0]["refKey"] == TIME_KEY
        assert detail["toolBindings"][0]["name"]
        assert detail["toolBindings"][0]["available"] is True
        assert detail["skillBindings"][0]["refKey"] == "write-guide"
        assert detail["skillBindings"][0]["name"] == "写作指南"
        assert detail["skillBindings"][0]["available"] is True
        assert detail["mcpBindings"][0]["refKey"] == str(server["id"])
        assert detail["mcpBindings"][0]["name"] == "time-server"
        assert detail["mcpBindings"][0]["available"] is True

    def test_new_binding_must_be_enabled(self, client: TestClient, seeded_tools: None) -> None:
        model = create_model(client)
        disable_tool(client, TIME_KEY)

        response = client.post("/api/agents", json=agent_payload(model["id"], toolKeys=[TIME_KEY]))

        assert response.status_code == 422
        fields = response.json()["detail"]["fields"]
        assert "toolKeys" in fields
        assert "已启用" in fields["toolKeys"]

    def test_disabled_existing_binding_is_kept(self, client: TestClient, seeded_tools: None) -> None:
        """已绑定的能力后来被停用时：保留绑定、可保存其他修改、数量照常统计。"""
        model = create_model(client)
        agent = create_agent(client, model["id"], toolKeys=[TIME_KEY])
        disable_tool(client, TIME_KEY)

        # 保留绑定并修改其他字段
        response = client.put(
            f"/api/agents/{agent['id']}",
            json=agent_payload(model["id"], name="改名后的助手", toolKeys=[TIME_KEY]),
        )
        assert response.status_code == 200
        assert response.json()["toolCount"] == 1

        detail = client.get(f"/api/agents/{agent['id']}").json()
        binding = detail["toolBindings"][0]
        assert binding["name"] == "当前时间"
        assert binding["enabled"] is False
        assert binding["available"] is False

    def test_new_disabled_binding_rejected_on_update(self, client: TestClient, seeded_tools: None) -> None:
        """编辑时新增一个已停用的绑定被拒绝；保留中的停用绑定不受影响。"""
        model = create_model(client)
        agent = create_agent(client, model["id"], toolKeys=[TIME_KEY])
        disable_tool(client, TIME_KEY)
        disable_tool(client, SHELL_KEY)

        response = client.put(
            f"/api/agents/{agent['id']}",
            json=agent_payload(model["id"], toolKeys=[TIME_KEY, SHELL_KEY]),
        )

        assert response.status_code == 422
        assert "toolKeys" in response.json()["detail"]["fields"]

    def test_can_remove_disabled_binding(self, client: TestClient, seeded_tools: None) -> None:
        model = create_model(client)
        agent = create_agent(client, model["id"], toolKeys=[TIME_KEY])
        disable_tool(client, TIME_KEY)

        response = client.put(f"/api/agents/{agent['id']}", json=agent_payload(model["id"], toolKeys=[]))

        assert response.status_code == 200
        assert response.json()["toolCount"] == 0

    def test_unknown_binding_rejected(self, client: TestClient, seeded_tools: None) -> None:
        model = create_model(client)

        response = client.post("/api/agents", json=agent_payload(model["id"], toolKeys=["no_such_tool"]))

        assert response.status_code == 422
        assert "toolKeys" in response.json()["detail"]["fields"]

    def test_disabled_skills_and_mcp_rejected_for_new_binding(self, client: TestClient, seeded_tools: None, tmp_path: Path) -> None:
        model = create_model(client)
        make_skill(client, tmp_path / "skills", "stopped-guide", "停用指南", enabled=False)
        server = make_mcp_server(client, "stopped-server", enabled=False)

        cases = [
            ({"skillDirNames": ["stopped-guide"]}, "skillDirNames"),
            ({"mcpServerIds": [server["id"]]}, "mcpServerIds"),
        ]
        for override, field in cases:
            response = client.post("/api/agents", json=agent_payload(model["id"], **override))
            assert response.status_code == 422, override
            assert field in response.json()["detail"]["fields"], override


class TestPromptVersion:
    def test_version_bumps_only_on_prompt_change(self, client: TestClient, seeded_tools: None) -> None:
        model = create_model(client)
        agent = create_agent(client, model["id"])  # 版本 1

        # 改名称、说明、模型与能力绑定：版本不变
        other_model = create_model(client, displayName="Qwen", modelIdentifier="qwen-max")
        response = client.put(
            f"/api/agents/{agent['id']}",
            json=agent_payload(other_model["id"], name="新名字", description="新说明", toolKeys=[SHELL_KEY]),
        )
        assert response.json()["promptVersion"] == 1

        # 提示词内容变化：版本递增
        response = client.put(
            f"/api/agents/{agent['id']}",
            json=agent_payload(other_model["id"], name="新名字", systemPrompt="你是一个严谨的写作助手"),
        )
        assert response.json()["promptVersion"] == 2

        # 内容未变（即使其他字段再改）：版本不变
        response = client.put(
            f"/api/agents/{agent['id']}",
            json=agent_payload(other_model["id"], name="另一个名字", systemPrompt="你是一个严谨的写作助手"),
        )
        assert response.json()["promptVersion"] == 2


class TestDefaultAndDelete:
    def test_set_default_switches(self, client: TestClient, seeded_tools: None, db_session: object) -> None:
        from app.db.models.agent import Agent

        model = create_model(client)
        first = create_agent(client, model["id"])
        second = create_agent(client, model["id"], name="翻译助手")

        response = client.put(f"/api/agents/{second['id']}/default")

        assert response.status_code == 200
        assert response.json()["isDefault"] is True
        db_session.expire_all()
        assert db_session.get(Agent, first["id"]).is_default is False
        assert db_session.get(Agent, second["id"]).is_default is True

    def test_delete_plain_agent(self, client: TestClient, seeded_tools: None) -> None:
        model = create_model(client)
        agent = create_agent(client, model["id"])

        response = client.delete(f"/api/agents/{agent['id']}")

        assert response.status_code == 204
        assert client.get("/api/agents").json() == []

    def test_delete_default_requires_new_default_choice(self, client: TestClient, seeded_tools: None) -> None:
        model = create_model(client)
        first = create_agent(client, model["id"])
        second = create_agent(client, model["id"], name="翻译助手")

        # 未指定新默认：409
        assert client.delete(f"/api/agents/{first['id']}").status_code == 409

        # 指定新默认：删除并完成切换
        response = client.delete(f"/api/agents/{first['id']}?new_default_id={second['id']}")
        assert response.status_code == 204
        remaining = client.get("/api/agents").json()
        assert [item["id"] for item in remaining] == [second["id"]]
        assert remaining[0]["isDefault"] is True

    def test_delete_last_agent_clears_default(self, client: TestClient, seeded_tools: None) -> None:
        model = create_model(client)
        agent = create_agent(client, model["id"])

        response = client.delete(f"/api/agents/{agent['id']}")

        assert response.status_code == 204
        assert client.get("/api/agents").json() == []

    def test_delete_cascades_bindings(self, client: TestClient, seeded_tools: None, db_session: object) -> None:
        model = create_model(client)
        agent = create_agent(client, model["id"])

        client.delete(f"/api/agents/{agent['id']}")
        db_session.expire_all()
        assert db_session.execute(select(AgentBinding)).scalars().all() == []


class TestResourceReferenceProtection:
    def test_referenced_model_cannot_be_deleted(self, client: TestClient, seeded_tools: None) -> None:
        model = create_model(client)
        create_agent(client, model["id"])

        response = client.delete(f"/api/models/{model['id']}")

        assert response.status_code == 409
        assert "写作助手" in response.json()["detail"]

    def test_referenced_model_check_comes_before_default_switch_rule(self, client: TestClient, seeded_tools: None) -> None:
        """删除默认模型时先检查 Agent 引用；即使提供了新默认模型也拒绝。"""
        model = create_model(client)
        other = create_model(client, displayName="Qwen", modelIdentifier="qwen-max")
        create_agent(client, model["id"])

        response = client.delete(f"/api/models/{model['id']}?new_default_id={other['id']}")

        assert response.status_code == 409

    def test_model_deletable_after_unbind(self, client: TestClient, seeded_tools: None) -> None:
        model = create_model(client)
        other = create_model(client, displayName="Qwen", modelIdentifier="qwen-max")
        agent = create_agent(client, model["id"])
        client.put(f"/api/agents/{agent['id']}", json=agent_payload(other["id"], toolKeys=[]))

        # 引用解除后可删除；model 是默认模型且还有其他模型，按模型管理规则指定新默认
        response = client.delete(f"/api/models/{model['id']}?new_default_id={other['id']}")

        assert response.status_code == 204

    def test_referenced_skill_cannot_be_deleted(self, client: TestClient, seeded_tools: None, tmp_path: Path) -> None:
        make_skill(client, tmp_path / "skills", "write-guide", "写作指南")
        model = create_model(client)
        create_agent(client, model["id"], skillDirNames=["write-guide"])

        response = client.delete("/api/skills/write-guide")

        assert response.status_code == 409
        assert "写作助手" in response.json()["detail"]

        # 移除绑定后可删除
        agent_id = client.get("/api/agents").json()[0]["id"]
        client.put(f"/api/agents/{agent_id}", json=agent_payload(model["id"], toolKeys=[], skillDirNames=[]))
        assert client.delete("/api/skills/write-guide").status_code == 204

    def test_referenced_mcp_server_cannot_be_deleted(self, client: TestClient, seeded_tools: None) -> None:
        server = make_mcp_server(client, "time-server")
        model = create_model(client)
        create_agent(client, model["id"], mcpServerIds=[server["id"]])

        response = client.delete(f"/api/mcp-servers/{server['id']}")

        assert response.status_code == 409
        assert "写作助手" in response.json()["detail"]

        # 移除绑定后可删除
        agent_id = client.get("/api/agents").json()[0]["id"]
        client.put(f"/api/agents/{agent_id}", json=agent_payload(model["id"], toolKeys=[], mcpServerIds=[]))
        assert client.delete(f"/api/mcp-servers/{server['id']}").status_code == 204

    def test_missing_skill_shows_as_unavailable(self, client: TestClient, seeded_tools: None, db_session: object, tmp_path: Path) -> None:
        """Skill 目录被手动删除（绕过接口）后，绑定保留并标记为不可用。"""
        make_skill(client, tmp_path / "skills", "write-guide", "写作指南")
        model = create_model(client)
        agent = create_agent(client, model["id"], skillDirNames=["write-guide"])

        shutil.rmtree(tmp_path / "skills" / "write-guide")
        client.get("/api/skills")  # 触发扫描，同步清理登记行
        db_session.expire_all()
        assert db_session.execute(select(Skill)).scalars().all() == []

        detail = client.get(f"/api/agents/{agent['id']}").json()
        binding = detail["skillBindings"][0]
        assert binding["name"] is None
        assert binding["available"] is False
        assert detail["skillCount"] == 1  # 数量按实际保存的绑定统计


class TestCompactionConfig:
    """阶段十：上下文压缩配置的默认值、校验与持久化。"""

    def test_defaults_are_enabled(self, client: TestClient, seeded_tools: None) -> None:
        model = create_model(client)
        body = create_agent(client, model["id"])

        assert body["autoCompact"] is True
        assert body["compactThreshold"] == 80
        assert body["compactKeepRounds"] == 4
        assert body["compactSummaryTokens"] == 1000

    def test_validation_errors(self, client: TestClient, seeded_tools: None) -> None:
        model = create_model(client)

        cases = [
            (agent_payload(model["id"], compactThreshold=5), "compactThreshold"),
            (agent_payload(model["id"], compactThreshold=100), "compactThreshold"),
            (agent_payload(model["id"], compactKeepRounds=0), "compactKeepRounds"),
            (agent_payload(model["id"], compactKeepRounds=51), "compactKeepRounds"),
            (agent_payload(model["id"], compactSummaryTokens=50), "compactSummaryTokens"),
            (agent_payload(model["id"], compactSummaryTokens=50000), "compactSummaryTokens"),
            (agent_payload(model["id"], autoCompact=None), "autoCompact"),
        ]
        for payload, field in cases:
            response = client.post("/api/agents", json=payload)
            assert response.status_code == 422, payload
            assert field in response.json()["detail"]["fields"], payload

    def test_update_persists_compaction_config(self, client: TestClient, seeded_tools: None) -> None:
        model = create_model(client)
        agent = create_agent(client, model["id"])

        payload = agent_payload(
            model["id"],
            autoCompact=False,
            compactThreshold=60,
            compactKeepRounds=2,
            compactSummaryTokens=600,
        )
        response = client.put(f"/api/agents/{agent['id']}", json=payload)
        assert response.status_code == 200, response.text
        assert response.json()["autoCompact"] is False
        assert response.json()["compactThreshold"] == 60

        detail = client.get(f"/api/agents/{agent['id']}").json()
        assert detail["autoCompact"] is False
        assert detail["compactThreshold"] == 60
        assert detail["compactKeepRounds"] == 2
        assert detail["compactSummaryTokens"] == 600
