/** 聊天模块接口定义与 SSE 事件流订阅。
 *
 * 密钥只存在于后端：前端拿到的任何数据都不含 API Key。
 */

import { ApiError, get, post, put } from './request'

export type ChatRole = 'user' | 'assistant'
export type MessageStatus = 'completed' | 'incomplete'

export interface ChatMessage {
  id: number
  sessionId: number
  role: ChatRole
  content: string
  status: MessageStatus
  agentId: number | null
  agentName: string | null
  seq: number
  createdAt: string
}

export interface ChatSessionSummary {
  id: number
  title: string
  agentId: number | null
  agentName: string | null
  isGenerating: boolean
  createdAt: string
  updatedAt: string
}

/** 进行中的生成状态：thinking 尚无正文，generating 正文输出中，tooling 工具执行中，compacting 压缩上下文中，approval 等待审批。 */
export type GenerationPhase = 'thinking' | 'generating' | 'tooling' | 'compacting' | 'approval'

/** 上下文压缩卡片的状态；trimmed 为备用裁剪（只影响本次请求）。 */
export type CompactStatus = 'running' | 'success' | 'partial' | 'error' | 'cancelled' | 'unknown' | 'trimmed'

/** 展示时间线条目：正文段、工具卡片段或上下文压缩段（与后端结构一致）。
 *  Token 数字均为估算值，与模型接口返回的实际用量（usage）区分。 */
export type GenerationTimelineItem =
  | { kind: 'text'; text: string }
  | {
      kind: 'tool'
      callId: string
      toolName: string
      toolType: ToolType
      displayName: string
      serverName: string | null
      status: 'running' | 'success' | 'error' | 'cancelled'
      durationMs?: number
      summary?: string
      arguments?: Record<string, unknown> | null
      result?: ToolResultPayload | null
    }
  | {
      kind: 'approval'
      requestId: string
      callId: string
      toolName: string
      toolType: ToolType
      displayName: string
      serverName?: string | null
      arguments?: Record<string, unknown> | null
      status: ApprovalDecisionStatus
    }
  | {
      kind: 'compact'
      compactionId: string | null
      status: CompactStatus
      reason?: string
      estimatedTokensBefore?: number | null
      estimatedTokensAfter?: number | null
      processedMessages?: number | null
      keptMessages?: number | null
      summaryTokens?: number | null
      batches?: number | null
      modelCalls?: number | null
      durationMs?: number | null
      droppedMessages?: number | null
      error?: RunError | null
    }

export interface ChatGeneration {
  phase: GenerationPhase
  content: string
  agentName: string
  /** 正在执行的工具展示名（仅 tooling 阶段） */
  toolName?: string | null
  /** 展示时间线（重连/打开会话时恢复工具卡片与顺序）；刚启动时为空 */
  timeline?: GenerationTimelineItem[] | null
  /** 当前仍在等待用户决议的审批请求（刷新后恢复审批卡片） */
  pendingApprovals?: ApprovalRequestInfo[] | null
}

/** 一条挂起 / 已决定的审批请求信息（与后端 approval_request 事件载荷一致）。 */
export interface ApprovalRequestInfo {
  requestId: string
  callId: string
  toolName: string
  toolType: ToolType
  displayName: string
  serverName?: string | null
  arguments?: Record<string, unknown> | null
}

export interface ChatMessagesResult {
  session: ChatSessionSummary
  messages: ChatMessage[]
  generation: ChatGeneration | null
}

export interface ChatSendResult {
  session: ChatSessionSummary
  userMessage: ChatMessage
  generation: ChatGeneration
}

export interface ChatStopResult {
  outcome: 'cancelled' | 'done' | 'error' | 'not_running'
  message: ChatMessage | null
  content: string
  errorMessage: string | null
  hint: string | null
  errorType: string | null
}

/** 运行事件的 Token 用量；null 表示上游未返回（未知）；cachedTokens 为输入中缓存命中的部分（已含在输入内，不重复相加） */
export interface RunUsage {
  promptTokens: number | null
  completionTokens: number | null
  totalTokens: number | null
  cachedTokens?: number | null
}

export type ToolType = 'builtin' | 'mcp' | 'skill' | 'unknown'

/** 工具调用结束状态；cancelled 表示运行取消时被中断，结果未产出 */
export type ToolEndStatus = 'success' | 'error' | 'cancelled'

/** 审批卡片的决定状态：pending 为等待用户决议，其余为终态。 */
export type ApprovalDecisionStatus = 'pending' | 'approved' | 'rejected' | 'timeout' | 'cancelled'

/** 工具卡片展开视图的整理后结果；truncated 为真时 text 是截断后的前段 */
export interface ToolResultPayload {
  text: string
  truncated: boolean
  totalLength: number
}

/** 运行结束的错误信息；正常结束为 null */
export interface RunError {
  type: string
  message: string
  hint: string | null
}

export type RunStatus = 'completed' | 'cancelled' | 'error' | 'rounds_exhausted'

/** SSE 事件流的事件载荷，与后端 Agent Runtime 的统一运行事件协议一致。 */
export type ChatEvent =
  | {
      type: 'snapshot'
      runId: string | null
      phase: GenerationPhase
      content: string
      agentName: string
      toolName: string | null
      timeline?: GenerationTimelineItem[] | null
      /** 当前仍在等待决议的审批请求（快照恢复审批卡片） */
      pendingApprovals?: ApprovalRequestInfo[] | null
    }
  | { type: 'run_start'; runId: string; seq: number; agentName: string }
  | {
      type: 'model_request_start'
      runId: string
      seq: number
      round: number
      callId: string
      /** 用途标记：上下文压缩的摘要请求；普通对话不携带 */
      purpose?: 'context_compaction'
    }
  | {
      type: 'text_delta'
      runId: string
      seq: number
      round: number
      callId: string
      text: string
    }
  | {
      type: 'model_request_end'
      runId: string
      seq: number
      round: number
      callId: string
      purpose?: 'context_compaction'
      status: 'success' | 'error' | 'cancelled'
      durationMs: number
      usage: RunUsage | null
    }
  | {
      type: 'tool_call_start'
      runId: string
      seq: number
      round: number
      callId: string
      toolName: string
      toolType: ToolType
      displayName: string
      serverName: string | null
      arguments: Record<string, unknown> | null
    }
  | {
      type: 'tool_call_end'
      runId: string
      seq: number
      round: number
      callId: string
      toolName: string
      toolType: ToolType
      status: ToolEndStatus
      durationMs: number
      summary: string
      result: ToolResultPayload | null
    }
  | {
      /** 工具执行审批请求：等待用户批准/拒绝（开启审批的 Agent 命中审批范围时下发） */
      type: 'approval_request'
      runId: string
      seq: number
      round: number
      requestId: string
      callId: string
      toolName: string
      toolType: ToolType
      displayName: string
      serverName: string | null
      arguments: Record<string, unknown> | null
    }
  | {
      /** 审批决定：approved / rejected / timeout / cancelled */
      type: 'approval_result'
      runId: string
      seq: number
      round: number
      requestId: string
      callId: string
      toolName: string
      toolType: ToolType
      decision: Exclude<ApprovalDecisionStatus, 'pending'>
    }
  | {
      type: 'run_end'
      runId: string
      seq: number
      status: RunStatus
      reason: string
      roundsUsed: number
      content: string
      message: ChatMessage | null
      error: RunError | null
    }
  | {
      /** 上下文压缩开始：估算输入达到触发比例（Token 均为估算值） */
      type: 'context_compaction_start'
      runId: string
      seq: number
      round: number
      compactionId: string
      reason: string
      estimatedTokens: number
      thresholdTokens: number
      availableTokens: number
      compactableMessages: number
      summaryTokens: number
      boundarySeq: number | null
      summaryTargetTokens: number
    }
  | {
      /** 上下文压缩结束：成功 / 部分完成 / 失败 / 取消（不含摘要正文） */
      type: 'context_compaction_end'
      runId: string
      seq: number
      round: number
      compactionId: string
      status: 'success' | 'partial' | 'error' | 'cancelled'
      durationMs: number
      estimatedTokensBefore: number
      estimatedTokensAfter: number | null
      processedMessages: number
      keptMessages: number
      batches: number
      modelCalls: number
      summaryTokens: number | null
      boundarySeq: number | null
      usage: RunUsage | null
      error: RunError | null
    }
  | {
      /** 备用裁剪：容量不足时按完整消息组从最早内容开始减少（只影响本次请求） */
      type: 'context_trim'
      runId: string
      seq: number
      round: number
      reason: string
      estimatedTokensBefore: number
      estimatedTokensAfter: number
      availableTokens: number
      droppedMessages: number
      droppedHistoryTurns: number
      droppedToolGroups: number
      keptMessages: number
    }
  | { type: 'idle' }

export function listChatSessions(): Promise<ChatSessionSummary[]> {
  return get<ChatSessionSummary[]>('/api/chat/sessions')
}

/** 新建会话；不传 agentId 时后端使用默认 Agent。 */
export function createChatSession(agentId?: number): Promise<ChatSessionSummary> {
  return post<ChatSessionSummary>('/api/chat/sessions', agentId === undefined ? {} : { agentId })
}

/** 打开会话：全部消息 + 进行中的生成状态（含刷新后恢复）。 */
export function getChatMessages(sessionId: number): Promise<ChatMessagesResult> {
  return get<ChatMessagesResult>(`/api/chat/sessions/${sessionId}/messages`)
}

/** 切换会话当前选择的 Agent。 */
export function selectChatAgent(sessionId: number, agentId: number): Promise<ChatSessionSummary> {
  return put<ChatSessionSummary>(`/api/chat/sessions/${sessionId}/agent`, { agentId })
}

/** 发送用户消息；成功后回复生成已在后台开始。 */
export function sendChatMessage(sessionId: number, content: string): Promise<ChatSendResult> {
  return post<ChatSendResult>(`/api/chat/sessions/${sessionId}/messages`, { content })
}

/** 停止生成；后端会保存已生成的部分正文。 */
export function stopChatGeneration(sessionId: number): Promise<ChatStopResult> {
  return post<ChatStopResult>(`/api/chat/sessions/${sessionId}/stop`)
}

/** 查询会话当前挂起的工具执行审批请求。 */
export function listChatApprovals(sessionId: number): Promise<ApprovalRequestInfo[]> {
  return get<{ approvals: ApprovalRequestInfo[] }>(`/api/chat/sessions/${sessionId}/approvals`).then(
    (result) => result.approvals,
  )
}

/** 对一次审批做出决议；action 为 approve（批准）或 reject（拒绝）。 */
export function decideChatApproval(
  sessionId: number,
  requestId: string,
  action: 'approve' | 'reject',
): Promise<{ requestId: string; action: string; outcome: string }> {
  return post(`/api/chat/sessions/${sessionId}/approvals/${requestId}/decision`, { action })
}

/**
 * 订阅会话的 SSE 事件流。
 *
 * 终态事件（done/error/cancelled）到达后服务端会关闭连接，本函数随即
 * 正常返回；调用方也可通过 signal 主动中断（停止接收输出）。网络异常
 * 时抛出 ApiError，是否重连由调用方决定。
 */
export async function openChatEventStream(
  sessionId: number,
  onEvent: (event: ChatEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  let response: Response
  try {
    response = await fetch(`/api/chat/sessions/${sessionId}/events`, {
      headers: { Accept: 'text/event-stream' },
      signal,
    })
  } catch {
    if (signal.aborted) return
    throw new ApiError('事件流连接失败，请确认后端服务已启动', 0)
  }

  if (!response.ok || !response.body) {
    throw new ApiError(`事件流连接失败（HTTP ${response.status}）`, response.status)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) return
      buffer += decoder.decode(value, { stream: true })
      // 事件以空行分隔；解析出 data: 行并合并（规范允许多行 data）
      for (;;) {
        const separator = buffer.indexOf('\n\n')
        if (separator < 0) break
        const block = buffer.slice(0, separator)
        buffer = buffer.slice(separator + 2)
        const data = block
          .split('\n')
          .filter((line) => line.startsWith('data:'))
          .map((line) => line.slice(5).trim())
          .join('\n')
        if (!data) continue // 心跳注释或非数据行
        try {
          onEvent(JSON.parse(data) as ChatEvent)
        } catch {
          // 跳过无法解析的行，不让坏行中断整条流
        }
      }
    }
  } catch {
    if (signal.aborted) return
    throw new ApiError('事件流连接中断', 0)
  }
}
