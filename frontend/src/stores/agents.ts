import { defineStore } from 'pinia'

import {
  createAgent as apiCreateAgent,
  deleteAgent as apiDeleteAgent,
  getAgent as apiGetAgent,
  listAgents,
  setDefaultAgent as apiSetDefaultAgent,
  updateAgent as apiUpdateAgent,
  type AgentDetail,
  type AgentItem,
  type AgentPayload,
} from '../api/agents'

interface AgentsState {
  agents: AgentItem[]
  loading: boolean
  loaded: boolean
}

export const useAgentsStore = defineStore('agents', {
  state: (): AgentsState => ({
    agents: [],
    loading: false,
    loaded: false,
  }),
  getters: {
    /** 默认 Agent 排在最前，与后端列表顺序一致；这里仅提供便捷访问。 */
    defaultAgent: (state: AgentsState): AgentItem | undefined =>
      state.agents.find((agent) => agent.isDefault),
  },
  actions: {
    async fetchAgents(): Promise<void> {
      this.loading = true
      try {
        this.agents = await listAgents()
        this.loaded = true
      } finally {
        this.loading = false
      }
    },
    /** 编辑表单加载：绑定可用状态以详情接口实时解析为准。 */
    async loadAgent(id: number): Promise<AgentDetail> {
      return apiGetAgent(id)
    },
    /** 新增后重新拉取列表，保证默认 Agent 置顶与状态一致。 */
    async createAgent(payload: AgentPayload): Promise<AgentItem> {
      const created = await apiCreateAgent(payload)
      await this.fetchAgents()
      return created
    },
    async updateAgent(id: number, payload: AgentPayload): Promise<AgentItem> {
      const updated = await apiUpdateAgent(id, payload)
      await this.fetchAgents()
      return updated
    },
    async removeAgent(id: number, newDefaultId?: number): Promise<void> {
      await apiDeleteAgent(id, newDefaultId)
      await this.fetchAgents()
    },
    async setDefault(id: number): Promise<void> {
      await apiSetDefaultAgent(id)
      await this.fetchAgents()
    },
  },
})
