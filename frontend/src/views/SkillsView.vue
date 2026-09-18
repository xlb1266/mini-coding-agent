<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import PageHeader from '../components/PageHeader.vue'
import AppDialog from '../components/AppDialog.vue'
import type { SkillItem } from '../api/skills'
import { useSkillsStore } from '../stores/skills'
import { formatDateTime } from './model-form'

const store = useSkillsStore()
const router = useRouter()

interface Banner {
  ok: boolean
  title: string
  detail?: string
}
const banner = ref<Banner | null>(null)

// ---- 导入 ----
const importOpen = ref(false)
const importFile = ref<File | null>(null)
const importError = ref('')
const importTargetName = ref('')

// ---- 删除 ----
const deleteTarget = ref<SkillItem | null>(null)
const deleting = ref(false)
const deleteError = ref('')

onMounted(() => {
  void store.fetchSkills()
})

function openImport(): void {
  importFile.value = null
  importError.value = ''
  importOpen.value = true
}

function onFileChange(event: Event): void {
  const input = event.target as HTMLInputElement
  importFile.value = input.files?.[0] ?? null
  importError.value = ''
}

async function confirmImport(): Promise<void> {
  if (importFile.value === null || store.importing) return
  importError.value = ''
  try {
    const created = await store.importSkill(importFile.value)
    importTargetName.value = created.name
    importOpen.value = false
    banner.value = {
      ok: true,
      title: `已导入 Skill「${created.name}」`,
      detail: `保存位置：workspace/skills/${created.dirName}`,
    }
  } catch (error) {
    importError.value = error instanceof Error ? error.message : '导入失败，请重试'
  }
}

function toggleEnabled(skill: SkillItem): void {
  banner.value = null
  store
    .setEnabled(skill.dirName, !skill.enabled)
    .catch((error: unknown) => {
      banner.value = {
        ok: false,
        title: skill.enabled ? '停用失败' : '启用失败',
        detail: error instanceof Error ? error.message : '请稍后重试',
      }
    })
}

function openDelete(skill: SkillItem): void {
  deleteTarget.value = skill
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
    await store.removeSkill(target.dirName)
    closeDelete()
  } catch (error) {
    deleteError.value = error instanceof Error ? error.message : '删除失败，请重试'
  } finally {
    deleting.value = false
  }
}

function openEdit(skill: SkillItem): void {
  void router.push(`/skills/${encodeURIComponent(skill.dirName)}/edit`)
}
</script>

<template>
  <PageHeader
    title="Skills 管理"
    description="管理供 Agent 阅读的任务说明。Skill 保存在 workspace/skills 目录下，每个 Skill 一个目录，本身不执行代码。"
  />

  <div v-if="banner" class="banner" :class="banner.ok ? 'is-ok' : 'is-fail'">
    <div class="banner-text">
      <strong>{{ banner.title }}</strong>
      <p v-if="banner.detail">{{ banner.detail }}</p>
    </div>
    <button class="banner-close" type="button" aria-label="关闭" @click="banner = null">×</button>
  </div>

  <template v-if="store.loaded && store.skills.length === 0">
    <section class="card empty-state">
      <p class="empty-title">还没有 Skill</p>
      <p class="empty-text">
        导入包含 SKILL.md 的 ZIP 压缩包，或在 workspace/skills
        目录下手动创建后点击刷新，即可在此管理。
      </p>
      <button class="btn-primary" type="button" @click="openImport">导入 Skill</button>
    </section>
  </template>

  <template v-else>
    <div class="toolbar">
      <span class="toolbar-meta">共 {{ store.skills.length }} 个 Skill</span>
      <div class="toolbar-actions">
        <button class="btn-ghost" type="button" :disabled="store.refreshing" @click="store.refreshSkills()">
          {{ store.refreshing ? '刷新中…' : '刷新' }}
        </button>
        <button class="btn-primary" type="button" @click="openImport">导入 Skill</button>
      </div>
    </div>

    <section class="card table-card">
      <table class="skills-table">
        <thead>
          <tr>
            <th>名称</th>
            <th>简短说明</th>
            <th>状态</th>
            <th>更新时间</th>
            <th class="actions">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="skill in store.skills" :key="skill.dirName">
            <td>
              <div class="name-cell">
                <span class="skill-name">{{ skill.name }}</span>
                <span class="skill-dir" :title="skill.dirName">{{ skill.dirName }}</span>
              </div>
            </td>
            <td><span class="desc-cell" :title="skill.description">{{ skill.description || '—' }}</span></td>
            <td>
              <span class="badge" :class="skill.enabled ? 'badge-on' : 'badge-off'">
                {{ skill.enabled ? '启用' : '停用' }}
              </span>
            </td>
            <td class="time-cell">{{ formatDateTime(skill.updatedAt) }}</td>
            <td class="actions">
              <button class="btn-mini" type="button" @click="openEdit(skill)">编辑</button>
              <button class="btn-mini" type="button" @click="toggleEnabled(skill)">
                {{ skill.enabled ? '停用' : '启用' }}
              </button>
              <button class="btn-mini danger" type="button" @click="openDelete(skill)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>

      <p v-if="store.loading && !store.loaded" class="table-loading">正在加载…</p>
    </section>
  </template>

  <AppDialog :open="importOpen" title="导入 Skill" :width="480" @close="importOpen = false">
    <div class="import-form">
      <p class="import-intro">
        选择包含 SKILL.md 的 ZIP 压缩包。SKILL.md 可以位于压缩包根目录，或位于唯一的顶层文件夹内；
        其余文件会随 Skill 一起解压保存。
      </p>
      <label class="file-pick">
        <input type="file" accept=".zip,application/zip" @change="onFileChange" />
        <span class="file-pick-btn">选择 ZIP 文件</span>
        <span class="file-name" :class="{ 'is-empty': importFile === null }">
          {{ importFile === null ? '未选择文件' : importFile.name }}
        </span>
      </label>
      <p class="import-hint">同名 Skill 不会被覆盖；导入失败时会提示具体原因。</p>
      <p v-if="importError" class="import-error">{{ importError }}</p>
    </div>
    <template #actions>
      <button class="btn-ghost" type="button" :disabled="store.importing" @click="importOpen = false">
        取消
      </button>
      <button
        class="btn-primary"
        type="button"
        :disabled="importFile === null || store.importing"
        @click="confirmImport"
      >
        {{ store.importing ? '正在导入…' : '导入' }}
      </button>
    </template>
  </AppDialog>

  <AppDialog :open="deleteTarget !== null" title="删除 Skill" :width="440" @close="closeDelete">
    <template v-if="deleteTarget">
      <p class="delete-text">
        确定要删除「{{ deleteTarget.name }}」吗？将删除 workspace/skills/{{ deleteTarget.dirName }}
        目录及其全部文件，删除后无法恢复。
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

.toolbar-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.table-card {
  overflow: hidden;
}

.skills-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12.5px;
}

.skills-table th {
  padding: 9px 12px;
  border-bottom: 1px solid var(--border);
  background: var(--bg-surface);
  color: var(--text-tertiary);
  font-size: 11px;
  font-weight: 600;
  text-align: left;
  white-space: nowrap;
}

.skills-table td {
  padding: 10px 12px;
  border-bottom: 1px solid var(--border);
  color: var(--text-primary);
  vertical-align: middle;
}

.skills-table tbody tr:last-child td {
  border-bottom: none;
}

.skills-table tbody tr:hover td {
  background: var(--bg-hover);
}

.name-cell {
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.skill-name {
  font-weight: 600;
}

.skill-dir {
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--font-mono);
  font-size: 10.5px;
  color: var(--text-tertiary);
}

.desc-cell {
  display: inline-block;
  max-width: 360px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: bottom;
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

.skills-table td.actions,
.skills-table th.actions {
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

.banner {
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
  border: none;
  background: transparent;
  color: var(--text-tertiary);
  font-size: 14px;
  line-height: 1;
  cursor: pointer;
  padding: 2px;
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
  max-width: 400px;
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

.import-form {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.import-intro {
  font-size: 11.5px;
  color: var(--text-tertiary);
}

.file-pick {
  display: flex;
  align-items: center;
  gap: 10px;
}

.file-pick input[type='file'] {
  display: none;
}

.file-pick-btn {
  display: inline-flex;
  align-items: center;
  height: 30px;
  padding: 0 14px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-surface);
  color: var(--text-primary);
  font-size: 12px;
  cursor: pointer;
  white-space: nowrap;
}

.file-pick:hover .file-pick-btn {
  border-color: var(--border-strong);
  background: var(--bg-hover);
}

.file-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  color: var(--text-primary);
}

.file-name.is-empty {
  color: var(--text-tertiary);
}

.import-hint {
  font-size: 10.5px;
  color: var(--text-tertiary);
}

.import-error {
  padding: 8px 10px;
  border: 1px solid var(--danger);
  border-radius: 8px;
  background: var(--accent-dim);
  font-size: 11.5px;
  color: var(--danger);
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
</style>
