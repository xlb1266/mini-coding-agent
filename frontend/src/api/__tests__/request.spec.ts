import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError, del, get, post, postForm, put } from '../request'

const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('request 请求封装', () => {
  it('GET 返回解析后的 JSON，并带上默认请求头', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ status: 'ok' }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(get('/api/health')).resolves.toEqual({ status: 'ok' })
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/health',
      expect.objectContaining({
        headers: expect.objectContaining({ 'Content-Type': 'application/json' }),
      }),
    )
  })

  it('POST 把请求体序列化为 JSON', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ id: 1 }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(post('/api/thing', { name: 'x' })).resolves.toEqual({ id: 1 })
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/thing',
      expect.objectContaining({ method: 'POST', body: JSON.stringify({ name: 'x' }) }),
    )
  })

  it('postForm 上传 FormData 时不设置 JSON Content-Type', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ id: 1 }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(postForm('/api/skills/import', new FormData())).resolves.toEqual({ id: 1 })
    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect(init.method).toBe('POST')
    expect((init.headers as Record<string, string>)['Content-Type']).toBeUndefined()
  })

  it('非 2xx 时抛出 ApiError，并优先使用后端 detail 信息', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ detail: 'not found' }, 404)))

    await expect(get('/api/missing')).rejects.toMatchObject({
      name: 'ApiError',
      status: 404,
      message: 'not found',
    })
  })

  it('网络异常时抛出带提示信息的 ApiError', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('failed to fetch')))

    await expect(get('/api/health')).rejects.toBeInstanceOf(ApiError)
  })

  it('PUT 携带 method 与 JSON 请求体', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ id: 1 }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(put('/api/models/1', { name: 'x' })).resolves.toEqual({ id: 1 })
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/models/1',
      expect.objectContaining({ method: 'PUT', body: JSON.stringify({ name: 'x' }) }),
    )
  })

  it('DELETE 只发送方法，不带请求体', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(del('/api/models/1')).resolves.toBeUndefined()
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/models/1',
      expect.objectContaining({ method: 'DELETE' }),
    )
  })

  it('detail 为对象时解析 message 与字段级 fields', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        jsonResponse({ detail: { message: '表单校验未通过', fields: { baseUrl: '地址不正确' } } }, 422),
      ),
    )

    let error: ApiError | undefined
    try {
      await get('/api/models')
      expect.unreachable('应当抛出 ApiError')
    } catch (e) {
      error = e as ApiError
    }
    expect(error).toBeInstanceOf(ApiError)
    expect(error!.message).toBe('表单校验未通过')
    expect(error!.fields).toEqual({ baseUrl: '地址不正确' })
  })
})
