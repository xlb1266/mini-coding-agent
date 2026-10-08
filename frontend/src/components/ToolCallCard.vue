<script setup lang="ts">
/** 工具调用子卡片：收起时是“类型徽标 + 动作前缀 + 主参数 pill + 状态图标”一行，
 * 点击才展开调用参数与返回结果（模仿 coding agent 轨迹区的卡片样式）。
 *
 * 状态给出图标与文字提示（aria-label / title，不只靠颜色）；工具返回内容按
 * 不可信文本处理，全部走文本插值。展开/收起时向父级发 toggle 事件，由父级
 * 调整滚动位置，避免阅读跳动。
 */
import { computed, ref } from 'vue'

import type { ToolActivity, ToolActivityStatus } from '../stores/chat'
import {
  formatResultText,
  RESULT_PREVIEW_LIMIT,
  shellBadge,
  toolAction,
  toolRowText,
} from '../views/chat-format'

const props = defineProps<{ tool: ToolActivity }>()
const emit = defineEmits<{ (e: 'toggle', card: HTMLElement): void }>()

const rootEl = ref<HTMLElement | null>(null)
const expanded = ref(false)
const resultOpen = ref(false)

const STATUS_META: Record<ToolActivityStatus, { label: string; icon: string }> = {
  running: { label: '执行中', icon: '◔' },
  success: { label: '完成', icon: '✓' },
  error: { label: '失败', icon: '✕' },
  cancelled: { label: '已取消', icon: '○' },
  unknown: { label: '状态未知', icon: '?' },
}

const statusMeta = computed(() => STATUS_META[props.tool.status] ?? STATUS_META.unknown!)

/** Shell 结果徽章（阶段十五 demo，纯展示）：✓/✗、超时终止、后台运行中+taskId。 */
const shell = computed(() => shellBadge(props.tool))

/** taskId 展示截断：过长时省略中段，完整值放 title。 */
const taskIdShort = computed(() => {
  const id = shell.value?.taskId
  if (!id) return ''
  return id.length <= 12 ? id : `${id.slice(0, 10)}…`
})

const typeLabel = computed(() => {
  if (props.tool.toolType === 'mcp') return 'MCP'
  if (props.tool.toolType === 'builtin') return '内置工具'
  if (props.tool.toolType === 'skill') return 'Skill'
  return '工具'
})

const action = computed(() => toolAction(props.tool))

const rowText = computed(() => toolRowText(props.tool))

const durationText = computed(() => {
  if (props.tool.durationMs === undefined) return ''
  return props.tool.durationMs >= 1000
    ? `${(props.tool.durationMs / 1000).toFixed(1)}s`
    : `${props.tool.durationMs}ms`
})

/** 调用参数按原始 JSON 展示；参数不是合法对象时为 null（给出说明文字）。 */
const argumentsJson = computed(() => {
  const args = props.tool.arguments
  if (!args) return null
  if (Object.keys(args).length === 0) return '{}'
  return JSON.stringify(args, null, 2)
})

const resultText = computed(() => (props.tool.result ? formatResultText(props.tool.result.text) : ''))

/** 展开后结果先给前一段；超长时标注并提供“展开全部”。 */
const resultPreview = computed(() => {
  if (resultOpen.value || resultText.value.length <= RESULT_PREVIEW_LIMIT) {
    return { text: resultText.value, clipped: false }
  }
  return { text: resultText.value.slice(0, RESULT_PREVIEW_LIMIT), clipped: true }
})

function toggle(): void {
  expanded.value = !expanded.value
  resultOpen.value = false
  if (rootEl.value) emit('toggle', rootEl.value)
}
</script>

<template>
  <div ref="rootEl" class="tool-card" :class="`is-${tool.status}`">
    <button
      class="tool-card-head"
      type="button"
      :aria-expanded="expanded"
      :aria-label="`${action.prefix}${rowText ?? ''}（${statusMeta.label}）`"
      @click="toggle"
    >
      <span class="tool-badge" :class="`is-${action.badgeTone}`" :title="typeLabel" aria-hidden="true">
        {{ action.badgeLabel }}
      </span>
      <span class="tool-prefix">{{ action.prefix }}</span>
      <span v-if="rowText" class="tool-pill" :title="rowText">{{ rowText }}</span>
      <!-- Shell 结果徽章：超时 / 后台任务 / 成败（纯展示） -->
      <span v-if="shell" class="tool-shell" :class="`is-${shell.tone}`">
        <span class="tool-shell-icon" aria-hidden="true">{{ shell.icon }}</span>
        <span class="tool-shell-label">{{ shell.label }}</span>
        <span
          v-if="taskIdShort"
          class="tool-shell-task"
          :title="`taskId：${shell.taskId}（展示用，状态以后台任务列表为准）`"
        >{{ taskIdShort }}</span>
      </span>
      <span v-else class="tool-status" :title="statusMeta.label">
        <span class="tool-status-icon" :aria-label="statusMeta.label">{{ statusMeta.icon }}</span>
        <span v-if="durationText" class="tool-duration">{{ durationText }}</span>
      </span>
      <span class="tool-chevron" :class="{ 'is-open': expanded }" aria-hidden="true">›</span>
    </button>
    <div v-if="expanded" class="tool-card-body">
      <div class="tool-section">
        <div class="tool-section-label">调用参数</div>
        <pre v-if="argumentsJson" class="tool-code">{{ argumentsJson }}</pre>
        <p v-else class="tool-section-empty">参数不是合法的 JSON 对象，或本次调用没有参数</p>
      </div>
      <div v-if="resultText" class="tool-section">
        <div class="tool-section-label">{{ tool.status === 'error' ? '失败原因' : '返回结果' }}</div>
        <pre class="tool-result">{{ resultPreview.text }}</pre>
        <p v-if="resultPreview.clipped" class="tool-note">
          已截断，共 {{ resultText.length }} 字符
          <button class="tool-link" type="button" @click="resultOpen = true">展开全部</button>
        </p>
        <p v-if="tool.result?.truncated" class="tool-note">
          结果过长，仅保留前 {{ tool.result.text.length }} 字符（原始约 {{ tool.result.totalLength }} 字符）
        </p>
      </div>
      <p v-else-if="tool.status === 'cancelled'" class="tool-section-empty">执行被取消，没有产生结果</p>
      <p v-else-if="tool.status === 'unknown'" class="tool-section-empty">连接中断，无法确认执行结果</p>
      <!-- 详情页等外部入口可放“加载完整结果”等补充操作；聊天页不使用 -->
      <slot name="footer" />
    </div>
  </div>
</template>

<style scoped>
.tool-card {
  margin: 3px 0;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-hover);
  font-size: 11px;
  text-align: left;
}

.tool-card-head {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 5px 7px;
  border: none;
  border-radius: 8px;
  background: none;
  cursor: pointer;
}

.tool-card-head:hover {
  background: var(--bg-active);
}

/* 类型徽标：文件类/accent 用主色底，小图标类用浅底 */
.tool-badge {
  display: inline-flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
  min-width: 17px;
  height: 16px;
  padding: 0 3px;
  border-radius: 4px;
  font-size: 8.5px;
  font-weight: 700;
  letter-spacing: 0.2px;
}

.tool-badge.is-accent {
  background: var(--accent);
  color: var(--text-inverse);
}

.tool-badge.is-muted {
  background: var(--bg-active);
  color: var(--text-secondary);
  font-size: 10px;
}

.tool-prefix {
  flex-shrink: 0;
  color: var(--text-secondary);
  font-size: 11.5px;
}

/* 主参数 pill：等宽字体，比卡片底色再深一档 */
.tool-pill {
  flex: 1;
  overflow: hidden;
  min-width: 0;
  padding: 1px 7px;
  border-radius: 5px;
  background: var(--bg-active);
  color: var(--text-primary);
  font-family: var(--font-mono);
  font-size: 10.5px;
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tool-status {
  display: inline-flex;
  flex-shrink: 0;
  gap: 4px;
  align-items: center;
  margin-left: auto;
  color: var(--text-tertiary);
  font-size: 10.5px;
}

.tool-status-icon {
  color: var(--text-secondary);
  font-size: 11px;
}

.tool-card.is-success .tool-status-icon {
  color: var(--success);
}

.tool-card.is-error .tool-status-icon {
  color: var(--danger);
}

.tool-card.is-running .tool-status-icon {
  color: var(--accent);
  animation: tool-pulse 1.2s ease-in-out infinite;
}

.tool-card.is-cancelled .tool-status-icon,
.tool-card.is-unknown .tool-status-icon {
  color: var(--warning);
}

.tool-card.is-error .tool-duration,
.tool-card.is-cancelled .tool-duration,
.tool-card.is-unknown .tool-duration {
  color: var(--danger);
}

/* ---- Shell 结果徽章（阶段十五 demo） ---- */

.tool-shell {
  display: inline-flex;
  flex-shrink: 0;
  gap: 3px;
  align-items: center;
  margin-left: auto;
  font-size: 10.5px;
}

.tool-shell-icon {
  font-size: 11px;
}

.tool-shell-label {
  color: var(--text-secondary);
  font-weight: 600;
}

.tool-shell.is-success .tool-shell-icon {
  color: var(--success);
}

.tool-shell.is-danger .tool-shell-icon {
  color: var(--danger);
}

.tool-shell.is-danger .tool-shell-label {
  color: var(--danger);
}

.tool-shell.is-running .tool-shell-icon {
  color: var(--accent);
  animation: tool-pulse 1.2s ease-in-out infinite;
}

.tool-shell.is-running .tool-shell-label {
  color: var(--accent);
}

.tool-shell-task {
  max-width: 90px;
  overflow: hidden;
  padding: 1px 5px;
  border-radius: 4px;
  background: var(--bg-active);
  color: var(--text-tertiary);
  font-family: var(--font-mono);
  font-size: 9.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

@keyframes tool-pulse {
  0%,
  100% {
    opacity: 0.35;
  }

  50% {
    opacity: 1;
  }
}

.tool-chevron {
  flex-shrink: 0;
  color: var(--text-tertiary);
  font-size: 11px;
  transition: transform 0.15s ease;
}

.tool-chevron.is-open {
  transform: rotate(90deg);
}

.tool-card-body {
  padding: 2px 8px 8px;
  border-top: 1px dashed var(--border);
}

.tool-section {
  margin-top: 6px;
}

.tool-section-label {
  margin-bottom: 3px;
  color: var(--text-tertiary);
  font-size: 10px;
  font-weight: 600;
}

.tool-section-empty {
  color: var(--text-tertiary);
  font-size: 10.5px;
}

.tool-code,
.tool-result {
  max-height: 320px;
  margin: 0;
  padding: 6px 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg-surface);
  color: var(--text-primary);
  font-family: var(--font-mono);
  font-size: 10.5px;
  line-height: 1.5;
  overflow: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.tool-note {
  margin-top: 4px;
  color: var(--text-tertiary);
  font-size: 10.5px;
}

.tool-link {
  margin-left: 4px;
  padding: 0;
  border: none;
  background: none;
  color: var(--accent);
  font-size: 10.5px;
  cursor: pointer;
}

.tool-link:hover {
  color: var(--accent-hover);
}
</style>
