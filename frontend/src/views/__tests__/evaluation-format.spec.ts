import { describe, expect, it } from 'vitest'

import {
  caseStatusMeta,
  formatAverage,
  formatScore,
  formatScoringDuration,
  progressText,
  taskStatusMeta,
  validateCaseForm,
} from '../evaluation-format'

describe('evaluation-format', () => {
  it('任务状态元数据覆盖四种状态', () => {
    expect(taskStatusMeta('pending').label).toBe('待运行')
    expect(taskStatusMeta('running').label).toBe('运行中')
    expect(taskStatusMeta('completed').label).toBe('已完成')
    expect(taskStatusMeta('interrupted').label).toBe('已中断')
    // 未知值不抛错，回退展示原始状态
    expect(taskStatusMeta('unknown-x').label).toBe('unknown-x')
  })

  it('用例状态元数据覆盖七种状态', () => {
    expect(caseStatusMeta('pending').label).toBe('待执行')
    expect(caseStatusMeta('executing').label).toBe('执行中')
    expect(caseStatusMeta('scoring').label).toBe('评分中')
    expect(caseStatusMeta('passed').label).toBe('通过')
    expect(caseStatusMeta('failed').label).toBe('未通过')
    expect(caseStatusMeta('exec_failed').label).toBe('执行失败')
    expect(caseStatusMeta('scoring_failed').label).toBe('评分失败')
  })

  it('分数与平均分格式化', () => {
    expect(formatScore(null)).toBe('—')
    expect(formatScore(85)).toBe('85')
    expect(formatScore(59.5)).toBe('59.5')
    // 平均分：无有效评分显示"暂无评分"，不显示 0
    expect(formatAverage(null)).toBe('暂无评分')
    expect(formatAverage(undefined)).toBe('暂无评分')
    expect(formatAverage(72.25)).toBe('72.3')
  })

  it('评分耗时与进度文本', () => {
    expect(formatScoringDuration(null)).toBe('—')
    expect(formatScoringDuration(820)).toBe('820ms')
    expect(formatScoringDuration(2100)).toBe('2.1s')
    expect(progressText(3, 5)).toBe('3 / 5')
  })

  it('用例表单校验：问题必填，期望答案与评分要求至少一项', () => {
    expect(validateCaseForm({ question: '问题', expectedAnswer: '', scoringRequirements: '要求' })).toEqual({})
    expect(validateCaseForm({ question: '问题', expectedAnswer: '期望', scoringRequirements: '' })).toEqual({})

    const missingQuestion = validateCaseForm({ question: '  ', expectedAnswer: '期望', scoringRequirements: '' })
    expect(missingQuestion.question).toBe('请填写用户问题')

    const missingBoth = validateCaseForm({ question: '问题', expectedAnswer: ' ', scoringRequirements: '' })
    expect(missingBoth.expectedAnswer).toContain('至少填写一项')

    const bothMissingFields = validateCaseForm({ question: '', expectedAnswer: '', scoringRequirements: '' })
    expect(Object.keys(bothMissingFields).sort()).toEqual(['expectedAnswer', 'question'])
  })
})
