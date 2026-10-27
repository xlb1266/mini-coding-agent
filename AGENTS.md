# mini-coding-agent 开发约定

本文件是 mini-coding-agent 项目的长期开发约定。每次开发会话先阅读本文件；约定变化时只更新这一个文件，不要把规则重复写进各次任务说明。

## 项目简介

mini-coding-agent 是一个本地优先的 coding agent 工作台，由 mini-agent 底座（`main` 的 `869fd50`）派生而来的独立项目，包含聊天、Agent 管理、模型管理、工具管理、MCP 管理、Skills 管理、运行记录、Agent 评测八个模块。coding agent 特化已开始（阶段十三/十四/十五/十六，分支 `feature/coding-agent-mvp`）：Agent 可绑定本机工作区目录（文件工具沙箱与 Shell 执行目录跟随），内置文件编辑 / 列目录 / 搜索工具，Shell 支持前台执行、后台任务托管（启动 dev server 等长驻命令，跨轮次存活、退出通知注入、整树击杀停止）与前台阻塞预算保险丝（超时自动转后台）。执行治理：聊天输入框左下角可切换三档执行档位（高度自主=不审批+高度自主工作流；阶段确认=不审批+阶段工作流；变更前确认=高风险动作审批+阶段工作流），会话级覆盖保存在 chat_sessions 上（连同会话级模型覆盖，NULL 跟随 Agent 配置，新会话沿用 Agent 默认），Agent 编辑表单不再提供档位下拉，仅保留 Shell 命令白名单（命中免审，黑名单仍生效）。自主档位两档：阶段确认模式（默认）走阶段化开发 v2——Agent 先按意图路由判断，从 0 到 1 的开发需求调用 submit_stage_plan 提交 2–8 个阶段的计划（运行以 plan_submitted 结束），用户确认后每个阶段一个独立会话逐阶段推进（按「开发→自检→交接文档→finish_stage」收尾，运行以 stage_complete 结束，聊天页展示阶段项目清单卡与验收横幅，点「进入下一阶段 / 生成项目汇总」由后端自动新建会话并携带计划与上一阶段交接，工作区由代码钉在项目上）；存量项目增量开发 / 问答走普通流程；高度自主模式不提供规划与收尾工具。聊天输入框（ChatComposer 组件）仿 ZCode 布局：底部工具栏左侧执行档位菜单、右侧上下文容量仪表（估算口径，点开看系统提示词/技能/工具定义/会话摘要/消息分类明细，不含下次输入与未连接的 MCP 工具定义）与模型切换菜单（平铺模型管理全部模型 + 管理模型入口），生成期间档位与模型禁止切换。聊天轨迹展示：文件写入类调用聚合为「修改了 N 个文件」卡（点开看 diff，默认收起）、Shell 结果带 ✓/✗/超时终止/后台运行中+taskId 徽章、正文全文渲染不折叠。评测支持 LLM Judge 过程评分与两任务回归对比。项目按阶段逐步实现，各阶段进度与实现说明见 docs/ 下的阶段文档；Agent Loop 统一由 `app/services/agent_runtime.py` 的 AgentRuntime 执行（聊天与后续评测共用），上下文容量管理与自动压缩也在统一 Runtime 内实现（摘要与边界保存在会话上，聊天记录不被删除或改写），本地工具执行一律走统一入口 `app/services/tools/executor.py`；运行记录与可观察性由 `app/services/run_recorder.py` 落库（一次发送消息对应一条 Run，事件复用 Runtime 真实输出，`/api/runs` 提供列表、详情时间线与详细载荷读取）。

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

## 快速开始

### 1. 启动后端（端口 8001，只监听本机）

```bash
cd backend
uv sync                                          # 按 uv.lock 安装依赖到 .venv
uv run alembic upgrade head                      # 建库并应用迁移（生成 backend/mini_agent.db）
uv run uvicorn app.main:app --host 127.0.0.1 --port 8001 --reload
```

命令含义：`uv run` 在项目虚拟环境中执行命令；`uvicorn app.main:app` 指启动 `app/main.py` 里的 FastAPI 实例；`--host 127.0.0.1` 限制只监听本机；`--reload` 让代码修改后自动重启。

验证：访问 http://127.0.0.1:8000/api/health ，应返回 `{"status":"ok","database":"ok",...}`。

### 2. 启动前端（端口 5174）

```bash
cd frontend
npm install                                      # 安装依赖
npm run dev -- --port 5174                                 # 启动 Vite 开发服务
```

打开 http://localhost:5174 。前端所有请求都使用相对路径 `/api`，由 Vite 开发服务代理转发到本机 8000 端口的后端，业务代码不写死后端地址。


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

当前迁移链：`0001_baseline`（空基线）→ `0002_create_model_tables`（模型管理两张表）→ `0003_create_mcp_tables`（MCP 管理两张表）→ `0004_create_skills_table`（Skills 登记表）→ `0005_create_tools`（工具表与内置工具种子，固定只插最初三个工具 key）→ `0006_create_agents`（Agent 管理两张表）→ `0007_create_chat_tables`（聊天两张表）→ `0008_add_context_compaction`（Agent 压缩配置四列 + 会话摘要与压缩边界两列）→ `0009_create_run_records`（运行记录三张表：Run 主记录 / 安全事件 / 详细载荷）→ `0010_create_evaluation_tables`（Agent 评测四张表 + agent_runs.source 列）→ `0011_add_agent_workspace`（agents 加工作区目录与审批开关两列）→ `0012_add_coding_tools`（三个 coding 内置工具种子行）→ `0013_add_message_timeline`（chat_messages 加 timeline_json，回复过程轨迹随消息入库）→ `0014_shell_background`（更新 Shell 与文件读写工具定义：action 后台任务 / tail_lines 尾部读取，仅 UPDATE 两行描述与参数）→ `0015_stage_governance`（阶段十五：agents 审批档位/自主档位/白名单三列并删除 require_approval；chat_sessions 验收状态与阶段摘要两列；eval_task_results 过程评分两列）→ `0016_stage_projects`（阶段化开发 v2：stage_projects 项目表；chat_sessions 加 project_id / project_role / stage_index / workspace_dir 四列）→ `0017_session_governance`（聊天输入框会话级覆盖：chat_sessions 加 approval_level / autonomy_mode / model_id 三列，NULL 跟随 Agent 配置）。连接串统一读自 `app/core/config.py` 的 `settings.database_url`。

## 配色与字号入口

统一定义在 `frontend/src/styles/variables.css`，页面与组件只允许使用这些变量，不得另写相近色值：

- 颜色：`--bg-deep`（最外层背景）、`--bg-base`（内容区）、`--bg-surface`（卡片/输入）、`--bg-hover`、`--bg-active`（选中菜单）、`--border`、`--accent` / `--accent-hover`（主按钮/品牌）、`--text-primary/secondary/tertiary/inverse`、`--success/--warning/--danger`
- 字体：`--font-body`（正文，DM Sans）、`--font-display`（标题/产品名，Outfit）、`--font-mono`（代码/命令，Fira Code）；均有系统字体回退
- 字号：页面主标题 18px/650；卡片标题 14–15px/600；正文与表单 12.5px；按钮与菜单 12–12.5px；辅助说明 10.5–11.5px；代码 12px；正文行高 1.5。禁止 32px 以上大标题
- 布局：桌面宽度左侧导航固定 188px，底部按钮可手动收起为 56px 图标栏（状态记入 localStorage）；宽度 <900px 自动收起为 56px 图标栏；整页无横向滚动，滚动只发生在右侧内容区

## 每次修改后必须运行的检查

1. `cd backend && uv run pytest`
2. `cd frontend && npm run type-check && npm run test && npm run build`

全部通过后再交付。

## 默认说话方式:短

- 先给结论,再给理由。能一行说清就一行,不要铺垫。
- 默认不超过 3 句话或 150 字。用户说"一行""三句话""简短"时严格照做,不要解释为什么短。
- 不要列表、小标题、加粗、结尾总结,除非用户明确要求,或者内容是非做不可的多步操作步骤。
- 不要复述用户的话,不要解释显而易见的前提,不要加"希望对你有帮助"之类的收尾。
- 不要把可能性全部并列罗列。有判断就直接给最值得做的那一个,其余一句带过或不说。
- 一次只推进一件事。需要展开时先问一句,不要自动展开成完整方案。
- 用户是零基础小白，不要堆叠专业术语，必要时可附带解释

## 例外:该长的地方长

- 代码、命令、配置、报错信息、精确操作步骤:保持完整准确,不做删减。
- 用户明确要"详细""完整方案""对比"时,按要求展开。

## 不确定就说不确定

直接讲清哪部分不确定、为什么,不要用模糊措辞绕开。