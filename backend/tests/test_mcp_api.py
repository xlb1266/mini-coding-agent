"""MCP 管理接口测试：CRUD、启停、测试连接记录、掩码保护与配置变更标记。"""

import pytest
from fastapi.testclient import TestClient

from app.db.models.mcp_server import McpEnvVar, McpServer
from app.services.mcp_client import McpTestError

ENV_VALUE_MASK = "••••••••"


def server_payload(**overrides: object) -> dict:
    payload: dict = {
        "name": "Weather MCP",
        "description": "天气查询服务",
        "command": "uvx",
        "args": ["-y", "mcp-server-weather"],
        "env": {"WEATHER_API_KEY": "sk-secret-123456"},
        "enabled": False,
    }
    payload.update(overrides)
    return payload


def create_server(client: TestClient, **overrides: object) -> dict:
    response = client.post("/api/mcp-servers", json=server_payload(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


class TestCreate:
    def test_create_returns_masked_env_and_defaults(self, client: TestClient) -> None:
        body = create_server(client)

        assert body["name"] == "Weather MCP"
        assert body["command"] == "uvx"
        assert body["args"] == ["-y", "mcp-server-weather"]
        assert body["env"] == {"WEATHER_API_KEY": ENV_VALUE_MASK}
        assert body["enabled"] is False
        assert body["lastTestStatus"] == "untested"
        assert body["lastTestToolCount"] is None
        assert "sk-secret-123456" not in str(body)

    def test_env_value_stored_encrypted(self, client: TestClient, db_session: object) -> None:
        create_server(client)

        row = db_session.query(McpEnvVar).one()
        assert row.key == "WEATHER_API_KEY"
        assert "sk-secret-123456" not in row.value_blob

    def test_name_and_command_required(self, client: TestClient) -> None:
        response = client.post("/api/mcp-servers", json=server_payload(name="   ", command=" "))
        assert response.status_code == 422
        fields = response.json()["detail"]["fields"]
        assert "请填写名称" in fields["name"]
        assert "请填写启动命令" in fields["command"]

    @pytest.mark.parametrize(
        "env",
        [
            {"BAD KEY": "v"},
            {"A=b": "v"},
            {"K": "   "},
            {"K": ENV_VALUE_MASK},
        ],
    )
    def test_invalid_env_rejected(self, client: TestClient, env: dict) -> None:
        response = client.post("/api/mcp-servers", json=server_payload(env=env))
        assert response.status_code == 422
        assert "env" in response.json()["detail"]["fields"]

    def test_duplicate_name_rejected_case_insensitive(self, client: TestClient) -> None:
        create_server(client)

        response = client.post("/api/mcp-servers", json=server_payload(name="weather mcp"))

        assert response.status_code == 422
        assert "已被其他" in response.json()["detail"]["fields"]["name"]


class TestUpdate:
    def test_update_with_mask_keeps_stored_value(self, client: TestClient, db_session: object) -> None:
        body = create_server(client)
        row = db_session.query(McpEnvVar).one()
        nonce, blob = row.value_nonce, row.value_blob

        response = client.put(
            f"/api/mcp-servers/{body['id']}", json=server_payload(description="新说明")
        )

        assert response.status_code == 200
        db_session.expire_all()
        updated = db_session.query(McpEnvVar).one()
        # 掩码回传表示保留原值：密文不动，原文不变
        assert updated.value_nonce == nonce
        assert updated.value_blob == blob
        assert response.json()["env"] == {"WEATHER_API_KEY": ENV_VALUE_MASK}

    def test_update_with_new_value_replaces(self, client: TestClient, db_session: object) -> None:
        body = create_server(client)

        response = client.put(
            f"/api/mcp-servers/{body['id']}",
            json=server_payload(env={"WEATHER_API_KEY": "sk-new-value-999"}),
        )

        assert response.status_code == 200
        assert "sk-new-value-999" not in response.text
        db_session.expire_all()
        row = db_session.query(McpEnvVar).one()
        assert "sk-new-value-999" not in row.value_blob

    def test_update_omitted_key_removed(self, client: TestClient, db_session: object) -> None:
        body = create_server(client)

        response = client.put(f"/api/mcp-servers/{body['id']}", json=server_payload(env={}))

        assert response.status_code == 200
        assert response.json()["env"] == {}
        db_session.expire_all()
        assert db_session.query(McpEnvVar).count() == 0

    def test_mask_for_unknown_key_rejected(self, client: TestClient) -> None:
        body = create_server(client)

        response = client.put(
            f"/api/mcp-servers/{body['id']}",
            json=server_payload(env={"WEATHER_API_KEY": ENV_VALUE_MASK, "NEW_KEY": ENV_VALUE_MASK}),
        )

        assert response.status_code == 422
        assert "NEW_KEY" in response.json()["detail"]["fields"]["env"]

    def test_update_missing_server_404(self, client: TestClient) -> None:
        response = client.put("/api/mcp-servers/999", json=server_payload())
        assert response.status_code == 404


class TestTestResultLifecycle:
    def record_success(self, client: TestClient, server_id: int, monkeypatch: object) -> None:
        def fake_discover(*args: object, **kwargs: object) -> list[dict]:
            return [
                {
                    "name": "get_weather",
                    "description": "查询天气",
                    "params": [
                        {"name": "city", "type": "string", "required": True, "description": "城市名"}
                    ],
                }
            ]

        monkeypatch.setattr("app.api.routes.mcp.discover_tools", fake_discover)
        response = client.post(f"/api/mcp-servers/{server_id}/test")
        assert response.status_code == 200, response.text

    def test_test_success_records_tools_and_count(
        self, client: TestClient, db_session: object, monkeypatch: object
    ) -> None:
        body = create_server(client)

        self.record_success(client, body["id"], monkeypatch)

        row = client.get("/api/mcp-servers").json()[0]
        assert row["lastTestStatus"] == "success"
        assert row["lastTestToolCount"] == 1
        assert row["lastTestTools"][0]["name"] == "get_weather"
        assert row["lastTestTools"][0]["params"][0]["type"] == "string"
        assert "sk-secret-123456" not in str(row)
        db_session.expire_all()
        assert db_session.get(McpServer, body["id"]).last_test_status == "success"

    def test_test_failure_records_classified_error(
        self, client: TestClient, db_session: object, monkeypatch: object
    ) -> None:
        body = create_server(client)

        def fake_discover(*args: object, **kwargs: object) -> None:
            raise McpTestError("timeout", "连接超时：20 秒内未收到响应", "稍后重试")

        monkeypatch.setattr("app.api.routes.mcp.discover_tools", fake_discover)
        response = client.post(f"/api/mcp-servers/{body['id']}/test")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "failed"
        assert data["errorType"] == "timeout"
        assert data["message"] == "连接超时：20 秒内未收到响应"
        assert data["hint"] == "稍后重试"
        row = client.get("/api/mcp-servers").json()[0]
        assert row["lastTestStatus"] == "failed"
        assert row["lastTestToolCount"] is None
        assert row["lastTestError"] == "连接超时：20 秒内未收到响应"
        db_session.expire_all()
        assert db_session.get(McpServer, body["id"]).last_test_tools_json is None

    def test_test_unknown_server_404(self, client: TestClient) -> None:
        assert client.post("/api/mcp-servers/999/test").status_code == 404

    def test_test_rejects_concurrent_for_same_server(self, client: TestClient) -> None:
        from app.api.routes import mcp as mcp_routes

        body = create_server(client)
        mcp_routes._testing_ids.add(body["id"])
        try:
            response = client.post(f"/api/mcp-servers/{body['id']}/test")
            assert response.status_code == 409
            assert "正在测试" in response.json()["detail"]
        finally:
            mcp_routes._testing_ids.discard(body["id"])

    def test_test_does_not_change_enabled_state(
        self, client: TestClient, monkeypatch: object
    ) -> None:
        body = create_server(client)

        self.record_success(client, body["id"], monkeypatch)

        row = client.get("/api/mcp-servers").json()[0]
        # 测试连接是独立操作：停用状态下可测试，成功也不会自动启用
        assert row["enabled"] is False

    def test_config_change_marks_success_stale(
        self, client: TestClient, monkeypatch: object
    ) -> None:
        body = create_server(client)
        self.record_success(client, body["id"], monkeypatch)

        response = client.put(
            f"/api/mcp-servers/{body['id']}",
            json=server_payload(args=["-y", "other-server"], env={"WEATHER_API_KEY": ENV_VALUE_MASK}),
        )

        assert response.status_code == 200
        row = response.json()
        assert row["lastTestStatus"] == "stale"
        assert row["lastTestToolCount"] is None

    def test_failed_result_also_marked_stale_on_config_change(
        self, client: TestClient, monkeypatch: object
    ) -> None:
        body = create_server(client)

        def fake_discover(*args: object, **kwargs: object) -> None:
            raise McpTestError("timeout", "连接超时", "稍后重试")

        monkeypatch.setattr("app.api.routes.mcp.discover_tools", fake_discover)
        client.post(f"/api/mcp-servers/{body['id']}/test")

        response = client.put(
            f"/api/mcp-servers/{body['id']}",
            json=server_payload(command="node", env={"WEATHER_API_KEY": ENV_VALUE_MASK}),
        )

        assert response.json()["lastTestStatus"] == "stale"

    def test_name_only_change_keeps_success(
        self, client: TestClient, monkeypatch: object
    ) -> None:
        body = create_server(client)
        self.record_success(client, body["id"], monkeypatch)

        response = client.put(
            f"/api/mcp-servers/{body['id']}",
            json=server_payload(description="只改说明", env={"WEATHER_API_KEY": ENV_VALUE_MASK}),
        )

        assert response.json()["lastTestStatus"] == "success"

    def test_config_change_without_test_stays_untested(self, client: TestClient) -> None:
        body = create_server(client)

        response = client.put(
            f"/api/mcp-servers/{body['id']}",
            json=server_payload(command="node", env={"WEATHER_API_KEY": ENV_VALUE_MASK}),
        )

        assert response.json()["lastTestStatus"] == "untested"


class TestEnabled:
    def test_enable_and_disable_persist(self, client: TestClient, db_session: object) -> None:
        body = create_server(client)

        response = client.put(f"/api/mcp-servers/{body['id']}/enabled", json={"enabled": True})
        assert response.status_code == 200
        assert response.json()["enabled"] is True
        db_session.expire_all()
        assert db_session.get(McpServer, body["id"]).enabled is True

        response = client.put(f"/api/mcp-servers/{body['id']}/enabled", json={"enabled": False})
        assert response.json()["enabled"] is False
        db_session.expire_all()
        assert db_session.get(McpServer, body["id"]).enabled is False

    def test_enabled_unknown_server_404(self, client: TestClient) -> None:
        response = client.put("/api/mcp-servers/999/enabled", json={"enabled": True})
        assert response.status_code == 404


class TestListAndDelete:
    def test_list_orders_by_creation_and_masks_env(self, client: TestClient) -> None:
        first = create_server(client)
        second = create_server(client, name="Other MCP")

        data = client.get("/api/mcp-servers").json()

        assert [item["id"] for item in data] == [first["id"], second["id"]]
        assert data[0]["env"] == {"WEATHER_API_KEY": ENV_VALUE_MASK}
        assert "sk-secret-123456" not in str(data)

    def test_get_detail_masks_env(self, client: TestClient) -> None:
        body = create_server(client)

        data = client.get(f"/api/mcp-servers/{body['id']}").json()

        assert data["env"] == {"WEATHER_API_KEY": ENV_VALUE_MASK}
        assert "sk-secret-123456" not in str(data)

    def test_delete_removes_server_and_env_rows(self, client: TestClient, db_session: object) -> None:
        body = create_server(client)

        response = client.delete(f"/api/mcp-servers/{body['id']}")

        assert response.status_code == 204
        db_session.expire_all()
        assert db_session.get(McpServer, body["id"]) is None
        assert db_session.query(McpEnvVar).count() == 0

        assert client.delete(f"/api/mcp-servers/{body['id']}").status_code == 404
