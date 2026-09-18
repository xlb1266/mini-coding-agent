# 阶段十一：运行记录与可观察性

## 本阶段完成内容

为 mini-agent 增加运行记录与可观察性：用户能知道一次 Agent 请求是否成功、耗时多久、调用了几次模型和工具、Token 用量多少，以及失败发生在哪一步。不重复创建记录、不另写模拟日志——一次"用户发送消息并启动 Agent"对应一条 Run，事件全部复用统一 Agent Runtime 发出的真实运行事件（与聊天页 SSE 转发的是同一批）。后续评测通过同一套接口记录运行过程。

### Run 数据结构（迁移 `0009_create_run_records`，三张表）

- `agent_runs`（Run 主记录）：
  - 标识与关联：`run_uid`（Runtime 运行标识）、`session_id` / `agent_id` / `model_id`（可空关联，仅为参考）；
  - 快照：`agent_name` / `model_name` / `model_identifier` 直接取运行开始时的配置，后续改名或删除不影响历史记录；列表展示不回查 Agent / 模型表；
  - 状态与结束原因分开保存：`status`（running / completed / partial / cancelled / failed）与 `finish_reason`（model_finished / max_rounds / cancelled / model_error / config_error / mcp_start_failed / runtime_error / context_overflow / interrupted）；
  - 指标（结束事件一次性写入，不由事件逐条累加）：`started_at` / `finished_at` / `duration_ms`、`model_calls`、`tool_calls`、`rejected_tool_calls`、`first_output_ms`、`prompt_tokens` / `completion_tokens` / `total_tokens` / `cached_tokens`（全 NULL 表示未知，不填 0）、`tokens_incomplete`（统计不完整标记）；
  - 摘要：`error_summary`（脱敏截断，正常完成为 NULL）、`user_input_preview`（脱敏截断，不保存全文）。
- `agent_run_events`（安全事件）：`run_uid` + `seq` 唯一（重复投递不重复插入），`event_type` 与 `payload_json`（脱敏后的 Runtime 事件原样，camelCase 字段与聊天页 SSE 一致）；text_delta 流式增量不落库（正文保存在聊天消息里）。
- `agent_run_event_details`（详细载荷）：一行事件至多一份；保存工具完整结果（脱敏、4000 字符截断）与最终正文片段（脱敏、4000 字符截断）；读取入口只有 `/api/runs/{id}/events/{eventId}/detail`，按需加载。

### Run 生命周期与状态

- 创建：发送消息校验通过后，`GenerationManager.start()` 预生成 `run_uid` 创建 running 记录并传给 Runtime（`AgentRuntime.run()` 新增 `run_id` 参数）；同一会话生成中重复发送（409）不创建。
- 终态映射（Runtime 终态 → Run 状态）：completed → 成功；rounds_exhausted → 部分完成（不是失败）；cancelled → 已取消；error → 失败。单个工具失败不把 Run 标为失败（Agent 可继续完成）；模型失败前已输出的部分正文保存为聊天未完成消息，Run 仍标记失败并保留正文片段（详细载荷）。
- 收口：所有收尾路径（正常结束 / 停止 / 取消补发 / 异常补发）都经过 `GenerationManager._finish`，`run_end` 事件插入与 Run 终态更新在 `RunRecorder.finish()` 的同一事务里完成——状态、指标汇总与事件保存保持一致。
- 后台运行：浏览器是否连接不影响记录（后台任务写库与 SSE 解耦）；断连沿用上一阶段机制（断连即取消），终态仍写入原 Run。
- 异常中断恢复：应用启动时 `mark_interrupted_runs()` 把遗留 running 记录标记为 failed + `interrupted` + "运行中断：后端服务重启或异常退出"，不会永久停留在运行中；会话工厂与请求依赖同源（测试随 dependency_overrides 换库）。

### 事件复用方式

Runtime 的 `on_event` 回调同时驱动两条路径：SSE 转发给聊天页（原机制）与 `RunRecorder.record_event()` 落库（新增）。两路消费同一批事件、同一套名称（displayName / serverName）/ 状态（success / error / cancelled / running）/ 摘要（summary）规则：

- 事件带 `runId`、运行内递增 `seq`（稳定顺序字段）与调用标识（模型请求 `callId`、工具 `callId`、压缩 `compactionId`），配对靠标识而非时间戳或名称；
- run_end 落库前剥离大载荷：`message`（已保存消息不重复入库）置空、`content` 移入详细载荷并留 `contentPreview`；tool_call_end 的 `result` 移入详细载荷并标 `hasDetail`；
- 事件与详细载荷写入前统一脱敏（密钥 + 绝对路径，见下）。

### 指标口径

- 总耗时：Run 开始到进入最终状态（`run_end` 事件携带 `durationMs`）。
- 模型调用次数：实际发出的模型请求次数，含失败请求与摘要生成请求（发出前计数）。
- 工具调用次数：实际进入工具执行入口的次数；权限或参数校验被拒绝（未知工具 / 参数非法 / 绑定或启用复检失败 / MCP 连接不可用）单独计为 `rejectedToolCalls`，不计为已执行。
- Token 用量：各次模型请求返回用量相加；`cachedTokens` 为输入中缓存命中部分（OpenAI 口径已含在 promptTokens 内，不重复相加）；上游未返回时显示"未知"，部分请求未返回时标"统计不完整"（`tokensIncomplete`），不填 0。
- 首个输出耗时：Run 开始到首个非空 text_delta（model_client 只对非空正文发 text 事件，空块与工具参数片段不触发）；无正文输出时为 null，前端显示"无正文输出"。

### Runtime 与模型客户端改动

- `_normalize_usage` 提取缓存命中（兼容 `prompt_tokens_details.cached_tokens` 与顶层 `cached_tokens`）；
- `run_start` 事件带 `startedAt`；`run_end` 事件新增 `durationMs` / `firstOutputMs` / `tokenUsage`（含 `incomplete`）/ `rejectedToolCalls`；`RunResult` 同步携带；
- 新增 `redact_paths`（Windows 盘符与 Unix 用户目录绝对路径 → `…/<文件名>`）与 `sanitize_display`（密钥 + 路径两层脱敏），应用于工具参数展示、结果载荷、工具摘要与 run_end 正文片段；进入模型上下文的工具结果保持原样（执行任务需要真实路径）。

### 查询与页面

- `GET /api/runs?status=&agentId=&page=&pageSize=`：按开始时间倒序分页；只读 agent_runs 行（count + 页数据两条 SELECT，无逐行回查，查询次数与行数无关）；状态与 Agent 筛选（驼峰 alias）。
- `GET /api/runs/{id}`：运行摘要 + 按 seq 排序的安全事件（LEFT JOIN 只取详细载荷主键判断有无，不读内容）。
- `GET /api/runs/{id}/events/{eventId}/detail`：详细载荷（校验事件属于该 Run，否则 404）。
- 前端：`RunsView` 列表（状态/Agent 筛选、分页、每行状态/Agent/模型/开始时间/耗时/调用次数/Token/错误摘要，拒绝次数单独标注）+ `RunDetailView` 详情（摘要卡 + 时间线）；时间线由 `runs-format.ts` 的 `buildTimeline` 从事件构建，工具卡片与压缩卡片直接复用聊天页组件（`ToolCallCard` 新增 footer 插槽放"加载完整结果"，聊天页零影响），模型步骤展示名称/状态/耗时/单次用量，run_end 展示终态/原因/正文片段（按需加载完整正文）。

## 验证结果

- 后端 `uv run pytest`：388 通过（新增 `tests/test_runs_api.py` 24 个：成功 / 工具失败后完成 / 模型失败保留部分正文 / 取消 / 轮数耗尽 / 后台无订阅完成 / 断连取消 / 启动中断恢复 / Token 汇总含缓存与统计不完整 / 未知不填 0 / 拒绝单独计数 / 首输出耗时 / 事件配对与去重 / 聊天与详情状态一致 / API Key 与绝对路径脱敏 / 详细载荷受控读取与跨 Run 404 / 分页筛选倒序 / 快照不受改名影响 / 查询次数与行数无关 / 列表不含事件）。
- 前端 `npm run type-check && npm run test && npm run build`：235 个测试通过（新增 runs-format 时间线与格式化 8 个、runs store 5 个、RunsView 6 个、RunDetailView 6 个），类型检查与构建通过。
- 迁移：`0009_create_run_records` 已应用（`alembic current` = 0009）。
