# 阶段十五：阶段化开发工作流 + 执行治理档位 + 评测深化与仪表盘

> 对应产品文档「核心工作流」「执行治理」「评测思路」三处未实现项的 demo 落地（分支 `feature/coding-agent-mvp`，迁移 0015）。

## 一、阶段化开发工作流（原 📋 → ✅ demo）

**两种自主档位**（Agent 级配置，`agents.autonomy_mode`）：

- `stage_confirm` 阶段确认（默认）：Runtime 在系统提示词附加「阶段化开发工作流」协议，并注入 `finish_stage` 收尾工具（非 tools 表行，Runtime 虚拟工具，不需要绑定）。
- `autonomous` 高度自主：不注入协议与工具，行为与阶段十四之前一致。

**阶段确认模式的收尾协议**：开发 → 自检 → 用文件工具写交接文档（`handoff-阶段N.md`，含背景/进度/已做决策/文件位置/下一步/验证方式）→ 调用 `finish_stage(stageName, summary, handoffPath, nextStep)`。

**finish_stage 的运行时行为**：

- 参数校验失败按普通工具失败返回，模型可修正重调；成功则记录阶段信息（脱敏后随 `run_end` 事件下发 `stage` 字段），本次运行以新终态 `stage_complete`（reason `stage_finished`）结束，同轮剩余工具不再执行。
- `stage_complete` 属于正常收尾：Run 记录映射为 completed，聊天消息按 completed 保存。
- 聊天侧（`chat_generation`）在 stage_complete 收尾时把会话置为 `awaiting_acceptance=true`，阶段摘要存 `stage_summary_json`。
- 前端聊天页出现验收横幅（阶段名/成果概述/交接文档路径/下一步建议）。
- **注意**：本节最初实现的「验收通过后同会话继续下一阶段」流程（`POST /sessions/{id}/stage/accept`）已在阶段化开发 v2 中被**项目级多会话**流程取代（见第八节）；`awaiting_acceptance / stage_summary_json` 沿用为阶段会话的验收状态，用户在会话里继续发消息即视为接受并清除横幅。

## 二、执行治理档位（原 📋 → ✅）

**审批档位**（`agents.approval_level`，取代原布尔开关 `require_approval`，0015 已删列并回填）：

| 档位 | 范围 |
| --- | --- |
| `off` | 不审批（原开关关闭） |
| `standard` | 原行为：Shell run/background/stop、文件写入、edit_file 需审批；只读免批 |
| `strict` | 除查询时间外全部内置工具调用需审批 |

**Shell 命令白名单**（`agents.shell_whitelist`，换行/逗号分隔的命令前缀）：命中的 Shell 调用跳过人工审批直接执行；危险命令黑名单仍然生效，白名单只作用于审批这一层。

Agent 表单（`AgentFormDialog`）提供审批档位下拉、白名单多行文本与自主档位下拉；取值由后端 422 字段级校验兜底。

## 三、评测深化（原 📋 → ✅ demo）

- **过程评分（LLM Judge）**：评分提示词扩展为四字段输出 `{score, reason, processScore, processReason}`；评分材料新增「执行过程摘要」（来自该 Run 的 `tool_call_end` / `approval_request` 真实事件，最多 50 行）。`processScore/processReason` 缺省容忍为 NULL（兼容只回结果分的评分模型）。新列：`eval_task_results.process_score / process_reason`。
- **回归对比**：`GET /api/evaluations/tasks/{base}/compare/{target}`，要求同数据集；输出两侧聚合（平均分/过程均分/通过数）与差值（target − base），并按用例顺序逐条对照得分与过程分。评测页任务详情内新增「回归对比」折叠面板（V1→V2 选择器 + 汇总芯片 + 逐用例表格，涨绿跌红）。

## 四、工具仪表盘（原 📋 → ✅，2026-10-08 移除）

曾实现 `GET /api/runs/tool-stats?days=N`（按时间窗口聚合 `tool_call_end` / `approval_request`）与独立页面 `/tool-stats`；用户实际使用后评估价值不大（指标可从运行记录详情获得），已整体移除（端点、schema、页面、路由、侧边栏入口与测试）。

## 五、聊天轨迹展示（demo 需求三条）

1. **文件写入聚合卡**：组内 `builtin_file(write)` 与 `builtin_edit_file` 聚合为「修改了 N 个文件」一张卡（`FileWriteGroupCard`），默认收起；展开逐文件展示「路径 + 结果（新建/覆盖/追加/替换 N 处）+ 统一 diff（±行着色）或写入内容」。
2. **Shell 结果徽章**：✓/✗；`timedOut` → ✗「超时终止」；`background`/`autoBackgrounded` → ●「运行中」+ taskId（纯展示，不追踪真实状态）。
3. **回复正文全文显示**：正文段始终经 MarkdownContent 全文渲染，无折叠；思考卡、工具卡保持默认收起（现状不变）。

## 六、迁移与文件清单

- 迁移 `0015_stage_governance`：agents 加 `approval_level / autonomy_mode / shell_whitelist`（回填并删除 `require_approval`）；chat_sessions 加 `awaiting_acceptance / stage_summary_json`；eval_task_results 加 `process_score / process_reason`。
- 后端：`agent_runtime.py`（finish_stage、阶段协议、审批档位/白名单）、`chat_generation.py`（stage_complete 收尾）、`run_recorder.py`（状态映射）、`eval_scorer.py / eval_runner.py`（过程评分）、`routes/chat.py`（验收接口、会话阶段字段）、`routes/agents.py`、`routes/evaluations.py`（对比）。
- 前端：`chat-format.ts`（shellBadge / fileWriteEntry / partitionGroupTools）、`FileWriteGroupCard.vue`（新）、`ToolGroupCard.vue`、`ToolCallCard.vue`、`ToolStatsView.vue`（新）、`ChatView.vue`（验收横幅）、`EvaluationView.vue`（过程分 + 对比面板）、`AgentFormDialog.vue`、`agent-form.ts`、api/stores/router/sidebar。

## 七、边界与后续

- 浏览器页面验收仍未建设（Agent 只能验证接口与日志），维持规划状态。
- 白名单为前缀匹配（命令归一化空白后小写比较），不解析参数语义；过度宽泛的前缀等于放行一整类命令。
- 后台任务「● 运行中」徽章是展示态：任务可能已悄悄退出，真实状态以 `action=list` 的任务列表与退出通知为准。

## 八、阶段化开发 v2：项目级多会话（用户验收后重做，迁移 0016）

阶段十五 demo 的「同会话验收后继续」被否决，重做为**每个阶段一个独立会话**，由项目记录串联：

- **数据模型（迁移 0016）**：新表 `stage_projects`（title、agent 快照、workspace_dir 快照、plan_json `[{name, goal, status}]`、current_stage、状态机 `awaiting_start → in_progress → awaiting_summary → summarizing → completed`）；chat_sessions 加 `project_id / project_role('plan'|'stage'|'summary') / stage_index / workspace_dir`（项目钉住的工作区）。
- **意图路由（提示词约定，仅 stage_confirm 档位注入）**：从 0 到 1 的开发需求 → 模型调用 `submit_stage_plan(title, stages[2–8])` 提交计划（新虚拟工具，调用后运行以新终态 `plan_submitted` 结束，run_end 带 plan）；存量项目增量开发 / 问答走普通流程。拿不准时先向用户确认。
- **finish_stage 增强**：收尾时读取工作区内的交接文档内容放进结果卡片（约 4000 字符封顶），用户不用打开文件即可看到交接内容。
- **工作区代码强制**：项目会话创建时把项目钉住的目录写入 `chat_sessions.workspace_dir`，Runtime 的 `RuntimeRequest.workspace_dir` 覆盖优先于 Agent 配置——多会话操作同一工作区不靠约定。
- **状态机（`chat_generation`）**：plan_submitted → 建（或覆盖）项目、会话挂 role=plan；stage_complete → 项目该阶段标记 done，全部完成转 awaiting_summary；汇总会话运行 completed → 项目 completed（失败回退 awaiting_summary 可重试）。
- **接口**：`GET /api/chat/projects/{id}`（计划 + 各阶段会话映射）；`POST .../start`（开始阶段 1）、`.../next`（进入下一阶段，要求前一阶段已完成）、`.../summary`（生成汇总）——三者都由后端新建会话 + 自动发首条消息（计划 + 上阶段交接文档路径与下一步 + 本阶段目标 / 汇总要求），返回 `{project, session}` 供前端切换。旧 `POST /sessions/{id}/stage/accept` 已删除。
- **前端**：新 `ProjectStagesCard` 清单卡（○待办 / ◔进行中 / ✓完成，可跳各阶段会话；规划会话显示「开始阶段 1（将自动新建会话）」，阶段会话验收时显示「进入下一阶段 / 生成项目汇总（将自动新建会话）」）；验收横幅保留阶段摘要展示，普通（非项目）阶段会话继续对话即推进。
- **测试**：`test_stage_project_flow.py`（计划校验、工作区覆盖优先级、finish_stage 读交接、项目全生命周期 API 级状态机）。
