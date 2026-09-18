# 阶段八：统一 Agent Runtime

## 本阶段完成内容

把 Agent Loop 收拢到统一的 Agent Runtime：聊天接口只负责接收请求与转发事件，不再自行维护模型与工具循环。Runtime 负责加载 Agent 配置、组装上下文、准备工具与 Skill 目录、调用模型、执行工具循环（含按需加载 Skill 指令）、处理取消与资源清理，并输出统一运行事件。本阶段不涉及评测模块与运行记录持久化，但 Runtime 的输入输出就是按"后续评测直接调用同一个 Runtime"设计的。

### 后端

- 统一 Agent Runtime（`app/services/agent_runtime.py`，新增）：
  - 输入 `RuntimeRequest`：Agent 标识、用户输入、历史消息（`[{"role","content"}]`）、运行级能力限制（`RunLimits`）、取消信号（`asyncio.Event`）与事件回调；输出统一运行事件流 + 最终 `RunResult`（状态 / 结束原因 / 累计正文 / 错误归类 / 轮次与调用计数）。Runtime 不依赖前端页面、HTTP 请求对象或浏览器连接。
  - 配置加载：运行开始时从数据库加载 Agent 的模型、系统提示词与三类绑定（内置工具 / Skill / MCP Server），只取已绑定且仍然启用的项；`RunLimits` 各字段与绑定取交集、`max_rounds` 只能调小——运行级限制只能收窄权限，不能额外授予未绑定或已停用的能力。API Key 与 MCP 环境变量在运行内解密使用，不落日志。
  - 工具循环：每轮发起一次模型请求；模型直接回答则正常结束；请求工具则经统一入口执行、结果追加到上下文后再请求模型；持续到正常结束、达到轮数限制、运行级错误或被取消。轮数 = 模型请求次数上限（取 Agent 配置的 `max_rounds`）；达到上限时模型仍在请求工具则停止，最后一轮的工具请求不执行（没有后续轮次能使用其结果），运行以 `rounds_exhausted` 结束并给出可读提示。
  - 正文顺序保留：工具调用前后、各轮次的正文增量都按序累积并下发 `text_delta`，进入下一轮不覆盖不清空；发送给模型的 assistant 消息携带正文与 `tool_calls`。
  - 流式调用复用 `model_client`，工具执行复用 `executor.execute_tool`，未另建重复实现。

- 统一工具标识与映射（避免名称冲突，全部经同一执行入口处理）：
  - 内置工具：模型可见名即 `tools.key`（`builtin_` 前缀），参数 schema 由注册表的参数规格生成；
  - MCP 工具：`mcp{server_id}_{工具名}`（非法字符替换 `_`、超长截断到 64、冲突加后缀），参数 schema 用 `tools/list` 返回的 `inputSchema`；
  - Skill 加载：固定 `load_skill`，仅当存在可用 Skill 时提供。
  - 执行前再次校验权限：内置工具查绑定 + 启用状态后走 `execute_tool`；MCP 工具查绑定 + Server 启用状态后经会话调用；`load_skill` 查绑定 + 启用状态后读文件。未绑定、已停用、不存在或参数不是合法 JSON 对象的请求都拒绝执行，错误作为工具结果交还模型，由模型决定后续处理；模型填了某个名称、或 Skill 指令要求某个工具，都不会放行未授权调用。

- Skills 发现与按需加载：
  - 运行开始时只在系统提示词追加"可用 Skills"目录（`skill_id` / 名称 / 说明），并说明"任务符合用途时先调用 `load_skill` 加载完整指令再执行"；不预置任何详细指令。
  - `load_skill` 是 Runtime 提供的内置能力，不要求在 Agent 管理页重复绑定；只接受目录中列出的稳定标识（目录名），不接受任意文件路径。Skill 不存在、已停用、未绑定、文件缺失、编码无法识别、指令为空或超过上限（200,000 字符，复用 skill_store 的指令上限）时返回明确错误，不静默截断。
  - 加载后的完整指令作为工具结果保留在本次运行上下文；展示摘要只含"已加载 Skill「名称」（N 字符）"，不携带指令正文。
  - MCP 工具结果（含 `isError` 结果、超长结果的显式截断标记）经环境变量值与 API Key 双重脱敏后进入上下文。

- MCP 会话化（`app/services/mcp_client.py` 重构）：
  - 新增 `McpSession`：启动子进程 → initialize 握手 → `tools/list`（含分页，返回原始定义含 `inputSchema`）→ 运行期间可多次 `tools/call`（默认 60 秒超时，`mcp_tool_call_timeout_seconds` 可配）→ `close()` 统一收尾（stdin 宽限 → terminate → kill）。
  - 原 `discover_tools`（测试连接）改为基于 `McpSession` 的一次性封装，行为与错误分类不变。
  - Runtime 在所有退出路径（正常 / 错误 / 轮数耗尽 / 取消）的 finally 中关闭全部会话；收尾期间被再次取消也不中断其余清理。

- 取消与收尾：
  - Runtime 接收独立的 `asyncio.Event` 取消信号，内部用 watcher 把信号置位转换为对运行任务的取消，与任务被直接取消（`task.cancel()`）走同一条收尾路径；模型流式请求、工具执行、循环调度的等待点都会被及时打断。
  - `run_end` 事件恰好发出一次（内部 finished 守卫），任何异常路径（配置不可用、MCP 启动失败、未预期异常、取消）都有终态。

- 统一运行事件（camelCase，每个事件都带 `runId` 与运行内递增 `seq`）：
  - `run_start` / `run_end`（status：completed / cancelled / error / rounds_exhausted；reason + error{type,message,hint} + 最终正文 + 已保存消息）；
  - `model_request_start` / `model_request_end`（`callId` 形如 `mr-1` 配对；结束事件带状态、耗时与上游实际返回的 Token 用量，未返回时为 null 表示未知，不填零）；
  - `text_delta`（正文增量，带轮次与调用标识）；
  - `tool_call_start` / `tool_call_end`（调用标识配对，`toolType` 标记 builtin / mcp / skill，结束事件带状态、耗时与脱敏摘要）。
  - 模型流式接口重构为 `stream_chat_completion_events`：正文增量 + 流结束时给出累积完成的工具调用（按 index 合并分片）、结束原因与 usage；`tools` 参数仅在有工具时携带。
  - 后台日志记录运行标识、轮次、调用标识、工具名称、执行状态、结束原因与脱敏摘要；不记录完整上下文、Skill 指令正文或未处理的工具结果。

- 聊天接入（`app/services/chat_generation.py` 重写 + `app/api/routes/chat.py`）：
  - 发送消息的前置校验（生成中 / Agent 与模型可用 / 密钥可解密 / 上下文预估超限）与用户消息保存流程不变；校验通过后交给 `AgentRuntime` 后台执行，聊天层只做三件事：把运行事件转发给 SSE 订阅者、维护快照状态（阶段 / 已生成正文 / 当前工具）、在终态按状态保存回复正文（completed 保存，其余保存为未完成，无正文不建消息）并附加到 `run_end`。
  - 取消：停止生成（置位取消信号 + 任务取消并等待收尾）、关闭页面或浏览器断开（SSE 订阅全部离开且运行曾有过订阅者时触发取消）都会中止运行；已生成部分正文保存为未完成。Runtime 内部不监听浏览器连接，后续阶段可调整断连语义。
  - SSE：连接时补发快照（含 `runId`、`phase`、`toolName`）或最近终态；`run_end` 为唯一终态事件；15 秒心跳保留。`GET /messages` 的生成快照增加 `toolName`。
  - 无数据库结构变更，无新迁移；会话/消息表结构沿用阶段七。

### 前端

- `api/chat.ts`：事件类型更新为统一运行事件协议（run_start / model_request_* / text_delta / tool_call_* / run_end / snapshot / idle），新增 `RunUsage`、`RunError`、`RunStatus`、`ToolType` 类型；`ChatGeneration` 增加 `toolName`。
- `stores/chat.ts`：生成状态扩展为 `{phase, content, agentName, toolName, tools[]}`——正文跨轮次累积，工具调用轨迹按序保留（名称、状态、耗时、摘要）；`run_end` 按状态收尾：有 `message` 则落盘消息，有 `error` 则展示错误卡片（rounds_exhausted 以错误卡片提示"达到最大轮数"，cancelled 不弹错误）。模型请求元数据事件不驱动 UI。
- `views/ChatView.vue`：生成气泡内新增工具调用轨迹（执行中 / 完成 / 失败 + 耗时 + 脱敏摘要）；工具执行阶段显示"正在调用工具：×××"，工具栏徽标同步显示 思考 / 生成 / 调用工具；错误卡片与未保存内容提示沿用原交互。

## 验证方式

```bash
# 1. 后端
cd backend
uv sync
uv run pytest

# 2. 前端（另开终端）
cd frontend
npm install
npm run type-check && npm run test && npm run build
npm run dev
```

1. 纯对话回归：不绑定任何工具/Skill 的 Agent 正常流式回复、多轮上下文、停止生成（部分正文标"未完成"）、错误卡片、刷新恢复——与阶段七行为一致。
2. 工具循环：给 Agent 绑定"当前时间"后提问"现在几点"——页面先出现"正在调用工具：当前时间"，完成后显示时间摘要与最终回复；回复 = 工具前后正文拼接，刷新后消息完整保存。
3. Skill 按需加载：导入或手工创建一个 Skill（如"周报生成"）并绑定启用；提问符合其用途的问题——回复前出现"正在调用工具：加载 Skill"，完成后轨迹显示"已加载 Skill「×××」（N 字符）"；模型随后按指令继续调用其它工具。
4. 未授权调用：停用某工具后让模型调用它（或在 Skill 指令里要求使用未绑定工具）——后端拒绝并把错误交还模型，页面工具轨迹显示"失败"，运行不被冲垮。
5. 轮数上限：把 Agent 的最大执行轮数调为 1 并绑定一个会诱导连续调用的工具——运行以"达到最大执行轮数"错误卡片结束，已生成正文保存为未完成。
6. MCP 工具：绑定一个启用的 MCP Server（先测试连接成功）——运行期间其工具以 `mcp{server_id}_{名称}` 出现在可用工具中，调用结果展示在轨迹里；运行结束（含停止）后 `tasklist`/`ps` 确认不留子进程。
7. 取消：生成中点"停止生成"、或直接关闭页面——后端日志出现"运行被取消"，部分正文保存为未完成，MCP 子进程被清理。
8. 日志抽查：后端日志只有运行标识、轮次、调用标识、工具名、状态、耗时与脱敏摘要；看不到 Skill 指令正文、完整工具输出或密钥。
