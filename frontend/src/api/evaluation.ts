/** Agent 评测模块接口定义：数据集 / 用例管理、评测任务创建 / 启动 / 详情。
 *
 * 任务与用例结果均为后端快照：创建任务时复制用例内容与 Agent 配置，
 * 后续编辑数据集不影响已有任务。任务执行为后端进程内后台顺序执行，
 * 前端通过轮询详情获取进度（刷新或关闭页面不影响执行）。
 */

import { del, get, post, put } from './request'

// ---- 状态 ----

export type TaskStatus = 'pending' | 'running' | 'completed' | 'interrupted'

/** 用例状态：pending 待执行 / executing 执行中 / scoring 评分中；
 * passed 通过 / failed 未通过 / exec_failed 执行失败 / scoring_failed 评分失败 */
export type CaseStatus =
  | 'pending'
  | 'executing'
  | 'scoring'
  | 'passed'
  | 'failed'
  | 'exec_failed'
  | 'scoring_failed'

// ---- 数据集与用例 ----

export interface EvalDatasetItem {
  id: number
  name: string
  description: string
  caseCount: number
  /** 是否为内置示例数据集（按稳定标识判断） */
  isExample: boolean
  createdAt: string
  updatedAt: string
}

export interface EvalCaseItem {
  id: number
  datasetId: number
  question: string
  expectedAnswer: string
  scoringRequirements: string
  createdAt: string
  updatedAt: string
}

export interface EvalDatasetDetail extends EvalDatasetItem {
  cases: EvalCaseItem[]
}

export interface DatasetPayload {
  name: string
  description: string
}

export interface EvalCasePayload {
  question: string
  expectedAnswer: string
  scoringRequirements: string
}

// ---- 评测任务 ----

export interface TaskStats {
  totalCases: number
  /** 已进入终态的用例数（包含失败用例） */
  completedCases: number
  passedCount: number
  failedCount: number
  execFailedCount: number
  scoringFailedCount: number
  pendingCases: number
  executingCases: number
  scoringCases: number
  /** 仅统计获得合法评分的用例；无有效评分时为 null（展示"暂无评分"） */
  averageScore: number | null
}

export interface EvalTaskItem {
  id: number
  datasetId: number
  datasetName: string
  agentId: number
  agentName: string
  scorerModelId: number
  scorerModelName: string
  scorerModelIdentifier: string
  /** 0-100；得分 >= 该值记为通过 */
  passScore: number
  /** 关闭时不向目标 Agent 提供工具定义，运行时也拒绝工具执行 */
  allowTools: boolean
  status: TaskStatus
  stats: TaskStats
  createdAt: string
  startedAt: string | null
  finishedAt: string | null
}

export interface EvalCaseResult {
  id: number
  caseOrder: number
  question: string
  expectedAnswer: string
  scoringRequirements: string
  status: CaseStatus
  /** 目标 Agent 的实际回答（执行失败时保留已有输出，但不参与评分） */
  actualAnswer: string | null
  score: number | null
  reason: string | null
  /** 关联的目标 Agent Run（运行详情页按数字 id 打开） */
  runId: number | null
  runUid: string | null
  execReason: string | null
  execError: string | null
  scoreError: string | null
  /** 评分调用自身开销（与目标 Agent Run 指标分开记录） */
  scoringDurationMs: number | null
  scoringTotalTokens: number | null
  scoringAttempts: number
}

export interface EvalTaskDetail extends EvalTaskItem {
  /** Agent 配置快照（名称、提示词版本、生效的工具 / Skill / MCP 集合等） */
  agentConfig: Record<string, unknown>
  results: EvalCaseResult[]
}

export interface TaskPayload {
  agentId: number
  datasetId: number
  scorerModelId: number
  passScore: number
  allowTools: boolean
}

// ---- 数据集 ----

export function listDatasets(): Promise<EvalDatasetItem[]> {
  return get<EvalDatasetItem[]>('/api/evaluations/datasets')
}

export function getDataset(id: number): Promise<EvalDatasetDetail> {
  return get<EvalDatasetDetail>(`/api/evaluations/datasets/${id}`)
}

export function createDataset(payload: DatasetPayload): Promise<EvalDatasetItem> {
  return post<EvalDatasetItem>('/api/evaluations/datasets', payload)
}

export function updateDataset(id: number, payload: DatasetPayload): Promise<EvalDatasetItem> {
  return put<EvalDatasetItem>(`/api/evaluations/datasets/${id}`, payload)
}

export function deleteDataset(id: number): Promise<void> {
  return del<void>(`/api/evaluations/datasets/${id}`)
}

export function createCase(datasetId: number, payload: EvalCasePayload): Promise<EvalCaseItem> {
  return post<EvalCaseItem>(`/api/evaluations/datasets/${datasetId}/cases`, payload)
}

export function updateCase(
  datasetId: number,
  caseId: number,
  payload: EvalCasePayload,
): Promise<EvalCaseItem> {
  return put<EvalCaseItem>(`/api/evaluations/datasets/${datasetId}/cases/${caseId}`, payload)
}

export function deleteCase(datasetId: number, caseId: number): Promise<void> {
  return del<void>(`/api/evaluations/datasets/${datasetId}/cases/${caseId}`)
}

// ---- 任务 ----

export function listTasks(): Promise<EvalTaskItem[]> {
  return get<EvalTaskItem[]>('/api/evaluations/tasks')
}

export function getTask(id: number): Promise<EvalTaskDetail> {
  return get<EvalTaskDetail>(`/api/evaluations/tasks/${id}`)
}

export function createTask(payload: TaskPayload): Promise<EvalTaskItem> {
  return post<EvalTaskItem>('/api/evaluations/tasks', payload)
}

/** 启动待运行任务；重复启动 / 已完成 / 已中断返回 409 */
export function startTask(id: number): Promise<EvalTaskItem> {
  return post<EvalTaskItem>(`/api/evaluations/tasks/${id}/start`)
}
