"""pytest 共享夹具：使用独立的内存数据库与临时密钥盒，避免污染本地数据。"""

from collections.abc import Generator
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.secret_box import SecretBox, get_secret_box
from app.api.routes.skills import get_skills_dir
from app.db.base import Base
from app.db.models.tool import Tool
from app.db.session import get_db, get_session_factory
from app.main import app
from app.services.tools.registry import builtin_tool_rows


@pytest.fixture()
def db_engine() -> Generator[object, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def db_session(db_engine: object) -> Generator[Session, None, None]:
    factory = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def seeded_tools(db_engine: object) -> Generator[None, None, None]:
    """测试库不走迁移，按迁移 0005 相同的注册表定义写入内置工具。"""
    factory = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    session = factory()
    try:
        for row in builtin_tool_rows():
            session.add(Tool(**row))
        session.commit()
        yield
    finally:
        session.close()


@pytest.fixture()
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把文件读写工具的授权目录指向临时目录，避免测试碰到真实工作区。"""
    target = tmp_path / "tool-workspace"
    from app.core.config import settings

    monkeypatch.setattr(settings, "tool_workspace_dir", target)
    return target


@pytest.fixture()
def client(db_engine: object, tmp_path: Path) -> Generator[TestClient, None, None]:
    testing_session_local = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)

    def override_get_db() -> Generator[Session, None, None]:
        db = testing_session_local()
        try:
            yield db
        finally:
            db.close()

    def override_secret_box() -> SecretBox:
        return SecretBox(tmp_path / "test-secret.key")

    def override_skills_dir() -> Path:
        return tmp_path / "skills"

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_session_factory] = lambda: testing_session_local
    app.dependency_overrides[get_secret_box] = override_secret_box
    app.dependency_overrides[get_skills_dir] = override_skills_dir
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
