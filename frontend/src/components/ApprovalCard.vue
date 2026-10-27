<script setup lang="ts">
/** 工具执行审批卡片：展示待审批的工具与整理后的参数，供用户批准或拒绝。
 *
 * pending 状态下按钮可点；点击后立即置灰（deciding）防重复提交，决议
 * 结果由 approval_result 事件更新；终态只读展示。操作失败信息显示在
 * 卡片内。工具返回内容按不可信文本处理，全部走文本插值。
 */
import { computed } from 'vue'

import type { ApprovalActivity, ApprovalDecisionStatus } from '../stores/chat'
import { argumentEntries } from '../views/chat-format'

const props = defineProps<{ approval: ApprovalActivity }>()
const emit = defineEmits<{ (e: 'decide', approved: boolean): void }>()

const STATUS_META: Record<ApprovalDecisionStatus, { label: string; icon: string }> = {
  pending: { label: '等待审批', icon: '⚠' },
  approved: { label: '已批准', icon: '✓' },
  rejected: { label: '已拒绝', icon: '✕' },
  timeout: { label: '已超时（按拒绝处理）', icon: '✕' },
  cancelled: { label: '已取消', icon: '○' },
}

const statusMeta = computed(() => STATUS_META[props.approval.status] ?? STATUS_META.cancelled!)

const typeLabel = computed(() => {
  if (props.approval.toolType === 'builtin') return '内置工具'
  if (props.approval.toolType === 'mcp') return 'MCP'
  if (props.approval.toolType === 'skill') return 'Skill'
  return '工具'
})

const args = computed(() => argumentEntries(props.approval.arguments))

/** 按钮只在等待决议且没有请求在途时可点；终态整卡只读。 */
const interactive = computed(() => props.approval.status === 'pending' && !props.approval.deciding)

function decide(approved: boolean): void {
  if (!interactive.value) return
  emit('decide', approved)
}
</script>

<template>
  <div class="approval-card" :class="`is-${approval.status}`">
    <div class="approval-head">
      <span class="approval-icon" :aria-label="statusMeta.label">{{ statusMeta.icon }}</span>
      <span class="approval-title">
        <span class="approval-label">执行审批</span>
        <span class="approval-tool">{{ approval.displayName || approval.toolName }}</span>
        <span class="approval-type">{{ typeLabel }}</span>
      </span>
      <span class="approval-state">{{ statusMeta.label }}</span>
    </div>
    <p class="approval-hint">
      Agent 请求执行这个操作，会影响真实文件与终端。批准后继续执行；拒绝会把结果告知模型并调整方案。
      超时未处理将按拒绝处理。
    </p>
    <div v-if="args.length" class="approval-args">
      <div v-for="arg in args" :key="arg.key" class="arg-row">
        <span class="arg-key">{{ arg.key }}</span>
        <span class="arg-value">{{ arg.value }}</span>
      </div>
    </div>
    <p v-else class="approval-no-args">本次调用没有参数</p>
    <div v-if="approval.status === 'pending'" class="approval-actions">
      <button class="btn-approve" type="button" :disabled="!interactive" @click="decide(true)">
        {{ approval.deciding ? '提交中…' : '批准' }}
      </button>
      <button class="btn-reject" type="button" :disabled="!interactive" @click="decide(false)">
        {{ approval.deciding ? '提交中…' : '拒绝' }}
      </button>
    </div>
    <p v-if="approval.error" class="approval-error">{{ approval.error }}</p>
  </div>
</template>

<style scoped>
.approval-card {
  margin: 4px 0;
  padding: 8px 10px;
  border: 1px solid var(--warning);
  border-radius: 8px;
  background: var(--bg-hover);
  font-size: 11px;
  text-align: left;
}

.approval-card.is-approved {
  border-color: var(--success);
}

.approval-card.is-rejected,
.approval-card.is-timeout {
  border-color: var(--danger);
}

.approval-card.is-cancelled {
  border-color: var(--border);
}

.approval-head {
  display: flex;
  align-items: center;
  gap: 7px;
}

.approval-icon {
  color: var(--warning);
  font-size: 13px;
}

.is-approved .approval-icon {
  color: var(--success);
}

.is-rejected .approval-icon,
.is-timeout .approval-icon {
  color: var(--danger);
}

.is-cancelled .approval-icon {
  color: var(--text-tertiary);
}

.approval-title {
  display: flex;
  flex: 1;
  min-width: 0;
  align-items: center;
  gap: 6px;
  overflow: hidden;
}

.approval-label {
  flex: none;
  padding: 1px 6px;
  border-radius: 5px;
  background: var(--accent-dim);
  color: var(--accent);
  font-size: 10.5px;
  font-weight: 600;
}

.approval-tool {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-primary);
  font-size: 12px;
  font-weight: 600;
}

.approval-type {
  flex: none;
  color: var(--text-tertiary);
  font-size: 10.5px;
}

.approval-state {
  flex: none;
  color: var(--text-secondary);
  font-size: 10.5px;
  font-weight: 600;
}

.approval-hint {
  margin-top: 5px;
  color: var(--text-tertiary);
  font-size: 10.5px;
  line-height: 1.5;
}

.approval-args {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin-top: 6px;
  padding: 6px 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg-surface);
}

.arg-row {
  display: flex;
  gap: 8px;
  min-width: 0;
}

.arg-key {
  flex: none;
  color: var(--text-secondary);
  font-family: var(--font-mono);
  font-size: 11px;
}

.arg-value {
  flex: 1;
  min-width: 0;
  overflow-wrap: break-word;
  color: var(--text-primary);
  font-family: var(--font-mono);
  font-size: 11px;
  white-space: pre-wrap;
}

.approval-no-args {
  margin-top: 6px;
  color: var(--text-tertiary);
  font-size: 10.5px;
}

.approval-actions {
  display: flex;
  gap: 8px;
  margin-top: 8px;
}

.btn-approve,
.btn-reject {
  height: 26px;
  padding: 0 14px;
  border: 1px solid transparent;
  border-radius: 7px;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
}

.btn-approve {
  background: var(--accent);
  color: var(--text-inverse);
}

.btn-approve:hover:not(:disabled) {
  background: var(--accent-hover);
}

.btn-reject {
  border-color: var(--border);
  background: var(--bg-surface);
  color: var(--text-secondary);
}

.btn-reject:hover:not(:disabled) {
  border-color: var(--danger);
  color: var(--danger);
}

.btn-approve:disabled,
.btn-reject:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.approval-error {
  margin-top: 6px;
  color: var(--danger);
  font-size: 10.5px;
}
</style>
