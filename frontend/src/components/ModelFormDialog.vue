<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'

import AppDialog from './AppDialog.vue'
import { ApiError } from '../api/request'
import type { ModelItem } from '../api/models'
import { useModelsStore } from '../stores/models'
import {
  buildModelPayload,
  emptyFormValues,
  validateModelForm,
  type ModelFormErrors,
  type ModelFormValues,
} from '../views/model-form'

const props = defineProps<{
  open: boolean
  /** null 表示新增，否则为编辑目标。 */
  editing: ModelItem | null
}>()

const emit = defineEmits<{
  close: []
  saved: []
}>()

const store = useModelsStore()

const values = reactive<ModelFormValues>(emptyFormValues())
const errors = ref<ModelFormErrors>({})
const formError = ref('')
const saving = ref(false)

const isEdit = computed(() => props.editing !== null)
/** 编辑已配置密钥的模型时，密钥输入框保持为空，留空即保留原密钥。 */
const keyConfigured = computed(() => isEdit.value && props.editing !== null && props.editing.hasApiKey)

watch(
  () => props.open,
  (open) => {
    if (!open) return
    errors.value = {}
    formError.value = ''
    saving.value = false

    const editing = props.editing
    if (editing) {
      values.displayName = editing.displayName
      values.modelIdentifier = editing.modelIdentifier
      values.baseUrl = editing.baseUrl
      values.apiKey = ''
      values.contextLength = String(editing.contextLength)
      values.maxOutputTokens = String(editing.maxOutputTokens)
      values.temperature = String(editing.temperature)
      values.inputPrice = editing.inputPrice === null ? '' : String(editing.inputPrice)
      values.outputPrice = editing.outputPrice === null ? '' : String(editing.outputPrice)
      values.cachedInputPrice = editing.cachedInputPrice === null ? '' : String(editing.cachedInputPrice)
    } else {
      Object.assign(values, emptyFormValues())
    }
  },
)

async function submit(): Promise<void> {
  errors.value = validateModelForm(values)
  if (Object.keys(errors.value).length > 0) return

  saving.value = true
  formError.value = ''
  try {
    const payload = buildModelPayload(values)
    if (props.editing) {
      await store.updateModel(props.editing.id, payload)
    } else {
      await store.createModel(payload)
    }
    emit('saved')
    emit('close')
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
</script>

<template>
  <AppDialog :open="open" :title="isEdit ? '编辑模型' : '新增模型'" :width="620" @close="emit('close')">
    <form class="model-form" novalidate @submit.prevent="submit">
      <p class="form-intro">
        仅支持兼容 OpenAI Chat Completions 接口的模型服务。带 <span class="req">*</span> 为必填项。
      </p>

      <div class="form-field">
        <label>显示名称 <span class="req">*</span></label>
        <input v-model="values.displayName" type="text" placeholder="例如：DeepSeek-V3" />
        <p class="field-hint">用于在页面中识别模型。</p>
        <p v-if="errors.displayName" class="field-error">{{ errors.displayName }}</p>
      </div>

      <div class="form-field">
        <label>模型标识 <span class="req">*</span></label>
        <input v-model="values.modelIdentifier" type="text" class="mono" placeholder="例如：deepseek-chat" />
        <p class="field-hint">请求接口时使用的模型名称，需与服务商提供的标识完全一致。</p>
        <p v-if="errors.modelIdentifier" class="field-error">{{ errors.modelIdentifier }}</p>
      </div>

      <div class="form-field">
        <label>服务地址 <span class="req">*</span></label>
        <input
          v-model="values.baseUrl"
          type="text"
          class="mono"
          placeholder="例如：https://api.deepseek.com/v1"
        />
        <p class="field-hint">
          填写 API 根地址，通常以 /v1 结尾（如 https://api.deepseek.com/v1、https://api.openai.com/v1，
          本地服务如 http://127.0.0.1:11434/v1）。是否带 /v1 以服务商文档为准，不要包含 /chat/completions。
        </p>
        <p v-if="errors.baseUrl" class="field-error">{{ errors.baseUrl }}</p>
      </div>

      <div class="form-field">
        <label>API Key</label>
        <input
          v-model="values.apiKey"
          type="password"
          autocomplete="new-password"
          class="mono"
          :placeholder="keyConfigured ? '已配置，留空则保留原密钥' : 'sk-…'"
        />
        <p v-if="keyConfigured" class="field-hint">已配置密钥。留空则保留原密钥，填写新密钥即可替换。</p>
        <p v-else class="field-hint">调用模型服务所需的密钥，保存后不再展示；本地服务无需密钥时可留空。</p>
        <p v-if="errors.apiKey" class="field-error">{{ errors.apiKey }}</p>
      </div>

      <div class="form-grid">
        <div class="form-field">
          <label>上下文长度（token） <span class="req">*</span></label>
          <input v-model="values.contextLength" type="text" inputmode="numeric" placeholder="例如：32768" />
          <p class="field-hint">32768 为建议值，以服务商说明为准。</p>
          <p v-if="errors.contextLength" class="field-error">{{ errors.contextLength }}</p>
        </div>
        <div class="form-field">
          <label>最大输出长度（token） <span class="req">*</span></label>
          <input v-model="values.maxOutputTokens" type="text" inputmode="numeric" placeholder="例如：4096" />
          <p class="field-hint">4096 为建议值，不能超过上下文长度。</p>
          <p v-if="errors.maxOutputTokens" class="field-error">{{ errors.maxOutputTokens }}</p>
        </div>
        <div class="form-field">
          <label>温度 <span class="req">*</span></label>
          <input v-model="values.temperature" type="text" inputmode="decimal" placeholder="例如：0.7" />
          <p class="field-hint">0.7 为建议值；0 更稳定，2 更随机，范围 0–2。</p>
          <p v-if="errors.temperature" class="field-error">{{ errors.temperature }}</p>
        </div>
      </div>

      <div class="form-grid">
        <div class="form-field">
          <label>输入单价（元/百万 token）</label>
          <input v-model="values.inputPrice" type="text" inputmode="decimal" placeholder="未填写" />
          <p v-if="errors.inputPrice" class="field-error">{{ errors.inputPrice }}</p>
        </div>
        <div class="form-field">
          <label>输出单价（元/百万 token）</label>
          <input v-model="values.outputPrice" type="text" inputmode="decimal" placeholder="未填写" />
          <p v-if="errors.outputPrice" class="field-error">{{ errors.outputPrice }}</p>
        </div>
        <div class="form-field">
          <label>缓存命中输入单价（元/百万 token）</label>
          <input v-model="values.cachedInputPrice" type="text" inputmode="decimal" placeholder="未填写" />
          <p v-if="errors.cachedInputPrice" class="field-error">{{ errors.cachedInputPrice }}</p>
        </div>
      </div>
      <p class="price-note">价格以服务商说明为准；留空表示未填写，填 0 表示该部分免费。</p>

      <p v-if="formError" class="form-error">{{ formError }}</p>
    </form>

    <template #actions>
      <button class="btn-ghost" type="button" :disabled="saving" @click="emit('close')">取消</button>
      <button class="btn-primary" type="button" :disabled="saving" @click="submit">
        {{ saving ? '保存中…' : '保存' }}
      </button>
    </template>
  </AppDialog>
</template>

<style scoped>
.form-intro {
  margin-bottom: 14px;
  font-size: 11.5px;
  color: var(--text-tertiary);
}

.model-form {
  display: flex;
  flex-direction: column;
  gap: 13px;
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

.form-field input.mono {
  font-family: var(--font-mono);
  font-size: 12px;
}

.form-field input:focus {
  outline: none;
  border-color: var(--accent);
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}

.field-hint {
  font-size: 10.5px;
  color: var(--text-tertiary);
}

.field-error {
  font-size: 10.5px;
  color: var(--danger);
}

.price-note {
  font-size: 10.5px;
  color: var(--text-tertiary);
}

.form-error {
  padding: 8px 10px;
  border: 1px solid var(--danger);
  border-radius: 8px;
  background: var(--accent-dim);
  font-size: 11.5px;
  color: var(--danger);
}
</style>
