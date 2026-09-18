"""测试连接接口的路由级测试（替换上游调用，不发生真实网络请求）。"""

from fastapi.testclient import TestClient

from app.services.model_client import ModelCallError

from tests.test_models_api import create_model


def test_test_connection_success(client: TestClient, monkeypatch: object) -> None:
    body = create_model(client)
    captured: dict = {}

    def fake_chat_completion(**kwargs: object) -> str:
        captured.update(kwargs)
        return "连接成功"

    monkeypatch.setattr("app.api.routes.models.chat_completion", fake_chat_completion)

    response = client.post(f"/api/models/{body['id']}/test")

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["reply"] == "连接成功"
    # 上游收到的密钥应是解密后的原文，max_tokens 不超过上限
    assert captured["api_key"] == "sk-test-123456"
    assert 0 < captured["max_tokens"] <= 64


def test_test_connection_failure_returns_classified_error(
    client: TestClient, monkeypatch: object
) -> None:
    body = create_model(client)

    def fake_chat_completion(**kwargs: object) -> None:
        raise ModelCallError("auth", "密钥无效或没有访问权限", "请检查 API Key")

    monkeypatch.setattr("app.api.routes.models.chat_completion", fake_chat_completion)

    response = client.post(f"/api/models/{body['id']}/test")

    assert response.status_code == 200  # 测试结果属于业务响应，而非接口错误
    data = response.json()
    assert data["ok"] is False
    assert data["errorType"] == "auth"
    assert data["message"] == "密钥无效或没有访问权限"
    assert data["hint"] == "请检查 API Key"
    assert "sk-test-123456" not in response.text


def test_test_connection_unknown_model_404(client: TestClient) -> None:
    assert client.post("/api/models/999/test").status_code == 404
