import { describe, expect, it } from 'vitest'

import { argumentEntries, formatArgumentValue, formatResultText } from '../chat-format'

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
