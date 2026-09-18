import { defineStore } from 'pinia'

import { getHealth } from '../api/health'

export type BackendStatus = 'checking' | 'ok' | 'down'

interface AppState {
  backendStatus: BackendStatus
}

export const useAppStore = defineStore('app', {
  state: (): AppState => ({
    backendStatus: 'checking',
  }),
  actions: {
    /** 探测后端 /api/health，结果供聊天页状态展示使用。 */
    async checkBackend(): Promise<void> {
      try {
        const health = await getHealth()
        this.backendStatus = health.status === 'ok' ? 'ok' : 'down'
      } catch {
        this.backendStatus = 'down'
      }
    },
  },
})
