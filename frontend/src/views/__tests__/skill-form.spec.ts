import { describe, expect, it } from 'vitest'

import { buildSkillPayload, emptySkillFormValues, validateSkillForm } from '../skill-form'

const validValues = {
  name: '网页总结',
  description: '总结网页文章的要点。',
  instructions: '# 步骤\n\n1. 阅读正文',
}

describe('validateSkillForm 校验', () => {
  it('合法表单返回空错误对象', () => {
    expect(validateSkillForm(validValues)).toEqual({})
  })

  it('必填项为空时逐项报错', () => {
    const errors = validateSkillForm(emptySkillFormValues())

    expect(errors.name).toBe('请填写名称')
    expect(errors.description).toBe('请填写用途说明')
    expect(errors.instructions).toBe('请填写详细指令')
  })

  it('纯空白内容视为为空', () => {
    const errors = validateSkillForm({ name: '  ', description: '\t', instructions: '\n' })

    expect(Object.keys(errors)).toEqual(['name', 'description', 'instructions'])
  })

  it('超长字段报错', () => {
    const errors = validateSkillForm({
      name: '名'.repeat(65),
      description: '说'.repeat(201),
      instructions: '指'.repeat(200_001),
    })

    expect(errors.name).toContain('64')
    expect(errors.description).toContain('200')
    expect(errors.instructions).toContain('200000')
  })
})

describe('buildSkillPayload 构造载荷', () => {
  it('去掉首尾空白后提交', () => {
    const payload = buildSkillPayload({
      name: '  网页总结  ',
      description: ' 总结要点。 ',
      instructions: ' # 步骤 \n\n 正文 ',
    })

    expect(payload).toEqual({
      name: '网页总结',
      description: '总结要点。',
      instructions: '# 步骤 \n\n 正文',
    })
  })
})
