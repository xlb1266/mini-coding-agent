<script setup lang="ts">
/** 聊天输入框：正文输入 + 底部工具栏（执行档位 / 上下文用量 / 模型切换）。
 *
 * 布局对齐 ZCode：外层一个圆角容器，上方是正文输入，下方一行工具栏——
 * 左侧执行档位切换菜单，右侧上下文容量仪表、模型切换菜单与发送按钮。
 * 档位与模型是会话级覆盖：切换只影响当前会话，生成期间禁止切换；
 * 上下文用量为估算口径（不含下次输入与未连接的 MCP 工具定义）。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { MODE_PRESETS, type ContextUsage, type ModePreset } from '../api/chat'
import type { ModelItem } from '../api/models'

const props = defineProps<{
  /** 无 Agent 或会话未选择 Agent：输入框禁用 */
  disabled: boolean
  generating: boolean
  stopPending: boolean
  /** 会话与 Agent 已就绪（不含"有输入内容"这一条） */
  sessionReady: boolean
  /** 当前生效档位组合对应的预设；null 表示沿用 Agent 的自定义组合 */
  preset: ModePreset | null
  /** 生效模型的展示名（会话覆盖优先，其次 Agent 绑定） */
  modelLabel: string
  /** 生效模型是否真实存在（被删除时提示） */
  modelReady: boolean
  activeModelId: number | null
  models: ModelItem[]
  usage: ContextUsage | null
}>()

const emit = defineEmits<{
  send: [content: string]
  stop: []
  changeMode: [preset: ModePreset]
  changeModel: [modelId: number]
}>()

const router = useRouter()

const draft = ref('')
const rootEl = ref<HTMLElement | null>(null)

const canSend = computed(
  () => props.sessionReady && !props.generating && !props.disabled && draft.value.trim().length > 0,
)

function handleKeydown(event: KeyboardEvent): void {
  if (event.key !== 'Enter' || event.shiftKey) return
  // 中文输入法选词/组字状态的回车不触发发送
  if (event.isComposing || event.keyCode === 229) return
  event.preventDefault()
  if (canSend.value) emit('send', draft.value)
}

function handleSend(): void {
  if (canSend.value) emit('send', draft.value)
}

/** 发送成功后由父组件调用：清空输入框（失败时保留内容便于修改重发）。 */
function clearDraft(): void {
  draft.value = ''
}

defineExpose({ clearDraft })

// ---- 弹出菜单（档位 / 用量 / 模型）：同一时间只开一个 ----

type MenuName = 'mode' | 'usage' | 'model'
const openMenu = ref<MenuName | ''>('')

function toggleMenu(name: MenuName): void {
  openMenu.value = openMenu.value === name ? '' : name
}

function handleDocumentPointerdown(event: PointerEvent): void {
  if (!openMenu.value) return
  if (rootEl.value && event.target instanceof Node && rootEl.value.contains(event.target)) return
  openMenu.value = ''
}

function handleKeyup(event: KeyboardEvent): void {
  if (event.key === 'Escape') openMenu.value = ''
}

onMounted(() => {
  document.addEventListener('pointerdown', handleDocumentPointerdown)
  document.addEventListener('keyup', handleKeyup)
})

onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', handleDocumentPointerdown)
  document.removeEventListener('keyup', handleKeyup)
})

// ---- 执行档位 ----

const governanceLocked = computed(() => props.generating)

function choosePreset(preset: ModePreset): void {
  openMenu.value = ''
  if (governanceLocked.value) return
  emit('changeMode', preset)
}

// ---- 上下文用量 ----

/** token 数展示：过万以"万"为单位（32768 → 3.3万），与万级上下文长度可读对齐。 */
function formatTokens(tokens: number): string {
  if (tokens < 10000) return String(tokens)
  const wan = tokens / 10000
  const rounded = wan >= 100 ? Math.round(wan) : Math.round(wan * 10) / 10
  return `${rounded}万`
}

const usagePercent = computed(() => {
  const usage = props.usage
  if (!usage || usage.contextLength <= 0) return 0
  return Math.round((usage.estimatedTokens * 1000) / usage.contextLength) / 10
})

/** 进度环：按百分比画弧线；阈值升档变色（70% 警告、90% 危险）。 */
const RING_RADIUS = 7
const RING_CIRCUMFERENCE = 2 * Math.PI * RING_RADIUS

const ringStyle = computed(() => {
  const ratio = Math.min(1, Math.max(0, usagePercent.value / 100))
  return {
    strokeDasharray: `${RING_CIRCUMFERENCE}`,
    strokeDashoffset: `${RING_CIRCUMFERENCE * (1 - ratio)}`,
  }
})

const usageColor = computed(() => {
  if (usagePercent.value >= 90) return 'var(--danger)'
  if (usagePercent.value >= 70) return 'var(--warning)'
  return 'var(--accent)'
})

/** 分类展示：按占用从大到小排序，配色按名次固定（与条目一一对应）。 */
const usageRows = computed(() => {
  const usage = props.usage
  if (!usage) return []
  const colors = [
    'var(--accent)',
    'var(--success)',
    'var(--warning)',
    'var(--text-tertiary)',
    'var(--border-strong)',
  ]
  return [...usage.categories]
    .map((category, index) => ({ ...category, color: colors[index % colors.length]! }))
    .sort((a, b) => b.tokens - a.tokens)
})

// ---- 模型切换 ----

const modelMenuDisabled = computed(() => props.generating)

function chooseModel(model: ModelItem): void {
  openMenu.value = ''
  if (modelMenuDisabled.value || model.id === props.activeModelId) return
  emit('changeModel', model.id)
}

function openModelAdmin(): void {
  openMenu.value = ''
  void router.push('/models')
}
</script>

<template>
  <div ref="rootEl" class="composer">
    <div class="composer-box">
      <textarea
        v-model="draft"
        class="composer-input"
        rows="3"
        placeholder="输入消息，Enter 发送，Shift + Enter 换行"
        :disabled="disabled"
        @keydown="handleKeydown"
      />
      <div class="composer-toolbar">
        <div class="toolbar-side">
          <!-- 执行档位切换（会话级覆盖） -->
          <div class="menu-anchor">
            <button
              type="button"
              class="chip"
              :class="{ 'is-open': openMenu === 'mode' }"
              :disabled="generating"
              :title="preset ? preset.label : '沿用 Agent 配置的档位组合'"
              @click="toggleMenu('mode')"
            >
              <span class="chip-icon">
                <!-- 高度自主：闪电 -->
                <svg v-if="preset?.key === 'autonomous_full'" viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2 3 14h7l-1 8 10-12h-7l1-8z" /></svg>
                <!-- 变更前确认：手掌 -->
                <svg v-else-if="preset?.key === 'confirm_changes'" viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 11V6a2 2 0 0 0-4 0v5" /><path d="M14 10V4a2 2 0 0 0-4 0v2" /><path d="M10 10.5V6a2 2 0 0 0-4 0v8" /><path d="M18 8a2 2 0 1 1 4 0v6a8 8 0 0 1-8 8h-2c-2.8 0-4.5-.86-5.99-2.34l-3.6-3.6a2 2 0 0 1 2.83-2.82L7 15" /></svg>
                <!-- 阶段确认（默认）：清单 -->
                <svg v-else viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="8" y="2" width="8" height="4" rx="1" /><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2" /><path d="M9 12h6" /><path d="M9 16h6" /></svg>
              </span>
              {{ preset ? preset.label : '自定义档位' }}
              <svg class="chip-chevron" viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6" /></svg>
            </button>
            <div v-if="openMenu === 'mode'" class="menu menu-mode">
              <button
                v-for="item in MODE_PRESETS"
                :key="item.key"
                type="button"
                class="menu-item"
                @click="choosePreset(item)"
              >
                <span class="menu-item-body">
                  <span class="menu-item-title">{{ item.label }}</span>
                  <span class="menu-item-desc">{{ item.description }}</span>
                </span>
                <svg v-if="preset?.key === item.key" class="menu-check" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5" /></svg>
              </button>
            </div>
          </div>
        </div>

        <div class="toolbar-side">
          <!-- 上下文容量（估算口径，点击看明细） -->
          <div class="menu-anchor">
            <button
              type="button"
              class="gauge"
              :class="{ 'is-open': openMenu === 'usage' }"
              :title="`上下文容量 ${usagePercent}%`"
              @click="toggleMenu('usage')"
            >
              <svg class="gauge-ring" viewBox="0 0 20 20" width="16" height="16">
                <circle class="gauge-track" cx="10" cy="10" r="7" />
                <circle
                  class="gauge-value"
                  cx="10"
                  cy="10"
                  r="7"
                  :style="ringStyle"
                  :stroke="usageColor"
                />
              </svg>
            </button>
            <div v-if="openMenu === 'usage'" class="menu menu-usage">
              <div class="usage-head">
                <span class="usage-title">上下文容量</span>
                <span class="usage-total">
                  {{ usage ? `${formatTokens(usage.estimatedTokens)}/${formatTokens(usage.contextLength)}（${usagePercent}%）` : '暂无数据' }}
                </span>
              </div>
              <div class="usage-bar">
                <div class="usage-bar-fill" :style="{ width: `${Math.min(100, usagePercent)}%`, background: usageColor }" />
              </div>
              <div v-if="usage" class="usage-rows">
                <div v-for="row in usageRows" :key="row.key" class="usage-row">
                  <span class="usage-dot" :style="{ background: row.color }" />
                  <span class="usage-label">{{ row.label }}</span>
                  <span class="usage-percent">{{ row.percent }}%</span>
                </div>
              </div>
              <p class="usage-note">
                估算值（按字符数粗估，非模型实际用量），不含下次输入。<template v-if="usage?.hasMcpTools"><br />MCP 工具定义需连接后才能发现，未计入。</template>
              </p>
            </div>
          </div>

          <!-- 模型切换（会话级覆盖） -->
          <div class="menu-anchor">
            <button
              type="button"
              class="chip"
              :class="{ 'is-open': openMenu === 'model', 'is-missing': !modelReady }"
              :disabled="generating"
              :title="modelReady ? `模型：${modelLabel}` : '当前模型已被删除，请重新选择'"
              @click="toggleMenu('model')"
            >
              <span class="chip-model-label">{{ modelLabel }}</span>
              <svg class="chip-chevron" viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6" /></svg>
            </button>
            <div v-if="openMenu === 'model'" class="menu menu-model">
              <p v-if="models.length === 0" class="menu-empty">还没有可用模型，请先到模型管理添加</p>
              <button
                v-for="model in models"
                :key="model.id"
                type="button"
                class="menu-item"
                @click="chooseModel(model)"
              >
                <span class="menu-item-body">
                  <span class="menu-item-title">{{ model.displayName }}</span>
                  <span class="menu-item-desc menu-item-mono">{{ model.modelIdentifier }}</span>
                </span>
                <svg v-if="model.id === activeModelId" class="menu-check" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5" /></svg>
              </button>
              <div class="menu-footer">
                <button type="button" class="menu-footer-link" @click="openModelAdmin">管理模型</button>
              </div>
            </div>
          </div>

          <button
            v-if="!generating"
            class="send-button"
            type="button"
            :disabled="!canSend"
            title="发送（Enter）"
            @click="handleSend"
          >
            <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5" /><path d="M5 12l7-7 7 7" /></svg>
          </button>
          <button
            v-else
            class="send-button is-stop"
            type="button"
            :disabled="stopPending"
            title="停止生成"
            @click="emit('stop')"
          >
            <svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2" /></svg>
          </button>
        </div>
      </div>
    </div>
    <p class="composer-hint">Enter 发送 · Shift + Enter 换行 · 生成期间可切换会话（关闭页面会停止生成）</p>
  </div>
</template>

<style scoped>
.composer {
  padding: 10px 14px 12px;
}

.composer-box {
  position: relative;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--bg-surface);
  transition: border-color 0.15s ease;
}

.composer-box:focus-within {
  border-color: var(--accent);
}

.composer-input {
  display: block;
  width: 100%;
  min-height: 62px;
  max-height: 180px;
  padding: 10px 12px 4px;
  border: none;
  background: none;
  color: var(--text-primary);
  font-size: 12.5px;
  font-family: var(--font-body);
  line-height: 1.5;
  resize: none;
  outline: none;
}

.composer-input:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

/* ---- 底部工具栏 ---- */

.composer-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 4px 8px 8px;
}

.toolbar-side {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.menu-anchor {
  position: relative;
}

.chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 26px;
  padding: 0 8px;
  border: none;
  border-radius: 8px;
  background: none;
  color: var(--text-secondary);
  font-size: 12px;
  white-space: nowrap;
  cursor: pointer;
}

.chip:hover:not(:disabled),
.chip.is-open {
  background: var(--bg-hover);
  color: var(--text-primary);
}

.chip:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.chip.is-missing {
  color: var(--danger);
}

.chip-icon {
  display: inline-flex;
  color: var(--text-secondary);
}

.chip.is-open .chip-icon,
.chip:hover .chip-icon {
  color: var(--accent);
}

.chip-model-label {
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.chip-chevron {
  color: var(--text-tertiary);
}

/* ---- 弹出菜单 ---- */

.menu {
  position: absolute;
  bottom: calc(100% + 8px);
  z-index: 20;
  min-width: 240px;
  padding: 5px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--bg-elevated);
  box-shadow: 0 8px 24px rgba(45, 32, 23, 0.14);
}

.menu-mode {
  left: 0;
  width: 264px;
}

.menu-model {
  right: 0;
  width: 264px;
}

.menu-usage {
  right: 0;
  width: 288px;
  padding: 12px;
}

.menu-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  width: 100%;
  padding: 8px 10px;
  border: none;
  border-radius: 8px;
  background: none;
  text-align: left;
  cursor: pointer;
}

.menu-item:hover {
  background: var(--bg-hover);
}

.menu-item-body {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.menu-item-title {
  color: var(--text-primary);
  font-size: 12.5px;
  font-weight: 600;
}

.menu-item-desc {
  color: var(--text-tertiary);
  font-size: 11px;
  line-height: 1.45;
}

.menu-item-mono {
  font-family: var(--font-mono);
  font-size: 10.5px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.menu-check {
  flex-shrink: 0;
  color: var(--accent);
}

.menu-empty {
  padding: 10px 12px;
  color: var(--text-tertiary);
  font-size: 11.5px;
}

.menu-footer {
  margin-top: 4px;
  padding: 6px 4px 2px;
  border-top: 1px solid var(--border);
}

.menu-footer-link {
  border: none;
  background: none;
  padding: 4px 8px;
  border-radius: 6px;
  color: var(--text-secondary);
  font-size: 12px;
  cursor: pointer;
}

.menu-footer-link:hover {
  background: var(--bg-hover);
  color: var(--accent);
}

/* ---- 上下文容量仪表 ---- */

.gauge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border: none;
  border-radius: 8px;
  background: none;
  cursor: pointer;
}

.gauge:hover:not(:disabled),
.gauge.is-open {
  background: var(--bg-hover);
}

.gauge-ring {
  transform: rotate(-90deg);
}

.gauge-track {
  fill: none;
  stroke: var(--bg-active);
  stroke-width: 2.6;
}

.gauge-value {
  fill: none;
  stroke-width: 2.6;
  stroke-linecap: round;
  transition: stroke-dashoffset 0.3s ease;
}

/* ---- 用量明细弹窗 ---- */

.usage-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
}

.usage-title {
  color: var(--text-primary);
  font-size: 12.5px;
  font-weight: 650;
}

.usage-total {
  color: var(--text-secondary);
  font-size: 11px;
  white-space: nowrap;
}

.usage-bar {
  height: 5px;
  margin-top: 9px;
  border-radius: 999px;
  background: var(--bg-active);
  overflow: hidden;
}

.usage-bar-fill {
  height: 100%;
  border-radius: 999px;
  transition: width 0.3s ease;
}

.usage-rows {
  display: flex;
  flex-direction: column;
  gap: 7px;
  margin-top: 11px;
}

.usage-row {
  display: flex;
  align-items: center;
  gap: 7px;
}

.usage-dot {
  flex-shrink: 0;
  width: 7px;
  height: 7px;
  border-radius: 50%;
}

.usage-label {
  flex: 1;
  color: var(--text-secondary);
  font-size: 11.5px;
}

.usage-percent {
  color: var(--text-primary);
  font-family: var(--font-mono);
  font-size: 11px;
}

.usage-note {
  margin-top: 11px;
  padding-top: 9px;
  border-top: 1px solid var(--border);
  color: var(--text-tertiary);
  font-size: 10.5px;
  line-height: 1.5;
}

/* ---- 发送 / 停止按钮 ---- */

.send-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 30px;
  height: 30px;
  border: none;
  border-radius: 50%;
  background: var(--accent);
  color: var(--text-inverse);
  cursor: pointer;
}

.send-button:hover:not(:disabled) {
  background: var(--accent-hover);
}

.send-button:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.send-button.is-stop {
  background: var(--danger);
}

.composer-hint {
  margin-top: 6px;
  color: var(--text-tertiary);
  font-size: 10.5px;
}
</style>
