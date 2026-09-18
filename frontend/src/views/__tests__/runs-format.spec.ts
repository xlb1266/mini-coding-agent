import { describe, expect, it } from 'vitest'

import type { RunEvent } from '../../api/runs'
import {
  RUN_STATUS_META,
  buildTimeline,
  finishReasonLabel,
  formatDuration,
  formatFirstOutput,
  formatRunTokens,
  formatUsage,
} from '../runs-format'

function makeEvent(eventType: string, seq: number, payload: Record<string, unknown>, overrides: Partial<RunEvent> = {}): RunEvent {
  return {
    id: seq,
    seq,
    eventType,
    payload: { type: eventType, seq, ...payload },
    hasDetail: false,
    ...overrides,
  }
}

describe('runs-format 时间线构建', () => {
  it('按事件顺序组装步骤：开始、模型（配对）、工具（配对）、压缩（配对）、结束', () => {
    const events: RunEvent[] = [
      makeEvent('run_start', 1, { agentName: '测试助手' }),
      makeEvent('model_request_start', 2, { round: 1, callId: 'mr-1' }),
      makeEvent('tool_call_start', 3, {
        round: 1,
        callId: 'call_1',
        toolName: 'builtin_current_time',
        toolType: 'builtin',
        displayName: '当前时间',
        serverName: null,
        arguments: { timezone: 'Asia/Shanghai' },
      }),
      makeEvent('tool_call_end', 4, {
        round: 1,
        callId: 'call_1',
        toolName: 'builtin_current_time',
        toolType: 'builtin',
        status: 'success',
        durationMs: 12,
        summary: '2026-09-14（Asia/Shanghai）',
        result: null,
      }, { hasDetail: true }),
      makeEvent('model_request_end', 5, { round: 1, callId: 'mr-1', status: 'success', durationMs: 800, usage: null }),
      makeEvent('model_request_start', 6, { round: 2, callId: 'mr-2' }),
      makeEvent('model_request_end', 7, { round: 2, callId: 'mr-2', status: 'success', durationMs: 300, usage: { promptTokens: 10, completionTokens: 5, totalTokens: 15, cachedTokens: null } }),
      makeEvent('run_end', 8, { status: 'completed', reason: 'model_finished', roundsUsed: 2, contentPreview: '回答完成', error: null }),
    ]

    const steps = buildTimeline(events)
    expect(steps.map((step) => step.kind)).toEqual([
      'run-start',
      'model',
      'tool',
      'model',
      'run-end',
    ])

    // 模型步骤：callId 配对起止事件，状态与用量来自结束事件
    const firstModel = steps[1]
    expect(firstModel).toMatchObject({ kind: 'model', callId: 'mr-1', status: 'success', durationMs: 800 })
    const secondModel = steps[3]
    expect(secondModel).toMatchObject({ kind: 'model', callId: 'mr-2', status: 'success' })
    if (secondModel.kind === 'model') {
      expect(secondModel.usage).toMatchObject({ promptTokens: 10, completionTokens: 5 })
    }

    // 工具步骤：展示字段与聊天卡片同源；详细载荷标记来自结束事件
    const toolStep = steps[2]
    expect(toolStep).toMatchObject({ kind: 'tool', callId: 'call_1', hasDetail: true })
    if (toolStep.kind === 'tool') {
      expect(toolStep.tool).toMatchObject({
        displayName: '当前时间',
        toolType: 'builtin',
        status: 'success',
        durationMs: 12,
        summary: '2026-09-14（Asia/Shanghai）',
        arguments: { timezone: 'Asia/Shanghai' },
      })
      expect(toolStep.tool.result).toBeUndefined() // 详细载荷按需加载
    }

    // 结束步骤（状态为 Runtime 终态，与单步状态分开映射）
    const runEnd = steps[4]
    expect(runEnd).toMatchObject({
      kind: 'run-end',
      status: 'completed',
      reason: 'model_finished',
      contentPreview: '回答完成',
    })
  })

  it('未配对到结束事件的步骤保持执行中（运行中或事件缺失）', () => {
    const steps = buildTimeline([
      makeEvent('run_start', 1, { agentName: 'A' }),
      makeEvent('model_request_start', 2, { callId: 'mr-1' }),
      makeEvent('tool_call_start', 3, { callId: 'call_1', toolName: 't', toolType: 'builtin', displayName: '工具' }),
    ])
    const model = steps[1]
    const tool = steps[2]
    expect(model).toMatchObject({ kind: 'model', status: 'running' })
    expect(tool).toMatchObject({ kind: 'tool' })
    if (tool.kind === 'tool') expect(tool.tool.status).toBe('running')
  })

  it('压缩起止按 compactionId 配对；备用裁剪直接追加', () => {
    const steps = buildTimeline([
      makeEvent('context_compaction_start', 1, { compactionId: 'cmp-1', reason: '估算输入过大', estimatedTokens: 900 }),
      makeEvent('context_trim', 2, { reason: '容量不足', estimatedTokensBefore: 900, estimatedTokensAfter: 500, droppedMessages: 4, keptMessages: 6 }),
      makeEvent('context_compaction_end', 3, { compactionId: 'cmp-1', status: 'success', estimatedTokensAfter: 500, processedMessages: 4, keptMessages: 10, batches: 1, modelCalls: 1, durationMs: 900 }),
    ])
    expect(steps).toHaveLength(2)
    const compact = steps[0]
    if (compact.kind === 'compact') {
      expect(compact.compact).toMatchObject({ compactionId: 'cmp-1', status: 'success', processedMessages: 4 })
    } else throw new Error('应为压缩步骤')
    const trim = steps[1]
    if (trim.kind === 'compact') {
      expect(trim.compact).toMatchObject({ compactionId: null, status: 'trimmed', droppedMessages: 4 })
    } else throw new Error('应为裁剪步骤')
  })

  it('摘要生成请求带 purpose 标记（不占用执行轮数）', () => {
    const steps = buildTimeline([
      makeEvent('model_request_start', 1, { callId: 'cmp-1-1', purpose: 'context_compaction' }),
      makeEvent('model_request_end', 2, { callId: 'cmp-1-1', purpose: 'context_compaction', status: 'success', durationMs: 500 }),
    ])
    expect(steps[0]).toMatchObject({ kind: 'model', purpose: 'context_compaction', status: 'success' })
  })
})

describe('runs-format 指标格式化', () => {
  it('formatDuration：毫秒 / 秒 / 分', () => {
    expect(formatDuration(45)).toBe('45ms')
    expect(formatDuration(1250)).toBe('1.3s')
    expect(formatDuration(95_000)).toBe('1分35秒')
    expect(formatDuration(null)).toBe('—')
    expect(formatDuration(undefined)).toBe('—')
  })

  it('formatRunTokens：未知 / 正常 / 缓存命中 / 统计不完整，不显示 0', () => {
    expect(formatRunTokens({ tokenUsage: null, tokensIncomplete: false })).toBe('未知')
    expect(
      formatRunTokens({
        tokenUsage: { promptTokens: 150, completionTokens: 50, totalTokens: 200, cachedTokens: 68 },
        tokensIncomplete: false,
      }),
    ).toBe('输入 150 / 输出 50 / 缓存命中 68 token')
    expect(
      formatRunTokens({
        tokenUsage: { promptTokens: 150, completionTokens: 50, totalTokens: 200, cachedTokens: null },
        tokensIncomplete: true,
      }),
    ).toBe('输入 150 / 输出 50 token（统计不完整）')
    expect(
      formatRunTokens({
        tokenUsage: { promptTokens: null, completionTokens: null, totalTokens: null, cachedTokens: null },
        tokensIncomplete: false,
      }),
    ).toBe('未知')
  })

  it('formatFirstOutput：无正文输出时明确标注', () => {
    expect(formatFirstOutput(320)).toBe('320ms')
    expect(formatFirstOutput(null)).toBe('无正文输出')
  })

  it('formatUsage：单次模型请求用量；未知为空串', () => {
    expect(formatUsage(null)).toBe('')
    expect(
      formatUsage({ promptTokens: 10, completionTokens: 5, totalTokens: 15, cachedTokens: 8 }),
    ).toBe('输入 10 / 输出 5 / 缓存命中 8 token')
    expect(
      formatUsage({ promptTokens: null, completionTokens: null, totalTokens: null, cachedTokens: null }),
    ).toBe('')
  })

  it('状态与结束原因的展示映射', () => {
    expect(RUN_STATUS_META.completed).toMatchObject({ label: '成功' })
    expect(RUN_STATUS_META.partial).toMatchObject({ label: '部分完成' })
    expect(RUN_STATUS_META.running).toMatchObject({ label: '运行中' })
    expect(finishReasonLabel('max_rounds')).toBe('达到最大执行轮数')
    expect(finishReasonLabel('interrupted')).toContain('运行中断')
    expect(finishReasonLabel(null)).toBe('—')
  })
})
