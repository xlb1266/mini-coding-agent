import { defineStore } from 'pinia'

import {
  createModel as apiCreateModel,
  deleteModel as apiDeleteModel,
  listModels,
  setDefaultModel as apiSetDefaultModel,
  testModel as apiTestModel,
  updateModel as apiUpdateModel,
  type ModelItem,
  type ModelPayload,
  type TestConnectionResult,
} from '../api/models'

interface ModelsState {
  models: ModelItem[]
  loading: boolean
  loaded: boolean
}

export const useModelsStore = defineStore('models', {
  state: (): ModelsState => ({
    models: [],
    loading: false,
    loaded: false,
  }),
  getters: {
    /** 默认模型排在最前，与后端列表顺序一致；这里仅提供便捷访问。 */
    defaultModel: (state: ModelsState): ModelItem | undefined =>
      state.models.find((model) => model.isDefault),
  },
  actions: {
    async fetchModels(): Promise<void> {
      this.loading = true
      try {
        this.models = await listModels()
        this.loaded = true
      } finally {
        this.loading = false
      }
    },
    /** 新增后重新拉取列表，保证默认模型置顶与状态一致。 */
    async createModel(payload: ModelPayload): Promise<ModelItem> {
      const created = await apiCreateModel(payload)
      await this.fetchModels()
      return created
    },
    async updateModel(id: number, payload: ModelPayload): Promise<ModelItem> {
      const updated = await apiUpdateModel(id, payload)
      await this.fetchModels()
      return updated
    },
    async removeModel(id: number, newDefaultId?: number): Promise<void> {
      await apiDeleteModel(id, newDefaultId)
      await this.fetchModels()
    },
    async setDefault(id: number): Promise<void> {
      await apiSetDefaultModel(id)
      await this.fetchModels()
    },
    async testConnection(id: number): Promise<TestConnectionResult> {
      return apiTestModel(id)
    },
  },
})
