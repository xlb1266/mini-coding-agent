# 阶段十：上下文压缩

## 本阶段完成内容

为统一 Agent Runtime 增加上下文容量管理与自动压缩：每次模型请求前按稳定估算方法估算请求占用（系统提示词、Skills 目录、工具定义、会话摘要、边界之后的历史与本次运行新增消息/工具结果），估算输入达到可用输入容量（上下文长度 − 输出预留 − 安全余量）的触发比例时，把较早历史交给当前模型分批整理成会话摘要继续使用。压缩只在 Agent Runtime 中实现，聊天与后续评测共用；原始聊天记录不被删除或改写。所有 Token 数字均为"估算值"，与模型接口返回的实际用量（usage）严格区分。

### 后端

- Token 估算与容量（`app/services/token_estimation.py` 新增，自 chat_generation 抽出复用）：
  - `estimate_tokens`：CJK 每字 1 token、其余每 4 字符 1 token，宁可高估不低估；
  - `estimate_messages_tokens`：消息正文、tool_calls 结构与工具定义 JSON 一起估算；
  - 可用输入容量 = 上下文长度 − 模型配置的最大输出 Token（输出预留）− 安全余量（上下文长度 5%，下限 64 token），不能把整个窗口用于输入。
- 压缩纯逻辑（`app/services/context_compaction.py` 新增）：容量计算、摘要目标长度收窄（不超过可用输入 1/4 与输出预留，小窗口模型下不占满上下文）、历史按用户消息分轮、本次运行按 assistant+tool 完整交互组分片、待压缩轮组按估算贪心分批（单组超预算独立成批）、摘要生成指令与请求构造、进入上下文的摘要包装消息（明确定位为背景参考，不替代系统提示词、不提升历史内容优先级）。
- Agent 配置（`app/db/models/agent.py`、迁移 `0008_add_context_compaction`、`app/schemas/agent.py`、`app/api/routes/agents.py`）：
  - 新增四个持久化字段：`auto_compact`（默认开启）、`compact_threshold`（触发比例百分比 10–95，默认 80）、`compact_keep_rounds`（保留最近对话轮数 1–50，默认 4）、`compact_summary_tokens`（摘要目标长度 100–32000，默认 1000）；接口层驼峰命名并带字段级中文校验提示。
- 会话压缩状态（`app/db/models/chat.py`、迁移 0008）：`chat_sessions.summary_text`（当前有效摘要）与 `summary_boundary_seq`（已压缩到哪条消息的边界）。
- Agent Runtime（`app/services/agent_runtime.py`）：
  - 消息布局改为 `_ContextPlan`（系统消息 + 摘要消息 + 边界之后历史 + 本次运行消息）；`RuntimeRequest` 增加会话摘要、边界与 `persist_summary` 持久化回调（无回调时压缩结果只保留在本次运行内，评测可直接复用）；
  - 每轮模型请求前执行 `_manage_context`：估算 → 达到触发比例且存在可压缩历史时先自动压缩 → 重新估算仍超容量时备用裁剪 → 必须保留的内容本身超容量则以 `context_overflow` 结束运行并明确提示；
  - 自动压缩（`_compact_history`）：只压缩边界之后、保留窗口之外的较早完整轮；已有摘要作为更新基础与新增消息一起生成新摘要；摘要请求自身满足容量限制（固定部分 + 输出预留内分批），不递归触发压缩；摘要生成为真实模型调用（计入 model_calls，事件带 `purpose: context_compaction`、callId `cmp-N-序号`），不占用执行轮数，但有运行级独立限制（`MAX_COMPACTION_MODEL_CALLS = 8` 次、总耗时 180s），超限按部分完成处理；
  - 压缩成功后经回调把新摘要与边界一致保存（线程中执行），再把上下文切到"摘要 + 边界之后的消息"；部分批次成功时保存已完成部分；保存失败或首批失败时保留上一份有效摘要与边界，不推进边界；
  - 取消响应：压缩中收到取消信号会给在途请求与压缩卡片补发 cancelled 终态后中止，不做持久化；
  - 备用裁剪（`_fallback_trim`）：按完整消息组从最早内容开始减少（先最早历史轮，再本次运行已完成的工具交互组，assistant 调用与配对结果整组删除），只影响本次请求，不更新边界、不删除历史；本次用户消息与未配对交互不会被裁掉；
  - 关闭自动压缩：不生成摘要、不裁剪，超容量时以 `context_overflow` 结束并提示"缩短输入 / 开启自动压缩 / 新建会话"，不静默丢弃历史。
  - `run_end` 事件补充 `modelCalls` / `toolCalls`。
- 运行事件：`context_compaction_start` / `context_compaction_end`（触发原因、压缩前后估算 Token、处理与保留的消息数量、批次数、模型调用次数、摘要估算长度、边界、耗时、失败原因）与 `context_trim`（前后估算 Token、裁掉/保留数量）。事件与日志只含估算数字与可读原因，不写完整历史或摘要正文。
- 聊天接入（`app/services/chat_generation.py`、`app/api/routes/chat.py`）：
  - 发送前校验改为容量口径：必须保留内容（系统提示词 + 会话摘要 + 本次输入）超容量直接拒绝；历史超容量且关闭自动压缩时拒绝（提示开启压缩），开启时放行交给 Runtime 压缩；
  - 历史只带边界之后的消息（含 seq），生成任务携带会话摘要/边界并注册 `persist_summary` 回调（新摘要与边界一致写库）；
  - 生成阶段新增 `compacting`；时间线增加 `compact` 卡片段（压缩起止按 compactionId 配对、备用裁剪直接追加），随 SSE 与快照下发。

### 前端

- `api/agents.ts`：Agent 配置类型增加四个压缩字段（列表、详情与提交载荷）。
- `api/chat.ts`：新增 `context_compaction_start` / `context_compaction_end` / `context_trim` 事件与 `compact` 时间线条目类型；模型请求事件增加可选 `purpose`；生成阶段增加 `compacting`。
- `stores/chat.ts`：时间线段落扩展为正文 / 工具卡片 / 压缩卡片三类；压缩开始进入 compacting 阶段并追加"压缩中"卡片，结束按 compactionId 配对原地更新，备用裁剪追加"已裁剪"卡片；收尾时运行中的压缩卡片与工具卡片同样标记"已取消 / 状态未知"；无保存消息的收尾运行保留卡片（`orphanTools` 更名为 `orphanSegments`，只存卡片段）。
- `components/CompactionCard.vue`（新增）：压缩/裁剪过程卡片，展示触发原因、估算 Token 前后、整理与保留消息数、摘要估算长度、批次与模型调用次数、耗时与失败原因；不含摘要正文。
- `views/ChatView.vue`：三处时间线渲染接入压缩卡片；阶段徽标与"正在压缩上下文…"提示；孤儿卡片按段类型渲染。
- `components/AgentFormDialog.vue` / `views/agent-form.ts`：新增"上下文压缩"配置区块（自动压缩开关 + 触发比例 / 保留最近轮数 / 摘要目标长度），带字段说明、范围校验（关闭开关时参数仍校验以保持合法）与编辑回填。

## 验证方式

```bash
# 1. 后端
cd backend
uv sync
uv run alembic upgrade head
uv run pytest

# 2. 前端（另开终端）
cd frontend
npm install
npm run type-check && npm run test && npm run build
npm run dev
```

1. 压缩配置：Agent 管理编辑任意 Agent——出现"上下文压缩"区块，默认开启、触发比例 80、保留 4 轮、摘要 1000；填 5、0、50 等越界值保存时字段旁提示原因；关闭开关后三个输入框置灰，保存后重新打开已回填。
2. 触发压缩（小窗口模型）：给模型配置填一个很小的上下文长度（如 2000），给 Agent 发送多轮长消息——达到阈值的那一轮先出现"压缩中"卡片（含触发原因与估算 Token），完成后变为"已完成"，展示估算 Token 前后对比与"整理 N 条，保留 M 条"；回复正常生成。
3. 历史保留：压缩完成后向上翻阅——所有原始消息仍在，没有被删除或改写；刷新页面后历史完整。
4. 增量压缩：继续对话再次触发压缩——压缩卡片的边界与整理数量只覆盖新增部分；长会话摘要不再从第一条消息重新整理。
5. 关闭压缩：把 Agent 的自动压缩关掉，继续在长会话发消息——不会出现压缩卡片；超出容量时收到明确错误提示（"请缩短输入内容、在 Agent 管理中开启自动压缩，或新建会话"），历史不被丢弃。
6. 失败回退：让摘要请求失败（如把 base_url 临时改错）——出现"失败"卡片后回复仍能生成，会话末尾出现"已裁剪"卡片（只影响本次请求）；恢复地址后下次压缩正常。
7. 大工具结果：绑定"当前时间"或 Skill 让单轮工具结果很大——下一轮请求前出现"已裁剪"卡片；请求中调用与结果成组消失，没有孤立卡片。
8. 停止生成：压缩进行中点"停止生成"——压缩卡片与运行收尾为"已取消"，历史与旧摘要/边界保持不变。
