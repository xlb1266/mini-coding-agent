import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi, beforeEach } from 'vitest'

import ChatComposer from '../ChatComposer.vue'
import { MODE_PRESETS, matchModePreset } from '../../api/chat'
import type { ContextUsage, ModePreset } from '../../api/chat'
import type { ModelItem } from '../../api/models'
import router from '../../router'

vi.mock('../../api/models', () => ({
  listModels: vi.fn().mockResolvedValue([]),
  createModel: vi.fn(),
  updateModel: vi.fn(),
  deleteModel: vi.fn(),
  setDefaultModel: vi.fn(),
  testModel: vi.fn(),
}))

interface ComposerProps {
  disabled: boolean
  generating: boolean
  stopPending: boolean
  sessionReady: boolean
  preset: ModePreset | null
  modelLabel: string
  modelReady: boolean
  activeModelId: number | null
  models: ModelItem[]
  usage: ContextUsage | null
}

function makeModel(overrides: Partial<ModelItem> = {}): ModelItem {
  return {
    id: 1,
    displayName: 'DeepSeek-V3',
    modelIdentifier: 'deepseek-chat',
    baseUrl: 'https://api.deepseek.com/v1',
    contextLength: 32768,
    maxOutputTokens: 1024,
    temperature: 0.7,
    inputPrice: null,
    outputPrice: null,
    cachedInputPrice: null,
    isDefault: true,
    hasApiKey: true,
    createdAt: '2026-09-11T02:00:00+00:00',
    updatedAt: '2026-09-11T02:00:00+00:00',
    ...overrides,
  }
}

function makeUsage(overrides: Partial<ContextUsage> = {}): ContextUsage {
  return {
    estimatedTokens: 330000,
    contextLength: 1000000,
    availableInputTokens: 900000,
    hasMcpTools: false,
    categories: [
      { key: 'systemPrompt', label: '系统提示词', tokens: 210000, percent: 21 },
      { key: 'skills', label: '技能', tokens: 30000, percent: 3 },
      { key: 'tools', label: '工具定义', tokens: 50000, percent: 5 },
      { key: 'summary', label: '会话摘要', tokens: 0, percent: 0 },
      { key: 'messages', label: '消息', tokens: 40000, percent: 4 },
    ],
    ...overrides,
  }
}

function mountComposer(props: Partial<ComposerProps> = {}) {
  return mount(ChatComposer, {
    props: {
      disabled: false,
      generating: false,
      stopPending: false,
      sessionReady: true,
      preset: MODE_PRESETS[1]!,
      modelLabel: 'DeepSeek-V3',
      modelReady: true,
      activeModelId: 1,
      models: [makeModel(), makeModel({ id: 2, displayName: 'GLM-5.3', modelIdentifier: 'glm-5.3' })],
      usage: makeUsage(),
      ...props,
    },
    global: { plugins: [router] },
  })
}

beforeEach(() => {
  document.body.innerHTML = ''
})

describe('matchModePreset（档位组合映射）', () => {
  it('三个预设与底层组合一一对应', () => {
    expect(matchModePreset('off', 'autonomous')?.key).toBe('autonomous_full')
    expect(matchModePreset('off', 'stage_confirm')?.key).toBe('stage_confirm')
    expect(matchModePreset('standard', 'stage_confirm')?.key).toBe('confirm_changes')
  })

  it('不匹配任何预设的组合返回 null（如 strict）', () => {
    expect(matchModePreset('strict', 'stage_confirm')).toBeNull()
    expect(matchModePreset(null, null)).toBeNull()
  })
})

describe('ChatComposer 输入与发送', () => {
  it('Enter 发送内容，clearDraft 清空输入框', async () => {
    const wrapper = mountComposer()
    const textarea = wrapper.find('textarea')
    await textarea.setValue('你好')
    await textarea.trigger('keydown', { key: 'Enter' })
    await flushPromises()

    expect(wrapper.emitted('send')?.[0]).toEqual(['你好'])

    ;(wrapper.vm as { clearDraft: () => void }).clearDraft()
    await flushPromises()
    expect((textarea.element as HTMLTextAreaElement).value).toBe('')
  })

  it('Shift + Enter 与输入法组字状态的回车不发送', async () => {
    const wrapper = mountComposer()
    const textarea = wrapper.find('textarea')
    await textarea.setValue('你好')
    await textarea.trigger('keydown', { key: 'Enter', shiftKey: true })
    await textarea.trigger('keydown', { key: 'Enter', isComposing: true, keyCode: 229 })

    expect(wrapper.emitted('send')).toBeUndefined()
  })

  it('生成中不发送，按钮切换为停止并 emit stop', async () => {
    const wrapper = mountComposer({ generating: true })
    const textarea = wrapper.find('textarea')
    await textarea.setValue('你好')
    await textarea.trigger('keydown', { key: 'Enter' })

    expect(wrapper.emitted('send')).toBeUndefined()

    const stop = wrapper.find('.send-button.is-stop')
    expect(stop.exists()).toBe(true)
    await stop.trigger('click')

    expect(wrapper.emitted('stop')).toHaveLength(1)
  })
})

describe('ChatComposer 执行档位菜单', () => {
  it('打开菜单列出三个预设（含说明文案），点击项 emit changeMode', async () => {
    const wrapper = mountComposer()

    await wrapper.find('.chip').trigger('click')
    const items = wrapper.findAll('.menu-mode .menu-item')
    expect(items).toHaveLength(3)
    expect(wrapper.text()).toContain('完全访问，无审批操作')
    expect(wrapper.text()).toContain('做完一个阶段生成交接文档并停下，等你验收后继续')

    await items[2]!.trigger('click')
    await flushPromises()

    expect(wrapper.emitted('changeMode')?.[0]).toEqual([MODE_PRESETS[2]])
  })

  it('当前生效预设的菜单项带勾选，chip 显示预设名', async () => {
    const wrapper = mountComposer({ preset: MODE_PRESETS[0]! })

    expect(wrapper.text()).toContain('高度自主')
    await wrapper.find('.chip').trigger('click')

    const checked = wrapper.findAll('.menu-mode .menu-item .menu-check')
    expect(checked).toHaveLength(1)
  })

  it('组合不匹配预设时 chip 显示自定义档位', () => {
    const wrapper = mountComposer({ preset: null })

    expect(wrapper.text()).toContain('自定义档位')
  })
})

describe('ChatComposer 模型切换菜单', () => {
  it('平铺列出模型并勾选当前项，点击其他模型 emit changeModel', async () => {
    const wrapper = mountComposer()
    const chips = wrapper.findAll('.chip')
    const modelChip = chips[chips.length - 1]!
    await modelChip.trigger('click')

    const items = wrapper.findAll('.menu-model .menu-item')
    expect(items).toHaveLength(2)
    expect(items[0]!.find('.menu-check').exists()).toBe(true)
    expect(items[0]!.text()).toContain('deepseek-chat')

    await items[1]!.trigger('click')
    await flushPromises()

    expect(wrapper.emitted('changeModel')?.[0]).toEqual([2])
  })

  it('模型菜单提供「管理模型」入口', async () => {
    const wrapper = mountComposer()
    const chips = wrapper.findAll('.chip')
    await chips[chips.length - 1]!.trigger('click')

    expect(wrapper.text()).toContain('管理模型')
  })
})

describe('ChatComposer 上下文容量', () => {
  it('点开仪表显示总量、进度条与分类明细，不显示缓存命中率', async () => {
    const wrapper = mountComposer()

    expect(wrapper.find('.gauge').exists()).toBe(true)
    await wrapper.find('.gauge').trigger('click')

    expect(wrapper.text()).toContain('上下文容量')
    expect(wrapper.text()).toContain('33万/100万')
    const rows = wrapper.findAll('.usage-row')
    expect(rows).toHaveLength(5)
    expect(wrapper.text()).toContain('系统提示词')
    expect(wrapper.text()).not.toContain('缓存命中')
  })

  it('绑定 MCP Server 时提示工具定义未计入', async () => {
    const wrapper = mountComposer({ usage: makeUsage({ hasMcpTools: true }) })
    await wrapper.find('.gauge').trigger('click')

    expect(wrapper.text()).toContain('MCP 工具定义')
  })
})
