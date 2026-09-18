<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import PageHeader from '../components/PageHeader.vue'
import AppDialog from '../components/AppDialog.vue'
import McpFormDialog from '../components/McpFormDialog.vue'
import type { McpServerItem, McpTestResult } from '../api/mcp'
import { useMcpStore } from '../stores/mcp'
import { commandPreview } from './mcp-form'
import { formatDateTime } from './model-form'

const store = useMcpStore()

const formOpen = ref(false)
const editingServer = ref<McpServerItem | null>(null)

const testingId = ref<number | null>(null)
interface TestBanner {
  ok: boolean
  title: string
  detail?: string
}
const testBanner = ref<TestBanner | null>(null)

const deleteTarget = ref<McpServerItem | null>(null)
const deleting = ref(false)
const deleteError = ref('')

const toolsServer = ref<McpServerItem | null>(null)
const toolsOpen = ref(false)

const hasAnyServer = computed(() => store.servers.length > 0)

onMounted(() => {
  void store.fetchServers()
})

function openCreate(): void {
  editingServer.value = null
  formOpen.value = true
}

function openEdit(server: McpServerItem): void {
  editingServer.value = server
  formOpen.value = true
}

function openTools(server: McpServerItem): void {
  toolsServer.value = server
  toolsOpen.value = true
}

function openDelete(server: McpServerItem): void {
  deleteTarget.value = server
  deleteError.value = ''
  deleting.value = false
}

function closeDelete(): void {
  deleteTarget.value = null
  deleteError.value = ''
}

async function confirmDelete(): Promise<void> {
  const target = deleteTarget.value
  if (target === null || deleting.value) return

  deleting.value = true
  deleteError.value = ''
  try {
    await store.removeServer(target.id)
    closeDelete()
  } catch (error) {
    deleteError.value = error instanceof Error ? error.message : '删除失败，请重试'
  } finally {
    deleting.value = false
  }
}

/** 测试连接：测试期间该行按钮显示进行状态并禁用，禁止重复提交。 */
async function runTest(server: McpServerItem): Promise<void> {
  if (testingId.value !== null) return
  testingId.value = server.id
  testBanner.value = null
  try {
    const result: McpTestResult = await store.testConnection(server.id)
    if (result.status === 'success') {
      testBanner.value = {
        ok: true,
        title:
          result.toolCount && result.toolCount > 0
            ? `「${server.name}」测试通过，发现 ${result.toolCount} 个工具`
            : `「${server.name}」连接成功，未发现工具`,
        detail:
          result.toolCount && result.toolCount > 0
            ? '点击该行「查看工具」可查看每个工具的名称、用途与参数说明。'
            : '该 Server 未提供任何工具。',
      }
    } else {
      testBanner.value = {
        ok: false,
        title: `「${server.name}」测试失败：${result.message ?? '未知错误'}`,
        detail: result.hint,
      }
    }
  } catch (error) {
    testBanner.value = {
      ok: false,
      title: `「${server.name}」测试失败`,
      detail: error instanceof Error ? error.message : '网络请求失败，请确认后端服务已启动',
    }
  } finally {
    testingId.value = null
  }
}

async function toggleEnabled(server: McpServerItem): Promise<void> {
  testBanner.value = null
  try {
    await store.setEnabled(server.id, !server.enabled)
  } catch (error) {
    testBanner.value = {
      ok: false,
      title: server.enabled ? '停用失败' : '启用失败',
      detail: error instanceof Error ? error.message : '请稍后重试',
    }
  }
}

/** 最近一次测试结果的展示文案与色调。 */
function testStatusView(server: McpServerItem): { label: string; detail: string; tone: string } {
  switch (server.lastTestStatus) {
    case 'success':
      return {
        label: '测试成功',
        detail: server.lastTestAt === null ? '' : formatDateTime(server.lastTestAt),
        tone: 'is-success',
      }
    case 'failed':
      return {
        label: '测试失败',
        detail: server.lastTestError ?? '',
        tone: 'is-failed',
      }
    case 'stale':
      return { label: '配置已变更，待重新测试', detail: '', tone: 'is-stale' }
    default:
      return { label: '未测试', detail: '', tone: 'is-untested' }
  }
}
</script>

<template>
  <PageHeader
    title="MCP 管理"
    description="管理通过本机 stdio 启动的 MCP Server；测试连接会实际启动程序并读取工具列表，只发现工具，不执行工具。"
  />

  <div v-if="testBanner" class="test-banner" :class="testBanner.ok ? 'is-ok' : 'is-fail'">
    <div class="test-banner-text">
      <strong>{{ testBanner.title }}</strong>
      <p v-if="testBanner.detail">{{ testBanner.detail }}</p>
    </div>
    <button class="test-banner-close" type="button" aria-label="关闭" @click="testBanner = null">×</button>
  </div>

  <template v-if="store.loaded && !hasAnyServer">
    <section class="card empty-state">
      <p class="empty-title">还没有添加 MCP Server</p>
      <p class="empty-text">
        添加一个本机 stdio MCP Server 并通过测试连接后，即可把它的工具提供给 Agent 使用。
        配置项通常可以在 MCP Server 的使用说明中直接找到。
      </p>
      <button class="btn-primary" type="button" @click="openCreate">新增 MCP Server</button>
    </section>
  </template>

  <template v-else>
    <div class="toolbar">
      <span class="toolbar-meta" :title="`共 ${store.servers.length} 个 MCP Server`">
        共 {{ store.servers.length }} 个 MCP Server
      </span>
      <button class="btn-primary" type="button" @click="openCreate">新增 MCP Server</button>
    </div>

    <section class="card table-card">
      <table class="mcp-table">
        <thead>
          <tr>
            <th>名称</th>
            <th>说明</th>
            <th>启动命令</th>
            <th>启用状态</th>
            <th>最近测试结果</th>
            <th class="num">工具数</th>
            <th class="actions">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="server in store.servers" :key="server.id">
            <td><span class="server-name">{{ server.name }}</span></td>
            <td>
              <span class="desc-cell" :title="server.description">
                {{ server.description || '—' }}
              </span>
            </td>
            <td>
              <span class="mono-cell" :title="commandPreview(server.command, server.args)">
                {{ commandPreview(server.command, server.args) }}
              </span>
            </td>
            <td>
              <span :class="server.enabled ? 'state-on' : 'state-off'">
                {{ server.enabled ? '已启用' : '未启用' }}
              </span>
            </td>
            <td>
              <div class="test-cell" :class="testStatusView(server).tone">
                <span class="test-label">{{ testStatusView(server).label }}</span>
                <span v-if="testStatusView(server).detail" class="test-detail" :title="testStatusView(server).detail">
                  {{ testStatusView(server).detail }}
                </span>
              </div>
            </td>
            <td class="num">
              <span v-if="server.lastTestStatus === 'success'" class="tool-count">
                {{ server.lastTestToolCount ?? 0 }}
              </span>
              <span v-else class="tool-count unknown">未知</span>
            </td>
            <td class="actions">
              <button
                class="btn-mini"
                type="button"
                :disabled="testingId !== null"
                @click="runTest(server)"
              >
                {{ testingId === server.id ? '正在测试…' : '测试连接' }}
              </button>
              <button
                v-if="server.lastTestStatus === 'success' && (server.lastTestTools?.length ?? 0) > 0"
                class="btn-mini"
                type="button"
                @click="openTools(server)"
              >
                查看工具
              </button>
              <button class="btn-mini" type="button" @click="toggleEnabled(server)">
                {{ server.enabled ? '停用' : '启用' }}
              </button>
              <button class="btn-mini" type="button" @click="openEdit(server)">编辑</button>
              <button class="btn-mini danger" type="button" @click="openDelete(server)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>

      <p v-if="store.loading && !store.loaded" class="table-loading">正在加载…</p>
    </section>
  </template>

  <McpFormDialog :open="formOpen" :editing="editingServer" @close="formOpen = false" />

  <AppDialog :open="toolsOpen" title="工具列表" :width="640" @close="toolsOpen = false">
    <template v-if="toolsServer">
      <p class="tools-meta">
        「{{ toolsServer.name }}」最近一次成功测试发现 {{ toolsServer.lastTestToolCount ?? 0 }} 个工具（仅发现，不执行）。
      </p>
      <div v-for="tool in toolsServer.lastTestTools" :key="tool.name" class="tool-card">
        <div class="tool-head">
          <span class="tool-name">{{ tool.name }}</span>
          <span v-if="tool.description" class="tool-desc">{{ tool.description }}</span>
        </div>
        <table v-if="tool.params.length > 0" class="params-table">
          <thead>
            <tr>
              <th>参数</th>
              <th>类型</th>
              <th>必填</th>
              <th>说明</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="param in tool.params" :key="param.name">
              <td class="mono">{{ param.name }}</td>
              <td class="mono">{{ param.type }}</td>
              <td>{{ param.required ? '是' : '否' }}</td>
              <td class="param-desc">{{ param.description || '—' }}</td>
            </tr>
          </tbody>
        </table>
        <p v-else class="no-params">该工具没有参数。</p>
      </div>
    </template>
    <template #actions>
      <button class="btn-ghost" type="button" @click="toolsOpen = false">关闭</button>
    </template>
  </AppDialog>

  <AppDialog :open="deleteTarget !== null" title="删除 MCP Server" :width="440" @close="closeDelete">
    <template v-if="deleteTarget">
      <p class="delete-text">
        确定要删除「{{ deleteTarget.name }}」吗？该 Server 保存的环境变量会一并清除，删除后无法恢复。
      </p>
      <p v-if="deleteError" class="delete-error">{{ deleteError }}</p>
    </template>
    <template #actions>
      <button class="btn-ghost" type="button" :disabled="deleting" @click="closeDelete">取消</button>
      <button class="btn-danger" type="button" :disabled="deleting" @click="confirmDelete">
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

.mcp-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12.5px;
}

.mcp-table th {
  padding: 9px 12px;
  border-bottom: 1px solid var(--border);
  background: var(--bg-surface);
  color: var(--text-tertiary);
  font-size: 11px;
  font-weight: 600;
  text-align: left;
  white-space: nowrap;
}

.mcp-table th.num,
.mcp-table td.num {
  text-align: right;
}

.mcp-table td {
  padding: 10px 12px;
  border-bottom: 1px solid var(--border);
  color: var(--text-primary);
  vertical-align: middle;
}

.mcp-table tbody tr:last-child td {
  border-bottom: none;
}

.mcp-table tbody tr:hover td {
  background: var(--bg-hover);
}

.server-name {
  font-weight: 600;
}

.desc-cell {
  display: inline-block;
  max-width: 150px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: bottom;
  color: var(--text-secondary);
}

.mono-cell {
  display: inline-block;
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: bottom;
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text-secondary);
}

.state-on {
  color: var(--success);
}

.state-off {
  color: var(--text-tertiary);
}

.test-cell {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 120px;
}

.test-label {
  font-size: 12px;
}

.test-detail {
  max-width: 150px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 10.5px;
  color: var(--text-tertiary);
}

.test-cell.is-success .test-label {
  color: var(--success);
}

.test-cell.is-failed .test-label {
  color: var(--danger);
}

.test-cell.is-stale .test-label {
  color: var(--warning);
}

.test-cell.is-untested .test-label {
  color: var(--text-tertiary);
}

.tool-count {
  font-size: 12px;
}

.tool-count.unknown {
  color: var(--text-tertiary);
}

.mcp-table td.actions,
.mcp-table th.actions {
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
  max-width: 420px;
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

.delete-error {
  margin-top: 10px;
  color: var(--danger);
  font-size: 11.5px;
}

.tools-meta {
  margin-bottom: 12px;
  color: var(--text-secondary);
  font-size: 11.5px;
}

.tool-card {
  padding: 10px 12px;
  margin-bottom: 8px;
  border: 1px solid var(--border);
  border-radius: 8px;
}

.tool-card:last-child {
  margin-bottom: 0;
}

.tool-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 6px;
}

.tool-name {
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 600;
  color: var(--text-primary);
}

.tool-desc {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-secondary);
  font-size: 11.5px;
}

.params-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 11.5px;
}

.params-table th {
  padding: 4px 8px;
  border-bottom: 1px solid var(--border);
  color: var(--text-tertiary);
  font-size: 10.5px;
  font-weight: 600;
  text-align: left;
  white-space: nowrap;
}

.params-table td {
  padding: 4px 8px;
  border-bottom: 1px solid var(--border);
  color: var(--text-secondary);
  vertical-align: top;
}

.params-table tbody tr:last-child td {
  border-bottom: none;
}

.params-table .mono {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-primary);
}

.param-desc {
  max-width: 260px;
}

.no-params {
  color: var(--text-tertiary);
  font-size: 11px;
}
</style>
