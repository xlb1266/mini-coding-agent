import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ThinkingCard from '../ThinkingCard.vue'
import type { ReasoningSegment } from '../../stores/chat'

function makeReasoning(overrides: Partial<ReasoningSegment> = {}): ReasoningSegment {
  return {
    kind: 'reasoning',
    text: '用户在问时间，需要调用工具查询。',
    durationMs: 1200,
    ...overrides,
  }
}

describe('ThinkingCard 收起态', () => {
  it('默认只有标题与耗时，完全不显示思考正文', () => {
    const wrapper = mount(ThinkingCard, { props: { reasoning: makeReasoning() } })

    expect(wrapper.text()).toContain('思考')
    expect(wrapper.text()).toContain('1.2s')
    expect(wrapper.find('.think-text').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('需要调用工具')
  })

  it('streaming 时标题为“思考中”，正文仍不外显', () => {
    const wrapper = mount(ThinkingCard, {
      props: { reasoning: makeReasoning({ durationMs: null }), streaming: true },
    })

    expect(wrapper.text()).toContain('思考中')
    expect(wrapper.find('.think-text').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('需要调用工具')
  })

  it('没有耗时不显示时长', () => {
    const wrapper = mount(ThinkingCard, {
      props: { reasoning: makeReasoning({ durationMs: null }) },
    })

    expect(wrapper.text()).not.toContain('ms')
    expect(wrapper.text()).not.toContain('1.2s')
  })
})

describe('ThinkingCard 展开态', () => {
  it('点击标题展开思考全文，再点击收起', async () => {
    const wrapper = mount(ThinkingCard, { props: { reasoning: makeReasoning() } })

    await wrapper.find('.think-head').trigger('click')

    expect(wrapper.find('.think-text').exists()).toBe(true)
    expect(wrapper.find('.think-text').text()).toBe('用户在问时间，需要调用工具查询。')

    await wrapper.find('.think-head').trigger('click')
    expect(wrapper.find('.think-text').exists()).toBe(false)
  })

  it('思考内容按纯文本渲染，不作为 HTML 执行', async () => {
    const malicious = '<img src=x onerror=alert(1)>'
    const wrapper = mount(ThinkingCard, {
      props: { reasoning: makeReasoning({ text: malicious }) },
    })
    await wrapper.find('.think-head').trigger('click')

    expect(wrapper.find('img').exists()).toBe(false)
    expect(wrapper.text()).toContain(malicious)
  })

  it('展开/收起向父级发出 toggle 事件，便于保持滚动位置', async () => {
    const wrapper = mount(ThinkingCard, { props: { reasoning: makeReasoning() } })

    await wrapper.find('.think-head').trigger('click')
    await wrapper.find('.think-head').trigger('click')

    expect(wrapper.emitted('toggle')).toHaveLength(2)
  })
})
