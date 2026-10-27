"""工具管理接口测试：列表、详情、内置工具保护、启停与统一执行入口。"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.models.tool import Tool


def tool_by_key(client: TestClient, key: str) -> dict:
    listing = client.get("/api/tools").json()
    return next(tool for tool in listing if tool["key"] == key)


class TestList:
    def test_builtin_tools_seeded(self, client: TestClient, seeded_tools: None) -> None:
        listing = client.get("/api/tools").json()

        assert [tool["key"] for tool in listing] == [
            "builtin_current_time",
            "builtin_shell",
            "builtin_file",
            "builtin_edit_file",
            "builtin_list_dir",
            "builtin_grep",
        ]
        assert all(tool["isBuiltin"] and tool["enabled"] for tool in listing)
        assert [tool["name"] for tool in listing] == [
            "当前时间",
            "Shell 命令执行",
            "文件读写",
            "文件编辑",
            "目录列表",
            "内容搜索",
        ]

    def test_list_without_seed_is_empty(self, client: TestClient) -> None:
        assert client.get("/api/tools").json() == []

    def test_detail_includes_parameter_docs(self, client: TestClient, seeded_tools: None) -> None:
        shell = tool_by_key(client, "builtin_shell")

        detail = client.get(f"/api/tools/{shell['id']}").json()

        assert [param["name"] for param in detail["parameters"]] == [
            "action", "command", "timeout", "taskId",
        ]
        action = detail["parameters"][0]
        assert action["required"] is False
        assert action["type"] == "string"
        assert action["description"]
        timeout = detail["parameters"][2]
        assert timeout["required"] is False
        # 工具说明包含适用场景 / 输入要求 / 使用限制三部分
        for section in ("适用场景", "输入要求", "使用限制"):
            assert section in detail["description"], detail["description"]

    def test_unknown_tool_404(self, client: TestClient, seeded_tools: None) -> None:
        assert client.get("/api/tools/999").status_code == 404


class TestBuiltinProtection:
    def test_cannot_delete_builtin(self, client: TestClient, seeded_tools: None, db_session: Session) -> None:
        shell = tool_by_key(client, "builtin_shell")

        response = client.delete(f"/api/tools/{shell['id']}")

        assert response.status_code == 403
        db_session.expire_all()
        assert db_session.get(Tool, shell["id"]) is not None

    def test_cannot_rename_builtin(self, client: TestClient, seeded_tools: None) -> None:
        shell = tool_by_key(client, "builtin_shell")

        response = client.put(f"/api/tools/{shell['id']}", json={"name": "新名字"})

        assert response.status_code == 403

    def test_cannot_change_builtin_description(self, client: TestClient, seeded_tools: None) -> None:
        shell = tool_by_key(client, "builtin_shell")

        response = client.put(f"/api/tools/{shell['id']}", json={"description": "新的用途"})

        assert response.status_code == 403

    def test_same_name_and_description_accepted(self, client: TestClient, seeded_tools: None) -> None:
        shell = tool_by_key(client, "builtin_shell")

        response = client.put(
            f"/api/tools/{shell['id']}",
            json={"name": shell["name"], "description": shell["description"]},
        )

        assert response.status_code == 200


class TestToggle:
    def test_disable_then_enable(self, client: TestClient, seeded_tools: None, db_session: Session) -> None:
        shell = tool_by_key(client, "builtin_shell")

        disabled = client.put(f"/api/tools/{shell['id']}", json={"enabled": False}).json()

        assert disabled["enabled"] is False
        db_session.expire_all()
        assert db_session.get(Tool, shell["id"]).enabled is False

        enabled = client.put(f"/api/tools/{shell['id']}", json={"enabled": True}).json()

        assert enabled["enabled"] is True

    def test_blank_name_rejected(self, client: TestClient, seeded_tools: None) -> None:
        shell = tool_by_key(client, "builtin_shell")

        assert client.put(f"/api/tools/{shell['id']}", json={"name": "   "}).status_code == 422


class TestExecute:
    def test_current_time_ok(self, client: TestClient, seeded_tools: None) -> None:
        tool = tool_by_key(client, "builtin_current_time")

        response = client.post(
            f"/api/tools/{tool['id']}/execute", json={"params": {"timezone": "Asia/Shanghai"}}
        )
        body = response.json()

        assert response.status_code == 200
        assert body["ok"] is True
        assert body["result"]["timezone"] == "Asia/Shanghai"
        assert body["result"]["utcOffset"] == "+08:00"
        assert body["result"]["weekday"] in ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")

    def test_invalid_timezone_reports_field_reason(self, client: TestClient, seeded_tools: None) -> None:
        tool = tool_by_key(client, "builtin_current_time")

        body = client.post(
            f"/api/tools/{tool['id']}/execute", json={"params": {"timezone": "CST"}}
        ).json()

        assert body["ok"] is False
        assert body["error"]["type"] == "invalid_params"
        assert "IANA" in body["error"]["fields"]["timezone"]

    def test_disabled_tool_reports_disabled(self, client: TestClient, seeded_tools: None) -> None:
        tool = tool_by_key(client, "builtin_shell")
        client.put(f"/api/tools/{tool['id']}", json={"enabled": False})

        body = client.post(
            f"/api/tools/{tool['id']}/execute", json={"params": {"command": "echo hi"}}
        ).json()

        assert body["ok"] is False
        assert body["error"]["type"] == "disabled"

    def test_shell_roundtrip(self, client: TestClient, seeded_tools: None) -> None:
        tool = tool_by_key(client, "builtin_shell")

        body = client.post(
            f"/api/tools/{tool['id']}/execute", json={"params": {"command": "echo mini-agent-ok"}}
        ).json()

        assert body["ok"] is True
        assert body["result"]["exitCode"] == 0
        assert body["result"]["success"] is True
        assert "mini-agent-ok" in body["result"]["stdout"]

    def test_blocked_command(self, client: TestClient, seeded_tools: None) -> None:
        tool = tool_by_key(client, "builtin_shell")

        body = client.post(
            f"/api/tools/{tool['id']}/execute", json={"params": {"command": "rm -rf /"}}
        ).json()

        assert body["ok"] is False
        assert body["error"]["type"] == "blocked"
        assert body["error"]["message"]

    def test_missing_required_param(self, client: TestClient, seeded_tools: None) -> None:
        tool = tool_by_key(client, "builtin_shell")

        body = client.post(f"/api/tools/{tool['id']}/execute", json={"params": {}}).json()

        assert body["ok"] is False
        assert body["error"]["type"] == "invalid_params"
        assert "command" in body["error"]["fields"]

    def test_unknown_param_rejected(self, client: TestClient, seeded_tools: None) -> None:
        tool = tool_by_key(client, "builtin_current_time")

        body = client.post(f"/api/tools/{tool['id']}/execute", json={"params": {"foo": "bar"}}).json()

        assert body["ok"] is False
        assert body["error"]["fields"]["foo"] == "不支持的工具参数"

    def test_file_write_then_read(
        self, client: TestClient, seeded_tools: None, workspace: object
    ) -> None:
        tool = tool_by_key(client, "builtin_file")

        write = client.post(
            f"/api/tools/{tool['id']}/execute",
            json={"params": {"action": "write", "path": "notes/a.md", "content": "你好", "mode": "overwrite"}},
        ).json()
        read = client.post(
            f"/api/tools/{tool['id']}/execute",
            json={"params": {"action": "read", "path": "notes/a.md"}},
        ).json()

        assert write["ok"] is True
        assert write["result"]["outcome"] == "created"
        assert read["ok"] is True
        assert read["result"]["content"] == "你好"

    def test_execute_unknown_tool_404(self, client: TestClient, seeded_tools: None) -> None:
        assert client.post("/api/tools/999/execute", json={"params": {}}).status_code == 404
