<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import PageHeader from '../components/PageHeader.vue'
import type { ToolExecuteResult, ToolItem } from '../api/tools'
import { useToolsStore } from '../stores/tools'
import { formatDateTime } from './model-form'
import {
  buildInitialState,
  buildRunParams,
  formatResult,
  isLongText,
  RESULT_LABELS,
  TYPE_LABELS,
  validateRunState,
  type ToolRunErrors,
  type ToolRunState,
} from './tool-run'

const store = useToolsStore()
const route = useRoute()
const router = useRouter()

const toolId = computed(() => Number(route.params.id))

const loading = ref(true)
const notFound = ref(false)
const tool = ref<ToolItem | null>(null)
const toggling = ref(false)
const toggleError = ref('')

const runState = reactive<ToolRunState>({ text: {}, bool: {} })
const runErrors = ref<ToolRunErrors>({})
const running = ref(false)
const runResult = ref<ToolExecuteResult | null>(null)

onMounted(async () => {
  try {
    const loaded = await store.loadTool(toolId.value)
    tool.value = loaded
    Object.assign(runState, buildInitialState(loaded.parameters))
  } catch {
    notFound.value = true
  } finally {
    loading.value = false
  }
})

function goBack(): void {
  void router.push('/tools')
}

async function toggle(): Promise<void> {
  if (tool.value === null || toggling.value) return
  toggling.value = true
  toggleError.value = ''
  try {
    tool.value = await store.setEnabled(tool.value.id, !tool.value.enabled)
  } catch (error) {
    toggleError.value = error instanceof Error ? error.message : '操作失败，请稍后重试'
  } finally {
    toggling.value = false
  }
}

async function run(): Promise<void> {
  if (tool.value === null || running.value) return
  runErrors.value = validateRunState(tool.value.parameters, runState)
  if (Object.keys(runErrors.value).length > 0) return

  running.value = true
  try {
    runResult.value = await store.execute(tool.value.id, buildRunParams(tool.value.parameters, runState))
    // 后端返回的参数级错误合并到对应输入框下展示
    const fields = runResult.value.error?.fields
    if (fields) runErrors.value = { ...fields }
  } catch (error) {
    runResult.value = {
      ok: false,
      error: {
        type: 'failed',
        message: error instanceof Error ? error.message : '请求失败，请确认后端服务已启动',
      },
    }
  } finally {
    running.value = false
  }
}

const resultRows = computed(() => (runResult.value?.result ? formatResult(runResult.value.result) : []))
</script>

<template>
  <PageHeader v-if="tool" :title="tool.name" :description="`稳定标识：${tool.key}`" />
  <PageHeader v-else title="工具详情" />

  <p class="back-link"><RouterLink to="/tools">← 返回工具列表</RouterLink></p>

  <section v-if="loading" class="card state-card">
    <p class="state-text">正在加载…</p>
  </section>

  <section v-else-if="notFound" class="card state-card">
    <p class="state-text">没有找到这个工具，可能已被删除。</p>
    <button class="btn-primary" type="button" @click="goBack">返回列表</button>
  </section>

  <template v-else-if="tool">
    <p v-if="toggleError" class="toggle-error">{{ toggleError }}</p>

    <section class="card section-card head-card">
      <div class="head-main">
        <div class="head-badges">
          <span v-if="tool.isBuiltin" class="badge badge-builtin">系统内置</span>
          <span class="badge" :class="tool.enabled ? 'badge-on' : 'badge-off'">
            {{ tool.enabled ? '启用中' : '已停用' }}
          </span>
        </div>
        <p class="head-hint">
          更新时间：{{ formatDateTime(tool.updatedAt) }}。已停用的工具不会被执行，也不会出现在 Agent 的可用工具中。
        </p>
      </div>
      <button class="btn-ghost" type="button" :disabled="toggling" @click="toggle">
        {{ toggling ? '正在保存…' : tool.enabled ? '停用工具' : '启用工具' }}
      </button>
    </section>

    <section class="card section-card">
      <h2 class="section-title">用途说明</h2>
      <p class="tool-description">{{ tool.description }}</p>
    </section>

    <section class="card section-card">
      <h2 class="section-title">参数说明</h2>
      <table v-if="tool.parameters.length > 0" class="params-table">
        <thead>
          <tr>
            <th>参数名</th>
            <th>类型</th>
            <th>必填</th>
            <th>说明</th>
            <th>示例</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="param in tool.parameters" :key="param.name">
            <td class="mono">{{ param.name }}</td>
            <td>{{ TYPE_LABELS[param.type] ?? param.type }}</td>
            <td>{{ param.required ? '是' : '否' }}</td>
            <td class="param-desc-cell">{{ param.description }}</td>
            <td class="mono example-cell">{{ param.example || '—' }}</td>
          </tr>
        </tbody>
      </table>
      <p v-else class="no-params">该工具没有参数。</p>
    </section>

    <section class="card section-card">
      <h2 class="section-title">试运行</h2>
      <p class="run-intro">填写参数后调用统一执行入口，验证工具可用；被拦截的危险操作会在这里看到原因。</p>
      <form class="run-form" novalidate @submit.prevent="run">
        <div v-for="param in tool.parameters" :key="param.name" class="form-field">
          <div class="param-head">
            <span class="param-name mono">{{ param.name }}</span>
            <span class="param-type">{{ TYPE_LABELS[param.type] ?? param.type }}</span>
            <span v-if="param.required" class="req">必填</span>
          </div>
          <p class="param-desc">{{ param.description }}</p>
          <textarea
            v-if="isLongText(param)"
            v-model="runState.text[param.name]"
            class="param-input long"
            rows="4"
            spellcheck="false"
          ></textarea>
          <input
            v-else-if="param.type !== 'boolean'"
            v-model="runState.text[param.name]"
            class="param-input"
            type="text"
            :placeholder="param.example ? `示例：${param.example}` : ''"
          />
          <label v-else class="checkbox-row">
            <input v-model="runState.bool[param.name]" type="checkbox" />
            <span>勾选表示 true，不勾选时不传该参数</span>
          </label>
          <p v-if="runErrors[param.name]" class="field-error">{{ runErrors[param.name] }}</p>
        </div>

        <button class="btn-primary" type="submit" :disabled="running">
          {{ running ? '正在执行…' : '运行' }}
        </button>
      </form>

      <div v-if="runResult" class="run-result" :class="runResult.ok ? 'is-ok' : 'is-fail'">
        <template v-if="runResult.ok">
          <p class="result-head">执行成功</p>
          <div class="result-rows">
            <div v-for="row in resultRows" :key="row.label" class="result-row">
              <span class="result-label mono">{{ row.label }}</span>
              <span class="result-value mono">{{ row.value }}</span>
            </div>
          </div>
        </template>
        <template v-else>
          <p class="result-head">
            {{ RESULT_LABELS[runResult.error?.type ?? 'failed'] ?? '执行失败' }}
          </p>
          <p class="result-message">{{ runResult.error?.message }}</p>
          <p v-if="runResult.error?.hint" class="result-hint">{{ runResult.error.hint }}</p>
        </template>
      </div>
    </section>
  </template>
</template>

<style scoped>
.back-link {
  margin-bottom: 10px;
  font-size: 12px;
}

.back-link a {
  color: var(--text-secondary);
  text-decoration: none;
}

.back-link a:hover {
  color: var(--text-primary);
}

.state-card {
  padding: 32px 20px;
  text-align: center;
}

.state-text {
  margin-bottom: 12px;
  color: var(--text-secondary);
}

.toggle-error {
  margin-bottom: 10px;
  padding: 8px 10px;
  border: 1px solid var(--danger);
  border-radius: 8px;
  background: var(--accent-dim);
  font-size: 11.5px;
  color: var(--danger);
}

.section-card {
  padding: 14px 16px;
  margin-bottom: 12px;
}

.section-title {
  margin-bottom: 10px;
  font-size: 14px;
  font-weight: 600;
}

.head-card {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.head-badges {
  display: flex;
  gap: 6px;
}

.head-hint {
  margin-top: 8px;
  font-size: 11.5px;
  color: var(--text-tertiary);
}

.tool-description {
  white-space: pre-line;
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--text-secondary);
}

.params-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}

.params-table th {
  padding: 7px 10px;
  border-bottom: 1px solid var(--border);
  background: var(--bg-surface);
  color: var(--text-tertiary);
  font-size: 11px;
  font-weight: 600;
  text-align: left;
  white-space: nowrap;
}

.params-table td {
  padding: 8px 10px;
  border-bottom: 1px solid var(--border);
  vertical-align: top;
}

.params-table tbody tr:last-child td {
  border-bottom: none;
}

.param-desc-cell {
  color: var(--text-secondary);
}

.example-cell {
  font-size: 11px;
  color: var(--text-tertiary);
  word-break: break-all;
}

.no-params {
  font-size: 12px;
  color: var(--text-tertiary);
}

.run-intro {
  margin-bottom: 12px;
  font-size: 11.5px;
  color: var(--text-tertiary);
}

.run-form {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.form-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.param-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.param-name {
  font-size: 12.5px;
  font-weight: 600;
}

.param-type {
  padding: 0 6px;
  border: 1px solid var(--border);
  border-radius: 5px;
  background: var(--bg-hover);
  font-size: 10.5px;
  color: var(--text-secondary);
}

.req {
  padding: 0 6px;
  border-radius: 5px;
  background: var(--accent-dim);
  font-size: 10.5px;
  font-weight: 600;
  color: var(--accent);
}

.param-desc {
  font-size: 11.5px;
  color: var(--text-secondary);
}

.param-input {
  height: 30px;
  padding: 0 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-surface);
  font-size: 12px;
}

.param-input.long {
  height: auto;
  padding: 8px 10px;
  font-family: var(--font-mono);
  font-size: 11.5px;
  line-height: 1.5;
}

.param-input:focus {
  outline: none;
  border-color: var(--border-strong);
}

.checkbox-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--text-secondary);
}

.field-error {
  font-size: 11px;
  color: var(--danger);
}

.run-form .btn-primary {
  align-self: flex-start;
}

.run-form .btn-primary:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.run-result {
  margin-top: 14px;
  padding: 10px 12px;
  border: 1px solid;
  border-radius: 8px;
  font-size: 12px;
}

.run-result.is-ok {
  border-color: var(--success);
}

.run-result.is-fail {
  border-color: var(--danger);
}

.result-head {
  margin-bottom: 6px;
  font-weight: 650;
}

.run-result.is-ok .result-head {
  color: var(--success);
}

.run-result.is-fail .result-head {
  color: var(--danger);
}

.result-message {
  color: var(--text-primary);
}

.result-hint {
  margin-top: 4px;
  font-size: 11.5px;
  color: var(--text-tertiary);
}

.result-rows {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.result-row {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.result-label {
  font-size: 10.5px;
  font-weight: 600;
  color: var(--text-tertiary);
}

.result-value {
  max-height: 180px;
  overflow-y: auto;
  padding: 6px 8px;
  border-radius: 6px;
  background: var(--bg-hover);
  font-size: 11.5px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-all;
}

.mono {
  font-family: var(--font-mono);
}
</style>
