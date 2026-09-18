"""FastAPI 应用入口：组装配置、中间件与路由。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import agents, chat, evaluations, health, mcp, models, runs, skills, tools
from app.core.config import settings
from app.db.session import get_session_factory
from app.schemas.agent import FIELD_ERROR_MESSAGES as AGENT_FIELD_ERRORS
from app.schemas.chat import FIELD_ERROR_MESSAGES as CHAT_FIELD_ERRORS
from app.schemas.evaluation import FIELD_ERROR_MESSAGES as EVALUATION_FIELD_ERRORS
from app.schemas.mcp import FIELD_ERROR_MESSAGES as MCP_FIELD_ERRORS
from app.schemas.model import FIELD_ERROR_MESSAGES as MODEL_FIELD_ERRORS
from app.schemas.skill import FIELD_ERROR_MESSAGES as SKILL_FIELD_ERRORS
from app.services.chat_generation import generation_manager
from app.services.eval_runner import evaluation_manager
from app.services.eval_seed import ensure_example_dataset
from app.services.run_recorder import mark_interrupted_runs

# 全部模块的字段级错误提示合并表：Pydantic 校验失败时按字段名查这里
FIELD_ERROR_MESSAGES = {
    **MODEL_FIELD_ERRORS,
    **SKILL_FIELD_ERRORS,
    **MCP_FIELD_ERRORS,
    **AGENT_FIELD_ERRORS,
    **CHAT_FIELD_ERRORS,
    **EVALUATION_FIELD_ERRORS,
}


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """把 Pydantic 类型/缺参错误转成与业务校验一致的 {"message", "fields"} 结构。"""
    fields: dict[str, str] = {}
    for error in exc.errors():
        loc = error.get("loc", [])
        field = loc[-1] if loc and isinstance(loc[-1], str) else ""
        if field and field not in fields:
            fields[field] = FIELD_ERROR_MESSAGES.get(field, "填写格式不正确")
    return JSONResponse(
        status_code=422,
        content={"detail": {"message": "表单校验未通过，请检查填写内容", "fields": fields}},
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """启动时恢复遗留记录并补种示例数据集；关闭时取消仍在运行的任务。

    后端重启或异常退出后，上一进程里 running 的 Run 无法继续：启动时
    统一标记为失败并注明"运行中断"，不能永久停留在运行中；评测任务
    同理标记为已中断（保留已有结果，不做自动续跑）。示例评测数据集按
    稳定标识幂等补种（已存在时不覆盖用户修改）。会话工厂与请求依赖
    同源（测试时随 dependency_overrides 一起换到测试库）。
    """
    factory = app.dependency_overrides.get(get_session_factory, get_session_factory)
    mark_interrupted_runs(factory())
    evaluation_manager.recover_interrupted(factory())
    ensure_example_dataset(factory())
    yield
    await generation_manager.close()
    await evaluation_manager.close()


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router, prefix="/api")
    app.include_router(models.router, prefix="/api")
    app.include_router(tools.router, prefix="/api")
    app.include_router(skills.router, prefix="/api")
    app.include_router(mcp.router, prefix="/api")
    app.include_router(agents.router, prefix="/api")
    app.include_router(chat.router, prefix="/api")
    app.include_router(runs.router, prefix="/api")
    app.include_router(evaluations.router, prefix="/api")
    app.add_exception_handler(RequestValidationError, validation_exception_handler)

    return app


app = create_app()
