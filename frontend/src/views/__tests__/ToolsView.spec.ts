import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { VueWrapper } from '@vue/test-utils'

import { listTools, updateTool } from '../../api/tools'
import type { ToolItem } from '../../api/tools'
import router from '../../router'
import ToolsView from '../ToolsView.vue'

vi.mock('../../api/tools', () => ({
  listTools: vi.fn(),
  getTool: vi.fn(),
  updateTool: vi.fn(),
  executeTool: vi.fn(),
}))

function makeTool(overrides: Partial<ToolItem> = {}): ToolItem {
  return {
    id: 1,
    key: 'builtin_current_time',
    name: '当前时间',
    description: '查询指定时区的当前日期和时间。适用场景：记录事件发生时间。',
    parameters: [
      {
        name: 'timezone',
        type: 'string',
        required: false,
        description: 'IANA 时区名称',
        example: 'Asia/Shanghai',
      },
    ],
    enabled: true,
    isBuiltin: true,
    createdAt: '2026-09-11T02:00:00+00:00',
    updatedAt: '2026-09-11T02:00:00+00:00',
    ...overrides,
  }
}

async function mountView(): Promise<VueWrapper> {
  const wrapper = mount(ToolsView, { global: { plugins: [createPinia(), router] } })
  await flushPromises()
  return wrapper
}

function findButtonByText(wrapper: VueWrapper, text: string) {
  return wrapper.findAll('button').find((button) => button.text().includes(text))
}

beforeEach(async () => {
  vi.clearAllMocks()
  await router.push('/chat')
  vi.mocked(listTools).mockResolvedValue([
    makeTool(),
    makeTool({
      id: 2,
      key: 'builtin_shell',
      name: 'Shell 命令执行',
      enabled: false,
      parameters: [
        { name: 'command', type: 'string', required: true, description: '要执行的命令' },
        { name: 'timeout', type: 'integer', required: false, description: '超时秒数' },
      ],
    }),
  ])
})

describe('ToolsView 工具列表页', () => {
  it('渲染工具列表：名称、key、内置徽标、启停状态与参数概览', async () => {
    const wrapper = await mountView()

    const rows = wrapper.findAll('tbody tr')
    expect(rows).toHaveLength(2)
    expect(wrapper.text()).toContain('内置')
    expect(wrapper.text()).toContain('builtin_shell')
    // 可选参数带（可选）标注，必填参数不带
    expect(rows[0]!.text()).toContain('timezone（可选）')
    expect(rows[1]!.text()).toContain('command、timeout（可选）')
    expect(rows[0]!.text()).toContain('启用')
    expect(rows[1]!.text()).toContain('停用')
  })

  it('点击启停按钮调用 updateTool 并刷新列表', async () => {
    vi.mocked(updateTool).mockResolvedValue(makeTool({ enabled: false }))
    const wrapper = await mountView()

    await findButtonByText(wrapper, '停用')!.trigger('click')
    await flushPromises()

    expect(vi.mocked(updateTool)).toHaveBeenCalledWith(1, { enabled: false })
    expect(vi.mocked(listTools)).toHaveBeenCalledTimes(2)
  })

  it('启停失败时展示失败横幅', async () => {
    vi.mocked(updateTool).mockRejectedValue(new Error('网络请求失败'))
    const wrapper = await mountView()

    await findButtonByText(wrapper, '停用')!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('停用失败')
    expect(wrapper.text()).toContain('网络请求失败')
  })

  it('点击详情跳转到工具详情页', async () => {
    const wrapper = await mountView()

    await findButtonByText(wrapper, '详情')!.trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.path).toBe('/tools/1')
  })
})
