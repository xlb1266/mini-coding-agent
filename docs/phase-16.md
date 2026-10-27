# 阶段十六：聊天输入框改版——执行档位 / 上下文容量 / 模型切换（会话级覆盖）

> 仿 ZCode 输入框 UI：把执行档位从 Agent 管理页移到聊天输入框，新增上下文占用量仪表与模型切换菜单（分支 `feature/coding-agent-mvp`，迁移 0017）。

## 一、执行档位：三个预设，会话级覆盖

底层治理模型不变（审批档位 `off / standard / strict` × 自主档位 `stage_confirm / autonomous`），聊天输入框左下角把两个维度捏成三个预设组合，按自由度从高到低：

| 预设 | 审批档位 | 自主档位 | 说明文案 |
| --- | --- | --- | --- |
| 高度自主 | `off` | `autonomous` | 完全访问，无审批操作。 |
| 阶段确认模式（默认） | `off` | `stage_confirm` | 做完一个阶段生成交接文档并停下，等你验收后继续。 |
| 变更前确认 | `standard` | `stage_confirm` | 执行命令、修改文件前先请求人工审批。 |

- **作用范围为会话级**：切换写入 `chat_sessions.approval_level / autonomy_mode`（NULL = 跟随 Agent 配置），只影响当前会话；新会话沿用 Agent 配置，评测不受影响。
- 生效组合与预设完全匹配时菜单项打勾；不匹配（如存量 Agent 配了 `strict`）时芯片显示「自定义档位」，实际治理沿用 Agent 值，选中任一预设后即归一。
- **Agent 编辑表单移除「审批档位」「自主档位」两个下拉**（档位唯一入口在聊天输入框）；Shell 命令白名单保留在表单。表单编辑时原值随载荷回传，不改动存量 Agent 的档位；新建 Agent 默认 `off + stage_confirm`。
- 生成期间禁止切档位（409），与切换 Agent 的约束一致。
- 透传链路：`routes/chat.py` 解析会话覆盖 → `generation_manager.start(approval_level, autonomy_mode)` → `RuntimeRequest.model_id / approval_level / autonomy_mode` → `AgentRuntime._load_config` 优先取覆盖值（合法值校验后回落 Agent 配置）。

## 二、上下文容量仪表（估算口径）

- 输入框右下角环形仪表，按「估算输入 / 模型上下文长度」的百分比画弧，≥70% 转警告色、≥90% 转危险色；点开弹窗展示总量（如 `4.5万/100万（0.5%）`）、进度条与分类明细，不展示缓存命中率（无此数据）。
- **分类明细与 Runtime 的请求组装同一套口径**（`agent_runtime.estimate_fixed_context` 复用 `_skill_catalog_section / _stage_protocol_section / get_definition` 等私有构造）：系统提示词（含阶段协议）、技能目录、工具定义（内置 + load_skill + 阶段工具）、会话摘要、有效历史消息；按 token 占用降序展示。全部为估算值（CJK 每字 1 token、其余每 4 字符 1 token），弹窗注明「不含下次输入」；Agent 绑定 MCP Server 时额外提示「MCP 工具定义需连接后才能发现，未计入」。
- 新端点 `GET /api/chat/sessions/{id}/context-usage`；会话未绑定 Agent 或模型不存在时 409。
- 刷新时机：切换会话、回复结束、切换档位（影响阶段协议）、切换模型（分母变化）。

## 三、模型切换（会话级覆盖）

- 模型芯片显示当前生效模型 displayName（会话覆盖优先，其次 Agent 绑定；模型被删除显示「模型缺失」红色态）；点开为平铺菜单，逐项展示 displayName + modelIdentifier，当前项打勾，底部「管理模型」跳转模型管理页。不分组（模型表无厂商字段）。
- 切换写入 `chat_sessions.model_id`，只影响当前会话后续请求；生成期间禁止（409），模型不存在 422。
- ChatView 每次进入都刷新模型列表，模型管理页的增删及时反映到菜单。
- 上下文估算的可用容量按生效模型计算，切模型后仪表百分比随之变化。

## 四、输入框布局（ChatComposer 组件）

聊天输入框抽出为 `ChatComposer.vue`，布局对齐 ZCode：外层圆角容器，上方正文输入（无内边框，容器聚焦高亮），下方工具栏一行——左侧档位芯片菜单，右侧用量仪表、模型芯片、圆形发送按钮（图标态，生成中切换为红色停止按钮）。Enter 发送 / Shift+Enter 换行 / 中文输入法组字回车不发送的既有行为保持；「保存成功才清空输入框」经 `defineExpose({ clearDraft })` 由 ChatView 调用。弹层互斥（同一时间只开一个），点击外部或 Esc 关闭。

## 五、迁移与文件清单

- 迁移 `0017_session_governance`：chat_sessions 加 `approval_level / autonomy_mode / model_id` 三列（均可空，model_id 带 model_configs 外键）。
- 后端：`agent_runtime.py`（RuntimeRequest 三覆盖字段、_load_config 解析、三个工具描述符抽成共用构造、`estimate_fixed_context`、系统提示词组装统一到 `_effective_system_prompt`）、`chat_generation.py`（GenerationRun.autonomy_mode、start 透传）、`routes/chat.py`（mode/model/context-usage 三端点、发送时解析覆盖）、`schemas/chat.py`、`db/models/chat.py`。
- 前端：`ChatComposer.vue`（新）、`ChatView.vue`（接入 + 用量刷新）、`api/chat.ts`（MODE_PRESETS / matchModePreset / 三接口）、`stores/chat.ts`（changeMode / changeModel）、`AgentFormDialog.vue`（移除两下拉）。
- 测试：后端 `test_chat_api.py` 新增 TestSessionModeAndModel / TestContextUsage 共 10 例（含端到端：会话覆盖换模型 + 开阶段确认后，请求打到新模型且系统提示词带阶段协议）；前端新增 `ChatComposer.spec.ts` 12 例，ChatView.spec 适配（mock 保留 matchModePreset 真实导出、发送/停止按钮改为图标态选择器）。

## 六、边界与后续

- 上下文用量为发送前快照：生成过程中不实时刷新，回复结束后刷新一次；不含「下一次输入」的预估。
- 三个预设之外的组合（如 strict）无聊天入口，存量会话显示「自定义档位」；后续如需可在菜单加第四项。
- 浏览器验收已做：三菜单渲染与切换、会话覆盖持久化（刷新后保留）、切模型后用量分母变化均已确认。
