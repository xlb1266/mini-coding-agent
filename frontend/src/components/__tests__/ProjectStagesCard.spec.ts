import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it } from 'vitest'

import ProjectStagesCard from '../ProjectStagesCard.vue'
import type { StageProject } from '../../api/chat'

function makeProject(overrides: Partial<StageProject> = {}): StageProject {
  return {
    id: 1,
    title: '待办清单应用',
    agentId: 3,
    agentName: '开发助手',
    workspaceDir: 'D:/ws/demo',
    stages: [
      { name: '阶段一：项目骨架', goal: '搭好目录与基础配置', status: 'done' },
      { name: '阶段二：核心功能', goal: '完成增删改查', status: 'in_progress' },
      { name: '阶段三：联调验收', goal: '整体跑通', status: 'pending' },
    ],
    currentStage: 2,
    status: 'in_progress',
    stageSessions: { 1: 11 },
    planSessionId: 10,
    summarySessionId: null,
    createdAt: '2026-10-06T00:00:00Z',
    updatedAt: '2026-10-06T00:00:00Z',
    ...overrides,
  }
}

function mountCard(props: Parameters<typeof makeProject> extends never ? never : {
  project?: StageProject
  sessionRole?: 'plan' | 'stage' | 'summary' | null
  awaitingAcceptance?: boolean
  busy?: boolean
} = {}) {
  return mount(ProjectStagesCard, {
    props: {
      project: props.project ?? makeProject(),
      sessionRole: props.sessionRole ?? 'stage',
      awaitingAcceptance: props.awaitingAcceptance ?? false,
      busy: props.busy ?? false,
    },
  })
}

/** 卡片默认收起：需要操作清单/按钮的用例先点头部展开。 */
async function expand(wrapper: ReturnType<typeof mountCard>): Promise<void> {
  if ((wrapper.find('.card-body').element as HTMLElement).style.display === 'none') {
    await wrapper.find('.card-head').trigger('click')
  }
}

/** v-show 的收起/展开断言：jsdom 会缓存游离元素的 getComputedStyle，
 *  isVisible() 在首次查询后对运行时 inline 更新失真，改查 inline style。 */
function bodyDisplay(wrapper: ReturnType<typeof mountCard>): string {
  return (wrapper.find('.card-body').element as HTMLElement).style.display
}

describe('ProjectStagesCard', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('默认收起：只显示标题行，不显示清单与推进按钮', () => {
    const wrapper = mountCard({ sessionRole: 'plan' })

    expect(wrapper.find('.card-head').exists()).toBe(true)
    expect(wrapper.text()).toContain('待办清单应用')
    expect(wrapper.text()).toContain('进行中 · 1/3')
    expect(bodyDisplay(wrapper)).toBe('none')
  })

  it('展开后目标全文展示（不截断），可再收起且状态写入 localStorage', async () => {
    const wrapper = mountCard()

    await wrapper.find('.card-head').trigger('click')
    expect(bodyDisplay(wrapper)).toBe('')
    const goals = wrapper.findAll('.stage-goal').map((node) => node.text())
    // 完整目标全文，没有被省略号截断
    expect(goals[0]).toBe('搭好目录与基础配置')
    expect(goals[2]).toBe('整体跑通')
    expect(localStorage.getItem('mini-agent:stage-card-expanded')).toBe('1')

    await wrapper.find('.card-head').trigger('click')
    expect(bodyDisplay(wrapper)).toBe('none')
    expect(localStorage.getItem('mini-agent:stage-card-expanded')).toBe('0')
  })

  it('记住展开状态：localStorage 为 1 时打开即展开', () => {
    localStorage.setItem('mini-agent:stage-card-expanded', '1')
    const wrapper = mountCard()
    expect(bodyDisplay(wrapper)).toBe('')
  })

  it('规划会话展开后：显示确认开始按钮并发出 start 事件', async () => {
    const wrapper = mountCard({
      project: makeProject({
        status: 'awaiting_start',
        currentStage: 0,
        stages: [
          { name: '阶段一：项目骨架', goal: '搭好骨架', status: 'pending' },
          { name: '阶段二：核心功能', goal: '完成功能', status: 'pending' },
        ],
      }),
      sessionRole: 'plan',
    })
    await expand(wrapper)

    const button = wrapper.find('button.btn-primary')
    expect(button.exists()).toBe(true)
    expect(button.text()).toContain('开始阶段 1')
    expect(button.text()).toContain('将自动新建会话')
    await button.trigger('click')
    expect(wrapper.emitted('start')).toHaveLength(1)
  })

  it('阶段会话等待验收且有未完成阶段：显示进入下一阶段按钮', async () => {
    const wrapper = mountCard({ awaitingAcceptance: true })
    await expand(wrapper)

    const button = wrapper.find('button.btn-primary')
    expect(button.text()).toContain('进入下一阶段')
    await button.trigger('click')
    expect(wrapper.emitted('next')).toHaveLength(1)
  })

  it('阶段会话等待验收且全部完成：显示生成项目汇总按钮', async () => {
    const wrapper = mountCard({
      project: makeProject({
        status: 'awaiting_summary',
        currentStage: 3,
        stages: [
          { name: '阶段一：项目骨架', goal: '搭好骨架', status: 'done' },
          { name: '阶段二：核心功能', goal: '完成功能', status: 'done' },
        ],
      }),
      awaitingAcceptance: true,
    })
    await expand(wrapper)

    const button = wrapper.find('button.btn-primary')
    expect(button.text()).toContain('生成项目汇总')
    await button.trigger('click')
    expect(wrapper.emitted('summary')).toHaveLength(1)
  })

  it('busy 时推进按钮置灰；点击阶段行发出 openSession', async () => {
    const wrapper = mountCard({
      project: makeProject({ status: 'awaiting_start' }),
      sessionRole: 'plan',
      busy: true,
    })
    await expand(wrapper)

    expect((wrapper.find('button.btn-primary').element as HTMLButtonElement).disabled).toBe(true)
    await wrapper.find('button.stage-row').trigger('click')
    expect(wrapper.emitted('openSession')![0]).toEqual([11])
  })
})
