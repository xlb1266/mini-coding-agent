/** Skill 编辑表单的纯函数校验，独立成模块便于单测。 */

import type { SkillPayload } from '../api/skills'

export interface SkillFormValues {
  name: string
  description: string
  instructions: string
}

export type SkillFormErrors = Partial<Record<keyof SkillFormValues, string>>

/** 与后端限制保持一致。 */
export const SKILL_LIMITS = {
  name: 64,
  description: 200,
  instructions: 200_000,
} as const

export function emptySkillFormValues(): SkillFormValues {
  return { name: '', description: '', instructions: '' }
}

/** 校验全部表单项，返回 {字段名: 错误原因}；空对象表示通过。 */
export function validateSkillForm(values: SkillFormValues): SkillFormErrors {
  const errors: SkillFormErrors = {}
  const name = values.name.trim()
  const description = values.description.trim()
  const instructions = values.instructions.trim()

  if (!name) {
    errors.name = '请填写名称'
  } else if (name.length > SKILL_LIMITS.name) {
    errors.name = `名称不能超过 ${SKILL_LIMITS.name} 个字符`
  }

  if (!description) {
    errors.description = '请填写用途说明'
  } else if (description.length > SKILL_LIMITS.description) {
    errors.description = `用途说明不能超过 ${SKILL_LIMITS.description} 个字符`
  }

  if (!instructions) {
    errors.instructions = '请填写详细指令'
  } else if (instructions.length > SKILL_LIMITS.instructions) {
    errors.instructions = `详细指令不能超过 ${SKILL_LIMITS.instructions} 个字符`
  }

  return errors
}

/** 构造提交载荷；调用前应先通过 validateSkillForm。 */
export function buildSkillPayload(values: SkillFormValues): SkillPayload {
  return {
    name: values.name.trim(),
    description: values.description.trim(),
    instructions: values.instructions.trim(),
  }
}
