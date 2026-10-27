<script setup lang="ts">
/** 模型思考卡片：默认只有“思考 · 耗时”一行，点击才展开思考正文。
 *
 * 思考只做展示，不属于回复正文；生成中标题显示“思考中”，
 * 正文不流式外显，避免占用阅读空间。
 */
import { computed, ref } from 'vue'

import type { ReasoningSegment } from '../stores/chat'

const props = defineProps<{ reasoning: ReasoningSegment; streaming?: boolean }>()
const emit = defineEmits<{ (e: 'toggle', card: HTMLElement): void }>()

const rootEl = ref<HTMLElement | null>(null)
const expanded = ref(false)

const durationText = computed(() => {
  const ms = props.reasoning.durationMs
  if (ms === undefined || ms === null) return ''
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`
})

function toggle(): void {
  expanded.value = !expanded.value
  if (rootEl.value) emit('toggle', rootEl.value)
}
</script>

<template>
  <div ref="rootEl" class="think-row" :class="{ 'is-streaming': streaming }">
    <button class="think-head" type="button" :aria-expanded="expanded" @click="toggle">
      <span class="think-icon" aria-hidden="true">✦</span>
      <span class="think-title">{{ streaming ? '思考中' : '思考' }}</span>
      <span v-if="durationText" class="think-duration">{{ durationText }}</span>
      <span class="think-chevron" :class="{ 'is-open': expanded }" aria-hidden="true">▾</span>
    </button>
    <p v-if="expanded" class="think-text">{{ reasoning.text }}</p>
  </div>
</template>

<style scoped>
.think-row {
  margin: 3px 0;
}

.think-head {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  max-width: 100%;
  padding: 3px 6px 3px 2px;
  border: none;
  border-radius: 6px;
  background: none;
  cursor: pointer;
}

.think-head:hover {
  background: var(--bg-hover);
}

.think-icon {
  flex-shrink: 0;
  width: 14px;
  color: var(--text-secondary);
  font-size: 11px;
  text-align: center;
}

.think-row.is-streaming .think-icon {
  color: var(--accent);
  animation: think-pulse 1.2s ease-in-out infinite;
}

@keyframes think-pulse {
  0%,
  100% {
    opacity: 0.35;
  }

  50% {
    opacity: 1;
  }
}

.think-title {
  color: var(--text-secondary);
  font-size: 11.5px;
  font-weight: 600;
}

.think-duration {
  flex-shrink: 0;
  color: var(--text-tertiary);
  font-size: 10.5px;
}

.think-chevron {
  flex-shrink: 0;
  color: var(--text-tertiary);
  font-size: 10px;
  transition: transform 0.15s ease;
}

.think-chevron.is-open {
  transform: rotate(180deg);
}

/* 展开后的思考正文：浅色正文直接接在标题下方，不再套一层卡片 */
.think-text {
  margin: 2px 0 2px 22px;
  color: var(--text-secondary);
  font-size: 11.5px;
  line-height: 1.6;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
