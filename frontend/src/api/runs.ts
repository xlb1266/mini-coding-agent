/** 运行记录模块接口定义：列表（分页 / 筛选）、详情时间线与详细载荷。
 *
 * 事件与聊天页 SSE 转发的是同一批 Runtime 事件（同一套名称 / 状态 /
 * 摘要规则）；text_delta 流式增量不落库，大段载荷（工具完整结果、最终
 * 正文片段）由详情端点按需加载。
 */

import { get } from './request'
import type { RunError, RunUsage, ToolResultPayload } from './chat'

export type RunStatus = 'running' | 'completed' | 'partial' | 'cancelled' | 'failed'

export interface RunItem {
  id: number
  runUid: string
  sessionId: number | null
  agentId: number | null
  agentName: string
  modelName: string
  modelIdentifier: string
  status: RunStatus
  finishReason: string | null
  errorSummary: string | null
  userInputPreview: string
  startedAt: string
  finishedAt: string | null
  durationMs: number | null
  modelCalls: number
  toolCalls: number
  rejectedToolCalls: number
  firstOutputMs: number | null
  tokenUsage: RunUsage | null
  tokensIncomplete: boolean
}

export interface RunListResult {
  items: RunItem[]
  total: number
  page: number
  pageSize: number
}

/** 详情时间线里一个事件的载荷（持久化的 Runtime 事件；字段按事件类型取用） */
export interface RunEventPayload {
  type: string
  seq?: number
  agentName?: string
  round?: number
  callId?: string
  purpose?: string
  status?: string
  reason?: string
  durationMs?: number | null
  usage?: RunUsage | null
  toolName?: string
  toolType?: string
  displayName?: string
  serverName?: string | null
  arguments?: Record<string, unknown> | null
  summary?: string
  result?: ToolResultPayload | null
  hasDetail?: boolean
  compactionId?: string | null
  error?: RunError | null
  contentPreview?: string
  roundsUsed?: number
  modelCalls?: number
  toolCalls?: number
  rejectedToolCalls?: number
  firstOutputMs?: number | null
  tokenUsage?: (RunUsage & { incomplete?: boolean }) | null
  estimatedTokens?: number
  thresholdTokens?: number
  availableTokens?: number
  estimatedTokensBefore?: number
  estimatedTokensAfter?: number | null
  processedMessages?: number
  keptMessages?: number
  compactableMessages?: number
  summaryTokens?: number | null
  boundarySeq?: number | null
  batches?: number
  droppedMessages?: number
  startedAt?: number
}

export interface RunEvent {
  id: number
  seq: number
  eventType: string
  payload: RunEventPayload
  /** 是否存在可按需加载的详细载荷 */
  hasDetail: boolean
}

export interface RunDetail {
  run: RunItem
  events: RunEvent[]
}

/** 详细载荷：tool_call_end 为 { result }，run_end 为 { content, truncated } */
export interface RunEventDetailResult {
  eventId: number
  seq: number
  eventType: string
  payload: {
    result?: ToolResultPayload
    content?: string
    truncated?: boolean
  }
}

export interface RunListQuery {
  status?: RunStatus
  agentId?: number
  page?: number
  pageSize?: number
}

export function listRuns(query: RunListQuery = {}): Promise<RunListResult> {
  const params: Record<string, string> = {}
  if (query.status) params.status = query.status
  if (query.agentId !== undefined) params.agentId = String(query.agentId)
  if (query.page !== undefined) params.page = String(query.page)
  if (query.pageSize !== undefined) params.pageSize = String(query.pageSize)
  const search = new URLSearchParams(params).toString()
  return get<RunListResult>(`/api/runs${search ? `?${search}` : ''}`)
}

export function getRunDetail(runId: number): Promise<RunDetail> {
  return get<RunDetail>(`/api/runs/${runId}`)
}

export function getRunEventDetail(runId: number, eventId: number): Promise<RunEventDetailResult> {
  return get<RunEventDetailResult>(`/api/runs/${runId}/events/${eventId}/detail`)
}
