<script setup lang="ts">
/** 文件写入聚合卡片（阶段十五 demo）：轨迹组内的文件写入类调用合并成
 *  "修改了 N 个文件"一张卡，默认收起，点击展开逐文件查看 diff / 写入内容。
 *
 * edit_file 的结果带统一 diff（+/- 行着色）；file 的 write 动作没有原文件
 * 内容可比对，展示写入内容本身并标注结果（新建/覆盖/追加）。工具返回内容
 * 按不可信文本处理：全部走文本插值，不拼 HTML。
 */
import { computed, ref } from 'vue'

import type { FileWriteEntry } from '../views/chat-format'

const props = defineProps<{ entries: FileWriteEntry[] }>()
const emit = defineEmits<{ (e: 'toggle', card: HTMLElement): void }>()

const rootEl = ref<HTMLElement | null>(null)
const expanded = ref(false)

const title = computed(() =>
  props.entries.length === 1 ? '修改了 1 个文件' : `修改了 ${props.entries.length} 个文件`,
)

function toggle(): void {
  expanded.value = !expanded.value
  if (rootEl.value) emit('toggle', rootEl.value)
}

function sizeText(entry: FileWriteEntry): string {
  if (entry.sizeBytes === null) return ''
  return `${entry.sizeBytes} 字节`
}

/** diff 行的展示分类：+ 增、- 删、@@ 与文件头为元信息，其余为上下文。 */
type DiffLineKind = 'add' | 'del' | 'meta' | 'context'

function diffLineKind(line: string): DiffLineKind {
  if (line.startsWith('+')) return 'add'
  if (line.startsWith('-')) return 'del'
  if (line.startsWith('@@') || line.startsWith('---') || line.startsWith('+++')) return 'meta'
  return 'context'
}

const OPEN_BODY_MAX_LINES = 400

/** 每个文件展开后的行列表；超长行数截断并提示（防止大 diff 撑爆页面）。 */
function bodyLines(entry: FileWriteEntry): { text: string; kind: DiffLineKind }[] {
  const source = entry.isEdit ? entry.diff : entry.content
  const lines = source.split('\n')
  if (lines.length > OPEN_BODY_MAX_LINES) {
    const clipped = lines.slice(0, OPEN_BODY_MAX_LINES).map((text) => ({ text, kind: diffLineKind(text) }))
    clipped.push({ text: `…（其余 ${lines.length - OPEN_BODY_MAX_LINES} 行省略，可通过读取文件查看完整内容）`, kind: 'meta' })
    return clipped
  }
  return lines.map((text) => ({ text, kind: entry.isEdit ? diffLineKind(text) : 'add' }))
}
</script>

<template>
  <div ref="rootEl" class="writes-card">
    <button class="writes-head" type="button" :aria-expanded="expanded" @click="toggle">
      <span class="writes-icon" aria-hidden="true">▤</span>
      <span class="writes-title">{{ title }}</span>
      <span class="writes-chevron" :class="{ 'is-open': expanded }" aria-hidden="true">▾</span>
    </button>
    <div v-if="expanded" class="writes-body">
      <div v-for="entry in entries" :key="entry.callId" class="writes-file">
        <div class="writes-file-head">
          <span class="writes-path" :title="entry.path">{{ entry.path }}</span>
          <span class="writes-outcome">{{ entry.outcomeLabel }}</span>
          <span v-if="sizeText(entry)" class="writes-size">{{ sizeText(entry) }}</span>
        </div>
        <pre v-if="bodyLines(entry).length > 0" class="writes-diff"><span
          v-for="(line, index) in bodyLines(entry)"
          :key="index"
          :class="`is-${line.kind}`"
          class="writes-line"
        >{{ line.text || ' ' }}
</span></pre>
        <p v-else class="writes-empty">没有可展示的变更内容</p>
        <p v-if="entry.truncated" class="writes-note">结果过长被截断，完整内容请读取文件查看</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.writes-card {
  margin: 3px 0;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-hover);
  font-size: 11px;
  text-align: left;
}

.writes-head {
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

.writes-head:hover {
  background: var(--bg-active);
}

.writes-icon {
  flex-shrink: 0;
  color: var(--text-secondary);
  font-size: 11px;
}

.writes-title {
  color: var(--text-secondary);
  font-size: 11.5px;
  font-weight: 600;
}

.writes-chevron {
  flex-shrink: 0;
  margin-left: auto;
  color: var(--text-tertiary);
  font-size: 10px;
  transition: transform 0.15s ease;
}

.writes-chevron.is-open {
  transform: rotate(180deg);
}

.writes-body {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 2px 8px 8px;
  border-top: 1px dashed var(--border);
}

.writes-file-head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 6px;
  min-width: 0;
}

.writes-path {
  overflow: hidden;
  min-width: 0;
  color: var(--text-primary);
  font-family: var(--font-mono);
  font-size: 10.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.writes-outcome {
  flex-shrink: 0;
  padding: 1px 5px;
  border-radius: 4px;
  background: var(--accent-dim, var(--bg-active));
  color: var(--accent);
  font-size: 10px;
  font-weight: 600;
}

.writes-size {
  flex-shrink: 0;
  color: var(--text-tertiary);
  font-size: 10px;
}

.writes-diff {
  max-height: 280px;
  margin: 4px 0 0;
  padding: 6px 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg-surface);
  font-family: var(--font-mono);
  font-size: 10.5px;
  line-height: 1.5;
  overflow: auto;
  white-space: pre;
}

.writes-line {
  display: block;
  color: var(--text-secondary);
}

.writes-line.is-add {
  background: rgba(46, 160, 67, 0.16);
  color: var(--success);
}

.writes-line.is-del {
  background: rgba(248, 81, 73, 0.14);
  color: var(--danger);
  text-decoration: line-through;
}

.writes-line.is-meta {
  color: var(--text-tertiary);
}

.writes-empty {
  margin-top: 4px;
  color: var(--text-tertiary);
  font-size: 10.5px;
}

.writes-note {
  margin-top: 4px;
  color: var(--text-tertiary);
  font-size: 10.5px;
}
</style>
