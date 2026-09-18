import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { VueWrapper } from '@vue/test-utils'

import { getRunDetail, getRunEventDetail, type RunDetail, type RunItem } from '../../api/runs'
import router from '../../router'
import RunDetailView from '../RunDetailView.vue'

vi.mock('../../api/runs', () => ({
  listRuns: vi.fn(),
  getRunDetail: vi.fn(),
  getRunEventDetail: vi.fn(),
}))

function makeRun(overrides: Partial<RunItem> = {}): RunItem {
  return {
    id: 5,
    runUid: 'abc1234567890def',
    sessionId: 1,
    agentId: 1,
    agentName: '测试助手',
    modelName: 'DeepSeek-V3',
    modelIdentifier: 'deepseek-chat',
    status: 'completed',
    finishReason: 'model_finished',
    errorSummary: null,
    userInputPreview: '查一下时间',
    startedAt: '2026-09-14T02:00:00+00:00',
    finishedAt: '2026-09-14T02:00:03+00:00',
    durationMs: 3250,
    modelCalls: 2,
    toolCalls: 1,
    rejectedToolCalls: 0,
    firstOutputMs: 500,
    tokenUsage: { promptTokens: 150, completionTokens: 50, totalTokens: 200, cachedTokens: 68 },
    tokensIncomplete: false,
    ...overrides,
  }
}

function makeDetail(run: RunItem): RunDetail {
  return {
    run,
    events: [
      { id: 21, seq: 1, eventType: 'run_start', payload: { type: 'run_start', agentName: '测试助手' }, hasDetail: false },
      { id: 22, seq: 2, eventType: 'model_request_start', payload: { type: 'model_request_start', round: 1, callId: 'mr-1' }, hasDetail: false },
      {
        id: 23,
        seq: 3,
        eventType: 'tool_call_start',
        payload: {
          type: 'tool_call_start',
          round: 1,
          callId: 'call_1',
          toolName: 'builtin_current_time',
          toolType: 'builtin',
          displayName: '当前时间',
          serverName: null,
          arguments: { timezone: 'Asia/Shanghai' },
        },
        hasDetail: false,
      },
      {
        id: 24,
        seq: 4,
        eventType: 'tool_call_end',
        payload: {
          type: 'tool_call_end',
          round: 1,
          callId: 'call_1',
          toolName: 'builtin_current_time',
          toolType: 'builtin',
          status: 'success',
          durationMs: 12,
          summary: '2026-09-14（Asia/Shanghai）',
          result: null,
        },
        hasDetail: true,
      },
      {
        id: 25,
        seq: 5,
        eventType: 'model_request_end',
        payload: { type: 'model_request_end', round: 1, callId: 'mr-1', status: 'success', durationMs: 800, usage: null },
        hasDetail: false,
      },
      {
        id: 26,
        seq: 6,
        eventType: 'run_end',
        payload: {
          type: 'run_end',
          status: 'completed',
          reason: 'model_finished',
          roundsUsed: 1,
          contentPreview: '现在是中午',
          error: null,
        },
        hasDetail: true,
      },
    ],
  }
}

async function mountView(runId = 5): Promise<VueWrapper> {
  const wrapper = mount(RunDetailView, {
    global: { plugins: [createPinia(), router] },
  })
  await router.push(`/runs/${runId}`)
  await flushPromises()
  return wrapper
}

beforeEach(async () => {
  vi.clearAllMocks()
  await router.push('/runs')
  await router.isReady()
})

describe('RunDetailView 运行详情页', () => {
  it('渲染运行摘要：状态、指标口径与结束原因分开展示', async () => {
    vi.mocked(getRunDetail).mockResolvedValue(makeDetail(makeRun()))
    const wrapper = await mountView()

    const text = wrapper.text()
    expect(text).toContain('成功')
    expect(text).toContain('结束原因：模型完成回复')
    expect(text).toContain('测试助手')
    expect(text).toContain('DeepSeek-V3')
    expect(text).toContain('3.3s')
    expect(text).toContain('500ms')
    expect(text).toContain('2 次')   // 模型调用
    expect(text).toContain('1 次')   // 工具调用
    expect(text).toContain('输入 150 / 输出 50 / 缓存命中 68 token')
  })

  it('渲染时间线：Agent 开始、模型步骤、工具卡片与最终回答', async () => {
    vi.mocked(getRunDetail).mockResolvedValue(makeDetail(makeRun()))
    const wrapper = await mountView()

    const text = wrapper.text()
    expect(text).toContain('Agent 开始')
    expect(text).toContain('模型请求 · 第 1 轮')
    expect(text).toContain('当前时间')       // 工具卡片（与聊天页同一组件）
    expect(text).toContain('2026-09-14（Asia/Shanghai）') // 结果摘要
    expect(text).toContain('运行结束 · 成功')
    expect(text).toContain('现在是中午')
  })

  it('无正文输出时明确标注（不是空白）', async () => {
    vi.mocked(getRunDetail).mockResolvedValue(makeDetail(makeRun({ firstOutputMs: null, status: 'failed', finishReason: 'model_error', errorSummary: '上游 500' })))
    const wrapper = await mountView()

    expect(wrapper.text()).toContain('无正文输出')
    expect(wrapper.text()).toContain('失败')
    expect(wrapper.text()).toContain('上游 500')
  })

  it('详细载荷按需加载：工具完整结果加载后填入卡片', async () => {
    vi.mocked(getRunDetail).mockResolvedValue(makeDetail(makeRun()))
    const wrapper = await mountView()

    // 按钮在卡片展开后可见：先展开工具卡片
    const cardHead = wrapper.find('.tool-card-head')
    expect(cardHead.exists()).toBe(true)
    await cardHead.trigger('click')

    const loadButton = wrapper.findAll('button').find((button) => button.text() === '加载完整结果')
    expect(loadButton).toBeDefined()
    vi.mocked(getRunEventDetail).mockResolvedValue({
      eventId: 24,
      seq: 4,
      eventType: 'tool_call_end',
      payload: { result: { text: '完整工具结果', truncated: false, totalLength: 6 } },
    })

    await loadButton!.trigger('click')
    await flushPromises()

    expect(getRunEventDetail).toHaveBeenCalledWith(5, 24)
    expect(wrapper.text()).toContain('完整工具结果')
  })

  it('最终回答的完整正文按需加载', async () => {
    vi.mocked(getRunDetail).mockResolvedValue(makeDetail(makeRun()))
    const wrapper = await mountView()

    const loadButton = wrapper.findAll('button').find((button) => button.text() === '查看完整正文')
    expect(loadButton).toBeDefined()
    vi.mocked(getRunEventDetail).mockResolvedValue({
      eventId: 26,
      seq: 6,
      eventType: 'run_end',
      payload: { content: '现在是北京时间中午十二点整，这是一段更长的完整正文。', truncated: false },
    })

    await loadButton!.trigger('click')
    await flushPromises()

    expect(getRunEventDetail).toHaveBeenCalledWith(5, 26)
    expect(wrapper.text()).toContain('这是一段更长的完整正文')
  })

  it('运行记录不存在时显示空态并可返回列表', async () => {
    vi.mocked(getRunDetail).mockRejectedValue(new Error('404'))
    const wrapper = await mountView(999)

    expect(wrapper.text()).toContain('运行记录不存在')

    await wrapper.findAll('button').find((button) => button.text() === '返回运行列表')!.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/runs')
  })
})
