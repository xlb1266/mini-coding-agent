"""本机系统端点测试：目录选择窗口的返回与错误路径（弹窗函数打桩）。"""

import pytest
from fastapi.testclient import TestClient

from app.services.directory_picker import DirectoryPickerError


@pytest.fixture()
def patch_ask(monkeypatch: pytest.MonkeyPatch):
    """替换掉真正的 tkinter 弹窗，按需注入返回值或异常。"""

    def _patch(return_value: str | None = None, raises: Exception | None = None):
        def fake_ask(title: str = "选择文件夹") -> str | None:
            if raises is not None:
                raise raises
            return return_value

        monkeypatch.setattr("app.api.routes.system.ask_directory", fake_ask)

    return _patch


def test_picked_directory_returned(client: TestClient, patch_ask) -> None:
    patch_ask(return_value=r"D:\projects\demo")

    response = client.post("/api/system/pick-directory")

    assert response.status_code == 200
    assert response.json() == {"path": r"D:\projects\demo"}


def test_cancelled_returns_null_path(client: TestClient, patch_ask) -> None:
    patch_ask(return_value=None)

    response = client.post("/api/system/pick-directory")

    assert response.status_code == 200
    assert response.json() == {"path": None}


def test_picker_failure_maps_to_500(client: TestClient, patch_ask) -> None:
    patch_ask(raises=DirectoryPickerError("无法打开系统目录选择窗口，请手动输入工作区目录路径"))

    response = client.post("/api/system/pick-directory")

    assert response.status_code == 500
    assert "手动输入" in response.json()["detail"]
