/** 工具试运行表单的纯逻辑：初值构建、校验与结果格式化，便于单测。 */

import type { ToolParam } from '../api/tools'

/** 试运行表单状态：文本/整数参数以字符串编辑，布尔参数单独记录勾选。 */
export interface ToolRunState {
  text: Record<string, string>
  bool: Record<string, boolean>
}

export interface ToolRunErrors {
  [param: string]: string
}

export const TYPE_LABELS: Record<string, string> = {
  string: '字符串',
  integer: '整数',
  boolean: '布尔值',
}

/** 统一执行结果的 error.type → 中文标题。 */
export const RESULT_LABELS: Record<string, string> = {
  disabled: '工具已停用',
  invalid_params: '参数不合法',
  blocked: '被安全拦截',
  timeout: '执行超时',
  failed: '执行失败',
}

/** 长文本参数用多行输入框（命令、文件内容）。 */
export function isLongText(param: ToolParam): boolean {
  return param.type === 'string' && (param.name === 'content' || param.name === 'command')
}

export function buildInitialState(params: ToolParam[]): ToolRunState {
  const state: ToolRunState = { text: {}, bool: {} }
  for (const param of params) {
    if (param.type === 'boolean') state.bool[param.name] = false
    else state.text[param.name] = ''
  }
  return state
}

export function validateRunState(params: ToolParam[], state: ToolRunState): ToolRunErrors {
  const errors: ToolRunErrors = {}
  for (const param of params) {
    if (param.type === 'boolean') continue
    const text = (state.text[param.name] ?? '').trim()
    if (param.required && text === '') {
      errors[param.name] = `请填写参数 ${param.name}`
      continue
    }
    if (text !== '' && param.type === 'integer' && !/^-?\d+$/.test(text)) {
      errors[param.name] = `参数 ${param.name} 需要是整数`
    }
  }
  return errors
}

/** 空的可选参数不发送；整数参数转换为 number，最终仍以后端校验为准。 */
export function buildRunParams(params: ToolParam[], state: ToolRunState): Record<string, unknown> {
  const payload: Record<string, unknown> = {}
  for (const param of params) {
    if (param.type === 'boolean') {
      if (state.bool[param.name] === true) payload[param.name] = true
      continue
    }
    const text = (state.text[param.name] ?? '').trim()
    if (text === '') continue
    payload[param.name] = param.type === 'integer' ? Number(text) : text
  }
  return payload
}

/** 把执行结果对象转成行展示；非字符串值 JSON 化，字符串保留换行。 */
export function formatResult(data: Record<string, unknown>): { label: string; value: string }[] {
  return Object.entries(data).map(([label, value]) => ({
    label,
    value: typeof value === 'string' ? value : JSON.stringify(value, null, 2),
  }))
}
