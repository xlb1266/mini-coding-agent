/** 工具管理接口定义。 */

import { get, post, put } from './request'

/** 一个工具参数的说明，用于详情页展示与试运行表单。 */
export interface ToolParam {
  name: string
  type: 'string' | 'integer' | 'boolean'
  required: boolean
  description: string
  example?: string | null
}

/** 一个已注册的本地工具。 */
export interface ToolItem {
  id: number
  /** 稳定标识，执行层按它绑定具体实现（如 builtin_shell）。 */
  key: string
  name: string
  description: string
  parameters: ToolParam[]
  enabled: boolean
  isBuiltin: boolean
  createdAt: string
  updatedAt: string
}

/** 统一执行结果：执行失败的原因都在 error 里，type 说明失败类别。 */
export interface ToolExecuteResult {
  ok: boolean
  result?: Record<string, unknown> | null
  error?: {
    type: string
    message: string
    hint?: string | null
    fields?: Record<string, string> | null
  } | null
}

export function listTools(): Promise<ToolItem[]> {
  return get<ToolItem[]>('/api/tools')
}

export function getTool(id: number): Promise<ToolItem> {
  return get<ToolItem>(`/api/tools/${id}`)
}

/** 启停对所有工具可用；name/description 仅对非内置工具生效。 */
export function updateTool(
  id: number,
  payload: { enabled?: boolean; name?: string; description?: string },
): Promise<ToolItem> {
  return put<ToolItem>(`/api/tools/${id}`, payload)
}

/** 统一执行入口：被拦截/超时/失败等结果都以 HTTP 200 的 ok=false 返回。 */
export function executeTool(id: number, params: Record<string, unknown>): Promise<ToolExecuteResult> {
  return post<ToolExecuteResult>(`/api/tools/${id}/execute`, { params })
}
