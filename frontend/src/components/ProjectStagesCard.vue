<script setup lang="ts">
import { computed, ref } from 'vue'

import type { StageProject, StageProjectStage, StageProjectStatus } from '../api/chat'

/** 阶段清单卡（阶段化开发 v2）：默认收成一行（标题 + 进度），点击展开看
 *  完整阶段清单（目标全文）与推进按钮；展开状态记入 localStorage。 */
const props = defineProps<{
  project: StageProject
  /** 当前会话在项目里的角色（plan / stage / summary） */
  sessionRole: 'plan' | 'stage' | 'summary' | null
  /** 当前阶段会话是否处于等待验收状态 */
  awaitingAcceptance: boolean
  /** 推进请求进行中（按钮置灰防重复提交） */
  busy: boolean
}>()

const emit = defineEmits<{
  (event: 'start'): void
  (event: 'next'): void
  (event: 'summary'): void
  (event: 'openSession', sessionId: number): void
}>()

// 展开状态记忆（跨刷新）；默认收起，只占一行
const EXPANDED_KEY = 'mini-agent:stage-card-expanded'

function readExpanded(): boolean {
  try {
    return localStorage.getItem(EXPANDED_KEY) === '1'
  } catch {
    return false
  }
}

const expanded = ref(readExpanded())

function toggleExpanded(): void {
  expanded.value = !expanded.value
  try {
    localStorage.setItem(EXPANDED_KEY, expanded.value ? '1' : '0')
  } catch {
    // 存不了就只在本页生效
  }
}

const STATUS_LABELS: Record<StageProjectStatus, string> = {
  awaiting_start: '待开始',
  in_progress: '进行中',
  awaiting_summary: '待汇总',
  summarizing: '汇总生成中',
  completed: '已完成',
}

const statusLabel = computed(() => STATUS_LABELS[props.project.status] ?? props.project.status)

const doneCount = computed(
  () => props.project.stages.filter((stage) => stage.status === 'done').length,
)

const progressLabel = computed(() =>
  props.project.stages.length > 0 ? `${doneCount.value}/${props.project.stages.length}` : '',
)

/** 下一个待推进的阶段（1-based 序号）；全部完成时为 0 */
const nextPendingIndex = computed(
  () => props.project.stages.findIndex((stage) => stage.status !== 'done') + 1,
)

/** 规划会话：等待用户确认计划开始开发 */
const showStart = computed(
  () => props.sessionRole === 'plan' && props.project.status === 'awaiting_start',
)

/** 阶段会话：等待验收时可进入下一阶段或生成汇总 */
const showStageAction = computed(() => props.sessionRole === 'stage' && props.awaitingAcceptance)

const nextStageAction = computed(() =>
  nextPendingIndex.value > 0 ? 'next' : 'summary',
)

function stageIcon(status: StageProjectStage['status']): string {
  if (status === 'done') return '✓'
  if (status === 'in_progress') return '◔'
  return '○'
}

function stageSessionId(index: number): number | null {
  return props.project.stageSessions[index] ?? null
}

function handleStageClick(index: number): void {
  const sessionId = stageSessionId(index)
  if (sessionId !== null) emit('openSession', sessionId)
}
</script>

<template>
  <div class="stage-project-card" :class="{ 'is-expanded': expanded }">
    <button
      type="button"
      class="card-head"
      :title="expanded ? '收起阶段清单' : '展开阶段清单'"
      @click="toggleExpanded"
    >
      <span class="project-tag">阶段项目</span>
      <span class="project-title">{{ project.title }}</span>
      <span class="project-status">{{ statusLabel }}<template v-if="progressLabel"> · {{ progressLabel }}</template></span>
      <span class="card-chevron" aria-hidden="true">▾</span>
    </button>

    <div v-show="expanded" class="card-body">
      <ol class="stage-list">
        <li
          v-for="(stage, index) in project.stages"
          :key="index"
          class="stage-item"
          :class="`is-${stage.status}`"
        >
          <button
            v-if="stageSessionId(index + 1) !== null"
            type="button"
            class="stage-row is-link"
            @click="handleStageClick(index + 1)"
          >
            <span class="stage-line">
              <span class="stage-icon">{{ stageIcon(stage.status) }}</span>
              <span class="stage-name">{{ stage.name }}</span>
              <span v-if="stageSessionId(index + 1) !== null" class="stage-jump">查看会话</span>
            </span>
            <span class="stage-goal">{{ stage.goal }}</span>
          </button>
          <div v-else class="stage-row">
            <span class="stage-line">
              <span class="stage-icon">{{ stageIcon(stage.status) }}</span>
              <span class="stage-name">{{ stage.name }}</span>
            </span>
            <span class="stage-goal">{{ stage.goal }}</span>
          </div>
        </li>
      </ol>

      <div v-if="showStart || showStageAction" class="card-actions">
        <template v-if="showStart">
          <button class="btn-primary" type="button" :disabled="busy" @click="emit('start')">
            开始阶段 1（将自动新建会话）
          </button>
          <span class="action-hint">确认计划后进入独立会话逐阶段开发</span>
        </template>
        <template v-else-if="showStageAction">
          <button
            v-if="nextStageAction === 'next'"
            class="btn-primary"
            type="button"
            :disabled="busy"
            @click="emit('next')"
          >
            进入下一阶段（将自动新建会话）
          </button>
          <button v-else class="btn-primary" type="button" :disabled="busy" @click="emit('summary')">
            生成项目汇总（将自动新建会话）
          </button>
          <span class="action-hint">如需修改，直接在下方输入反馈即可</span>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
.stage-project-card {
  padding: 8px 14px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--bg-surface);
}

/* 头部整行可点：收起/展开切换 */
.card-head {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 2px 0;
  border: none;
  background: none;
  text-align: left;
  cursor: pointer;
}

.project-tag {
  flex-shrink: 0;
  padding: 1px 6px;
  border-radius: 4px;
  background: var(--accent);
  color: var(--text-inverse);
  font-size: 10px;
  font-weight: 650;
}

.project-title {
  overflow: hidden;
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.project-status {
  flex-shrink: 0;
  margin-left: auto;
  color: var(--text-tertiary);
  font-size: 10.5px;
}

.card-chevron {
  flex-shrink: 0;
  color: var(--text-tertiary);
  font-size: 10px;
  transition: transform 0.15s ease;
}

.is-expanded .card-chevron {
  transform: rotate(180deg);
}

.card-body {
  margin-top: 8px;
}

.stage-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.stage-row {
  display: flex;
  flex-direction: column;
  gap: 1px;
  width: 100%;
  padding: 4px 6px;
  border: none;
  border-radius: 6px;
  background: none;
  text-align: left;
}

button.stage-row {
  cursor: pointer;
}

button.stage-row:hover {
  background: var(--bg-hover);
}

/* 名称行：图标 + 阶段名（+ 跳转提示） */
.stage-line {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.stage-icon {
  flex-shrink: 0;
  width: 16px;
  color: var(--text-tertiary);
  font-size: 12px;
  text-align: center;
}

.is-done .stage-icon {
  color: var(--success);
}

.is-in_progress .stage-icon {
  color: var(--accent);
}

.stage-name {
  flex-shrink: 0;
  color: var(--text-primary);
  font-size: 12px;
  font-weight: 600;
}

.is-done .stage-name {
  color: var(--text-secondary);
}

.stage-jump {
  flex-shrink: 0;
  color: var(--accent);
  font-size: 10.5px;
}

/* 目标全文：换行完整展示，缩进与阶段名对齐 */
.stage-goal {
  padding-left: 24px;
  color: var(--text-tertiary);
  font-size: 11px;
  line-height: 1.5;
  overflow-wrap: break-word;
  white-space: normal;
}

.card-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px dashed var(--border);
}

.action-hint {
  color: var(--text-tertiary);
  font-size: 10.5px;
}
</style>
