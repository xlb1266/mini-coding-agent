/** 评测页面纯展示逻辑：状态元数据、统计与文本格式化。
 *
 * 状态只用图标 + 文字 + 色调区分（与运行记录页一致）；平均分为
 * null 时展示"暂无评分"（不把失败用例按零分混入平均分）。
 */

import type { CaseStatus, TaskStatus } from '../api/evaluation'

export type StatusTone = 'success' | 'warning' | 'danger' | 'accent' | 'muted'

export interface StatusMeta {
  label: string
  icon: string
  tone: StatusTone
}

export const TASK_STATUS_META: Record<TaskStatus, StatusMeta> = {
  pending: { label: '待运行', icon: '○', tone: 'muted' },
  running: { label: '运行中', icon: '◔', tone: 'accent' },
  completed: { label: '已完成', icon: '✓', tone: 'success' },
  interrupted: { label: '已中断', icon: '◑', tone: 'warning' },
}

export const CASE_STATUS_META: Record<CaseStatus, StatusMeta> = {
  pending: { label: '待执行', icon: '○', tone: 'muted' },
  executing: { label: '执行中', icon: '◔', tone: 'accent' },
  scoring: { label: '评分中', icon: '◔', tone: 'accent' },
  passed: { label: '通过', icon: '✓', tone: 'success' },
  failed: { label: '未通过', icon: '✕', tone: 'danger' },
  exec_failed: { label: '执行失败', icon: '✕', tone: 'danger' },
  scoring_failed: { label: '评分失败', icon: '✕', tone: 'warning' },
}

export function taskStatusMeta(status: string): StatusMeta {
  return TASK_STATUS_META[status as TaskStatus] ?? { label: status, icon: '?', tone: 'muted' }
}

export function caseStatusMeta(status: string): StatusMeta {
  return CASE_STATUS_META[status as CaseStatus] ?? { label: status, icon: '?', tone: 'muted' }
}

/** 得分展示：无评分显示 — */
export function formatScore(score: number | null | undefined): string {
  if (score === null || score === undefined) return '—'
  return Number.isInteger(score) ? String(score) : score.toFixed(1)
}

/** 平均分展示：无有效评分时显示"暂无评分" */
export function formatAverage(score: number | null | undefined): string {
  if (score === null || score === undefined) return '暂无评分'
  return score.toFixed(1)
}

/** 评分调用耗时（与运行耗时同一格式）；null 表示未评分 */
export function formatScoringDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return '—'
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

/** 进度摘要：已完成数 / 总用例数（已完成含失败用例） */
export function progressText(completed: number, total: number): string {
  return `${completed} / ${total}`
}

/** 用例表单校验：问题必填；期望答案与评分要求至少一项 */
export function validateCaseForm(form: {
  question: string
  expectedAnswer: string
  scoringRequirements: string
}): Record<string, string> {
  const errors: Record<string, string> = {}
  if (!form.question.trim()) errors.question = '请填写用户问题'
  if (!form.expectedAnswer.trim() && !form.scoringRequirements.trim()) {
    errors.expectedAnswer = '期望答案与评分要求至少填写一项'
  }
  return errors
}
