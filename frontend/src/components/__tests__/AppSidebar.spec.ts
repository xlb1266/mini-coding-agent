import { mount } from '@vue/test-utils'
import { beforeAll, beforeEach, describe, expect, it } from 'vitest'

import router from '../../router'
import AppSidebar from '../AppSidebar.vue'

beforeAll(async () => {
  await router.push('/chat')
  await router.isReady()
})

beforeEach(() => {
  localStorage.clear()
})

describe('AppSidebar 侧边导航', () => {
  it('按固定顺序渲染 8 个菜单项', () => {
    const wrapper = mount(AppSidebar, { global: { plugins: [router] } })
    const items = wrapper.findAll('.nav-item')

    expect(items).toHaveLength(8)
    expect(items.map((item) => item.text())).toEqual([
      '聊天',
      'Agent 管理',
      '模型管理',
      '工具管理',
      'MCP 管理',
      'Skills 管理',
      '运行记录',
      'Agent 评测',
    ])
  })

  it('当前地址对应的菜单项带选中样式', async () => {
    await router.push('/models')
    const wrapper = mount(AppSidebar, { global: { plugins: [router] } })
    const active = wrapper.find('.nav-item.router-link-active')

    expect(active.text()).toContain('模型管理')
  })

  it('默认展开；点击切换按钮收起为图标栏并写入 localStorage', async () => {
    const wrapper = mount(AppSidebar, { global: { plugins: [router] } })
    const toggle = wrapper.find('.collapse-toggle')

    expect((wrapper.find('.sidebar').element as HTMLElement).classList.contains('is-collapsed')).toBe(false)
    expect(toggle.text()).toContain('收起')

    await toggle.trigger('click')

    expect((wrapper.find('.sidebar').element as HTMLElement).classList.contains('is-collapsed')).toBe(true)
    // 收起后菜单文字由 CSS 隐藏（图标栏），按钮变为展开
    expect(toggle.text()).not.toContain('收起')
    expect(localStorage.getItem('mini-agent:sidebar-collapsed')).toBe('1')

    await toggle.trigger('click')

    expect((wrapper.find('.sidebar').element as HTMLElement).classList.contains('is-collapsed')).toBe(false)
    expect(localStorage.getItem('mini-agent:sidebar-collapsed')).toBe('0')
  })

  it('localStorage 记录收起态时打开即收起', () => {
    localStorage.setItem('mini-agent:sidebar-collapsed', '1')
    const wrapper = mount(AppSidebar, { global: { plugins: [router] } })

    expect((wrapper.find('.sidebar').element as HTMLElement).classList.contains('is-collapsed')).toBe(true)
  })
})
