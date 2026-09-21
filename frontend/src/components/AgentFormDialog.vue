<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import AppDialog from './AppDialog.vue'
import { ApiError } from '../api/request'
import type { AgentBindingRef, AgentDetail, AgentItem } from '../api/agents'
import { useAgentsStore } from '../stores/agents'
import { useMcpStore } from '../stores/mcp'
import { useModelsStore } from '../stores/models'
import { useSkillsStore } from '../stores/skills'
import { useToolsStore } from '../stores/tools'
import {
  buildAgentPayload,
  emptyFormValues,
  findMissingBindings,
  COMPACT_KEEP_ROUNDS_MAX,
  COMPACT_KEEP_ROUNDS_MIN,
  COMPACT_SUMMARY_TOKENS_MAX,
  COMPACT_SUMMARY_TOKENS_MIN,
  COMPACT_THRESHOLD_MAX,
  COMPACT_THRESHOLD_MIN,
  MAX_ROUNDS_MAX,
  MAX_ROUNDS_MIN,
  validateAgentForm,
  type AgentFormErrors,
  type AgentFormValues,
} from '../views/agent-form'

const props = defineProps<{
  open: boolean
  /** null 表示新建，否则为编辑目标。 */
  editing: AgentItem | null
}>()

const emit = defineEmits<{
  close: []
  saved: []
}>()

const router = useRouter()
const agentsStore = useAgentsStore()
const modelsStore = useModelsStore()
const toolsStore = useToolsStore()
const skillsStore = useSkillsStore()
const mcpStore = useMcpStore()

const values = reactive<AgentFormValues>(emptyFormValues())
const errors = ref<AgentFormErrors>({})
const formError = ref('')
const saving = ref(false)
const loading = ref(false)
/** 编辑时加载到的完整配置；绑定可用状态以它为准。 */
const loadedDetail = ref<AgentDetail | null>(null)

const isEdit = computed(() => props.editing !== null)
const hasModels = computed(() => modelsStore.models.length > 0)

const toolOptionKeys = computed(() => new Set(toolsStore.tools.map((tool) => tool.key)))
const skillOptionKeys = computed(() => new Set(skillsStore.skills.map((skill) => skill.dirName)))
const mcpOptionKeys = computed(() => new Set(mcpStore.servers.map((server) => String(server.id))))

/** 资源已不存在的绑定（如 Skill 目录被手动删除后扫描清理）：保留展示，允许移除。 */
const missingToolBindings = computed(() =>
  loadedDetail.value === null ? [] : findMissingBindings(loadedDetail.value.toolBindings, toolOptionKeys.value),
)
const missingSkillBindings = computed(() =>
  loadedDetail.value === null ? [] : findMissingBindings(loadedDetail.value.skillBindings, skillOptionKeys.value),
)
const missingMcpBindings = computed(() =>
  loadedDetail.value === null ? [] : findMissingBindings(loadedDetail.value.mcpBindings, mcpOptionKeys.value),
)

function isBoundTool(key: string): boolean {
  return values.toolKeys.includes(key)
}

function isBoundSkill(dirName: string): boolean {
  return values.skillDirNames.includes(dirName)
}

function isBoundMcp(id: string): boolean {
  return values.mcpServerIds.includes(id)
}

watch(
  () => props.open,
  (open) => {
    if (!open) return
    errors.value = {}
    formError.value = ''
    saving.value = false
    loading.value = false
    loadedDetail.value = null
    Object.assign(values, emptyFormValues())

    void loadOptions()
    const editing = props.editing
    if (editing) {
      void fillFromDetail(editing.id)
    } else {
      // 新建时默认选中当前默认模型
      const defaultModel = modelsStore.defaultModel
      if (defaultModel) values.modelId = String(defaultModel.id)
    }
  },
)

async function loadOptions(): Promise<void> {
  loading.value = true
  try {
    await Promise.all([
      modelsStore.models.length === 0 ? modelsStore.fetchModels() : Promise.resolve(),
      toolsStore.fetchTools(),
      skillsStore.fetchSkills(),
      mcpStore.fetchServers(),
    ])
  } finally {
    loading.value = false
  }
}

async function fillFromDetail(id: number): Promise<void> {
  loading.value = true
  try {
    const detail = await agentsStore.loadAgent(id)
    loadedDetail.value = detail
    values.name = detail.name
    values.description = detail.description
    values.systemPrompt = detail.systemPrompt
    values.modelId = detail.model === null ? '' : String(detail.model.id)
    values.maxRounds = String(detail.maxRounds)
    values.workspaceDir = detail.workspaceDir
    values.requireApproval = detail.requireApproval
    values.autoCompact = detail.autoCompact
    values.compactThreshold = String(detail.compactThreshold)
    values.compactKeepRounds = String(detail.compactKeepRounds)
    values.compactSummaryTokens = String(detail.compactSummaryTokens)
    values.toolKeys = detail.toolBindings.map((binding) => binding.refKey)
    values.skillDirNames = detail.skillBindings.map((binding) => binding.refKey)
    values.mcpServerIds = detail.mcpBindings.map((binding) => binding.refKey)
  } catch (error) {
    formError.value = error instanceof Error ? error.message : '加载 Agent 配置失败，请重试'
  } finally {
    loading.value = false
  }
}

function goModels(): void {
  emit('close')
  void router.push('/models')
}

async function submit(): Promise<void> {
  errors.value = validateAgentForm(values)
  if (Object.keys(errors.value).length > 0) return

  saving.value = true
  formError.value = ''
  try {
    const payload = buildAgentPayload(values)
    if (props.editing) {
      await agentsStore.updateAgent(props.editing.id, payload)
    } else {
      await agentsStore.createAgent(payload)
    }
    emit('saved')
    emit('close')
  } catch (error) {
    if (error instanceof ApiError && error.fields) {
      errors.value = { ...error.fields }
    } else {
      formError.value = error instanceof Error ? error.message : '保存失败，请重试'
    }
  } finally {
    saving.value = false
  }
}

function bindingBadge(binding: AgentBindingRef): string {
  return binding.name === null ? '已失效，不可用' : '已停用，不可用'
}
</script>

<template>
  <AppDialog :open="open" :title="isEdit ? '编辑 Agent' : '新建 Agent'" :width="680" @close="emit('close')">
    <div v-if="loading" class="form-loading">正在加载…</div>

    <form v-else class="agent-form" novalidate @submit.prevent="submit">
      <p class="form-intro">
        Agent 把模型、系统提示词与能力组合成面向具体任务的配置。带 <span class="req">*</span> 为必填项。
      </p>

      <div v-if="!hasModels" class="no-model-box">
        <p class="no-model-text">还没有可用模型，请先添加模型，再创建或编辑 Agent。</p>
        <button class="btn-link" type="button" @click="goModels">前往模型管理</button>
      </div>

      <div class="form-field">
        <label>名称 <span class="req">*</span></label>
        <input v-model="values.name" type="text" placeholder="例如：写作助手" />
        <p class="field-hint">用于在页面中识别这个 Agent。</p>
        <p v-if="errors.name" class="field-error">{{ errors.name }}</p>
      </div>

      <div class="form-field">
        <label>说明</label>
        <input v-model="values.description" type="text" placeholder="描述这个 Agent 的用途和适用任务" />
        <p v-if="errors.description" class="field-error">{{ errors.description }}</p>
      </div>

      <div class="form-field">
        <label>
          系统提示词
          <span v-if="editing" class="version-tag">当前版本 v{{ editing.promptVersion }}</span>
        </label>
        <textarea
          v-model="values.systemPrompt"
          rows="8"
          placeholder="定义 Agent 的角色、任务要求和行为限制…"
        ></textarea>
        <p class="field-hint">保存后内容变化会生成新版本（版本号从 1 开始）；修改其他配置不影响版本号。</p>
        <p v-if="errors.systemPrompt" class="field-error">{{ errors.systemPrompt }}</p>
      </div>

      <div class="form-grid">
        <div class="form-field">
          <label>模型 <span class="req">*</span></label>
          <select v-model="values.modelId">
            <option value="" disabled>请选择模型…</option>
            <option v-for="model in modelsStore.models" :key="model.id" :value="String(model.id)">
              {{ model.displayName }}（{{ model.modelIdentifier }}）
            </option>
          </select>
          <p class="field-hint">Agent 运行时使用的语言模型。</p>
          <p v-if="errors.modelId" class="field-error">{{ errors.modelId }}</p>
        </div>
        <div class="form-field">
          <label>最大执行轮数 <span class="req">*</span></label>
          <input v-model="values.maxRounds" type="text" inputmode="numeric" placeholder="25" />
          <p class="field-hint">限制 Agent Loop 的执行轮数，范围 {{ MAX_ROUNDS_MIN }}–{{ MAX_ROUNDS_MAX }}，默认 25。</p>
          <p v-if="errors.maxRounds" class="field-error">{{ errors.maxRounds }}</p>
        </div>
      </div>

      <div class="form-field">
        <label>工作区目录</label>
        <input
          v-model="values.workspaceDir"
          type="text"
          placeholder="例如 D:\projects\demo（留空使用默认目录）"
        />
        <p class="field-hint">
          Agent 运行时文件工具（读写 / 编辑 / 列目录 / 搜索）的授权范围，以及 Shell 命令的执行目录。
          必须是本机已存在的绝对路径目录，不会被自动创建；留空时使用系统默认工作区。
        </p>
        <p v-if="errors.workspaceDir" class="field-error">{{ errors.workspaceDir }}</p>
      </div>

      <div class="form-field compact-section">
        <label class="compact-toggle">
          <input v-model="values.autoCompact" type="checkbox" />
          自动压缩上下文
        </label>
        <p class="field-hint">
          长会话接近模型容量时，把较早对话自动整理成摘要继续使用；聊天记录本身不会被删除。
          关闭后不生成摘要，输入超过容量时会直接提示缩短内容或重新开启。
        </p>
        <div class="form-grid compact-grid" :class="{ 'is-disabled': !values.autoCompact }">
          <div class="form-field">
            <label>触发比例（%）</label>
            <input
              v-model="values.compactThreshold"
              type="text"
              inputmode="numeric"
              placeholder="80"
              :disabled="!values.autoCompact"
            />
            <p class="field-hint">
              估算输入达到可用输入容量（上下文长度 − 输出预留 − 安全余量）的该比例时触发，
              范围 {{ COMPACT_THRESHOLD_MIN }}–{{ COMPACT_THRESHOLD_MAX }}。
            </p>
            <p v-if="errors.compactThreshold" class="field-error">{{ errors.compactThreshold }}</p>
          </div>
          <div class="form-field">
            <label>保留最近轮数</label>
            <input
              v-model="values.compactKeepRounds"
              type="text"
              inputmode="numeric"
              placeholder="4"
              :disabled="!values.autoCompact"
            />
            <p class="field-hint">
              压缩时完整保留最近几轮对话（一轮 = 一条用户消息及其回复），
              范围 {{ COMPACT_KEEP_ROUNDS_MIN }}–{{ COMPACT_KEEP_ROUNDS_MAX }}。
            </p>
            <p v-if="errors.compactKeepRounds" class="field-error">{{ errors.compactKeepRounds }}</p>
          </div>
          <div class="form-field">
            <label>摘要目标长度（Token，估算）</label>
            <input
              v-model="values.compactSummaryTokens"
              type="text"
              inputmode="numeric"
              placeholder="1000"
              :disabled="!values.autoCompact"
            />
            <p class="field-hint">
              生成摘要的目标长度，约为 {{ COMPACT_SUMMARY_TOKENS_MIN }}–{{ COMPACT_SUMMARY_TOKENS_MAX }}（不保证精确等长）；
              小窗口模型下会自动按可用容量收窄，不会占满上下文。
            </p>
            <p v-if="errors.compactSummaryTokens" class="field-error">{{ errors.compactSummaryTokens }}</p>
          </div>
        </div>
      </div>

      <div class="form-field">
        <label>工具 <span class="chosen">已选 {{ values.toolKeys.length }} 个</span></label>
        <p class="field-hint">勾选 Agent 可以使用的内置工具；只有启用的工具可以新增。</p>
        <div v-if="toolsStore.tools.length > 0 || missingToolBindings.length > 0" class="option-list">
          <label v-for="tool in toolsStore.tools" :key="tool.key" class="option-row">
            <input
              v-model="values.toolKeys"
              type="checkbox"
              :value="tool.key"
              :disabled="!tool.enabled && !isBoundTool(tool.key)"
            />
            <span class="option-name">{{ tool.name }}</span>
            <span class="option-desc" :title="tool.description">{{ tool.description }}</span>
            <span v-if="!tool.enabled" class="option-badge">
              {{ isBoundTool(tool.key) ? '已停用，不可用' : '已停用' }}
            </span>
          </label>
          <label v-for="binding in missingToolBindings" :key="`missing-${binding.refKey}`" class="option-row">
            <input v-model="values.toolKeys" type="checkbox" :value="binding.refKey" />
            <span class="option-name">未知工具</span>
            <span class="option-desc">{{ binding.refKey }}</span>
            <span class="option-badge">{{ bindingBadge(binding) }}</span>
          </label>
        </div>
        <p v-else class="options-empty">暂无可选择的工具。</p>
      </div>

      <div class="form-field">
        <label>Skills <span class="chosen">已选 {{ values.skillDirNames.length }} 个</span></label>
        <p class="field-hint">勾选 Agent 处理任务时可以参考的做事说明；只有启用的 Skill 可以新增。</p>
        <div v-if="skillsStore.skills.length > 0 || missingSkillBindings.length > 0" class="option-list">
          <label v-for="skill in skillsStore.skills" :key="skill.dirName" class="option-row">
            <input
              v-model="values.skillDirNames"
              type="checkbox"
              :value="skill.dirName"
              :disabled="!skill.enabled && !isBoundSkill(skill.dirName)"
            />
            <span class="option-name">{{ skill.name }}</span>
            <span class="option-desc" :title="skill.description">{{ skill.description }}</span>
            <span v-if="!skill.enabled" class="option-badge">
              {{ isBoundSkill(skill.dirName) ? '已停用，不可用' : '已停用' }}
            </span>
          </label>
          <label v-for="binding in missingSkillBindings" :key="`missing-${binding.refKey}`" class="option-row">
            <input v-model="values.skillDirNames" type="checkbox" :value="binding.refKey" />
            <span class="option-name">未知 Skill</span>
            <span class="option-desc">{{ binding.refKey }}</span>
            <span class="option-badge">{{ bindingBadge(binding) }}</span>
          </label>
        </div>
        <p v-else class="options-empty">还没有可用的 Skill，可先在 Skills 管理中创建或导入。</p>
      </div>

      <div class="form-field">
        <label>MCP Server <span class="chosen">已选 {{ values.mcpServerIds.length }} 个</span></label>
        <p class="field-hint">勾选为 Agent 提供外部工具的 MCP Server；只有启用的 Server 可以新增。</p>
        <div v-if="mcpStore.servers.length > 0 || missingMcpBindings.length > 0" class="option-list">
          <label v-for="server in mcpStore.servers" :key="server.id" class="option-row">
            <input
              v-model="values.mcpServerIds"
              type="checkbox"
              :value="String(server.id)"
              :disabled="!server.enabled && !isBoundMcp(String(server.id))"
            />
            <span class="option-name">{{ server.name }}</span>
            <span class="option-desc" :title="server.description || server.command">
              {{ server.description || server.command }}
            </span>
            <span v-if="!server.enabled" class="option-badge">
              {{ isBoundMcp(String(server.id)) ? '已停用，不可用' : '已停用' }}
            </span>
          </label>
          <label v-for="binding in missingMcpBindings" :key="`missing-${binding.refKey}`" class="option-row">
            <input v-model="values.mcpServerIds" type="checkbox" :value="binding.refKey" />
            <span class="option-name">未知 Server</span>
            <span class="option-desc">{{ binding.refKey }}</span>
            <span class="option-badge">{{ bindingBadge(binding) }}</span>
          </label>
        </div>
        <p v-else class="options-empty">还没有可用的 MCP Server，可先在 MCP 管理中添加。</p>
      </div>

      <p v-if="formError" class="form-error">{{ formError }}</p>
    </form>

    <template #actions>
      <button class="btn-ghost" type="button" :disabled="saving" @click="emit('close')">取消</button>
      <button class="btn-primary" type="button" :disabled="saving || loading" @click="submit">
        {{ saving ? '保存中…' : '保存' }}
      </button>
    </template>
  </AppDialog>
</template>

<style scoped>
.form-loading {
  padding: 24px 0;
  text-align: center;
  color: var(--text-tertiary);
  font-size: 12px;
}

.form-intro {
  margin-bottom: 14px;
  font-size: 11.5px;
  color: var(--text-tertiary);
}

.agent-form {
  display: flex;
  flex-direction: column;
  gap: 13px;
}

.no-model-box {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 9px 12px;
  border: 1px solid var(--warning);
  border-radius: 8px;
  background: var(--accent-dim);
}

.no-model-text {
  font-size: 11.5px;
  color: var(--text-primary);
}

.btn-link {
  border: none;
  background: transparent;
  color: var(--accent);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  white-space: nowrap;
}

.btn-link:hover {
  color: var(--accent-hover);
  text-decoration: underline;
}

.form-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.form-field label {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-primary);
}

.req {
  color: var(--danger);
}

.chosen {
  margin-left: 6px;
  font-weight: 500;
  color: var(--text-tertiary);
}

.version-tag {
  margin-left: 6px;
  padding: 1px 6px;
  border-radius: 5px;
  background: var(--accent-dim);
  color: var(--accent);
  font-size: 10.5px;
  font-weight: 600;
}

.form-field input[type='text'] {
  height: 30px;
  padding: 0 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-surface);
  font-size: 12.5px;
  color: var(--text-primary);
}

.form-field textarea {
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-surface);
  font-family: var(--font-body);
  font-size: 12.5px;
  line-height: 1.55;
  color: var(--text-primary);
  resize: vertical;
}

.form-field select {
  height: 30px;
  padding: 0 8px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-surface);
  font-size: 12.5px;
  color: var(--text-primary);
}

.form-field input:focus,
.form-field textarea:focus,
.form-field select:focus {
  outline: none;
  border-color: var(--accent);
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

/* 上下文压缩配置区块：三列放下触发比例 / 保留轮数 / 摘要目标长度 */
.compact-grid {
  grid-template-columns: repeat(3, minmax(0, 1fr));
  margin-top: 8px;
}

.compact-grid.is-disabled {
  opacity: 0.55;
}

.compact-section {
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-surface);
}

.compact-toggle {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  cursor: pointer;
}

.compact-toggle input[type='checkbox'] {
  width: 14px;
  height: 14px;
  accent-color: var(--accent);
}

.field-hint {
  font-size: 10.5px;
  color: var(--text-tertiary);
}

.field-error {
  font-size: 10.5px;
  color: var(--danger);
}

.option-list {
  display: flex;
  flex-direction: column;
  max-height: 170px;
  overflow-y: auto;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-surface);
}

.option-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 10px;
  cursor: pointer;
}

.option-row + .option-row {
  border-top: 1px solid var(--border);
}

.option-row:hover {
  background: var(--bg-hover);
}

.option-row input[type='checkbox'] {
  flex: none;
  width: 13px;
  height: 13px;
  accent-color: var(--accent);
}

.option-row input[type='checkbox']:disabled {
  cursor: not-allowed;
}

.option-name {
  flex: none;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-primary);
}

.option-desc {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 11px;
  color: var(--text-tertiary);
}

.option-badge {
  flex: none;
  padding: 1px 6px;
  border-radius: 5px;
  background: var(--bg-active);
  color: var(--warning);
  font-size: 10.5px;
  font-weight: 600;
  white-space: nowrap;
}

.options-empty {
  padding: 10px 12px;
  border: 1px dashed var(--border);
  border-radius: 8px;
  font-size: 11.5px;
  color: var(--text-tertiary);
}

.form-error {
  padding: 8px 10px;
  border: 1px solid var(--danger);
  border-radius: 8px;
  background: var(--accent-dim);
  font-size: 11.5px;
  color: var(--danger);
}

.btn-ghost {
  display: inline-flex;
  align-items: center;
  height: 30px;
  padding: 0 14px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-surface);
  color: var(--text-secondary);
  font-size: 12px;
  cursor: pointer;
}

.btn-ghost:hover:not(:disabled) {
  background: var(--bg-hover);
  color: var(--text-primary);
}

.btn-ghost:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
</style>
