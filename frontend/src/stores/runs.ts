/** 运行记录状态管理：列表（分页 / 筛选）、详情时间线与详细载荷按需加载。
 *
 * 列表只保存摘要行；详情与详细载荷按 Run 缓存，互不预加载。
 */

import { defineStore } from 'pinia'

import {
  getRunDetail as apiGetRunDetail,
  getRunEventDetail as apiGetRunEventDetail,
  listRuns as apiListRuns,
  type RunDetail,
  type RunEventDetailResult,
  type RunItem,
  type RunListQuery,
  type RunStatus,
} from '../api/runs'

/** 列表筛选：空串表示全部状态 */
export type StatusFilter = '' | RunStatus

interface RunsState {
  items: RunItem[]
  total: number
  page: number
  pageSize: number
  statusFilter: StatusFilter
  agentFilter: number | null
  loading: boolean
  loaded: boolean
  error: string
  /** 详情缓存（按 runId） */
  details: Record<number, RunDetail>
  detailLoading: Record<number, boolean>
  /** 详细载荷缓存：`${runId}:${eventId}` → payload */
  eventDetails: Record<string, RunEventDetailResult['payload']>
}

export const useRunsStore = defineStore('runs', {
  state: (): RunsState => ({
    items: [],
    total: 0,
    page: 1,
    pageSize: 20,
    statusFilter: '',
    agentFilter: null,
    loading: false,
    loaded: false,
    error: '',
    details: {},
    detailLoading: {},
    eventDetails: {},
  }),
  getters: {
    totalPages(state): number {
      return Math.max(1, Math.ceil(state.total / state.pageSize))
    },
  },
  actions: {
    async fetchRuns(): Promise<void> {
      this.loading = true
      this.error = ''
      try {
        const query: RunListQuery = { page: this.page, pageSize: this.pageSize }
        if (this.statusFilter !== '') query.status = this.statusFilter
        if (this.agentFilter !== null) query.agentId = this.agentFilter
        const result = await apiListRuns(query)
        this.items = result.items
        this.total = result.total
        this.page = result.page
        this.pageSize = result.pageSize
        this.loaded = true
      } catch (error) {
        this.error = error instanceof Error ? error.message : '加载运行列表失败'
      } finally {
        this.loading = false
      }
    },

    /** 切换筛选条件：回到第一页再拉取 */
    async setFilters(status: StatusFilter, agentId: number | null): Promise<void> {
      this.statusFilter = status
      this.agentFilter = agentId
      this.page = 1
      await this.fetchRuns()
    },

    async goToPage(page: number): Promise<void> {
      const target = Math.min(Math.max(1, page), this.totalPages)
      if (target === this.page && this.loaded) return
      this.page = target
      await this.fetchRuns()
    },

    async refresh(): Promise<void> {
      await this.fetchRuns()
    },

    /** 详情：安全事件 + 摘要（详细载荷不在此时加载） */
    async fetchDetail(runId: number): Promise<RunDetail> {
      const cached = this.details[runId]
      if (cached) return cached
      this.detailLoading[runId] = true
      try {
        const detail = await apiGetRunDetail(runId)
        this.details[runId] = detail
        return this.details[runId]!
      } finally {
        this.detailLoading[runId] = false
      }
    },

    /** 详细载荷：工具完整结果 / 最终正文片段（按需、受控加载） */
    async loadEventDetail(runId: number, eventId: number) {
      const key = `${runId}:${eventId}`
      const cached = this.eventDetails[key]
      if (cached) return cached
      const result = await apiGetRunEventDetail(runId, eventId)
      this.eventDetails[key] = {
        result: result.payload.result,
        content: result.payload.content,
        truncated: result.payload.truncated,
      }
      return this.eventDetails[key]!
    },
  },
})
