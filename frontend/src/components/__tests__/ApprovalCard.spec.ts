import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ApprovalCard from '../ApprovalCard.vue'
import type { ApprovalActivity } from '../../stores/chat'

function makeApproval(overrides: Partial<ApprovalActivity> = {}): ApprovalActivity {
  return {
    requestId: 'apr-1',
    callId: 'call_1',
    toolName: 'builtin_shell',
    displayName: 'Shell 命令',
    toolType: 'builtin',
    status: 'pending',
    arguments: { command: 'echo hi' },
    ...overrides,
  }
}

describe('ApprovalCard 等待审批', () => {
  it('展示审批标识、工具名、类型、状态与整理后的参数，展示批准/拒绝按钮', () => {
    const wrapper = mount(ApprovalCard, { props: { approval: makeApproval() } })

    expect(wrapper.text()).toContain('执行审批')
    expect(wrapper.text()).toContain('Shell 命令')
    expect(wrapper.text()).toContain('内置工具')
    expect(wrapper.text()).toContain('等待审批')
    expect(wrapper.text()).toContain('command')
    expect(wrapper.text()).toContain('echo hi')
    expect(wrapper.find('.btn-approve').text()).toBe('批准')
    expect(wrapper.find('.btn-reject').text()).toBe('拒绝')
  })

  it('点击按钮发出 decide 事件；deciding 期间按钮置灰', async () => {
    const wrapper = mount(ApprovalCard, { props: { approval: makeApproval() } })

    await wrapper.find('.btn-approve').trigger('click')
    expect(wrapper.emitted('decide')).toEqual([[true]])
    await wrapper.find('.btn-reject').trigger('click')
    expect(wrapper.emitted('decide')).toEqual([[true], [false]])

    // 提交中：按钮禁用防重复提交
    await wrapper.setProps({ approval: makeApproval({ deciding: true }) })
    expect(wrapper.find('.btn-approve').attributes('disabled')).toBeDefined()
    expect(wrapper.find('.btn-reject').attributes('disabled')).toBeDefined()
    await wrapper.find('.btn-approve').trigger('click')
    expect(wrapper.emitted('decide')).toHaveLength(2)
  })

  it('终态只读：不出现按钮，状态文字随决定变化', () => {
    const approved = mount(ApprovalCard, { props: { approval: makeApproval({ status: 'approved' }) } })
    expect(approved.text()).toContain('已批准')
    expect(approved.find('button').exists()).toBe(false)

    const rejected = mount(ApprovalCard, { props: { approval: makeApproval({ status: 'rejected' }) } })
    expect(rejected.text()).toContain('已拒绝')

    const timedOut = mount(ApprovalCard, { props: { approval: makeApproval({ status: 'timeout' }) } })
    expect(timedOut.text()).toContain('已超时')

    const cancelled = mount(ApprovalCard, { props: { approval: makeApproval({ status: 'cancelled' }) } })
    expect(cancelled.text()).toContain('已取消')
  })

  it('决议失败信息显示在卡片内', () => {
    const wrapper = mount(ApprovalCard, {
      props: { approval: makeApproval({ error: '审批请求不存在或已被处理' }) },
    })
    expect(wrapper.text()).toContain('审批请求不存在或已被处理')
  })
})
