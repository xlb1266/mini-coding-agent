# 阶段五：MCP 管理

## 本阶段完成内容

在已有骨架上实现"MCP 管理"模块。仅支持在本机启动、通过 stdio 通信的 MCP Server；暂不支持远程 HTTP、OAuth 与账号授权。测试连接会实际启动程序完成 MCP 协议握手并读取工具列表，只发现工具，不执行工具。

### 后端

- 业务表（Alembic 迁移 `0003`）：
  - `mcp_servers`：名称（唯一）、说明、启动命令、启动参数（JSON 数组保存，保持顺序）、启用状态，以及最近一次测试结果（状态 success/failed/stale、时间、错误类型与脱敏后的错误信息、成功时发现的工具列表 JSON）。
  - `mcp_env_vars`：环境变量的加密存储（复用 `secret.key` 主密钥），随 Server 删除级联清理。
- 启动命令与参数分开保存，直接作为 `argv` 传给子进程，不拼接成 Shell 命令执行。
- MCP stdio 客户端（`app/services/mcp_client.py`，仅用标准库，不引入新依赖）：
  - 流程：启动子进程 → `initialize` 握手（协议版本 `2025-06-18`）→ `notifications/initialized` → `tools/list`（支持 `nextCursor` 分页）→ 关闭连接并清理进程。
  - 无论成功、失败还是超时都保证清理子进程：先关 stdin 等待自然退出，超时 terminate，再超时 kill。
  - 错误归类：启动命令不存在（command_not_found）/ 程序启动失败或提前退出（start_failed，附退出码与脱敏后的 stderr 摘录）/ 连接或读取工具超时（timeout，单步 20s）/ 协议版本或响应格式不兼容（protocol）/ Server 主动返回错误（server）/ 未知错误（unknown，附脱敏诊断信息）。
  - 所有面向用户的文案经过环境变量值脱敏，防止密钥意外回显。
- 接口（`app/api/routes/mcp.py`，前缀 `/api/mcp-servers`）：
  - `GET ""` 列表；`GET /{id}` 详情；`POST ""` 新增（201）；`PUT /{id}` 全量编辑；`DELETE /{id}` 删除；`PUT /{id}/enabled` 启用/停用；`POST /{id}/test` 测试连接。
  - 校验：名称与启动命令必填、名称全局唯一（不区分大小写）、环境变量名不得包含空格或等号、值不得为空；失败返回 422 与 `{"message", "fields"}` 字段级原因。
  - 环境变量保护：任何响应只返回统一掩码（`••••••••`）；编辑时回传掩码表示保留已保存的原值，掩码不会作为实际值保存；替换、删除均可。
  - 配置变更标记：修改启动命令、参数或环境变量后，原有测试结果标记为 stale（配置已变更，待重新测试）；仅修改名称或说明不影响。
  - 并发守卫：同一 Server 不允许同时进行多次测试（409）；测试连接是独立操作，停用状态下也可测试，测试成功不会自动启用。

### 前端

- `views/McpView.vue`：Server 表格（名称、说明、启动命令预览、启用状态、最近测试结果、工具数、操作），测试结果横幅（成功显示发现的工具数量；失败显示分类原因与建议），空状态与添加入口。
  - 列表语义：未测试显示"未测试"、工具数显示"未知"；测试成功显示数量与时间；失败显示脱敏错误信息；配置变更显示"配置已变更，待重新测试"且工具数回到"未知"。
  - "查看工具"对话框展示每个工具的名称、用途，以及参数的类型、是否必填与说明；Server 未提供工具时横幅显示"连接成功，未发现工具"。
- `components/McpFormDialog.vue`：新增/编辑表单（名称、说明、启动命令、启动参数列表、环境变量键值行），带填写说明与示例，说明配置通常可从 MCP Server 的使用说明中直接获取；环境变量保存后以掩码回显；字段级校验错误就地展示。
- **JSON 一键导入**：新增与编辑表单里都有"从 JSON 导入配置"入口，粘贴 MCP 客户端导出的配置即可自动填好名称、启动命令、参数与环境变量，确认后点"保存"生效。
  - 识别三种写法：`{"mcpServers": {"名称": {...}}}`（Cursor / Claude Desktop 等最常见的写法）、`{"servers": {...}}`（VS Code 风格，`type: "stdio"` 会被忽略）、以及直接粘贴单个 Server 对象（此时沿用表单里已填的名称）。
  - 一份配置包含多个 Server 时，先在下拉里选择要导入的一个。
  - 解析失败给出可读原因：JSON 语法错误、缺少 `mcpServers`、缺少 `command`、`args` 不是数组、`env` 不是键值对象、参数或变量值不是文本等。
  - 配置里带 `url` 或 `type` 为 http / sse 的远程 Server 会明确提示本阶段只支持本机 stdio，不会写入表单。
  - 参数与变量值中的数字、布尔值自动转成文本（如 `[8080]` → `"8080"`），值为 `null` 的环境变量视为未填写并跳过。
  - 表单已有内容时（编辑已有 Server，或已手工填写）导入前需要确认覆盖，避免误清空。
  - 说明字段沿用导入配置里的 `description`（没有则保留表单现有说明）。
- `views/mcp-form.ts`：表单校验、掩码识别、载荷构造与 JSON 导入解析的纯函数；`api/mcp.ts`、`stores/mcp.ts` 接口与状态。

### Windows 上的两个坑（启动命令与事件循环）

**启动命令**：`npx`、`uvx` 这类命令在 Windows 上是 `.cmd` 批处理包装脚本，Windows 的进程创建接口无法直接执行它们。服务层会用 `shutil.which` 解析命令，遇到 `.cmd` / `.bat` 时经 `cmd.exe /c` 启动（参数仍逐项作为 `argv` 传递，不做字符串拼接）；其他情况一律直接启动解析到的可执行文件。因此从各家文档复制来的 `npx` / `uvx` 配置在 Windows 上也能直接测试连接与调用。

**事件循环**：Windows 上 `uvicorn` 以 `--reload`（或 `--workers`）启动时用的是 `SelectorEventLoop`，而它不支持 asyncio 的子进程传输——直接在服务进程的循环里 `create_subprocess_exec` 会抛 `NotImplementedError`，表现为「给 Agent 配了 MCP 后一发消息就报未预期异常」。因此 MCP 的全部子进程 I/O 都跑在 `mcp_client.py` 自己的专用事件循环上（Windows 下固定为 `ProactorEventLoop`，后台线程里常驻），`McpSession` 与 `discover_tools` 对调用方仍是普通异步/同步接口，与调用方所处的循环无关；调用方任务被取消时会同步取消专用循环上的任务，子进程仍会被清理。

## 验证方式

```bash
# 1. 后端
cd backend
uv sync
uv run alembic upgrade head        # 应用 0003 迁移
uv run pytest

# 2. 前端（另开终端）
cd frontend
npm install
npm run type-check && npm run test && npm run build
npm run dev
```

打开 http://localhost:5173/#/mcp （侧边栏"MCP 管理"）：

1. 空状态显示添加入口；新增时按 MCP Server 文档填写启动命令（如 `uvx` / `npx`）、参数与环境变量。
2. 展开「从 JSON 导入配置」，粘贴下面的配置点「导入并填入表单」，表单会被自动填好（名称 amap-maps、命令 npx、两个参数、一个环境变量），再点「保存」即可：

   ```json
   {
     "mcpServers": {
       "amap-maps": {
         "command": "npx",
         "args": ["-y", "@amap/amap-maps-mcp-server"],
         "env": { "AMAP_MAPS_API_KEY": "在高德官网申请的 key" }
       }
     }
   }
   ```

   改为包含两个 Server 的配置会先要求选择要导入哪一个；把 `command` 删掉或粘贴不完整的 JSON 会给出对应的错误提示。
3. 点击"测试连接"：按钮变为"正在测试…"且不可重复点击；成功显示发现的工具数量，可点"查看工具"看参数说明；失败显示分类原因、建议与脱敏诊断信息。
4. 测试失败时按提示排查：命令不存在 / 启动即退出 / 超时 / 协议不兼容 / Server 返回错误，文案各不相同。
5. 编辑并修改启动命令、参数或环境变量后，列表显示"配置已变更，待重新测试"；只改名称或说明不影响。
6. 启用/停用与测试互相独立；停用状态下也可测试，测试成功不会自动启用。
7. 删除需确认；刷新页面、重启后端后配置、启用状态与最近测试结果均保留。

## 环境变量安全说明

- 环境变量的值以密文存于 `mcp_env_vars` 表，与模型 API Key 共用本地主密钥 `backend/secret.key`。
- 接口任何响应只出现统一掩码 `••••••••`；测试连接的错误信息、程序输出摘录均经过脱敏。
- 删除主密钥文件会导致已保存的变量值无法解密，测试连接会提示重新编辑并填写变量值。
