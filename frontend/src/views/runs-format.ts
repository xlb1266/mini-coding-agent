/** 运行详情时间线的纯展示逻辑：事件 → 步骤、状态与指标格式化。
 *
 * 时间线直接消费持久化的 Runtime 事件（与聊天页 SSE 同一批事件），
 * 工具卡片与压缩卡片复用聊天页的展示类型（ToolActivity / CompactActivity）
 * 与同一组件，保证同一次调用在两个页面显示一致的名称、状态与摘要。
 */

import type { CompactActivity, ApprovalActivity, ToolActivity } from '../stores/chat'
import type { RunEvent, RunItem, RunStatus } from '../api/runs'
import type { ApprovalDecisionStatus, CompactStatus, RunError, RunUsage, ToolType } from '../api/chat'

// ---- 状态元数据（图标 + 文字，不只靠颜色区分） ----

export type StatusTone = 'success' | 'warning' | 'danger' | 'accent' | 'muted'

export const RUN_STATUS_META: Record<RunStatus, { label: string; icon: string; tone: StatusTone }> = {
  running: { label: '运行中', icon: '◔', tone: 'accent' },
  completed: { label: '成功', icon: '✓', tone: 'success' },
  partial: { label: '部分完成', icon: '◑', tone: 'warning' },
  cancelled: { label: '已取消', icon: '○', tone: 'warning' },
  failed: { label: '失败', icon: '✕', tone: 'danger' },
}

const STEP_STATUS_META: Record<StepStatus, { label: string; icon: string }> = {
  running: { label: '执行中', icon: '◔' },
  success: { label: '完成', icon: '✓' },
  error: { label: '失败', icon: '✕' },
  cancelled: { label: '已取消', icon: '○' },
  unknown: { label: '状态未知', icon: '?' },
}

export type StepStatus = 'running' | 'success' | 'error' | 'cancelled' | 'unknown'

export function stepStatusMeta(status: StepStatus): { label: string; icon: string } {
  return STEP_STATUS_META[status] ?? STEP_STATUS_META.unknown!
}

/** 运行结束事件的状态值域（Runtime 终态），与单步状态不同 */
export type RunEndStatus = 'completed' | 'rounds_exhausted' | 'cancelled' | 'error'

const RUN_END_STATUS_META: Record<string, { label: string; icon: string }> = {
  completed: { label: '成功', icon: '✓' },
  rounds_exhausted: { label: '部分完成', icon: '◑' },
  cancelled: { label: '已取消', icon: '○' },
  error: { label: '失败', icon: '✕' },
}

export function runEndStatusMeta(status: string): { label: string; icon: string } {
  return RUN_END_STATUS_META[status] ?? { label: '状态未知', icon: '?' }
}

/** 结束原因 → 简短中文说明（状态与原因分开展示） */
export const FINISH_REASON_LABELS: Record<string, string> = {
  model_finished: '模型完成回复',
  max_rounds: '达到最大执行轮数',
  cancelled: '用户停止运行',
  model_error: '模型服务错误',
  config_error: '运行配置不可用',
  mcp_start_failed: 'MCP Server 启动失败',
  runtime_error: '运行内部异常',
  context_overflow: '上下文容量不足',
  interrupted: '运行中断（后端重启或异常退出）',
}

export function finishReasonLabel(reason: string | null | undefined): string {
  if (!reason) return '—'
  return FINISH_REASON_LABELS[reason] ?? reason
}

// ---- 指标格式化 ----

export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return '—'
  if (ms < 1000) return `${ms}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
  const minutes = Math.floor(ms / 60_000)
  const seconds = Math.round((ms % 60_000) / 1000)
  return seconds > 0 ? `${minutes}分${seconds}秒` : `${minutes}分`
}

/** 单次模型请求的用量（输入 / 输出 / 缓存命中）；未知项跳过 */
export function formatUsage(usage: RunUsage | null | undefined): string {
  if (!usage) return ''
  const parts: string[] = []
  if (usage.promptTokens !== null && usage.promptTokens !== undefined) parts.push(`输入 ${usage.promptTokens}`)
  if (usage.completionTokens !== null && usage.completionTokens !== undefined) parts.push(`输出 ${usage.completionTokens}`)
  if (parts.length === 0) return ''
  const cached = usage.cachedTokens
  if (cached !== null && cached !== undefined && cached > 0) parts.push(`缓存命中 ${cached}`)
  return `${parts.join(' / ')} token`
}

/** 整次运行的 Token 用量：未知显示"未知"，部分请求未返回显示"统计不完整"，不显示 0 */
export function formatRunTokens(run: Pick<RunItem, 'tokenUsage' | 'tokensIncomplete'>): string {
  const usage = run.tokenUsage
  if (!usage) return '未知'
  const text = formatUsage(usage)
  if (!text) return '未知'
  return run.tokensIncomplete ? `${text}（统计不完整）` : text
}

/** 首个正文输出耗时；无正文输出时明确标注 */
export function formatFirstOutput(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return '无正文输出'
  return formatDuration(ms)
}

// ---- 时间线步骤 ----

export interface ModelStep {
  kind: 'model'
  callId: string
  round?: number
  /** 摘要生成请求标记为上下文压缩（不占用执行轮数） */
  purpose?: string
  status: StepStatus
  durationMs?: number | null
  usage?: RunUsage | null
}

export interface ToolStep {
  kind: 'tool'
  eventId: number
  callId: string
  /** 复用聊天页的展示类型：ToolCallCard 直接渲染同一规则 */
  tool: ToolActivity
  /** 结果保存在详细载荷里，需按需加载后填入 tool.result */
  hasDetail: boolean
}

export interface CompactStep {
  kind: 'compact'
  /** 复用聊天页的展示类型：CompactionCard 直接渲染 */
  compact: CompactActivity
}

export interface RunStartStep {
  kind: 'run-start'
  agentName: string
}

export interface ApprovalStep {
  kind: 'approval'
  /** 复用聊天页的展示类型：ApprovalCard 只读渲染（终态展示，不可再决议） */
  approval: ApprovalActivity
}

export interface RunEndStep {
  kind: 'run-end'
  eventId: number
  /** Runtime 终态：completed / rounds_exhausted / cancelled / error */
  status: string
  reason: string
  error?: RunError | null
  contentPreview?: string
  /** 详细载荷加载后置为 false（按钮消失） */
  hasDetail: boolean
  /** 完整正文片段仍被截断（详细载荷本身超长） */
  truncated?: boolean
  roundsUsed?: number
}

export type RunTimelineStep = RunStartStep | ModelStep | ToolStep | ApprovalStep | CompactStep | RunEndStep

function asStepStatus(value: unknown): StepStatus {
  return value === 'success' || value === 'error' || value === 'cancelled' || value === 'running'
    ? value
    : 'unknown'
}

function asCompactStatus(value: unknown): CompactStatus {
  return typeof value === 'string' && value
    ? (value as CompactStatus)
    : 'unknown'
}

/**
 * 把按真实事件顺序（seq）排列的安全事件组装成时间线步骤：
 * 模型 / 工具 / 压缩步骤用调用标识配对起止事件（不靠时间戳或名称），
 * 未配对到结束事件的步骤保持"执行中"（运行仍在进行或事件缺失）。
 */
export function buildTimeline(events: RunEvent[]): RunTimelineStep[] {
  const steps: RunTimelineStep[] = []
  const models = new Map<string, ModelStep>()
  const tools = new Map<string, ToolStep>()
  const compacts = new Map<string, CompactStep>()
  const approvals = new Map<string, ApprovalStep>()

  for (const event of events) {
    const payload = event.payload
    switch (event.eventType) {
      case 'run_start': {
        steps.push({ kind: 'run-start', agentName: payload.agentName ?? '' })
        break
      }
      case 'model_request_start': {
        const step: ModelStep = {
          kind: 'model',
          callId: payload.callId ?? '',
          round: payload.round,
          purpose: payload.purpose,
          status: 'running',
        }
        steps.push(step)
        models.set(step.callId, step)
        break
      }
      case 'model_request_end': {
        const step = models.get(payload.callId ?? '')
        if (step && step.status === 'running') {
          step.status = asStepStatus(payload.status)
          step.durationMs = payload.durationMs ?? null
          step.usage = payload.usage ?? null
        }
        break
      }
      case 'tool_call_start': {
        const callId = payload.callId ?? ''
        const tool: ToolActivity = {
          callId,
          toolName: payload.toolName ?? '',
          displayName: payload.displayName || payload.toolName || '',
          serverName: payload.serverName ?? null,
          toolType: (payload.toolType as ToolType) ?? 'unknown',
          status: 'running',
          arguments: payload.arguments ?? null,
        }
        const step: ToolStep = { kind: 'tool', eventId: event.id, callId, tool, hasDetail: false }
        steps.push(step)
        tools.set(callId, step)
        break
      }
      case 'tool_call_end': {
        const step = tools.get(payload.callId ?? '')
        if (step && step.tool.status === 'running') {
          step.tool.status = asStepStatus(payload.status)
          step.tool.durationMs = payload.durationMs ?? undefined
          step.tool.summary = payload.summary
          // 详细载荷挂在结束事件上：配对时把事件指向更新为结束事件
          step.eventId = event.id
          step.hasDetail = event.hasDetail || Boolean(payload.hasDetail)
        }
        break
      }
      case 'context_compaction_start': {
        const compactionId = payload.compactionId ?? ''
        const compact: CompactActivity = {
          compactionId,
          status: 'running',
          reason: payload.reason,
          estimatedTokensBefore: payload.estimatedTokens ?? null,
          summaryTokens: payload.summaryTokens ?? null,
        }
        const step: CompactStep = { kind: 'compact', compact }
        steps.push(step)
        compacts.set(compactionId, step)
        break
      }
      case 'context_compaction_end': {
        const step = compacts.get(payload.compactionId ?? '')
        if (step && step.compact.status === 'running') {
          step.compact.status = asCompactStatus(payload.status)
          step.compact.estimatedTokensAfter = payload.estimatedTokensAfter ?? null
          step.compact.processedMessages = payload.processedMessages ?? null
          step.compact.keptMessages = payload.keptMessages ?? null
          step.compact.summaryTokens = payload.summaryTokens ?? null
          step.compact.batches = payload.batches ?? null
          step.compact.modelCalls = payload.modelCalls ?? null
          step.compact.durationMs = payload.durationMs ?? null
          step.compact.error = payload.error ?? null
        }
        break
      }
      case 'context_trim': {
        steps.push({
          kind: 'compact',
          compact: {
            compactionId: null,
            status: 'trimmed',
            reason: payload.reason,
            estimatedTokensBefore: payload.estimatedTokensBefore ?? null,
            estimatedTokensAfter: payload.estimatedTokensAfter ?? null,
            keptMessages: payload.keptMessages ?? null,
            droppedMessages: payload.droppedMessages ?? null,
          },
        })
        break
      }
      case 'approval_request': {
        const requestId = payload.requestId ?? ''
        const approval: ApprovalActivity = {
          requestId,
          callId: payload.callId ?? '',
          toolName: payload.toolName ?? '',
          displayName: payload.displayName || payload.toolName || '',
          toolType: (payload.toolType as ToolType) ?? 'unknown',
          status: 'pending',
          arguments: payload.arguments ?? null,
        }
        const step: ApprovalStep = { kind: 'approval', approval }
        steps.push(step)
        approvals.set(requestId, step)
        break
      }
      case 'approval_result': {
        const step = approvals.get(payload.requestId ?? '')
        if (step && step.approval.status === 'pending') {
          const decision = payload.decision
          step.approval.status = (
            decision === 'approved' || decision === 'rejected' || decision === 'timeout'
              ? decision
              : 'cancelled'
          ) as ApprovalDecisionStatus
        }
        break
      }
      case 'run_end': {
        steps.push({
          kind: 'run-end',
          eventId: event.id,
          status: payload.status ?? 'error',
          reason: payload.reason ?? '',
          error: payload.error ?? null,
          contentPreview: payload.contentPreview,
          hasDetail: event.hasDetail || Boolean(payload.hasDetail),
          roundsUsed: payload.roundsUsed,
        })
        break
      }
      default:
        break
    }
  }
  // 回放兜底：运行中断等导致审批缺少决定事件时，卡片不停留在“等待审批”
  for (const step of approvals.values()) {
    if (step.approval.status === 'pending') step.approval.status = 'cancelled'
  }
  return steps
}
