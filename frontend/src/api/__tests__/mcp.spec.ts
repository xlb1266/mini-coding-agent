import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  createMcpServer,
  deleteMcpServer,
  listMcpServers,
  setMcpServerEnabled,
  testMcpServer,
  updateMcpServer,
} from '../mcp'

const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('mcp API', () => {
  it('listMcpServers 请求 /api/mcp-servers', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse([]))
    vi.stubGlobal('fetch', fetchMock)

    await expect(listMcpServers()).resolves.toEqual([])
    expect(fetchMock).toHaveBeenCalledWith('/api/mcp-servers', expect.anything())
  })

  it('createMcpServer 以 POST 提交载荷', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ id: 1 }, 201))
    vi.stubGlobal('fetch', fetchMock)
    const payload = {
      name: '文件工具服务',
      description: '读写本地文件',
      command: 'uvx',
      args: ['-y', 'mcp-server-files'],
      env: { FILES_API_KEY: 'sk-1' },
    }

    await createMcpServer(payload)

    const [path, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(path).toBe('/api/mcp-servers')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body as string)).toEqual(payload)
  })

  it('updateMcpServer 以 PUT 提交到 /api/mcp-servers/:id', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ id: 7 }))
    vi.stubGlobal('fetch', fetchMock)
    const payload = { name: 'A', description: '', command: 'node', args: [], env: {} }

    await updateMcpServer(7, payload)

    const [path, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(path).toBe('/api/mcp-servers/7')
    expect(init.method).toBe('PUT')
  })

  it('setMcpServerEnabled 以 PUT 请求 enabled 子路径并携带布尔值', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ id: 3, enabled: true }))
    vi.stubGlobal('fetch', fetchMock)

    await setMcpServerEnabled(3, true)

    const [path, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(path).toBe('/api/mcp-servers/3/enabled')
    expect(init.method).toBe('PUT')
    expect(JSON.parse(init.body as string)).toEqual({ enabled: true })
  })

  it('deleteMcpServer 以 DELETE 请求 /api/mcp-servers/:id', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))
    vi.stubGlobal('fetch', fetchMock)

    await deleteMcpServer(5)

    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/mcp-servers/5')
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(init.method).toBe('DELETE')
  })

  it('testMcpServer 以 POST 请求 test 子路径', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ status: 'success', toolCount: 2, tools: [] }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(testMcpServer(9)).resolves.toEqual({ status: 'success', toolCount: 2, tools: [] })
    const [path, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(path).toBe('/api/mcp-servers/9/test')
    expect(init.method).toBe('POST')
  })
})
