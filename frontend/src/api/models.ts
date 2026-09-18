/** 模型管理接口定义。 */

import { del, get, post, put } from './request'

/** 一条模型配置；密钥只以 hasApiKey 状态出现，接口永不返回密钥正文。 */
export interface ModelItem {
  id: number
  displayName: string
  modelIdentifier: string
  baseUrl: string
  contextLength: number
  maxOutputTokens: number
  temperature: number
  /** 每百万 token 价格（元）；null 表示未填写，与 0（免费）区分。 */
  inputPrice: number | null
  outputPrice: number | null
  cachedInputPrice: number | null
  isDefault: boolean
  hasApiKey: boolean
  createdAt: string
  updatedAt: string
}

/** 新增/编辑表单提交的载荷；apiKey 传 undefined 表示不提供/保留原密钥。 */
export interface ModelPayload {
  displayName: string
  modelIdentifier: string
  baseUrl: string
  apiKey?: string
  contextLength: number
  maxOutputTokens: number
  temperature: number
  inputPrice: number | null
  outputPrice: number | null
  cachedInputPrice: number | null
}

export interface TestConnectionResult {
  ok: boolean
  reply?: string
  errorType?: string
  message?: string
  hint?: string
}

export function listModels(): Promise<ModelItem[]> {
  return get<ModelItem[]>('/api/models')
}

export function createModel(payload: ModelPayload): Promise<ModelItem> {
  return post<ModelItem>('/api/models', payload)
}

export function updateModel(id: number, payload: ModelPayload): Promise<ModelItem> {
  return put<ModelItem>(`/api/models/${id}`, payload)
}

/** 删除模型；删除默认模型且还有其他模型时，需携带 newDefaultId 完成默认切换。 */
export function deleteModel(id: number, newDefaultId?: number): Promise<void> {
  const query = newDefaultId === undefined ? '' : `?new_default_id=${newDefaultId}`
  return del<void>(`/api/models/${id}${query}`)
}

export function setDefaultModel(id: number): Promise<ModelItem> {
  return put<ModelItem>(`/api/models/${id}/default`)
}

export function testModel(id: number): Promise<TestConnectionResult> {
  return post<TestConnectionResult>(`/api/models/${id}/test`)
}
