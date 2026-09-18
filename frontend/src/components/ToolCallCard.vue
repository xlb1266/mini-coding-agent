<script setup lang="ts">
/** 工具执行过程卡片：收起时只有状态与摘要，展开后看整理后的参数与结果。
 *
 * 状态同时使用图标与文字标注（不只靠颜色区分）；工具返回内容按不可信
 * 文本处理，全部走文本插值。展开/收起时向父级发 toggle 事件，由父级
 * 调整滚动位置，避免阅读跳动。
 */
import { computed, ref } from 'vue'

import type { ToolActivity, ToolActivityStatus } from '../stores/chat'
import { toolDisplayTitle } from '../stores/chat'
import { argumentEntries, formatResultText, RESULT_PREVIEW_LIMIT } from '../views/chat-format'

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

const typeLabel = computed(() => {
  if (props.tool.toolType === 'mcp') return 'MCP'
  if (props.tool.toolType === 'builtin') return '内置工具'
  if (props.tool.toolType === 'skill') return 'Skill'
  return '工具'
})

const title = computed(() => toolDisplayTitle(props.tool))

const durationText = computed(() => {
  if (props.tool.durationMs === undefined) return ''
  return props.tool.durationMs >= 1000
    ? `${(props.tool.durationMs / 1000).toFixed(1)}s`
    : `${props.tool.durationMs}ms`
})

const args = computed(() => argumentEntries(props.tool.arguments))

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
    <button class="tool-card-head" type="button" :aria-expanded="expanded" @click="toggle">
      <span class="tool-status-icon" :aria-label="statusMeta.label">{{ statusMeta.icon }}</span>
      <span class="tool-card-title">
        <span class="tool-name">{{ title }}</span>
        <span class="tool-type">{{ typeLabel }}</span>
      </span>
      <span class="tool-state">
        {{ statusMeta.label }}<template v-if="durationText"> · {{ durationText }}</template>
      </span>
      <span class="tool-chevron" :class="{ 'is-open': expanded }" aria-hidden="true">▾</span>
    </button>
    <p v-if="!expanded && tool.summary" class="tool-summary">{{ tool.summary }}</p>
    <div v-if="expanded" class="tool-card-body">
      <div class="tool-section">
        <div class="tool-section-label">输入参数</div>
        <div v-if="args.length" class="arg-list">
          <div v-for="arg in args" :key="arg.key" class="arg-row">
            <span class="arg-key">{{ arg.key }}</span>
            <span class="arg-value">{{ arg.value }}</span>
          </div>
        </div>
        <p v-else class="tool-section-empty">参数不是合法的 JSON 对象，或本次调用没有参数</p>
      </div>
      <div v-if="resultText" class="tool-section">
        <div class="tool-section-label">{{ tool.status === 'error' ? '失败原因' : '执行结果' }}</div>
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
  margin: 4px 0;
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
  padding: 5px 8px;
  border: none;
  border-radius: 8px;
  background: none;
  cursor: pointer;
}

.tool-card-head:hover {
  background: var(--bg-active);
}

.tool-status-icon {
  flex-shrink: 0;
  width: 14px;
  color: var(--text-secondary);
  font-size: 11px;
  text-align: center;
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

@keyframes tool-pulse {
  0%,
  100% {
    opacity: 0.35;
  }

  50% {
    opacity: 1;
  }
}

.tool-card-title {
  display: flex;
  flex: 1;
  align-items: baseline;
  gap: 6px;
  min-width: 0;
}

.tool-name {
  overflow: hidden;
  color: var(--text-primary);
  font-size: 11.5px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tool-type {
  flex-shrink: 0;
  padding: 1px 5px;
  border-radius: 4px;
  background: var(--bg-surface);
  color: var(--text-tertiary);
  font-size: 10px;
}

.tool-state {
  flex-shrink: 0;
  color: var(--text-secondary);
  font-size: 10.5px;
}

.tool-card.is-success .tool-state {
  color: var(--success);
}

.tool-card.is-error .tool-state {
  color: var(--danger);
}

.tool-card.is-cancelled .tool-state,
.tool-card.is-unknown .tool-state {
  color: var(--warning);
}

.tool-chevron {
  flex-shrink: 0;
  color: var(--text-tertiary);
  font-size: 10px;
  transition: transform 0.15s ease;
}

.tool-chevron.is-open {
  transform: rotate(180deg);
}

.tool-summary {
  overflow: hidden;
  padding: 0 8px 5px 28px;
  color: var(--text-secondary);
  font-size: 10.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tool-card-body {
  padding: 2px 8px 8px 28px;
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

.arg-list {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.arg-row {
  display: grid;
  grid-template-columns: minmax(60px, max-content) 1fr;
  gap: 8px;
}

.arg-key {
  color: var(--text-secondary);
  font-family: var(--font-mono);
  font-size: 10.5px;
  overflow-wrap: anywhere;
}

.arg-value {
  color: var(--text-primary);
  font-family: var(--font-mono);
  font-size: 10.5px;
  line-height: 1.5;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}

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
