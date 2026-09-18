<script setup lang="ts">
/** 上下文压缩 / 备用裁剪的过程卡片。

 * 只展示触发原因与安全的估算数字（估算值，非模型实际 Token 用量），
 * 不展示摘要正文与历史内容；状态同时用图标与文字标注，不只靠颜色。
 */
import { computed } from 'vue'

import type { CompactActivity } from '../stores/chat'

const props = defineProps<{ compact: CompactActivity }>()

const STATUS_META: Record<CompactActivity['status'], { label: string; icon: string }> = {
  running: { label: '压缩中', icon: '◔' },
  success: { label: '已完成', icon: '✓' },
  partial: { label: '部分完成', icon: '◑' },
  error: { label: '失败', icon: '✕' },
  cancelled: { label: '已取消', icon: '○' },
  unknown: { label: '状态未知', icon: '?' },
  trimmed: { label: '已裁剪', icon: '✂' },
}

const statusMeta = computed(() => STATUS_META[props.compact.status] ?? STATUS_META.unknown!)

const isTrim = computed(() => props.compact.status === 'trimmed')
const title = computed(() => (isTrim.value ? '上下文裁剪' : '上下文压缩'))

const durationText = computed(() => {
  const ms = props.compact.durationMs
  if (ms === undefined || ms === null) return ''
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`
})

const tokenText = computed(() => {
  const before = props.compact.estimatedTokensBefore
  const after = props.compact.estimatedTokensAfter
  if (before === undefined || before === null) return ''
  if (after === undefined || after === null) return `估算 ${before} token`
  return `估算 ${before} → ${after} token`
})

const messageText = computed(() => {
  if (isTrim.value) {
    const dropped = props.compact.droppedMessages
    const kept = props.compact.keptMessages
    if (dropped === undefined || dropped === null) return ''
    return kept === undefined || kept === null
      ? `裁掉 ${dropped} 条消息`
      : `裁掉 ${dropped} 条，保留 ${kept} 条`
  }
  const processed = props.compact.processedMessages
  const kept = props.compact.keptMessages
  if (processed === undefined || processed === null) return ''
  return kept === undefined || kept === null
    ? `整理 ${processed} 条消息`
    : `整理 ${processed} 条，保留 ${kept} 条`
})

const callText = computed(() => {
  const batches = props.compact.batches
  const calls = props.compact.modelCalls
  if ((batches === undefined || batches === null) && (calls === undefined || calls === null)) {
    return ''
  }
  const parts: string[] = []
  if (batches !== undefined && batches !== null) parts.push(`${batches} 批`)
  if (calls !== undefined && calls !== null) parts.push(`模型调用 ${calls} 次`)
  return parts.join(' · ')
})

const summaryText = computed(() => {
  const tokens = props.compact.summaryTokens
  if (tokens === undefined || tokens === null || isTrim.value) return ''
  return `摘要约 ${tokens} token（估算）`
})
</script>

<template>
  <div class="compact-card" :class="`is-${compact.status}`">
    <div class="compact-head">
      <span class="compact-icon" :aria-label="statusMeta.label">{{ statusMeta.icon }}</span>
      <span class="compact-title">{{ title }}</span>
      <span class="compact-state">
        {{ statusMeta.label }}<template v-if="durationText"> · {{ durationText }}</template>
      </span>
    </div>
    <p v-if="compact.reason" class="compact-reason">{{ compact.reason }}</p>
    <div class="compact-facts">
      <span v-if="tokenText" class="fact">{{ tokenText }}</span>
      <span v-if="messageText" class="fact">{{ messageText }}</span>
      <span v-if="summaryText" class="fact">{{ summaryText }}</span>
      <span v-if="callText" class="fact">{{ callText }}</span>
    </div>
    <p v-if="compact.error" class="compact-error">{{ compact.error.message }}</p>
    <p v-if="isTrim" class="compact-note">裁剪只影响本次请求，会话历史保持完整。</p>
  </div>
</template>

<style scoped>
.compact-card {
  margin: 4px 0;
  padding: 5px 8px 6px 8px;
  border: 1px dashed var(--border);
  border-radius: 8px;
  background: var(--bg-hover);
  font-size: 11px;
  text-align: left;
}

.compact-head {
  display: flex;
  align-items: center;
  gap: 6px;
}

.compact-icon {
  flex-shrink: 0;
  width: 14px;
  color: var(--text-secondary);
  font-size: 11px;
  text-align: center;
}

.compact-card.is-success .compact-icon,
.compact-card.is-partial .compact-icon {
  color: var(--success);
}

.compact-card.is-error .compact-icon {
  color: var(--danger);
}

.compact-card.is-running .compact-icon {
  color: var(--accent);
  animation: compact-pulse 1.2s ease-in-out infinite;
}

.compact-card.is-cancelled .compact-icon,
.compact-card.is-unknown .compact-icon,
.compact-card.is-trimmed .compact-icon {
  color: var(--warning);
}

@keyframes compact-pulse {
  0%,
  100% {
    opacity: 0.35;
  }

  50% {
    opacity: 1;
  }
}

.compact-title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  color: var(--text-primary);
  font-size: 11.5px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.compact-state {
  flex-shrink: 0;
  color: var(--text-secondary);
  font-size: 10.5px;
}

.compact-card.is-success .compact-state,
.compact-card.is-partial .compact-state {
  color: var(--success);
}

.compact-card.is-error .compact-state {
  color: var(--danger);
}

.compact-card.is-cancelled .compact-state,
.compact-card.is-unknown .compact-state,
.compact-card.is-trimmed .compact-state {
  color: var(--warning);
}

.compact-reason {
  margin: 3px 0 0 20px;
  color: var(--text-secondary);
  font-size: 10.5px;
  line-height: 1.5;
}

.compact-facts {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
  margin: 3px 0 0 20px;
}

.fact {
  color: var(--text-tertiary);
  font-size: 10.5px;
}

.compact-error {
  margin: 4px 0 0 20px;
  color: var(--danger);
  font-size: 10.5px;
}

.compact-note {
  margin: 3px 0 0 20px;
  color: var(--text-tertiary);
  font-size: 10px;
}
</style>
