import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  createChatSession,
  decideChatApproval,
  getChatMessages,
  listChatSessions,
  openChatEventStream,
  selectChatAgent,
  sendChatMessage,
  stopChatGeneration,
  type ChatEvent,
  type ChatMessage,
  type ChatSessionSummary,
} from '../../api/chat'
import { segmentsText, useChatStore } from '../chat'
import type { ApprovalSegment, GenerationSegment, RunningGeneration, ToolSegment } from '../chat'

vi.mock('../../api/chat', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/chat')>()
  return {
    ...actual,
    listChatSessions: vi.fn(),
    createChatSession: vi.fn(),
    getChatMessages: vi.fn(),
    selectChatAgent: vi.fn(),
    sendChatMessage: vi.fn(),
    stopChatGeneration: vi.fn(),
    openChatEventStream: vi.fn(),
    decideChatApproval: vi.fn(),
  }
})

function makeSession(overrides: Partial<ChatSessionSummary> = {}): ChatSessionSummary {
  return {
    id: 1,
    title: '新会话',
    agentId: 1,
    agentName: '测试助手',
    isGenerating: false,
    createdAt: '2026-09-12T02:00:00+00:00',
    updatedAt: '2026-09-12T02:00:00+00:00',
    ...overrides,
  }
}

function makeMessage(overrides: Partial<ChatMessage> = {}): ChatMessage {
  return {
    id: 1,
    sessionId: 1,
    role: 'user',
    content: '你好',
    status: 'completed',
    agentId: null,
    agentName: null,
    seq: 1,
    createdAt: '2026-09-12T02:00:01+00:00',
    ...overrides,
  }
}

function makeGeneration(overrides: Partial<RunningGeneration> = {}): RunningGeneration {
  return {
    runId: null,
    phase: 'thinking',
    agentName: '测试助手',
    toolName: null,
    segments: [],
    ...overrides,
  }
}

function toolSegments(generation: RunningGeneration | undefined): ToolSegment[] {
  return (generation?.segments ?? []).filter(
    (segment): segment is ToolSegment => segment.kind === 'tool',
  )
}

function toolStartEvent(overrides: Record<string, unknown> = {}): ChatEvent {
  return {
    type: 'tool_call_start',
    runId: 'r1',
    seq: 2,
    round: 1,
    callId: 'call_1',
    toolName: 'builtin_current_time',
    toolType: 'builtin',
    displayName: '当前时间',
    serverName: null,
    arguments: { timezone: 'Asia/Shanghai' },
    ...overrides,
  } as ChatEvent
}

function toolEndEvent(overrides: Record<string, unknown> = {}): ChatEvent {
  return {
    type: 'tool_call_end',
    runId: 'r1',
    seq: 3,
    round: 1,
    callId: 'call_1',
    toolName: 'builtin_current_time',
    toolType: 'builtin',
    status: 'success',
    durationMs: 5,
    summary: '2026-09-12T10:00:00（Asia/Shanghai）',
    result: { text: '{\n  "datetime": "2026-09-12T10:00:00"\n}', truncated: false, totalLength: 36 },
    ...overrides,
  } as ChatEvent
}

/** 收集订阅回调：测试通过它向后端事件一样推送事件。 */
let subscribers: ((event: ChatEvent) => void)[] = []

function emitEvent(event: ChatEvent): void {
  for (const handler of subscribers) handler(event)
}

beforeEach(() => {
  vi.clearAllMocks()
  setActivePinia(createPinia())
  subscribers = []
  vi.mocked(listChatSessions).mockResolvedValue([])
  vi.mocked(openChatEventStream).mockImplementation((_sessionId, onEvent) => {
    subscribers.push(onEvent)
    // 流保持打开（真实场景下终态事件后由服务端关闭）
    return new Promise<void>(() => undefined)
  })
})

describe('chat store 会话管理', () => {
  it('fetchSessions 拉取列表', async () => {
    vi.mocked(listChatSessions).mockResolvedValue([makeSession(), makeSession({ id: 2 })])

    const store = useChatStore()
    await store.fetchSessions()

    expect(store.sessions.map((s) => s.id)).toEqual([1, 2])
    expect(store.sessionsLoaded).toBe(true)
  })

  it('createSession 成功后选中并加载消息', async () => {
    const created = makeSession({ id: 5 })
    vi.mocked(createChatSession).mockResolvedValue(created)
    vi.mocked(listChatSessions).mockResolvedValue([created])
    vi.mocked(getChatMessages).mockResolvedValue({
      session: created,
      messages: [],
      generation: null,
    })

    const store = useChatStore()
    await store.createSession()

    expect(store.activeSessionId).toBe(5)
    expect(getChatMessages).toHaveBeenCalledWith(5)
  })

  it('selectSession 打开已加载的生成中会话时重新订阅事件', async () => {
    const session = makeSession()
    vi.mocked(listChatSessions).mockResolvedValue([session])
    vi.mocked(getChatMessages).mockResolvedValue({ session, messages: [], generation: null })

    const store = useChatStore()
    await store.fetchSessions()
    await store.selectSession(1)

    // 模拟后台生成中：直接进入生成态并保持订阅（如离开页面后回来）
    store.generations[1] = makeGeneration({ phase: 'generating', segments: [{ kind: 'text', text: '部分' }] })
    await store.selectSession(1)

    expect(openChatEventStream).toHaveBeenCalled()
  })
})

describe('chat store 发送与流式事件', () => {
  async function setupActiveSession(): Promise<void> {
    const session = makeSession()
    vi.mocked(listChatSessions).mockResolvedValue([session])
    vi.mocked(getChatMessages).mockResolvedValue({ session, messages: [], generation: null })
    const store = useChatStore()
    await store.fetchSessions()
    await store.selectSession(1)
  }

  it('sendMessage 保存用户消息、进入生成态并订阅事件流', async () => {
    await setupActiveSession()
    const store = useChatStore()
    vi.mocked(sendChatMessage).mockResolvedValue({
      session: makeSession({ title: '你好', isGenerating: true }),
      userMessage: makeMessage({ content: '你好' }),
      generation: { phase: 'thinking', content: '', agentName: '测试助手' },
    })

    await store.sendMessage('你好')

    expect(store.messagesBySession[1]!.map((m) => m.role)).toEqual(['user'])
    expect(store.generations[1]!.phase).toBe('thinking')
    expect(openChatEventStream).toHaveBeenCalledWith(1, expect.any(Function), expect.anything())
  })

  it('text_delta 事件按顺序累积，run_end 完成态落盘并结束生成', async () => {
    await setupActiveSession()
    const store = useChatStore()
    vi.mocked(sendChatMessage).mockResolvedValue({
      session: makeSession({ isGenerating: true }),
      userMessage: makeMessage(),
      generation: { phase: 'thinking', content: '', agentName: '测试助手' },
    })
    await store.sendMessage('你好')

    emitEvent({ type: 'snapshot', runId: 'r1', phase: 'thinking', content: '', agentName: '测试助手', toolName: null })
    emitEvent({ type: 'text_delta', runId: 'r1', seq: 2, round: 1, callId: 'mr-1', text: '你' })
    emitEvent({ type: 'text_delta', runId: 'r1', seq: 3, round: 1, callId: 'mr-1', text: '好' })

    expect(store.generations[1]!.phase).toBe('generating')
    expect(segmentsText(store.generations[1]!.segments)).toBe('你好')

    const reply = makeMessage({
      id: 2,
      role: 'assistant',
      content: '你好',
      agentId: 1,
      agentName: '测试助手',
      seq: 2,
    })
    emitEvent({
      type: 'run_end',
      runId: 'r1',
      seq: 4,
      status: 'completed',
      reason: 'model_finished',
      roundsUsed: 1,
      content: '你好',
      message: reply,
      error: null,
    })

    expect(store.generations[1]).toBeUndefined()
    expect(store.messagesBySession[1]!.map((m) => m.content)).toEqual(['你好', '你好'])
    // 结束后刷新会话列表
    expect(listChatSessions).toHaveBeenCalled()
  })

  it('重放的终态事件不会产生重复消息', async () => {
    await setupActiveSession()
    const store = useChatStore()
    const reply = makeMessage({ id: 2, role: 'assistant', seq: 2 })
    store.upsertMessage(reply)
    store.upsertMessage(reply)

    expect(store.messagesBySession[1]).toHaveLength(1)
  })

  it('run_end 错误态记录原因；已保存的未完成回复进入消息列表', async () => {
    await setupActiveSession()
    const store = useChatStore()
    store.generations[1] = makeGeneration({
      phase: 'generating',
      segments: [{ kind: 'text', text: '生成到一半' }],
    })
    store.subscribeEvents(1)

    const partial = makeMessage({
      id: 3,
      role: 'assistant',
      content: '生成到一半',
      status: 'incomplete',
      seq: 2,
    })
    emitEvent({
      type: 'run_end',
      runId: 'r1',
      seq: 9,
      status: 'error',
      reason: 'model_error',
      roundsUsed: 1,
      content: '生成到一半',
      message: partial,
      error: { type: 'network', message: '与模型服务的连接中断', hint: '请核对服务地址' },
    })

    expect(store.generations[1]).toBeUndefined()
    expect(store.generationErrors[1]!.message).toBe('与模型服务的连接中断')
    expect(store.messagesBySession[1]!.some((m) => m.status === 'incomplete')).toBe(true)
  })

  it('工具调用与正文按事件顺序交错；callId 配对更新，不重复建卡', async () => {
    await setupActiveSession()
    const store = useChatStore()
    vi.mocked(sendChatMessage).mockResolvedValue({
      session: makeSession({ isGenerating: true }),
      userMessage: makeMessage(),
      generation: { phase: 'thinking', content: '', agentName: '测试助手' },
    })
    await store.sendMessage('现在几点')

    // 正文 → 工具卡片 → 正文：顺序就是事件顺序
    emitEvent({ type: 'text_delta', runId: 'r1', seq: 1, round: 1, callId: 'mr-1', text: '先查时间：' })
    emitEvent(toolStartEvent())
    expect(store.generations[1]!.phase).toBe('tooling')
    expect(store.generations[1]!.toolName).toBe('当前时间')
    const cards = toolSegments(store.generations[1])
    expect(cards).toHaveLength(1)
    expect(cards[0]!.tool.status).toBe('running')
    expect(cards[0]!.tool.arguments).toEqual({ timezone: 'Asia/Shanghai' })

    emitEvent(toolEndEvent())
    const updated = toolSegments(store.generations[1])[0]!.tool
    expect(updated.status).toBe('success')
    expect(updated.durationMs).toBe(5)
    expect(updated.result!.truncated).toBe(false)
    expect(store.generations[1]!.toolName).toBeNull()

    // 工具之后的正文接在卡片下方，不覆盖前文
    emitEvent({ type: 'text_delta', runId: 'r1', seq: 4, round: 2, callId: 'mr-2', text: '现在是' })
    emitEvent({ type: 'text_delta', runId: 'r1', seq: 5, round: 2, callId: 'mr-2', text: '上午十点' })

    const generation = store.generations[1]!
    expect(generation.segments.map((s) => s.kind)).toEqual(['text', 'tool', 'text'])
    expect(segmentsText(generation.segments)).toBe('先查时间：现在是上午十点')
    expect(generation.phase).toBe('generating')
  })

  it('同一个工具多次调用时每次独立展示，完成顺序不影响卡片位置', async () => {
    await setupActiveSession()
    const store = useChatStore()
    store.generations[1] = makeGeneration()
    store.subscribeEvents(1)

    emitEvent(toolStartEvent({ callId: 'call_a', seq: 2 }))
    emitEvent(toolStartEvent({ callId: 'call_b', seq: 3, displayName: '执行命令', toolName: 'builtin_shell' }))
    // 后开始的先完成：只更新自己的卡片，位置不变
    emitEvent(toolEndEvent({ callId: 'call_b', seq: 4, summary: '命令完成' }))
    emitEvent(toolEndEvent({ callId: 'call_a', seq: 5 }))

    const cards = toolSegments(store.generations[1])
    expect(cards.map((c) => c.tool.callId)).toEqual(['call_a', 'call_b'])
    expect(cards[0]!.tool.status).toBe('success')
    expect(cards[1]!.tool.status).toBe('success')
    expect(cards[1]!.tool.displayName).toBe('执行命令')
  })

  it('run_end 完成且带保存消息：时间线挂到消息上，卡片与状态保留', async () => {
    await setupActiveSession()
    const store = useChatStore()
    store.generations[1] = makeGeneration({
      segments: [
        { kind: 'text', text: '先查时间：' },
        {
          kind: 'tool',
          tool: {
            callId: 'call_1',
            toolName: 'builtin_current_time',
            displayName: '当前时间',
            serverName: null,
            toolType: 'builtin',
            status: 'success',
            durationMs: 5,
            summary: '十点',
            result: null,
          },
        },
        { kind: 'text', text: '上午十点' },
      ],
    })
    store.subscribeEvents(1)

    const reply = makeMessage({ id: 7, role: 'assistant', content: '先查时间：上午十点', seq: 2 })
    emitEvent({
      type: 'run_end',
      runId: 'r1',
      seq: 6,
      status: 'completed',
      reason: 'model_finished',
      roundsUsed: 2,
      content: '先查时间：上午十点',
      message: reply,
      error: null,
    })

    expect(store.generations[1]).toBeUndefined()
    // 时间线按消息 id 保留：切换会话再回来仍能渲染卡片与顺序
    expect(store.toolTimelines[7]!.map((s) => s.kind)).toEqual(['text', 'tool', 'text'])
  })

  it('run_end 取消态：运行中卡片标记“已取消”，无正文时留作会话末尾卡片', async () => {
    await setupActiveSession()
    const store = useChatStore()
    store.generations[1] = makeGeneration({
      phase: 'tooling',
      segments: [
        {
          kind: 'tool',
          tool: {
            callId: 'call_1',
            toolName: 'builtin_shell',
            displayName: '执行命令',
            serverName: null,
            toolType: 'builtin',
            status: 'running',
            arguments: { command: 'ping example.com' },
          },
        },
      ],
    })
    store.subscribeEvents(1)

    emitEvent({
      type: 'run_end',
      runId: 'r1',
      seq: 2,
      status: 'cancelled',
      reason: 'cancelled',
      roundsUsed: 1,
      content: '',
      message: null,
      error: null,
    })

    expect(store.generations[1]).toBeUndefined()
    expect(store.messagesBySession[1]).toEqual([])
    expect(store.orphanSegments[1]).toHaveLength(1)
    expect((store.orphanSegments[1]![0] as ToolSegment).tool.status).toBe('cancelled')
    // 下次发送时清掉孤儿卡片
    vi.mocked(sendChatMessage).mockResolvedValue({
      session: makeSession({ isGenerating: true }),
      userMessage: makeMessage({ seq: 3 }),
      generation: { phase: 'thinking', content: '', agentName: '测试助手' },
    })
    await store.sendMessage('再来一次')
    expect(store.orphanSegments[1]).toBeUndefined()
  })

  it('MCP 工具卡片记录 Server 名称；取消终态事件直接标记卡片', async () => {
    await setupActiveSession()
    const store = useChatStore()
    store.generations[1] = makeGeneration()
    store.subscribeEvents(1)

    emitEvent(
      toolStartEvent({
        callId: 'call_m',
        toolName: 'mcp3_get_weather',
        toolType: 'mcp',
        displayName: 'get_weather',
        serverName: '天气服务',
        arguments: { city: '北京' },
      }),
    )
    expect(store.generations[1]!.toolName).toBe('天气服务·get_weather')

    emitEvent(
      toolEndEvent({
        callId: 'call_m',
        toolName: 'mcp3_get_weather',
        toolType: 'mcp',
        status: 'cancelled',
        summary: '执行被取消',
        result: null,
      }),
    )
    const card = toolSegments(store.generations[1])[0]!.tool
    expect(card.status).toBe('cancelled')
    expect(card.serverName).toBe('天气服务')
    expect(card.result).toBeNull()
  })

  it('生成中重复发送被拒绝', async () => {
    await setupActiveSession()
    const store = useChatStore()
    store.generations[1] = makeGeneration()

    await expect(store.sendMessage('再来一条')).rejects.toThrow('正在生成')
    expect(sendChatMessage).not.toHaveBeenCalled()
  })

  it('生成中切换 Agent 被拒绝', async () => {
    await setupActiveSession()
    const store = useChatStore()
    store.generations[1] = makeGeneration({ phase: 'generating', segments: [{ kind: 'text', text: 'x' }] })

    await expect(store.changeAgent(2)).rejects.toThrow('正在生成')
    expect(selectChatAgent).not.toHaveBeenCalled()
  })

  it('stopGeneration 用后端保存的部分回复更新列表，运行中卡片标记取消', async () => {
    await setupActiveSession()
    const store = useChatStore()
    store.generations[1] = makeGeneration({
      phase: 'tooling',
      segments: [
        { kind: 'text', text: '部分' },
        {
          kind: 'tool',
          tool: {
            callId: 'call_1',
            toolName: 'builtin_shell',
            displayName: '执行命令',
            serverName: null,
            toolType: 'builtin',
            status: 'running',
          },
        },
      ],
    })
    vi.mocked(stopChatGeneration).mockResolvedValue({
      outcome: 'cancelled',
      message: makeMessage({ id: 9, role: 'assistant', content: '部分', status: 'incomplete', seq: 2 }),
      content: '部分',
      errorMessage: null,
      hint: null,
      errorType: null,
    })

    await store.stopGeneration(1)

    expect(store.generations[1]).toBeUndefined()
    expect(store.messagesBySession[1]!.some((m) => m.id === 9)).toBe(true)
    // 终态事件未到达时（竞态）：停止路径兜底把卡片标记为已取消并挂到消息上
    expect(store.toolTimelines[9]![1]!.kind).toBe('tool')
    const tool = (store.toolTimelines[9]![1] as ToolSegment).tool
    expect(tool.status).toBe('cancelled')
  })

  it('changeAgent 更新会话的 Agent 归属', async () => {
    await setupActiveSession()
    const store = useChatStore()
    vi.mocked(selectChatAgent).mockResolvedValue(
      makeSession({ agentId: 2, agentName: '新助手' }),
    )

    await store.changeAgent(2)

    expect(store.activeSession!.agentName).toBe('新助手')
  })
})

describe('chat store 快照恢复', () => {
  it('snapshot 的时间线重建段落：正文与卡片交错、状态与结果还原', async () => {
    const store = useChatStore()
    store.generations[1] = makeGeneration()
    store.subscribeEvents(1)

    emitEvent({
      type: 'snapshot',
      runId: 'r9',
      phase: 'tooling',
      content: '先查时间：',
      agentName: '测试助手',
      toolName: '当前时间',
      timeline: [
        { kind: 'text', text: '先查时间：' },
        {
          kind: 'tool',
          callId: 'call_1',
          toolName: 'builtin_current_time',
          toolType: 'builtin',
          displayName: '当前时间',
          serverName: null,
          status: 'success',
          durationMs: 8,
          summary: '十点',
          arguments: { timezone: 'Asia/Shanghai' },
          result: { text: '{"datetime":"10:00"}', truncated: false, totalLength: 19 },
        },
      ],
    })

    const generation = store.generations[1]!
    expect(generation.segments.map((s) => s.kind)).toEqual(['text', 'tool'])
    expect(generation.phase).toBe('tooling')
    const card = (generation.segments[1] as ToolSegment).tool
    expect(card.status).toBe('success')
    expect(card.arguments).toEqual({ timezone: 'Asia/Shanghai' })
    expect(card.result!.text).toBe('{"datetime":"10:00"}')

    // 恢复后继续接收实时事件
    emitEvent({ type: 'text_delta', runId: 'r9', seq: 7, round: 2, callId: 'mr-2', text: '上午十点' })
    expect(segmentsText(generation.segments)).toBe('先查时间：上午十点')
  })

  it('无时间线的快照退回纯正文段', () => {
    const store = useChatStore()
    store.generations[1] = makeGeneration()
    store.subscribeEvents(1)

    emitEvent({
      type: 'snapshot',
      runId: 'r1',
      phase: 'generating',
      content: '只有正文',
      agentName: '测试助手',
      toolName: null,
    })

    expect(store.generations[1]!.segments).toEqual([{ kind: 'text', text: '只有正文' }])
  })

  it('旧运行的迟到事件不落入当前时间线', () => {
    const store = useChatStore()
    store.generations[1] = makeGeneration({ runId: 'run-new' })
    store.subscribeEvents(1)

    emitEvent(toolStartEvent({ runId: 'run-old', callId: 'call_old' }))
    emitEvent({ type: 'text_delta', runId: 'run-old', seq: 2, round: 1, callId: 'mr-1', text: '旧正文' })

    expect(store.generations[1]!.segments).toEqual([])
  })
})

describe('chat store 断线自愈', () => {
  it('事件流断开后按后端状态恢复；连续失败超过上限则工具标记“状态未知”并保留卡片', async () => {
    const session = makeSession({ isGenerating: true })
    vi.mocked(listChatSessions).mockResolvedValue([session])
    vi.mocked(getChatMessages).mockResolvedValue({
      session,
      messages: [],
      generation: { phase: 'tooling', content: '部分', agentName: '测试助手' },
    })
    // 每次订阅都立刻失败
    vi.mocked(openChatEventStream).mockRejectedValue(new Error('connection lost'))

    const store = useChatStore()
    await store.fetchSessions()
    await store.selectSession(1)
    store.generations[1] = makeGeneration({
      phase: 'tooling',
      segments: [
        { kind: 'text', text: '部分' },
        {
          kind: 'tool',
          tool: {
            callId: 'call_1',
            toolName: 'builtin_shell',
            displayName: '执行命令',
            serverName: null,
            toolType: 'builtin',
            status: 'running',
          },
        },
      ],
    })
    store.subscribeEvents(1)

    // 失败 → 重连 → 再失败……直到超过重连上限（全程只有微任务，无定时器）
    for (let i = 0; i < 60; i += 1) {
      await Promise.resolve()
    }

    expect(store.generationErrors[1]!.message).toContain('事件流连接中断')
    expect(store.generations[1]).toBeUndefined()
    expect(openChatEventStream).toHaveBeenCalledTimes(4)
    // 无法确认执行结果：卡片标记“状态未知”，不得停留在“正在执行”或误标成功
    expect(store.orphanSegments[1]).toHaveLength(1)
    expect((store.orphanSegments[1]![0] as ToolSegment).tool.status).toBe('unknown')
  })
})

describe('chat store 消息插入顺序', () => {
  it('按 seq 排序插入乱序到达的消息', () => {
    const store = useChatStore()
    store.upsertMessage(makeMessage({ id: 2, role: 'assistant', seq: 2 }))
    store.upsertMessage(makeMessage({ id: 1, seq: 1 }))

    expect(store.messagesBySession[1]!.map((m) => m.seq)).toEqual([1, 2])
  })
})

describe('chat store 上下文压缩', () => {
  let compactIdSeq = 0

  function compactionStartEvent(overrides: Record<string, unknown> = {}): ChatEvent {
    compactIdSeq += 1
    return {
      type: 'context_compaction_start',
      runId: 'r1',
      seq: 10 + compactIdSeq,
      round: 1,
      compactionId: 'cmp-1',
      reason: '估算输入约 5000 token，达到可用输入容量的 80%',
      estimatedTokens: 5000,
      thresholdTokens: 4000,
      availableTokens: 5000,
      compactableMessages: 12,
      summaryTokens: 100,
      boundarySeq: 8,
      summaryTargetTokens: 1000,
      ...overrides,
    } as ChatEvent
  }

  function compactionEndEvent(overrides: Record<string, unknown> = {}): ChatEvent {
    return {
      type: 'context_compaction_end',
      runId: 'r1',
      seq: 20,
      round: 1,
      compactionId: 'cmp-1',
      status: 'success',
      durationMs: 1200,
      estimatedTokensBefore: 5000,
      estimatedTokensAfter: 900,
      processedMessages: 12,
      keptMessages: 4,
      batches: 2,
      modelCalls: 2,
      summaryTokens: 220,
      boundarySeq: 20,
      usage: { promptTokens: 3000, completionTokens: 200, totalTokens: 3200 },
      error: null,
      ...overrides,
    } as ChatEvent
  }

  function trimEvent(overrides: Record<string, unknown> = {}): ChatEvent {
    return {
      type: 'context_trim',
      runId: 'r1',
      seq: 30,
      round: 2,
      reason: '容量不足，已按完整消息组从最早内容开始裁剪（仅影响本次请求，不改动会话历史与压缩边界）',
      estimatedTokensBefore: 6000,
      estimatedTokensAfter: 3000,
      availableTokens: 5000,
      droppedMessages: 4,
      droppedHistoryTurns: 2,
      droppedToolGroups: 0,
      keptMessages: 6,
      ...overrides,
    } as ChatEvent
  }

  async function setupActive(): Promise<void> {
    const session = makeSession()
    vi.mocked(listChatSessions).mockResolvedValue([session])
    vi.mocked(getChatMessages).mockResolvedValue({ session, messages: [], generation: null })
    const store = useChatStore()
    await store.fetchSessions()
    await store.selectSession(1)
    store.generations[1] = makeGeneration()
    store.subscribeEvents(1)
  }

  it('压缩开始进入 compacting 阶段并追加运行中的压缩卡片', async () => {
    await setupActive()
    const store = useChatStore()

    emitEvent(compactionStartEvent())

    const generation = store.generations[1]!
    expect(generation.phase).toBe('compacting')
    expect(generation.segments).toHaveLength(1)
    const compact = generation.segments[0] as Extract<GenerationSegment, { kind: 'compact' }>
    expect(compact.compact.status).toBe('running')
    expect(compact.compact.estimatedTokensBefore).toBe(5000)
  })

  it('压缩结束按 compactionId 配对更新卡片并回到生成阶段', async () => {
    await setupActive()
    const store = useChatStore()
    emitEvent(compactionStartEvent())
    emitEvent(compactionEndEvent())

    const generation = store.generations[1]!
    expect(generation.phase).toBe('thinking')
    expect(generation.segments).toHaveLength(1) // 原地更新，不追加新卡
    const compact = generation.segments[0] as Extract<GenerationSegment, { kind: 'compact' }>
    expect(compact.compact.status).toBe('success')
    expect(compact.compact.estimatedTokensAfter).toBe(900)
    expect(compact.compact.processedMessages).toBe(12)
    expect(compact.compact.modelCalls).toBe(2)
    expect(compact.compact.durationMs).toBe(1200)
  })

  it('失败与部分完成状态原样写入卡片错误信息', async () => {
    await setupActive()
    const store = useChatStore()
    emitEvent(compactionStartEvent())
    emitEvent(
      compactionEndEvent({
        status: 'error',
        error: { type: 'server', message: '会话摘要保存失败', hint: '本次请求将改用备用裁剪' },
      }),
    )

    const compact = store.generations[1]!.segments[0] as Extract<
      GenerationSegment,
      { kind: 'compact' }
    >
    expect(compact.compact.status).toBe('error')
    expect(compact.compact.error?.message).toBe('会话摘要保存失败')
  })

  it('备用裁剪事件追加一张已裁剪卡片', async () => {
    await setupActive()
    const store = useChatStore()

    emitEvent(trimEvent())

    const generation = store.generations[1]!
    expect(generation.segments).toHaveLength(1)
    const compact = generation.segments[0] as Extract<GenerationSegment, { kind: 'compact' }>
    expect(compact.compact.status).toBe('trimmed')
    expect(compact.compact.droppedMessages).toBe(4)
    expect(compact.compact.keptMessages).toBe(6)
    expect(compact.compact.estimatedTokensAfter).toBe(3000)
  })

  it('取消收尾：运行中的压缩卡片标记为已取消，无正文时留作孤儿卡片', async () => {
    await setupActive()
    const store = useChatStore()
    emitEvent(compactionStartEvent())
    emitEvent({
      type: 'run_end',
      runId: 'r1',
      seq: 99,
      status: 'cancelled',
      reason: 'cancelled',
      roundsUsed: 1,
      content: '',
      message: null,
      error: null,
    })

    expect(store.generations[1]).toBeUndefined()
    expect(store.orphanSegments[1]).toHaveLength(1)
    const compact = store.orphanSegments[1]![0] as Extract<GenerationSegment, { kind: 'compact' }>
    expect(compact.compact.status).toBe('cancelled')
  })
})

describe('chat store 工具执行审批（模块二）', () => {
  function approvalRequestEvent(overrides: Record<string, unknown> = {}): ChatEvent {
    return {
      type: 'approval_request',
      runId: 'r1',
      seq: 5,
      round: 1,
      requestId: 'apr-1',
      callId: 'call_1',
      toolName: 'builtin_shell',
      toolType: 'builtin',
      displayName: 'Shell 命令',
      serverName: null,
      arguments: { command: 'echo hi' },
      ...overrides,
    } as ChatEvent
  }

  function approvalResultEvent(overrides: Record<string, unknown> = {}): ChatEvent {
    return {
      type: 'approval_result',
      runId: 'r1',
      seq: 6,
      round: 1,
      requestId: 'apr-1',
      callId: 'call_1',
      toolName: 'builtin_shell',
      toolType: 'builtin',
      decision: 'approved',
      ...overrides,
    } as ChatEvent
  }

  function approvalSegments(generation: RunningGeneration | undefined): ApprovalSegment[] {
    return (generation?.segments ?? []).filter(
      (segment): segment is ApprovalSegment => segment.kind === 'approval',
    )
  }

  async function setupActive(): Promise<void> {
    const session = makeSession()
    vi.mocked(listChatSessions).mockResolvedValue([session])
    vi.mocked(getChatMessages).mockResolvedValue({ session, messages: [], generation: null })
    const store = useChatStore()
    await store.fetchSessions()
    await store.selectSession(1)
    store.generations[1] = makeGeneration()
    store.subscribeEvents(1)
  }

  it('approval_request 建卡进入 approval 阶段；approval_result 原地更新终态', async () => {
    await setupActive()
    const store = useChatStore()

    emitEvent({ type: 'text_delta', runId: 'r1', seq: 2, round: 1, callId: 'mr-1', text: '我来执行' })
    emitEvent(approvalRequestEvent())

    const generation = store.generations[1]!
    expect(generation.phase).toBe('approval')
    const approval = approvalSegments(generation)[0]!.approval
    expect(approval.status).toBe('pending')
    expect(approval.toolName).toBe('builtin_shell')
    expect(approval.arguments).toEqual({ command: 'echo hi' })

    emitEvent(approvalResultEvent())
    expect(generation.phase).toBe('generating')
    expect(approval.status).toBe('approved')
    expect(approval.deciding).toBe(false)
  })

  it('decideApproval 调用后端决议并在等待期间置灰；失败显示错误', async () => {
    await setupActive()
    const store = useChatStore()
    emitEvent(approvalRequestEvent())
    const approval = approvalSegments(store.generations[1]!)[0]!.approval

    // 正常决议
    vi.mocked(decideChatApproval).mockResolvedValueOnce({
      requestId: 'apr-1',
      action: 'approve',
      outcome: 'approved',
    })
    const deciding = store.decideApproval(1, 'apr-1', true)
    expect(approval.deciding).toBe(true)
    await deciding
    expect(decideChatApproval).toHaveBeenCalledWith(1, 'apr-1', 'approve')
    expect(approval.error).toBeNull()

    // 决议失败（如审批已被处理）：按钮恢复可点，错误显示在卡片
    vi.mocked(decideChatApproval).mockRejectedValueOnce(new Error('审批请求不存在或已被处理'))
    emitEvent(approvalRequestEvent({ requestId: 'apr-2', seq: 7 }))
    await store.decideApproval(1, 'apr-2', false)
    const second = approvalSegments(store.generations[1]!).find((s) => s.approval.requestId === 'apr-2')!
      .approval
    expect(second.deciding).toBe(false)
    expect(second.error).toBe('审批请求不存在或已被处理')
  })

  it('已决定或正在提交的审批不重复提交', async () => {
    await setupActive()
    const store = useChatStore()
    emitEvent(approvalRequestEvent())
    emitEvent(approvalResultEvent())

    await store.decideApproval(1, 'apr-1', true)
    expect(decideChatApproval).not.toHaveBeenCalled()
  })

  it('run_end 取消时未决议的审批标记为已取消', async () => {
    await setupActive()
    const store = useChatStore()
    emitEvent(approvalRequestEvent())
    emitEvent({
      type: 'run_end',
      runId: 'r1',
      seq: 99,
      status: 'cancelled',
      reason: 'cancelled',
      roundsUsed: 1,
      content: '',
      message: null,
      error: null,
    })

    expect(store.orphanSegments[1]).toHaveLength(1)
    const approval = store.orphanSegments[1]![0] as Extract<GenerationSegment, { kind: 'approval' }>
    expect(approval.approval.status).toBe('cancelled')
  })

  it('快照时间线恢复审批卡片（刷新后仍在等待的审批继续展示）', async () => {
    await setupActive()
    const store = useChatStore()
    emitEvent({
      type: 'snapshot',
      runId: 'r1',
      phase: 'approval',
      content: '',
      agentName: '测试助手',
      toolName: null,
      pendingApprovals: [
        {
          requestId: 'apr-1',
          callId: 'call_1',
          toolName: 'builtin_shell',
          toolType: 'builtin',
          displayName: 'Shell 命令',
          arguments: { command: 'echo hi' },
        },
      ],
      timeline: [
        { kind: 'text', text: '准备执行命令' },
        {
          kind: 'approval',
          requestId: 'apr-1',
          callId: 'call_1',
          toolName: 'builtin_shell',
          toolType: 'builtin',
          displayName: 'Shell 命令',
          arguments: { command: 'echo hi' },
          status: 'pending',
        },
      ],
    })

    const generation = store.generations[1]!
    expect(generation.phase).toBe('approval')
    const approval = approvalSegments(generation)[0]!.approval
    expect(approval.status).toBe('pending')
    expect(approval.displayName).toBe('Shell 命令')
  })
})
