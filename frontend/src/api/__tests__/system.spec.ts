import { afterEach, describe, expect, it, vi } from 'vitest'

import { pickDirectory } from '../system'

const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('system API', () => {
  it('pickDirectory 请求 /api/system/pick-directory 并返回选中路径', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ path: 'D:\\projects\\demo' }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(pickDirectory()).resolves.toEqual({ path: 'D:\\projects\\demo' })
    expect(fetchMock).toHaveBeenCalledWith('/api/system/pick-directory', expect.anything())
  })

  it('用户取消选择时 path 为 null', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ path: null })))

    await expect(pickDirectory()).resolves.toEqual({ path: null })
  })

  it('窗口打不开时抛出后端给的提示', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        jsonResponse({ detail: '无法打开系统目录选择窗口，请手动输入工作区目录路径' }, 500),
      ),
    )

    await expect(pickDirectory()).rejects.toThrow('无法打开系统目录选择窗口')
  })
})
