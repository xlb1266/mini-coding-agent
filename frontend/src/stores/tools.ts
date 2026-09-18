import { defineStore } from 'pinia'

import {
  executeTool as apiExecuteTool,
  getTool as apiGetTool,
  listTools,
  updateTool as apiUpdateTool,
  type ToolExecuteResult,
  type ToolItem,
} from '../api/tools'

interface ToolsState {
  tools: ToolItem[]
  loading: boolean
  loaded: boolean
}

export const useToolsStore = defineStore('tools', {
  state: (): ToolsState => ({
    tools: [],
    loading: false,
    loaded: false,
  }),
  getters: {
    /** 内置工具数量，供列表页统计展示。 */
    builtinCount: (state: ToolsState): number => state.tools.filter((tool) => tool.isBuiltin).length,
  },
  actions: {
    async fetchTools(): Promise<void> {
      this.loading = true
      try {
        this.tools = await listTools()
        this.loaded = true
      } finally {
        this.loading = false
      }
    },
    /** 启停后重新拉取列表，保证状态一致。 */
    async setEnabled(id: number, enabled: boolean): Promise<ToolItem> {
      const updated = await apiUpdateTool(id, { enabled })
      await this.fetchTools()
      return updated
    },
    /** 详情页加载：始终从后端取最新状态，避免缓存里启停状态过期。 */
    async loadTool(id: number): Promise<ToolItem> {
      return apiGetTool(id)
    },
    async execute(id: number, params: Record<string, unknown>): Promise<ToolExecuteResult> {
      return apiExecuteTool(id, params)
    },
  },
})
