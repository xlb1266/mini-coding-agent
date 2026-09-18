<script setup lang="ts">
/** 运行记录列表：按开始时间倒序分页，支持按状态与 Agent 筛选。
 *
 * 每行只读摘要字段（快照与汇总指标），点击进入详情页看完整时间线。
 * Token 用量未知显示"未知"、部分请求未返回显示"统计不完整"，不显示 0。
 */

import { computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'

import PageHeader from '../components/PageHeader.vue'
import { useAgentsStore } from '../stores/agents'
import { useRunsStore, type StatusFilter } from '../stores/runs'
import type { RunItem, RunStatus } from '../api/runs'
import { formatDateTime } from './model-form'
import { RUN_STATUS_META, formatDuration, formatRunTokens } from './runs-format'

const router = useRouter()
const store = useRunsStore()
const agentsStore = useAgentsStore()

const STATUS_OPTIONS: { value: StatusFilter; label: string }[] = [
  { value: '', label: '全部状态' },
  { value: 'running', label: '运行中' },
  { value: 'completed', label: '成功' },
  { value: 'partial', label: '部分完成' },
  { value: 'cancelled', label: '已取消' },
  { value: 'failed', label: '失败' },
]

const statusFilterModel = computed({
  get: () => store.statusFilter,
  set: (value: StatusFilter) => void store.setFilters(value, store.agentFilter),
})

const agentFilterModel = computed({
  get: () => store.agentFilter,
  set: (value: number | null) => void store.setFilters(store.statusFilter, value),
})

const agentOptions = computed(() => agentsStore.agents)

onMounted(() => {
  void store.fetchRuns()
  if (!agentsStore.loaded) void agentsStore.fetchAgents()
})

function statusMeta(run: RunItem) {
  return RUN_STATUS_META[run.status as RunStatus] ?? RUN_STATUS_META.failed
}

function openDetail(run: RunItem): void {
  void router.push(`/runs/${run.id}`)
}

async function prevPage(): Promise<void> {
  await store.goToPage(store.page - 1)
}

async function nextPage(): Promise<void> {
  await store.goToPage(store.page + 1)
}
</script>

<template>
  <PageHeader title="运行记录" description="每次 Agent 运行的状态、耗时、模型与工具调用、Token 用量和完整时间线。" />

  <div class="toolbar">
    <div class="filters">
      <select v-model="statusFilterModel" aria-label="按状态筛选">
        <option v-for="option in STATUS_OPTIONS" :key="option.value" :value="option.value">
          {{ option.label }}
        </option>
      </select>
      <select v-model="agentFilterModel" aria-label="按 Agent 筛选">
        <option :value="null">全部 Agent</option>
        <option v-for="agent in agentOptions" :key="agent.id" :value="agent.id">
          {{ agent.name }}
        </option>
      </select>
    </div>
    <span class="toolbar-meta" :title="`共 ${store.total} 次运行`">共 {{ store.total }} 次运行</span>
  </div>

  <section class="card table-card">
    <table class="runs-table">
      <thead>
        <tr>
          <th>状态</th>
          <th>Agent</th>
          <th>模型</th>
          <th>开始时间</th>
          <th class="num">总耗时</th>
          <th class="num">模型调用</th>
          <th class="num">工具调用</th>
          <th>Token 用量</th>
          <th>错误摘要</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="run in store.items" :key="run.id" class="run-row" @click="openDetail(run)">
          <td>
            <span class="status-chip" :class="`tone-${statusMeta(run).tone}`">
              {{ statusMeta(run).icon }} {{ statusMeta(run).label }}
            </span>
          </td>
          <td>
            <div class="agent-cell">
              <span class="agent-name">{{ run.agentName }}</span>
              <span class="run-preview" :title="run.userInputPreview">{{ run.userInputPreview }}</span>
            </div>
          </td>
          <td>
            <span class="model-cell" :title="run.modelIdentifier">
              {{ run.modelName }}
              <span class="model-identifier">{{ run.modelIdentifier }}</span>
            </span>
          </td>
          <td class="time-cell">{{ formatDateTime(run.startedAt) }}</td>
          <td class="num">{{ formatDuration(run.durationMs) }}</td>
          <td class="num">{{ run.modelCalls }}</td>
          <td class="num">
            {{ run.toolCalls }}
            <span v-if="run.rejectedToolCalls > 0" class="rejected-note" title="权限或参数校验被拒绝、未执行的调用次数">
              +{{ run.rejectedToolCalls }} 拒绝
            </span>
          </td>
          <td class="token-cell">{{ formatRunTokens(run) }}</td>
          <td>
            <span v-if="run.errorSummary" class="error-cell" :title="run.errorSummary">{{ run.errorSummary }}</span>
            <span v-else class="no-error">—</span>
          </td>
        </tr>
      </tbody>
    </table>

    <p v-if="store.loading && !store.loaded" class="table-loading">正在加载…</p>
    <p v-else-if="store.error" class="table-error">{{ store.error }}</p>
    <div v-else-if="store.loaded && store.items.length === 0" class="empty-block">
      还没有运行记录；到聊天页发送一条消息即可产生第一次运行。
    </div>

    <div v-if="store.total > 0" class="pagination">
      <button class="btn-page" type="button" :disabled="store.page <= 1 || store.loading" @click="prevPage">
        上一页
      </button>
      <span class="page-info">第 {{ store.page }} / {{ store.totalPages }} 页</span>
      <button
        class="btn-page"
        type="button"
        :disabled="store.page >= store.totalPages || store.loading"
        @click="nextPage"
      >
        下一页
      </button>
    </div>
  </section>
</template>

<style scoped>
.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 10px;
}

.filters {
  display: flex;
  gap: 8px;
}

.filters select {
  height: 28px;
  padding: 0 8px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-surface);
  color: var(--text-primary);
  font-size: 12px;
}

.filters select:hover {
  border-color: var(--border-strong);
}

.toolbar-meta {
  font-size: 11.5px;
  color: var(--text-tertiary);
  white-space: nowrap;
}

.table-card {
  overflow: hidden;
}

.runs-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12.5px;
}

.runs-table th {
  padding: 9px 12px;
  border-bottom: 1px solid var(--border);
  background: var(--bg-surface);
  color: var(--text-tertiary);
  font-size: 11px;
  font-weight: 600;
  text-align: left;
  white-space: nowrap;
}

.runs-table th.num,
.runs-table td.num {
  text-align: right;
}

.runs-table td {
  padding: 9px 12px;
  border-bottom: 1px solid var(--border);
  color: var(--text-primary);
  vertical-align: middle;
}

.runs-table tbody tr:last-child td {
  border-bottom: none;
}

.run-row {
  cursor: pointer;
}

.runs-table tbody tr:hover td {
  background: var(--bg-hover);
}

.status-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  border-radius: 6px;
  font-size: 11px;
  white-space: nowrap;
}

.status-chip.tone-success {
  background: color-mix(in srgb, var(--success) 14%, transparent);
  color: var(--success);
}

.status-chip.tone-warning {
  background: color-mix(in srgb, var(--warning) 16%, transparent);
  color: var(--warning);
}

.status-chip.tone-danger {
  background: color-mix(in srgb, var(--danger) 14%, transparent);
  color: var(--danger);
}

.status-chip.tone-accent {
  background: color-mix(in srgb, var(--accent) 14%, transparent);
  color: var(--accent);
}

.status-chip.tone-muted {
  background: var(--bg-hover);
  color: var(--text-secondary);
}

.agent-cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 140px;
}

.agent-name {
  font-weight: 600;
}

.run-preview {
  overflow: hidden;
  max-width: 220px;
  color: var(--text-tertiary);
  font-size: 10.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.model-cell {
  display: inline-flex;
  align-items: baseline;
  gap: 5px;
  max-width: 180px;
}

.model-identifier {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--font-mono);
  font-size: 10.5px;
  color: var(--text-tertiary);
}

.time-cell {
  color: var(--text-secondary);
  font-size: 12px;
  white-space: nowrap;
}

.rejected-note {
  margin-left: 4px;
  color: var(--warning);
  font-size: 10.5px;
}

.token-cell {
  max-width: 190px;
  overflow: hidden;
  color: var(--text-secondary);
  font-size: 11.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.error-cell {
  display: inline-block;
  max-width: 200px;
  overflow: hidden;
  color: var(--danger);
  font-size: 11.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: bottom;
}

.no-error {
  color: var(--text-tertiary);
}

.table-loading,
.table-error,
.empty-block {
  padding: 18px 12px;
  color: var(--text-tertiary);
  font-size: 12px;
}

.table-error {
  color: var(--danger);
}

.empty-block {
  text-align: center;
}

.pagination {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
  padding: 10px 12px;
}

.page-info {
  color: var(--text-secondary);
  font-size: 11.5px;
}

.btn-page {
  height: 26px;
  padding: 0 10px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg-surface);
  color: var(--text-secondary);
  font-size: 11.5px;
  cursor: pointer;
}

.btn-page:hover:not(:disabled) {
  border-color: var(--border-strong);
  background: var(--bg-hover);
  color: var(--text-primary);
}

.btn-page:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
