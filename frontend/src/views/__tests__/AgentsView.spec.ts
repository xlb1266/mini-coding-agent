import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { VueWrapper } from '@vue/test-utils'

import { deleteAgent, listAgents, setDefaultAgent } from '../../api/agents'
import type { AgentItem } from '../../api/agents'
import router from '../../router'
import AgentsView from '../AgentsView.vue'

vi.mock('../../api/agents', () => ({
  listAgents: vi.fn(),
  getAgent: vi.fn(),
  createAgent: vi.fn(),
  updateAgent: vi.fn(),
  deleteAgent: vi.fn(),
  setDefaultAgent: vi.fn(),
}))

function makeAgent(overrides: Partial<AgentItem> = {}): AgentItem {
  return {
    id: 1,
    name: '写作助手',
    description: '润色与改写文章',
    model: { id: 1, displayName: 'DeepSeek-V3', modelIdentifier: 'deepseek-chat' },
    promptVersion: 2,
    maxRounds: 25,
    autoCompact: true,
    compactThreshold: 80,
    compactKeepRounds: 4,
    compactSummaryTokens: 1000,
    isDefault: true,
    toolCount: 2,
    skillCount: 1,
    mcpCount: 0,
    createdAt: '2026-09-11T02:00:00+00:00',
    updatedAt: '2026-09-11T02:00:00+00:00',
    ...overrides,
  }
}

async function mountView(): Promise<VueWrapper> {
  const wrapper = mount(AgentsView, { global: { plugins: [createPinia(), router] } })
  await flushPromises()
  return wrapper
}

function findButtonByText(wrapper: VueWrapper, text: string) {
  return wrapper.findAll('button').find((button) => button.text().includes(text))
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(listAgents).mockResolvedValue([])
})

describe('AgentsView 列表页', () => {
  it('无 Agent 时展示空状态与新建入口', async () => {
    const wrapper = await mountView()

    expect(wrapper.text()).toContain('还没有创建 Agent')
    expect(findButtonByText(wrapper, '新建 Agent')).toBeTruthy()
  })

  it('渲染列表：默认徽标、模型名称与绑定数量', async () => {
    vi.mocked(listAgents).mockResolvedValue([
      makeAgent(),
      makeAgent({
        id: 2,
        name: '翻译助手',
        isDefault: false,
        model: { id: 2, displayName: 'Qwen-Max', modelIdentifier: 'qwen-max' },
        toolCount: 0,
        skillCount: 3,
        mcpCount: 1,
      }),
    ])
    const wrapper = await mountView()

    const rows = wrapper.findAll('tbody tr')
    expect(rows).toHaveLength(2)
    expect(rows[0]!.text()).toContain('默认')
    expect(rows[0]!.text()).toContain('DeepSeek-V3')
    expect(rows[0]!.text()).toContain('deepseek-chat')
    expect(rows[1]!.text()).toContain('翻译助手')
    expect(rows[1]!.text()).toContain('Qwen-Max')
    expect(rows[1]!.text()).toContain('设为默认')
    // 绑定数量按实际保存统计（含 0）
    expect(rows[1]!.text()).toContain('1')
  })

  it('设为默认调用接口并刷新列表', async () => {
    vi.mocked(listAgents)
      .mockResolvedValueOnce([makeAgent(), makeAgent({ id: 2, name: '翻译助手', isDefault: false })])
      .mockResolvedValueOnce([makeAgent({ isDefault: false }), makeAgent({ id: 2, name: '翻译助手', isDefault: true })])
    vi.mocked(setDefaultAgent).mockResolvedValue(makeAgent({ id: 2, name: '翻译助手', isDefault: true }))
    const wrapper = await mountView()

    const button = findButtonByText(wrapper, '设为默认')
    expect(button).toBeTruthy()
    await button!.trigger('click')
    await flushPromises()

    expect(setDefaultAgent).toHaveBeenCalledWith(2)
    expect(listAgents).toHaveBeenCalledTimes(2)
  })

  it('删除普通 Agent 确认后调用删除接口', async () => {
    vi.mocked(listAgents).mockResolvedValue([makeAgent({ isDefault: false })])
    vi.mocked(deleteAgent).mockResolvedValue(undefined)
    const wrapper = await mountView()

    await findButtonByText(wrapper, '删除')!.trigger('click')
    expect(wrapper.text()).toContain('确定要删除「写作助手」吗')

    // 无其他 Agent 时不需要选择新默认，直接确认
    await findButtonByText(wrapper, '确认删除')!.trigger('click')
    await flushPromises()

    expect(deleteAgent).toHaveBeenCalledWith(1, undefined)
  })

  it('删除默认 Agent 且还有其他 Agent 时必须先选择新默认', async () => {
    vi.mocked(listAgents).mockResolvedValue([
      makeAgent(),
      makeAgent({ id: 2, name: '翻译助手', isDefault: false }),
    ])
    vi.mocked(deleteAgent).mockResolvedValue(undefined)
    const wrapper = await mountView()

    const rows = wrapper.findAll('tbody tr')
    await rows[0]!.find('button.danger').trigger('click')

    expect(wrapper.text()).toContain('删除默认 Agent')
    expect(wrapper.text()).toContain('请选择新的默认 Agent')

    const confirm = findButtonByText(wrapper, '确认删除')!
    expect(confirm.attributes('disabled')).toBeDefined()

    // 选择新默认后可确认删除
    await wrapper.find('.delete-choice select').setValue(2)
    await confirm.trigger('click')
    await flushPromises()

    expect(deleteAgent).toHaveBeenCalledWith(1, 2)
  })
})
