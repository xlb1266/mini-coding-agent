"""健康检查接口测试。"""

from fastapi.testclient import TestClient


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["app"] == "mini-agent"
    assert body["database"] == "ok"


def test_health_can_be_called_repeatedly(client: TestClient) -> None:
    for _ in range(3):
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
