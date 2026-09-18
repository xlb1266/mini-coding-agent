<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'

import AppDialog from './AppDialog.vue'
import { ApiError } from '../api/request'
import type { McpServerItem } from '../api/mcp'
import { useMcpStore } from '../stores/mcp'
import {
  buildMcpPayload,
  emptyFormValues,
  formValuesFromImport,
  formValuesFromServer,
  hasFormContent,
  isMaskedValue,
  parseMcpImport,
  validateMcpForm,
  type ImportedMcpServer,
  type McpFormErrors,
  type McpFormValues,
} from '../views/mcp-form'

const props = defineProps<{
  open: boolean
  /** null 表示新增，否则为编辑目标。 */
  editing: McpServerItem | null
}>()

const emit = defineEmits<{
  close: []
  saved: []
}>()

const store = useMcpStore()

const values = reactive<McpFormValues>(emptyFormValues())
const errors = ref<McpFormErrors>({})
const formError = ref('')
const saving = ref(false)

const isEdit = computed(() => props.editing !== null)

/** JSON 导入面板的状态：解析结果、多 Server 选择、覆盖确认。 */
const importOpen = ref(false)
const importText = ref('')
const importError = ref('')
const importNotice = ref('')
const importServers = ref<ImportedMcpServer[]>([])
const importChoice = ref(0)
const pendingImport = ref<ImportedMcpServer | null>(null)

function resetImportState(): void {
  importOpen.value = false
  importText.value = ''
  importError.value = ''
  importNotice.value = ''
  importServers.value = []
  importChoice.value = 0
  pendingImport.value = null
}

watch(
  () => props.open,
  (open) => {
    if (!open) return
    errors.value = {}
    formError.value = ''
    saving.value = false
    resetImportState()

    const editing = props.editing
    const source = editing ? formValuesFromServer(editing) : emptyFormValues()
    Object.assign(values, source)
  },
)

function toggleImport(): void {
  importOpen.value = !importOpen.value
  importError.value = ''
  pendingImport.value = null
}

/** 解析粘贴的 JSON，并填入表单；表单已有内容时先要求确认覆盖。 */
function runImport(): void {
  importError.value = ''
  importNotice.value = ''
  pendingImport.value = null

  const result = parseMcpImport(importText.value)
  if (!result.ok) {
    importServers.value = []
    importError.value = result.error
    return
  }
  importServers.value = result.servers
  if (importChoice.value >= result.servers.length) importChoice.value = 0
  const target = result.servers[importChoice.value]!
  if (!pendingImport.value && hasFormContent(values)) {
    pendingImport.value = target
    return
  }
  fillForm(target)
}

function confirmOverwrite(): void {
  const target = pendingImport.value
  if (target === null) return
  fillForm(target)
}

function fillForm(server: ImportedMcpServer): void {
  const filled = formValuesFromImport(server, {
    name: values.name,
    description: values.description,
  })
  values.name = filled.name
  values.description = filled.description
  values.command = filled.command
  values.args = filled.args
  values.envRows = filled.envRows
  errors.value = {}
  importError.value = ''
  pendingImport.value = null
  importServers.value = []
  importOpen.value = false
  importNotice.value = `已导入${server.name ? `「${server.name}」` : ''}的配置，确认无误后点「保存」生效。`
}

function addArg(): void {
  values.args.push('')
}

function removeArg(index: number): void {
  values.args.splice(index, 1)
  if (values.args.length === 0) values.args.push('')
}

function addEnvRow(): void {
  values.envRows.push({ key: '', value: '' })
}

function removeEnvRow(index: number): void {
  values.envRows.splice(index, 1)
  if (values.envRows.length === 0) values.envRows.push({ key: '', value: '' })
}

async function submit(): Promise<void> {
  errors.value = validateMcpForm(values)
  if (Object.keys(errors.value).length > 0) return

  saving.value = true
  formError.value = ''
  try {
    const payload = buildMcpPayload(values)
    if (props.editing) {
      await store.updateServer(props.editing.id, payload)
    } else {
      await store.createServer(payload)
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
</script>

<template>
  <AppDialog
    :open="open"
    :title="isEdit ? '编辑 MCP Server' : '新增 MCP Server'"
    :width="640"
    @close="emit('close')"
  >
    <form class="mcp-form" novalidate @submit.prevent="submit">
      <p class="form-intro">
        仅支持在本机启动、通过 stdio 通信的 MCP Server。以下配置通常可以在 MCP Server
        的使用说明（README / 安装文档）中直接找到，无需理解协议细节。带
        <span class="req">*</span> 为必填项。
      </p>

      <div class="import-box">
        <button class="import-toggle" type="button" @click="toggleImport">
          {{ importOpen ? '收起 JSON 导入' : '从 JSON 导入配置' }}
        </button>
        <div v-if="importOpen" class="import-panel">
          <p class="import-hint">
            粘贴 MCP Server 的 JSON 配置（Cursor、Claude Desktop 等常见的 mcpServers 写法即可），
            会自动填好名称、启动命令、参数与环境变量。
          </p>
          <textarea
            v-model="importText"
            class="import-text mono"
            rows="5"
            spellcheck="false"
            placeholder='{"mcpServers": {"amap-maps": {"command": "npx", "args": ["-y", "@amap/amap-maps-mcp-server"], "env": {"AMAP_MAPS_API_KEY": "你的key"}}}}'
          />
          <div v-if="importServers.length > 1" class="import-choice">
            <label>该配置包含 {{ importServers.length }} 个 Server，请选择要导入的一个：</label>
            <select v-model="importChoice">
              <option v-for="(server, index) in importServers" :key="`${server.name}-${index}`" :value="index">
                {{ server.name || `未命名 Server ${index + 1}` }}
              </option>
            </select>
          </div>
          <p v-if="importError" class="field-error">{{ importError }}</p>
          <div class="import-actions">
            <button v-if="pendingImport === null" class="import-btn" type="button" @click="runImport">
              {{ importServers.length > 1 ? '导入选中的 Server' : '导入并填入表单' }}
            </button>
            <template v-else>
              <span class="import-confirm-text">表单已有内容，导入会覆盖它。</span>
              <button class="import-btn" type="button" @click="confirmOverwrite">确认覆盖并导入</button>
            </template>
          </div>
        </div>
      </div>

      <p v-if="importNotice" class="import-notice">{{ importNotice }}</p>

      <div class="form-field">
        <label>名称 <span class="req">*</span></label>
        <input v-model="values.name" type="text" placeholder="例如：文件工具服务" />
        <p class="field-hint">用于在列表中识别该 MCP Server。</p>
        <p v-if="errors.name" class="field-error">{{ errors.name }}</p>
      </div>

      <div class="form-field">
        <label>说明</label>
        <input v-model="values.description" type="text" placeholder="描述该 Server 的用途" />
        <p v-if="errors.description" class="field-error">{{ errors.description }}</p>
      </div>

      <div class="form-field">
        <label>启动命令 <span class="req">*</span></label>
        <input
          v-model="values.command"
          type="text"
          class="mono"
          placeholder="例如：uvx、npx、node，或完整路径 C:\tools\my-mcp.exe"
        />
        <p class="field-hint">
          启动程序所需的可执行文件名称或路径；会直接作为进程启动，不会拼接成 Shell 命令执行。
          Windows 上 npm / uv 安装的 npx、uvx 等命令是 .cmd 包装脚本，本工具会自动经 cmd.exe 启动；
          也可以直接填写 .exe 的完整路径。
        </p>
        <p v-if="errors.command" class="field-error">{{ errors.command }}</p>
      </div>

      <div class="form-field">
        <label>启动参数</label>
        <div class="row-list">
          <div v-for="(_, index) in values.args" :key="`arg-${index}`" class="row-line">
            <input
              v-model="values.args[index]"
              type="text"
              class="mono"
              :placeholder="index === 0 ? '例如：-y' : '例如：mcp-server-fetch'"
            />
            <button class="row-remove" type="button" aria-label="删除参数" @click="removeArg(index)">×</button>
          </div>
        </div>
        <button class="row-add" type="button" @click="addArg">+ 添加参数</button>
        <p class="field-hint">每一项对应一个参数，按此顺序直接传给启动命令。</p>
      </div>

      <div class="form-field">
        <label>环境变量</label>
        <div class="row-list">
          <div v-for="(row, index) in values.envRows" :key="`env-${index}`" class="row-line env-line">
            <input v-model="row.key" type="text" class="mono" placeholder="变量名，例如：API_KEY" />
            <input
              v-model="row.value"
              type="text"
              class="mono"
              :placeholder="isMaskedValue(row.value) ? '已保存，修改将替换原值' : '变量值'"
            />
            <button class="row-remove" type="button" aria-label="删除环境变量" @click="removeEnvRow(index)">×</button>
          </div>
        </div>
        <button class="row-add" type="button" @click="addEnvRow">+ 添加环境变量</button>
        <p class="field-hint">
          可选。值可能包含密钥等敏感信息，保存后只显示掩码（••••••••），编辑时保留原值即可，不会把掩码当作实际值保存。
        </p>
        <p v-if="errors.env" class="field-error">{{ errors.env }}</p>
      </div>

      <p v-if="formError" class="form-error">{{ formError }}</p>
    </form>

    <template #actions>
      <button class="btn-ghost" type="button" :disabled="saving" @click="emit('close')">取消</button>
      <button class="btn-primary" type="button" :disabled="saving" @click="submit">
        {{ saving ? '保存中…' : '保存' }}
      </button>
    </template>
  </AppDialog>
</template>

<style scoped>
.form-intro {
  margin-bottom: 14px;
  font-size: 11.5px;
  color: var(--text-tertiary);
}

.mcp-form {
  display: flex;
  flex-direction: column;
  gap: 13px;
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

.form-field input {
  height: 30px;
  padding: 0 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-surface);
  font-size: 12.5px;
  color: var(--text-primary);
}

.form-field input.mono {
  font-family: var(--font-mono);
  font-size: 12px;
}

.form-field input:focus {
  outline: none;
  border-color: var(--accent);
}

.row-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.row-line {
  display: flex;
  align-items: center;
  gap: 6px;
}

.import-box {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px 12px;
  border: 1px dashed var(--border-strong);
  border-radius: 8px;
  background: var(--bg-base);
}

.import-toggle {
  align-self: flex-start;
  padding: 0;
  border: none;
  background: transparent;
  color: var(--accent);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
}

.import-toggle:hover {
  color: var(--accent-hover);
}

.import-panel {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.import-hint {
  font-size: 10.5px;
  color: var(--text-tertiary);
}

.import-text {
  width: 100%;
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-surface);
  font-family: var(--font-mono);
  font-size: 11.5px;
  line-height: 1.5;
  color: var(--text-primary);
  resize: vertical;
}

.import-text:focus {
  outline: none;
  border-color: var(--accent);
}

.import-choice {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.import-choice label {
  font-size: 11.5px;
  color: var(--text-secondary);
}

.import-choice select {
  height: 28px;
  padding: 0 8px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-surface);
  font-size: 12px;
}

.import-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.import-actions .import-btn {
  height: 26px;
  padding: 0 11px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg-surface);
  color: var(--text-secondary);
  font-size: 11.5px;
  cursor: pointer;
}

.import-actions .import-btn:hover {
  border-color: var(--border-strong);
  background: var(--bg-hover);
  color: var(--text-primary);
}

.import-confirm-text {
  font-size: 11.5px;
  color: var(--warning);
}

.import-notice {
  padding: 7px 10px;
  border: 1px solid var(--success);
  border-radius: 8px;
  font-size: 11.5px;
  color: var(--success);
}

.row-line input {
  flex: 1;
  min-width: 0;
}

.env-line input:first-child {
  flex: 0 0 38%;
}

.row-remove {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  flex-shrink: 0;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--text-tertiary);
  font-size: 13px;
  cursor: pointer;
}

.row-remove:hover {
  background: var(--bg-hover);
  color: var(--danger);
}

.row-add {
  align-self: flex-start;
  margin-top: 2px;
  padding: 0;
  border: none;
  background: transparent;
  color: var(--accent);
  font-size: 11.5px;
  cursor: pointer;
}

.row-add:hover {
  color: var(--accent-hover);
}

.field-hint {
  font-size: 10.5px;
  color: var(--text-tertiary);
}

.field-error {
  font-size: 10.5px;
  color: var(--danger);
}

.form-error {
  padding: 8px 10px;
  border: 1px solid var(--danger);
  border-radius: 8px;
  background: var(--accent-dim);
  font-size: 11.5px;
  color: var(--danger);
}
</style>
