import { defineStore } from 'pinia'

import {
  createMcpServer as apiCreateServer,
  deleteMcpServer as apiDeleteServer,
  listMcpServers,
  setMcpServerEnabled as apiSetEnabled,
  testMcpServer as apiTestServer,
  updateMcpServer as apiUpdateServer,
  type McpServerItem,
  type McpServerPayload,
  type McpTestResult,
} from '../api/mcp'

interface McpState {
  servers: McpServerItem[]
  loading: boolean
  loaded: boolean
}

export const useMcpStore = defineStore('mcp', {
  state: (): McpState => ({
    servers: [],
    loading: false,
    loaded: false,
  }),
  actions: {
    async fetchServers(): Promise<void> {
      this.loading = true
      try {
        this.servers = await listMcpServers()
        this.loaded = true
      } finally {
        this.loading = false
      }
    },
    async createServer(payload: McpServerPayload): Promise<McpServerItem> {
      const created = await apiCreateServer(payload)
      await this.fetchServers()
      return created
    },
    async updateServer(id: number, payload: McpServerPayload): Promise<McpServerItem> {
      const updated = await apiUpdateServer(id, payload)
      await this.fetchServers()
      return updated
    },
    async removeServer(id: number): Promise<void> {
      await apiDeleteServer(id)
      await this.fetchServers()
    },
    async setEnabled(id: number, enabled: boolean): Promise<void> {
      await apiSetEnabled(id, enabled)
      await this.fetchServers()
    },
    /** 测试连接；成功或失败都会在后端落库，这里返回后重新拉取列表。 */
    async testConnection(id: number): Promise<McpTestResult> {
      try {
        return await apiTestServer(id)
      } finally {
        await this.fetchServers()
      }
    },
  },
})
