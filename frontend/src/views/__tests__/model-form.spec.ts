import { describe, expect, it } from 'vitest'

import {
  buildModelPayload,
  emptyFormValues,
  formatDateTime,
  formatTokenCount,
  validateBaseUrl,
  validateModelForm,
  type ModelFormValues,
} from '../model-form'

function validValues(overrides: Partial<ModelFormValues> = {}): ModelFormValues {
  return {
    ...emptyFormValues(),
    displayName: 'DeepSeek-V3',
    modelIdentifier: 'deepseek-chat',
    baseUrl: 'https://api.deepseek.com/v1',
    contextLength: '32768',
    maxOutputTokens: '4096',
    temperature: '0.7',
    ...overrides,
  }
}

describe('validateModelForm', () => {
  it('合法表单无错误', () => {
    expect(validateModelForm(validValues())).toEqual({})
  })

  it('必填项缺失时报对应字段', () => {
    const errors = validateModelForm(
      validValues({ displayName: '  ', modelIdentifier: '', baseUrl: '' }),
    )
    expect(errors.displayName).toBeTruthy()
    expect(errors.modelIdentifier).toBeTruthy()
    expect(errors.baseUrl).toBeTruthy()
  })

  it.each([
    ['api.deepseek.com/v1', '缺少协议'],
    ['ftp://api.deepseek.com/v1', '协议不对'],
    ['https://', '没有主机名'],
  ])('服务地址「%s」不合法（%s）', (url) => {
    expect(validateModelForm(validValues({ baseUrl: url })).baseUrl).toBeTruthy()
  })

  it('服务地址包含 /chat/completions 时给出专门提示', () => {
    const errors = validateModelForm(
      validValues({ baseUrl: 'https://api.deepseek.com/v1/chat/completions' }),
    )
    expect(errors.baseUrl).toContain('/chat/completions')
  })

  it.each(['0', '-100', '32768.5', 'abc', ''])(
    '上下文长度「%s」必须为正整数',
    (value) => {
      expect(validateModelForm(validValues({ contextLength: value })).contextLength).toBeTruthy()
    },
  )

  it('最大输出长度不能超过上下文长度', () => {
    const errors = validateModelForm(validValues({ maxOutputTokens: '32769' }))
    expect(errors.maxOutputTokens).toContain('上下文长度')
  })

  it('最大输出长度等于上下文长度时合法', () => {
    expect(validateModelForm(validValues({ maxOutputTokens: '32768' })).maxOutputTokens).toBeUndefined()
  })

  it.each(['-0.1', '2.1', 'abc', ''])('温度「%s」超出 0–2 或不合法', (value) => {
    expect(validateModelForm(validValues({ temperature: value })).temperature).toBeTruthy()
  })

  it('温度取边界值 0 与 2 合法', () => {
    expect(validateModelForm(validValues({ temperature: '0' })).temperature).toBeUndefined()
    expect(validateModelForm(validValues({ temperature: '2' })).temperature).toBeUndefined()
  })

  it('价格为负数时报错，留空与 0 均合法', () => {
    const errors = validateModelForm(validValues({ inputPrice: '-1', outputPrice: '0' }))
    expect(errors.inputPrice).toBeTruthy()
    expect(errors.outputPrice).toBeUndefined()
    expect(errors.cachedInputPrice).toBeUndefined()
  })

  it('全角数字与千分位逗号可以正常解析', () => {
    const errors = validateModelForm(
      validValues({ contextLength: '１３１０７２', temperature: '０.３', inputPrice: '1,234.5' }),
    )
    expect(errors.contextLength).toBeUndefined()
    expect(errors.temperature).toBeUndefined()
    expect(errors.inputPrice).toBeUndefined()
  })
})

describe('validateBaseUrl', () => {
  it('本地 Ollama 地址合法', () => {
    expect(validateBaseUrl('http://127.0.0.1:11434/v1')).toBeNull()
  })
})

describe('buildModelPayload', () => {
  it('转换数字、去除首尾空白与末尾斜杠', () => {
    const payload = buildModelPayload(
      validValues({ displayName: ' 名称 ', baseUrl: 'https://api.example.com/v1/' }),
    )
    expect(payload.displayName).toBe('名称')
    expect(payload.baseUrl).toBe('https://api.example.com/v1')
    expect(payload.contextLength).toBe(32768)
    expect(payload.maxOutputTokens).toBe(4096)
    expect(payload.temperature).toBe(0.7)
  })

  it('留空价格映射为 null，填 0 保留为 0，密钥留空映射为 undefined', () => {
    const payload = buildModelPayload(
      validValues({ inputPrice: '0', outputPrice: '', cachedInputPrice: '', apiKey: '  ' }),
    )
    expect(payload.inputPrice).toBe(0)
    expect(payload.outputPrice).toBeNull()
    expect(payload.cachedInputPrice).toBeNull()
    expect(payload.apiKey).toBeUndefined()
  })

  it('填写密钥时去除空白后提交', () => {
    const payload = buildModelPayload(validValues({ apiKey: ' sk-1 ' }))
    expect(payload.apiKey).toBe('sk-1')
  })

  it('全角数字与千分位在载荷中转为规范数字', () => {
    const payload = buildModelPayload(
      validValues({ contextLength: '１３１０７２', temperature: '０.３', inputPrice: '1,234.5' }),
    )
    expect(payload.contextLength).toBe(131072)
    expect(payload.temperature).toBeCloseTo(0.3)
    expect(payload.inputPrice).toBe(1234.5)
  })
})

describe('展示格式化', () => {
  it('上下文长度带千分位', () => {
    expect(formatTokenCount(32768)).toBe('32,768')
  })

  it('ISO 时间转为本地 YYYY-MM-DD HH:mm', () => {
    expect(formatDateTime('2026-09-11T06:30:00Z')).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/)
  })

  it('非法时间原样返回', () => {
    expect(formatDateTime('not-a-date')).toBe('not-a-date')
  })
})
