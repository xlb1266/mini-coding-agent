<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import PageHeader from '../components/PageHeader.vue'
import ApprovalCard from '../components/ApprovalCard.vue'
import ChatComposer from '../components/ChatComposer.vue'
import CompactionCard from '../components/CompactionCard.vue'
import MarkdownContent from '../components/MarkdownContent.vue'
import ProjectStagesCard from '../components/ProjectStagesCard.vue'
import ThinkingCard from '../components/ThinkingCard.vue'
import ToolGroupCard from '../components/ToolGroupCard.vue'
import type { ContextUsage, ModePreset } from '../api/chat'
import { getChatContextUsage, matchModePreset } from '../api/chat'
import { useAgentsStore } from '../stores/agents'
import { segmentsText, useChatStore } from '../stores/chat'
import { useModelsStore } from '../stores/models'
import type { GenerationSegment } from '../stores/chat'
import { buildDisplayItems } from './chat-format'
import type { ChatDisplayItem } from './chat-format'
import type { ChatMessage } from '../api/chat'

const agentsStore = useAgentsStore()
const chatStore = useChatStore()
const modelsStore = useModelsStore()
const router = useRouter()

const actionError = ref('')
const creatingSession = ref(false)
const stopPending = ref(false)

const composerRef = ref<InstanceType<typeof ChatComposer> | null>(null)

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
    !isGenerating.value,
)

// ---- 会话级档位与模型（输入框切换，null 跟随 Agent 配置） ----

const activeAgent = computed(() =>
  session.value?.agentId != null
    ? agentsStore.agents.find((agent) => agent.id === session.value!.agentId) ?? null
    : null,
)

const effectiveApproval = computed(
  () => session.value?.approvalLevel ?? activeAgent.value?.approvalLevel ?? null,
)

const effectiveAutonomy = computed(
  () => session.value?.autonomyMode ?? activeAgent.value?.autonomyMode ?? null,
)

/** 当前生效档位组合对应的菜单预设；组合不匹配三项预设时为 null（显示"自定义档位"）。 */
const activePreset = computed(() => matchModePreset(effectiveApproval.value, effectiveAutonomy.value))

const effectiveModelId = computed(
  () => session.value?.modelId ?? activeAgent.value?.model?.id ?? null,
)

const modelReady = computed(() => {
  if (effectiveModelId.value === null) return false
  return modelsStore.models.some((model) => model.id === effectiveModelId.value)
})

const modelLabel = computed(() => {
  const model = modelsStore.models.find((item) => item.id === effectiveModelId.value)
  return model?.displayName ?? '模型缺失'
})

// ---- 上下文占用量（估算口径）：切会话、回复结束、切档位/模型后刷新 ----

const usage = ref<ContextUsage | null>(null)

async function fetchUsage(): Promise<void> {
  const sessionId = session.value?.id
  if (sessionId === undefined || session.value?.agentId == null) {
    usage.value = null
    return
  }
  try {
    const next = await getChatContextUsage(sessionId)
    // 请求期间切走了会话：丢弃过期结果
    if (session.value?.id === sessionId) usage.value = next
  } catch {
    if (session.value?.id === sessionId) usage.value = null
  }
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : '操作失败，请重试'
}

// ---- 初始化与选择 ----

onMounted(async () => {
  await agentsStore.fetchAgents().catch(() => undefined)
  await chatStore.fetchSessions().catch(() => undefined)
  // 每次进入聊天页都刷新模型列表：模型管理页的增删要及时反映到切换菜单
  await modelsStore.fetchModels().catch(() => undefined)
  if (chatStore.activeSessionId === null && chatStore.sessions.length > 0) {
    await chatStore.selectSession(chatStore.sessions[0]!.id).catch(() => undefined)
  }
})

watch(
  () => session.value?.id,
  () => {
    actionError.value = ''
    void fetchUsage()
  },
  { immediate: true },
)

watch(
  () => session.value?.agentId,
  (agentId) => {
    agentSelect.value = agentId ?? ''
  },
  { immediate: true },
)

// 回复结束后上下文已变化：刷新占用量
watch(isGenerating, (generating, previous) => {
  if (previous && !generating) void fetchUsage()
})

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

async function handleSend(content: string): Promise<void> {
  if (!canSend.value || !session.value) return
  actionError.value = ''
  try {
    await chatStore.sendMessage(content)
    // 保存成功才清空输入框；失败时保留内容便于修改重发
    composerRef.value?.clearDraft()
  } catch (error) {
    actionError.value = errorMessage(error)
  }
}

/** 切换执行档位（会话级覆盖）：成功后同步会话并刷新上下文用量。 */
async function handleChangeMode(preset: ModePreset): Promise<void> {
  if (!session.value) return
  actionError.value = ''
  try {
    await chatStore.changeMode(session.value.id, preset.approvalLevel, preset.autonomyMode)
    void fetchUsage()
  } catch (error) {
    actionError.value = errorMessage(error)
  }
}

/** 切换会话模型（会话级覆盖）：成功后刷新上下文用量（上下文长度随模型变化）。 */
async function handleChangeModel(modelId: number): Promise<void> {
  if (!session.value) return
  actionError.value = ''
  try {
    await chatStore.changeModel(session.value.id, modelId)
    void fetchUsage()
  } catch (error) {
    actionError.value = errorMessage(error)
  }
}

// ---- 阶段项目（阶段化开发 v2）----

/** 当前会话所属的阶段项目（项目会话才拉取）。 */
const project = computed(() => {
  const projectId = session.value?.projectId
  return projectId ? chatStore.stageProjects[projectId] ?? null : null
})

const projectBusy = ref(false)

// 切换会话就刷新项目卡：同一项目内切换阶段会话时 projectId 不变，
// 只 watch projectId 会漏掉阶段收尾后的状态推进
watch(
  () => [session.value?.id, session.value?.projectId] as const,
  ([, projectId]) => {
    if (projectId) void chatStore.fetchStageProject(projectId)
  },
  { immediate: true },
)

// 生成结束（阶段收尾 / 计划上报会推进项目状态）后刷新项目卡
watch(isGenerating, (generating, previous) => {
  const projectId = session.value?.projectId
  if (previous && !generating && projectId) void chatStore.fetchStageProject(projectId)
})

/** 项目推进（开始 / 下一阶段 / 汇总）：后端自动新建会话并切换过去。 */
async function runProjectAction(action: 'start' | 'next' | 'summary'): Promise<void> {
  const projectId = session.value?.projectId
  if (!projectId || projectBusy.value) return
  projectBusy.value = true
  actionError.value = ''
  try {
    if (action === 'start') {
      await chatStore.startStageProject(projectId)
    } else if (action === 'next') {
      await chatStore.nextStageProject(projectId)
    } else {
      await chatStore.summarizeStageProject(projectId)
    }
  } catch (error) {
    actionError.value = errorMessage(error)
  } finally {
    projectBusy.value = false
  }
}

// ---- 阶段完成提示 ----
// 不再做单独的悬浮横幅：阶段成果概述在模型回复正文里，
// 交接文档全文在 finish_stage 工具卡展开区；推进按钮在上方项目卡里。

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

          <!-- 阶段项目清单卡（阶段化开发 v2）：项目会话常显，推进按钮按状态出现 -->
          <div v-if="project" class="project-strip">
            <ProjectStagesCard
              :project="project"
              :session-role="session.projectRole ?? null"
              :awaiting-acceptance="!!session.awaitingAcceptance"
              :busy="projectBusy || isGenerating"
              @start="runProjectAction('start')"
              @next="runProjectAction('next')"
              @summary="runProjectAction('summary')"
              @open-session="handleSelectSession"
            />
          </div>

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

          <footer class="composer-area">
            <p v-if="actionError" class="action-error">{{ actionError }}</p>
            <ChatComposer
              ref="composerRef"
              :disabled="!hasAgents || agentMissing"
              :generating="isGenerating"
              :stop-pending="stopPending"
              :session-ready="canSend"
              :preset="activePreset"
              :model-label="modelLabel"
              :model-ready="modelReady"
              :active-model-id="effectiveModelId"
              :models="modelsStore.models"
              :usage="usage"
              @send="handleSend"
              @stop="handleStop"
              @change-mode="handleChangeMode"
              @change-model="handleChangeModel"
            />
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

/* ---- 阶段项目清单卡（阶段化开发 v2） ---- */

.project-strip {
  padding: 8px 14px 0;
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

/* ---- 输入区 ----
 * 输入框本体（正文、档位/用量/模型工具栏）在 ChatComposer 组件内。 */

.composer-area {
  border-top: 1px solid var(--border);
}

.action-error {
  margin: 8px 14px 0;
  color: var(--danger);
  font-size: 11.5px;
}

/* ---- 窄屏：压缩会话列表宽度 ---- */

@media (max-width: 899px) {
  .session-pane {
    width: 168px;
  }
}
</style>
