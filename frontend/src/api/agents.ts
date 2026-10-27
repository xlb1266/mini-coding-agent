/** Agent 管理接口定义。 */

import { del, get, post, put } from './request'

/** Agent 引用的模型摘要：名称与标识，前端不展示内部外键。 */
export interface AgentModelInfo {
  id: number
  displayName: string
  modelIdentifier: string
}

/**
 * 一条能力绑定（工具 / Skill / MCP Server）。
 * 资源已不存在时 name 为 null；已停用或已不存在时 available 为 false。
 * refKey：工具为 key，Skill 为目录名，MCP Server 为十进制 id 字符串。
 */
export interface AgentBindingRef {
  refKey: string
  name: string | null
  description: string
  enabled: boolean | null
  available: boolean
}

/** 审批档位（阶段十五）：off 不审批 / standard 高风险动作审批 / strict 全部工具审批。 */
export type ApprovalLevel = 'off' | 'standard' | 'strict'

/** 自主档位（阶段十五）：stage_confirm 阶段确认（做完即停等人验收）/ autonomous 高度自主。 */
export type AutonomyMode = 'stage_confirm' | 'autonomous'

/** 一条 Agent 配置；绑定数量按实际保存的绑定统计，含已停用能力。 */
export interface AgentItem {
  id: number
  name: string
  description: string
  model: AgentModelInfo | null
  promptVersion: number
  maxRounds: number
  /** 工作区目录：空串 = 使用全局默认目录；文件工具沙箱与 Shell 工作目录跟随它 */
  workspaceDir: string
  /** 审批档位（取代旧布尔开关） */
  approvalLevel: ApprovalLevel
  /** 自主档位：决定是否提供 finish_stage 与阶段工作流协议 */
  autonomyMode: AutonomyMode
  /** Shell 命令白名单：换行/逗号分隔的命令前缀，命中免审批（黑名单仍生效） */
  shellWhitelist: string
  /** 上下文压缩配置：长会话超过估算容量时自动整理摘要 */
  autoCompact: boolean
  /** 触发比例（百分比整数 10–95）：估算输入达到可用输入容量的该比例时触发 */
  compactThreshold: number
  /** 保留最近对话轮数（1–50）；一轮 = 用户消息及其回复 */
  compactKeepRounds: number
  /** 摘要目标长度（估算 token，100–32000；运行时结合模型容量收窄） */
  compactSummaryTokens: number
  isDefault: boolean
  toolCount: number
  skillCount: number
  mcpCount: number
  createdAt: string
  updatedAt: string
}

/** Agent 完整配置：编辑表单用，含系统提示词与逐条绑定信息。 */
export interface AgentDetail extends AgentItem {
  systemPrompt: string
  toolBindings: AgentBindingRef[]
  skillBindings: AgentBindingRef[]
  mcpBindings: AgentBindingRef[]
}

/** 新增/编辑表单提交的载荷。 */
export interface AgentPayload {
  name: string
  description: string
  systemPrompt: string
  modelId: number
  maxRounds: number
  workspaceDir: string
  approvalLevel: ApprovalLevel
  autonomyMode: AutonomyMode
  shellWhitelist: string
  autoCompact: boolean
  compactThreshold: number
  compactKeepRounds: number
  compactSummaryTokens: number
  toolKeys: string[]
  skillDirNames: string[]
  mcpServerIds: number[]
}

export function listAgents(): Promise<AgentItem[]> {
  return get<AgentItem[]>('/api/agents')
}

export function getAgent(id: number): Promise<AgentDetail> {
  return get<AgentDetail>(`/api/agents/${id}`)
}

export function createAgent(payload: AgentPayload): Promise<AgentItem> {
  return post<AgentItem>('/api/agents', payload)
}

export function updateAgent(id: number, payload: AgentPayload): Promise<AgentItem> {
  return put<AgentItem>(`/api/agents/${id}`, payload)
}

export function setDefaultAgent(id: number): Promise<AgentItem> {
  return put<AgentItem>(`/api/agents/${id}/default`)
}

/** 删除 Agent；删除默认 Agent 且还有其他 Agent 时，需携带 newDefaultId 完成切换。 */
export function deleteAgent(id: number, newDefaultId?: number): Promise<void> {
  const query = newDefaultId === undefined ? '' : `?new_default_id=${newDefaultId}`
  return del<void>(`/api/agents/${id}${query}`)
}
