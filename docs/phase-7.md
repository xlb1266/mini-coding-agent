# 阶段七：聊天页面

## 本阶段完成内容

在已有骨架上实现"聊天"模块：用户选择 Agent 进行多轮对话，会话与消息全部落库，回复以 SSE 流式实时展示，刷新或重新打开后可继续聊天。本阶段只实现模型直接回答：不调用工具、不启动 MCP Server，也不加载 Skills。

### 后端

- 业务表（Alembic 迁移 `0007`）：
  - `chat_sessions`：标题（初始"新会话"）、当前选择的 Agent（`agent_id` 可空外键）、创建与更新时间。
  - `chat_messages`：所属会话、角色（user / assistant）、正文、完成状态（completed / incomplete）、会话内顺序号 `seq`（从 1 递增，读取按 `seq` 排序，不依赖时间戳）、生成时的 Agent 快照（`agent_id` + `agent_name`）。
  - Agent 被删除时不阻止删除：删除路由先把 `chat_sessions` / `chat_messages` 的 `agent_id` 置空，会话与历史消息保留，旧回复继续显示生成时的 Agent 名称；原会话继续发送前需要重新选择可用 Agent。
- 流式模型调用（`app/services/model_client.py` 新增 `stream_chat_completion`）：
  - 以 OpenAI Chat Completions 兼容格式发起 `stream: true` 请求，逐段产出正文增量；读超时 300 秒（作用于相邻数据块之间）。
  - 错误归类与非流式"测试连接"共用一套逻辑（密钥 / 模型标识 / 地址 / 超时 / 网络 / 服务端错误），新增 `context` 类型：上游 400 报文中出现 context length / token limit 等关键词时归类为"历史过长"。
  - 所有错误文案与落库正文都经过密钥脱敏，任何响应不回显 API Key；密钥只在后端解密使用，前端拿不到。
- 生成任务管理（`app/services/chat_generation.py`，进程内单例 `generation_manager`）：
  - 生成任务是与 HTTP 请求解耦的 asyncio 任务：前端切换会话、新建会话或刷新页面都不中断，回复在后台继续生成并最终保存。
  - 事件广播（SSE）：`snapshot`（连接时的快照：阶段 + 已累积正文）、`phase`（收到首段正文，thinking → generating）、`delta`（增量）、`done`（完成并保存成功）、`error`（失败原因 + 已生成内容 + 是否已保存部分正文）、`cancelled`（停止；无正文时 `message` 为 null）。`done` / `error` / `cancelled` 为终态，任务结束后保留一份最近终态（进程内），迟到的订阅者可补收，避免"发送后订阅前任务恰好结束"丢事件。
  - 停止：取消任务并等待收尾。已生成部分正文则保存为 incomplete；没有正文不创建空白回复。取消后未完成的回复不进入后续上下文。
  - 失败收尾：用户消息保留；有部分正文保存为 incomplete 并在 error 事件中带回；空回复（只收到结束符）按错误处理，不保存空白消息；错误提示不写入聊天记录。保存失败时事件明确提示"内容未入库"。
  - 上下文构造：系统提示词 + 当前会话按 `seq` 排列的有效历史（用户消息全部计入，assistant 仅 completed），刚保存的本次用户消息只计一次；不同会话历史完全隔离。发送前按"中文 1 token/字、其他 4 字符/token"的保守估算检查容量，超过 `context_length - max_output_tokens` 时直接返回 409 提示缩短输入或新建会话，不静默丢弃历史、不发请求。
  - 应用关闭时（lifespan）取消全部生成任务。
- 接口（`app/api/routes/chat.py`，前缀 `/api/chat`）：
  - `GET /sessions` 会话摘要列表（按更新时间倒序，只含摘要不含正文，带 `isGenerating` 标记与 Agent 名称）；`POST /sessions` 新建（不传 agentId 用默认 Agent；无可用 Agent 返回 409）。
  - `GET /sessions/{id}/messages` 打开会话：摘要 + 全部消息 + 进行中的生成状态（含已生成部分正文，供刷新后恢复）。
  - `PUT /sessions/{id}/agent` 切换 Agent（生成中 409）；`POST /sessions/{id}/messages` 发送（空内容 422；生成中 409；Agent / 模型 / 密钥不可用 409；上下文超限 409——校验全部通过后才保存用户消息并启动生成）；`POST /sessions/{id}/stop` 停止（幂等，无任务返回 not_running）。
  - `GET /sessions/{id}/events` SSE 事件流：连接只影响"谁收到推送"，断开不影响生成；15 秒心跳注释防代理断连。
- 首条用户消息保存后折叠空白截取前 30 字符作为会话标题，不调用模型生成；产生新消息时更新会话 `updated_at`，仅查看会话不改变排序。

### 前端

- `api/chat.ts`：接口定义与 `openChatEventStream`（fetch 流式读取 + SSE 解析：按空行分块、合并多行 data、跳过心跳注释与坏行，支持跨读块的事件拼接）。
- `stores/chat.ts`：生成状态与 SSE 连接挂在 store 而非组件上——切换会话、新建会话或离开聊天页面时连接保持，回复后台继续；回到会话时用 `/messages` 的生成快照恢复进度并重新订阅。终态事件按消息 id 去重（重放不产生重复气泡）；事件流意外断开时以后端状态为准自动重连（连续超过 3 次失败才提示），任务已结束则用后端最终状态补齐。
- `views/ChatView.vue`：左右分栏（左侧会话列表：新建按钮、标题、更新时间、Agent 名、"生成中"标记；右侧聊天区：Agent 选择、消息流、输入区）。要点：
  - 用户消息靠右、Agent 回复靠左；回复带 Agent 名称与时间，未完成回复标"未完成"。
  - "正在思考"（收到首段正文前）→ "正在生成"（增量追加 + 光标动画）；接近底部时自动跟随滚动，上滚回看不打扰。
  - 输入框 Enter 发送、Shift + Enter 换行；输入法组词状态（`isComposing` / keyCode 229）的回车不触发发送；空白内容发送按钮禁用。
  - 发送成功才清空输入；失败保留内容并显示原因。保存用户消息失败时不请求模型。
  - 生成中：发送变"停止生成"，Agent 选择与重复发送禁用；切换会话不受限（后台继续）。
  - 没有可用 Agent：提示先创建 Agent 并提供"前往 Agent 管理"入口，禁用发送；会话原 Agent 被删除时仍可查看记录，选择器提示先选择可用 Agent。
  - 失败提示以独立错误卡片展示（原因 + 排查建议 + 未保存内容说明），不混入聊天记录。
- `App.vue` / `base.css`：聊天路由下 `.page` 切换为 `page-fill`（占满内容区高度、放开宽度上限），左右分栏各自滚动。

## 验证方式

```bash
# 1. 后端
cd backend
uv sync
uv run alembic upgrade head        # 应用 0007 迁移
uv run pytest

# 2. 前端（另开终端）
cd frontend
npm install
npm run type-check && npm run test && npm run build
npm run dev
```

打开 http://localhost:5173 （聊天是默认页；模型与 API Key 在模型管理中配置，Agent 在 Agent 管理中创建）：

1. 无 Agent 时聊天页提示先创建 Agent，提供"前往 Agent 管理"入口；创建 Agent 后回到聊天页可新建会话（默认选中新会话，初始标题"新会话"）。
2. 发送第一条消息：标题变为消息前 30 字；回复先显示"正在思考"，收到正文后切换"正在生成"并逐步追加；完成后刷新页面，会话、消息与 Agent 选择全部恢复，可继续追问。
3. 多轮对话中模型能理解上下文（连续追问"再详细一点"有效）；两个会话历史互不混入。
4. 生成中点"停止生成"：部分正文保存并标"未完成"，发送与 Agent 选择恢复可用；空内容/纯空白不能发送；输入法组词时回车不发送。
5. 生成期间切换到其他会话或离开聊天页再回来：生成继续，回到会话能看到已生成部分并接续展示。
6. 把模型服务地址改错再发送：用户消息保留，错误卡片提示原因（不出现空白回复）；修复后可继续发送，未完成回复不会进入后续上下文。
7. 删除会话正在使用的 Agent：会话与记录保留，旧回复仍显示原 Agent 名；继续发送前要求重新选择 Agent。
8. 把模型的上下文长度调小到装不下历史：发送时明确提示"历史消息过长"，提示缩短输入或新建会话。
