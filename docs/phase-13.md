# 阶段十三：coding agent MVP（工作区、内置工具与执行审批）

## 本阶段完成内容

在底座八个模块之上完成 coding agent MVP 的核心两块（分支 `feature/coding-agent-mvp`，暂未合并回 main）：

- Agent 绑定本机工作区目录：文件类工具的沙箱范围与 Shell 命令的执行目录跟随工作区；
- 新增三个内置工具：文件编辑（`builtin_edit_file`）、列目录（`builtin_list_dir`）、搜索代码（`builtin_grep`），补齐"看结构、搜代码、精确改文件"的最小闭环；
- 工具执行前人工审批：开启审批的 Agent 在执行高风险工具前暂停，等用户在聊天界面批准或拒绝，构成黑名单之外的第二层防护。

原计划第三模块的「Coding 预设」（Agent 编辑页一键填入 coding 系统提示词模板与工具勾选）暂缓未实现，见文末后续项。

## 数据结构（迁移 0011 / 0012 / 0013）

- `0011_add_agent_workspace`：agents 表加 `workspace_dir`（String(512)，默认空串 = 使用全局默认目录 `settings.tool_workspace_dir`）与 `require_approval`（Boolean，默认 False；本迁移只落列，审批逻辑由后续提交实现）；
- `0012_add_coding_tools`：向 tools 表插入三个新内置工具的种子行；同时 `0005_create_tools` 固定为只插最初三个工具 key，防止注册表扩充后重放迁移时重复插入；
- `0013_add_message_timeline`：chat_messages 加 `timeline_json`（Text，可空），保存回复的过程轨迹；纯文字回复为 NULL；
- 保存校验：`workspace_dir` 留空合法；填写时必须是已存在的绝对路径目录，否则 422，且不会自动创建目录。前端 Agent 表单做对应校验并提供「选择目录」按钮。

## 工作区与目录选择

- `ToolContext`（`app/services/tools/base.py`，携带 `workspace_root`）经统一执行入口 `execute_tool(tool, params, context)` 透传到各 handler；文件读写 / 编辑 / 列目录 / 搜索的路径解析与沙箱校验都以它为根，context 为 None 时回落全局默认目录（工具管理页的手动测试走该回落）；
- Shell 工具以工作区为 cwd 执行命令；
- Runtime 在 `_load_config` 读取 agent 的 `workspace_dir` 构造本次运行的 ToolContext（空串用默认）；
- 浏览器拿不到完整本机绝对路径，Agent 表单「选择目录」按钮由后端代劳弹出原生窗口：`POST /api/system/pick-directory`（`app/services/directory_picker.py`，tkinter 实现；路由加线程锁防叠窗，已有窗口打开时 409）；前端 fetch 不设超时，等待用户挑选完成再返回路径。

## 新内置工具

- `builtin_edit_file`：参数 path、old_string、new_string、replace_all（可选）。精确字符串替换：old_string 出现 0 次报"未找到"，出现多次且未开 replace_all 时报错并提示提供更长上下文；成功返回统一 diff（限长）与替换次数；
- `builtin_list_dir`：参数 path（默认工作区根目录）、recursive（可选，递归深度上限 3、条目上限 500），返回名称 / 类型 / 大小列表，目录在前按名称排序；
- `builtin_grep`：参数 pattern（正则，编译失败报参数错误）、path（可选子目录）、glob（可选文件名过滤）、case_insensitive（可选）、max_results（默认 50、上限 200），纯 Python 遍历工作区（不依赖系统 shell），跳过二进制与超限文件，返回 `{path, line, text}` 匹配列表与截断标记。

## 工具执行审批

- 审批范围（`AgentRuntime.tool_requires_approval`）：Shell 工具的任何命令（命令最难预判风险，这一版不做安全白名单）；文件读写工具 action=write 的写入操作（覆盖 / 新建 / 追加）；`builtin_edit_file` 的全部操作。读取、列目录、搜索、时间与 Skill 加载不需要审批；MCP 工具本版不纳入审批；
- 审批链路：`RuntimeRequest.approval` 提供审批钩子（`ApprovalHooks`）且命中范围时，Runtime 先发 `approval_request` 事件（requestId 形如 `apr-N`、工具名、脱敏后的参数）并挂起等待；批准则继续执行，拒绝把拒绝结果返回给模型供其调整方案而不是原样重试；等待超时（`settings.approval_timeout_seconds`，默认 300 秒）按拒绝处理；用户停止生成时挂起请求以 cancelled 收尾再取消运行；评测路径不提供钩子，一切照旧自动执行；
- 挂起登记在 `app/services/approvals.py` 的 `ApprovalRegistry`（requestId → future，支持按会话 / Run 查询与清理），聊天生成管理器负责创建与决议；
- 接口：`GET /api/chat/sessions/{id}/approvals` 查询挂起审批（刷新页面后恢复审批卡片），`POST /api/chat/sessions/{id}/approvals/{request_id}/decision` 提交决议（无挂起 404、会话未在生成 409）；
- `approval_request` / `approval_result` 事件照常进 Run 记录，Run 终态携带未决审批列表，运行详情时间线可回放当时批准或拒绝了什么；
- 黑名单与审批是两层独立防护：黑名单在执行层强制落实，被拦截的命令即使获得批准也不会执行；
- 前端：聊天页审批卡片（工具名、参数、批准 / 拒绝按钮，点击后置灰防重复提交），打开会话时拉取挂起审批恢复展示；Agent 编辑表单增加「执行审批」开关。

## 聊天轨迹展示（思考行与工具组卡片）

聊天页的过程展示按 agent 轨迹区样式重做，**默认全部收起，点击标题行才展开**：

- 运行状态行：生成中在气泡顶部显示「✦ 处理中 1m 52s」；开始时间由后端随 `run_start` 与重连快照（`startedAt`，epoch 毫秒）下发，刷新页面后仍准确；
- 思考行：推理模型的思考内容（`reasoning_content` / `reasoning` 增量）默认只显示「✦ 思考 1s」一行，展开才看正文；思考只进展示时间线，不写入保存的回复正文，也不进入对话上下文；
- 工具组卡片：「使用N个工具」把相邻的工具调用折叠成一组，展开后是逐次调用的子卡片；组内有失败 / 取消时在标题行用文字点明；
- 子卡片：`类型徽标 + 动作前缀 + 主参数 pill + 状态图标 + 耗时`（如「TS 已读取：src/agent.ts」），展开看「调用参数」与「返回结果」；动作前缀按工具生成（已读取 / 已写入 / 已编辑 / 已列出 / 搜索文件 / 执行命令），文件类徽标取扩展名；
- 分段规则：相邻工具合并成一组，正文、思考、审批、压缩卡片都会打断分组；审批与压缩卡片始终直接展示（审批需要用户操作）；
- 轨迹随消息入库（迁移 0013 给 `chat_messages` 加 `timeline_json`）：运行结束时把展示轨迹与回复正文一起保存，**刷新页面或重新打开会话后思考行与工具卡片照常恢复**（仍默认收起）；纯文字回复不写轨迹（列为 NULL）。入库前先收尾（思考补耗时、未完成的工具标记取消/未知、待决议审批标记已取消），思考正文按 4000 字符截断，入库与下发同一口径脱敏。终态 SSE 事件的消息载荷不带轨迹（同一批事件已由运行记录保存，避免重复放大）；无保存消息的收尾运行仍只在页面内存保留卡片。

后端新增事件 `reasoning_delta`（`model_client` 解析思考增量 → Runtime 转发 → `chat_generation` 合并进时间线并补耗时），逐条思考与正文增量一样不落库；状态与内容仍全部经过密钥脱敏后下发。

## 安全边界

- 危险命令黑名单是尽力而为的拦截，不等于安全沙箱；需要强隔离时应把后端整体放进容器或受限账号运行；
- 审批默认关闭，按 Agent 开启；开启后 Shell 与文件写入类操作执行前需人工确认；
- 评测任务复用同一 Runtime 但不触发审批，自动执行（黑名单照常生效）；
- 文件类工具只能访问工作区内的路径；Shell 命令的 cwd 落在工作区，但工作区不是 Shell 的硬边界，仍以黑名单兜底。

## 测试

后端：`tests/test_coding_tools.py`（三个新工具各场景）、`tests/test_system_api.py`（目录选择）、`tests/test_approvals.py`（审批流：批准 / 拒绝 / 超时 / 取消 / 评测不挂起）、`tests/test_approvals_api.py`（两端点正常与异常分支），以及 test_agent_runtime / test_agents_api / test_tool_executor / test_migrations 的相应扩展。前端：agent-form 新字段校验、chat store 审批事件处理、`ApprovalCard.spec.ts`。

轨迹展示：`tests/test_model_client.py`（思考增量两种字段名）、`tests/test_chat_api.py`（思考进时间线、补耗时、不进保存正文、快照带 startedAt、轨迹随消息入库可在刷新后恢复、纯文字回复无轨迹、终态事件不带轨迹）；前端 `ThinkingCard.spec.ts`、`ToolGroupCard.spec.ts`、`ToolCallCard.spec.ts`、`chat-format.spec.ts`（分组与动作前缀）、`ChatView.spec.ts`（默认收起、处理中计时）、`chat.spec.ts`（思考流合并、刷新后按消息轨迹恢复）。

已知 flaky：`tests/test_chat_api.py` 的 `test_full_compaction_flow_persists_summary` 在全量 pytest 下偶发失败（时序敏感），单跑稳定通过，与本阶段改动无关。

## 未完成与后续项

- **Coding 预设未实现**：Agent 编辑页一键填入 coding 系统提示词模板、默认勾选五个内置工具（文件读写 / Shell / 文件编辑 / 列目录 / 搜索）、打开审批开关（本阶段决定暂缓）；
- 手动全流程验收（新建 Agent → 指定工作区 → 聊天改文件 → 审批 → 运行记录回放）尚未正式执行；
- `feature/coding-agent-mvp` 分支未合并回 main。
