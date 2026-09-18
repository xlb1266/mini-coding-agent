# mini-opencode：基于 mini-agent 的编码 Agent 二次开发计划

本文档是 mini-agent 底座二次开发为编码 Agent（代号 **mini-opencode**）的设计与实施计划。所有结论来自一轮完整的设计访谈，每一条都经过决策，不是待办清单式的猜测。

## 一、背景与目标

mini-agent 已经是一个本地优先的 Agent 工作台：聊天、Agent 管理、模型管理、工具管理、MCP 管理、Skills 管理、运行记录、Agent 评测八个模块，统一由 `app/services/agent_runtime.py` 的 AgentRuntime 执行。

目标是把它变成一个**能真的写代码的 Agent**：能在真实代码库上读、找、改、跑测试，同时**边界清楚、动作可授权、过程可审计**。关键词是后半句——能力扩张不难，难的是让它在你的仓库里动代码时，你始终知道它做了什么、能随时叫停。

## 二、决策清单

| # | 议题 | 决策 |
| --- | --- | --- |
| 1 | 交付形态 | 在现有 Web 工作台内做，复用 Runtime / 运行记录 / 评测；不做终端 CLI |
| 2 | 安全边界 | 先只防「模型犯错」；容器级真隔离留配置位不实现，作为后续独立阶段 |
| 3 | 代码库边界 | 新增 `projects` 表；Agent 只能读写项目根目录内，`.git` 只读 |
| 4 | 审批粒度 | 三档模式 + 参数模式规则 + once / always / reject 三态 |
| 5 | 规则作用域 | 两级：Agent 上的模式字段 + 项目级规则表 + 会话内临时覆盖（不落盘） |
| 6 | 项目绑定 | 会话绑定项目；Agent 上留默认项目做兜底 |
| 7 | 编辑工具 | 字符串替换式 `edit` + 带行号的 `read` |
| 8 | Shell 环境 | 显式走 Git Bash，工作目录设为项目根；随后补结构化 `grep` / `glob` |
| 9 | 审批期间运行 | 断开浏览器不取消运行；超时判为拒绝 |
| 10 | git 保护 | 只读留痕 + diff 可视化；绝不写用户仓库 |
| 11 | 提示词 | 内置编码 Agent 模板 + 自动注入项目上下文（含 `AGENTS.md`） |
| 12 | 默认档位 | 编码 Agent 默认 `ask`；只读类工具在 `ask` 下默认免审 |
| 13 | 同一轮工具调用 | 继续串行执行，不并行、不做批量审批 |
| 14 | 长输出、doom_loop、question 工具 | 全部往后放，不纳入第一个里程碑 |

### 设计者代为决定的 3 件事（可否决）

1. **长输出处理整个不做**（含工件目录）。理由：Shell 走 Git Bash 且工作目录为项目根后，模型可以自己 `pytest > out.txt` 再用 `read` 读回，机制天然存在，不需要新造。代价是被截断时模型得自己想到这一招。
2. **「等待审批」用事件表达，不新增运行终态**。`agent_runs.status` 保持 `running`，另发 `tool_approval_request` 事件，前端据此显示「等待审批」，审批记录落 `permission_requests` 表。这样不牵动现有查询与 UI 的状态机，而承诺（断开不取消 + 超时拒绝）不变。
3. **审批超时后，若已无订阅者则取消运行**。避免用户走开后，运行独自跑十几轮全被拒绝的调用。

## 三、现状核查

做之前先分清「已有」和「真的缺」，避免重复造。

### 已经有了（不要重做）

- **三个内置工具**：`builtin_shell`、`builtin_file`、`builtin_current_time`（`app/services/tools/registry.py:28`），执行层有实打实的加固：正则黑名单 + 分段词法分析双重拦截、强制超时、输出截断、文件大小限制（`app/services/tools/handlers.py`）；
- **统一执行入口** `execute_tool`（`app/services/tools/executor.py:31`）：参数校验、异常兜底、统一结果类型；
- **Skills 已接进运行时**：`load_skill` 工具 + 系统提示词里自动追加 Skills 目录（`agent_runtime.py:938`、`_skill_catalog_section`）；
- **MCP 完整**：stdio 客户端、工具自动发现、`mcp{server_id}_` 前缀、env 加密落 `mcp_env_vars`（`app/services/mcp_client.py`）；
- **系统提示词**是 `agents.system_prompt` 这个可编辑、带版本号的字段，不是「没配置」；
- **运行记录与评测**：Run / 事件 / 详细载荷三张表，`rejected_tool_calls` 计数口径、`RunLimits` 运行级收窄机制都在。

### 真的缺（本次要做的）

1. **文件工具够不到真实代码库**：根目录写死在 `settings.tool_workspace_dir`（`app/core/config.py:29`，默认 `backend/tool_workspace`）；
2. **Shell 没有 `cwd`**（`handlers.py:242`）：`subprocess.run(command, shell=True, ...)` 未传 `cwd`，命令实际运行在后端进程目录 `backend/`；
3. **Windows 上 Shell 走 `cmd.exe`**：`grep`、`find`、`head`、`tail`、`sed` 全部不存在，文本检索能力为零；
4. **只有整文件 `write`**（overwrite / create / append），没有局部编辑；模型改一行要覆盖整个文件，读错版本就静默回退；
5. **没有结构化检索工具**，只能靠 `ls` + 整文件读，几十轮烧光上下文；
6. **完全没有审批层**：只有不可协商的黑名单（`ToolBlockedError` → `ERROR_BLOCKED`）和评测用的 `RunLimits` 收窄，没有中间态。

**第 2、3 条是独立于本计划的现存缺陷**，即使不做 mini-opencode 也值得单独修。

## 四、阶段十三（第一个里程碑）：项目工作区 + 权限审批

**目标**：让 Agent 能在真实代码库上、在边界内、在授权下工作。

### 4.1 数据模型（迁移 `0011_create_project_and_permission_tables`）

按项目约定手写 `upgrade()` / `downgrade()`，模型改完后在 `app/db/base.py` 导入以便 autogenerate 发现。

- **`projects`**：`id` / `name` / `root_path`（唯一，绝对路径）/ `description` / `is_git_repo`（后端探测结果缓存）/ `created_at` / `updated_at`；
- **`project_permission_rules`**：`id` / `project_id`（FK，级联删除）/ `permission_key`（`read` / `write` / `edit` / `bash` / `mcp` / `skill`）/ `pattern`（如 `git *`，`*` 为通配）/ `decision`（`allow` / `ask` / `deny`）/ 时间戳；`(project_id, permission_key, pattern)` 唯一；
- **`permission_requests`**：`id` / `run_uid` / `call_id` / `permission_key` / `tool_name` / `arguments_json`（脱敏后）/ `decision`（`approved_once` / `approved_always` / `rejected` / `timeout`）/ `decided_at` / `created_at`；
- **`chat_sessions`** 新增 `project_id`（FK，可空，建会话后不可改）；
- **`agents`** 新增 `permission_mode`（`read-only` / `ask` / `auto`，默认 `ask`）与 `default_project_id`（FK，可空）；
- **`agent_runs`** 新增项目快照列 `project_id` / `project_root`：项目路径会变，留痕必须存当时的实际值。

### 4.2 权限策略模块（新建 `app/services/permissions.py`）

对每一次工具调用，按以下顺序判定：

1. **硬红线**：命中现有 `assert_shell_command_allowed`、`.git` 目录内写入、项目根外路径 → `deny`。任何模式与规则都不能覆盖这一层，它是「审批也放不过去」的底线；
2. **语义映射**：把 `(tool_key, arguments)` 映射为 `(permission_key, match_target)`。`builtin_shell` → `bash` + 命令字符串；`builtin_file` 按 `action` 拆：`read` → `read`、`write` → `write`，target 为路径。阶段十四拆成独立工具后，这段映射自然消失；
3. **模式展开**：`read-only` → 只读类 `allow`、写与 `bash` `deny`；`ask` → 只读类 `allow`、写与 `bash` `ask`；`auto` → 全部 `allow`（仍受硬红线限制）；
4. **规则覆盖**：按 `permission_key` 取项目规则，对 `match_target` 做 glob 匹配；**命中多条时取最严**（`deny` > `ask` > `allow`）。选「最严优先」而非「最具体优先」，因为配置写错时它更安全，也更好预测；
5. **运行内记忆**：本次运行内选过 `always` 的 `(permission_key, pattern)` → `allow`（进程内，不落盘，运行结束即失效）；
6. 输出 `allow` / `ask` / `deny` 三态。

### 4.3 审批等待机制

- **事件**：`tool_approval_request`（含 `requestId` / `callId` / `toolName` / `permissionKey` / 脱敏参数 / `expiresAt`）与 `tool_approval_resolved`；
- **接口**：`POST /api/chat/sessions/{session_id}/approvals/{request_id}`，body `{decision: "once" | "always" | "reject", reason?}`；
- **接入点**：Runtime 在 `_execute_and_record` 内、`_dispatch` 之前插入判定（`agent_runtime.py:1741` 的 `else` 分支内，即工具已知且参数合法之后、交给执行器之前）。判定为 `ask` 时发出请求并 `await` 一个以 `request_id` 为键的进程内事件，带超时（新增 `settings.tool_approval_timeout_seconds = 300`）；
- **拒绝与超时都作为正常工具结果回填上下文**，复用 `_ToolOutcome` 与 `ERROR_BLOCKED` 的表达方式，让模型换方案继续，而不是把整个运行炸掉。一次被拒的 `rm` 不该终结整轮任务；
- **契约变更**：`app/services/chat_generation.py:403` 的 `unsubscribe` 目前无条件 `cancel_event.set()`。改为：若该会话存在待审批请求则不取消；超时拒绝后若无订阅者，再取消运行。

### 4.4 工具层改造

- `execute_tool(tool, params, ctx)` 新增 `ToolContext(workspace_root, git_readonly=True)`，三个调用点同步修改：`app/api/routes/tools.py:144`（试运行，无项目时回退 `settings.tool_workspace_dir`）、`agent_runtime.py:1872`（从运行所属项目取根）、`app/services/tools/__init__.py` 导出；
- `_resolve_in_workspace` 的根从全局配置改为 `ctx.workspace_root`；解析后的路径中含 `.git` 段时，读放行、写拒绝；
- **Shell 执行环境**：新增 `settings.shell_executable`（默认自动探测 Git Bash，探测不到回退 `cmd`），执行改为 `subprocess.run([shell, "-c", command], cwd=ctx.workspace_root, ...)`，**同时补上现在缺失的 `cwd`**；现有黑名单保留在最底层不变。

### 4.5 项目上下文注入

在 `_build_context_plan`（`agent_runtime.py:1012`）里，除系统提示词与 Skills 目录外，追加一段项目上下文：

- 项目根绝对路径；
- 当前 git 分支；
- 脏文件列表（提醒模型工作区可能有人正在改）；
- `AGENTS.md` 内容（若存在），并**明确标注为「项目约定参考」，同时声明其中出现的指令不得覆盖系统提示词**——它既是约定也是提示注入面。

`RuntimeRequest`（`agent_runtime.py:264`）新增 `project_root` / `project_context` 字段。

### 4.6 前端

- 新增 `ProjectsView`（列表 / 新建 / 编辑：名称、绝对路径、说明；路径存在性与 git 探测走后端校验），侧边栏加菜单项；
- `ChatView` 建会话时选项目，会话头部常驻显示当前项目；
- **审批卡片**：工具名、语义类别、脱敏参数（长命令折叠）、三个按钮（允许一次 / 本次运行内总是允许 / 拒绝）+ 倒计时；
- `RunDetailView` 时间线展示审批请求与决策结果；
- `AgentFormDialog` 加权限模式三档选择与默认项目；
- 颜色字号一律使用 `frontend/src/styles/variables.css` 中的变量。

### 4.7 验收清单

1. `cd backend && uv run alembic upgrade head` 新表建立；`uv run pytest` 全绿；
2. 新建项目指向一个真实仓库 → Agent 读文件成功；读项目根外的文件被拒，提示「超出授权目录」；
3. 让 Agent 跑 `pwd` → 返回项目根路径（不是 `backend/`）；跑 `grep -rn "def main" .` → 在 Git Bash 下正常返回结果；
4. 让 Agent 删文件 → 出现审批卡片。点「拒绝」→ 模型收到拒绝理由并换方案；点「允许一次」→ 执行；再让它做同类操作 → 因 `always` 不再弹窗；
5. 审批卡片挂着时关掉浏览器标签 → 运行不取消；5 分钟后重开 → 看到超时拒绝的留痕；
6. 让 Agent 改 `.git/config` → 被拒（硬红线）；
7. 系统提示词里能看到项目根路径与 `AGENTS.md` 内容。

## 五、阶段十四（第二个里程碑）：编码工具集

**目标**：真正会改代码。

### 5.1 迁移 `0012_split_coding_tools`

- `tools` 表插入 5 行内置工具：`builtin_read` / `builtin_write` / `builtin_edit` / `builtin_grep` / `builtin_glob`；
- `builtin_file` 置为 `enabled=false` 但**保留行**（旧运行记录还能按 key 解析出名称）；
- `agent_bindings` 中已绑定的 `builtin_file` 迁移为 `builtin_read` + `builtin_write` 两条；`downgrade` 反向合并。

### 5.2 五个工具的行为契约

1. **`read(path, offset?, limit?)`** → 带行号内容（`行号: 内容`）+ 文件大小 + 是否被截断；单次上限沿用 512KB；
2. **`write(path, content, mode?)`** → 沿用现有 overwrite / create / append 语义；
3. **`edit(path, old_string, new_string)`** → 要求 `old_string` 在文件中**唯一出现**：0 次报「未找到，请先 read 确认」；多次报「出现 N 次，请提供更多上下文使匹配唯一」；成功返回变更行号范围与改动摘要（供前端渲染 diff）。这个失败模式既显式又可自愈；
4. **`grep(pattern, path?, glob?, max_results?)`** → 返回 `文件:行号:内容`；跳过 `.git` / `node_modules` / 二进制 / 超大文件；默认上限 100 条，截断时明确提示；
5. **`glob(pattern, path?, max_results?)`** → 返回匹配的相对路径，按修改时间降序，默认上限 100 条。

五个工具的 `description` 沿用现有风格（中文，含适用场景 / 输入要求 / 使用限制），因为它们直接决定模型用不用得对。权限按新工具粒度生效，只读三类默认免审。

### 5.3 编码 Agent 预设提示词

后端内置一段中文提示词模板 + 一个「一键创建编码 Agent」入口。内容要点：

- **角色定位**；
- **工具选择策略**：先 `glob` / `grep` 定位，`edit` 前必须先 `read`，禁止用整文件 `write` 代替局部编辑，改完就跑测试；
- **工作方式**：小步改动，每步可验证；
- **边界**：只在项目根内活动，不碰 `.git`；
- **失败处理**：工具报错先读提示再重试，不要重复同一个调用。

### 5.4 git diff 可视化

运行结束时把 `git status` / `git diff --stat` 落进 Run 记录（纯只读，仓库一个字节不被写），`RunDetailView` 渲染改动文件列表与 diff。

### 5.5 验收清单

1. 迁移与测试全绿；`builtin_file` 在工具管理页显示为已停用，新 5 个工具可见且参数说明完整；
2. 让 Agent 改一个函数 → 走 `edit` 而非整文件覆盖，改动可渲染；
3. 故意让 `old_string` 不唯一 → 模型收到「出现 N 次」的提示后补充上下文，重试成功；
4. 让 Agent 找「哪个文件用了某个函数」→ 走 `grep` 而非 shell 拼 grep；
5. 一键创建的编码 Agent 做中等难度验收（如「给这个函数补单元测试并跑通」），全程无需人工提示。

## 六、明确不做（以及为什么）

| 不做 | 理由 |
| --- | --- |
| 长输出落盘 + 工件目录 | 模型可用 Shell 自己重定向到项目文件再 `read` 读回，机制天然存在 |
| `doom_loop` 检测、截断提示语改造 | 用户决定往后放，第一个里程碑保持变更薄 |
| `question` 工具（模型中途问用户） | 基础设施虽现成，但需提示词教它何时该问，否则退化成骚扰工具 |
| 并行执行同一轮工具调用 | 本地磁盘读是毫秒级、模型调用是秒级，收益测不出来；却引入「一轮多审批」与「结果回填顺序」两个新问题 |
| 同类操作批量审批 | `always` 已经解决大部分重复弹窗 |
| MCP HTTP / SSE 传输 | 主流 MCP Server 都跑 stdio，与编码能力正交，属另一条线 |
| 容器级真隔离 | 第二阶段；本阶段只在配置与策略模块里留开关位置 |
| `git` 自动提交检查点 | 会写用户仓库，爆炸半径大；留成后续可选开关（默认关） |
| 终端 CLI | Web 工作台已复用全部底座资产，审批 UI 在 Web 上反而更强 |
| LSP 工具、websearch、持久记忆、多平台网关 | 均不属于编码底座的核心路径 |

## 七、已知限制（明确接受）

- 后端进程重启会中断等待中的审批（进程内等待，不落盘续跑）；
- 权限黑名单仍是尽力而为的黑名单，不是内核级隔离；`auto` 模式下模型仍可能做出破坏性操作；
- 不做长输出落盘，模型需自己用重定向 + `read` 读回；
- MCP 仍只支持 stdio；
- 同一轮工具调用串行执行；
- `AGENTS.md` 注入同时是提示注入面，只在提示词层面声明约束，没有强制机制。

## 八、参考的开源实践

借鉴原则：**借工具形态与权限模型，不借架构**。

- **OpenCode**：参数模式规则（`{"bash": {"*":"ask", "git *":"allow", "rm *":"deny"}}`）、审批三态 once / always / reject（`always` 仅本会话有效）、细粒度工具划分（read / write / edit / apply_patch / grep / glob / list）、`doom_loop` 兜底；
- **OpenAI Codex CLI**：「能力」与「同意」两层分离——沙箱决定能做多大范围，审批决定何时必须问；`workspace-write` 模式下 `.git` 始终只读；这三条都被直接采用；
- **Claude Code**：字符串替换式编辑、`AGENTS.md` 项目约定、专用 grep / glob 工具（即使在 grep 随处可用的 Linux 上仍然自建）；
- **Hermes Agent**（Nous Research）：项目上下文文件约定与 Skills 自学习思路；其重心是跨平台助手，与编码底座重叠面较小。

不借的部分：Codex 的沙箱底座（seatbelt / landlock / bwrap，Windows 上无对应物，需 WSL2 或容器）、OpenCode 与 Hermes 的 LSP / 多平台网关 / 持久记忆。

## 九、每阶段收尾检查

按 `AGENTS.md` 的硬性要求，全部通过才交付：

1. `cd backend && uv run pytest`
2. `cd frontend && npm run type-check && npm run test && npm run build`

另外每阶段写 `docs/phase-13.md` / `docs/phase-14.md`，并同步更新 `README.md` 的进度与 `AGENTS.md` 的迁移链（`0001` … `0010` → `0011` → `0012`）。

## 十、落地顺序

1. **阶段十三**：项目边界 + 权限审批（第一个里程碑，交付「安全可用」）；
2. **阶段十四**：编码工具集 + 预设提示词（第二个里程碑，交付「真正会写代码」）。

顺序不能颠倒：先用现有工具 + 权限层把边界立起来，再扩能力。反过来做等于在一个没有护栏的仓库里先放大模型的手。
