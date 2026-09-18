<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import PageHeader from '../components/PageHeader.vue'
import AppDialog from '../components/AppDialog.vue'
import AgentFormDialog from '../components/AgentFormDialog.vue'
import type { AgentItem } from '../api/agents'
import { useAgentsStore } from '../stores/agents'
import { formatDateTime } from './model-form'

const store = useAgentsStore()

const formOpen = ref(false)
const editingAgent = ref<AgentItem | null>(null)

const deleteTarget = ref<AgentItem | null>(null)
const newDefaultChoice = ref<number | null>(null)
const deleting = ref(false)
const deleteError = ref('')

const notice = ref<{ title: string; detail?: string } | null>(null)

/** 删除默认 Agent 且还有其他 Agent 时，必须先选择新的默认 Agent。 */
const needNewDefault = computed(
  () => deleteTarget.value !== null && deleteTarget.value.isDefault && store.agents.length > 1,
)
const otherAgents = computed(() =>
  deleteTarget.value === null ? [] : store.agents.filter((agent) => agent.id !== deleteTarget.value!.id),
)

onMounted(() => {
  void store.fetchAgents()
})

function openCreate(): void {
  editingAgent.value = null
  formOpen.value = true
}

function openEdit(agent: AgentItem): void {
  editingAgent.value = agent
  formOpen.value = true
}

function openDelete(agent: AgentItem): void {
  deleteTarget.value = agent
  newDefaultChoice.value = null
  deleteError.value = ''
  deleting.value = false
}

function closeDelete(): void {
  deleteTarget.value = null
  newDefaultChoice.value = null
  deleteError.value = ''
}

async function confirmDelete(): Promise<void> {
  const target = deleteTarget.value
  if (target === null || deleting.value) return
  if (needNewDefault.value && newDefaultChoice.value === null) return

  deleting.value = true
  deleteError.value = ''
  try {
    await store.removeAgent(target.id, needNewDefault.value ? newDefaultChoice.value! : undefined)
    closeDelete()
  } catch (error) {
    deleteError.value = error instanceof Error ? error.message : '删除失败，请重试'
  } finally {
    deleting.value = false
  }
}

async function chooseDefault(agent: AgentItem): Promise<void> {
  notice.value = null
  try {
    await store.setDefault(agent.id)
  } catch (error) {
    notice.value = {
      title: '设置默认 Agent 失败',
      detail: error instanceof Error ? error.message : '请稍后重试',
    }
  }
}
</script>

<template>
  <PageHeader title="Agent 管理" description="组合模型、系统提示词、工具、Skills 与 MCP Server，配置面向具体任务的 Agent。" />

  <div v-if="notice" class="notice-banner">
    <div class="notice-text">
      <strong>{{ notice.title }}</strong>
      <p v-if="notice.detail">{{ notice.detail }}</p>
    </div>
    <button class="notice-close" type="button" aria-label="关闭" @click="notice = null">×</button>
  </div>

  <template v-if="store.loaded && store.agents.length === 0">
    <section class="card empty-state">
      <p class="empty-title">还没有创建 Agent</p>
      <p class="empty-text">
        组合模型、系统提示词、工具与 Skills，创建第一个面向具体任务的 Agent。
      </p>
      <button class="btn-primary" type="button" @click="openCreate">新建 Agent</button>
    </section>
  </template>

  <template v-else>
    <div class="toolbar">
      <span class="toolbar-meta" :title="`共 ${store.agents.length} 个 Agent`">
        共 {{ store.agents.length }} 个 Agent
      </span>
      <button class="btn-primary" type="button" @click="openCreate">新建 Agent</button>
    </div>

    <section class="card table-card">
      <table class="agents-table">
        <thead>
          <tr>
            <th>名称</th>
            <th>说明</th>
            <th>模型</th>
            <th class="num">工具</th>
            <th class="num">Skills</th>
            <th class="num">MCP Server</th>
            <th>最后更新</th>
            <th class="actions">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="agent in store.agents" :key="agent.id">
            <td>
              <div class="name-cell">
                <span class="agent-name">{{ agent.name }}</span>
                <span v-if="agent.isDefault" class="badge-default">默认</span>
              </div>
            </td>
            <td>
              <span class="desc-cell" :title="agent.description">{{ agent.description || '—' }}</span>
            </td>
            <td>
              <span v-if="agent.model" class="model-cell" :title="agent.model.modelIdentifier">
                {{ agent.model.displayName }}
                <span class="model-identifier">{{ agent.model.modelIdentifier }}</span>
              </span>
              <span v-else class="missing-model">模型缺失</span>
            </td>
            <td class="num" :class="{ zero: agent.toolCount === 0 }">{{ agent.toolCount }}</td>
            <td class="num" :class="{ zero: agent.skillCount === 0 }">{{ agent.skillCount }}</td>
            <td class="num" :class="{ zero: agent.mcpCount === 0 }">{{ agent.mcpCount }}</td>
            <td class="time-cell">{{ formatDateTime(agent.updatedAt) }}</td>
            <td class="actions">
              <button
                v-if="!agent.isDefault"
                class="btn-mini"
                type="button"
                @click="chooseDefault(agent)"
              >
                设为默认
              </button>
              <button class="btn-mini" type="button" @click="openEdit(agent)">编辑</button>
              <button class="btn-mini danger" type="button" @click="openDelete(agent)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>

      <p v-if="store.loading && !store.loaded" class="table-loading">正在加载…</p>
    </section>
  </template>

  <AgentFormDialog :open="formOpen" :editing="editingAgent" @close="formOpen = false" />

  <AppDialog
    :open="deleteTarget !== null"
    :title="needNewDefault ? '删除默认 Agent' : '删除 Agent'"
    :width="440"
    @close="closeDelete"
  >
    <template v-if="deleteTarget">
      <p class="delete-text">
        确定要删除「{{ deleteTarget.name }}」吗？Agent 的能力绑定会一并清除，删除后无法恢复。
      </p>
      <div v-if="needNewDefault" class="delete-choice">
        <p class="delete-choice-label">该 Agent 当前是默认 Agent，请选择新的默认 Agent：</p>
        <select v-model="newDefaultChoice">
          <option :value="null" disabled>请选择 Agent…</option>
          <option v-for="agent in otherAgents" :key="agent.id" :value="agent.id">
            {{ agent.name }}
          </option>
        </select>
      </div>
      <p v-if="deleteError" class="delete-error">{{ deleteError }}</p>
    </template>
    <template #actions>
      <button class="btn-ghost" type="button" :disabled="deleting" @click="closeDelete">取消</button>
      <button
        class="btn-danger"
        type="button"
        :disabled="deleting || (needNewDefault && newDefaultChoice === null)"
        @click="confirmDelete"
      >
        {{ deleting ? '正在删除…' : '确认删除' }}
      </button>
    </template>
  </AppDialog>
</template>

<style scoped>
.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}

.toolbar-meta {
  font-size: 11.5px;
  color: var(--text-tertiary);
}

.table-card {
  overflow: hidden;
}

.agents-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12.5px;
}

.agents-table th {
  padding: 9px 12px;
  border-bottom: 1px solid var(--border);
  background: var(--bg-surface);
  color: var(--text-tertiary);
  font-size: 11px;
  font-weight: 600;
  text-align: left;
  white-space: nowrap;
}

.agents-table th.num,
.agents-table td.num {
  text-align: right;
}

.agents-table td {
  padding: 10px 12px;
  border-bottom: 1px solid var(--border);
  color: var(--text-primary);
  vertical-align: middle;
}

.agents-table tbody tr:last-child td {
  border-bottom: none;
}

.agents-table tbody tr:hover td {
  background: var(--bg-hover);
}

.name-cell {
  display: flex;
  align-items: center;
  gap: 6px;
}

.agent-name {
  font-weight: 600;
}

.badge-default {
  padding: 1px 6px;
  border-radius: 5px;
  background: var(--accent-dim);
  color: var(--accent);
  font-size: 10.5px;
  font-weight: 600;
  white-space: nowrap;
}

.desc-cell {
  display: inline-block;
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: bottom;
  color: var(--text-secondary);
}

.model-cell {
  display: inline-flex;
  align-items: baseline;
  gap: 5px;
  max-width: 200px;
}

.model-cell .model-identifier {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-tertiary);
}

.missing-model {
  color: var(--danger);
  font-size: 12px;
}

.time-cell {
  color: var(--text-secondary);
  font-size: 12px;
  white-space: nowrap;
}

.num.zero {
  color: var(--text-tertiary);
}

.agents-table td.actions,
.agents-table th.actions {
  text-align: right;
  white-space: nowrap;
}

.btn-mini {
  height: 24px;
  padding: 0 9px;
  margin-left: 6px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg-surface);
  color: var(--text-secondary);
  font-size: 11.5px;
  cursor: pointer;
}

.btn-mini:first-child {
  margin-left: 0;
}

.btn-mini:hover:not(:disabled) {
  border-color: var(--border-strong);
  background: var(--bg-hover);
  color: var(--text-primary);
}

.btn-mini:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.btn-mini.danger {
  color: var(--danger);
}

.btn-mini.danger:hover:not(:disabled) {
  border-color: var(--danger);
  background: var(--bg-surface);
  color: var(--danger);
}

.btn-ghost {
  display: inline-flex;
  align-items: center;
  height: 30px;
  padding: 0 14px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-surface);
  color: var(--text-secondary);
  font-size: 12px;
  cursor: pointer;
}

.btn-ghost:hover:not(:disabled) {
  background: var(--bg-hover);
  color: var(--text-primary);
}

.btn-ghost:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.btn-danger {
  display: inline-flex;
  align-items: center;
  height: 30px;
  padding: 0 14px;
  border: none;
  border-radius: 8px;
  background: var(--danger);
  color: var(--text-inverse);
  font-size: 12px;
  cursor: pointer;
}

.btn-danger:hover:not(:disabled) {
  opacity: 0.9;
}

.btn-danger:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.notice-banner {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 12px;
  padding: 10px 14px;
  border-radius: 10px;
  border: 1px solid var(--danger);
  background: var(--bg-surface);
  color: var(--danger);
  font-size: 12px;
}

.notice-text strong {
  font-weight: 650;
}

.notice-text p {
  margin-top: 3px;
  color: var(--text-secondary);
  font-size: 11.5px;
}

.notice-close {
  border: none;
  background: transparent;
  color: var(--text-tertiary);
  font-size: 14px;
  line-height: 1;
  cursor: pointer;
  padding: 2px;
}

.notice-close:hover {
  color: var(--text-primary);
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

.table-loading {
  padding: 16px 12px;
  color: var(--text-tertiary);
  font-size: 12px;
}

.delete-text {
  color: var(--text-primary);
  font-size: 12.5px;
}

.delete-choice {
  margin-top: 12px;
}

.delete-choice-label {
  margin-bottom: 6px;
  color: var(--text-secondary);
  font-size: 12px;
}

.delete-choice select {
  width: 100%;
  height: 30px;
  padding: 0 8px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-surface);
  font-size: 12.5px;
}

.delete-error {
  margin-top: 10px;
  color: var(--danger);
  font-size: 11.5px;
}
</style>
