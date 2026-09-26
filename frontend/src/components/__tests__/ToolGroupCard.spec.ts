import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ToolGroupCard from '../ToolGroupCard.vue'
import type { ToolActivity } from '../../stores/chat'

function makeTool(overrides: Partial<ToolActivity> = {}): ToolActivity {
  return {
    callId: 'call_1',
    toolName: 'builtin_grep',
    displayName: '内容搜索',
    serverName: null,
    toolType: 'builtin',
    status: 'success',
    arguments: { pattern: '**/*.md' },
    ...overrides,
  }
}

describe('ToolGroupCard 收起态', () => {
  it('默认只显示“使用N个工具”，组内子卡片不可见', () => {
    const wrapper = mount(ToolGroupCard, {
      props: { tools: [makeTool(), makeTool({ callId: 'call_2' }), makeTool({ callId: 'call_3' })] },
    })

    expect(wrapper.text()).toContain('使用3个工具')
    expect(wrapper.find('.tool-group-body').exists()).toBe(false)
    expect(wrapper.find('.tool-card').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('搜索文件')
  })

  it('单个工具也显示“使用1个工具”', () => {
    const wrapper = mount(ToolGroupCard, { props: { tools: [makeTool()] } })

    expect(wrapper.text()).toContain('使用1个工具')
  })

  it('组内有失败或取消时在标题行用文字点明', () => {
    const wrapper = mount(ToolGroupCard, {
      props: {
        tools: [makeTool(), makeTool({ callId: 'call_2', status: 'error' }), makeTool({ callId: 'call_3', status: 'cancelled' })],
      },
    })

    expect(wrapper.text()).toContain('1 个失败')
    expect(wrapper.text()).toContain('1 个已取消')
  })

  it('全部成功时不显示异常提示', () => {
    const wrapper = mount(ToolGroupCard, { props: { tools: [makeTool(), makeTool({ callId: 'call_2' })] } })

    expect(wrapper.text()).not.toContain('失败')
    expect(wrapper.text()).not.toContain('已取消')
  })
})

describe('ToolGroupCard 展开态', () => {
  it('点击展开组内子卡片（子卡片仍各自收起），再点击收起', async () => {
    const wrapper = mount(ToolGroupCard, {
      props: {
        tools: [
          makeTool(),
          makeTool({ callId: 'call_2', toolName: 'builtin_shell', arguments: { command: 'ls' } }),
        ],
      },
    })

    await wrapper.find('.tool-group-head').trigger('click')

    const cards = wrapper.findAll('.tool-card')
    expect(cards).toHaveLength(2)
    expect(wrapper.text()).toContain('搜索文件')
    expect(wrapper.text()).toContain('执行命令')
    // 子卡片默认收起：不含调用参数区块
    expect(wrapper.text()).not.toContain('调用参数')

    await wrapper.find('.tool-group-head').trigger('click')
    expect(wrapper.find('.tool-group-body').exists()).toBe(false)
  })

  it('组内子卡片的展开会继续向父级冒泡 toggle 事件', async () => {
    const wrapper = mount(ToolGroupCard, { props: { tools: [makeTool()] } })

    await wrapper.find('.tool-group-head').trigger('click')
    await wrapper.find('.tool-card-head').trigger('click')

    expect(wrapper.emitted('toggle')).toHaveLength(2)
    expect(wrapper.text()).toContain('调用参数')
  })
})
