<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import PageHeader from '../components/PageHeader.vue'
import type { ToolItem } from '../api/tools'
import { useToolsStore } from '../stores/tools'
import { formatDateTime } from './model-form'

const store = useToolsStore()
const router = useRouter()

interface Banner {
  ok: boolean
  title: string
  detail?: string
}
const banner = ref<Banner | null>(null)

onMounted(() => {
  void store.fetchTools()
})

function openDetail(tool: ToolItem): void {
  void router.push(`/tools/${tool.id}`)
}

function toggleEnabled(tool: ToolItem): void {
  banner.value = null
  store
    .setEnabled(tool.id, !tool.enabled)
    .catch((error: unknown) => {
      banner.value = {
        ok: false,
        title: tool.enabled ? '停用失败' : '启用失败',
        detail: error instanceof Error ? error.message : '请稍后重试',
      }
    })
}

function paramSummary(tool: ToolItem): string {
  return tool.parameters.map((param) => (param.required ? param.name : `${param.name}（可选）`)).join('、')
}

const enabledCount = computed(() => store.tools.filter((tool) => tool.enabled).length)
</script>

<template>
  <PageHeader
    title="工具管理"
    description="管理 Agent 可以调用的本地工具。已停用的工具不会被执行；系统内置工具允许启停，不允许删除或修改用途。"
  />

  <div v-if="banner" class="banner" :class="banner.ok ? 'is-ok' : 'is-fail'">
    <div class="banner-text">
      <strong>{{ banner.title }}</strong>
      <p v-if="banner.detail">{{ banner.detail }}</p>
    </div>
    <button class="banner-close" type="button" aria-label="关闭" @click="banner = null">×</button>
  </div>

  <template v-if="store.loaded && store.tools.length === 0">
    <section class="card empty-state">
      <p class="empty-title">还没有注册任何工具</p>
      <p class="empty-text">
        内置工具随数据库迁移自动写入；若列表为空，请在 backend
        目录执行 uv run alembic upgrade head 后刷新本页。
      </p>
    </section>
  </template>

  <template v-else>
    <div class="toolbar">
      <span class="toolbar-meta">
        共 {{ store.tools.length }} 个工具（内置 {{ store.builtinCount }} 个），已启用 {{ enabledCount }} 个
      </span>
      <button class="btn-ghost" type="button" :disabled="store.loading" @click="store.fetchTools()">
        刷新
      </button>
    </div>

    <section class="card table-card">
      <table class="tools-table">
        <thead>
          <tr>
            <th>名称</th>
            <th>用途说明</th>
            <th>参数</th>
            <th>状态</th>
            <th>更新时间</th>
            <th class="actions">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="tool in store.tools" :key="tool.id">
            <td>
              <div class="name-cell">
                <div class="name-row">
                  <span class="tool-name">{{ tool.name }}</span>
                  <span v-if="tool.isBuiltin" class="badge badge-builtin">内置</span>
                </div>
                <span class="tool-key" :title="tool.key">{{ tool.key }}</span>
              </div>
            </td>
            <td><span class="desc-cell" :title="tool.description">{{ tool.description }}</span></td>
            <td><span class="param-cell" :title="paramSummary(tool)">{{ paramSummary(tool) || '—' }}</span></td>
            <td>
              <span class="badge" :class="tool.enabled ? 'badge-on' : 'badge-off'">
                {{ tool.enabled ? '启用' : '停用' }}
              </span>
            </td>
            <td class="time-cell">{{ formatDateTime(tool.updatedAt) }}</td>
            <td class="actions">
              <button class="btn-mini" type="button" @click="openDetail(tool)">详情</button>
              <button class="btn-mini" type="button" @click="toggleEnabled(tool)">
                {{ tool.enabled ? '停用' : '启用' }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>

      <p v-if="store.loading && !store.loaded" class="table-loading">正在加载…</p>
    </section>
  </template>
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

.tools-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12.5px;
}

.tools-table th {
  padding: 9px 12px;
  border-bottom: 1px solid var(--border);
  background: var(--bg-surface);
  color: var(--text-tertiary);
  font-size: 11px;
  font-weight: 600;
  text-align: left;
  white-space: nowrap;
}

.tools-table td {
  padding: 10px 12px;
  border-bottom: 1px solid var(--border);
  color: var(--text-primary);
  vertical-align: middle;
}

.tools-table tbody tr:last-child td {
  border-bottom: none;
}

.tools-table tbody tr:hover td {
  background: var(--bg-hover);
}

.name-cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.name-row {
  display: flex;
  align-items: center;
  gap: 6px;
}

.tool-name {
  font-weight: 600;
}

.tool-key {
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--font-mono);
  font-size: 10.5px;
  color: var(--text-tertiary);
}

.desc-cell {
  display: -webkit-box;
  max-width: 320px;
  overflow: hidden;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  color: var(--text-secondary);
}

.param-cell {
  display: inline-block;
  max-width: 160px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: bottom;
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-secondary);
}

.badge {
  display: inline-block;
  padding: 1px 8px;
  border-radius: 5px;
  font-size: 10.5px;
  font-weight: 600;
  white-space: nowrap;
}

.badge-builtin {
  background: var(--accent-dim);
  color: var(--accent);
}

.badge-on {
  background: var(--accent-dim);
  color: var(--success);
}

.badge-off {
  background: var(--bg-hover);
  color: var(--text-tertiary);
}

.time-cell {
  color: var(--text-secondary);
  font-size: 12px;
  white-space: nowrap;
}

.tools-table td.actions,
.tools-table th.actions {
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

.banner {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 12px;
  padding: 10px 14px;
  border: 1px solid;
  border-radius: 10px;
  font-size: 12px;
}

.banner.is-ok {
  border-color: var(--success);
  background: var(--bg-surface);
  color: var(--success);
}

.banner.is-fail {
  border-color: var(--danger);
  background: var(--bg-surface);
  color: var(--danger);
}

.banner-text strong {
  font-weight: 650;
}

.banner-text p {
  margin-top: 3px;
  color: var(--text-secondary);
  font-size: 11.5px;
}

.banner-close {
  padding: 2px;
  border: none;
  background: transparent;
  color: var(--text-tertiary);
  font-size: 14px;
  line-height: 1;
  cursor: pointer;
}

.banner-close:hover {
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
  max-width: 420px;
  color: var(--text-secondary);
  font-size: 12px;
}

.table-loading {
  padding: 16px 12px;
  color: var(--text-tertiary);
  font-size: 12px;
}
</style>
