<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import PageHeader from '../components/PageHeader.vue'
import AppDialog from '../components/AppDialog.vue'
import ModelFormDialog from '../components/ModelFormDialog.vue'
import type { ModelItem, TestConnectionResult } from '../api/models'
import { useModelsStore } from '../stores/models'
import { formatDateTime, formatTokenCount } from './model-form'

const store = useModelsStore()

const formOpen = ref(false)
const editingModel = ref<ModelItem | null>(null)

const testingId = ref<number | null>(null)
interface TestBanner {
  ok: boolean
  title: string
  detail?: string
}
const testBanner = ref<TestBanner | null>(null)

const deleteTarget = ref<ModelItem | null>(null)
const newDefaultChoice = ref<number | null>(null)
const deleting = ref(false)
const deleteError = ref('')

/** 删除默认模型且还有其他模型时，必须先选择新的默认模型。 */
const needNewDefault = computed(
  () => deleteTarget.value !== null && deleteTarget.value.isDefault && store.models.length > 1,
)
const otherModels = computed(() =>
  deleteTarget.value === null ? [] : store.models.filter((model) => model.id !== deleteTarget.value!.id),
)

onMounted(() => {
  void store.fetchModels()
})

function openCreate(): void {
  editingModel.value = null
  formOpen.value = true
}

function openEdit(model: ModelItem): void {
  editingModel.value = model
  formOpen.value = true
}

function openDelete(model: ModelItem): void {
  deleteTarget.value = model
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
    await store.removeModel(target.id, needNewDefault.value ? newDefaultChoice.value! : undefined)
    closeDelete()
  } catch (error) {
    deleteError.value = error instanceof Error ? error.message : '删除失败，请重试'
  } finally {
    deleting.value = false
  }
}

/** 测试连接：测试期间禁止重复提交（所有测试按钮禁用）。 */
async function runTest(model: ModelItem): Promise<void> {
  if (testingId.value !== null) return
  testingId.value = model.id
  testBanner.value = null
  try {
    const result: TestConnectionResult = await store.testConnection(model.id)
    if (result.ok) {
      testBanner.value = {
        ok: true,
        title: `「${model.displayName}」测试通过，模型已连接`,
        detail: result.reply ? `模型回复：${result.reply}` : undefined,
      }
    } else {
      testBanner.value = {
        ok: false,
        title: `「${model.displayName}」测试失败：${result.message ?? '未知原因'}`,
        detail: result.hint,
      }
    }
  } catch (error) {
    testBanner.value = {
      ok: false,
      title: `「${model.displayName}」测试失败`,
      detail: error instanceof Error ? error.message : '网络请求失败，请确认后端服务已启动',
    }
  } finally {
    testingId.value = null
  }
}

async function chooseDefault(model: ModelItem): Promise<void> {
  testBanner.value = null
  try {
    await store.setDefault(model.id)
  } catch (error) {
    testBanner.value = {
      ok: false,
      title: '设置默认模型失败',
      detail: error instanceof Error ? error.message : '请稍后重试',
    }
  }
}
</script>

<template>
  <PageHeader title="模型管理" description="接入和管理大语言模型，仅支持兼容 OpenAI Chat Completions 接口的模型服务。" />

  <div v-if="testBanner" class="test-banner" :class="testBanner.ok ? 'is-ok' : 'is-fail'">
    <div class="test-banner-text">
      <strong>{{ testBanner.title }}</strong>
      <p v-if="testBanner.detail">{{ testBanner.detail }}</p>
    </div>
    <button class="test-banner-close" type="button" aria-label="关闭" @click="testBanner = null">×</button>
  </div>

  <template v-if="store.loaded && store.models.length === 0">
    <section class="card empty-state">
      <p class="empty-title">还没有接入模型</p>
      <p class="empty-text">添加一个兼容 OpenAI Chat Completions 接口的模型服务后，即可在聊天与 Agent 中使用。</p>
      <button class="btn-primary" type="button" @click="openCreate">新增模型</button>
    </section>
  </template>

  <template v-else>
    <div class="toolbar">
      <span class="toolbar-meta" :title="`共 ${store.models.length} 个模型`">
        共 {{ store.models.length }} 个模型
      </span>
      <button class="btn-primary" type="button" @click="openCreate">新增模型</button>
    </div>

    <section class="card table-card">
      <table class="models-table">
        <thead>
          <tr>
            <th>显示名称</th>
            <th>模型标识</th>
            <th>服务地址</th>
            <th class="num">上下文长度</th>
            <th>密钥</th>
            <th>最后更新</th>
            <th class="actions">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="model in store.models" :key="model.id">
            <td>
              <div class="name-cell">
                <span class="model-name">{{ model.displayName }}</span>
                <span v-if="model.isDefault" class="badge-default">默认</span>
              </div>
            </td>
            <td><span class="mono-cell" :title="model.modelIdentifier">{{ model.modelIdentifier }}</span></td>
            <td><span class="mono-cell url" :title="model.baseUrl">{{ model.baseUrl }}</span></td>
            <td class="num">{{ formatTokenCount(model.contextLength) }}</td>
            <td>
              <span :class="model.hasApiKey ? 'key-on' : 'key-off'">
                {{ model.hasApiKey ? '已配置' : '未配置' }}
              </span>
            </td>
            <td class="time-cell">{{ formatDateTime(model.updatedAt) }}</td>
            <td class="actions">
              <button
                class="btn-mini"
                type="button"
                :disabled="testingId !== null"
                @click="runTest(model)"
              >
                {{ testingId === model.id ? '正在测试…' : '测试连接' }}
              </button>
              <button
                v-if="!model.isDefault"
                class="btn-mini"
                type="button"
                @click="chooseDefault(model)"
              >
                设为默认
              </button>
              <button class="btn-mini" type="button" @click="openEdit(model)">编辑</button>
              <button class="btn-mini danger" type="button" @click="openDelete(model)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>

      <p v-if="store.loading && !store.loaded" class="table-loading">正在加载…</p>
    </section>
  </template>

  <ModelFormDialog :open="formOpen" :editing="editingModel" @close="formOpen = false" />

  <AppDialog
    :open="deleteTarget !== null"
    :title="needNewDefault ? '删除默认模型' : '删除模型'"
    :width="440"
    @close="closeDelete"
  >
    <template v-if="deleteTarget">
      <p class="delete-text">
        确定要删除「{{ deleteTarget.displayName }}」吗？该模型保存的密钥会一并清除，删除后无法恢复。
      </p>
      <div v-if="needNewDefault" class="delete-choice">
        <p class="delete-choice-label">该模型当前是默认模型，请选择新的默认模型：</p>
        <select v-model="newDefaultChoice">
          <option :value="null" disabled>请选择模型…</option>
          <option v-for="model in otherModels" :key="model.id" :value="model.id">
            {{ model.displayName }}
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

.models-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12.5px;
}

.models-table th {
  padding: 9px 12px;
  border-bottom: 1px solid var(--border);
  background: var(--bg-surface);
  color: var(--text-tertiary);
  font-size: 11px;
  font-weight: 600;
  text-align: left;
  white-space: nowrap;
}

.models-table th.num,
.models-table td.num {
  text-align: right;
}

.models-table td {
  padding: 10px 12px;
  border-bottom: 1px solid var(--border);
  color: var(--text-primary);
  vertical-align: middle;
}

.models-table tbody tr:last-child td {
  border-bottom: none;
}

.models-table tbody tr:hover td {
  background: var(--bg-hover);
}

.name-cell {
  display: flex;
  align-items: center;
  gap: 6px;
}

.model-name {
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

.mono-cell {
  display: inline-block;
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: bottom;
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text-secondary);
}

.time-cell {
  color: var(--text-secondary);
  font-size: 12px;
  white-space: nowrap;
}

.key-on {
  color: var(--success);
}

.key-off {
  color: var(--text-tertiary);
}

.models-table td.actions,
.models-table th.actions {
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

.test-banner {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 12px;
  padding: 10px 14px;
  border-radius: 10px;
  border: 1px solid;
  font-size: 12px;
}

.test-banner.is-ok {
  border-color: var(--success);
  background: var(--bg-surface);
  color: var(--success);
}

.test-banner.is-fail {
  border-color: var(--danger);
  background: var(--bg-surface);
  color: var(--danger);
}

.test-banner-text strong {
  font-weight: 650;
}

.test-banner-text p {
  margin-top: 3px;
  color: var(--text-secondary);
  font-size: 11.5px;
}

.test-banner-close {
  border: none;
  background: transparent;
  color: var(--text-tertiary);
  font-size: 14px;
  line-height: 1;
  cursor: pointer;
  padding: 2px;
}

.test-banner-close:hover {
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
