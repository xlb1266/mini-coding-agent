"""SQLAlchemy 模型注册处：把全部业务模型导入到 Base.metadata。

Alembic autogenerate 以本模块导出的 metadata 为准。
Base 定义在 app/db/base_class.py（先导入基类再导入模型，避免循环导入）。
"""

from app.db.base_class import Base  # noqa: F401
from app.db.models.agent import Agent, AgentBinding  # noqa: F401
from app.db.models.chat import ChatMessage, ChatSession  # noqa: F401
from app.db.models.evaluation import EvalCase, EvalDataset, EvalTask, EvalTaskResult  # noqa: F401
from app.db.models.mcp_server import McpEnvVar, McpServer  # noqa: F401
from app.db.models.model_config import ModelConfig, ModelSecret  # noqa: F401
from app.db.models.run_record import AgentRun, AgentRunEvent, AgentRunEventDetail  # noqa: F401
from app.db.models.skill import Skill  # noqa: F401
from app.db.models.stage_project import StageProject  # noqa: F401
from app.db.models.tool import Tool  # noqa: F401
