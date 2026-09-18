<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import PageHeader from '../components/PageHeader.vue'
import { ApiError } from '../api/request'
import type { SkillDetail } from '../api/skills'
import { useSkillsStore } from '../stores/skills'
import {
  buildSkillPayload,
  SKILL_LIMITS,
  validateSkillForm,
  type SkillFormErrors,
  type SkillFormValues,
} from './skill-form'

const store = useSkillsStore()
const route = useRoute()
const router = useRouter()

const dirName = computed(() => String(route.params.dirName ?? ''))

const loading = ref(true)
const notFound = ref(false)
const saving = ref(false)
const errors = ref<SkillFormErrors>({})
const formError = ref('')

const values = reactive<SkillFormValues>({ name: '', description: '', instructions: '' })

onMounted(async () => {
  try {
    const detail: SkillDetail = await store.loadSkill(dirName.value)
    values.name = detail.name
    values.description = detail.description
    values.instructions = detail.instructions
  } catch {
    notFound.value = true
  } finally {
    loading.value = false
  }
})

async function save(): Promise<void> {
  errors.value = validateSkillForm(values)
  if (Object.keys(errors.value).length > 0) return

  saving.value = true
  formError.value = ''
  try {
    await store.updateSkill(dirName.value, buildSkillPayload(values))
    void router.push('/skills')
  } catch (error) {
    if (error instanceof ApiError && error.fields) {
      errors.value = { ...error.fields }
    } else {
      formError.value = error instanceof Error ? error.message : '保存失败，请重试'
    }
  } finally {
    saving.value = false
  }
}

function cancel(): void {
  void router.push('/skills')
}

const instructionCount = computed(() => values.instructions.length)
</script>

<template>
  <PageHeader
    title="编辑 Skill"
    :description="`保存位置：workspace/skills/${dirName}/SKILL.md，保存后立即写入文件并更新列表。`"
  />

  <section v-if="loading" class="card state-card">
    <p class="state-text">正在加载…</p>
  </section>

  <section v-else-if="notFound" class="card state-card">
    <p class="state-text">没有找到这个 Skill，可能已被删除。</p>
    <button class="btn-primary" type="button" @click="cancel">返回列表</button>
  </section>

  <template v-else>
    <section class="card edit-card">
      <form class="skill-form" novalidate @submit.prevent="save">
        <div class="form-field">
          <label>名称 <span class="req">*</span></label>
          <input v-model="values.name" type="text" :maxlength="SKILL_LIMITS.name" placeholder="例如：网页内容总结" />
          <p class="field-hint">用于在列表中识别该 Skill；目录名保持不变。</p>
          <p v-if="errors.name" class="field-error">{{ errors.name }}</p>
        </div>

        <div class="form-field">
          <label>用途说明 <span class="req">*</span></label>
          <input
            v-model="values.description"
            type="text"
            :maxlength="SKILL_LIMITS.description"
            placeholder="一句话描述这个 Skill 处理什么任务"
          />
          <p class="field-hint">简短说明会显示在列表中，也是 Agent 判断是否加载该 Skill 的依据。</p>
          <p v-if="errors.description" class="field-error">{{ errors.description }}</p>
        </div>

        <div class="form-field">
          <label>详细指令 <span class="req">*</span></label>
          <textarea
            v-model="values.instructions"
            class="instructions-input"
            spellcheck="false"
            placeholder="用 Markdown 描述处理该类任务时需要遵守的步骤、输出格式和限制…"
          ></textarea>
          <p class="field-hint">支持 Markdown 格式，适合编写较长的任务说明。当前 {{ instructionCount }} 字。</p>
          <p v-if="errors.instructions" class="field-error">{{ errors.instructions }}</p>
        </div>

        <p v-if="formError" class="form-error">{{ formError }}</p>
      </form>

      <div class="edit-actions">
        <button class="btn-ghost" type="button" :disabled="saving" @click="cancel">取消</button>
        <button class="btn-primary" type="button" :disabled="saving" @click="save">
          {{ saving ? '保存中…' : '保存' }}
        </button>
      </div>
    </section>
  </template>
</template>

<style scoped>
.state-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  padding: 48px 20px;
}

.state-text {
  color: var(--text-secondary);
  font-size: 12.5px;
}

.edit-card {
  padding: 18px;
}

.skill-form {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.form-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.form-field label {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-primary);
}

.req {
  color: var(--danger);
}

.form-field input {
  height: 30px;
  padding: 0 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-surface);
  font-size: 12.5px;
  color: var(--text-primary);
}

.form-field input:focus,
.instructions-input:focus {
  outline: none;
  border-color: var(--accent);
}

/* 详细指令编辑区：占满卡片宽度并预留足够高度，适合编辑较长的 Markdown */
.instructions-input {
  min-height: 420px;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-surface);
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.6;
  color: var(--text-primary);
  resize: vertical;
}

.field-hint {
  font-size: 10.5px;
  color: var(--text-tertiary);
}

.field-error {
  font-size: 10.5px;
  color: var(--danger);
}

.form-error {
  padding: 8px 10px;
  border: 1px solid var(--danger);
  border-radius: 8px;
  background: var(--accent-dim);
  font-size: 11.5px;
  color: var(--danger);
}

.edit-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 16px;
  padding-top: 14px;
  border-top: 1px solid var(--border);
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
</style>
