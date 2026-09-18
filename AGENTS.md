# mini-agent 开发约定

本文件是 mini-agent 项目的长期开发约定。每次开发会话先阅读本文件；约定变化时只更新这一个文件，不要把规则重复写进各次任务说明。

## 项目简介

mini-agent 是一个本地优先的 Agent 工作台，包含聊天、Agent 管理、模型管理、工具管理、MCP 管理、Skills 管理、运行记录、Agent 评测八个模块。项目按阶段逐步实现，各阶段进度与实现说明见 docs/ 下的阶段文档；Agent Loop 统一由 `app/services/agent_runtime.py` 的 AgentRuntime 执行（聊天与后续评测共用），上下文容量管理与自动压缩也在统一 Runtime 内实现（摘要与边界保存在会话上，聊天记录不被删除或改写），本地工具执行一律走统一入口 `app/services/tools/executor.py`；运行记录与可观察性由 `app/services/run_recorder.py` 落库（一次发送消息对应一条 Run，事件复用 Runtime 真实输出，`/api/runs` 提供列表、详情时间线与详细载荷读取）。

## 固定技术栈

- 前端：Vue 3 + TypeScript + Vite + Vue Router + Pinia + 原生 CSS，npm 管理依赖，Vitest 做测试
- 后端：Python 3.12 + FastAPI + Pydantic + SQLAlchemy + Alembic + SQLite，uv 管理 Python 与依赖，pytest 做测试

禁止引入：React、Next.js、Nuxt、Django、Flask，以及 Element Plus、Ant Design、Vuetify 等大型 UI 组件库。

## 目录结构

```
./
  frontend/               前端（Vite 项目根）
    src/api/              请求封装与接口定义（一律请求相对路径 /api）
    src/components/       通用组件（侧边栏、页头、占位面板）
    src/router/           路由表
    src/stores/           Pinia 状态
    src/styles/           全局样式与设计变量（variables.css、base.css）
    src/views/            页面
  backend/                后端（uv 项目根）
    app/main.py           FastAPI 入口与应用工厂
    app/api/routes/       路由
    app/core/config.py    配置（数据库地址、CORS 等）
    app/db/               SQLAlchemy 引擎、会话、模型基类（业务模型放 app/db/models/）
    alembic/              数据库迁移（versions/ 下为迁移脚本）
    tests/                pytest 测试
  docs/                   阶段文档
  AGENTS.md               本文件
  README.md               使用说明
```

## 常用命令

后端（在 `backend/` 目录执行）：

| 命令 | 作用 |
| --- | --- |
| `uv sync` | 安装依赖（按 uv.lock） |
| `uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload` | 启动开发服务，只监听本机 |
| `uv run pytest` | 运行测试 |
| `uv run alembic upgrade head` | 应用迁移 |
| `uv run alembic revision -m "说明"` | 新建迁移脚本（需手写 upgrade/downgrade） |

前端（在 `frontend/` 目录执行）：

| 命令 | 作用 |
| --- | --- |
| `npm install` | 安装依赖 |
| `npm run dev` | 启动开发服务（5173，/api 代理到 8000） |
| `npm run type-check` | vue-tsc 类型检查 |
| `npm run test` | Vitest 测试 |
| `npm run build` | 类型检查 + 生产构建 |

## 启动项目（手动验收）

前后端各开一个终端，先启动后端再启动前端；验收完在对应终端按 `Ctrl + C` 停止。

1. 清理端口残留（重要）：Windows 允许两个进程同时绑定 8000 端口且不报错，流量被分摊后页面会一直卡在"正在思考"。启动前先查占用（最后一列是 PID）：

   ```powershell
   netstat -ano | findstr ":8000 :5173"   # 无输出即端口干净
   taskkill /F /PID <PID>                 # 有残留就按 PID 杀掉
   ```

   Git Bash 下杀进程写法为 `taskkill //F //PID <PID>`（斜杠加倍，防止被当成路径）。

2. 启动后端（在 `backend/` 目录）：

   ```bash
   uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
   ```

   `--host 127.0.0.1` 只监听本机；`--reload` 改代码自动重启。验证：访问 http://127.0.0.1:8000/api/health ，应返回 `{"status":"ok",...}`。

3. 启动前端（另开终端，在 `frontend/` 目录）：

   ```bash
   npm run dev
   ```

   浏览器打开 http://localhost:5173 。注意 Vite 默认只监听 IPv6 的 localhost，用 `127.0.0.1:5173` 可能打不开；后端才是 `127.0.0.1:8000`。前端请求全部走相对路径 `/api`，由 Vite 代理转发到后端，无需其他配置。

## 硬性规则

- Python 一律走 uv：装依赖用 `uv add`，同步用 `uv sync`，运行命令用 `uv run`（含 Alembic、pytest）。禁止 pip，README 与文档中不得出现让用户直接运行 pip 的步骤。
- 后端默认只监听 `127.0.0.1`。
- 前端所有请求使用相对路径 `/api/**`，由 Vite dev server 代理到 `http://127.0.0.1:8000`；业务代码不得写死后端地址。
- 数据库只用 SQLite（`backend/mini_agent.db`）。业务数据一律入库；改表必须新增 Alembic migration，不手改库，不用 JSON 文件存业务数据。
- 不引入新的大型依赖，保持现有技术栈。

## 数据库迁移方式

1. 修改或新增 `backend/app/db/models/` 下的模型，并在 `backend/app/db/base.py` 中导入（保证 Alembic autogenerate 能发现）
2. `uv run alembic revision -m "..."` 生成新脚本，手写 `upgrade()` / `downgrade()`
3. `uv run alembic upgrade head` 应用迁移
4. `uv run pytest` 确认无回归

当前迁移链：`0001_baseline`（空基线）→ `0002_create_model_tables`（模型管理两张表）→ `0003_create_mcp_tables`（MCP 管理两张表）→ `0004_create_skills_table`（Skills 登记表）→ `0005_create_tools`（工具表与内置工具种子）→ `0006_create_agents`（Agent 管理两张表）→ `0007_create_chat_tables`（聊天两张表）→ `0008_add_context_compaction`（Agent 压缩配置四列 + 会话摘要与压缩边界两列）→ `0009_create_run_records`（运行记录三张表：Run 主记录 / 安全事件 / 详细载荷）。连接串统一读自 `app/core/config.py` 的 `settings.database_url`。

## 配色与字号入口

统一定义在 `frontend/src/styles/variables.css`，页面与组件只允许使用这些变量，不得另写相近色值：

- 颜色：`--bg-deep`（最外层背景）、`--bg-base`（内容区）、`--bg-surface`（卡片/输入）、`--bg-hover`、`--bg-active`（选中菜单）、`--border`、`--accent` / `--accent-hover`（主按钮/品牌）、`--text-primary/secondary/tertiary/inverse`、`--success/--warning/--danger`
- 字体：`--font-body`（正文，DM Sans）、`--font-display`（标题/产品名，Outfit）、`--font-mono`（代码/命令，Fira Code）；均有系统字体回退
- 字号：页面主标题 18px/650；卡片标题 14–15px/600；正文与表单 12.5px；按钮与菜单 12–12.5px；辅助说明 10.5–11.5px；代码 12px；正文行高 1.5。禁止 32px 以上大标题
- 布局：桌面宽度左侧导航固定 188px；宽度 <900px 收起为 56px 图标栏；整页无横向滚动，滚动只发生在右侧内容区

## 每次修改后必须运行的检查

1. `cd backend && uv run pytest`
2. `cd frontend && npm run type-check && npm run test && npm run build`

全部通过后再交付。
