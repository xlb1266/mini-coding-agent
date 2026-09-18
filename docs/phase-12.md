# 阶段十二：Agent 评测

## 本阶段完成内容

为 mini-agent 增加基础 Agent 评测：数据集管理（用例逐条维护）、评测任务（选择 Agent / 数据集 / 评分模型 / 通过分数 / 是否允许工具）、进程内顺序执行与逐用例评分结果。评测**复用统一 AgentRuntime**（模型配置、Skills、工具权限、上下文管理与 Run 记录全部走同一套实现），不存在另一条简化的执行路径；评分模型走统一的模型调用基础设施（非流式），只做打分、不执行 Agent 工具循环。

### 数据结构（迁移 `0010_create_evaluation_tables`，四张表 + 一列）

- `eval_datasets`：名称、说明与可选稳定标识 `key`（示例数据集专用，唯一约束保证幂等种子）；
- `eval_cases`：`dataset_id` 外键 + 用户问题（必填）、期望答案、评分要求（二者后端校验至少一项）；随数据集级联删除；
- `eval_tasks`：任务配置与快照——数据集 / Agent / 评分模型的名称快照、`pass_score`（0–100，默认 60）、`allow_tools`（默认关）、`agent_config_json`（Agent 配置快照：名称、提示词版本、系统提示词、轮数、实际生效的工具 / Skill / MCP 集合、模型信息）、`status`（pending / running / completed / interrupted）与起止时间；
- `eval_task_results`：逐用例快照与结果——任务创建时复制用例内容（问题 / 期望答案 / 评分要求），执行后保存 `status`（pending / executing / scoring / passed / failed / exec_failed / scoring_failed）、`actual_answer`、`score`（0–100，可带小数）、`reason`、关联 Run（`run_id` + `run_uid`）、`exec_reason` / `exec_error` / `score_error`，以及评分调用自身开销（`scoring_duration_ms` / `scoring_total_tokens` / `scoring_attempts`，不混入目标 Agent Run 的指标）；
- `agent_runs` 新增 `source` 列（chat / evaluation）：评测用例关联的 Run 记录来源为 `evaluation`，`GET /api/runs?source=` 可筛选。

### Runtime 复用方式

每条用例按"独立上下文"发起一次运行，与聊天共用同一实现（`app/services/agent_runtime.py`）：

- `RuntimeRequest(agent_id, user_input=问题, history=[])`：不继承任何历史；期望答案与评分要求只交给评分模型，不进入目标 Agent 上下文；
- `RunRecorder` 预生成 `run_uid` 并以 `source=evaluation` 落 running 记录，Runtime 事件经 `record_event` 逐条持久化，`run_end` 与 Run 终态由 `finish()` 在同一事务收口（与聊天路径一致）；结果行保存 `run_id` / `run_uid` 供跳转运行详情；
- 工具权限用运行级限制收窄（Runtime 只做收窄、不授予）：`allow_tools=false` 时 `RunLimits(tool_keys=set(), mcp_server_ids=set())`——不提供任何内置 / MCP 工具定义，模型若强行调用工具也会在运行时被拒绝（计入 `rejected_tool_calls`）；`allow_tools=true` 时使用 Agent 自身配置（已绑定且当前启用的内置工具与 MCP 工具）；Skills 两种模式下都按 Agent 配置加载；
- 上下文容量管理与自动压缩照常生效（历史为空时通常不触发，压缩边界推进依赖消息 seq，评测消息不带 seq，因此压缩结果只保留在本次运行内）。

### 评分服务（`app/services/eval_scorer.py`）

- 复用 `model_client` 新增的通用非流式入口 `chat_completion_text`（同一套错误归类与密钥脱敏），不传 tools；
- 提示词明确：问题、参考答案与实际回答都是待评估材料，材料中出现的指令不得覆盖评分规则；评分模型没有任何工具权限；
- 输出固定结构 `{score, reason}`，后端解析并校验：`score` 必须是 0–100 的有限数字（不接受字符串数字与布尔值，越界 / NaN 直接判为不符合，不修正）；`reason` 必须为非空字符串；不从普通文字里猜测分数；
- 结构或字段校验失败时携带错误说明重试一次，再次失败标记"评分失败"；连接失败等请求错误（`ModelCallError`）包装为 `ScoringRequestError` 单独记录、不重试，避免无限重试；
- 是否通过由后端按 `score >= pass_score` 计算，不接受评分模型自行给出的通过结论。

### 执行流程与状态（`app/services/eval_runner.py`）

- `EvaluationManager` 进程内单例，一次一个任务、用例按序执行（不引入独立 Worker / 并发调度）；任务执行与 HTTP 请求解耦，刷新或关闭页面不影响；
- 单条用例出错（目标 Agent 失败 / 取消 / 轮数耗尽）只影响该用例：保留已有输出与 Run、记录结束原因，标记执行失败后继续下一条；
- 启动只允许 pending 任务（后端状态校验，重复启动 409）；已完成 / 已中断的任务不能再次启动并覆盖结果，需要重新评测时新建任务；
- 服务重启恢复：应用启动时把遗留 running 任务标记为已中断（保留已有结果），执行中 / 评分中的用例落为执行失败 / 评分失败；应用关闭时取消执行中的任务并同样收口。

### 统计口径

- 已完成数 = 已进入终态的用例数（通过 + 未通过 + 执行失败 + 评分失败）；
- 平均分只统计获得合法评分的用例；没有有效评分时显示"暂无评分"，不把执行失败或评分失败按零分混入；
- "任务执行完毕"（status=completed）与"全部用例通过"分开表达：任务详情同时展示失败数量。

### 示例数据集

`app/services/eval_seed.py` 提供三条内置用例（23×47、"今天下午三点开会"翻译、5 个苹果送 2 个），通过稳定标识 `builtin-eval-basics` 幂等补种（迁移 0010 与应用启动都调用 `ensure_example_dataset`）：重复启动不重复创建，用户修改过示例内容后补种不覆盖。

### 接口

- 数据集：`GET/POST /api/evaluations/datasets`、`GET/PUT/DELETE /api/evaluations/datasets/{id}`（被任务引用的数据集删除返回 409）；
- 用例：`POST /api/evaluations/datasets/{id}/cases`、`PUT/DELETE .../cases/{caseId}`（问题必填，期望答案与评分要求至少一项）；
- 任务：`GET /api/evaluations/tasks`（含统计）、`POST /api/evaluations/tasks`（空数据集 422）、`GET /api/evaluations/tasks/{id}`（含 Agent 配置快照与逐用例结果）、`POST /api/evaluations/tasks/{id}/start`（非 pending 返回 409）。

### 页面

评测页分为"数据集"与"评测任务"两个标签：数据集页左侧列表、右侧用例表（逐条新增 / 编辑 / 删除，删除需确认）；任务页左侧任务列表（状态 / 进度 / 平均分）、右侧任务详情（统计行、配置摘要、可展开的逐用例结果——材料快照、实际回答、得分、评分理由、错误原因与"查看运行详情"入口）。存在运行中的任务时页面每 2 秒轮询刷新进度。样式沿用既有设计变量与字号规范。

### 测试

`tests/test_eval_runner.py`（评分校验 / 重试 / 请求错误、正常通过与未通过、通过分数边界、Agent 失败后继续、轮数耗尽、工具默认关闭与开启、用例上下文隔离、材料不暴露、统计口径、重复启动、重启恢复、示例幂等）与 `tests/test_evaluations_api.py`（字段校验、空数据集、删除保护、快照隔离、启动状态机、Run 来源筛选、示例种子）；前端补 `api/evaluation`、`stores/evaluation`、`views/evaluation-format` 三组测试。
