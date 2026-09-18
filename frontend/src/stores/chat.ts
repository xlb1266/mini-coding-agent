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
 * （toolTimelines），无保存消息时以孤儿卡片留在会话末尾（orphanSegments）；
 * 两者只在页面内存中保留，刷新后不恢复（留到运行记录阶段）。
 *
 * 阶段十：时间线增加压缩卡片段（kind: 'compact'）——上下文压缩起止用
 * compactionId 配对、原地更新，备用裁剪直接追加；压缩期间的阶段为
 * compacting。卡片只展示估算数字与原因，不包含摘要正文。
 */

import { defineStore } from 'pinia'

import {
  createChatSession as apiCreateSession,
  getChatMessages as apiGetMessages,
  listChatSessions,
  openChatEventStream,
  selectChatAgent as apiSelectAgent,
  sendChatMessage as apiSendMessage,
  stopChatGeneration as apiStopGeneration,
  type ChatEvent,
  type ChatGeneration,
  type ChatMessage,
  type ChatSessionSummary,
  type CompactStatus,
  type GenerationPhase,
  type GenerationTimelineItem,
  type RunError,
  type ToolResultPayload,
  type ToolType,
} from '../api/chat'

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

/** 生成时间线的三种段：正文段、工具卡片段与压缩卡片段，按事件顺序交错。 */
export interface TextSegment {
  kind: 'text'
  text: string
}

export interface ToolSegment {
  kind: 'tool'
  tool: ToolActivity
}

export interface CompactSegment {
  kind: 'compact'
  compact: CompactActivity
}

export type GenerationSegment = TextSegment | ToolSegment | CompactSegment

/** 进行中的生成：正文与工具卡片按事件顺序交错保留。 */
export interface RunningGeneration {
  /** 运行标识：校验事件归属，旧运行的迟到事件不落入当前时间线 */
  runId: string | null
  phase: GenerationPhase
  agentName: string
  toolName: string | null
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

/** 收尾扫描：仍在执行的卡片标记为取消或状态未知，不得停留在“执行中”或误标成功。 */
function finalizeActivities(segments: GenerationSegment[], status: ToolActivityStatus): void {
  for (const segment of segments) {
    if (segment.kind === 'tool' && segment.tool.status === 'running') {
      segment.tool.status = status
    }
    if (segment.kind === 'compact' && segment.compact.status === 'running') {
      segment.compact.status = status
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
    segments: segmentsFromTimeline(generation.timeline, generation.content),
  }
}

function generationFromSnapshot(event: Extract<ChatEvent, { type: 'snapshot' }>): RunningGeneration {
  return {
    runId: event.runId,
    phase: event.phase,
    agentName: event.agentName,
    toolName: event.toolName,
    segments: segmentsFromTimeline(event.timeline, event.content),
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

    /** 切换会话的 Agent（只影响后续请求）。 */
    async changeAgent(agentId: number): Promise<void> {
      const sessionId = this.activeSessionId
      if (sessionId === null) {
        return
      }
      if (this.generations[sessionId]) {
        throw new Error('当前会话正在生成回复，请等待完成或先停止')
      }
      const updated = await apiSelectAgent(sessionId, agentId)
      this.syncSession(updated)
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
        if (generation) generation.runId = event.runId
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
          (segment): segment is ToolSegment | CompactSegment => segment.kind !== 'text',
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
          (segment): segment is ToolSegment | CompactSegment => segment.kind !== 'text',
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
