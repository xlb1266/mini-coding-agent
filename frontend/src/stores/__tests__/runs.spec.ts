import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  getRunDetail,
  getRunEventDetail,
  listRuns,
  type RunDetail,
  type RunItem,
} from '../../api/runs'
import { useRunsStore } from '../runs'

vi.mock('../../api/runs', () => ({
  listRuns: vi.fn(),
  getRunDetail: vi.fn(),
  getRunEventDetail: vi.fn(),
}))

function makeRun(overrides: Partial<RunItem> = {}): RunItem {
  return {
    id: 1,
    runUid: 'abc12345',
    sessionId: 1,
    agentId: 1,
    agentName: '测试助手',
    modelName: 'DeepSeek-V3',
    modelIdentifier: 'deepseek-chat',
    status: 'completed',
    finishReason: 'model_finished',
    errorSummary: null,
    userInputPreview: '打个招呼',
    startedAt: '2026-09-14T02:00:00+00:00',
    finishedAt: '2026-09-14T02:00:03+00:00',
    durationMs: 3000,
    modelCalls: 1,
    toolCalls: 0,
    rejectedToolCalls: 0,
    firstOutputMs: 500,
    tokenUsage: { promptTokens: 10, completionTokens: 5, totalTokens: 15, cachedTokens: null },
    tokensIncomplete: false,
    ...overrides,
  }
}

function makeDetail(runId = 1): RunDetail {
  return {
    run: makeRun({ id: runId }),
    events: [
      { id: 11, seq: 1, eventType: 'run_start', payload: { type: 'run_start', agentName: '测试助手' }, hasDetail: false },
      { id: 12, seq: 2, eventType: 'run_end', payload: { type: 'run_end', status: 'completed' }, hasDetail: true },
    ],
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

describe('runs store', () => {
  it('fetchRuns 携带筛选与分页参数并保存结果', async () => {
    const store = useRunsStore()
    store.statusFilter = 'failed'
    store.agentFilter = 3
    store.page = 2
    store.pageSize = 10
    vi.mocked(listRuns).mockResolvedValue({
      items: [makeRun()],
      total: 11,
      page: 2,
      pageSize: 10,
    })

    await store.fetchRuns()

    expect(listRuns).toHaveBeenCalledWith({ status: 'failed', agentId: 3, page: 2, pageSize: 10 })
    expect(store.items).toHaveLength(1)
    expect(store.total).toBe(11)
    expect(store.totalPages).toBe(2)
    expect(store.loaded).toBe(true)
  })

  it('setFilters 回到第一页再拉取', async () => {
    const store = useRunsStore()
    store.page = 4
    vi.mocked(listRuns).mockResolvedValue({ items: [], total: 0, page: 1, pageSize: 20 })

    await store.setFilters('cancelled', null)

    expect(store.statusFilter).toBe('cancelled')
    expect(store.page).toBe(1)
    expect(listRuns).toHaveBeenCalledWith({ status: 'cancelled', agentId: undefined, page: 1, pageSize: 20 })
  })

  it('goToPage 边界收敛：不越界也不重复拉取', async () => {
    const store = useRunsStore()
    // 后端按请求页码回显（与真实接口一致），翻页后 store 保存回显页码
    vi.mocked(listRuns).mockImplementation(async (query) => ({
      items: [makeRun()],
      total: 45,
      page: query?.page ?? 1,
      pageSize: query?.pageSize ?? 20,
    }))
    await store.fetchRuns()
    expect(store.totalPages).toBe(3)

    await store.goToPage(0)
    expect(store.page).toBe(1)
    await store.goToPage(99)
    expect(store.page).toBe(3)
    // 已在当前页时不再请求
    const calls = vi.mocked(listRuns).mock.calls.length
    await store.goToPage(3)
    expect(vi.mocked(listRuns).mock.calls.length).toBe(calls)
  })

  it('fetchDetail 缓存：同一 Run 只请求一次', async () => {
    const store = useRunsStore()
    vi.mocked(getRunDetail).mockResolvedValue(makeDetail())

    const first = await store.fetchDetail(1)
    const second = await store.fetchDetail(1)

    expect(getRunDetail).toHaveBeenCalledTimes(1)
    expect(first).toBe(second)
  })

  it('loadEventDetail 按需加载并缓存详细载荷', async () => {
    const store = useRunsStore()
    vi.mocked(getRunEventDetail).mockResolvedValue({
      eventId: 12,
      seq: 2,
      eventType: 'run_end',
      payload: { content: '完整正文', truncated: false },
    })

    const first = await store.loadEventDetail(1, 12)
    const second = await store.loadEventDetail(1, 12)

    expect(getRunEventDetail).toHaveBeenCalledTimes(1)
    expect(first.content).toBe('完整正文')
    expect(store.eventDetails['1:12'].content).toBe('完整正文')
    expect(second).toBe(first)
  })
})
