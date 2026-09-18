import { mount } from '@vue/test-utils'
import { beforeAll, describe, expect, it } from 'vitest'

import router from '../../router'
import AppSidebar from '../AppSidebar.vue'

beforeAll(async () => {
  await router.push('/chat')
  await router.isReady()
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
})
