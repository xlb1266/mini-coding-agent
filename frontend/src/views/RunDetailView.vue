<script setup lang="ts">
/** 运行详情：运行摘要 + 按真实事件顺序排列的时间线。
 *
 * 时间线直接消费持久化的 Runtime 事件（与聊天页同一批事件、同一套
 * 名称 / 状态 / 摘要规则）：工具卡片与压缩卡片复用聊天页组件；模型
 * 步骤展示状态、耗时与单次用量；最终回答的完整片段与工具完整结果都
 * 是详细载荷，按需单独加载。
 */

import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import PageHeader from '../components/PageHeader.vue'
import ToolCallCard from '../components/ToolCallCard.vue'
import CompactionCard from '../components/CompactionCard.vue'
import type { RunDetail, RunItem } from '../api/runs'
import { useRunsStore } from '../stores/runs'
import { formatDateTime } from './model-form'
import {
  RUN_STATUS_META,
  buildTimeline,
  finishReasonLabel,
  formatDuration,
  formatFirstOutput,
  formatRunTokens,
  formatUsage,
  runEndStatusMeta,
  stepStatusMeta,
  type ModelStep,
  type RunTimelineStep,
  type ToolStep,
  type RunEndStep,
} from './runs-format'

const route = useRoute()
const router = useRouter()
const store = useRunsStore()

const runId = computed(() => Number(route.params.id))
const detail = ref<RunDetail | null>(null)
const notFound = ref(false)
const loading = ref(true)
/** 正在加载详细载荷的事件（`${runId}:${eventId}`） */
const loadingDetail = ref<Record<string, boolean>>({})

const run = computed<RunItem | null>(() => detail.value?.run ?? null)

/**
 * 时间线为响应式 ref（不是 computed）：详细载荷按需加载后直接写回
 * 步骤对象（tool.result / contentPreview），卡片原地更新；用 computed
 * 会在重算时重建步骤、丢失展开状态，且普通对象修改不触发渲染。
 */
const timeline = ref<RunTimelineStep[]>([])

const statusMeta = computed(() =>
  run.value ? RUN_STATUS_META[run.value.status] ?? RUN_STATUS_META.failed : null,
)

onMounted(async () => {
  try {
    detail.value = await store.fetchDetail(runId.value)
    timeline.value = buildTimeline(detail.value.events)
  } catch {
    notFound.value = true
  } finally {
    loading.value = false
  }
})

function goBack(): void {
  void router.push('/runs')
}

function modelTitle(step: ModelStep): string {
  return step.purpose === 'context_compaction'
    ? '模型请求（上下文压缩摘要）'
    : `模型请求${step.round ? ` · 第 ${step.round} 轮` : ''}`
}

function modelUsageText(step: ModelStep): string {
  const text = formatUsage(step.usage)
  return text || '用量未知'
}

/** 按需加载工具完整结果，加载后填入卡片（聊天页同一展示规则） */
async function loadToolResult(step: ToolStep): Promise<void> {
  if (!detail.value || step.tool.result) return
  const key = `${runId.value}:${step.eventId}`
  loadingDetail.value[key] = true
  try {
    const payload = await store.loadEventDetail(runId.value, step.eventId)
    if (payload.result) step.tool.result = payload.result
  } finally {
    loadingDetail.value[key] = false
  }
}

/** 按需加载最终回答的完整片段 */
async function loadRunContent(step: RunEndStep): Promise<void> {
  if (!detail.value) return
  const key = `${runId.value}:${step.eventId}`
  loadingDetail.value[key] = true
  try {
    const payload = await store.loadEventDetail(runId.value, step.eventId)
    step.contentPreview = payload.content ?? step.contentPreview
    step.truncated = Boolean(payload.truncated)
    step.hasDetail = false // 已加载，不再显示按钮
  } finally {
    loadingDetail.value[key] = false
  }
}

function isLoadingDetail(eventId: number): boolean {
  return Boolean(loadingDetail.value[`${runId.value}:${eventId}`])
}
</script>

<template>
  <PageHeader title="运行详情" description="一次 Agent 运行的完整时间线：模型请求、工具执行、上下文压缩与最终结果。" />

  <button class="btn-back" type="button" @click="goBack">← 返回运行列表</button>

  <p v-if="loading" class="state-text">正在加载…</p>
  <div v-else-if="notFound || !run" class="card empty-state">
    <p class="empty-title">运行记录不存在</p>
    <p class="empty-text">该记录可能已被删除，或地址有误。</p>
    <button class="btn-primary" type="button" @click="goBack">返回运行列表</button>
  </div>

  <template v-else>
    <!-- 运行摘要 -->
    <section class="card summary-card">
      <div class="summary-head">
        <span v-if="statusMeta" class="status-chip" :class="`tone-${statusMeta.tone}`">
          {{ statusMeta.icon }} {{ statusMeta.label }}
        </span>
        <span class="finish-reason" :title="run.finishReason ?? ''">
          结束原因：{{ finishReasonLabel(run.finishReason) }}
        </span>
        <span class="run-uid" :title="run.runUid">Run {{ run.runUid.slice(0, 8) }}</span>
      </div>

      <div class="summary-grid">
        <div class="field">
          <span class="field-label">Agent</span>
          <span class="field-value">{{ run.agentName }}</span>
        </div>
        <div class="field">
          <span class="field-label">模型</span>
          <span class="field-value" :title="run.modelIdentifier">
            {{ run.modelName }}
            <span class="model-identifier">{{ run.modelIdentifier }}</span>
          </span>
        </div>
        <div class="field">
          <span class="field-label">开始时间</span>
          <span class="field-value">{{ formatDateTime(run.startedAt) }}</span>
        </div>
        <div class="field">
          <span class="field-label">结束时间</span>
          <span class="field-value">{{ run.finishedAt ? formatDateTime(run.finishedAt) : '—' }}</span>
        </div>
        <div class="field">
          <span class="field-label">总耗时</span>
          <span class="field-value">{{ formatDuration(run.durationMs) }}</span>
        </div>
        <div class="field">
          <span class="field-label">首个正文输出</span>
          <span class="field-value">{{ formatFirstOutput(run.firstOutputMs) }}</span>
        </div>
        <div class="field">
          <span class="field-label">模型调用</span>
          <span class="field-value">{{ run.modelCalls }} 次</span>
        </div>
        <div class="field">
          <span class="field-label">工具调用</span>
          <span class="field-value">
            {{ run.toolCalls }} 次
            <span v-if="run.rejectedToolCalls > 0" class="rejected-note">（另有 {{ run.rejectedToolCalls }} 次校验拒绝，未执行）</span>
          </span>
        </div>
        <div class="field">
          <span class="field-label">Token 用量</span>
          <span class="field-value">{{ formatRunTokens(run) }}</span>
        </div>
        <div class="field field-wide">
          <span class="field-label">用户输入</span>
          <span class="field-value">{{ run.userInputPreview || '—' }}</span>
        </div>
        <div v-if="run.errorSummary" class="field field-wide">
          <span class="field-label">错误摘要</span>
          <span class="field-value error-text">{{ run.errorSummary }}</span>
        </div>
      </div>
    </section>

    <!-- 事件时间线 -->
    <section class="card timeline-card">
      <h2 class="timeline-title">时间线</h2>
      <p class="timeline-note">按真实事件顺序排列；工具与压缩卡片与聊天页使用同一批事件和展示规则。</p>

      <div v-if="timeline.length === 0" class="timeline-empty">本次运行没有记录到事件。</div>

      <ol class="timeline">
        <li v-for="(step, index) in timeline" :key="index" class="timeline-item">
          <!-- Agent 开始 -->
          <div v-if="step.kind === 'run-start'" class="marker marker-run">
            <span class="marker-icon">▶</span>
            <span class="marker-label">Agent 开始</span>
            <span v-if="step.agentName" class="marker-meta">{{ step.agentName }}</span>
          </div>

          <!-- 模型请求 -->
          <div v-else-if="step.kind === 'model'" class="step-row" :class="`is-${step.status}`">
            <span class="marker-icon">{{ stepStatusMeta(step.status).icon }}</span>
            <div class="step-main">
              <div class="step-head">
                <span class="step-title">{{ modelTitle(step) }}</span>
                <span class="step-state">{{ stepStatusMeta(step.status).label }}<template v-if="step.durationMs !== null && step.durationMs !== undefined"> · {{ formatDuration(step.durationMs) }}</template></span>
              </div>
              <p class="step-meta">{{ modelUsageText(step) }}</p>
            </div>
          </div>

          <!-- 工具执行（复用聊天页卡片） -->
          <div v-else-if="step.kind === 'tool'" class="tool-wrap">
            <ToolCallCard :tool="step.tool">
              <template v-if="step.hasDetail && !step.tool.result" #footer>
                <button
                  class="tool-load"
                  type="button"
                  :disabled="isLoadingDetail(step.eventId)"
                  @click="loadToolResult(step)"
                >
                  {{ isLoadingDetail(step.eventId) ? '正在加载…' : '加载完整结果' }}
                </button>
              </template>
            </ToolCallCard>
          </div>

          <!-- 上下文压缩 / 裁剪（复用聊天页卡片） -->
          <CompactionCard v-else-if="step.kind === 'compact'" :compact="step.compact" />

          <!-- 运行结束 -->
          <div v-else-if="step.kind === 'run-end'" class="run-end" :class="`is-${step.status}`">
            <div class="step-row">
              <span class="marker-icon">{{ runEndStatusMeta(step.status).icon }}</span>
              <div class="step-main">
                <div class="step-head">
                  <span class="step-title">运行结束 · {{ runEndStatusMeta(step.status).label }}</span>
                  <span class="step-state">{{ finishReasonLabel(step.reason) }}</span>
                </div>
                <p v-if="step.roundsUsed !== undefined" class="step-meta">执行轮数 {{ step.roundsUsed }}</p>
                <div v-if="step.error" class="run-end-error">
                  <p class="error-text">{{ step.error.message }}</p>
                  <p v-if="step.error.hint" class="error-hint">{{ step.error.hint }}</p>
                </div>
                <div v-if="step.contentPreview" class="run-end-content">
                  <div class="field-label">最终回答</div>
                  <pre class="content-text">{{ step.contentPreview }}</pre>
                  <p v-if="step.truncated" class="truncated-note">正文过长，仅保留前 4000 字符</p>
                  <button
                    v-if="step.hasDetail"
                    class="tool-load"
                    type="button"
                    :disabled="isLoadingDetail(step.eventId)"
                    @click="loadRunContent(step)"
                  >
                    {{ isLoadingDetail(step.eventId) ? '正在加载…' : '查看完整正文' }}
                  </button>
                </div>
                <p v-else-if="step.status === 'completed'" class="step-meta">（本次运行没有正文输出）</p>
              </div>
            </div>
          </div>
        </li>
      </ol>
    </section>
  </template>
</template>

<style scoped>
.btn-back {
  margin-bottom: 12px;
  padding: 0;
  border: none;
  background: none;
  color: var(--text-secondary);
  font-size: 12px;
  cursor: pointer;
}

.btn-back:hover {
  color: var(--accent);
}

.state-text {
  padding: 16px 0;
  color: var(--text-tertiary);
  font-size: 12px;
}

.summary-card {
  margin-bottom: 12px;
}

.summary-head {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}

.status-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 9px;
  border-radius: 6px;
  font-size: 11.5px;
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

.finish-reason {
  color: var(--text-secondary);
  font-size: 11.5px;
}

.run-uid {
  margin-left: auto;
  color: var(--text-tertiary);
  font-family: var(--font-mono);
  font-size: 10.5px;
}

.summary-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(190px, 1fr));
  gap: 10px 16px;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.field-wide {
  grid-column: 1 / -1;
}

.field-label {
  color: var(--text-tertiary);
  font-size: 10.5px;
  font-weight: 600;
}

.field-value {
  overflow-wrap: anywhere;
  color: var(--text-primary);
  font-size: 12.5px;
}

.model-identifier {
  font-family: var(--font-mono);
  font-size: 10.5px;
  color: var(--text-tertiary);
}

.rejected-note {
  color: var(--warning);
  font-size: 10.5px;
}

.error-text {
  color: var(--danger);
}

.timeline-card {
  padding: 14px 16px;
}

.timeline-title {
  font-family: var(--font-display);
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.timeline-note {
  margin-top: 2px;
  color: var(--text-tertiary);
  font-size: 10.5px;
}

.timeline-empty {
  padding: 16px 0;
  color: var(--text-tertiary);
  font-size: 12px;
}

.timeline {
  position: relative;
  margin: 14px 0 0;
  padding: 0 0 0 4px;
  list-style: none;
}

.timeline::before {
  position: absolute;
  top: 6px;
  bottom: 6px;
  left: 11px;
  width: 1px;
  background: var(--border);
  content: '';
}

.timeline-item {
  position: relative;
  padding: 0 0 12px 26px;
}

.timeline-item:last-child {
  padding-bottom: 2px;
}

.timeline-item::before {
  position: absolute;
  top: 6px;
  left: 6px;
  width: 11px;
  height: 11px;
  border: 2px solid var(--border);
  border-radius: 50%;
  background: var(--bg-base);
  content: '';
}

.marker {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 22px;
}

.marker-icon {
  color: var(--text-secondary);
  font-size: 11px;
}

.marker-run .marker-icon {
  color: var(--accent);
}

.marker-label {
  color: var(--text-primary);
  font-size: 12.5px;
  font-weight: 600;
}

.marker-meta {
  color: var(--text-tertiary);
  font-size: 11px;
}

.step-row {
  display: flex;
  gap: 8px;
  align-items: flex-start;
}

.step-row .marker-icon {
  margin-top: 2px;
}

.step-row.is-success .marker-icon {
  color: var(--success);
}

.step-row.is-error .marker-icon {
  color: var(--danger);
}

.step-row.is-running .marker-icon {
  color: var(--accent);
}

.step-row.is-cancelled .marker-icon,
.step-row.is-unknown .marker-icon {
  color: var(--warning);
}

.step-main {
  flex: 1;
  min-width: 0;
}

.step-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  flex-wrap: wrap;
}

.step-title {
  color: var(--text-primary);
  font-size: 12.5px;
  font-weight: 600;
}

.step-state {
  color: var(--text-secondary);
  font-size: 11px;
}

.step-row.is-success .step-state {
  color: var(--success);
}

.step-row.is-error .step-state {
  color: var(--danger);
}

.step-meta {
  margin-top: 2px;
  color: var(--text-tertiary);
  font-size: 11px;
}

.tool-wrap {
  margin-top: 2px;
}

.tool-load {
  margin-top: 6px;
  padding: 2px 10px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg-surface);
  color: var(--accent);
  font-size: 11px;
  cursor: pointer;
}

.tool-load:hover:not(:disabled) {
  border-color: var(--accent);
  background: var(--bg-hover);
}

.tool-load:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.run-end .step-row {
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-hover);
}

.run-end.is-error .step-row {
  border-color: color-mix(in srgb, var(--danger) 45%, var(--border));
}

.run-end-error {
  margin-top: 6px;
}

.error-hint {
  margin-top: 2px;
  color: var(--text-tertiary);
  font-size: 11px;
}

.run-end-content {
  margin-top: 8px;
}

.truncated-note {
  margin-top: 4px;
  color: var(--text-tertiary);
  font-size: 10.5px;
}

.content-text {
  max-height: 320px;
  margin: 4px 0 0;
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg-surface);
  color: var(--text-primary);
  font-family: var(--font-mono);
  font-size: 11px;
  line-height: 1.5;
  overflow: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 48px 20px;
  text-align: center;
}

.empty-title {
  font-size: 14px;
  font-weight: 600;
}

.empty-text {
  max-width: 380px;
  color: var(--text-secondary);
  font-size: 12px;
}

.empty-state .btn-primary {
  margin-top: 10px;
}
</style>
