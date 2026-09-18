import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  createModel,
  deleteModel,
  listModels,
  setDefaultModel,
  testModel,
  updateModel,
} from '../models'

const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('models API', () => {
  it('listModels 请求 /api/models', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse([]))
    vi.stubGlobal('fetch', fetchMock)

    await expect(listModels()).resolves.toEqual([])
    expect(fetchMock).toHaveBeenCalledWith('/api/models', expect.anything())
  })

  it('createModel 以 POST 提交驼峰命名的载荷', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ id: 1 }, 201))
    vi.stubGlobal('fetch', fetchMock)
    const payload = {
      displayName: 'DeepSeek-V3',
      modelIdentifier: 'deepseek-chat',
      baseUrl: 'https://api.deepseek.com/v1',
      apiKey: 'sk-1',
      contextLength: 32768,
      maxOutputTokens: 4096,
      temperature: 0.7,
      inputPrice: null,
      outputPrice: null,
      cachedInputPrice: null,
    }

    await createModel(payload)

    const [path, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(path).toBe('/api/models')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body as string)).toEqual(payload)
  })

  it('updateModel 以 PUT 提交到 /api/models/:id', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ id: 7 }))
    vi.stubGlobal('fetch', fetchMock)
    const payload = {
      displayName: 'A',
      modelIdentifier: 'a',
      baseUrl: 'http://127.0.0.1:11434/v1',
      contextLength: 8192,
      maxOutputTokens: 2048,
      temperature: 0.7,
      inputPrice: null,
      outputPrice: null,
      cachedInputPrice: null,
    }

    await updateModel(7, payload)

    const [path, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(path).toBe('/api/models/7')
    expect(init.method).toBe('PUT')
  })

  it('deleteModel 未指定新默认时不带查询参数，指定时携带 new_default_id', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))
    vi.stubGlobal('fetch', fetchMock)

    await deleteModel(3)
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/models/3')

    await deleteModel(3, 5)
    expect(fetchMock.mock.calls[1]?.[0]).toBe('/api/models/3?new_default_id=5')
  })

  it('setDefaultModel 以 PUT 请求 default 子路径', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ id: 2 }))
    vi.stubGlobal('fetch', fetchMock)

    await setDefaultModel(2)
    const [path, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(path).toBe('/api/models/2/default')
    expect(init.method).toBe('PUT')
  })

  it('testModel 以 POST 请求 test 子路径', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true, reply: '连接成功' }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(testModel(9)).resolves.toEqual({ ok: true, reply: '连接成功' })
    const [path, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(path).toBe('/api/models/9/test')
    expect(init.method).toBe('POST')
  })
})
