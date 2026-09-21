import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { VueWrapper } from '@vue/test-utils'

import { listRuns, type RunItem, type RunListResult } from '../../api/runs'
import { listAgents } from '../../api/agents'
import router from '../../router'
import RunsView from '../RunsView.vue'

vi.mock('../../api/runs', () => ({
  listRuns: vi.fn(),
  getRunDetail: vi.fn(),
  getRunEventDetail: vi.fn(),
}))

vi.mock('../../api/agents', () => ({
  listAgents: vi.fn(),
  createAgent: vi.fn(),
  updateAgent: vi.fn(),
  deleteAgent: vi.fn(),
  setDefaultAgent: vi.fn(),
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

function listResult(items: RunItem[], total = items.length, page = 1, pageSize = 20): RunListResult {
  return { items, total, page, pageSize }
}

async function mountView(): Promise<VueWrapper> {
  const wrapper = mount(RunsView, { global: { plugins: [createPinia(), router] } })
  await flushPromises()
  return wrapper
}

beforeEach(async () => {
  vi.clearAllMocks()
  await router.push('/runs')
  await router.isReady()
  vi.mocked(listAgents).mockResolvedValue([])
  vi.mocked(listRuns).mockResolvedValue(listResult([]))
})

describe('RunsView 运行列表页', () => {
  it('渲染运行行：状态、Agent、模型、耗时、调用次数与 Token 用量', async () => {
    vi.mocked(listRuns).mockResolvedValue(
      listResult([
        makeRun(),
        makeRun({
          id: 2,
          status: 'failed',
          errorSummary: '模型服务错误：上游 500',
          durationMs: null,
          tokenUsage: null,
          rejectedToolCalls: 1,
        }),
      ]),
    )
    const wrapper = await mountView()

    const rows = wrapper.findAll('tbody tr')
    expect(rows).toHaveLength(2)
    expect(rows[0]!.text()).toContain('成功')
    expect(rows[0]!.text()).toContain('测试助手')
    expect(rows[0]!.text()).toContain('DeepSeek-V3')
    expect(rows[0]!.text()).toContain('3.3s')
    expect(rows[0]!.text()).toContain('输入 150 / 输出 50 / 缓存命中 68 token')
    // 失败行：错误摘要可见、Token 未知、拒绝次数单独标注
    expect(rows[1]!.text()).toContain('失败')
    expect(rows[1]!.text()).toContain('模型服务错误：上游 500')
    expect(rows[1]!.text()).toContain('未知')
    expect(rows[1]!.text()).toContain('+1 拒绝')
  })

  it('状态筛选变化时携带参数重新请求（回到第一页）', async () => {
    const wrapper = await mountView()
    expect(listRuns).toHaveBeenCalledWith({ page: 1, pageSize: 20 })

    const statusSelect = wrapper.findAll('select')[0]!
    await statusSelect.setValue('failed')
    await flushPromises()

    expect(listRuns).toHaveBeenCalledWith({ status: 'failed', page: 1, pageSize: 20 })
  })

  it('Agent 筛选变化时携带 agentId 重新请求', async () => {
    vi.mocked(listAgents).mockResolvedValue([
      { id: 1, name: '测试助手', description: '', model: null, promptVersion: 1, maxRounds: 25, workspaceDir: '', requireApproval: false, autoCompact: true, compactThreshold: 80, compactKeepRounds: 4, compactSummaryTokens: 1000, isDefault: true, toolCount: 0, skillCount: 0, mcpCount: 0, createdAt: '', updatedAt: '' },
    ])
    const wrapper = await mountView()

    const agentSelect = wrapper.findAll('select')[1]!
    await agentSelect.setValue('1')
    await flushPromises()

    expect(listRuns).toHaveBeenCalledWith({ agentId: 1, page: 1, pageSize: 20 })
  })

  it('分页：翻页按钮携带页码请求，边界禁用', async () => {
    // 后端按请求页码回显，与真实接口一致
    vi.mocked(listRuns).mockImplementation(async (query) =>
      listResult([makeRun()], 45, query?.page ?? 1, query?.pageSize ?? 20),
    )
    const wrapper = await mountView()

    const next = wrapper.findAll('button').find((button) => button.text() === '下一页')!
    const prev = wrapper.findAll('button').find((button) => button.text() === '上一页')!
    expect(wrapper.text()).toContain('第 1 / 3 页')
    expect((prev.element as HTMLButtonElement).disabled).toBe(true)

    await next.trigger('click')
    await flushPromises()
    expect(listRuns).toHaveBeenLastCalledWith({ page: 2, pageSize: 20 })

    await prev.trigger('click')
    await flushPromises()
    expect(listRuns).toHaveBeenLastCalledWith({ page: 1, pageSize: 20 })
  })

  it('点击行进入运行详情页', async () => {
    vi.mocked(listRuns).mockResolvedValue(listResult([makeRun({ id: 7 })]))
    const wrapper = await mountView()

    await wrapper.find('tbody tr').trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.path).toBe('/runs/7')
  })

  it('空列表提示引导到聊天页', async () => {
    vi.mocked(listRuns).mockResolvedValue(listResult([]))
    const wrapper = await mountView()

    expect(wrapper.text()).toContain('还没有运行记录')
  })
})
