/** Agent 表单的纯函数校验与载荷构造，独立成模块便于单测。 */

import type { AgentBindingRef, AgentPayload } from '../api/agents'

/** 与后端 app/schemas/agent.py 保持一致的限制。 */
export const NAME_MAX_LENGTH = 64
export const DESCRIPTION_MAX_LENGTH = 512
export const SYSTEM_PROMPT_MAX_LENGTH = 32000

/** 最大执行轮数范围与默认值；执行限制在 Agent Loop 阶段生效。 */
export const MAX_ROUNDS_MIN = 1
export const MAX_ROUNDS_MAX = 100
export const DEFAULT_MAX_ROUNDS = 25

/** 上下文压缩配置范围与默认值（与后端 app/schemas/agent.py 一致）。 */
export const COMPACT_THRESHOLD_MIN = 10
export const COMPACT_THRESHOLD_MAX = 95
export const DEFAULT_COMPACT_THRESHOLD = 80
export const COMPACT_KEEP_ROUNDS_MIN = 1
export const COMPACT_KEEP_ROUNDS_MAX = 50
export const DEFAULT_COMPACT_KEEP_ROUNDS = 4
export const COMPACT_SUMMARY_TOKENS_MIN = 100
export const COMPACT_SUMMARY_TOKENS_MAX = 32000
export const DEFAULT_COMPACT_SUMMARY_TOKENS = 1000

/** 表单原始值：数字输入框与模型下拉的内容先按字符串保存，校验时再转换。
 *  mcpServerIds 以字符串保存便于复选框绑定，构造载荷时再转数字。 */
export interface AgentFormValues {
  name: string
  description: string
  systemPrompt: string
  modelId: string
  maxRounds: string
  autoCompact: boolean
  compactThreshold: string
  compactKeepRounds: string
  compactSummaryTokens: string
  toolKeys: string[]
  skillDirNames: string[]
  mcpServerIds: string[]
}

export type AgentFormErrors = Partial<Record<keyof AgentFormValues, string>>

export function emptyFormValues(): AgentFormValues {
  return {
    name: '',
    description: '',
    systemPrompt: '',
    modelId: '',
    maxRounds: String(DEFAULT_MAX_ROUNDS),
    autoCompact: true,
    compactThreshold: String(DEFAULT_COMPACT_THRESHOLD),
    compactKeepRounds: String(DEFAULT_COMPACT_KEEP_ROUNDS),
    compactSummaryTokens: String(DEFAULT_COMPACT_SUMMARY_TOKENS),
    toolKeys: [],
    skillDirNames: [],
    mcpServerIds: [],
  }
}

/** 归一化常见输入形态：全角数字转半角，去掉千分位逗号与空格。 */
function normalizeNumeric(raw: string): string {
  return raw
    .replace(/[０-９]/g, (ch) => String.fromCharCode(ch.charCodeAt(0) - 0xfee0))
    .replace(/[,\s]/g, '')
}

/** 校验全部表单项，返回 {字段名: 错误原因}；空对象表示通过。 */
export function validateAgentForm(values: AgentFormValues): AgentFormErrors {
  const errors: AgentFormErrors = {}

  const name = values.name.trim()
  if (!name) {
    errors.name = '请填写名称'
  } else if (name.length > NAME_MAX_LENGTH) {
    errors.name = `名称不能超过 ${NAME_MAX_LENGTH} 个字符`
  }

  if (values.description.trim().length > DESCRIPTION_MAX_LENGTH) {
    errors.description = `说明不能超过 ${DESCRIPTION_MAX_LENGTH} 个字符`
  }

  if (values.systemPrompt.length > SYSTEM_PROMPT_MAX_LENGTH) {
    errors.systemPrompt = `系统提示词不能超过 ${SYSTEM_PROMPT_MAX_LENGTH} 个字符`
  }

  if (!values.modelId) {
    errors.modelId = '请选择一个已配置的模型'
  }

  const rounds = Number(normalizeNumeric(values.maxRounds.trim()))
  if (
    values.maxRounds.trim() === '' ||
    !Number.isInteger(rounds) ||
    rounds < MAX_ROUNDS_MIN ||
    rounds > MAX_ROUNDS_MAX
  ) {
    errors.maxRounds = `最大执行轮数必须为 ${MAX_ROUNDS_MIN}–${MAX_ROUNDS_MAX} 的正整数`
  }

  // 上下文压缩配置：即使关闭自动压缩也保持合法值，便于随时重新开启
  const threshold = Number(normalizeNumeric(values.compactThreshold.trim()))
  if (
    values.compactThreshold.trim() === '' ||
    !Number.isInteger(threshold) ||
    threshold < COMPACT_THRESHOLD_MIN ||
    threshold > COMPACT_THRESHOLD_MAX
  ) {
    errors.compactThreshold = `触发比例必须为 ${COMPACT_THRESHOLD_MIN}–${COMPACT_THRESHOLD_MAX} 的整数（百分比）`
  }

  const keepRounds = Number(normalizeNumeric(values.compactKeepRounds.trim()))
  if (
    values.compactKeepRounds.trim() === '' ||
    !Number.isInteger(keepRounds) ||
    keepRounds < COMPACT_KEEP_ROUNDS_MIN ||
    keepRounds > COMPACT_KEEP_ROUNDS_MAX
  ) {
    errors.compactKeepRounds = `保留轮数必须为 ${COMPACT_KEEP_ROUNDS_MIN}–${COMPACT_KEEP_ROUNDS_MAX} 的正整数`
  }

  const summaryTokens = Number(normalizeNumeric(values.compactSummaryTokens.trim()))
  if (
    values.compactSummaryTokens.trim() === '' ||
    !Number.isInteger(summaryTokens) ||
    summaryTokens < COMPACT_SUMMARY_TOKENS_MIN ||
    summaryTokens > COMPACT_SUMMARY_TOKENS_MAX
  ) {
    errors.compactSummaryTokens = `摘要目标长度必须为 ${COMPACT_SUMMARY_TOKENS_MIN}–${COMPACT_SUMMARY_TOKENS_MAX} 的整数（Token 估算值）`
  }

  return errors
}

/** 构造提交载荷；调用前先用 validateAgentForm 确认无错误。 */
export function buildAgentPayload(values: AgentFormValues): AgentPayload {
  return {
    name: values.name.trim(),
    description: values.description.trim(),
    systemPrompt: values.systemPrompt,
    modelId: Number(values.modelId),
    maxRounds: Number(normalizeNumeric(values.maxRounds.trim())),
    autoCompact: values.autoCompact,
    compactThreshold: Number(normalizeNumeric(values.compactThreshold.trim())),
    compactKeepRounds: Number(normalizeNumeric(values.compactKeepRounds.trim())),
    compactSummaryTokens: Number(normalizeNumeric(values.compactSummaryTokens.trim())),
    toolKeys: [...values.toolKeys],
    skillDirNames: [...values.skillDirNames],
    mcpServerIds: values.mcpServerIds.map(Number),
  }
}

/**
 * 找出资源已不存在的绑定（refKey 不在可选列表里，例如 Skill 目录被手动
 * 删除后扫描清理）：编辑页要展示为不可用并允许手动移除。
 */
export function findMissingBindings(
  bindings: AgentBindingRef[],
  optionKeys: Set<string>,
): AgentBindingRef[] {
  return bindings.filter((binding) => !optionKeys.has(binding.refKey))
}
