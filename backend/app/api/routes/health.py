"""健康检查路由。"""

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db

router = APIRouter(tags=["health"])


@router.get("/health")
def read_health(db: Session = Depends(get_db)) -> dict[str, str]:
    """返回服务与数据库连接状态，用于前端与部署环境探活。"""
    try:
        db.execute(text("SELECT 1"))
        database = "ok"
    except Exception:  # noqa: BLE001 - 健康检查需要吞掉所有数据库异常并如实上报
        database = "unavailable"

    return {
        "status": "ok",
        "app": settings.app_name,
        "version": settings.app_version,
        "database": database,
    }
