/** 聊天状态管理：会话列表、消息、流式生成与后台续跑。
 *
 * 生成中的状态（generations）与 SSE 连接（streams）都挂在 store 而不是
 * 组件上：切换会话、新建会话或离开聊天页面时连接保持，回复在后台继续
 * 生成；回到会话时通过 /messages 的生成快照恢复进度并重新订阅。
 *
 * 阶段九：一次生成的正文与工具卡片按事件顺序交错保存在 segments 里
 * （正文段 / 工具卡片段），工具卡片用 callId 关联起止事件、原地更新；
 * 运行收尾时仍在执行的卡片标记为“已取消”或“状态未知”，不停留在
 * “正在执行”也不误标成功。已结束运行的时间线按保存消息 id 保留
 * （toolTimelines）；运行中留在内存里，收尾后由后端随消息入库，
 * 刷新或重新打开会话时从消息自带的 timeline 恢复（seedTimelines）。
 *
 * 阶段十：时间线增加压缩卡片段（kind: 'compact'）——上下文压缩起止用
 * compactionId 配对、原地更新，备用裁剪直接追加；压缩期间的阶段为
 * compacting。卡片只展示估算数字与原因，不包含摘要正文。
 *
 * 模块二：时间线增加审批卡片段（kind: 'approval'）——开启审批的 Agent
 * 执行高风险工具前暂停，approval_request 建卡（pending），approval_result
 * 用 requestId 配对原地更新为终态（approved/rejected/timeout/cancelled）；
 * 阶段为 approval。决议经 decideApproval 调用后端接口，等待期间按钮置灰。
 *
 * 思考段：时间线增加思考段（kind: 'reasoning'）——推理模型的思考增量
 * 按事件顺序合并进段，仅用于思考卡片展示，不拼进回复正文（正文取回
 * 正文段，与后端保存口径一致）。
 */

import { defineStore } from 'pinia'

import {
  createChatSession as apiCreateSession,
  decideChatApproval as apiDecideApproval,
  getChatMessages as apiGetMessages,
  getStageProject as apiGetStageProject,
  listChatSessions,
  nextStageProject as apiNextStageProject,
  openChatEventStream,
  selectChatAgent as apiSelectAgent,
  selectChatMode as apiSelectMode,
  selectChatModel as apiSelectModel,
  sendChatMessage as apiSendMessage,
  startStageProject as apiStartStageProject,
  stopChatGeneration as apiStopGeneration,
  summarizeStageProject as apiSummarizeStageProject,
  type ApprovalDecisionStatus,
  type ApprovalLevel,
  type AutonomyMode,
  type ChatEvent,
  type ChatGeneration,
  type ChatMessage,
  type ChatSessionSummary,
  type CompactStatus,
  type GenerationPhase,
  type GenerationTimelineItem,
  type RunError,
  type StageProject,
  type ToolResultPayload,
  type ToolType,
} from '../api/chat'

export type { ApprovalDecisionStatus }

/** 一次生成失败的原因与已生成内容（未保存部分只在内存中）。 */
export interface GenerationError {
  message: string
  hint: string | null
  errorType: string | null
  content: string
  savedMessage: ChatMessage | null
}

/** 一次工具调用的展示状态（含 Skill 加载）。 */
export type ToolActivityStatus = 'running' | 'success' | 'error' | 'cancelled' | 'unknown'

export interface ToolActivity {
  /** 工具调用标识：关联开始/结束事件；内部编号只用于关联，不作主标题 */
  callId: string
  /** 模型可见的内部工具名（如 mcp3_search） */
  toolName: string
  /** 易读名称：本地工具名 / MCP 工具名 / “加载 Skill” */
  displayName: string
  /** MCP Server 名称；非 MCP 工具为 null */
  serverName: string | null
  toolType: ToolType
  status: ToolActivityStatus
  durationMs?: number
  summary?: string
  /** 整理后的输入参数（后端已脱敏并截断）；参数不是合法 JSON 时为 null */
  arguments?: Record<string, unknown> | null
  /** 整理后的结果文本；未结束 / 被取消时为 null */
  result?: ToolResultPayload | null
}

/** 上下文压缩/备用裁剪卡片的展示状态；只含安全的估算数字，不含摘要正文。 */
export interface CompactActivity {
  /** 压缩标识：关联开始/结束事件；备用裁剪没有配对标识 */
  compactionId: string | null
  status: CompactStatus
  /** 触发原因（展示文本） */
  reason?: string
  /** 前后估算 Token（估算值，非模型实际用量） */
  estimatedTokensBefore?: number | null
  estimatedTokensAfter?: number | null
  /** 处理 / 保留的消息数量 */
  processedMessages?: number | null
  keptMessages?: number | null
  /** 新摘要的估算长度 */
  summaryTokens?: number | null
  /** 分批数量与摘要请求的模型调用次数 */
  batches?: number | null
  modelCalls?: number | null
  durationMs?: number | null
  /** 备用裁剪裁掉的消息数量 */
  droppedMessages?: number | null
  error?: RunError | null
}

/** 审批卡片的展示状态：pending 等待决议（按钮可点），其余为终态。 */
export interface ApprovalActivity {
  requestId: string
  callId: string
  toolName: string
  displayName: string
  toolType: ToolType
  status: ApprovalDecisionStatus
  arguments: Record<string, unknown> | null
  /** 决议请求进行中：按钮置灰防重复提交 */
  deciding?: boolean
  /** 决议失败时的提示（如审批已被处理） */
  error?: string | null
}

/** 生成时间线的段：正文段、思考段、工具/审批/压缩卡片段，按事件顺序交错。 */
export interface TextSegment {
  kind: 'text'
  text: string
}

/** 模型思考段（推理模型下发）；仅展示，不拼进正文。 */
export interface ReasoningSegment {
  kind: 'reasoning'
  text: string
  durationMs?: number | null
}

export interface ToolSegment {
  kind: 'tool'
  tool: ToolActivity
}

export interface ApprovalSegment {
  kind: 'approval'
  approval: ApprovalActivity
}

export interface CompactSegment {
  kind: 'compact'
  compact: CompactActivity
}

export type GenerationSegment =
  | TextSegment
  | ReasoningSegment
  | ToolSegment
  | ApprovalSegment
  | CompactSegment

/** 进行中的生成：正文与工具卡片按事件顺序交错保留。 */
export interface RunningGeneration {
  /** 运行标识：校验事件归属，旧运行的迟到事件不落入当前时间线 */
  runId: string | null
  phase: GenerationPhase
  agentName: string
  toolName: string | null
  /** 运行开始时间（epoch 毫秒）：展示“处理中 Xs”；未知时为 null */
  startedAt: number | null
  /** 当前思考段的开始时间（本地时钟）：思考结束后据此补上耗时 */
  reasoningStartedAt: number | null
  segments: GenerationSegment[]
}

interface ChatState {
  sessions: ChatSessionSummary[]
  sessionsLoaded: boolean
  sessionsLoading: boolean
  activeSessionId: number | null
  messagesBySession: Record<number, ChatMessage[]>
  loadedSessions: Record<number, boolean>
  /** 进行中的生成（按会话隔离，后台生成中的会话也保留在这里） */
  generations: Record<number, RunningGeneration>
  /** 生成失败的提示（终态后保留，直到该会话再次发送） */
  generationErrors: Record<number, GenerationError | null>
  /** 已结束运行的工具时间线，按已保存消息 id 索引（页面内保留） */
  toolTimelines: Record<number, GenerationSegment[]>
  /** 无保存消息的收尾运行留下的工具/压缩卡片，按会话索引 */
  orphanSegments: Record<number, GenerationSegment[]>
  /** 每个会话的 SSE 连接；组件卸载不中断，仅停止/重连时 abort */
  streams: Record<number, AbortController>
  /** 事件流意外断开的连续重连次数（成功收到事件即清零） */
  reconnectAttempts: Record<number, number>
  /** 阶段项目（阶段化开发 v2）：按项目 id 缓存，随会话切换按需拉取 */
  stageProjects: Record<number, StageProject>
}

const MAX_RECONNECT_ATTEMPTS = 3

/** 拼出时间线里的全部正文（工具前后的文字按序连接）。 */
export function segmentsText(segments: GenerationSegment[]): string {
  return segments
    .filter((segment): segment is TextSegment => segment.kind === 'text')
    .map((segment) => segment.text)
    .join('')
}

/** 在时间线里定位 callId 对应的卡片：优先仍在执行的，否则取最后一个同名卡片。 */
function matchTool(segments: GenerationSegment[], callId: string): ToolActivity | null {
  let fallback: ToolActivity | null = null
  for (let i = segments.length - 1; i >= 0; i -= 1) {
    const segment = segments[i]!
    if (segment.kind !== 'tool' || segment.tool.callId !== callId) continue
    if (segment.tool.status === 'running') return segment.tool
    if (fallback === null) fallback = segment.tool
  }
  return fallback
}

/** 在时间线里定位压缩标识对应的压缩卡片。 */
function matchCompact(segments: GenerationSegment[], compactionId: string): CompactActivity | null {
  for (let i = segments.length - 1; i >= 0; i -= 1) {
    const segment = segments[i]!
    if (segment.kind === 'compact' && segment.compact.compactionId === compactionId) {
      return segment.compact
    }
  }
  return null
}

/** 在时间线里定位 requestId 对应的审批卡片。 */
function matchApproval(
  segments: GenerationSegment[],
  requestId: string,
): ApprovalActivity | null {
  for (let i = segments.length - 1; i >= 0; i -= 1) {
    const segment = segments[i]!
    if (segment.kind === 'approval' && segment.approval.requestId === requestId) {
      return segment.approval
    }
  }
  return null
}

/** 收尾扫描：仍在执行的卡片标记为取消或状态未知，不得停留在“执行中”或误标成功。 */
function finalizeActivities(segments: GenerationSegment[], status: ToolActivityStatus): void {
  for (const segment of segments) {
    if (segment.kind === 'tool' && segment.tool.status === 'running') {
      segment.tool.status = status
    }
    if (segment.kind === 'compact' && segment.compact.status === 'running') {
      segment.compact.status = status
    }
    if (segment.kind === 'approval' && segment.approval.status === 'pending') {
      // 运行结束（停止/断连）时未决议的审批不再有效
      segment.approval.status = 'cancelled'
    }
  }
}

function hasActivity(segments: GenerationSegment[]): boolean {
  return segments.some((segment) => segment.kind !== 'text')
}

/** 把后端时间线（或正文兜底）转换为本地生成段落。 */
function segmentsFromTimeline(
  timeline: GenerationTimelineItem[] | null | undefined,
  fallbackText: string,
): GenerationSegment[] {
  if (!timeline || timeline.length === 0) {
    return fallbackText ? [{ kind: 'text', text: fallbackText }] : []
  }
  const segments: GenerationSegment[] = []
  for (const item of timeline) {
    if (item.kind === 'text') {
      segments.push({ kind: 'text', text: item.text })
    } else if (item.kind === 'reasoning') {
      segments.push({
        kind: 'reasoning',
        text: item.text,
        durationMs: item.durationMs ?? null,
      })
    } else if (item.kind === 'tool') {
      segments.push({
        kind: 'tool',
        tool: {
          callId: item.callId,
          toolName: item.toolName,
          displayName: item.displayName,
          serverName: item.serverName ?? null,
          toolType: item.toolType,
          status: item.status,
          durationMs: item.durationMs,
          summary: item.summary,
          arguments: item.arguments ?? null,
          result: item.result ?? null,
        },
      })
    } else if (item.kind === 'approval') {
      segments.push({
        kind: 'approval',
        approval: {
          requestId: item.requestId,
          callId: item.callId,
          toolName: item.toolName,
          displayName: item.displayName || item.toolName,
          toolType: item.toolType,
          status: item.status,
          arguments: item.arguments ?? null,
        },
      })
    } else {
      segments.push({
        kind: 'compact',
        compact: {
          compactionId: item.compactionId ?? null,
          status: item.status,
          reason: item.reason,
          estimatedTokensBefore: item.estimatedTokensBefore ?? null,
          estimatedTokensAfter: item.estimatedTokensAfter ?? null,
          processedMessages: item.processedMessages ?? null,
          keptMessages: item.keptMessages ?? null,
          summaryTokens: item.summaryTokens ?? null,
          batches: item.batches ?? null,
          modelCalls: item.modelCalls ?? null,
          durationMs: item.durationMs ?? null,
          droppedMessages: item.droppedMessages ?? null,
          error: item.error ?? null,
        },
      })
    }
  }
  return segments
}

function toRunningGeneration(generation: ChatGeneration): RunningGeneration {
  return {
    runId: null,
    phase: generation.phase,
    agentName: generation.agentName,
    toolName: generation.toolName ?? null,
    startedAt: generation.startedAt ?? null,
    reasoningStartedAt: null,
    segments: segmentsFromTimeline(generation.timeline, generation.content),
  }
}

function generationFromSnapshot(event: Extract<ChatEvent, { type: 'snapshot' }>): RunningGeneration {
  return {
    runId: event.runId,
    phase: event.phase,
    agentName: event.agentName,
    toolName: event.toolName,
    startedAt: event.startedAt ?? null,
    reasoningStartedAt: null,
    segments: segmentsFromTimeline(event.timeline, event.content),
  }
}

/** 收起进行中的思考段：补上耗时（本地计时），下一段思考重新计时。 */
function closeReasoning(generation: RunningGeneration): void {
  const startedAt = generation.reasoningStartedAt
  if (startedAt === null) return
  generation.reasoningStartedAt = null
  const last = generation.segments[generation.segments.length - 1]
  if (last && last.kind === 'reasoning' && (last.durationMs === undefined || last.durationMs === null)) {
    last.durationMs = Date.now() - startedAt
  }
}

/** 工具卡片的展示标题：MCP 工具同时展示 Server 名称与工具名。 */
export function toolDisplayTitle(
  tool: Pick<ToolActivity, 'displayName' | 'serverName' | 'toolType' | 'toolName'>,
): string {
  if (tool.toolType === 'mcp' && tool.serverName) return `${tool.serverName} · ${tool.displayName}`
  return tool.displayName || tool.toolName
}

export const useChatStore = defineStore('chat', {
  state: (): ChatState => ({
    sessions: [],
    sessionsLoaded: false,
    sessionsLoading: false,
    activeSessionId: null,
    messagesBySession: {},
    loadedSessions: {},
    generations: {},
    generationErrors: {},
    toolTimelines: {},
    orphanSegments: {},
    streams: {},
    reconnectAttempts: {},
    stageProjects: {},
  }),
  getters: {
    activeSession(state): ChatSessionSummary | null {
      return state.sessions.find((session) => session.id === state.activeSessionId) ?? null
    },
    isSessionGenerating(state) {
      return (sessionId: number): boolean => state.generations[sessionId] !== undefined
    },
  },
  actions: {
    async fetchSessions(): Promise<void> {
      this.sessionsLoading = true
      try {
        this.sessions = await listChatSessions()
        this.sessionsLoaded = true
      } finally {
        this.sessionsLoading = false
      }
    },

    /** 刷新列表并同步各会话的生成标记（后台任务结束时排序会变化）。 */
    async refreshSessions(): Promise<void> {
      await this.fetchSessions()
    },

    async createSession(agentId?: number): Promise<ChatSessionSummary> {
      const created = await apiCreateSession(agentId)
      await this.fetchSessions()
      await this.selectSession(created.id)
      return created
    },

    /** 选中会话并按需加载消息；仍在生成的会话恢复进度并继续订阅。 */
    async selectSession(sessionId: number): Promise<void> {
      this.activeSessionId = sessionId
      if (!this.loadedSessions[sessionId]) {
        await this.loadMessages(sessionId)
      } else if (this.generations[sessionId]) {
        this.subscribeEvents(sessionId)
      }
    },

    async loadMessages(sessionId: number): Promise<void> {
      const result = await apiGetMessages(sessionId)
      this.messagesBySession[sessionId] = result.messages
      this.loadedSessions[sessionId] = true
      this.seedTimelines(result.messages)
      this.syncSession(result.session)
      if (result.generation) {
        this.generations[sessionId] = toRunningGeneration(result.generation)
        this.subscribeEvents(sessionId)
      } else {
        delete this.generations[sessionId]
      }
    },

    /** 用后端返回的会话摘要替换本地同 id 项（不重排，排序以后端为准）。 */
    syncSession(session: ChatSessionSummary): void {
      const index = this.sessions.findIndex((item) => item.id === session.id)
      if (index >= 0) {
        this.sessions[index] = session
      } else {
        this.sessions.unshift(session)
      }
    },

    /** 用消息自带的轨迹恢复卡片（刷新 / 重新打开会话后仍能看到思考行与工具卡片）。
     *  只补本地还没有轨迹的消息，不覆盖页面内存里更完整的实时时间线。 */
    seedTimelines(messages: ChatMessage[]): void {
      for (const message of messages) {
        if (!message.timeline || message.timeline.length === 0) continue
        if (this.toolTimelines[message.id]) continue
        const segments = segmentsFromTimeline(message.timeline, '')
        if (segments.length > 0) this.toolTimelines[message.id] = segments
      }
    },

    /** 发送消息：成功后清空该会话错误、开始生成并订阅事件流。 */
    async sendMessage(content: string): Promise<ChatMessage> {
      const sessionId = this.activeSessionId
      if (sessionId === null) {
        throw new Error('请先选择一个会话')
      }
      if (this.generations[sessionId]) {
        throw new Error('当前会话正在生成回复，请等待完成或先停止')
      }
      const result = await apiSendMessage(sessionId, content)
      this.upsertMessage(result.userMessage)
      this.syncSession(result.session)
      this.generationErrors[sessionId] = null
      delete this.orphanSegments[sessionId]
      this.generations[sessionId] = toRunningGeneration(result.generation)
      this.subscribeEvents(sessionId)
      // 产生新消息后会话应排到列表最前
      void this.fetchSessions()
      return result.userMessage
    },

    /** 停止生成：后端保存部分正文后返回结果，本地流一并中断。 */
    async stopGeneration(sessionId: number): Promise<void> {
      const result = await apiStopGeneration(sessionId)
      if (result.message) {
        this.upsertMessage(result.message)
      }
      // 终态事件可能先于停止响应到达（也可能到不了）：这里兜底收尾
      this.settleGeneration(sessionId, 'cancelled', result.message)
      this.finishGeneration(sessionId)
      this.abortStream(sessionId)
    },

    /** 对一次审批做出决议：后端决议成功后，approval_result 事件会更新卡片终态。
     *  等待响应期间按钮置灰防重复提交；失败（如审批已被处理）把错误显示在卡片上。 */
    async decideApproval(sessionId: number, requestId: string, approved: boolean): Promise<void> {
      const generation = this.generations[sessionId]
      if (!generation) return
      const approval = matchApproval(generation.segments, requestId)
      if (!approval || approval.status !== 'pending' || approval.deciding) return
      approval.deciding = true
      approval.error = null
      try {
        await apiDecideApproval(sessionId, requestId, approved ? 'approve' : 'reject')
      } catch (error) {
        approval.deciding = false
        approval.error = error instanceof Error ? error.message : '审批操作失败，请重试'
      }
    },

    /** 切换会话的 Agent（只影响后续请求）。 */
    async changeAgent(agentId: number): Promise<void> {      const sessionId = this.activeSessionId
      if (sessionId === null) {
        return
      }
      if (this.generations[sessionId]) {
        throw new Error('当前会话正在生成回复，请等待完成或先停止')
      }
      const updated = await apiSelectAgent(sessionId, agentId)
      this.syncSession(updated)
    },

    /** 切换会话的执行档位（会话级覆盖，只影响当前会话）。 */
    async changeMode(
      sessionId: number,
      approvalLevel: ApprovalLevel,
      autonomyMode: AutonomyMode,
    ): Promise<void> {
      if (this.generations[sessionId]) {
        throw new Error('当前会话正在生成回复，请等待完成或先停止')
      }
      const updated = await apiSelectMode(sessionId, approvalLevel, autonomyMode)
      this.syncSession(updated)
    },

    /** 切换会话使用的模型（会话级覆盖，只影响当前会话）。 */
    async changeModel(sessionId: number, modelId: number): Promise<void> {
      if (this.generations[sessionId]) {
        throw new Error('当前会话正在生成回复，请等待完成或先停止')
      }
      const updated = await apiSelectModel(sessionId, modelId)
      this.syncSession(updated)
    },

    /** 拉取阶段项目详情（失败时返回 null，页面提示为空态）。 */
    async fetchStageProject(projectId: number): Promise<StageProject | null> {
      try {
        const project = await apiGetStageProject(projectId)
        this.stageProjects[projectId] = project
        return project
      } catch {
        return null
      }
    },

    /** 确认计划并开始阶段 1：后端自动新建阶段会话，前端切换过去。
     *  失败（状态已被推进等）时也刷新一次项目卡，让按钮与真实状态对齐。 */
    async startStageProject(projectId: number): Promise<void> {
      try {
        const result = await apiStartStageProject(projectId)
        this.stageProjects[projectId] = result.project
        await this.fetchSessions()
        await this.selectSession(result.session.id)
      } catch (error) {
        void this.fetchStageProject(projectId)
        throw error
      }
    },

    /** 进入下一阶段：后端自动新建阶段会话（带上一阶段交接），前端切换过去。
     *  全部阶段已完成时会 409：刷新项目卡后按钮会变为「生成项目汇总」。 */
    async nextStageProject(projectId: number): Promise<void> {
      try {
        const result = await apiNextStageProject(projectId)
        this.stageProjects[projectId] = result.project
        await this.fetchSessions()
        await this.selectSession(result.session.id)
      } catch (error) {
        void this.fetchStageProject(projectId)
        throw error
      }
    },

    /** 生成项目汇总：后端新建独立汇总会话，前端切换过去。 */
    async summarizeStageProject(projectId: number): Promise<void> {
      try {
        const result = await apiSummarizeStageProject(projectId)
        this.stageProjects[projectId] = result.project
        await this.fetchSessions()
        await this.selectSession(result.session.id)
      } catch (error) {
        void this.fetchStageProject(projectId)
        throw error
      }
    },

    // ---- SSE 订阅与事件处理 ----

    subscribeEvents(sessionId: number): void {
      if (this.streams[sessionId]) return
      const controller = new AbortController()
      this.streams[sessionId] = controller
      void openChatEventStream(
        sessionId,
        (event) => this.handleEvent(sessionId, event),
        controller.signal,
      )
        .catch(() => {
          // 连接已断：先同步清掉本地连接记录，重连才不会被“已有连接”守卫挡住
          if (this.streams[sessionId] === controller) {
            delete this.streams[sessionId]
          }
          return this.handleStreamFailure(sessionId)
        })
        .finally(() => {
          if (this.streams[sessionId] === controller) {
            delete this.streams[sessionId]
          }
        })
    },

    abortStream(sessionId: number): void {
      this.streams[sessionId]?.abort()
      delete this.streams[sessionId]
    },

    handleEvent(sessionId: number, event: ChatEvent): void {
      this.reconnectAttempts[sessionId] = 0
      if (event.type === 'snapshot') {
        this.generations[sessionId] = generationFromSnapshot(event)
        return
      }
      if (event.type === 'idle') {
        // 后端没有进行中的运行：连接随即关闭，无需处理
        return
      }
      if (event.type === 'run_start') {
        const generation = this.generations[sessionId]
        if (generation) {
          generation.runId = event.runId
          if (typeof event.startedAt === 'number') generation.startedAt = event.startedAt
        }
        return
      }
      if (event.type === 'model_request_start' || event.type === 'model_request_end') {
        // 运行与模型请求的元数据事件：状态由正文增量与工具事件驱动
        return
      }
      const generation = this.generations[sessionId]
      if (!generation) return
      // 运行标识校验：旧运行的迟到事件不更新当前卡片，避免错位或重复建卡
      if (event.runId !== '' && generation.runId !== null && event.runId !== generation.runId) {
        return
      }
      // 思考段在第一个非思考事件到达时收起并补耗时（正文/工具/审批/压缩等）
      if (event.type !== 'reasoning_delta') closeReasoning(generation)

      if (event.type === 'text_delta') {
        // 工具调用前后、各轮次的正文都按序追加，不覆盖；与上一段正文相连时合并
        const last = generation.segments[generation.segments.length - 1]
        if (last && last.kind === 'text') {
          last.text += event.text
        } else {
          generation.segments.push({ kind: 'text', text: event.text })
        }
        if (generation.phase !== 'generating') generation.phase = 'generating'
        return
      }
      if (event.type === 'reasoning_delta') {
        // 思考增量只进时间线展示：与上一段思考相连时合并，不拼进正文
        const last = generation.segments[generation.segments.length - 1]
        if (last && last.kind === 'reasoning') {
          last.text += event.text
        } else {
          generation.segments.push({ kind: 'reasoning', text: event.text })
        }
        if (generation.reasoningStartedAt === null) generation.reasoningStartedAt = Date.now()
        return
      }
      if (event.type === 'tool_call_start') {
        generation.phase = 'tooling'
        generation.toolName = event.serverName
          ? `${event.serverName}·${event.displayName || event.toolName}`
          : event.displayName || event.toolName
        generation.segments.push({
          kind: 'tool',
          tool: {
            callId: event.callId,
            toolName: event.toolName,
            displayName: event.displayName || event.toolName,
            serverName: event.serverName,
            toolType: event.toolType,
            status: 'running',
            arguments: event.arguments ?? null,
          },
        })
        return
      }
      if (event.type === 'tool_call_end') {
        generation.toolName = null
        generation.phase = segmentsText(generation.segments) ? 'generating' : 'thinking'
        // callId 配对、原地更新；完成顺序不影响卡片位置
        const tool = matchTool(generation.segments, event.callId)
        if (tool) {
          tool.status = event.status
          tool.durationMs = event.durationMs
          tool.summary = event.summary
          tool.result = event.result ?? null
        }
        return
      }
      if (event.type === 'approval_request') {
        // 审批卡片进入时间线；等待决议期间阶段标记为 approval
        generation.phase = 'approval'
        generation.segments.push({
          kind: 'approval',
          approval: {
            requestId: event.requestId,
            callId: event.callId,
            toolName: event.toolName,
            displayName: event.displayName || event.toolName,
            toolType: event.toolType,
            status: 'pending',
            arguments: event.arguments ?? null,
          },
        })
        return
      }
      if (event.type === 'approval_result') {
        generation.phase = segmentsText(generation.segments) ? 'generating' : 'thinking'
        // requestId 配对、原地更新审批卡片终态
        const approval = matchApproval(generation.segments, event.requestId)
        if (approval) {
          approval.status = event.decision
          approval.deciding = false
        }
        return
      }
      if (event.type === 'context_compaction_start') {
        generation.phase = 'compacting'
        generation.segments.push({
          kind: 'compact',
          compact: {
            compactionId: event.compactionId,
            status: 'running',
            reason: event.reason,
            estimatedTokensBefore: event.estimatedTokens,
            summaryTokens: event.summaryTokens,
          },
        })
        return
      }
      if (event.type === 'context_compaction_end') {
        generation.phase = segmentsText(generation.segments) ? 'generating' : 'thinking'
        // compactionId 配对、原地更新压缩卡片
        const compact = matchCompact(generation.segments, event.compactionId)
        if (compact) {
          compact.status = event.status
          compact.estimatedTokensAfter = event.estimatedTokensAfter
          compact.processedMessages = event.processedMessages
          compact.keptMessages = event.keptMessages
          compact.summaryTokens = event.summaryTokens
          compact.batches = event.batches
          compact.modelCalls = event.modelCalls
          compact.durationMs = event.durationMs
          compact.error = event.error ?? null
        }
        return
      }
      if (event.type === 'context_trim') {
        // 备用裁剪没有起止配对，直接追加一张卡片
        generation.segments.push({
          kind: 'compact',
          compact: {
            compactionId: null,
            status: 'trimmed',
            reason: event.reason,
            estimatedTokensBefore: event.estimatedTokensBefore,
            estimatedTokensAfter: event.estimatedTokensAfter,
            keptMessages: event.keptMessages,
            droppedMessages: event.droppedMessages,
          },
        })
        return
      }
      if (event.type === 'run_end') {
        this.settleGeneration(sessionId, event.status, event.message)
        if (event.message) {
          this.upsertMessage(event.message)
        }
        if (event.error) {
          this.generationErrors[sessionId] = {
            message: event.error.message,
            hint: event.error.hint,
            errorType: event.error.type,
            content: event.content,
            savedMessage: event.message,
          }
        }
        this.finishGeneration(sessionId)
      }
    },

    /** 运行收尾：未完成卡片按状态标记，时间线挂到保存的消息或留作会话末尾卡片。 */
    settleGeneration(sessionId: number, status: string, message: ChatMessage | null): void {
      const generation = this.generations[sessionId]
      if (!generation) return
      finalizeActivities(generation.segments, status === 'cancelled' ? 'cancelled' : 'unknown')
      if (!hasActivity(generation.segments)) return
      if (message) {
        this.toolTimelines[message.id] = generation.segments
      } else {
        // 没有保存消息（取消且无正文 / 保存失败）：卡片单独留在会话末尾
        this.orphanSegments[sessionId] = generation.segments.filter(
          (segment): segment is Exclude<GenerationSegment, TextSegment> => segment.kind !== 'text',
        )
      }
    },

    /** 生成结束的收尾：清生成状态、中断流、按最新时间刷新列表排序。 */
    finishGeneration(sessionId: number): void {
      delete this.generations[sessionId]
      this.abortStream(sessionId)
      void this.fetchSessions()
    },

    /** 事件流意外断开：以后端状态为准，仍在生成则退避重连。 */
    async handleStreamFailure(sessionId: number): Promise<void> {
      const generation = this.generations[sessionId]
      if (!generation) return
      const attempts = (this.reconnectAttempts[sessionId] ?? 0) + 1
      this.reconnectAttempts[sessionId] = attempts
      if (attempts > MAX_RECONNECT_ATTEMPTS) {
        this.markGenerationUnknown(sessionId)
        return
      }
      try {
        const result = await apiGetMessages(sessionId)
        this.syncSession(result.session)
        if (result.generation) {
          // 重连后先收到快照（带完整时间线），本地旧状态会被覆盖恢复
          this.subscribeEvents(sessionId)
        } else {
          // 生成已结束：用后端最终状态补齐（终态事件可能没收到）
          this.seedTimelines(result.messages)
          for (const message of result.messages) {
            this.upsertMessage(message)
          }
          this.finishGeneration(sessionId)
        }
      } catch {
        // 拉取也失败：无法确认执行结果，标记“状态未知”
        this.markGenerationUnknown(sessionId)
      }
    },

    /** 连接中断且无法确认结果：工具卡片标记“状态未知”，保留在会话末尾。 */
    markGenerationUnknown(sessionId: number): void {
      const generation = this.generations[sessionId]
      if (!generation) return
      finalizeActivities(generation.segments, 'unknown')
      if (hasActivity(generation.segments)) {
        this.orphanSegments[sessionId] = generation.segments.filter(
          (segment): segment is Exclude<GenerationSegment, TextSegment> => segment.kind !== 'text',
        )
      }
      this.generationErrors[sessionId] = {
        message: '与后端的事件流连接中断',
        hint: '回复仍在后台生成；请刷新页面或稍后重新打开该会话查看结果',
        errorType: 'network',
        content: segmentsText(generation.segments),
        savedMessage: null,
      }
      this.finishGeneration(sessionId)
    },

    /** 按 id 去重地插入或替换消息，保证重放事件不产生重复气泡。 */
    upsertMessage(message: ChatMessage): void {
      const list = this.messagesBySession[message.sessionId] ?? []
      const index = list.findIndex((item) => item.id === message.id)
      if (index >= 0) {
        list.splice(index, 1, message)
      } else {
        let insertAt = list.length
        while (insertAt > 0 && list[insertAt - 1]!.seq > message.seq) {
          insertAt -= 1
        }
        list.splice(insertAt, 0, message)
      }
      this.messagesBySession[message.sessionId] = list
    },
  },
})
