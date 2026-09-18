"""模型管理接口测试：CRUD、默认模型一致性、密钥保护与参数校验。"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models.model_config import ModelConfig, ModelSecret
from app.db.session import get_db
from app.main import app


def model_payload(**overrides: object) -> dict:
    payload: dict = {
        "displayName": "DeepSeek-V3",
        "modelIdentifier": "deepseek-chat",
        "baseUrl": "https://api.deepseek.com/v1",
        "apiKey": "sk-test-123456",
        "contextLength": 32768,
        "maxOutputTokens": 4096,
        "temperature": 0.7,
        "inputPrice": 2.0,
        "outputPrice": 8.0,
        "cachedInputPrice": 0.5,
    }
    payload.update(overrides)
    return payload


def create_model(client: TestClient, **overrides: object) -> dict:
    response = client.post("/api/models", json=model_payload(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


def current_default(db_session: object) -> ModelConfig | None:
    return db_session.query(ModelConfig).filter(ModelConfig.is_default.is_(True)).first()


class TestCreate:
    def test_first_model_becomes_default(self, client: TestClient, db_session: object) -> None:
        body = create_model(client)

        assert body["isDefault"] is True
        assert body["hasApiKey"] is True
        assert body["displayName"] == "DeepSeek-V3"
        assert body["baseUrl"] == "https://api.deepseek.com/v1"
        assert current_default(db_session) is not None

    def test_second_model_not_default(self, client: TestClient) -> None:
        create_model(client)
        body = create_model(client, displayName="Qwen-Max", modelIdentifier="qwen-max")

        assert body["isDefault"] is False

    def test_key_never_in_response_or_database(self, client: TestClient, db_session: object) -> None:
        body = create_model(client)

        assert "sk-test-123456" not in str(body)
        assert "apiKey" not in body
        row = db_session.query(ModelSecret).one()
        blob: str = row.key_blob
        assert "sk-test-123456" not in blob

    def test_create_without_api_key_allowed(self, client: TestClient) -> None:
        payload = model_payload()
        del payload["apiKey"]
        response = client.post("/api/models", json=payload)

        assert response.status_code == 201
        assert response.json()["hasApiKey"] is False

    def test_base_url_trailing_slash_normalized(self, client: TestClient) -> None:
        body = create_model(client, baseUrl="https://api.deepseek.com/v1/")

        assert body["baseUrl"] == "https://api.deepseek.com/v1"


class TestUpdate:
    def test_update_without_key_keeps_secret(self, client: TestClient, db_session: object) -> None:
        body = create_model(client)
        secret_id = db_session.query(ModelSecret).one().id

        response = client.put(f"/api/models/{body['id']}", json=model_payload(displayName="改名"))

        assert response.status_code == 200
        updated = response.json()
        assert updated["displayName"] == "改名"
        assert updated["hasApiKey"] is True
        assert db_session.get(ModelSecret, secret_id) is not None

    def test_update_with_new_key_replaces_secret(self, client: TestClient) -> None:
        body = create_model(client)

        response = client.put(f"/api/models/{body['id']}", json=model_payload(apiKey="sk-new-key"))

        assert response.status_code == 200
        assert response.json()["hasApiKey"] is True

    def test_update_missing_model_404(self, client: TestClient) -> None:
        response = client.put("/api/models/999", json=model_payload())
        assert response.status_code == 404


class TestDefault:
    def test_set_default_switches(self, client: TestClient, db_session: object) -> None:
        first = create_model(client)
        second = create_model(client, displayName="Qwen", modelIdentifier="qwen-max")

        response = client.put(f"/api/models/{second['id']}/default")

        assert response.status_code == 200
        assert response.json()["isDefault"] is True
        db_session.expire_all()
        assert db_session.get(ModelConfig, first["id"]).is_default is False
        assert db_session.get(ModelConfig, second["id"]).is_default is True

    def test_list_orders_default_first(self, client: TestClient) -> None:
        first = create_model(client)
        second = create_model(client, displayName="Qwen", modelIdentifier="qwen-max")
        client.put(f"/api/models/{second['id']}/default")

        listing = client.get("/api/models").json()

        assert [m["id"] for m in listing] == [second["id"], first["id"]]

    def test_set_default_unknown_model_404(self, client: TestClient) -> None:
        assert client.put("/api/models/999/default").status_code == 404


class TestDelete:
    def test_delete_non_default(self, client: TestClient, db_session: object) -> None:
        first = create_model(client)
        second = create_model(client, displayName="Qwen", modelIdentifier="qwen-max")

        response = client.delete(f"/api/models/{second['id']}")

        assert response.status_code == 204
        db_session.expire_all()
        assert db_session.get(ModelConfig, second["id"]) is None
        # 默认模型不受影响，密钥行随删除清理
        assert db_session.get(ModelConfig, first["id"]) is not None
        assert len(db_session.scalars(select(ModelSecret)).all()) == 1

    def test_delete_default_requires_new_default(self, client: TestClient) -> None:
        create_model(client)
        second = create_model(client, displayName="Qwen", modelIdentifier="qwen-max")
        client.put(f"/api/models/{second['id']}/default")

        response = client.delete(f"/api/models/{second['id']}")

        assert response.status_code == 409

    def test_delete_default_with_transfer(self, client: TestClient, db_session: object) -> None:
        first = create_model(client)
        second = create_model(client, displayName="Qwen", modelIdentifier="qwen-max")
        client.put(f"/api/models/{second['id']}/default")

        response = client.delete(f"/api/models/{second['id']}", params={"new_default_id": first["id"]})

        assert response.status_code == 204
        db_session.expire_all()
        assert db_session.get(ModelConfig, first["id"]).is_default is True
        assert db_session.get(ModelConfig, second["id"]) is None
        assert current_default(db_session) is not None

    def test_delete_default_with_unknown_transfer_target(self, client: TestClient) -> None:
        body = create_model(client)
        other = create_model(client, displayName="Qwen", modelIdentifier="qwen-max")
        client.put(f"/api/models/{other['id']}/default")

        response = client.delete(f"/api/models/{other['id']}", params={"new_default_id": 999})

        assert response.status_code == 422

    def test_delete_last_model_clears_default(self, client: TestClient, db_session: object) -> None:
        body = create_model(client)

        response = client.delete(f"/api/models/{body['id']}")

        assert response.status_code == 204
        db_session.expire_all()
        assert db_session.scalars(select(ModelConfig)).all() == []
        assert db_session.scalars(select(ModelSecret)).all() == []
        assert client.get("/api/models").json() == []

    def test_delete_unknown_model_404(self, client: TestClient) -> None:
        assert client.delete("/api/models/999").status_code == 404


class TestValidation:
    @pytest.mark.parametrize(
        ("overrides", "field"),
        [
            ({"displayName": "   "}, "displayName"),
            ({"modelIdentifier": ""}, "modelIdentifier"),
            ({"baseUrl": ""}, "baseUrl"),
            ({"baseUrl": "api.deepseek.com/v1"}, "baseUrl"),
            ({"baseUrl": "ftp://api.deepseek.com/v1"}, "baseUrl"),
            ({"baseUrl": "https://api.deepseek.com/v1/chat/completions"}, "baseUrl"),
            ({"contextLength": 0}, "contextLength"),
            ({"contextLength": -100}, "contextLength"),
            ({"contextLength": 4096.5}, "contextLength"),
            ({"maxOutputTokens": 0}, "maxOutputTokens"),
            ({"maxOutputTokens": 32769}, "maxOutputTokens"),
            ({"temperature": -0.1}, "temperature"),
            ({"temperature": 2.1}, "temperature"),
            ({"inputPrice": -1}, "inputPrice"),
            ({"outputPrice": -0.01}, "outputPrice"),
            ({"cachedInputPrice": -1}, "cachedInputPrice"),
        ],
    )
    def test_field_errors_reported(self, client: TestClient, overrides: dict, field: str) -> None:
        response = client.post("/api/models", json=model_payload(**overrides))

        assert response.status_code == 422
        detail = response.json()["detail"]
        assert field in detail["fields"], detail

    def test_context_type_error_maps_to_chinese(self, client: TestClient) -> None:
        payload = model_payload()
        payload["contextLength"] = "abc"
        response = client.post("/api/models", json=payload)

        assert response.status_code == 422
        assert "正整数" in response.json()["detail"]["fields"]["contextLength"]

    def test_zero_price_is_valid_and_kept(self, client: TestClient) -> None:
        body = create_model(client, inputPrice=0, cachedInputPrice=None)

        assert body["inputPrice"] == 0
        assert body["cachedInputPrice"] is None


class TestGet:
    def test_get_model(self, client: TestClient) -> None:
        body = create_model(client)

        detail = client.get(f"/api/models/{body['id']}").json()

        assert detail["id"] == body["id"]
        assert "sk-test-123456" not in str(detail)
        assert "apiKey" not in detail

    def test_get_unknown_model_404(self, client: TestClient) -> None:
        assert client.get("/api/models/999").status_code == 404
