/** 评测模块状态管理：数据集（含用例）与评测任务。
 *
 * 列表保存摘要行；数据集用例与任务结果按 id 缓存详情。任务在后台
 * 顺序执行，页面通过轮询刷新运行中任务的进度与结果。
 */

import { defineStore } from 'pinia'

import {
  createCase as apiCreateCase,
  createDataset as apiCreateDataset,
  createTask as apiCreateTask,
  deleteCase as apiDeleteCase,
  deleteDataset as apiDeleteDataset,
  getDataset as apiGetDataset,
  getTask as apiGetTask,
  listDatasets as apiListDatasets,
  listTasks as apiListTasks,
  startTask as apiStartTask,
  updateCase as apiUpdateCase,
  updateDataset as apiUpdateDataset,
  type DatasetPayload,
  type EvalCaseItem,
  type EvalCasePayload,
  type EvalDatasetDetail,
  type EvalDatasetItem,
  type EvalTaskDetail,
  type EvalTaskItem,
  type TaskPayload,
} from '../api/evaluation'

interface EvaluationState {
  datasets: EvalDatasetItem[]
  datasetsLoading: boolean
  datasetsLoaded: boolean
  datasetsError: string
  datasetDetails: Record<number, EvalDatasetDetail>

  tasks: EvalTaskItem[]
  tasksLoading: boolean
  tasksLoaded: boolean
  tasksError: string
  taskDetails: Record<number, EvalTaskDetail>
  taskActionError: string
}

export const useEvaluationStore = defineStore('evaluation', {
  state: (): EvaluationState => ({
    datasets: [],
    datasetsLoading: false,
    datasetsLoaded: false,
    datasetsError: '',
    datasetDetails: {},

    tasks: [],
    tasksLoading: false,
    tasksLoaded: false,
    tasksError: '',
    taskDetails: {},
    taskActionError: '',
  }),

  getters: {
    /** 是否存在运行中的任务（页面据此轮询刷新） */
    hasRunningTask: (state: EvaluationState): boolean =>
      state.tasks.some((task) => task.status === 'running'),

    runningTaskIds: (state: EvaluationState): number[] =>
      state.tasks.filter((task) => task.status === 'running').map((task) => task.id),
  },

  actions: {
    // ---- 数据集 ----

    async fetchDatasets(): Promise<void> {
      this.datasetsLoading = true
      this.datasetsError = ''
      try {
        this.datasets = await apiListDatasets()
        this.datasetsLoaded = true
      } catch (error) {
        this.datasetsError = error instanceof Error ? error.message : '加载数据集失败'
      } finally {
        this.datasetsLoading = false
      }
    },

    /** 数据集详情（含用例）；force 为 true 时忽略缓存重新拉取 */
    async fetchDataset(id: number, force = false): Promise<EvalDatasetDetail> {
      if (!force) {
        const cached = this.datasetDetails[id]
        if (cached) return cached
      }
      const detail = await apiGetDataset(id)
      this.datasetDetails = { ...this.datasetDetails, [id]: detail }
      return detail
    },

    async createDataset(payload: DatasetPayload): Promise<EvalDatasetItem> {
      const created = await apiCreateDataset(payload)
      await this.fetchDatasets()
      return created
    },

    async updateDataset(id: number, payload: DatasetPayload): Promise<void> {
      await apiUpdateDataset(id, payload)
      await Promise.all([this.fetchDatasets(), this.fetchDataset(id, true)])
    },

    async removeDataset(id: number): Promise<void> {
      await apiDeleteDataset(id)
      const details = { ...this.datasetDetails }
      delete details[id]
      this.datasetDetails = details
      await this.fetchDatasets()
    },

    async createCase(datasetId: number, payload: EvalCasePayload): Promise<EvalCaseItem> {
      const created = await apiCreateCase(datasetId, payload)
      await Promise.all([this.fetchDatasets(), this.fetchDataset(datasetId, true)])
      return created
    },

    async updateCase(
      datasetId: number,
      caseId: number,
      payload: EvalCasePayload,
    ): Promise<void> {
      await apiUpdateCase(datasetId, caseId, payload)
      await Promise.all([this.fetchDatasets(), this.fetchDataset(datasetId, true)])
    },

    async removeCase(datasetId: number, caseId: number): Promise<void> {
      await apiDeleteCase(datasetId, caseId)
      await Promise.all([this.fetchDatasets(), this.fetchDataset(datasetId, true)])
    },

    // ---- 任务 ----

    async fetchTasks(): Promise<void> {
      this.tasksLoading = true
      this.tasksError = ''
      try {
        this.tasks = await apiListTasks()
        this.tasksLoaded = true
      } catch (error) {
        this.tasksError = error instanceof Error ? error.message : '加载评测任务失败'
      } finally {
        this.tasksLoading = false
      }
    },

    async fetchTask(id: number, force = false): Promise<EvalTaskDetail> {
      if (!force) {
        const cached = this.taskDetails[id]
        if (cached) return cached
      }
      const detail = await apiGetTask(id)
      this.taskDetails = { ...this.taskDetails, [id]: detail }
      return detail
    },

    async createTask(payload: TaskPayload): Promise<EvalTaskItem> {
      this.taskActionError = ''
      try {
        const created = await apiCreateTask(payload)
        await this.fetchTasks()
        return created
      } catch (error) {
        this.taskActionError = error instanceof Error ? error.message : '创建任务失败'
        throw error
      }
    },

    /** 启动任务并立即刷新列表（运行期间由页面轮询接管） */
    async startTask(id: number): Promise<void> {
      this.taskActionError = ''
      try {
        await apiStartTask(id)
        await this.fetchTasks()
      } catch (error) {
        this.taskActionError = error instanceof Error ? error.message : '启动任务失败'
        throw error
      }
    },

    /** 轮询一次：刷新任务列表与运行中任务的详情 */
    async pollRunning(): Promise<void> {
      await this.fetchTasks()
      const refreshes = this.runningTaskIds.map((id) => this.fetchTask(id, true))
      await Promise.allSettled(refreshes)
    },
  },
})
