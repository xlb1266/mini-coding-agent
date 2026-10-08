import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { VueWrapper } from '@vue/test-utils'

import { listAgents } from '../../api/agents'
import type { AgentItem } from '../../api/agents'
import {
  createChatSession,
  getChatMessages,
  listChatSessions,
  openChatEventStream,
  selectChatAgent,
  sendChatMessage,
  stopChatGeneration,
} from '../../api/chat'
import type { ChatMessage, ChatSessionSummary } from '../../api/chat'
import router from '../../router'
import ChatView from '../ChatView.vue'

vi.mock('../../api/agents', () => ({
  listAgents: vi.fn(),
  getAgent: vi.fn(),
  createAgent: vi.fn(),
  updateAgent: vi.fn(),
  deleteAgent: vi.fn(),
  setDefaultAgent: vi.fn(),
}))

vi.mock('../../api/chat', () => ({
  listChatSessions: vi.fn(),
  createChatSession: vi.fn(),
  getChatMessages: vi.fn(),
  selectChatAgent: vi.fn(),
  sendChatMessage: vi.fn(),
  stopChatGeneration: vi.fn(),
  openChatEventStream: vi.fn(),
}))

function makeAgent(): AgentItem {
  return {
    id: 1,
    name: '测试助手',
    description: '',
    model: { id: 1, displayName: 'DeepSeek-V3', modelIdentifier: 'deepseek-chat' },
    promptVersion: 1,
    maxRounds: 25,
    workspaceDir: '',
    approvalLevel: 'off',
    autonomyMode: 'stage_confirm',
    shellWhitelist: '',
    autoCompact: true,
    compactThreshold: 80,
    compactKeepRounds: 4,
    compactSummaryTokens: 1000,
    isDefault: true,
    toolCount: 0,
    skillCount: 0,
    mcpCount: 0,
    createdAt: '2026-09-11T02:00:00+00:00',
    updatedAt: '2026-09-11T02:00:00+00:00',
  }
}

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

function makeAgentsAndSession(
  options: { session?: Partial<ChatSessionSummary>; messages?: ChatMessage[] } = {},
): void {
  vi.mocked(listAgents).mockResolvedValue([makeAgent()])
  const session = makeSession(options.session)
  vi.mocked(listChatSessions).mockResolvedValue([session])
  vi.mocked(getChatMessages).mockResolvedValue({
    session,
    messages: options.messages ?? [],
    generation: null,
  })
  vi.mocked(openChatEventStream).mockResolvedValue(undefined)
}

async function mountView(): Promise<VueWrapper> {
  const wrapper = mount(ChatView, { global: { plugins: [createPinia(), router] } })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(listAgents).mockResolvedValue([])
  vi.mocked(listChatSessions).mockResolvedValue([])
  vi.mocked(openChatEventStream).mockResolvedValue(undefined)
})

describe('ChatView 空状态', () => {
  it('没有 Agent 时提示先创建并给出入口，不渲染输入区', async () => {
    const wrapper = await mountView()

    expect(wrapper.text()).toContain('还没有可用的 Agent')
    const goButton = wrapper.findAll('button').find((b) => b.text() === '前往 Agent 管理')
    expect(goButton).toBeTruthy()
    expect(wrapper.find('textarea').exists()).toBe(false)
  })

  it('有 Agent 但没有会话时提示新建', async () => {
    vi.mocked(listAgents).mockResolvedValue([makeAgent()])
    vi.mocked(listChatSessions).mockResolvedValue([])

    const wrapper = await mountView()

    expect(wrapper.text()).toContain('开始一段新的对话')
    expect(wrapper.find('textarea').exists()).toBe(false)
  })

  it('点击新建会话调用创建接口', async () => {
    makeAgentsAndSession()
    vi.mocked(listChatSessions).mockResolvedValue([])
    vi.mocked(createChatSession).mockResolvedValue(makeSession())

    const wrapper = await mountView()
    const button = wrapper.findAll('button').find((b) => b.text().includes('新建会话'))
    await button!.trigger('click')
    await flushPromises()

    expect(createChatSession).toHaveBeenCalledWith(undefined)
  })
})

describe('ChatView 会话与消息渲染', () => {
  it('渲染会话列表（标题、Agent 名）与左右分侧的消息气泡', async () => {
    makeAgentsAndSession({
      messages: [
        makeMessage(),
        makeMessage({
          id: 2,
          role: 'assistant',
          content: '你好，有什么可以帮你？',
          agentId: 1,
          agentName: '测试助手',
          seq: 2,
        }),
      ],
    })

    const wrapper = await mountView()

    expect(wrapper.text()).toContain('新会话')
    expect(wrapper.text()).toContain('测试助手')
    const rows = wrapper.findAll('.message-row')
    expect(rows).toHaveLength(2)
    expect(rows[0]!.classes()).toContain('is-user')
    expect(rows[1]!.classes()).toContain('is-assistant')
    expect(rows[1]!.text()).toContain('你好，有什么可以帮你？')
  })

  it('未完成回复带“未完成”标记', async () => {
    makeAgentsAndSession({
      messages: [
        makeMessage({ id: 2, role: 'assistant', content: '半截回复', status: 'incomplete', seq: 2 }),
      ],
    })

    const wrapper = await mountView()

    expect(wrapper.text()).toContain('未完成')
  })

  it('会话原 Agent 被删除时提示选择且输入禁用', async () => {
    makeAgentsAndSession({ session: { agentId: null, agentName: null } })

    const wrapper = await mountView()

    expect(wrapper.text()).toContain('请选择 Agent')
    expect(wrapper.find('textarea').attributes('disabled')).toBeDefined()
  })
})

describe('ChatView 发送与停止', () => {
  it('Enter 发送成功后清空输入框', async () => {
    makeAgentsAndSession()
    vi.mocked(sendChatMessage).mockResolvedValue({
      session: makeSession({ isGenerating: true }),
      userMessage: makeMessage(),
      generation: { phase: 'thinking', content: '', agentName: '测试助手' },
    })

    const wrapper = await mountView()
    const textarea = wrapper.find('textarea')
    await textarea.setValue('你好')
    await textarea.trigger('keydown', { key: 'Enter' })
    await flushPromises()

    expect(sendChatMessage).toHaveBeenCalledWith(1, '你好')
    expect((textarea.element as HTMLTextAreaElement).value).toBe('')
  })

  it('输入法组字状态的回车不触发发送', async () => {
    makeAgentsAndSession()
    vi.mocked(sendChatMessage).mockResolvedValue({
      session: makeSession(),
      userMessage: makeMessage(),
      generation: { phase: 'thinking', content: '', agentName: '测试助手' },
    })

    const wrapper = await mountView()
    const textarea = wrapper.find('textarea')
    await textarea.setValue('你好')
    await textarea.trigger('keydown', { key: 'Enter', isComposing: true, keyCode: 229 })
    await flushPromises()

    expect(sendChatMessage).not.toHaveBeenCalled()
  })

  it('Shift + Enter 不触发发送', async () => {
    makeAgentsAndSession()

    const wrapper = await mountView()
    const textarea = wrapper.find('textarea')
    await textarea.setValue('你好')
    await textarea.trigger('keydown', { key: 'Enter', shiftKey: true })
    await flushPromises()

    expect(sendChatMessage).not.toHaveBeenCalled()
  })

  it('空白内容不能发送（按钮禁用）', async () => {
    makeAgentsAndSession()

    const wrapper = await mountView()
    const textarea = wrapper.find('textarea')
    await textarea.setValue('   ')
    const button = wrapper.findAll('button').find((b) => b.text() === '发送')

    expect(button!.attributes('disabled')).toBeDefined()
  })

  it('发送失败时保留输入内容并提示原因', async () => {
    makeAgentsAndSession()
    vi.mocked(sendChatMessage).mockRejectedValue(new Error('网络请求失败'))

    const wrapper = await mountView()
    const textarea = wrapper.find('textarea')
    await textarea.setValue('这条消息发送不了')
    await textarea.trigger('keydown', { key: 'Enter' })
    await flushPromises()

    expect((textarea.element as HTMLTextAreaElement).value).toBe('这条消息发送不了')
    expect(wrapper.text()).toContain('网络请求失败')
  })

  it('生成中显示思考状态与停止按钮，Agent 选择禁用，点击停止调用接口', async () => {
    makeAgentsAndSession()
    vi.mocked(getChatMessages).mockResolvedValue({
      session: makeSession({ isGenerating: true }),
      messages: [makeMessage()],
      generation: { phase: 'thinking', content: '', agentName: '测试助手' },
    })
    vi.mocked(stopChatGeneration).mockResolvedValue({
      outcome: 'cancelled',
      message: null,
      content: '',
      errorMessage: null,
      hint: null,
      errorType: null,
    })

    const wrapper = await mountView()

    expect(wrapper.text()).toContain('正在思考')
    expect(wrapper.find('.agent-select').attributes('disabled')).toBeDefined()
    const stopButton = wrapper.findAll('button').find((b) => b.text() === '停止生成')
    expect(stopButton).toBeTruthy()

    await stopButton!.trigger('click')
    await flushPromises()

    expect(stopChatGeneration).toHaveBeenCalledWith(1)
  })

  it('流式生成中显示部分正文', async () => {
    makeAgentsAndSession()
    vi.mocked(getChatMessages).mockResolvedValue({
      session: makeSession({ isGenerating: true }),
      messages: [],
      generation: { phase: 'generating', content: '已经生成的部分', agentName: '测试助手' },
    })

    const wrapper = await mountView()

    expect(wrapper.text()).toContain('正在生成')
    expect(wrapper.text()).toContain('已经生成的部分')
  })

  it('生成失败展示独立错误提示，不混入聊天记录', async () => {
    makeAgentsAndSession()
    const { useChatStore } = await import('../../stores/chat')
    const pinia = createPinia()
    setActivePinia(pinia)
    const wrapper = mount(ChatView, { global: { plugins: [pinia, router] } })
    await flushPromises()
    const store = useChatStore()
    store.generationErrors[1] = {
      message: '与模型服务的连接中断',
      hint: '请核对服务地址与端口',
      errorType: 'network',
      content: '',
      savedMessage: null,
    }
    await flushPromises()

    expect(wrapper.text()).toContain('与模型服务的连接中断')
    expect(wrapper.text()).toContain('请核对服务地址与端口')
    expect(wrapper.findAll('.message-bubble')).toHaveLength(0)
  })
})

describe('ChatView 切换 Agent', () => {
  it('选择新 Agent 调用切换接口', async () => {
    makeAgentsAndSession()
    vi.mocked(selectChatAgent).mockResolvedValue(makeSession({ agentId: 1, agentName: '测试助手' }))

    const wrapper = await mountView()
    const select = wrapper.find('.agent-select')
    await select.setValue('1')
    await flushPromises()

    expect(selectChatAgent).toHaveBeenCalledWith(1, 1)
  })
})

describe('ChatView 工具过程展示', () => {
  it('生成中：默认只显示“使用N个工具”一行，点击才展开子卡片与参数', async () => {
    makeAgentsAndSession()
    const { useChatStore } = await import('../../stores/chat')
    const pinia = createPinia()
    setActivePinia(pinia)
    const wrapper = mount(ChatView, { global: { plugins: [pinia, router] } })
    await flushPromises()
    const store = useChatStore()
    store.generations[1] = {
      runId: 'r1',
      startedAt: null,
      reasoningStartedAt: null,
      phase: 'tooling',
      agentName: '测试助手',
      toolName: '当前时间',
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
            status: 'running',
            arguments: { timezone: 'Asia/Shanghai' },
          },
        },
      ],
    }
    await flushPromises()

    expect(wrapper.text()).toContain('先查时间：')
    expect(wrapper.text()).toContain('处理中')
    expect(wrapper.text()).toContain('正在调用工具：当前时间')
    // 工具默认折叠：只有组标题行，看不到子卡片与参数
    expect(wrapper.text()).toContain('使用1个工具')
    expect(wrapper.text()).not.toContain('查询时间')
    expect(wrapper.text()).not.toContain('Asia/Shanghai')

    await wrapper.find('.tool-group-head').trigger('click')
    expect(wrapper.text()).toContain('查询时间')
    expect(wrapper.find('.tool-pill').text()).toBe('Asia/Shanghai')
    expect(wrapper.find('.tool-status-icon').attributes('aria-label')).toBe('执行中')
    // 正文在工具组上方（DOM 顺序）
    const bubble = wrapper.find('.is-streaming')
    expect(bubble.text().indexOf('先查时间：')).toBeLessThan(bubble.text().indexOf('使用1个工具'))
  })

  it('已结束回复带时间线：卡片与正文交错渲染在消息气泡内', async () => {
    makeAgentsAndSession({
      messages: [
        makeMessage(),
        makeMessage({ id: 2, role: 'assistant', content: '先查时间：上午十点', seq: 2 }),
      ],
    })
    const { useChatStore } = await import('../../stores/chat')
    const pinia = createPinia()
    setActivePinia(pinia)
    const wrapper = mount(ChatView, { global: { plugins: [pinia, router] } })
    await flushPromises()
    const store = useChatStore()
    store.toolTimelines[2] = [
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
        },
      },
      { kind: 'text', text: '上午十点' },
    ]
    await flushPromises()

    const text = wrapper.text()
    expect(text).toContain('使用1个工具')
    expect(text.indexOf('先查时间：')).toBeLessThan(text.indexOf('上午十点'))
    expect(wrapper.findAllComponents({ name: 'ToolGroupCard' }).length).toBe(1)
    // 已结束的运行同样默认收起，卡片不直接铺开
    expect(wrapper.find('.tool-card').exists()).toBe(false)
  })

  it('无保存消息的收尾运行：孤儿卡片渲染在消息之后', async () => {
    makeAgentsAndSession({ messages: [makeMessage()] })
    const { useChatStore } = await import('../../stores/chat')
    const pinia = createPinia()
    setActivePinia(pinia)
    const wrapper = mount(ChatView, { global: { plugins: [pinia, router] } })
    await flushPromises()
    const store = useChatStore()
    store.orphanSegments[1] = [
      {
        kind: 'tool',
        tool: {
          callId: 'call_1',
          toolName: 'builtin_shell',
          displayName: '执行命令',
          serverName: null,
          toolType: 'builtin',
          status: 'cancelled',
        },
      },
    ]
    await flushPromises()

    expect(wrapper.find('.tool-orphan').exists()).toBe(true)
    expect(wrapper.text()).toContain('使用1个工具')
    expect(wrapper.text()).toContain('1 个已取消')
  })

  it('运行中显示“处理中 + 已用时长”，思考默认收起、点击才看正文', async () => {
    makeAgentsAndSession()
    vi.mocked(getChatMessages).mockResolvedValue({
      session: makeSession({ isGenerating: true }),
      messages: [makeMessage()],
      generation: {
        phase: 'thinking',
        content: '',
        agentName: '测试助手',
        startedAt: Date.now() - 112_000,
        timeline: [{ kind: 'reasoning', text: '先看看目录结构', durationMs: null }],
      },
    })

    const wrapper = await mountView()

    expect(wrapper.text()).toContain('处理中 1m 52s')
    expect(wrapper.text()).toContain('思考中')
    expect(wrapper.text()).not.toContain('先看看目录结构')

    await wrapper.find('.think-head').trigger('click')
    expect(wrapper.text()).toContain('先看看目录结构')
  })

  it('上翻暂停跟随后显示“回到最新消息”，点击后回到底部并恢复跟随', async () => {
    makeAgentsAndSession({
      messages: [
        makeMessage(),
        makeMessage({ id: 2, role: 'assistant', content: '长回复'.repeat(200), seq: 2 }),
      ],
    })
    const wrapper = await mountView()

    expect(wrapper.find('.jump-latest').exists()).toBe(false)

    const messages = wrapper.find('.messages')
    Object.defineProperty(messages.element, 'scrollHeight', { value: 2000, configurable: true })
    Object.defineProperty(messages.element, 'clientHeight', { value: 300, configurable: true })
    Object.defineProperty(messages.element, 'scrollTop', { value: 0, configurable: true, writable: true })
    await messages.trigger('scroll')

    const jump = wrapper.find('.jump-latest')
    expect(jump.exists()).toBe(true)

    await jump.trigger('click')
    expect((messages.element as HTMLElement).scrollTop).toBe(2000)
    expect(wrapper.find('.jump-latest').exists()).toBe(false)
  })
})
