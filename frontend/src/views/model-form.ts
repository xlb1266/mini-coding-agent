/** 模型表单的纯函数校验与载荷构造，独立成模块便于单测。 */

import type { ModelPayload } from '../api/models'

/** 表单原始值：数字输入框的内容一律先按字符串保存，校验时再转换。 */
export interface ModelFormValues {
  displayName: string
  modelIdentifier: string
  baseUrl: string
  apiKey: string
  contextLength: string
  maxOutputTokens: string
  temperature: string
  inputPrice: string
  outputPrice: string
  cachedInputPrice: string
}

export type ModelFormErrors = Partial<Record<keyof ModelFormValues, string>>

/** 新增表单的建议值；仅作占位参考，实际参数以服务商说明为准。 */
export const SUGGESTED_VALUES = {
  contextLength: '32768',
  maxOutputTokens: '4096',
  temperature: '0.7',
} as const

export function emptyFormValues(): ModelFormValues {
  return {
    displayName: '',
    modelIdentifier: '',
    baseUrl: '',
    apiKey: '',
    contextLength: SUGGESTED_VALUES.contextLength,
    maxOutputTokens: SUGGESTED_VALUES.maxOutputTokens,
    temperature: SUGGESTED_VALUES.temperature,
    inputPrice: '',
    outputPrice: '',
    cachedInputPrice: '',
  }
}

/** 归一化常见输入形态：全角数字/点转半角，去掉千分位逗号与空格。 */
function normalizeNumeric(raw: string): string {
  return raw
    .replace(/[０-９]/g, (ch) => String.fromCharCode(ch.charCodeAt(0) - 0xfee0))
    .replace(/[．]/g, '.')
    .replace(/[,\s]/g, '')
}

function toNumber(raw: string): number | null {
  const text = normalizeNumeric(raw.trim())
  if (!text) return null
  const value = Number(text)
  return Number.isFinite(value) ? value : null
}

function toPositiveInt(raw: string): number | null {
  const value = toNumber(raw)
  return value !== null && Number.isInteger(value) && value > 0 ? value : null
}

const PRICE_FIELDS = [
  ['inputPrice', '输入单价'],
  ['outputPrice', '输出单价'],
  ['cachedInputPrice', '缓存命中输入单价'],
] as const

/** 校验全部表单项，返回 {字段名: 错误原因}；空对象表示通过。 */
export function validateModelForm(values: ModelFormValues): ModelFormErrors {
  const errors: ModelFormErrors = {}

  if (!values.displayName.trim()) {
    errors.displayName = '请填写显示名称'
  }
  if (!values.modelIdentifier.trim()) {
    errors.modelIdentifier = '请填写模型标识'
  }
  const baseUrlError = validateBaseUrl(values.baseUrl)
  if (baseUrlError) {
    errors.baseUrl = baseUrlError
  }

  const contextLength = toPositiveInt(values.contextLength)
  if (contextLength === null) {
    errors.contextLength = '上下文长度必须为正整数'
  }
  const maxOutputTokens = toPositiveInt(values.maxOutputTokens)
  if (maxOutputTokens === null) {
    errors.maxOutputTokens = '最大输出长度必须为正整数'
  } else if (contextLength !== null && maxOutputTokens > contextLength) {
    errors.maxOutputTokens = '最大输出长度不能超过上下文长度'
  }

  const temperature = toNumber(values.temperature)
  if (temperature === null || temperature < 0 || temperature > 2) {
    errors.temperature = '温度范围为 0–2，请填写该区间内的数字'
  }

  for (const [field, label] of PRICE_FIELDS) {
    const raw = values[field].trim()
    if (!raw) continue // 留空表示未填写，合法
    const price = toNumber(raw)
    if (price === null || price < 0) {
      errors[field] = `${label}不能为负数`
    }
  }

  return errors
}

/** 服务地址必须为 http(s) 完整地址，且不要包含 /chat/completions 路径。 */
export function validateBaseUrl(raw: string): string | null {
  const text = raw.trim()
  if (!text) return '请填写服务地址'

  let parsed: URL
  try {
    parsed = new URL(text)
  } catch {
    return '请填写以 http:// 或 https:// 开头的完整服务地址'
  }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
    return '请填写以 http:// 或 https:// 开头的完整服务地址'
  }
  if (!parsed.hostname) {
    return '请填写包含主机名的完整服务地址'
  }
  if (parsed.pathname.replace(/\/+$/, '').toLowerCase().endsWith('/chat/completions')) {
    return '服务地址不需要包含 /chat/completions，请填写到 /v1 为止'
  }
  return null
}

/** 构造提交载荷；调用前应先通过 validateModelForm。 */
export function buildModelPayload(values: ModelFormValues): ModelPayload {
  const price = (raw: string): number | null => toNumber(raw)

  return {
    displayName: values.displayName.trim(),
    modelIdentifier: values.modelIdentifier.trim(),
    baseUrl: values.baseUrl.trim().replace(/\/+$/, ''),
    apiKey: values.apiKey.trim() ? values.apiKey.trim() : undefined,
    contextLength: toPositiveInt(values.contextLength) as number,
    maxOutputTokens: toPositiveInt(values.maxOutputTokens) as number,
    temperature: toNumber(values.temperature) as number,
    inputPrice: price(values.inputPrice),
    outputPrice: price(values.outputPrice),
    cachedInputPrice: price(values.cachedInputPrice),
  }
}

/** 列表展示：上下文长度千分位。 */
export function formatTokenCount(value: number): string {
  return value.toLocaleString('en-US')
}

/** 列表展示：ISO 时间转本地 "YYYY-MM-DD HH:mm"。 */
export function formatDateTime(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  const pad = (n: number) => String(n).padStart(2, '0')
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}`
  )
}
