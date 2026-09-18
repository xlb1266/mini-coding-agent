/** MCP 管理接口定义。 */

import { del, get, post, put } from './request'

/**
 * 后端对已保存环境变量值的统一掩码；编辑时回传该值表示“保留原值”。
 * 与后端 app/schemas/mcp.py 的 ENV_VALUE_MASK 保持一致。
 */
export const ENV_VALUE_MASK = '••••••••'

/** 一个 MCP 工具参数的说明。 */
export interface McpToolParam {
  name: string
  type: string
  required: boolean
  description: string
}

/** 测试连接发现的一个工具；只发现工具，不执行工具。 */
export interface McpTool {
  name: string
  description: string
  params: McpToolParam[]
}

/** 最近一次测试的状态；stale 表示配置已变更，待重新测试。 */
export type McpTestStatus = 'untested' | 'success' | 'failed' | 'stale'

/** 一条 MCP Server 配置；环境变量值只以掩码出现，接口永不返回原文。 */
export interface McpServerItem {
  id: number
  name: string
  description: string
  command: string
  args: string[]
  env: Record<string, string>
  enabled: boolean
  lastTestStatus: McpTestStatus
  lastTestAt: string | null
  lastTestErrorType: string | null
  lastTestError: string | null
  lastTestToolCount: number | null
  lastTestTools: McpTool[] | null
  createdAt: string
  updatedAt: string
}

/** 新增/编辑表单提交的载荷；env 值为掩码时后端保留已保存的原值。 */
export interface McpServerPayload {
  name: string
  description: string
  command: string
  args: string[]
  env: Record<string, string>
}

export interface McpTestResult {
  status: 'success' | 'failed'
  testedAt: string
  toolCount?: number
  tools?: McpTool[]
  errorType?: string
  message?: string
  hint?: string
}

export function listMcpServers(): Promise<McpServerItem[]> {
  return get<McpServerItem[]>('/api/mcp-servers')
}

export function createMcpServer(payload: McpServerPayload): Promise<McpServerItem> {
  return post<McpServerItem>('/api/mcp-servers', payload)
}

export function updateMcpServer(id: number, payload: McpServerPayload): Promise<McpServerItem> {
  return put<McpServerItem>(`/api/mcp-servers/${id}`, payload)
}

export function setMcpServerEnabled(id: number, enabled: boolean): Promise<McpServerItem> {
  return put<McpServerItem>(`/api/mcp-servers/${id}/enabled`, { enabled })
}

export function deleteMcpServer(id: number): Promise<void> {
  return del<void>(`/api/mcp-servers/${id}`)
}

/** 测试连接：实际启动 Server 完成握手并读取工具列表，结束后进程会被清理。 */
export function testMcpServer(id: number): Promise<McpTestResult> {
  return post<McpTestResult>(`/api/mcp-servers/${id}/test`)
}
