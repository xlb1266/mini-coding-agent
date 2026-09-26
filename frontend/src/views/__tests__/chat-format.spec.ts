import { describe, expect, it } from 'vitest'

import {
  argumentEntries,
  buildDisplayItems,
  formatArgumentValue,
  formatResultText,
  primaryArgument,
  toolAction,
  toolRowText,
} from '../chat-format'
import type { GenerationSegment, ToolActivity } from '../../stores/chat'

describe('chat-format 参数格式化', () => {
  it('字符串保留原文与换行，其余 JSON 化带缩进', () => {
    expect(formatArgumentValue('echo hi\nsecond line')).toBe('echo hi\nsecond line')
    expect(formatArgumentValue(30)).toBe('30')
    expect(formatArgumentValue(true)).toBe('true')
    expect(formatArgumentValue(null)).toBe('（空）')
    expect(formatArgumentValue({ nested: { a: 1 } })).toBe('{\n  "nested": {\n    "a": 1\n  }\n}')
  })

  it('argumentEntries 转成展示行；null 参数返回空数组', () => {
    expect(argumentEntries(null)).toEqual([])
    expect(argumentEntries(undefined)).toEqual([])
    expect(argumentEntries({ command: 'dir', timeout: 10 })).toEqual([
      { key: 'command', value: 'dir' },
      { key: 'timeout', value: '10' },
    ])
  })
})

describe('chat-format 主参数 pill', () => {
  it('优先取路径/命令类键名，而不是第一个字符串参数', () => {
    expect(primaryArgument({ note: '说明', path: 'src/app.py' })).toBe('src/app.py')
    expect(primaryArgument({ file_path: 'a.md', keyword: '测试' })).toBe('a.md')
    expect(primaryArgument({ command: 'dir', timeout: 10 })).toBe('dir')
  })

  it('没有常见键名时取第一个字符串值；没有字符串参数返回 null', () => {
    expect(primaryArgument({ timezone: 'Asia/Shanghai' })).toBe('Asia/Shanghai')
    expect(primaryArgument({ count: 3, flag: true })).toBeNull()
    expect(primaryArgument(null)).toBeNull()
    expect(primaryArgument(undefined)).toBeNull()
  })

  it('超长参数截断加省略号，空白字符串不作为主参数', () => {
    const long = 'x'.repeat(100)
    const picked = primaryArgument({ path: long })
    expect(picked).not.toBeNull()
    expect(picked!.endsWith('…')).toBe(true)
    expect(picked!.length).toBeLessThanOrEqual(72)
    expect(primaryArgument({ path: '   ' })).toBeNull()
  })
})

describe('chat-format 结果格式化', () => {
  it('结构化内容重新缩进，方便查看字段和值', () => {
    expect(formatResultText('{"datetime":"10:00","timezone":"Asia/Shanghai"}')).toBe(
      '{\n  "datetime": "10:00",\n  "timezone": "Asia/Shanghai"\n}',
    )
    expect(formatResultText(' [1, 2, 3] ')).toBe('[\n  1,\n  2,\n  3\n]')
  })

  it('普通文本原样保留（含换行）', () => {
    expect(formatResultText('北京：晴，25℃\n湿度 40%')).toBe('北京：晴，25℃\n湿度 40%')
    expect(formatResultText('"只是一个字符串"')).toBe('"只是一个字符串"')
  })

  it('被截断的 JSON 无法解析时按原文展示', () => {
    const clipped = '{\n  "data": "…（已截断'
    expect(formatResultText(clipped)).toBe(clipped)
  })
})

function makeTool(overrides: Partial<ToolActivity> = {}): ToolActivity {
  return {
    callId: 'call_1',
    toolName: 'builtin_file',
    displayName: '文件读写',
    serverName: null,
    toolType: 'builtin',
    status: 'success',
    ...overrides,
  }
}

describe('chat-format 工具动作与行文本', () => {
  it('内置工具用中文动作前缀，文件类按扩展名给徽标', () => {
    expect(toolAction(makeTool({ arguments: { action: 'read', path: 'src/app.py' } }))).toEqual({
      prefix: '已读取：',
      badgeLabel: 'PY',
      badgeTone: 'accent',
    })
    expect(toolAction(makeTool({ arguments: { action: 'write', path: 'a.json' } }))).toMatchObject({
      prefix: '已写入：',
      badgeLabel: '{}',
    })
    expect(toolAction(makeTool({ toolName: 'builtin_grep', arguments: { pattern: 'TODO' } }))).toMatchObject({
      prefix: '搜索文件',
      badgeLabel: '⌕',
    })
    expect(toolAction(makeTool({ toolName: 'builtin_shell', arguments: { command: 'ls' } }))).toMatchObject({
      prefix: '执行命令',
      badgeLabel: '>_',
    })
  })

  it('没有扩展名或不带路径的文件工具用通用文件徽标', () => {
    expect(toolAction(makeTool({ arguments: { path: 'LICENSE' } })).badgeLabel).toBe('▤')
    expect(toolAction(makeTool({ arguments: null })).badgeLabel).toBe('▤')
  })

  it('MCP 与未知工具用展示名做主标题', () => {
    expect(
      toolAction(makeTool({ toolType: 'mcp', toolName: 'mcp1_x', displayName: 'get_weather' })),
    ).toMatchObject({ prefix: 'get_weather', badgeLabel: '⚙', badgeTone: 'accent' })
    expect(
      toolAction(makeTool({ toolType: 'unknown', toolName: 'mystery', displayName: '' })),
    ).toMatchObject({ prefix: 'mystery', badgeLabel: '·', badgeTone: 'muted' })
  })

  it('行文本按工具挑最相关参数：搜索看模式、命令看命令、文件看路径', () => {
    expect(toolRowText(makeTool({ toolName: 'builtin_grep', arguments: { pattern: '**/*.md', path: 'src' } }))).toBe('**/*.md')
    expect(toolRowText(makeTool({ toolName: 'builtin_shell', arguments: { command: 'npm run build' } }))).toBe('npm run build')
    expect(toolRowText(makeTool({ arguments: { action: 'read', path: 'src/agent.ts' } }))).toBe('src/agent.ts')
  })

  it('没有可用参数时退回结果摘要；都没有则返回 null', () => {
    expect(toolRowText(makeTool({ toolName: 'builtin_list_dir', arguments: {}, summary: '12 个条目' }))).toBe('12 个条目')
    expect(toolRowText(makeTool({ arguments: null, summary: undefined }))).toBeNull()
  })
})

describe('chat-format 时间线分组', () => {
  function toolSegment(callId: string): GenerationSegment {
    return { kind: 'tool', tool: makeTool({ callId }) }
  }

  it('相邻工具合并为“使用N个工具”组，正文与思考打断分组', () => {
    const items = buildDisplayItems([
      { kind: 'reasoning', text: '先看看目录' },
      toolSegment('c1'),
      toolSegment('c2'),
      toolSegment('c3'),
      { kind: 'text', text: '我先看一下目录结构：' },
      toolSegment('c4'),
    ])

    expect(items.map((item) => item.kind)).toEqual(['reasoning', 'toolGroup', 'text', 'toolGroup'])
    const first = items[1]!
    expect(first.kind === 'toolGroup' && first.tools.map((tool) => tool.callId)).toEqual(['c1', 'c2', 'c3'])
    const second = items[3]!
    expect(second.kind === 'toolGroup' && second.tools).toHaveLength(1)
  })

  it('审批与压缩卡片不被并入工具组', () => {
    const items = buildDisplayItems([
      toolSegment('c1'),
      {
        kind: 'approval',
        approval: {
          requestId: 'req-1',
          callId: 'c2',
          toolName: 'builtin_shell',
          displayName: 'Shell 命令',
          toolType: 'builtin',
          status: 'pending',
          arguments: { command: 'rm -rf x' },
        },
      },
      toolSegment('c3'),
    ])

    expect(items.map((item) => item.kind)).toEqual(['toolGroup', 'approval', 'toolGroup'])
  })

  it('每个展示项都有稳定的 key，便于按序渲染', () => {
    const items = buildDisplayItems([toolSegment('c1'), { kind: 'text', text: 'a' }, toolSegment('c2')])
    const keys = items.map((item) => item.key)

    expect(new Set(keys).size).toBe(keys.length)
  })
})
