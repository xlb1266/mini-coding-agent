<script setup lang="ts">
/** Agent 评测页：数据集管理（用例逐条维护）与评测任务（创建 / 启动 / 进度 / 结果）。
 *
 * 任务在 后端进程内 顺序执行：页面轮询刷新运行中任务的进度与结果，
 * 刷新或关闭页面不影响执行。结果行可展开查看材料快照、实际回答、
 * 评分与错误原因，并可跳转到关联 Run 的运行详情。
 */

import { computed, onMounted, onUnmounted, ref, watch, type Ref } from 'vue'
import { useRouter } from 'vue-router'

import AppDialog from '../components/AppDialog.vue'
import PageHeader from '../components/PageHeader.vue'
import {
  compareTasks as apiCompareTasks,
  createCase as apiCreateCase,
  updateCase as apiUpdateCase,
  type EvalCaseItem,
  type EvalTaskDetail,
  type TaskCompare,
} from '../api/evaluation'
import { ApiError } from '../api/request'
import { useAgentsStore } from '../stores/agents'
import { useEvaluationStore } from '../stores/evaluation'
import { useModelsStore } from '../stores/models'
import { formatDateTime } from './model-form'
import {
  caseStatusMeta,
  formatAverage,
  formatScore,
  formatScoringDuration,
  progressText,
  taskStatusMeta,
  validateCaseForm,
} from './evaluation-format'

const router = useRouter()
const store = useEvaluationStore()
const agentsStore = useAgentsStore()
const modelsStore = useModelsStore()

type Tab = 'datasets' | 'tasks'
const tab = ref<Tab>('datasets')

// ---- 轮询：存在运行中的任务时每 2 秒刷新进度 ----
let pollTimer: number | null = null

function stopPolling(): void {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer)
    pollTimer = null
  }
}

function syncPolling(): void {
  if (store.hasRunningTask && pollTimer === null) {
    pollTimer = window.setInterval(() => {
      void store.pollRunning()
    }, 2000)
  } else if (!store.hasRunningTask && pollTimer !== null) {
    stopPolling()
  }
}

watch(() => store.hasRunningTask, syncPolling)
onUnmounted(stopPolling)

onMounted(() => {
  void store.fetchDatasets()
  void store.fetchTasks()
  if (!agentsStore.loaded) void agentsStore.fetchAgents()
  if (!modelsStore.loaded) void modelsStore.fetchModels()
})

// ===========================================================================
// 数据集
// ===========================================================================

const selectedDatasetId = ref<number | null>(null)
const selectedDataset = computed(() =>
  selectedDatasetId.value === null ? null : (store.datasetDetails[selectedDatasetId.value] ?? null),
)

watch(selectedDatasetId, (id) => {
  if (id !== null && !store.datasetDetails[id]) void store.fetchDataset(id)
})

async function openDataset(id: number): Promise<void> {
  selectedDatasetId.value = id
  await store.fetchDataset(id, true)
}

// ---- 数据集表单（新建 / 编辑） ----

const datasetFormOpen = ref(false)
const datasetEditing = ref<number | null>(null)
const datasetForm = ref({ name: '', description: '' })
const datasetFormErrors = ref<Record<string, string>>({})
const datasetSaving = ref(false)

function openDatasetCreate(): void {
  datasetEditing.value = null
  datasetForm.value = { name: '', description: '' }
  datasetFormErrors.value = {}
  datasetFormOpen.value = true
}

function openDatasetEdit(id: number): void {
  const dataset = store.datasets.find((item) => item.id === id)
  if (!dataset) return
  datasetEditing.value = id
  datasetForm.value = { name: dataset.name, description: dataset.description }
  datasetFormErrors.value = {}
  datasetFormOpen.value = true
}

async function submitDataset(): Promise<void> {
  datasetFormErrors.value = {}
  if (!datasetForm.value.name.trim()) {
    datasetFormErrors.value = { name: '请填写名称' }
    return
  }
  datasetSaving.value = true
  try {
    if (datasetEditing.value === null) {
      const created = await store.createDataset(datasetForm.value)
      datasetFormOpen.value = false
      await openDataset(created.id)
    } else {
      await store.updateDataset(datasetEditing.value, datasetForm.value)
      datasetFormOpen.value = false
    }
  } catch (error) {
    applyFormError(error, datasetFormErrors)
  } finally {
    datasetSaving.value = false
  }
}

// ---- 删除数据集（需确认） ----

const datasetDeleteOpen = ref(false)
const datasetDeleteTarget = ref<number | null>(null)
const datasetDeleteError = ref('')
const datasetDeleting = ref(false)

function openDatasetDelete(id: number): void {
  datasetDeleteTarget.value = id
  datasetDeleteError.value = ''
  datasetDeleteOpen.value = true
}

async function confirmDatasetDelete(): Promise<void> {
  if (datasetDeleteTarget.value === null) return
  datasetDeleting.value = true
  datasetDeleteError.value = ''
  try {
    await store.removeDataset(datasetDeleteTarget.value)
    if (selectedDatasetId.value === datasetDeleteTarget.value) selectedDatasetId.value = null
    datasetDeleteOpen.value = false
  } catch (error) {
    datasetDeleteError.value = error instanceof Error ? error.message : '删除失败'
  } finally {
    datasetDeleting.value = false
  }
}

// ---- 用例表单（新增 / 编辑） ----

const caseFormOpen = ref(false)
const caseEditing = ref<number | null>(null)
const caseForm = ref({ question: '', expectedAnswer: '', scoringRequirements: '' })
const caseFormErrors = ref<Record<string, string>>({})
const caseSaving = ref(false)

function openCaseCreate(): void {
  if (selectedDatasetId.value === null) return
  caseEditing.value = null
  caseForm.value = { question: '', expectedAnswer: '', scoringRequirements: '' }
  caseFormErrors.value = {}
  caseFormOpen.value = true
}

function openCaseEdit(row: EvalCaseItem): void {
  caseEditing.value = row.id
  caseForm.value = {
    question: row.question,
    expectedAnswer: row.expectedAnswer,
    scoringRequirements: row.scoringRequirements,
  }
  caseFormErrors.value = {}
  caseFormOpen.value = true
}

async function submitCase(): Promise<void> {
  const errors = validateCaseForm(caseForm.value)
  caseFormErrors.value = errors
  if (Object.keys(errors).length > 0 || selectedDatasetId.value === null) return
  caseSaving.value = true
  try {
    if (caseEditing.value === null) {
      await apiCreateCase(selectedDatasetId.value, caseForm.value)
    } else {
      await apiUpdateCase(selectedDatasetId.value, caseEditing.value, caseForm.value)
    }
    caseFormOpen.value = false
    await store.fetchDatasets()
    await store.fetchDataset(selectedDatasetId.value, true)
  } catch (error) {
    applyFormError(error, caseFormErrors)
  } finally {
    caseSaving.value = false
  }
}

// ---- 删除用例（需确认） ----

const caseDeleteOpen = ref(false)
const caseDeleteTarget = ref<EvalCaseItem | null>(null)
const caseDeleting = ref(false)

function openCaseDelete(row: EvalCaseItem): void {
  caseDeleteTarget.value = row
  caseDeleteOpen.value = true
}

async function confirmCaseDelete(): Promise<void> {
  if (caseDeleteTarget.value === null || selectedDatasetId.value === null) return
  caseDeleting.value = true
  try {
    await store.removeCase(selectedDatasetId.value, caseDeleteTarget.value.id)
    caseDeleteOpen.value = false
  } finally {
    caseDeleting.value = false
  }
}

function applyFormError(error: unknown, target: Ref<Record<string, string>>): void {
  if (error instanceof ApiError && error.fields) {
    target.value = { ...error.fields }
  } else {
    target.value = { form: error instanceof Error ? error.message : '保存失败' }
  }
}

// ===========================================================================
// 评测任务
// ===========================================================================

const selectedTaskId = ref<number | null>(null)
const selectedTask = computed<EvalTaskDetail | null>(() =>
  selectedTaskId.value === null ? null : (store.taskDetails[selectedTaskId.value] ?? null),
)

watch(selectedTaskId, (id) => {
  if (id !== null && !store.taskDetails[id]) void store.fetchTask(id)
  resetCompare(id)
})

// ---- 回归对比（阶段十五）：同一数据集的两个任务按用例对照得分 ----

const compareBaseId = ref<number | null>(null)
const compareTargetId = ref<number | null>(null)
const compareLoading = ref(false)
const compareError = ref('')
const compareResult = ref<TaskCompare | null>(null)

/** 可参与对比的任务：与当前任务同一数据集且已跑完。 */
const comparableTasks = computed(() => {
  const current = selectedTask.value
  if (!current) return []
  return store.tasks.filter((task) => task.status === 'completed' && task.datasetId === current.datasetId)
})

function resetCompare(taskId: number | null): void {
  compareBaseId.value = taskId
  compareTargetId.value = null
  compareResult.value = null
  compareError.value = ''
}

function formatDelta(delta: number | null): string {
  if (delta === null) return '—'
  return delta > 0 ? `+${delta}` : String(delta)
}

/** 对比表里单用例得分的差值配色：涨为绿、跌为红、持平中性。 */
function deltaTone(caseRow: { baseScore: number | null; targetScore: number | null }): string {
  if (caseRow.baseScore === null || caseRow.targetScore === null) return ''
  const delta = caseRow.targetScore - caseRow.baseScore
  if (delta > 0) return 'tone-up'
  if (delta < 0) return 'tone-down'
  return ''
}

async function runCompare(): Promise<void> {
  if (compareBaseId.value === null || compareTargetId.value === null) return
  compareLoading.value = true
  compareError.value = ''
  try {
    compareResult.value = await apiCompareTasks(compareBaseId.value, compareTargetId.value)
  } catch (error) {
    compareError.value = error instanceof Error ? error.message : '对比失败，请重试'
    compareResult.value = null
  } finally {
    compareLoading.value = false
  }
}

async function openTask(id: number): Promise<void> {
  selectedTaskId.value = id
  await store.fetchTask(id, true)
}

async function startTaskNow(id: number): Promise<void> {
  try {
    await store.startTask(id)
    await store.fetchTask(id, true)
    syncPolling()
  } catch {
    // 错误已写入 store.taskActionError，由页面展示
  }
}

const expandedResults = ref<Set<number>>(new Set())

function toggleResult(id: number): void {
  const next = new Set(expandedResults.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  expandedResults.value = next
}

// ---- 新建任务 ----

const taskFormOpen = ref(false)
const taskSaving = ref(false)
const taskFormErrors = ref<Record<string, string>>({})
const taskForm = ref({
  agentId: null as number | null,
  datasetId: null as number | null,
  scorerModelId: null as number | null,
  passScore: 60,
  allowTools: false,
})

const taskFormDatasets = computed(() => store.datasets.filter((item) => item.caseCount > 0))

function openTaskCreate(): void {
  taskFormErrors.value = {}
  taskForm.value = {
    agentId: null,
    datasetId: null,
    scorerModelId: null,
    passScore: 60,
    allowTools: false,
  }
  taskFormOpen.value = true
}

function validateTaskForm(): boolean {
  const errors: Record<string, string> = {}
  if (taskForm.value.agentId === null) errors.agentId = '请选择待评测的 Agent'
  if (taskForm.value.datasetId === null) errors.datasetId = '请选择评测数据集'
  if (taskForm.value.scorerModelId === null) errors.scorerModelId = '请选择评分模型'
  const pass = taskForm.value.passScore
  if (!Number.isInteger(pass) || pass < 0 || pass > 100) {
    errors.passScore = '通过分数必须为 0–100 的整数'
  }
  taskFormErrors.value = errors
  return Object.keys(errors).length === 0
}

async function submitTask(andStart: boolean): Promise<void> {
  if (!validateTaskForm() || taskForm.value.agentId === null || taskForm.value.datasetId === null) {
    return
  }
  taskSaving.value = true
  try {
    const created = await store.createTask({
      agentId: taskForm.value.agentId,
      datasetId: taskForm.value.datasetId,
      scorerModelId: taskForm.value.scorerModelId ?? 0,
      passScore: taskForm.value.passScore,
      allowTools: taskForm.value.allowTools,
    })
    taskFormOpen.value = false
    if (andStart) {
      await store.startTask(created.id)
      syncPolling()
    }
    await openTask(created.id)
    tab.value = 'tasks'
  } catch {
    // 字段级错误已写入 store；表单保持打开
    if (store.taskActionError) taskFormErrors.value = { form: store.taskActionError }
  } finally {
    taskSaving.value = false
  }
}
</script>

<template>
  <PageHeader title="Agent 评测" description="构建评测数据集，运行评测任务并查看逐用例的评分结果。" />

  <div class="tabs" role="tablist">
    <button
      type="button"
      class="tab"
      :class="{ active: tab === 'datasets' }"
      role="tab"
      :aria-selected="tab === 'datasets'"
      @click="tab = 'datasets'"
    >
      数据集
    </button>
    <button
      type="button"
      class="tab"
      :class="{ active: tab === 'tasks' }"
      role="tab"
      :aria-selected="tab === 'tasks'"
      @click="tab = 'tasks'"
    >
      评测任务
    </button>
  </div>

  <!-- ==================== 数据集 ==================== -->
  <section v-if="tab === 'datasets'" class="eval-layout">
    <div class="card list-card">
      <div class="list-head">
        <h2 class="card-title">数据集</h2>
        <button class="btn-primary" type="button" @click="openDatasetCreate">新建数据集</button>
      </div>
      <p v-if="store.datasetsError" class="list-error">{{ store.datasetsError }}</p>
      <p v-else-if="store.datasetsLoading && !store.datasetsLoaded" class="list-loading">正在加载…</p>
      <div v-else-if="store.datasets.length === 0" class="empty-block">
        还没有数据集；点击「新建数据集」创建，或使用内置示例数据集。
      </div>
      <ul v-else class="dataset-items">
        <li
          v-for="dataset in store.datasets"
          :key="dataset.id"
          class="dataset-item"
          :class="{ selected: dataset.id === selectedDatasetId }"
          @click="openDataset(dataset.id)"
        >
          <div class="dataset-item-main">
            <span class="dataset-name">
              {{ dataset.name }}
              <span v-if="dataset.isExample" class="badge-example">示例</span>
            </span>
            <span class="dataset-desc" :title="dataset.description">{{ dataset.description || '—' }}</span>
          </div>
          <div class="dataset-item-side">
            <span class="dataset-count">{{ dataset.caseCount }} 条用例</span>
            <span class="dataset-time">{{ formatDateTime(dataset.updatedAt) }}</span>
          </div>
          <div class="row-actions" @click.stop>
            <button class="btn-mini" type="button" @click="openDatasetEdit(dataset.id)">编辑</button>
            <button class="btn-mini danger" type="button" @click="openDatasetDelete(dataset.id)">删除</button>
          </div>
        </li>
      </ul>
    </div>

    <div class="card detail-card">
      <template v-if="selectedDataset">
        <div class="list-head">
          <h2 class="card-title">
            {{ selectedDataset.name }}
            <span class="detail-count">{{ selectedDataset.cases.length }} 条用例</span>
          </h2>
          <button class="btn-primary" type="button" @click="openCaseCreate">新增用例</button>
        </div>
        <table v-if="selectedDataset.cases.length > 0" class="cases-table">
          <thead>
            <tr>
              <th class="num">#</th>
              <th>用户问题</th>
              <th>期望答案</th>
              <th>评分要求</th>
              <th class="actions">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, index) in selectedDataset.cases" :key="row.id">
              <td class="num">{{ index + 1 }}</td>
              <td class="text-cell">{{ row.question }}</td>
              <td class="text-cell muted">{{ row.expectedAnswer || '—' }}</td>
              <td class="text-cell muted">{{ row.scoringRequirements || '—' }}</td>
              <td class="actions">
                <button class="btn-mini" type="button" @click="openCaseEdit(row)">编辑</button>
                <button class="btn-mini danger" type="button" @click="openCaseDelete(row)">删除</button>
              </td>
            </tr>
          </tbody>
        </table>
        <div v-else class="empty-block">该数据集还没有用例；空数据集不能启动评测。</div>
      </template>
      <div v-else class="empty-block tall">
        在左侧选择一个数据集，或新建数据集开始添加测试用例。<br />
        每条用例包含用户问题（必填）、期望答案与评分要求（至少填写一项）。
      </div>
    </div>
  </section>

  <!-- ==================== 评测任务 ==================== -->
  <section v-else class="eval-layout">
    <div class="card list-card">
      <div class="list-head">
        <h2 class="card-title">评测任务</h2>
        <button
          class="btn-primary"
          type="button"
          :disabled="taskFormDatasets.length === 0 || agentsStore.agents.length === 0"
          @click="openTaskCreate"
        >
          新建任务
        </button>
      </div>
      <p v-if="store.tasksError" class="list-error">{{ store.tasksError }}</p>
      <p v-else-if="store.tasksLoading && !store.tasksLoaded" class="list-loading">正在加载…</p>
      <div v-else-if="store.tasks.length === 0" class="empty-block">
        还没有评测任务；先在数据集页准备好用例，再点击「新建任务」。
      </div>
      <ul v-else class="task-items">
        <li
          v-for="task in store.tasks"
          :key="task.id"
          class="task-item"
          :class="{ selected: task.id === selectedTaskId }"
          @click="openTask(task.id)"
        >
          <div class="task-item-top">
            <span class="status-chip" :class="`tone-${taskStatusMeta(task.status).tone}`">
              {{ taskStatusMeta(task.status).icon }} {{ taskStatusMeta(task.status).label }}
            </span>
            <span class="task-title">{{ task.agentName }} · {{ task.datasetName }}</span>
          </div>
          <div class="task-item-meta">
            <span :title="`通过分数 ${task.passScore} 分`">通过 ≥ {{ task.passScore }} 分</span>
            <span>{{ task.allowTools ? '允许工具' : '工具关闭' }}</span>
            <span :title="`已完成 ${task.stats.completedCases} / ${task.stats.totalCases}`">
              进度 {{ progressText(task.stats.completedCases, task.stats.totalCases) }}
            </span>
            <span>平均 {{ formatAverage(task.stats.averageScore) }}</span>
          </div>
          <div class="task-item-time">{{ formatDateTime(task.createdAt) }}</div>
        </li>
      </ul>
    </div>

    <div class="card detail-card">
      <template v-if="selectedTask">
        <div class="list-head">
          <h2 class="card-title">任务 #{{ selectedTask.id }}</h2>
          <button
            v-if="selectedTask.status === 'pending'"
            class="btn-primary"
            type="button"
            @click="startTaskNow(selectedTask.id)"
          >
            启动评测
          </button>
        </div>
        <p v-if="store.taskActionError" class="list-error">{{ store.taskActionError }}</p>

        <div class="stats-row">
          <span class="status-chip" :class="`tone-${taskStatusMeta(selectedTask.status).tone}`">
            {{ taskStatusMeta(selectedTask.status).icon }} {{ taskStatusMeta(selectedTask.status).label }}
          </span>
          <span class="stat-chip">总用例 {{ selectedTask.stats.totalCases }}</span>
          <span class="stat-chip">已完成 {{ selectedTask.stats.completedCases }}</span>
          <span class="stat-chip tone-pass">通过 {{ selectedTask.stats.passedCount }}</span>
          <span class="stat-chip tone-fail">未通过 {{ selectedTask.stats.failedCount }}</span>
          <span class="stat-chip tone-fail">执行失败 {{ selectedTask.stats.execFailedCount }}</span>
          <span class="stat-chip tone-fail">评分失败 {{ selectedTask.stats.scoringFailedCount }}</span>
          <span class="stat-chip" :title="'平均分只统计获得合法评分的用例'">
            平均分 {{ formatAverage(selectedTask.stats.averageScore) }}
          </span>
          <span class="stat-chip" :title="'过程评分均分（LLM Judge 对执行过程合理性的打分）'">
            过程均分 {{ formatAverage(selectedTask.stats.averageProcessScore) }}
          </span>
        </div>

        <p class="task-config">
          Agent「{{ selectedTask.agentName }}」 · 数据集「{{ selectedTask.datasetName }}」 ·
          通过分数 ≥ {{ selectedTask.passScore }} 分 ·
          {{ selectedTask.allowTools ? '允许使用 Agent 已绑定且启用的工具' : '不提供工具（工具关闭）' }} ·
          评分模型 {{ selectedTask.scorerModelName }}
          <span class="mono">{{ selectedTask.scorerModelIdentifier }}</span>
          <template v-if="typeof selectedTask.agentConfig.promptVersion === 'number'">
            · 提示词版本 v{{ selectedTask.agentConfig.promptVersion }}
          </template>
        </p>

        <!-- 回归对比（阶段十五）：同一数据集的两个任务按用例对照 -->
        <details class="compare-panel">
          <summary>回归对比（V1 → V2）</summary>
          <div class="compare-form">
            <select v-model="compareBaseId" class="compare-select" aria-label="基准任务">
              <option v-for="task in comparableTasks" :key="task.id" :value="task.id">
                V1 · 任务#{{ task.id }} {{ task.agentName }}（{{ formatDateTime(task.createdAt) }}）
              </option>
            </select>
            <span class="compare-arrow">→</span>
            <select v-model="compareTargetId" class="compare-select" aria-label="对比任务">
              <option :value="null" disabled>选择对比任务</option>
              <option v-for="task in comparableTasks" :key="task.id" :value="task.id">
                V2 · 任务#{{ task.id }} {{ task.agentName }}（{{ formatDateTime(task.createdAt) }}）
              </option>
            </select>
            <button
              class="btn-primary"
              type="button"
              :disabled="compareLoading || compareBaseId === null || compareTargetId === null || compareBaseId === compareTargetId"
              @click="runCompare"
            >
              {{ compareLoading ? '对比中…' : '开始对比' }}
            </button>
          </div>
          <p v-if="compareError" class="list-error">{{ compareError }}</p>
          <template v-if="compareResult">
            <div class="compare-summary">
              <span class="stat-chip">
                平均分 {{ formatAverage(compareResult.baseStats.averageScore) }} →
                {{ formatAverage(compareResult.targetStats.averageScore) }}
                <b class="compare-delta">{{ formatDelta(compareResult.averageScoreDelta) }}</b>
              </span>
              <span class="stat-chip">
                过程均分 {{ formatAverage(compareResult.baseStats.averageProcessScore) }} →
                {{ formatAverage(compareResult.targetStats.averageProcessScore) }}
                <b class="compare-delta">{{ formatDelta(compareResult.averageProcessScoreDelta) }}</b>
              </span>
              <span class="stat-chip">
                通过 {{ compareResult.baseStats.passedCount }}/{{ compareResult.baseStats.totalCases }} →
                {{ compareResult.targetStats.passedCount }}/{{ compareResult.targetStats.totalCases }}
                <b class="compare-delta">{{ formatDelta(compareResult.passRateDelta) }}%</b>
              </span>
            </div>
            <div class="compare-table-wrap">
              <table class="compare-table">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>用例</th>
                    <th class="is-num">V1 得分</th>
                    <th class="is-num">V2 得分</th>
                    <th class="is-num">Δ 得分</th>
                    <th class="is-num">V1 过程</th>
                    <th class="is-num">V2 过程</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="caseRow in compareResult.cases" :key="caseRow.caseOrder">
                    <td>{{ caseRow.caseOrder }}</td>
                    <td class="compare-question" :title="caseRow.question">{{ caseRow.question }}</td>
                    <td class="is-num">{{ caseRow.baseScore === null ? '—' : formatScore(caseRow.baseScore) }}</td>
                    <td class="is-num">{{ caseRow.targetScore === null ? '—' : formatScore(caseRow.targetScore) }}</td>
                    <td class="is-num" :class="deltaTone(caseRow)">
                      {{ caseRow.baseScore === null || caseRow.targetScore === null ? '—' : formatDelta(caseRow.targetScore - caseRow.baseScore) }}
                    </td>
                    <td class="is-num">{{ caseRow.baseProcessScore === null ? '—' : formatScore(caseRow.baseProcessScore) }}</td>
                    <td class="is-num">{{ caseRow.targetProcessScore === null ? '—' : formatScore(caseRow.targetProcessScore) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </template>
        </details>

        <ul class="result-items">
          <li v-for="result in selectedTask.results" :key="result.id" class="result-item">
            <button class="result-head" type="button" @click="toggleResult(result.id)">
              <span class="result-order">#{{ result.caseOrder }}</span>
              <span class="result-question" :title="result.question">{{ result.question }}</span>
              <span
                v-if="result.score !== null"
                class="result-score"
                :class="result.status === 'passed' ? 'pass' : 'fail'"
              >
                {{ formatScore(result.score) }} 分
              </span>
              <span
                class="status-chip"
                :class="`tone-${caseStatusMeta(result.status).tone}`"
              >
                {{ caseStatusMeta(result.status).icon }} {{ caseStatusMeta(result.status).label }}
              </span>
              <span class="result-toggle">{{ expandedResults.has(result.id) ? '收起' : '展开' }}</span>
            </button>
            <div v-if="expandedResults.has(result.id)" class="result-body">
              <div class="result-grid">
                <div class="result-field">
                  <span class="result-label">用户问题</span>
                  <p class="result-text">{{ result.question }}</p>
                </div>
                <div class="result-field">
                  <span class="result-label">期望答案</span>
                  <p class="result-text">{{ result.expectedAnswer || '（未提供）' }}</p>
                </div>
                <div class="result-field">
                  <span class="result-label">评分要求</span>
                  <p class="result-text">{{ result.scoringRequirements || '（未提供）' }}</p>
                </div>
                <div class="result-field">
                  <span class="result-label">实际回答</span>
                  <p class="result-text answer">{{ result.actualAnswer || '（无输出）' }}</p>
                </div>
                <div v-if="result.score !== null" class="result-field">
                  <span class="result-label">得分</span>
                  <p class="result-text">
                    {{ formatScore(result.score) }} 分
                    <span v-if="selectedTask" class="result-hint">
                      （通过分数 {{ selectedTask.passScore }} 分，{{ result.status === 'passed' ? '已达到' : '未达到' }}）
                    </span>
                  </p>
                </div>
                <div v-if="result.reason" class="result-field">
                  <span class="result-label">评分理由</span>
                  <p class="result-text">{{ result.reason }}</p>
                </div>
                <div v-if="result.processScore !== null" class="result-field">
                  <span class="result-label">过程评分</span>
                  <p class="result-text">
                    {{ formatScore(result.processScore) }} 分
                    <span v-if="result.processReason" class="result-hint">（{{ result.processReason }}）</span>
                  </p>
                </div>
                <div v-if="result.execError" class="result-field">
                  <span class="result-label">执行失败原因</span>
                  <p class="result-text error">{{ result.execError }}</p>
                </div>
                <div v-if="result.scoreError" class="result-field">
                  <span class="result-label">评分失败原因</span>
                  <p class="result-text error">{{ result.scoreError }}</p>
                </div>
                <div v-if="result.scoringDurationMs !== null" class="result-field">
                  <span class="result-label">评分开销</span>
                  <p class="result-text">
                    耗时 {{ formatScoringDuration(result.scoringDurationMs) }}
                    <span v-if="result.scoringTotalTokens !== null"> · {{ result.scoringTotalTokens }} token</span>
                    <span v-if="result.scoringAttempts > 1"> · 尝试 {{ result.scoringAttempts }} 次</span>
                  </p>
                </div>
                <div class="result-field">
                  <span class="result-label">关联 Run</span>
                  <p class="result-text">
                    <button
                      v-if="result.runId !== null"
                      class="link-btn"
                      type="button"
                      @click.stop="router.push(`/runs/${result.runId}`)"
                    >
                      查看运行详情
                    </button>
                    <span v-else class="result-hint">—</span>
                    <span v-if="result.execReason" class="result-hint">（结束原因：{{ result.execReason }}）</span>
                  </p>
                </div>
              </div>
            </div>
          </li>
        </ul>
      </template>
      <div v-else class="empty-block tall">
        在左侧选择一个任务查看统计与逐用例结果。<br />
        任务在后台顺序执行；刷新或关闭页面不会取消评测。
      </div>
    </div>
  </section>

  <!-- ==================== 对话框 ==================== -->

  <AppDialog
    :open="datasetFormOpen"
    :title="datasetEditing === null ? '新建数据集' : '编辑数据集'"
    :width="480"
    @close="datasetFormOpen = false"
  >
    <div class="dialog-form">
      <div class="form-field">
        <label>名称 <span class="req">*</span></label>
        <input v-model="datasetForm.name" maxlength="64" placeholder="例如：基础问答测试" />
        <p v-if="datasetFormErrors.name" class="field-error">{{ datasetFormErrors.name }}</p>
      </div>
      <div class="form-field">
        <label>说明</label>
        <textarea
          v-model="datasetForm.description"
          rows="2"
          maxlength="512"
          placeholder="数据集的用途说明（可选）"
        ></textarea>
        <p v-if="datasetFormErrors.description" class="field-error">{{ datasetFormErrors.description }}</p>
      </div>
      <p v-if="datasetFormErrors.form" class="field-error">{{ datasetFormErrors.form }}</p>
    </div>
    <template #actions>
      <button class="btn-ghost" type="button" :disabled="datasetSaving" @click="datasetFormOpen = false">
        取消
      </button>
      <button class="btn-primary" type="button" :disabled="datasetSaving" @click="submitDataset">
        {{ datasetSaving ? '正在保存…' : '保存' }}
      </button>
    </template>
  </AppDialog>

  <AppDialog
    :open="caseFormOpen"
    :title="caseEditing === null ? '新增用例' : '编辑用例'"
    :width="560"
    @close="caseFormOpen = false"
  >
    <div class="dialog-form">
      <div class="form-field">
        <label>用户问题 <span class="req">*</span></label>
        <textarea
          v-model="caseForm.question"
          rows="3"
          maxlength="8000"
          placeholder="发送给被评测 Agent 的问题"
        ></textarea>
        <p v-if="caseFormErrors.question" class="field-error">{{ caseFormErrors.question }}</p>
      </div>
      <div class="form-field">
        <label>期望答案</label>
        <textarea v-model="caseForm.expectedAnswer" rows="2" maxlength="8000" placeholder="参考答案（与评分要求至少填写一项）"></textarea>
      </div>
      <div class="form-field">
        <label>评分要求</label>
        <textarea
          v-model="caseForm.scoringRequirements"
          rows="2"
          maxlength="8000"
          placeholder="评分模型判断回答质量的标准（与期望答案至少填写一项）"
        ></textarea>
        <p v-if="caseFormErrors.expectedAnswer" class="field-error">{{ caseFormErrors.expectedAnswer }}</p>
      </div>
      <p v-if="caseFormErrors.form" class="field-error">{{ caseFormErrors.form }}</p>
    </div>
    <template #actions>
      <button class="btn-ghost" type="button" :disabled="caseSaving" @click="caseFormOpen = false">取消</button>
      <button class="btn-primary" type="button" :disabled="caseSaving" @click="submitCase">
        {{ caseSaving ? '正在保存…' : '保存' }}
      </button>
    </template>
  </AppDialog>

  <AppDialog :open="datasetDeleteOpen" title="删除数据集" :width="440" @close="datasetDeleteOpen = false">
    <p class="delete-text">确定要删除该数据集吗？数据集内的全部用例会一并删除，删除后无法恢复。</p>
    <p v-if="datasetDeleteError" class="delete-error">{{ datasetDeleteError }}</p>
    <template #actions>
      <button class="btn-ghost" type="button" :disabled="datasetDeleting" @click="datasetDeleteOpen = false">
        取消
      </button>
      <button class="btn-danger" type="button" :disabled="datasetDeleting" @click="confirmDatasetDelete">
        {{ datasetDeleting ? '正在删除…' : '确认删除' }}
      </button>
    </template>
  </AppDialog>

  <AppDialog :open="caseDeleteOpen" title="删除用例" :width="440" @close="caseDeleteOpen = false">
    <p class="delete-text">
      确定要删除用例「{{ caseDeleteTarget?.question.slice(0, 30) }}{{ (caseDeleteTarget?.question.length ?? 0) > 30 ? '…' : '' }}」吗？
    </p>
    <template #actions>
      <button class="btn-ghost" type="button" :disabled="caseDeleting" @click="caseDeleteOpen = false">取消</button>
      <button class="btn-danger" type="button" :disabled="caseDeleting" @click="confirmCaseDelete">
        {{ caseDeleting ? '正在删除…' : '确认删除' }}
      </button>
    </template>
  </AppDialog>

  <AppDialog :open="taskFormOpen" title="新建评测任务" :width="520" @close="taskFormOpen = false">
    <div class="dialog-form">
      <div class="form-field">
        <label>待评测 Agent <span class="req">*</span></label>
        <select v-model="taskForm.agentId">
          <option :value="null" disabled>请选择 Agent…</option>
          <option v-for="agent in agentsStore.agents" :key="agent.id" :value="agent.id">
            {{ agent.name }}
          </option>
        </select>
        <p v-if="taskFormErrors.agentId" class="field-error">{{ taskFormErrors.agentId }}</p>
      </div>
      <div class="form-field">
        <label>评测数据集 <span class="req">*</span></label>
        <select v-model="taskForm.datasetId">
          <option :value="null" disabled>请选择数据集…</option>
          <option v-for="dataset in taskFormDatasets" :key="dataset.id" :value="dataset.id">
            {{ dataset.name }}（{{ dataset.caseCount }} 条用例）
          </option>
        </select>
        <p v-if="taskFormErrors.datasetId" class="field-error">{{ taskFormErrors.datasetId }}</p>
        <p v-else-if="taskFormDatasets.length === 0" class="field-hint">
          没有包含用例的数据集；请先到数据集页添加用例。
        </p>
      </div>
      <div class="form-field">
        <label>评分模型 <span class="req">*</span></label>
        <select v-model="taskForm.scorerModelId">
          <option :value="null" disabled>请选择评分模型…</option>
          <option v-for="model in modelsStore.models" :key="model.id" :value="model.id">
            {{ model.displayName }}（{{ model.modelIdentifier }}）
          </option>
        </select>
        <p v-if="taskFormErrors.scorerModelId" class="field-error">{{ taskFormErrors.scorerModelId }}</p>
        <p v-else class="field-hint">评分模型只做打分，不执行 Agent 工具循环。</p>
      </div>
      <div class="form-row">
        <div class="form-field">
          <label>通过分数（0–100）</label>
          <input v-model.number="taskForm.passScore" type="number" min="0" max="100" step="1" />
          <p v-if="taskFormErrors.passScore" class="field-error">{{ taskFormErrors.passScore }}</p>
          <p v-else class="field-hint">得分大于或等于该值记为通过，默认 60 分。</p>
        </div>
        <label class="check-field">
          <input v-model="taskForm.allowTools" type="checkbox" />
          <span>允许工具</span>
        </label>
      </div>
      <p class="field-hint">
        允许工具关闭时，不向目标 Agent 提供工具定义，运行时也拒绝工具执行；Skills 仍按 Agent 配置加载。
      </p>
      <p v-if="taskFormErrors.form" class="field-error">{{ taskFormErrors.form }}</p>
    </div>
    <template #actions>
      <button class="btn-ghost" type="button" :disabled="taskSaving" @click="taskFormOpen = false">取消</button>
      <button class="btn-ghost" type="button" :disabled="taskSaving" @click="submitTask(false)">
        保存为待运行
      </button>
      <button class="btn-primary" type="button" :disabled="taskSaving" @click="submitTask(true)">
        {{ taskSaving ? '正在保存…' : '保存并启动' }}
      </button>
    </template>
  </AppDialog>
</template>

<style scoped>
/* ---- 回归对比（阶段十五） ---- */
.compare-panel {
  margin: 10px 0;
  padding: 8px 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-hover);
}

.compare-panel summary {
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  user-select: none;
}

.compare-form {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-top: 8px;
  flex-wrap: wrap;
}

.compare-select {
  height: 28px;
  max-width: 260px;
  padding: 0 8px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-surface);
  color: var(--text-primary);
  font-size: 11.5px;
}

.compare-select:focus {
  border-color: var(--accent);
  outline: none;
}

.compare-arrow {
  color: var(--text-tertiary);
  font-size: 12px;
}

.compare-summary {
  display: flex;
  gap: 8px;
  margin-top: 10px;
  flex-wrap: wrap;
}

.compare-delta {
  margin-left: 4px;
  color: var(--accent);
}

.compare-table-wrap {
  margin-top: 10px;
  overflow-x: auto;
}

.compare-table {
  width: 100%;
  border-collapse: collapse;
}

.compare-table th,
.compare-table td {
  padding: 5px 8px;
  border-bottom: 1px solid var(--border);
  color: var(--text-primary);
  font-size: 11.5px;
  text-align: left;
  white-space: nowrap;
}

.compare-table th {
  color: var(--text-tertiary);
  font-size: 10.5px;
  font-weight: 600;
}

.compare-table .is-num {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.compare-question {
  max-width: 280px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.compare-table td.tone-up {
  color: var(--success);
  font-weight: 600;
}

.compare-table td.tone-down {
  color: var(--danger);
  font-weight: 600;
}

.tabs {
  display: flex;
  gap: 6px;
  margin-bottom: 12px;
}

.tab {
  height: 28px;
  padding: 0 14px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-surface);
  color: var(--text-secondary);
  font-size: 12px;
  cursor: pointer;
}

.tab.active {
  border-color: var(--accent);
  background: var(--accent-dim);
  color: var(--accent);
  font-weight: 600;
}

.eval-layout {
  display: grid;
  grid-template-columns: 330px minmax(0, 1fr);
  gap: 12px;
  align-items: start;
}

.list-card,
.detail-card {
  padding: 14px 16px;
}

.list-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 10px;
}

.card-title {
  font-family: var(--font-display);
  font-size: 14.5px;
  font-weight: 650;
  color: var(--text-primary);
}

.detail-count,
.dataset-count {
  margin-left: 6px;
  font-size: 11px;
  font-weight: 500;
  color: var(--text-tertiary);
}

.list-error,
.list-loading {
  padding: 10px 0;
  font-size: 12px;
  color: var(--danger);
}

.list-loading {
  color: var(--text-tertiary);
}

.empty-block {
  padding: 26px 10px;
  font-size: 12px;
  line-height: 1.7;
  color: var(--text-tertiary);
  text-align: center;
}

.empty-block.tall {
  padding-top: 60px;
}

/* ---- 数据集列表 ---- */

.dataset-items,
.task-items,
.result-items {
  list-style: none;
  margin: 0;
  padding: 0;
}

.dataset-item {
  position: relative;
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 10px 8px;
  border-radius: 8px;
  cursor: pointer;
}

.dataset-item:hover {
  background: var(--bg-hover);
}

.dataset-item.selected {
  background: var(--bg-active);
}

.dataset-item-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.dataset-name {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--text-primary);
}

.badge-example {
  margin-left: 4px;
  padding: 1px 6px;
  border-radius: 5px;
  background: var(--accent-dim);
  color: var(--accent);
  font-size: 10.5px;
  font-weight: 600;
}

.dataset-desc {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 11px;
  color: var(--text-tertiary);
}

.dataset-item-side {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 3px;
  font-size: 10.5px;
  color: var(--text-tertiary);
  white-space: nowrap;
}

.row-actions {
  display: none;
  gap: 4px;
}

.dataset-item:hover .row-actions {
  display: flex;
  flex-direction: column;
}

/* ---- 用例表 ---- */

.cases-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12.5px;
}

.cases-table th {
  padding: 8px 10px;
  border-bottom: 1px solid var(--border);
  background: var(--bg-surface);
  color: var(--text-tertiary);
  font-size: 11px;
  font-weight: 600;
  text-align: left;
  white-space: nowrap;
}

.cases-table td {
  padding: 9px 10px;
  border-bottom: 1px solid var(--border);
  color: var(--text-primary);
  vertical-align: top;
}

.cases-table tbody tr:last-child td {
  border-bottom: none;
}

.cases-table tbody tr:hover td {
  background: var(--bg-hover);
}

.cases-table th.num,
.cases-table td.num {
  text-align: right;
  width: 34px;
  color: var(--text-tertiary);
}

.text-cell {
  max-width: 260px;
}

.text-cell.muted {
  color: var(--text-secondary);
}

.cases-table th.actions,
.cases-table td.actions {
  text-align: right;
  white-space: nowrap;
  width: 120px;
}

/* ---- 任务列表 ---- */

.task-item {
  display: flex;
  flex-direction: column;
  gap: 5px;
  padding: 10px 8px;
  border-radius: 8px;
  cursor: pointer;
}

.task-item:hover {
  background: var(--bg-hover);
}

.task-item.selected {
  background: var(--bg-active);
}

.task-item-top {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.task-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12.5px;
  font-weight: 600;
  color: var(--text-primary);
}

.task-item-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 10px;
  font-size: 11px;
  color: var(--text-secondary);
}

.task-item-time {
  font-size: 10.5px;
  color: var(--text-tertiary);
}

/* ---- 状态与统计 ---- */

.status-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  border-radius: 6px;
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
}

.tone-success {
  background: rgba(76, 140, 74, 0.12);
  color: var(--success);
}

.tone-warning {
  background: rgba(196, 143, 58, 0.14);
  color: var(--warning);
}

.tone-danger {
  background: rgba(178, 74, 58, 0.12);
  color: var(--danger);
}

.tone-accent {
  background: var(--accent-dim);
  color: var(--accent);
}

.tone-muted {
  background: var(--bg-hover);
  color: var(--text-tertiary);
}

.stats-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}

.stat-chip {
  padding: 3px 9px;
  border: 1px solid var(--border);
  border-radius: 7px;
  font-size: 11.5px;
  color: var(--text-secondary);
  white-space: nowrap;
}

.stat-chip.tone-pass {
  color: var(--success);
}

.stat-chip.tone-fail {
  color: var(--danger);
}

.task-config {
  margin: 0 0 12px;
  padding: 9px 12px;
  border-radius: 8px;
  background: var(--bg-hover);
  font-size: 11.5px;
  line-height: 1.6;
  color: var(--text-secondary);
}

.mono {
  font-family: var(--font-mono);
  font-size: 11px;
}

/* ---- 结果列表 ---- */

.result-item {
  border: 1px solid var(--border);
  border-radius: 8px;
  margin-bottom: 8px;
  overflow: hidden;
}

.result-head {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 9px 12px;
  border: none;
  background: transparent;
  cursor: pointer;
  text-align: left;
}

.result-head:hover {
  background: var(--bg-hover);
}

.result-order {
  font-family: var(--font-mono);
  font-size: 11.5px;
  color: var(--text-tertiary);
}

.result-question {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12.5px;
  color: var(--text-primary);
}

.result-score {
  font-size: 12.5px;
  font-weight: 650;
  white-space: nowrap;
}

.result-score.pass {
  color: var(--success);
}

.result-score.fail {
  color: var(--danger);
}

.result-toggle {
  font-size: 11px;
  color: var(--text-tertiary);
  white-space: nowrap;
}

.result-body {
  padding: 4px 12px 12px;
  border-top: 1px solid var(--border);
  background: var(--bg-base);
}

.result-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px 16px;
  padding-top: 10px;
}

.result-field {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}

.result-label {
  font-size: 10.5px;
  font-weight: 600;
  color: var(--text-tertiary);
}

.result-text {
  margin: 0;
  font-size: 12px;
  line-height: 1.55;
  color: var(--text-primary);
  white-space: pre-wrap;
  word-break: break-word;
}

.result-text.answer {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 6px 8px;
}

.result-text.error {
  color: var(--danger);
}

.result-hint {
  font-size: 10.5px;
  color: var(--text-tertiary);
}

.link-btn {
  border: none;
  background: transparent;
  padding: 0;
  font-size: 12px;
  color: var(--accent);
  cursor: pointer;
  text-decoration: underline;
}

/* ---- 小按钮与表单 ---- */

.btn-mini {
  height: 24px;
  padding: 0 9px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg-surface);
  color: var(--text-secondary);
  font-size: 11.5px;
  cursor: pointer;
}

.btn-mini + .btn-mini {
  margin-left: 6px;
}

.btn-mini:hover:not(:disabled) {
  border-color: var(--border-strong);
  background: var(--bg-hover);
  color: var(--text-primary);
}

.btn-mini.danger {
  color: var(--danger);
}

.btn-mini.danger:hover:not(:disabled) {
  border-color: var(--danger);
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

.btn-ghost:disabled,
.btn-primary:disabled,
.btn-danger:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.btn-danger {
  display: inline-flex;
  align-items: center;
  height: 30px;
  padding: 0 14px;
  border: none;
  border-radius: 8px;
  background: var(--danger);
  color: var(--text-inverse);
  font-size: 12px;
  cursor: pointer;
}

.btn-danger:hover:not(:disabled) {
  opacity: 0.9;
}

/* ---- 窄屏：单列布局 ---- */

@media (max-width: 899px) {
  .eval-layout {
    grid-template-columns: 1fr;
  }

  .row-actions {
    display: flex;
    flex-direction: column;
  }

  .result-grid {
    grid-template-columns: 1fr;
  }
}
</style>
