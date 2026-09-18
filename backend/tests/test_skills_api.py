"""Skills 管理接口测试：扫描列表、ZIP 导入校验、编辑写回、启停持久化与删除。"""

import io
import shutil
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SKILL_MD = "---\nname: 网页总结\ndescription: 总结网页文章要点。\n---\n\n# 网页总结\n\n按要求输出。"


def make_zip(files: dict[str, bytes | str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data if isinstance(data, bytes) else data.encode("utf-8"))
    return buffer.getvalue()


def import_zip(client: TestClient, data: bytes, filename: str = "skill.zip") -> object:
    return client.post(
        "/api/skills/import", files={"file": (filename, data, "application/zip")}
    )


def skills_dir(tmp_path: Path) -> Path:
    # 与 conftest 的 override_skills_dir 指向同一个临时目录
    return tmp_path / "skills"


class TestList:
    def test_empty_list(self, client: TestClient) -> None:
        response = client.get("/api/skills")

        assert response.status_code == 200
        assert response.json() == []

    def test_manual_dir_appears_after_scan(self, client: TestClient, tmp_path: Path) -> None:
        skill_dir = skills_dir(tmp_path) / "hand-made"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")

        body = client.get("/api/skills").json()

        assert len(body) == 1
        assert body[0]["dirName"] == "hand-made"
        assert body[0]["name"] == "网页总结"
        assert body[0]["description"] == "总结网页文章要点。"
        assert body[0]["enabled"] is True

    def test_dir_without_skill_md_ignored(self, client: TestClient, tmp_path: Path) -> None:
        (skills_dir(tmp_path) / "broken").mkdir(parents=True)
        (skills_dir(tmp_path) / "broken" / "README.md").write_text("x", encoding="utf-8")

        assert client.get("/api/skills").json() == []

    def test_deleted_dir_removed_from_list(self, client: TestClient, tmp_path: Path) -> None:
        skill_dir = skills_dir(tmp_path) / "gone"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
        assert len(client.get("/api/skills").json()) == 1

        shutil.rmtree(skill_dir)
        assert client.get("/api/skills").json() == []


class TestImport:
    def test_import_wrapped_layout(self, client: TestClient, tmp_path: Path) -> None:
        response = import_zip(client, make_zip({"my-skill/SKILL.md": SKILL_MD}))

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["dirName"] == "my-skill"
        assert body["name"] == "网页总结"
        assert body["enabled"] is True
        assert (skills_dir(tmp_path) / "my-skill" / "SKILL.md").is_file()

    def test_import_root_layout_uses_frontmatter_name(self, client: TestClient) -> None:
        response = import_zip(client, make_zip({"SKILL.md": SKILL_MD}), "随便的文件名.zip")

        assert response.status_code == 201
        assert response.json()["dirName"] == "网页总结"

    def test_import_keeps_extra_files(self, client: TestClient, tmp_path: Path) -> None:
        data = make_zip({"pack/SKILL.md": SKILL_MD, "pack/assets/guide.txt": "说明"})

        assert import_zip(client, data).status_code == 201
        assert (skills_dir(tmp_path) / "pack" / "assets" / "guide.txt").read_text(
            encoding="utf-8"
        ) == "说明"

    def test_import_gbk_encoded_skill_md(self, client: TestClient) -> None:
        data = make_zip({"demo/SKILL.md": SKILL_MD.encode("gbk")})

        assert import_zip(client, data).status_code == 201

    def test_import_backslash_paths_normalized(self, client: TestClient) -> None:
        data = make_zip({"demo\\SKILL.md": SKILL_MD, "demo\\assets\\a.txt": "x"})

        assert import_zip(client, data).status_code == 201

    def test_import_missing_skill_md_rejected(self, client: TestClient) -> None:
        data = make_zip({"demo/README.md": "没有 SKILL.md"})

        response = import_zip(client, data)

        assert response.status_code == 422
        assert "SKILL.md" in response.json()["detail"]

    def test_import_not_zip_rejected(self, client: TestClient) -> None:
        response = import_zip(client, b"this is not a zip file")

        assert response.status_code == 422

    def test_import_empty_skill_md_rejected(self, client: TestClient) -> None:
        data = make_zip({"demo/SKILL.md": "   \n  "})

        assert import_zip(client, data).status_code == 422

    def test_import_unsafe_path_rejected(self, client: TestClient, tmp_path: Path) -> None:
        data = make_zip({"demo/SKILL.md": SKILL_MD, "../evil.txt": "越权写入"})

        response = import_zip(client, data)

        assert response.status_code == 422
        assert "不安全" in response.json()["detail"]
        # 压缩包内的一切都不应被写出，包括 skills 目录之外
        assert not (tmp_path / "evil.txt").exists()
        assert not (skills_dir(tmp_path) / "demo").exists()

    def test_import_multiple_top_folders_rejected(self, client: TestClient) -> None:
        data = make_zip({"a/SKILL.md": SKILL_MD, "b/SKILL.md": SKILL_MD})

        response = import_zip(client, data)

        assert response.status_code == 422

    def test_import_duplicate_not_overwritten(self, client: TestClient, tmp_path: Path) -> None:
        assert import_zip(client, make_zip({"demo/SKILL.md": SKILL_MD})).status_code == 201
        target = skills_dir(tmp_path) / "demo" / "SKILL.md"
        before = target.read_text(encoding="utf-8")

        response = import_zip(client, make_zip({"demo/SKILL.md": "---\nname: 另一个\n---\n\nx"}))

        assert response.status_code == 409
        assert "同名" in response.json()["detail"]
        assert target.read_text(encoding="utf-8") == before


class TestEnabled:
    def test_disable_persists_across_scans(self, client: TestClient, tmp_path: Path) -> None:
        import_zip(client, make_zip({"demo/SKILL.md": SKILL_MD}))

        response = client.put("/api/skills/demo/enabled", json={"enabled": False})

        assert response.status_code == 200
        assert response.json()["enabled"] is False
        # 文件保留；重新扫描（含文件变更）后状态仍然保留
        (skills_dir(tmp_path) / "demo" / "SKILL.md").write_text(SKILL_MD + "\n补充", encoding="utf-8")
        body = client.get("/api/skills").json()
        assert body[0]["enabled"] is False
        assert (skills_dir(tmp_path) / "demo" / "SKILL.md").is_file()

    def test_enable_again(self, client: TestClient) -> None:
        import_zip(client, make_zip({"demo/SKILL.md": SKILL_MD}))
        client.put("/api/skills/demo/enabled", json={"enabled": False})

        body = client.put("/api/skills/demo/enabled", json={"enabled": True}).json()

        assert body["enabled"] is True

    def test_enabled_missing_skill_404(self, client: TestClient) -> None:
        response = client.put("/api/skills/nope/enabled", json={"enabled": False})

        assert response.status_code == 404


class TestUpdate:
    def test_update_writes_skill_file(self, client: TestClient, tmp_path: Path) -> None:
        import_zip(client, make_zip({"demo/SKILL.md": SKILL_MD}))

        response = client.put(
            "/api/skills/demo",
            json={"name": "改名后", "description": "新的说明", "instructions": "# 新指令\n\n内容"},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["name"] == "改名后"
        assert body["dirName"] == "demo"

        raw = (skills_dir(tmp_path) / "demo" / "SKILL.md").read_text(encoding="utf-8")
        assert "name: 改名后" in raw
        assert "description: 新的说明" in raw
        assert "# 新指令" in raw

        detail = client.get("/api/skills/demo").json()
        assert detail["instructions"].startswith("# 新指令")

    def test_update_refreshes_list_order(self, client: TestClient) -> None:
        import_zip(client, make_zip({"old/SKILL.md": SKILL_MD}, ))
        import_zip(client, make_zip({"newer/SKILL.md": SKILL_MD}))
        # 改动较早的 Skill 后，它应排到最前（按更新时间倒序）
        client.put(
            "/api/skills/old",
            json={"name": "网页总结", "description": "总结网页文章要点。", "instructions": "改写正文"},
        )

        names = [row["dirName"] for row in client.get("/api/skills").json()]
        assert names == ["old", "newer"]

    def test_update_validation_errors(self, client: TestClient) -> None:
        import_zip(client, make_zip({"demo/SKILL.md": SKILL_MD}))

        response = client.put(
            "/api/skills/demo", json={"name": "  ", "description": "", "instructions": ""}
        )

        assert response.status_code == 422
        fields = response.json()["detail"]["fields"]
        assert set(fields) == {"name", "description", "instructions"}

    def test_update_missing_skill_404(self, client: TestClient) -> None:
        response = client.put(
            "/api/skills/nope", json={"name": "x", "description": "y", "instructions": "z"}
        )

        assert response.status_code == 404


class TestDetail:
    def test_detail_returns_instructions(self, client: TestClient) -> None:
        import_zip(client, make_zip({"demo/SKILL.md": SKILL_MD}))

        body = client.get("/api/skills/demo").json()

        assert body["instructions"].startswith("# 网页总结")
        assert body["enabled"] is True

    def test_detail_missing_404(self, client: TestClient) -> None:
        assert client.get("/api/skills/nope").status_code == 404

    def test_detail_rejects_path_traversal(self, client: TestClient) -> None:
        assert client.get("/api/skills/..").status_code == 404


class TestDelete:
    def test_delete_removes_dir_and_row(self, client: TestClient, tmp_path: Path) -> None:
        import_zip(client, make_zip({"demo/SKILL.md": SKILL_MD}))

        response = client.delete("/api/skills/demo")

        assert response.status_code == 204
        assert not (skills_dir(tmp_path) / "demo").exists()
        assert client.get("/api/skills").json() == []

    def test_delete_missing_404(self, client: TestClient) -> None:
        assert client.delete("/api/skills/nope").status_code == 404
