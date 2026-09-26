<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import PageHeader from '../components/PageHeader.vue'
import ApprovalCard from '../components/ApprovalCard.vue'
import CompactionCard from '../components/CompactionCard.vue'
import MarkdownContent from '../components/MarkdownContent.vue'
import ThinkingCard from '../components/ThinkingCard.vue'
import ToolGroupCard from '../components/ToolGroupCard.vue'
import { useAgentsStore } from '../stores/agents'
import { segmentsText, useChatStore } from '../stores/chat'
import type { GenerationSegment } from '../stores/chat'
import { buildDisplayItems } from './chat-format'
import type { ChatDisplayItem } from './chat-format'
import type { ChatMessage } from '../api/chat'

const agentsStore = useAgentsStore()
const chatStore = useChatStore()
const router = useRouter()

const draft = ref('')
const actionError = ref('')
const creatingSession = ref(false)
const stopPending = ref(false)

// Agent 选择框的本地值：切换会话时同步，后端确认失败时回滚
const agentSelect = ref<number | ''>('')

const hasAgents = computed(() => agentsStore.agents.length > 0)
const session = computed(() => chatStore.activeSession)
const messages = computed<ChatMessage[]>(() =>
  session.value ? chatStore.messagesBySession[session.value.id] ?? [] : [],
)
const generation = computed(() =>
  session.value ? chatStore.generations[session.value.id] : undefined,
)
const generationError = computed(() =>
  session.value ? chatStore.generationErrors[session.value.id] : null,
)
const isGenerating = computed(() => generation.value !== undefined)
const agentMissing = computed(
  () => hasAgents.value && session.value !== null && session.value.agentId === null,
)
const canSend = computed(
  () =>
    hasAgents.value &&
    session.value !== null &&
    session.value.agentId !== null &&
    !isGenerating.value &&
    draft.value.trim().length > 0,
)

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : '操作失败，请重试'
}

// ---- 初始化与选择 ----

onMounted(async () => {
  await agentsStore.fetchAgents().catch(() => undefined)
  await chatStore.fetchSessions().catch(() => undefined)
  if (chatStore.activeSessionId === null && chatStore.sessions.length > 0) {
    await chatStore.selectSession(chatStore.sessions[0]!.id).catch(() => undefined)
  }
})

watch(
  () => session.value?.id,
  () => {
    draft.value = ''
    actionError.value = ''
  },
)

watch(
  () => session.value?.agentId,
  (agentId) => {
    agentSelect.value = agentId ?? ''
  },
  { immediate: true },
)

async function handleSelectSession(sessionId: number): Promise<void> {
  actionError.value = ''
  // 切换只是视图切换：原会话若在生成，后台继续
  await chatStore.selectSession(sessionId).catch((error) => {
    actionError.value = errorMessage(error)
  })
}

async function handleCreateSession(): Promise<void> {
  if (!hasAgents.value || creatingSession.value) return
  creatingSession.value = true
  actionError.value = ''
  try {
    await chatStore.createSession()
  } catch (error) {
    actionError.value = errorMessage(error)
  } finally {
    creatingSession.value = false
  }
}

async function handleAgentChange(): Promise<void> {
  if (agentSelect.value === '' || session.value === null) return
  actionError.value = ''
  try {
    await chatStore.changeAgent(Number(agentSelect.value))
  } catch (error) {
    agentSelect.value = session.value.agentId ?? ''
    actionError.value = errorMessage(error)
  }
}

// ---- 发送与停止 ----

function handleKeydown(event: KeyboardEvent): void {
  if (event.key !== 'Enter' || event.shiftKey) return
  // 中文输入法选词/组字状态的回车不触发发送
  if (event.isComposing || event.keyCode === 229) return
  event.preventDefault()
  void handleSend()
}

async function handleSend(): Promise<void> {
  if (!canSend.value || !session.value) return
  const content = draft.value
  actionError.value = ''
  try {
    await chatStore.sendMessage(content)
    // 保存成功才清空输入框；失败时保留内容便于修改重发
    draft.value = ''
  } catch (error) {
    actionError.value = errorMessage(error)
  }
}

async function handleStop(): Promise<void> {
  if (!session.value || stopPending.value) return
  stopPending.value = true
  actionError.value = ''
  try {
    await chatStore.stopGeneration(session.value.id)
  } catch (error) {
    actionError.value = errorMessage(error)
  } finally {
    stopPending.value = false
  }
}

// ---- 滚动与时间 ----

const messagesEl = ref<HTMLElement | null>(null)
const stickToBottom = ref(true)

function handleScroll(): void {
  const el = messagesEl.value
  if (!el) return
  stickToBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < 60
}

function scrollToBottom(): void {
  const el = messagesEl.value
  if (!el) return
  el.scrollTop = el.scrollHeight
  stickToBottom.value = true
}

/** 自动跟随暂停（用户上翻）时出现的快捷入口 */
const showJumpLatest = computed(() => session.value !== null && !stickToBottom.value)

const streamTick = computed(() => {
  const g = generation.value
  const segments = g?.segments ?? []
  const lastTool = [...segments].reverse().find((segment) => segment.kind === 'tool')
  return `${messages.value.length}:${g?.phase ?? ''}:${segmentsText(segments).length}:${
    lastTool && lastTool.kind === 'tool' ? lastTool.tool.status : ''
  }:${segments.length}`
})

watch(streamTick, async () => {
  if (!stickToBottom.value) return
  await nextTick()
  const el = messagesEl.value
  if (el) el.scrollTop = el.scrollHeight
})

// 切换会话时重置滚动位置：保持跟随时直接跳到底部
watch(
  () => session.value?.id,
  async () => {
    await nextTick()
    if (stickToBottom.value) scrollToBottom()
  },
)

/** 展开/收起卡片时保持阅读位置：仅当卡片在可视底部以上时补偿高度差。 */
async function handleCardToggle(card: HTMLElement): Promise<void> {
  const el = messagesEl.value
  if (!el || stickToBottom.value) return
  const cardRect = card.getBoundingClientRect()
  const viewportRect = el.getBoundingClientRect()
  if (cardRect.top > viewportRect.bottom) return
  await nextTick()
  const grown = card.getBoundingClientRect().height - cardRect.height
  if (grown !== 0) el.scrollTop += grown
}

// ---- 消息时间线（已结束运行的工具过程，按消息 id 保留在 store） ----

function timelineFor(message: ChatMessage): GenerationSegment[] | null {
  return chatStore.toolTimelines[message.id] ?? null
}

/** 已结束回复的展示项：连续工具合并成“使用N个工具”组，默认全部收起。 */
function timelineItems(message: ChatMessage): ChatDisplayItem[] {
  return buildDisplayItems(timelineFor(message) ?? [])
}

const orphanItems = computed(() =>
  buildDisplayItems(session.value ? chatStore.orphanSegments[session.value.id] ?? [] : []),
)

const generationItems = computed(() => buildDisplayItems(generation.value?.segments ?? []))

/** 末项是思考项：思考仍在流式生成，由思考行自己表达“思考中”。 */
const reasoningStreaming = computed(() => {
  const items = generationItems.value
  const last = items[items.length - 1]
  return last !== undefined && last.kind === 'reasoning'
})

// ---- 运行计时（“处理中 1m 52s”） ----

const nowMs = ref(Date.now())
let clockTimer: number | undefined

onMounted(() => {
  clockTimer = window.setInterval(() => {
    nowMs.value = Date.now()
  }, 1000)
})

onUnmounted(() => {
  if (clockTimer !== undefined) window.clearInterval(clockTimer)
})

/** 运行耗时文本：秒级用“12s”，分钟级用“1m 52s”，小时级用“1h 5m”。 */
function formatElapsed(ms: number): string {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000))
  if (totalSeconds < 60) return `${totalSeconds}s`
  const minutes = Math.floor(totalSeconds / 60)
  if (minutes < 60) return `${minutes}m ${totalSeconds % 60}s`
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`
}

const elapsedText = computed(() => {
  const startedAt = generation.value?.startedAt
  if (startedAt === undefined || startedAt === null) return ''
  return formatElapsed(nowMs.value - startedAt)
})

/** 运行状态行文本：“处理中 1m 52s”。 */
const runStatusText = computed(() => (elapsedText.value ? `处理中 ${elapsedText.value}` : '处理中'))

const phaseLabel = computed(() => {
  const phase = generation.value?.phase
  if (phase === 'tooling') return '调用工具'
  if (phase === 'generating') return '生成'
  if (phase === 'compacting') return '压缩上下文'
  if (phase === 'approval') return '等待审批'
  return '思考'
})

/** 对一次审批做出决议；结果由 approval_result 事件更新到卡片上。 */
function handleDecide(requestId: string, approved: boolean): void {
  if (!session.value) return
  void chatStore.decideApproval(session.value.id, requestId, approved)
}

function formatMessageTime(iso: string): string {
  const date = new Date(iso)
  return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
}

function formatSessionTime(iso: string): string {
  const date = new Date(iso)
  const now = new Date()
  const sameDay =
    date.getFullYear() === now.getFullYear() &&
    date.getMonth() === now.getMonth() &&
    date.getDate() === now.getDate()
  if (sameDay) {
    return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
  }
  const yesterday = new Date(now)
  yesterday.setDate(now.getDate() - 1)
  const isYesterday =
    date.getFullYear() === yesterday.getFullYear() &&
    date.getMonth() === yesterday.getMonth() &&
    date.getDate() === yesterday.getDate()
  if (isYesterday) return '昨天'
  return `${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}
</script>

<template>
  <div class="chat-root">
    <PageHeader title="聊天" description="选择 Agent 进行多轮对话，回复内容实时流式展示。" />

    <div class="chat-body">
      <!-- 左侧：会话列表 -->
      <aside class="session-pane card">
        <div class="session-head">
          <button
            class="btn-primary new-session"
            type="button"
            :disabled="!hasAgents || creatingSession"
            @click="handleCreateSession"
          >
            {{ creatingSession ? '正在创建…' : '新建会话' }}
          </button>
        </div>
        <div class="session-list">
          <p v-if="chatStore.sessions.length === 0" class="session-empty">还没有会话，点击上方按钮开始</p>
          <button
            v-for="item in chatStore.sessions"
            :key="item.id"
            type="button"
            class="session-item"
            :class="{ 'is-active': item.id === chatStore.activeSessionId }"
            @click="handleSelectSession(item.id)"
          >
            <span class="session-title">
              {{ item.title }}
              <span v-if="chatStore.isSessionGenerating(item.id)" class="generating-tag">生成中</span>
            </span>
            <span class="session-meta">
              {{ formatSessionTime(item.updatedAt) }}<template v-if="item.agentName"> · {{ item.agentName }}</template>
            </span>
          </button>
        </div>
      </aside>

      <!-- 右侧：聊天区域 -->
      <section class="chat-pane card">
        <div v-if="!hasAgents" class="chat-placeholder">
          <h2 class="placeholder-title">还没有可用的 Agent</h2>
          <p class="placeholder-text">聊天前请先创建 Agent，配置好模型与系统提示词。</p>
          <button class="btn-primary" type="button" @click="router.push('/agents')">前往 Agent 管理</button>
        </div>

        <div v-else-if="!session" class="chat-placeholder">
          <h2 class="placeholder-title">开始一段新的对话</h2>
          <p class="placeholder-text">点击左侧「新建会话」，或从列表中选择一个会话继续。</p>
        </div>

        <template v-else>
          <header class="chat-toolbar">
            <label class="agent-label" for="chat-agent-select">Agent</label>
            <select
              id="chat-agent-select"
              v-model="agentSelect"
              class="agent-select"
              :disabled="isGenerating"
              @change="handleAgentChange"
            >
              <option v-if="session.agentId === null" value="">请选择 Agent</option>
              <option v-for="agent in agentsStore.agents" :key="agent.id" :value="agent.id">
                {{ agent.name }}{{ agent.isDefault ? '（默认）' : '' }}
              </option>
            </select>
            <span v-if="isGenerating" class="generating-badge">正在{{ phaseLabel }}…</span>
            <span v-else-if="agentMissing" class="agent-missing">当前会话未选择 Agent，发送前请先选择</span>
          </header>

          <div ref="messagesEl" class="messages" @scroll="handleScroll">
            <p v-if="messages.length === 0" class="messages-empty">输入消息开始对话，回复会实时显示在这里。</p>

            <div
              v-for="message in messages"
              :key="message.id"
              class="message-row"
              :class="message.role === 'user' ? 'is-user' : 'is-assistant'"
            >
              <div class="message-bubble" :class="message.role === 'user' ? 'is-user' : 'is-assistant'">
                <div v-if="message.role === 'assistant'" class="bubble-head">
                  <span class="bubble-agent">{{ message.agentName ?? 'Agent' }}</span>
                  <span v-if="message.status === 'incomplete'" class="bubble-status">未完成</span>
                </div>
                <!-- 带时间线的回复：正文、思考行与工具组按事件顺序交错展示 -->
                <template v-if="message.role === 'assistant' && timelineFor(message)">
                  <template v-for="item in timelineItems(message)" :key="item.key">
                    <MarkdownContent v-if="item.kind === 'text'" :text="item.text" />
                    <ThinkingCard v-else-if="item.kind === 'reasoning'" :reasoning="item.reasoning" />
                    <ToolGroupCard
                      v-else-if="item.kind === 'toolGroup'"
                      :tools="item.tools"
                      @toggle="handleCardToggle"
                    />
                    <ApprovalCard
                      v-else-if="item.kind === 'approval'"
                      :approval="item.approval"
                      @decide="(approved) => handleDecide(item.approval.requestId, approved)"
                    />
                    <CompactionCard v-else :compact="item.compact" />
                  </template>
                </template>
                <MarkdownContent v-else-if="message.role === 'assistant'" :text="message.content" />
                <div v-else class="bubble-content">{{ message.content }}</div>
                <div class="bubble-time">{{ formatMessageTime(message.createdAt) }}</div>
              </div>
            </div>

            <!-- 无保存消息的收尾运行（取消且无正文等）留下的思考/工具卡片 -->
            <div v-if="orphanItems.length" class="message-row is-assistant">
              <div class="tool-orphan">
                <template v-for="item in orphanItems" :key="item.key">
                  <ThinkingCard v-if="item.kind === 'reasoning'" :reasoning="item.reasoning" />
                  <ToolGroupCard
                    v-else-if="item.kind === 'toolGroup'"
                    :tools="item.tools"
                    @toggle="handleCardToggle"
                  />
                  <ApprovalCard
                    v-else-if="item.kind === 'approval'"
                    :approval="item.approval"
                    @decide="(approved) => handleDecide(item.approval.requestId, approved)"
                  />
                  <CompactionCard v-else-if="item.kind === 'compact'" :compact="item.compact" />
                </template>
              </div>
            </div>

            <!-- 进行中的生成气泡 -->
            <div v-if="generation" class="message-row is-assistant">
              <div class="message-bubble is-assistant is-streaming">
                <div class="bubble-head">
                  <span class="run-status">
                    <span class="run-mark" aria-hidden="true">✦</span>{{ runStatusText }}
                  </span>
                  <span class="bubble-agent">{{ generation.agentName }}</span>
                </div>
                <template v-for="item in generationItems" :key="item.key">
                  <MarkdownContent
                    v-if="item.kind === 'text'"
                    :text="item.text"
                    :cursor="generation.phase === 'generating' && item.key === generationItems[generationItems.length - 1]?.key"
                  />
                  <ThinkingCard
                    v-else-if="item.kind === 'reasoning'"
                    :reasoning="item.reasoning"
                    :streaming="item.key === generationItems[generationItems.length - 1]?.key"
                    @toggle="handleCardToggle"
                  />
                  <ToolGroupCard
                    v-else-if="item.kind === 'toolGroup'"
                    :tools="item.tools"
                    @toggle="handleCardToggle"
                  />
                  <ApprovalCard
                    v-else-if="item.kind === 'approval'"
                    :approval="item.approval"
                    @decide="(approved) => handleDecide(item.approval.requestId, approved)"
                  />
                  <CompactionCard v-else :compact="item.compact" />
                </template>
                <div
                  v-if="generation.phase !== 'generating' && generation.phase !== 'approval' && !reasoningStreaming"
                  class="bubble-content thinking"
                >
                  <template v-if="generation.phase === 'tooling'">正在调用工具：{{ generation.toolName }}<span class="dots">…</span></template>
                  <template v-else-if="generation.phase === 'compacting'">正在压缩上下文<span class="dots">…</span></template>
                  <template v-else>正在思考<span class="dots">…</span></template>
                </div>
              </div>
            </div>

            <!-- 生成失败提示（不作为聊天内容保存） -->
            <div v-if="generationError" class="generation-error">
              <div class="error-title">{{ generationError.message }}</div>
              <div v-if="generationError.hint" class="error-hint">{{ generationError.hint }}</div>
              <div v-if="generationError.content && !generationError.savedMessage" class="error-unsaved">
                <div class="unsaved-label">以下内容已生成但未保存：</div>
                <div class="unsaved-content">{{ generationError.content }}</div>
              </div>
            </div>
          </div>

          <button v-if="showJumpLatest" class="jump-latest" type="button" @click="scrollToBottom">
            ↓ 回到最新消息
          </button>

          <footer class="composer">
            <p v-if="actionError" class="action-error">{{ actionError }}</p>
            <div class="composer-row">
              <textarea
                v-model="draft"
                class="composer-input"
                rows="3"
                placeholder="输入消息，Enter 发送，Shift + Enter 换行"
                :disabled="!hasAgents || agentMissing"
                @keydown="handleKeydown"
              />
              <button
                v-if="!isGenerating"
                class="btn-primary send-button"
                type="button"
                :disabled="!canSend"
                @click="handleSend"
              >
                发送
              </button>
              <button
                v-else
                class="btn-danger send-button"
                type="button"
                :disabled="stopPending"
                @click="handleStop"
              >
                {{ stopPending ? '正在停止…' : '停止生成' }}
              </button>
            </div>
            <p class="composer-hint">Enter 发送 · Shift + Enter 换行 · 生成期间可切换会话（关闭页面会停止生成）</p>
          </footer>
        </template>
      </section>
    </div>
  </div>
</template>

<style scoped>
.chat-root {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
}

.chat-body {
  display: flex;
  flex: 1;
  gap: 12px;
  min-height: 0;
}

/* ---- 左侧会话列表 ---- */

.session-pane {
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  width: 232px;
  min-height: 0;
}

.session-head {
  padding: 10px;
  border-bottom: 1px solid var(--border);
}

.new-session {
  width: 100%;
  justify-content: center;
}

.new-session:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.session-list {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 2px;
  padding: 8px;
  overflow-y: auto;
}

.session-empty {
  padding: 10px 6px;
  color: var(--text-tertiary);
  font-size: 11.5px;
}

.session-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px 10px;
  border: none;
  border-radius: 8px;
  background: none;
  text-align: left;
  cursor: pointer;
}

.session-item:hover {
  background: var(--bg-hover);
}

.session-item.is-active {
  background: var(--bg-active);
}

.session-title {
  display: block;
  overflow: hidden;
  color: var(--text-primary);
  font-size: 12.5px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.generating-tag {
  margin-left: 4px;
  padding: 1px 5px;
  border-radius: 4px;
  background: var(--accent-dim);
  color: var(--accent);
  font-size: 10px;
  font-weight: 600;
}

.session-meta {
  overflow: hidden;
  color: var(--text-tertiary);
  font-size: 10.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ---- 右侧聊天区域 ---- */

.chat-pane {
  position: relative;
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
}

.chat-placeholder {
  display: flex;
  flex: 1;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 24px;
}

.placeholder-title {
  color: var(--text-primary);
  font-size: 14px;
  font-weight: 600;
}

.placeholder-text {
  color: var(--text-secondary);
  font-size: 12px;
}

.chat-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  border-bottom: 1px solid var(--border);
}

.agent-label {
  color: var(--text-secondary);
  font-size: 12px;
}

.agent-select {
  height: 28px;
  max-width: 240px;
  padding: 0 8px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-surface);
  color: var(--text-primary);
  font-size: 12.5px;
}

.agent-select:focus {
  border-color: var(--accent);
  outline: none;
}

.agent-select:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.generating-badge {
  color: var(--accent);
  font-size: 11.5px;
}

.agent-missing {
  color: var(--warning);
  font-size: 11.5px;
}

/* ---- 消息区 ---- */

.messages {
  flex: 1;
  padding: 14px 16px;
  overflow-y: auto;
}

.messages-empty {
  padding: 24px 0;
  color: var(--text-tertiary);
  font-size: 12px;
  text-align: center;
}

.message-row {
  display: flex;
  margin-bottom: 12px;
}

.message-row.is-user {
  justify-content: flex-end;
}

.message-row.is-assistant {
  justify-content: flex-start;
}

.message-bubble {
  max-width: 76%;
  padding: 8px 12px;
  border-radius: 10px;
}

.message-bubble.is-user {
  border: 1px solid var(--accent);
  background: var(--accent);
  color: var(--text-inverse);
}

.message-bubble.is-assistant {
  border: 1px solid var(--border);
  background: var(--bg-surface);
  color: var(--text-primary);
}

.bubble-head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 3px;
}

/* 运行状态行（“处理中 1m 52s”）：与轨迹区的状态行一致 */
.run-status {
  display: inline-flex;
  gap: 4px;
  align-items: center;
  color: var(--text-secondary);
  font-size: 11px;
  font-weight: 600;
}

.run-mark {
  color: var(--accent);
  font-size: 11px;
  animation: pulse 1.2s ease-in-out infinite;
}

.bubble-agent {
  color: var(--text-tertiary);
  font-size: 10.5px;
}

.bubble-status {
  padding: 1px 5px;
  border-radius: 4px;
  background: var(--bg-hover);
  color: var(--warning);
  font-size: 10px;
  font-weight: 600;
}

.bubble-content {
  overflow-wrap: break-word;
  font-size: 12.5px;
  line-height: 1.55;
  white-space: pre-wrap;
}

.bubble-time {
  margin-top: 3px;
  color: var(--text-tertiary);
  font-size: 10px;
  text-align: right;
}

.message-bubble.is-user .bubble-time {
  color: var(--text-inverse);
  opacity: 0.75;
}

.thinking {
  color: var(--text-secondary);
}

/* ---- 工具卡片与孤儿卡片组 ---- */

.tool-orphan {
  display: flex;
  flex-direction: column;
  max-width: 76%;
}

/* ---- 回到最新消息 ---- */

.jump-latest {
  position: absolute;
  bottom: 96px;
  left: 50%;
  z-index: 2;
  padding: 5px 12px;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: var(--bg-surface);
  box-shadow: 0 2px 8px rgba(45, 32, 23, 0.12);
  color: var(--text-secondary);
  font-size: 11.5px;
  cursor: pointer;
  transform: translateX(-50%);
}

.jump-latest:hover {
  border-color: var(--accent);
  color: var(--accent);
}

.dots {
  animation: pulse 1.2s ease-in-out infinite;
}

@keyframes pulse {
  0%,
  100% {
    opacity: 0.3;
  }

  50% {
    opacity: 1;
  }
}

/* ---- 生成错误提示 ---- */

.generation-error {
  margin-bottom: 12px;
  padding: 10px 12px;
  border: 1px solid var(--danger);
  border-radius: 10px;
  background: var(--bg-surface);
}

.error-title {
  color: var(--danger);
  font-size: 12px;
  font-weight: 600;
}

.error-hint {
  margin-top: 3px;
  color: var(--text-secondary);
  font-size: 11.5px;
}

.error-unsaved {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px dashed var(--border);
}

.unsaved-label {
  color: var(--warning);
  font-size: 10.5px;
}

.unsaved-content {
  margin-top: 4px;
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1.55;
  white-space: pre-wrap;
}

/* ---- 输入区 ---- */

.composer {
  padding: 10px 14px 12px;
  border-top: 1px solid var(--border);
}

.action-error {
  margin-bottom: 8px;
  color: var(--danger);
  font-size: 11.5px;
}

.composer-row {
  display: flex;
  gap: 10px;
  align-items: flex-end;
}

.composer-input {
  flex: 1;
  min-height: 62px;
  max-height: 180px;
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-surface);
  color: var(--text-primary);
  font-size: 12.5px;
  line-height: 1.5;
  resize: none;
}

.composer-input:focus {
  border-color: var(--accent);
  outline: none;
}

.composer-input:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.send-button {
  flex-shrink: 0;
}

.send-button:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.composer-hint {
  margin-top: 6px;
  color: var(--text-tertiary);
  font-size: 10.5px;
}

/* ---- 窄屏：压缩会话列表宽度 ---- */

@media (max-width: 899px) {
  .session-pane {
    width: 168px;
  }
}
</style>
