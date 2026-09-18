# 阶段一：从空目录初始化系统

## 本阶段完成内容

- 前端骨架：Vue 3 + TypeScript + Vite + Vue Router + Pinia。统一布局（188px 侧边栏、8 个菜单项、宽度 <900px 收起为图标栏）、全局配色与字体变量（`src/styles/variables.css`）、统一的 `/api` 请求封装（`src/api/request.ts`）、8 个占位页面（含聊天页的后端连接状态展示）。
- 后端骨架：FastAPI 应用工厂、健康检查接口 `GET /api/health`、SQLAlchemy + SQLite 连接（`backend/mini_agent.db`）、Alembic 迁移环境与基线迁移 `0001_baseline`（仅建立迁移链，业务表在后续阶段创建）。
- 工程约定：根目录 `AGENTS.md` 集中记录技术栈限制、uv 规则、常用命令、设计变量与修改后检查清单。

## 验证方式

```bash
# 1. 后端
cd backend
uv sync
uv run alembic upgrade head
uv run pytest
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000

# 2. 前端（另开终端）
cd frontend
npm install
npm run dev
```

打开 http://localhost:5173 ：默认进入聊天页；8 个菜单可切换；刷新任意地址不出现空白页；聊天页显示后端 `/api/health` 已连接。

## 后续阶段

聊天、Agent 管理、模型管理、工具管理、MCP 管理、Skills 管理、运行记录、Agent 评测按阶段逐步实现。业务数据一律写入 SQLite，建表通过新增 Alembic migration 完成。
