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
  it('默认收起：只有动作前缀、主参数与状态图标，不显示调用参数与返回结果', () => {
    const wrapper = mount(ToolCallCard, {
      props: {
        tool: makeTool({
          status: 'success',
          durationMs: 1500,
          arguments: { timezone: 'Asia/Shanghai' },
          result: { text: '{"datetime":"10:00"}', truncated: false, totalLength: 19 },
        }),
      },
    })

    expect(wrapper.text()).toContain('查询时间')
    expect(wrapper.find('.tool-pill').text()).toBe('Asia/Shanghai')
    expect(wrapper.text()).toContain('1.5s')
    // 状态用图标 + 可访问名称标注（不只靠颜色）
    expect(wrapper.find('.tool-status-icon').text()).toBe('✓')
    expect(wrapper.find('.tool-status-icon').attributes('aria-label')).toBe('完成')
    // 详细内容默认不可见
    expect(wrapper.find('.tool-card-body').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('调用参数')
    expect(wrapper.text()).not.toContain('datetime')
  })

  it('内置文件工具用扩展名徽标与“已读取：”前缀', () => {
    const wrapper = mount(ToolCallCard, {
      props: {
        tool: makeTool({
          toolName: 'builtin_file',
          displayName: '文件读写',
          status: 'success',
          arguments: { action: 'read', path: 'src/agent.ts' },
        }),
      },
    })

    expect(wrapper.find('.tool-badge').text()).toBe('TS')
    expect(wrapper.text()).toContain('已读取：')
    expect(wrapper.find('.tool-pill').text()).toBe('src/agent.ts')
  })

  it('搜索工具显示“搜索文件”与搜索词，Shell 显示“执行命令”', () => {
    const grep = mount(ToolCallCard, {
      props: {
        tool: makeTool({
          toolName: 'builtin_grep',
          displayName: '内容搜索',
          status: 'success',
          arguments: { pattern: '**/*.md', path: 'src' },
        }),
      },
    })
    expect(grep.text()).toContain('搜索文件')
    expect(grep.find('.tool-pill').text()).toBe('**/*.md')
    expect(grep.find('.tool-badge').text()).toBe('⌕')

    const shell = mount(ToolCallCard, {
      props: {
        tool: makeTool({
          toolName: 'builtin_shell',
          displayName: 'Shell 命令',
          status: 'success',
          arguments: { command: 'npm run build' },
        }),
      },
    })
    expect(shell.text()).toContain('执行命令')
    expect(shell.find('.tool-pill').text()).toBe('npm run build')
  })

  it('MCP 工具用 Server 工具名做主标题', () => {
    const wrapper = mount(ToolCallCard, {
      props: {
        tool: makeTool({
          toolType: 'mcp',
          toolName: 'mcp3_get_weather',
          displayName: 'get_weather',
          serverName: '天气服务',
          arguments: { city: '北京' },
        }),
      },
    })

    expect(wrapper.text()).toContain('get_weather')
    expect(wrapper.find('.tool-badge').text()).toBe('⚙')
    expect(wrapper.find('.tool-pill').text()).toBe('北京')
  })

  it('失败与取消使用不同的图标和可访问名称标注', () => {
    const failed = mount(ToolCallCard, { props: { tool: makeTool({ status: 'error' }) } })
    expect(failed.find('.tool-status-icon').text()).toBe('✕')
    expect(failed.find('.tool-status-icon').attributes('aria-label')).toBe('失败')

    const cancelled = mount(ToolCallCard, { props: { tool: makeTool({ status: 'cancelled' }) } })
    expect(cancelled.find('.tool-status-icon').text()).toBe('○')
    expect(cancelled.find('.tool-status-icon').attributes('aria-label')).toBe('已取消')

    const unknown = mount(ToolCallCard, { props: { tool: makeTool({ status: 'unknown' }) } })
    expect(unknown.find('.tool-status-icon').text()).toBe('?')
    expect(unknown.find('.tool-status-icon').attributes('aria-label')).toBe('状态未知')
  })

  it('没有字符串参数时用结果摘要兜底；两者都没有则不显示 pill', () => {
    const withSummary = mount(ToolCallCard, {
      props: {
        tool: makeTool({ arguments: { count: 3 }, summary: '12 个条目' }),
      },
    })
    expect(withSummary.find('.tool-pill').text()).toBe('12 个条目')

    const bare = mount(ToolCallCard, { props: { tool: makeTool() } })
    expect(bare.find('.tool-pill').exists()).toBe(false)
  })
})

describe('ToolCallCard 展开态', () => {
  it('点击才展开调用参数与返回结果，再点击收起', async () => {
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
    expect(wrapper.text()).toContain('调用参数')
    expect(wrapper.find('.tool-code').text()).toBe(
      '{\n  "timezone": "Asia/Shanghai",\n  "count": 3\n}',
    )
    expect(wrapper.text()).toContain('返回结果')
    expect(wrapper.find('.tool-result').text()).toContain('"datetime": "10:00"')

    await wrapper.find('.tool-card-head').trigger('click')
    expect(wrapper.find('.tool-card-body').exists()).toBe(false)
  })

  it('失败时展开显示可读的失败原因，不出现堆栈', async () => {
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
    await wrapper.find('.tool-card-head').trigger('click')
    expect(wrapper.text()).toContain('失败原因')
    expect(wrapper.text()).toContain('无法识别的时区')
    expect(wrapper.text()).not.toContain('Traceback')
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

  it('展开/收起向父级发出 toggle 事件，便于保持滚动位置', async () => {
    const wrapper = mount(ToolCallCard, { props: { tool: makeTool() } })

    await wrapper.find('.tool-card-head').trigger('click')

    expect(wrapper.emitted('toggle')).toHaveLength(1)
  })
})
