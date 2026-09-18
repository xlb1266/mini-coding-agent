"""SQLAlchemy 引擎与会话管理。"""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

# SQLite 需要允许跨线程使用连接（FastAPI 的同步路由运行在线程池中）
connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}

engine = create_engine(settings.database_url, connect_args=connect_args)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖：提供一个请求级会话，请求结束后自动关闭。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_session_factory() -> sessionmaker:
    """FastAPI 依赖：给独立于请求生命周期的后台任务（聊天生成）用的会话工厂。

    测试时用 dependency_overrides 换成测试库的工厂，避免后台任务写坏本地库。
    """
    return SessionLocal
