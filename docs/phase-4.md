# 阶段四：Skills 管理

## 本阶段完成内容

在已有骨架上实现"Skills 管理"模块。Skill 是一份供 Agent 阅读的任务说明，描述处理某类任务时需要遵守的步骤、输出格式和限制；本身不执行代码，也不直接调用工具。

### 存储格式

- Skill 统一保存在项目根目录的 `workspace/skills/` 下，每个 Skill 一个独立目录，内含 `SKILL.md`：
  - frontmatter 提供 `name`（显示名称）与 `description`（简短说明）；
  - `---` 之后的 Markdown 正文为详细指令，即交给 Agent 的任务说明。
- 业务数据入库（Alembic 迁移 `0004`）：`skills` 表保存 `dir_name`（目录名，唯一稳定标识）、名称/说明缓存、`enabled` 启用状态与创建/更新时间；正文以文件为唯一数据源。
- 目录名与显示名分离：编辑名称只改 frontmatter，不重命名目录；`updated_at` 取 `SKILL.md` 的修改时间，手动改文件后刷新即可反映。

### 后端（`app/api/routes/skills.py`，前缀 `/api/skills`；文件逻辑在 `app/services/skill_store.py`）

- `GET ""` 列表：每次请求都重新扫描 `workspace/skills` 目录并同步数据库——登记新目录、刷新名称/说明/更新时间、清理已删除目录的记录。手动在目录里创建或删除 Skill 后无需重启服务。
- `GET /{dir_name}` 详情：返回完整 `instructions`（直接读文件）。
- `POST /import` ZIP 导入（201）：
  - 校验：必须是有效 ZIP；路径安全检查（拒绝 `..` 段、冒号等不安全路径，统一按 `/` 处理反斜杠）；结构检查（`SKILL.md` 位于根目录，或位于唯一的顶层文件夹内）；内容检查（`SKILL.md` 非空）；编码兼容 UTF-8（含 BOM）与 GBK；大小上限 50MB。
  - 目录名来源：压缩包内顶层文件夹 > frontmatter `name` > ZIP 文件名，非法字符替换为 `-`。
  - 同名 Skill 已存在时返回 409，不覆盖已有内容；解压过程出错会清理半成品目录。
- `PUT /{dir_name}` 编辑保存：校验名称（≤64 字）、用途说明（≤200 字）、详细指令（≤200000 字）后写回 `SKILL.md` 并刷新数据库信息与更新时间。
- `PUT /{dir_name}/enabled` 启用/停用：只改数据库状态；停用后文件保留在磁盘上，不再提供给 Agent 使用。状态持久化，刷新页面或重启服务后保留。
- `DELETE /{dir_name}` 删除：删除整个 Skill 目录与登记记录。
- 字段校验失败返回 422 与 `{"message", "fields"}`；路径非法一律按 404 处理。

### 前端

- `views/SkillsView.vue`：列表页（名称+目录名、简短说明、启用/停用徽标、更新时间、操作列），工具栏含"刷新"（重新拉取并触发后端扫描）与"导入 Skill"；导入对话框（选择 ZIP、结构说明、失败原因就地展示、成功显示保存位置横幅）；删除前二次确认，文案注明将删除的目录。
- `views/SkillEditView.vue`（路由 `/skills/:dirName/edit`）：编辑页提供名称、用途说明、详细指令三个区域；指令为大面积等宽字体 textarea（最小高 420px，可纵向拉伸），支持 Markdown，带字数统计；字段级错误就地展示，保存成功后回到列表。
- `api/skills.ts`、`stores/skills.ts`：接口定义与状态管理；`request.ts` 新增 `postForm`（FormData 上传不设置 JSON Content-Type）；`views/skill-form.ts` 表单校验纯函数（与后端限制一致）。
- 页面配色、字号、布局沿用 `variables.css` 设计变量与既有管理页样式。

## 验证方式

```bash
# 1. 后端
cd backend
uv sync
uv run alembic upgrade head        # 应用 0004 迁移
uv run pytest

# 2. 前端（另开终端）
cd frontend
npm install
npm run type-check && npm run test && npm run build
npm run dev
```

打开 http://localhost:5173/skills （侧边栏"Skills 管理"）：

1. 空状态提供导入入口；导入包含 SKILL.md 的 ZIP 后列表出现新 Skill。
2. 在 `workspace/skills` 下手动创建 `某目录/SKILL.md`，点击"刷新"即可出现在列表中，无需重启。
3. 导入缺少 SKILL.md、含 `../` 路径或非 ZIP 文件时，对话框内显示具体失败原因；重复导入同名 Skill 返回"已存在同名 Skill"且不覆盖。
4. 编辑页修改名称/说明/指令保存后，`SKILL.md` 内容同步更新，列表的名称与更新时间刷新。
5. 停用后状态列变为"停用"，文件仍保留在磁盘；刷新页面、重启前后端后启停状态保留。
6. 删除需确认，确认后目录与记录一并清除。
