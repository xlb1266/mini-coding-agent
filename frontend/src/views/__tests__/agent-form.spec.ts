import { describe, expect, it } from 'vitest'

import type { AgentBindingRef } from '../../api/agents'
import {
  buildAgentPayload,
  emptyFormValues,
  findMissingBindings,
  validateAgentForm,
  type AgentFormValues,
} from '../agent-form'

function validValues(overrides: Partial<AgentFormValues> = {}): AgentFormValues {
  return {
    ...emptyFormValues(),
    name: '写作助手',
    systemPrompt: '你是一个写作助手',
    modelId: '1',
    maxRounds: '25',
    ...overrides,
  }
}

describe('validateAgentForm', () => {
  it('合法表单无错误', () => {
    expect(validateAgentForm(validValues())).toEqual({})
  })

  it('名称必填，未选模型时报 modelId', () => {
    const errors = validateAgentForm(validValues({ name: '   ', modelId: '' }))
    expect(errors.name).toBeTruthy()
    expect(errors.modelId).toBeTruthy()
  })

  it.each(['0', '-5', '3.5', 'abc', '', '101'])(
    '最大执行轮数「%s」必须为 1–100 的正整数',
    (value) => {
      expect(validateAgentForm(validValues({ maxRounds: value })).maxRounds).toBeTruthy()
    },
  )

  it('最大执行轮数接受范围内的整数与全角数字', () => {
    expect(validateAgentForm(validValues({ maxRounds: '1' })).maxRounds).toBeUndefined()
    expect(validateAgentForm(validValues({ maxRounds: '100' })).maxRounds).toBeUndefined()
    expect(validateAgentForm(validValues({ maxRounds: '２５' })).maxRounds).toBeUndefined()
  })

  it('说明超长时报 description', () => {
    const errors = validateAgentForm(validValues({ description: 'x'.repeat(513) }))
    expect(errors.description).toBeTruthy()
  })

  it('系统提示词允许为空（可选），但超长时报错', () => {
    expect(validateAgentForm(validValues({ systemPrompt: '' })).systemPrompt).toBeUndefined()
    expect(validateAgentForm(validValues({ systemPrompt: 'x'.repeat(32001) })).systemPrompt).toBeTruthy()
  })

  it.each(['5', '96', '3.5', 'abc', ''])(
    '压缩触发比例「%s」必须为 10–95 的整数（百分比）',
    (value) => {
      expect(validateAgentForm(validValues({ compactThreshold: value })).compactThreshold).toBeTruthy()
    },
  )

  it('压缩触发比例接受范围内的整数与全角数字', () => {
    expect(validateAgentForm(validValues({ compactThreshold: '10' })).compactThreshold).toBeUndefined()
    expect(validateAgentForm(validValues({ compactThreshold: '95' })).compactThreshold).toBeUndefined()
    expect(validateAgentForm(validValues({ compactThreshold: '６０' })).compactThreshold).toBeUndefined()
  })

  it.each(['0', '51', '2.5', ''])('保留最近轮数「%s」必须为 1–50 的正整数', (value) => {
    expect(validateAgentForm(validValues({ compactKeepRounds: value })).compactKeepRounds).toBeTruthy()
  })

  it.each(['50', '99', '32001', 'abc', ''])('摘要目标长度「%s」必须为 100–32000 的整数', (value) => {
    expect(
      validateAgentForm(validValues({ compactSummaryTokens: value })).compactSummaryTokens,
    ).toBeTruthy()
  })

  it('关闭自动压缩时压缩参数仍然校验（保持合法便于重新开启）', () => {
    const errors = validateAgentForm(
      validValues({ autoCompact: false, compactThreshold: '5', compactSummaryTokens: '50' }),
    )
    expect(errors.compactThreshold).toBeTruthy()
    expect(errors.compactSummaryTokens).toBeTruthy()
  })

  it('工作区目录允许留空与合法绝对路径', () => {
    expect(validateAgentForm(validValues({ workspaceDir: '' })).workspaceDir).toBeUndefined()
    expect(validateAgentForm(validValues({ workspaceDir: '  ' })).workspaceDir).toBeUndefined()
    expect(validateAgentForm(validValues({ workspaceDir: 'D:\\projects\\demo' })).workspaceDir).toBeUndefined()
    expect(validateAgentForm(validValues({ workspaceDir: 'D:/projects/demo' })).workspaceDir).toBeUndefined()
    expect(validateAgentForm(validValues({ workspaceDir: '/home/user/project' })).workspaceDir).toBeUndefined()
  })

  it.each(['relative/dir', 'demo', './demo', '..\\demo'])(
    '工作区目录「%s」必须是绝对路径',
    (value) => {
      expect(validateAgentForm(validValues({ workspaceDir: value })).workspaceDir).toBeTruthy()
    },
  )

  it('工作区目录超过 512 字符时报错', () => {
    const errors = validateAgentForm(validValues({ workspaceDir: 'D:\\' + 'x'.repeat(600) }))
    expect(errors.workspaceDir).toBeTruthy()
  })
})

describe('buildAgentPayload', () => {
  it('构造驼峰载荷并转换数字字段', () => {
    const payload = buildAgentPayload(
      validValues({
        description: ' 润色文章 ',
        modelId: '3',
        maxRounds: '１０',
        workspaceDir: ' D:\\projects\\demo ',
        requireApproval: true,
        compactThreshold: '６０',
        compactKeepRounds: '2',
        compactSummaryTokens: '800',
        toolKeys: ['builtin_shell'],
        skillDirNames: ['write-guide'],
        mcpServerIds: ['2', '5'],
      }),
    )

    expect(payload).toEqual({
      name: '写作助手',
      description: '润色文章',
      systemPrompt: '你是一个写作助手',
      modelId: 3,
      maxRounds: 10,
      workspaceDir: 'D:\\projects\\demo',
      requireApproval: true,
      autoCompact: true,
      compactThreshold: 60,
      compactKeepRounds: 2,
      compactSummaryTokens: 800,
      toolKeys: ['builtin_shell'],
      skillDirNames: ['write-guide'],
      mcpServerIds: [2, 5],
    })
  })
})

describe('findMissingBindings', () => {
  const bound: AgentBindingRef[] = [
    { refKey: 'write-guide', name: '写作指南', description: '', enabled: true, available: true },
    { refKey: 'gone-skill', name: null, description: '', enabled: null, available: false },
  ]

  it('过滤出不在可选列表中的绑定', () => {
    const missing = findMissingBindings(bound, new Set(['write-guide']))
    expect(missing).toHaveLength(1)
    expect(missing[0]!.refKey).toBe('gone-skill')
  })

  it('全部存在时返回空数组', () => {
    expect(findMissingBindings(bound, new Set(['write-guide', 'gone-skill']))).toEqual([])
  })
})
