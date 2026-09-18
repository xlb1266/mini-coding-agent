import { afterEach, describe, expect, it, vi } from 'vitest'

import { openChatEventStream, type ChatEvent } from '../chat'

function sseResponse(chunks: string[], status = 200): Response {
  const encoder = new TextEncoder()
  const stream = new ReadableStream({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(chunk))
      }
      controller.close()
    },
  })
  return new Response(stream, { status, headers: { 'Content-Type': 'text/event-stream' } })
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('openChatEventStream SSE 解析', () => {
  it('解析 data 事件、忽略心跳注释，按顺序回调', async () => {
    const events: ChatEvent[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        sseResponse([
          ': ping\n\n',
          'data: {"type":"snapshot","phase":"thinking","content":"","agentName":"助手"}\n\n',
          'data: {"type":"delta","text":"你"}\n\n',
          'data: {"type":"delta","text":"好"}\n\n',
        ]),
      ),
    )

    await openChatEventStream(1, (event) => events.push(event), new AbortController().signal)

    expect(events).toEqual([
      { type: 'snapshot', phase: 'thinking', content: '', agentName: '助手' },
      { type: 'delta', text: '你' },
      { type: 'delta', text: '好' },
    ])
    expect(fetch).toHaveBeenCalledWith(
      '/api/chat/sessions/1/events',
      expect.objectContaining({ headers: { Accept: 'text/event-stream' } }),
    )
  })

  it('跨读块的事件不会丢失或重复', async () => {
    const events: ChatEvent[] = []
    // 同一事件被拆到两个 chunk，中间还夹着一个完整事件
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        sseResponse([
          'data: {"type":"del',
          'ta","text":"第一"}\n\ndata: {"type":"delta","text":"第二"}\n',
          '\ndata: {"type":"done","message":{"id":1}}\n\n',
        ]),
      ),
    )

    await openChatEventStream(1, (event) => events.push(event), new AbortController().signal)

    expect(events).toEqual([
      { type: 'delta', text: '第一' },
      { type: 'delta', text: '第二' },
      { type: 'done', message: { id: 1 } },
    ])
  })

  it('无法解析的行被跳过，不中断整条流', async () => {
    const events: ChatEvent[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        sseResponse(['data: not-json\n\n', 'data: {"type":"delta","text":"ok"}\n\n']),
      ),
    )

    await openChatEventStream(1, (event) => events.push(event), new AbortController().signal)

    expect(events).toEqual([{ type: 'delta', text: 'ok' }])
  })

  it('请求地址为相对路径 /api，由 Vite 代理转发', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(sseResponse([])))

    await openChatEventStream(7, () => undefined, new AbortController().signal)

    expect(fetch).toHaveBeenCalledWith('/api/chat/sessions/7/events', expect.anything())
  })

  it('连接失败抛出 ApiError', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('network down')))

    await expect(
      openChatEventStream(1, () => undefined, new AbortController().signal),
    ).rejects.toMatchObject({ message: '事件流连接失败，请确认后端服务已启动' })
  })

  it('非 200 响应抛出带状态码的 ApiError', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(sseResponse([], 500)))

    await expect(
      openChatEventStream(1, () => undefined, new AbortController().signal),
    ).rejects.toMatchObject({ status: 500 })
  })

  it('主动中断时静默返回，不抛错', async () => {
    const controller = new AbortController()
    controller.abort()
    // fetch 因已中止的 signal 立即拒绝
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new DOMException('Aborted', 'AbortError')))

    await expect(
      openChatEventStream(1, () => undefined, controller.signal),
    ).resolves.toBeUndefined()
  })
})
