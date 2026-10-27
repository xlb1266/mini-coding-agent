<script setup lang="ts">
/** 工具调用组卡片：默认只显示“使用N个工具”一行，点击才展开组内的一次次工具调用。
 *
 * 组内子卡片仍是收起的，需要再点一次才看参数与结果——与轨迹区
 * “组 → 单次调用”的两级折叠保持一致。文件写入类调用在组内聚合为
 * “修改了 N 个文件”一张卡（阶段十五 demo，默认同样收起）。
 * 任一工具仍在执行时标题行图标闪动，组内有失败时在标题行用文字点明。
 */
import { computed, ref } from 'vue'

import FileWriteGroupCard from './FileWriteGroupCard.vue'
import ToolCallCard from './ToolCallCard.vue'
import { partitionGroupTools } from '../views/chat-format'
import type { ToolActivity } from '../stores/chat'

const props = defineProps<{ tools: ToolActivity[] }>()
const emit = defineEmits<{ (e: 'toggle', card: HTMLElement): void }>()

const rootEl = ref<HTMLElement | null>(null)
const expanded = ref(false)

const nodes = computed(() => partitionGroupTools(props.tools))

const running = computed(() => props.tools.some((tool) => tool.status === 'running'))
const failedCount = computed(
  () => props.tools.filter((tool) => tool.status === 'error' || tool.status === 'unknown').length,
)
const cancelledCount = computed(
  () => props.tools.filter((tool) => tool.status === 'cancelled').length,
)

const title = computed(() => `使用${props.tools.length}个工具`)

/** 组内非成功状态的文字说明：失败与取消都点明，避免只靠颜色区分。 */
const stateText = computed(() => {
  const parts: string[] = []
  if (failedCount.value) parts.push(`${failedCount.value} 个失败`)
  if (cancelledCount.value) parts.push(`${cancelledCount.value} 个已取消`)
  return parts.join(' · ')
})

function toggle(): void {
  expanded.value = !expanded.value
  if (rootEl.value) emit('toggle', rootEl.value)
}
</script>

<template>
  <div ref="rootEl" class="tool-group" :class="{ 'is-running': running }">
    <button class="tool-group-head" type="button" :aria-expanded="expanded" @click="toggle">
      <span class="tool-group-icon" aria-hidden="true">⚒</span>
      <span class="tool-group-title">{{ title }}</span>
      <span v-if="stateText" class="tool-group-state">{{ stateText }}</span>
      <span class="tool-chevron" :class="{ 'is-open': expanded }" aria-hidden="true">▾</span>
    </button>
    <div v-if="expanded" class="tool-group-body">
      <template v-for="(node, index) in nodes" :key="node.kind === 'tool' ? node.tool.callId : `writes-${index}`">
        <FileWriteGroupCard
          v-if="node.kind === 'writes'"
          :entries="node.entries"
          @toggle="(card: HTMLElement) => emit('toggle', card)"
        />
        <ToolCallCard
          v-else
          :tool="node.tool"
          @toggle="(card: HTMLElement) => emit('toggle', card)"
        />
      </template>
    </div>
  </div>
</template>

<style scoped>
.tool-group {
  margin: 3px 0;
}

.tool-group-head {
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

.tool-group-head:hover {
  background: var(--bg-hover);
}

.tool-group-icon {
  flex-shrink: 0;
  width: 14px;
  color: var(--text-secondary);
  font-size: 11px;
  text-align: center;
}

.tool-group.is-running .tool-group-icon {
  color: var(--accent);
  animation: group-pulse 1.2s ease-in-out infinite;
}

@keyframes group-pulse {
  0%,
  100% {
    opacity: 0.35;
  }

  50% {
    opacity: 1;
  }
}

.tool-group-title {
  color: var(--text-secondary);
  font-size: 11.5px;
  font-weight: 600;
}

.tool-group-state {
  flex-shrink: 0;
  color: var(--danger);
  font-size: 10.5px;
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

/* 组内子卡片：缩进一级，体现“组 → 单次调用”的层级 */
.tool-group-body {
  display: flex;
  flex-direction: column;
  margin: 2px 0 4px 16px;
}
</style>
