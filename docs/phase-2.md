# 阶段二：模型管理

## 本阶段完成内容

在阶段一骨架上实现"模型管理"模块。仅支持兼容 OpenAI Chat Completions 接口的模型服务，统一一套调用逻辑。

### 后端

- 业务表（Alembic 迁移 `0002`）：
  - `model_configs`：显示名称、模型标识、服务地址、上下文长度、最大输出长度、温度、输入/输出/缓存命中输入单价（元/百万 token，NULL 表示未填写，与 0 免费区分）、是否默认、创建/更新时间。
  - `model_secrets`：API Key 的加密存储（与业务表分离），随模型删除级联清理。
- 密钥保护（`app/core/secret_box.py`）：
  - 主密钥为 32 字节随机数，首次使用时自动生成到 `backend/secret.key`（已在 `.gitignore` 中排除，不入版本库）。
  - API Key 使用 HMAC-SHA256 流加密 + encrypt-then-MAC 认证加密后落库，明文不出现在任何业务表、日志或接口响应中。
  - 列表/详情/保存接口只返回 `hasApiKey` 状态；编辑时留空即保留原密钥，仅填写新密钥才替换。
- 接口（`app/api/routes/models.py`，前缀 `/api/models`）：
  - `GET ""` 列表（默认模型置顶）；`GET /{id}` 详情；`POST ""` 新增（201）；`PUT /{id}` 全量编辑；`DELETE /{id}` 删除（支持 `new_default_id` 查询参数）；`PUT /{id}/default` 设为默认；`POST /{id}/test` 测试连接。
  - 校验：必填项、服务地址必须是 http(s) 完整地址且不得包含 `/chat/completions`、上下文长度与最大输出长度为正整数且最大输出 ≤ 上下文、温度 0–2、价格非负。失败返回 422 与 `{"message", "fields"}` 字段级原因。
  - 默认模型一致性（单事务）：保存第一个模型自动设为默认；设为默认自动取消旧的；删除默认模型且还有其他模型时必须携带新的默认模型 id；删除最后一个模型后回到空状态。
- 测试连接（`app/services/model_client.py`）：向 `{服务地址}/chat/completions` 发送一次真实小请求（max_tokens ≤ 64，20s 超时），按响应归类为：密钥错误 / 模型标识错误 / 服务地址不可用 / 超时 / 返回格式异常 / 服务端错误，并附排查建议；所有错误文案与回复内容都经过密钥脱敏。

### 前端

- `views/ModelsView.vue`：模型表格（显示名称+默认徽标、模型标识、服务地址、上下文长度、密钥状态、最后更新时间、操作），测试连接结果横幅（成功显示"模型已连接"+模型回复；失败显示原因与建议），空状态与添加入口。
- `components/ModelFormDialog.vue`：新增/编辑表单，含全部配置项与填写说明（服务地址示例、建议值标注、价格"留空=未填写、0=免费"）；编辑已配置密钥时输入框为空并提示"已配置，留空则保留原密钥"；字段级校验错误就地展示。
- `components/AppDialog.vue`：通用对话框外壳；删除确认对话框在删除默认模型时要求先选择新的默认模型。
- `views/model-form.ts`：表单校验与载荷构造的纯函数；`api/models.ts`、`stores/models.ts` 接口与状态。

## 验证方式

```bash
# 1. 后端
cd backend
uv sync
uv run alembic upgrade head        # 应用 0002 迁移
uv run pytest

# 2. 前端（另开终端）
cd frontend
npm install
npm run type-check && npm run test && npm run build
npm run dev
```

打开 http://localhost:5173/#/models （侧边栏"模型管理"）：

1. 空状态显示添加入口；新增第一个模型后自动成为默认模型。
2. 编辑模型不填密钥保存后，密钥状态仍为"已配置"。
3. 点击"测试连接"：按钮变为"正在测试…"且不可重复点击；成功显示模型回复，失败显示分类原因与建议。
4. 删除普通模型需确认；删除默认模型需先选择新默认；删除最后一个模型回到空状态。
5. 刷新页面、重启后端后配置与默认状态保留。

## 密钥安全说明

- `backend/secret.key` 是本地主密钥文件，删除后已保存的密钥将无法解密（测试连接会提示重新填写 API Key）。
- 密钥只存于 `model_secrets` 表（密文），不进版本库、不进前端存储、不写日志。
