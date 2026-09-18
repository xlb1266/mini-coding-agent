import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import CompactionCard from '../CompactionCard.vue'
import type { CompactActivity } from '../../stores/chat'

function makeCompact(overrides: Partial<CompactActivity> = {}): CompactActivity {
  return {
    compactionId: 'cmp-1',
    status: 'success',
    reason: '估算输入约 5000 token，达到可用输入容量的 80%',
    estimatedTokensBefore: 5000,
    estimatedTokensAfter: 900,
    processedMessages: 12,
    keptMessages: 4,
    summaryTokens: 220,
    batches: 2,
    modelCalls: 2,
    durationMs: 1200,
    error: null,
    ...overrides,
  }
}

describe('CompactionCard', () => {
  it('成功卡片：展示标题、状态、估算 Token 前后与处理/保留数量', () => {
    const wrapper = mount(CompactionCard, { props: { compact: makeCompact() } })

    expect(wrapper.text()).toContain('上下文压缩')
    expect(wrapper.text()).toContain('已完成')
    expect(wrapper.text()).toContain('估算 5000 → 900 token')
    expect(wrapper.text()).toContain('整理 12 条，保留 4 条')
    expect(wrapper.text()).toContain('摘要约 220 token（估算）')
    expect(wrapper.text()).toContain('2 批 · 模型调用 2 次')
    expect(wrapper.find('.compact-card').classes()).toContain('is-success')
  })

  it('运行中卡片显示压缩中与触发原因，不显示结果数字', () => {
    const wrapper = mount(
      CompactionCard,
      {
        props: {
          compact: makeCompact({
            status: 'running',
            estimatedTokensAfter: null,
            processedMessages: null,
            keptMessages: null,
            batches: null,
            modelCalls: null,
            durationMs: null,
          }),
        },
      },
    )

    expect(wrapper.text()).toContain('压缩中')
    expect(wrapper.text()).toContain('估算 5000 token')
    expect(wrapper.text()).not.toContain('整理')
  })

  it('失败卡片展示失败原因', () => {
    const wrapper = mount(
      CompactionCard,
      {
        props: {
          compact: makeCompact({
            status: 'error',
            error: { type: 'server', message: '会话摘要保存失败', hint: '本次请求将改用备用裁剪' },
          }),
        },
      },
    )

    expect(wrapper.text()).toContain('失败')
    expect(wrapper.text()).toContain('会话摘要保存失败')
  })

  it('备用裁剪卡片展示裁掉与保留数量，并说明不影响会话历史', () => {
    const wrapper = mount(
      CompactionCard,
      {
        props: {
          compact: makeCompact({
            compactionId: null,
            status: 'trimmed',
            estimatedTokensAfter: 3000,
            processedMessages: null,
            summaryTokens: null,
            batches: null,
            modelCalls: null,
            droppedMessages: 4,
            keptMessages: 6,
          }),
        },
      },
    )

    expect(wrapper.text()).toContain('上下文裁剪')
    expect(wrapper.text()).toContain('已裁剪')
    expect(wrapper.text()).toContain('裁掉 4 条，保留 6 条')
    expect(wrapper.text()).not.toContain('摘要约')
    expect(wrapper.text()).toContain('会话历史保持完整')
  })
})
