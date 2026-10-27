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
  /** 该次回复的过程轨迹（思考段与工具卡片段）；纯文字回复为 null */
  timeline?: GenerationTimelineItem[] | null
}

/** 阶段化开发（阶段十五）：finish_stage 上报的阶段摘要（脱敏后）。 */
export interface StageSummary {
  stageName: string
  summary: string
  handoffPath: string
  nextStep: string
}

export interface ChatSessionSummary {
  id: number
  title: string
  agentId: number | null
  agentName: string | null
  isGenerating: boolean
  /** True 表示上一阶段已完成（finish_stage），等待用户验收 */
  awaitingAcceptance?: boolean
  /** 上一阶段的交接摘要；无则为 null/缺省 */
  stageSummary?: StageSummary | null
  /** 所属阶段项目 id（阶段化开发 v2）；普通会话为 null/缺省 */
  projectId?: number | null
  /** 项目里的角色：plan（规划）/ stage（阶段）/ summary（汇总） */
  projectRole?: 'plan' | 'stage' | 'summary' | null
  /** 本会话负责的项目阶段序号（1-based）；规划与汇总会话为 null */
  stageIndex?: number | null
  /** 会话级审批档位覆盖（输入框切换）；null = 跟随 Agent 配置 */
  approvalLevel?: ApprovalLevel | null
  /** 会话级自主档位覆盖；null = 跟随 Agent 配置 */
  autonomyMode?: AutonomyMode | null
  /** 会话级模型覆盖；null = 跟随 Agent 绑定的模型 */
  modelId?: number | null
  createdAt: string
  updatedAt: string
}

/** 审批档位（与 agents 模块一致）：off 不审批 / standard 高风险审批 / strict 全部审批。 */
export type ApprovalLevel = 'off' | 'standard' | 'strict'

/** 自主档位（与 agents 模块一致）：stage_confirm 阶段确认 / autonomous 高度自主。 */
export type AutonomyMode = 'stage_confirm' | 'autonomous'

/** 聊天输入框的执行档位预设：审批档位 × 自主档位的组合。 */
export interface ModePreset {
  key: string
  label: string
  description: string
  approvalLevel: ApprovalLevel
  autonomyMode: AutonomyMode
}

/** 三个执行档位预设（与后端治理模型对应）；展示顺序按自由度从高到低。 */
export const MODE_PRESETS: ModePreset[] = [
  {
    key: 'autonomous_full',
    label: '高度自主',
    description: '完全访问，无审批操作。',
    approvalLevel: 'off',
    autonomyMode: 'autonomous',
  },
  {
    key: 'stage_confirm',
    label: '阶段确认模式',
    description: '做完一个阶段生成交接文档并停下，等你验收后继续。',
    approvalLevel: 'off',
    autonomyMode: 'stage_confirm',
  },
  {
    key: 'confirm_changes',
    label: '变更前确认',
    description: '执行命令、修改文件前先请求人工审批。',
    approvalLevel: 'standard',
    autonomyMode: 'stage_confirm',
  },
]

/** 根据审批档位与自主档位组合找出对应的预设；组合不匹配任何预设时返回 null。 */
export function matchModePreset(
  approvalLevel: ApprovalLevel | null | undefined,
  autonomyMode: AutonomyMode | null | undefined,
): ModePreset | null {
  return (
    MODE_PRESETS.find(
      (preset) => preset.approvalLevel === approvalLevel && preset.autonomyMode === autonomyMode,
    ) ?? null
  )
}

/** 上下文占用量的一个分类（估算值）。 */
export interface ContextUsageCategory {
  key: string
  label: string
  tokens: number
  percent: number
}

/** 会话上下文占用量（估算口径，不含下次输入与未连接的 MCP 工具定义）。 */
export interface ContextUsage {
  estimatedTokens: number
  contextLength: number
  availableInputTokens: number
  categories: ContextUsageCategory[]
  hasMcpTools: boolean
}

/** 阶段项目（阶段化开发 v2）：阶段清单与推进状态。 */
export interface StageProjectStage {
  name: string
  goal: string
  status: 'pending' | 'in_progress' | 'done'
}

/** 项目状态机：待开始 → 推进中 → 待汇总 → 汇总中 → 已完成 */
export type StageProjectStatus = 'awaiting_start' | 'in_progress' | 'awaiting_summary' | 'summarizing' | 'completed'

export interface StageProject {
  id: number
  title: string
  agentId: number | null
  agentName: string
  /** 项目钉住的工作区目录（各阶段会话一致） */
  workspaceDir: string
  stages: StageProjectStage[]
  currentStage: number
  status: StageProjectStatus
  /** 阶段序号（1-based）→ 该阶段会话 id */
  stageSessions: Record<number, number>
  planSessionId: number | null
  summarySessionId: number | null
  createdAt: string
  updatedAt: string
}

/** 项目推进操作（开始 / 下一阶段 / 生成汇总）的结果：新会话已创建并自动发出首条消息。 */
export interface StageProjectOperationResult {
  project: StageProject
  session: ChatSessionSummary
}

/** 进行中的生成状态：thinking 尚无正文，generating 正文输出中，tooling 工具执行中，compacting 压缩上下文中，approval 等待审批。 */
export type GenerationPhase = 'thinking' | 'generating' | 'tooling' | 'compacting' | 'approval'

/** 上下文压缩卡片的状态；trimmed 为备用裁剪（只影响本次请求）。 */
export type CompactStatus = 'running' | 'success' | 'partial' | 'error' | 'cancelled' | 'unknown' | 'trimmed'

/** 展示时间线条目：正文段、思考段、工具卡片段或上下文压缩段（与后端结构一致）。
 *  Token 数字均为估算值，与模型接口返回的实际用量（usage）区分。 */
export type GenerationTimelineItem =
  | { kind: 'text'; text: string }
  | {
      /** 模型思考段（推理模型下发；仅展示，不进入保存的回复正文） */
      kind: 'reasoning'
      text: string
      durationMs?: number | null
    }
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
  /** 运行开始时间（epoch 毫秒）：展示“处理中 Xs”，刷新后仍准确 */
  startedAt?: number | null
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

/** 运行结束状态；stage_complete 为阶段确认模式下的正常收尾（等待验收），
 *  plan_submitted 为规划会话提交阶段计划后的正常收尾（等待确认开始）。 */
export type RunStatus =
  | 'completed'
  | 'stage_complete'
  | 'plan_submitted'
  | 'cancelled'
  | 'error'
  | 'rounds_exhausted'

/** 阶段计划（run_end.plan）：submit_stage_plan 的上报内容。 */
export interface StagePlanPayload {
  title: string
  stages: { name: string; goal: string }[]
}

/** SSE 事件流的事件载荷，与后端 Agent Runtime 的统一运行事件协议一致。 */
export type ChatEvent =
  | {
      type: 'snapshot'
      runId: string | null
      phase: GenerationPhase
      content: string
      agentName: string
      toolName: string | null
      /** 运行开始时间（epoch 毫秒） */
      startedAt?: number | null
      timeline?: GenerationTimelineItem[] | null
      /** 当前仍在等待决议的审批请求（快照恢复审批卡片） */
      pendingApprovals?: ApprovalRequestInfo[] | null
    }
  | { type: 'run_start'; runId: string; seq: number; agentName: string; startedAt?: number }
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
      /** 思考增量（推理模型的 reasoning_content / reasoning）；仅展示，不保存进回复正文 */
      type: 'reasoning_delta'
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
      /** 阶段收尾信息（stage_complete 时携带 finish_stage 的摘要） */
      stage?: StageSummary | null
      /** 阶段计划（plan_submitted 时携带 submit_stage_plan 的上报内容） */
      plan?: StagePlanPayload | null
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

/** 切换会话的执行档位（审批档位 × 自主档位组合），只影响当前会话。 */
export function selectChatMode(
  sessionId: number,
  approvalLevel: ApprovalLevel,
  autonomyMode: AutonomyMode,
): Promise<ChatSessionSummary> {
  return put<ChatSessionSummary>(`/api/chat/sessions/${sessionId}/mode`, {
    approvalLevel,
    autonomyMode,
  })
}

/** 切换会话使用的模型（会话级覆盖，只影响当前会话）。 */
export function selectChatModel(sessionId: number, modelId: number): Promise<ChatSessionSummary> {
  return put<ChatSessionSummary>(`/api/chat/sessions/${sessionId}/model`, { modelId })
}

/** 会话上下文占用量（估算口径）。 */
export function getChatContextUsage(sessionId: number): Promise<ContextUsage> {
  return get<ContextUsage>(`/api/chat/sessions/${sessionId}/context-usage`)
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

/** 验收通过（阶段化开发）：清除会话的阶段等待状态，之后可继续下一阶段。 */
export function acceptChatStage(sessionId: number): Promise<ChatSessionSummary> {
  return post<ChatSessionSummary>(`/api/chat/sessions/${sessionId}/stage/accept`, {})
}

/** 阶段项目详情：计划清单、推进状态与各会话映射。 */
export function getStageProject(projectId: number): Promise<StageProject> {
  return get<StageProject>(`/api/chat/projects/${projectId}`)
}

/** 确认计划并开始阶段 1：后端自动新建阶段会话并发出开工消息。 */
export function startStageProject(projectId: number): Promise<StageProjectOperationResult> {
  return post<StageProjectOperationResult>(`/api/chat/projects/${projectId}/start`, {})
}

/** 进入下一阶段：后端自动新建阶段会话，首条消息带上计划与上一阶段交接。 */
export function nextStageProject(projectId: number): Promise<StageProjectOperationResult> {
  return post<StageProjectOperationResult>(`/api/chat/projects/${projectId}/next`, {})
}

/** 生成项目汇总：后端新建独立汇总会话并发汇总要求。 */
export function summarizeStageProject(projectId: number): Promise<StageProjectOperationResult> {
  return post<StageProjectOperationResult>(`/api/chat/projects/${projectId}/summary`, {})
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
