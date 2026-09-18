# 阶段三：工具管理

## 本阶段完成内容

在已有骨架上实现"工具管理"模块。工具决定了 Agent 能执行哪些操作，本阶段提供三类内置工具、统一的工具执行入口，以及工具的管理界面。

### 数据库

- Alembic 迁移 `0005_create_tools`：创建 `tools` 表（key 唯一稳定标识、名称、用途说明、参数说明 JSON、enabled 启用状态、is_builtin 内置标记、创建/更新时间），并写入三个内置工具的种子数据。
- 种子数据的唯一来源是 `app/services/tools/registry.py` 的 `builtin_tool_rows()`，迁移与测试共用，避免定义漂移。

### 内置工具（`backend/app/services/tools/`）

- **当前时间**（`builtin_current_time`）：查询指定时区的当前日期和时间。时区参数使用 IANA 名称（如 Asia/Shanghai、America/New_York、Etc/UTC），拒绝 CST 等含义不明确的缩写；未指定时使用系统默认时区（`MINI_AGENT_DEFAULT_TIMEZONE`，默认 Asia/Shanghai）。Windows 依赖 `tzdata` 包提供时区数据。
- **Shell 命令执行**（`builtin_shell`）：执行命令并返回退出码、stdout、stderr 与耗时。强制超时（默认 30 秒，请求可传 1–120 秒）与输出上限（stdout/stderr 各 10000 字符，超出截断）。执行层拦截危险命令，包括：递归删除根目录或系统目录（`rm -rf /`、`rd /s /q c:\` 等）、格式化磁盘或写磁盘设备（format、mkfs、diskpart、dd of=/dev/…）、修改关键系统权限（icacls/takeown/cacls、对系统目录的 chmod/chown）、关闭安全防护（Defender、防火墙相关命令）、读取或外传密钥（.ssh、.aws、.env、secret.key 等路径及 printenv）、未经检查直接执行远程下载的脚本（`curl … | sh`、iex、DownloadString 等）。拦截由"正则黑名单 + 按命令分段的目标路径分析"双层实现，包装命令（`cmd /c`、`bash -c`）会向内分析。
- **文件读写**（`builtin_file`）：读取、创建和修改授权工作区内的文本文件。工作区默认 `backend/tool_workspace`（`MINI_AGENT_TOOL_WORKSPACE_DIR` 可改）；路径支持相对路径与工作区内绝对路径，解析后校验必须落在工作区内，越界一律拒绝。单次读写上限 512KB；读取的文件必须已存在；写入支持 `overwrite`（覆盖/新建）、`create`（仅新建，已存在报错）、`append`（追加）三种模式，内容以 UTF-8 保存。

### 统一执行入口（`app/services/tools/executor.py`）

- `execute_tool(tool, params)` 是所有本地工具的唯一执行路径，后续 Agent Loop 调用本地工具都经过这里。
- 执行前依次检查：工具是否启用（返回 `disabled`）、是否有对应执行器、参数是否符合规格（缺失/类型/未知参数，返回 `invalid_params` 并附逐参数原因）。
- 执行后返回统一结果：成功为 `{ok: true, data: …}`；失败为 `{ok: false, error_type, message, hint, fields}`，错误类型包括 `disabled` / `invalid_params` / `blocked` / `timeout` / `failed`。
- 一切异常（包括未预期异常）都在入口处转换为失败结果，不会向外抛出，后端进程不会因某个工具而崩溃。
- HTTP 入口：`POST /api/tools/{id}/execute`，工具不存在返回 404，其余执行结果（含被拦截、超时、失败）都以 HTTP 200 返回，方便调用方统一处理。

### 工具接口（`app/api/routes/tools.py`，前缀 `/api/tools`）

- `GET ""` 列表 / `GET /{id}` 详情：返回名称、key、用途说明、参数说明、启用状态与内置标记。
- `PUT /{id}`：启停对所有工具可用；名称与用途说明仅对非内置工具生效。系统内置工具改名或改用途返回 403（与原值相同则放行），空值返回 422。
- `DELETE /{id}`：系统内置工具返回 403，不允许删除。
- 工具说明包含"适用场景 / 输入要求 / 使用限制"三部分，帮助模型判断何时调用、如何填写参数。

### 前端

- `views/ToolsView.vue`：列表页展示名称+key、内置徽标、用途说明（两行截断）、参数概览、启停状态徽标与更新时间；操作列提供"详情"与"启停"，失败用横幅提示，顶部统计内置/启用数量。
- `views/ToolDetailView.vue`（路由 `/tools/:id`）：用途说明全文、参数说明表格（参数名/类型/必填/说明/示例）、启停操作，以及**试运行面板**——按参数规格生成表单（命令与文件内容用多行输入框），调用统一执行入口后展示结果或失败原因（含被拦截原因、参数级错误就地标注）。
- 表单纯逻辑独立在 `views/tool-run.ts`（初值构建、校验、参数序列化、结果格式化），便于单元测试。

### 测试

- 后端 pytest：工具 API（列表/详情/内置保护/启停/统一执行）、执行器单元测试（时间、文件越界、异常兜底）、Shell 拦截清单（45 条必须拦截的危险命令 + 22 条必须放行的日常命令）。
- 前端 Vitest：列表页渲染与交互、试运行表单逻辑、路由含 `/tools/:id`。

## 验证方式

```bash
cd backend
uv run alembic upgrade head
uv run pytest
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

打开 http://localhost:5173/tools ：

1. 列表显示三个内置工具（当前时间、Shell 命令执行、文件读写），带"内置"徽标与"启用"状态。
2. 点击"停用"后状态变化；进入详情页对已停用工具试运行，返回"工具已停用"。
3. 详情页查看用途说明与参数说明；试运行当前时间工具填写 `Asia/Shanghai`，应返回北京时间与星期。
4. 试运行 Shell 工具输入 `rm -rf /`，应显示"被安全拦截"；输入 `echo hello`，应返回输出与退出码 0。
5. 试运行文件读写工具：write 写入 `notes/a.md` 后 read 读回内容一致；读取 `../` 等工作区外路径应提示参数错误。
6. 对内置工具点击"详情"后尝试改名接口应返回 403（前端不提供改名入口，内置工具只允许启停）。
