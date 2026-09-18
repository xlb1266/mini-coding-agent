import { describe, expect, it } from 'vitest'

import type { ToolParam } from '../../api/tools'
import {
  buildInitialState,
  buildRunParams,
  formatResult,
  isLongText,
  validateRunState,
} from '../tool-run'

const params: ToolParam[] = [
  { name: 'command', type: 'string', required: true, description: '要执行的命令', example: 'git status' },
  { name: 'timeout', type: 'integer', required: false, description: '超时秒数' },
  { name: 'flag', type: 'boolean', required: false, description: '开关' },
]

describe('buildInitialState', () => {
  it('文本与整数参数初值为空串，布尔参数为 false', () => {
    expect(buildInitialState(params)).toEqual({ text: { command: '', timeout: '' }, bool: { flag: false } })
  })
})

describe('validateRunState', () => {
  it('必填参数缺失时报错，可选参数留空不报错', () => {
    const errors = validateRunState(params, { text: { command: '', timeout: '' }, bool: {} })

    expect(Object.keys(errors)).toEqual(['command'])
  })

  it('整数参数填写非整数时报错', () => {
    const errors = validateRunState(params, { text: { command: 'ls', timeout: '2.5' }, bool: {} })

    expect(errors.timeout).toContain('整数')
  })

  it('整数参数填写合法整数与负数时不报错', () => {
    const errors = validateRunState(params, { text: { command: 'ls', timeout: '-30' }, bool: {} })

    expect(errors).toEqual({})
  })
})

describe('buildRunParams', () => {
  it('留空的可选参数不发送，整数参数转换为 number', () => {
    const payload = buildRunParams(params, { text: { command: ' ls ', timeout: '' }, bool: { flag: false } })

    expect(payload).toEqual({ command: 'ls' })
  })

  it('布尔参数仅在勾选时发送', () => {
    const off = buildRunParams(params, { text: { command: 'ls', timeout: '5' }, bool: { flag: false } })
    const on = buildRunParams(params, { text: { command: 'ls', timeout: '5' }, bool: { flag: true } })

    expect(off).toEqual({ command: 'ls', timeout: 5 })
    expect(on).toEqual({ command: 'ls', timeout: 5, flag: true })
  })
})

describe('isLongText', () => {
  it('content 与 command 参数使用多行输入框', () => {
    const contentParam: ToolParam = { name: 'content', type: 'string', required: false, description: '内容' }

    expect(isLongText(contentParam)).toBe(true)
    expect(isLongText(params[0]!)).toBe(true)
    expect(isLongText(params[1]!)).toBe(false)
  })
})

describe('formatResult', () => {
  it('字符串值原样保留换行，其他值 JSON 化', () => {
    const rows = formatResult({ stdout: '第一行\n第二行', exitCode: 0, nested: { a: 1 } })

    expect(rows).toEqual([
      { label: 'stdout', value: '第一行\n第二行' },
      { label: 'exitCode', value: '0' },
      { label: 'nested', value: '{\n  "a": 1\n}' },
    ])
  })
})
