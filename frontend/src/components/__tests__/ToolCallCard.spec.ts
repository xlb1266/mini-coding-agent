import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ToolCallCard from '../ToolCallCard.vue'
import type { ToolActivity } from '../../stores/chat'

function makeTool(overrides: Partial<ToolActivity> = {}): ToolActivity {
  return {
    callId: 'call_1',
    toolName: 'builtin_current_time',
    displayName: '当前时间',
    serverName: null,
    toolType: 'builtin',
    status: 'running',
    ...overrides,
  }
}

describe('ToolCallCard 收起态', () => {
  it('显示易读名称、类型、状态文字与图标和摘要，不显示参数', () => {
    const wrapper = mount(ToolCallCard, {
      props: { tool: makeTool({ status: 'success', durationMs: 1500, summary: '2026-09-12T10:00:00' }) },
    })

    expect(wrapper.text()).toContain('当前时间')
    expect(wrapper.text()).toContain('内置工具')
    // 状态同时有文字与视觉标识（不只靠颜色）
    expect(wrapper.text()).toContain('完成')
    expect(wrapper.find('.tool-status-icon').text()).toBe('✓')
    expect(wrapper.text()).toContain('1.5s')
    expect(wrapper.text()).toContain('2026-09-12T10:00:00')
    expect(wrapper.find('.tool-card-body').exists()).toBe(false)
  })

  it('MCP 工具同时展示 Server 名称与工具名', () => {
    const wrapper = mount(ToolCallCard, {
      props: {
        tool: makeTool({
          toolType: 'mcp',
          toolName: 'mcp3_get_weather',
          displayName: 'get_weather',
          serverName: '天气服务',
        }),
      },
    })

    expect(wrapper.find('.tool-name').text()).toBe('天气服务 · get_weather')
    expect(wrapper.text()).toContain('MCP')
  })

  it('失败与取消使用不同的文字和图标标注', () => {
    const failed = mount(ToolCallCard, { props: { tool: makeTool({ status: 'error' }) } })
    expect(failed.text()).toContain('失败')
    expect(failed.find('.tool-status-icon').text()).toBe('✕')

    const cancelled = mount(ToolCallCard, { props: { tool: makeTool({ status: 'cancelled' }) } })
    expect(cancelled.text()).toContain('已取消')
    expect(cancelled.find('.tool-status-icon').text()).toBe('○')

    const unknown = mount(ToolCallCard, { props: { tool: makeTool({ status: 'unknown' }) } })
    expect(unknown.text()).toContain('状态未知')
    expect(unknown.find('.tool-status-icon').text()).toBe('?')
  })
})

describe('ToolCallCard 展开态', () => {
  it('展开显示整理后的输入参数与结果，收起后隐藏', async () => {
    const wrapper = mount(ToolCallCard, {
      props: {
        tool: makeTool({
          status: 'success',
          arguments: { timezone: 'Asia/Shanghai', count: 3 },
          result: { text: '{\n  "datetime": "10:00"\n}', truncated: false, totalLength: 22 },
        }),
      },
    })

    await wrapper.find('.tool-card-head').trigger('click')

    expect(wrapper.find('.tool-card-body').exists()).toBe(true)
    const keys = wrapper.findAll('.arg-key').map((el) => el.text())
    expect(keys).toEqual(['timezone', 'count'])
    expect(wrapper.text()).toContain('Asia/Shanghai')
    expect(wrapper.find('.tool-result').text()).toContain('"datetime": "10:00"')

    await wrapper.find('.tool-card-head').trigger('click')
    expect(wrapper.find('.tool-card-body').exists()).toBe(false)
  })

  it('失败时展示可读的失败原因，不出现堆栈', () => {
    const wrapper = mount(ToolCallCard, {
      props: {
        tool: makeTool({
          status: 'error',
          arguments: { timezone: 'Mars/Olympus' },
          result: { text: '无法识别的时区「Mars/Olympus」\n参数问题：timezone: 请使用 IANA 名称', truncated: false, totalLength: 40 },
        }),
      },
    })

    expect(wrapper.text()).not.toContain('失败原因') // 收起态不显示区块标题
    return wrapper.find('.tool-card-head').trigger('click').then(() => {
      expect(wrapper.text()).toContain('失败原因')
      expect(wrapper.text()).toContain('无法识别的时区')
      expect(wrapper.text()).not.toContain('Traceback')
    })
  })

  it('长结果默认只展示前段并标注截断，“展开全部”后显示完整内容', async () => {
    const longText = `{\n  "data": "${'雨'.repeat(2000)}"\n}`
    const wrapper = mount(ToolCallCard, {
      props: {
        tool: makeTool({
          status: 'success',
          result: { text: longText, truncated: false, totalLength: longText.length },
        }),
      },
    })
    await wrapper.find('.tool-card-head').trigger('click')

    expect(wrapper.text()).toContain('已截断')
    expect(wrapper.find('.tool-result').text().length).toBeLessThan(longText.length)

    await wrapper.find('.tool-link').trigger('click')
    expect(wrapper.find('.tool-result').text()).toBe(longText)
  })

  it('后端截断的结果标注原始长度', async () => {
    const wrapper = mount(ToolCallCard, {
      props: {
        tool: makeTool({
          status: 'success',
          result: { text: '前 4000 字符', truncated: true, totalLength: 6000 },
        }),
      },
    })
    await wrapper.find('.tool-card-head').trigger('click')

    expect(wrapper.text()).toContain('仅保留前')
    expect(wrapper.text()).toContain('6000')
  })

  it('取消与未知状态提示没有结果', async () => {
    const cancelled = mount(ToolCallCard, { props: { tool: makeTool({ status: 'cancelled' }) } })
    await cancelled.find('.tool-card-head').trigger('click')
    expect(cancelled.text()).toContain('执行被取消，没有产生结果')

    const unknown = mount(ToolCallCard, { props: { tool: makeTool({ status: 'unknown' }) } })
    await unknown.find('.tool-card-head').trigger('click')
    expect(unknown.text()).toContain('连接中断，无法确认执行结果')
  })

  it('参数为 null（不是合法 JSON）时给出说明', async () => {
    const wrapper = mount(ToolCallCard, {
      props: { tool: makeTool({ arguments: null }) },
    })
    await wrapper.find('.tool-card-head').trigger('click')

    expect(wrapper.text()).toContain('参数不是合法的 JSON 对象')
  })

  it('工具返回内容按纯文本渲染，不作为 HTML 执行', async () => {
    const malicious = '<img src=x onerror=alert(1)><script>window.pwned=1</script>'
    const wrapper = mount(ToolCallCard, {
      props: {
        tool: makeTool({
          status: 'success',
          arguments: { command: malicious },
          result: { text: malicious, truncated: false, totalLength: malicious.length },
        }),
      },
    })
    await wrapper.find('.tool-card-head').trigger('click')

    expect(wrapper.find('img').exists()).toBe(false)
    expect(wrapper.find('script').exists()).toBe(false)
    expect(wrapper.text()).toContain('<img src=x onerror=alert(1)>')
  })
})
