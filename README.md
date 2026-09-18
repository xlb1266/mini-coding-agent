# mini-agent

本地优先的 Agent 工作台。包含聊天、Agent 管理、模型管理、工具管理、MCP 管理、Skills 管理、运行记录、Agent 评测八个模块。

当前进度：阶段一（系统初始化）、阶段二（模型管理）、阶段三（工具管理）、阶段四（Skills 管理）、阶段五（MCP 管理）、阶段六（Agent 管理）、阶段七（聊天页面）、阶段八（统一 Agent Runtime）、阶段九（工具执行过程展示）、阶段十（上下文容量管理与自动压缩）、阶段十一（运行记录与可观察性）与阶段十二（Agent 评测）已完成。详见 [docs/phase-1.md](docs/phase-1.md) 至 [docs/phase-12.md](docs/phase-12.md)。

## 技术栈

| 端 | 技术 |
| --- | --- |
| 前端 | Vue 3 + TypeScript + Vite + Vue Router + Pinia + 原生 CSS，测试用 Vitest |
| 后端 | Python 3.12 + FastAPI + Pydantic + SQLAlchemy + Alembic，数据库为 SQLite，测试用 pytest |
| 包管理 | 前端 npm；Python 与后端依赖统一由 uv 管理 |

## 目录结构

```
./
  frontend/    前端源码（frontend/src）
  backend/     后端源码（backend/app）、Alembic 迁移（backend/alembic）
  docs/        阶段文档
  AGENTS.md    开发约定（技术栈、命令、设计变量、修改后检查）
```

## 环境要求

1. Node.js 20 及以上（前端构建与开发服务）
2. uv（同时负责管理 Python 3.12 与后端依赖，安装方式见 uv 官方文档：https://docs.astral.sh/uv/getting-started/installation/ ）

## 快速开始

### 1. 启动后端（端口 8000，只监听本机）

```bash
cd backend
uv sync                                          # 按 uv.lock 安装依赖到 .venv
uv run alembic upgrade head                      # 建库并应用迁移（生成 backend/mini_agent.db）
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

命令含义：`uv run` 在项目虚拟环境中执行命令；`uvicorn app.main:app` 指启动 `app/main.py` 里的 FastAPI 实例；`--host 127.0.0.1` 限制只监听本机；`--reload` 让代码修改后自动重启。

验证：访问 http://127.0.0.1:8000/api/health ，应返回 `{"status":"ok","database":"ok",...}`。

### 2. 启动前端（端口 5173）

```bash
cd frontend
npm install                                      # 安装依赖
npm run dev                                      # 启动 Vite 开发服务
```

打开 http://localhost:5173 。前端所有请求都使用相对路径 `/api`，由 Vite 开发服务代理转发到本机 8000 端口的后端，业务代码不写死后端地址。

## 测试

```bash
# 后端（backend/ 目录）
uv run pytest

# 前端（frontend/ 目录）
npm run test        # Vitest 单测
npm run type-check  # vue-tsc 类型检查
npm run build       # 类型检查 + 生产构建
```

## 数据库与迁移

- 数据库为 SQLite，文件固定在 `backend/mini_agent.db`，后续业务数据一律写入数据库。
- 修改表结构时：改 `backend/app/db/models/` 下的模型（并在 `app/db/base.py` 中导入）→ `uv run alembic revision -m "说明"` → 手写迁移的 `upgrade()`/`downgrade()` → `uv run alembic upgrade head`。
- 迁移连接串统一读自 `backend/app/core/config.py`，与运行时使用同一个数据库。

## 开发约定

所有长期约定（技术栈限制、uv 规则、目录职责、配色与字号变量、每次修改后的检查清单）集中在根目录 [AGENTS.md](AGENTS.md)。
