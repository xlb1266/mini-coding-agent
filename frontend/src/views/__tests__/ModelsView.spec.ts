import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { VueWrapper } from '@vue/test-utils'

import { listModels, testModel } from '../../api/models'
import type { ModelItem } from '../../api/models'
import ModelsView from '../ModelsView.vue'

vi.mock('../../api/models', () => ({
  listModels: vi.fn(),
  createModel: vi.fn(),
  updateModel: vi.fn(),
  deleteModel: vi.fn(),
  setDefaultModel: vi.fn(),
  testModel: vi.fn(),
}))

function makeModel(overrides: Partial<ModelItem> = {}): ModelItem {
  return {
    id: 1,
    displayName: 'DeepSeek-V3',
    modelIdentifier: 'deepseek-chat',
    baseUrl: 'https://api.deepseek.com/v1',
    contextLength: 32768,
    maxOutputTokens: 4096,
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

async function mountView(): Promise<VueWrapper> {
  const wrapper = mount(ModelsView, { global: { plugins: [createPinia()] } })
  await flushPromises()
  return wrapper
}

function findButtonByText(wrapper: VueWrapper, text: string) {
  return wrapper.findAll('button').find((button) => button.text().includes(text))
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(listModels).mockResolvedValue([])
})

describe('ModelsView 列表页', () => {
  it('无模型时展示空状态与添加入口', async () => {
    const wrapper = await mountView()

    expect(wrapper.text()).toContain('还没有接入模型')
    expect(findButtonByText(wrapper, '新增模型')).toBeTruthy()
  })

  it('渲染模型列表与默认徽标、密钥状态', async () => {
    vi.mocked(listModels).mockResolvedValue([
      makeModel(),
      makeModel({
        id: 2,
        displayName: 'Qwen-Max',
        modelIdentifier: 'qwen-max',
        isDefault: false,
        hasApiKey: false,
      }),
    ])
    const wrapper = await mountView()

    const rows = wrapper.findAll('tbody tr')
    expect(rows).toHaveLength(2)
    expect(rows[0]!.text()).toContain('默认')
    expect(rows[0]!.text()).toContain('已配置')
    expect(rows[1]!.text()).toContain('未配置')
    expect(rows[1]!.text()).toContain('32,768')
    // 默认模型不显示"设为默认"按钮
    expect(rows[0]!.text()).not.toContain('设为默认')
    expect(rows[1]!.text()).toContain('设为默认')
  })

  it('测试连接成功时展示"模型已连接"与回复内容', async () => {
    vi.mocked(listModels).mockResolvedValue([makeModel()])
    vi.mocked(testModel).mockResolvedValue({ ok: true, reply: '连接成功' })
    const wrapper = await mountView()

    await findButtonByText(wrapper, '测试连接')!.trigger('click')
    await flushPromises()

    expect(vi.mocked(testModel)).toHaveBeenCalledWith(1)
    expect(wrapper.text()).toContain('模型已连接')
    expect(wrapper.text()).toContain('模型回复：连接成功')
  })

  it('测试失败时展示原因与排查建议', async () => {
    vi.mocked(listModels).mockResolvedValue([makeModel()])
    vi.mocked(testModel).mockResolvedValue({
      ok: false,
      errorType: 'auth',
      message: '密钥无效或没有访问权限',
      hint: '请检查 API Key 是否正确',
    })
    const wrapper = await mountView()

    await findButtonByText(wrapper, '测试连接')!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('测试失败：密钥无效或没有访问权限')
    expect(wrapper.text()).toContain('请检查 API Key 是否正确')
  })

  it('测试进行中按钮显示"正在测试"并禁用，防止重复提交', async () => {
    vi.mocked(listModels).mockResolvedValue([makeModel()])
    vi.mocked(testModel).mockReturnValue(new Promise(() => {}))
    const wrapper = await mountView()

    await findButtonByText(wrapper, '测试连接')!.trigger('click')
    await flushPromises()

    const testingButton = findButtonByText(wrapper, '正在测试')
    expect(testingButton).toBeTruthy()
    expect(testingButton!.attributes('disabled')).toBeDefined()
  })

  it('删除默认模型时必须先选择新的默认模型', async () => {
    vi.mocked(listModels).mockResolvedValue([
      makeModel({ id: 1, displayName: 'A', isDefault: false }),
      makeModel({ id: 2, displayName: 'B', isDefault: true }),
    ])
    const wrapper = await mountView()

    const rows = wrapper.findAll('tbody tr')
    const deleteButtons = rows.map((row) =>
      row.findAll('button').find((button) => button.text() === '删除'),
    )
    await deleteButtons[1]!.trigger('click')

    expect(wrapper.text()).toContain('请选择新的默认模型')
    const confirmButton = findButtonByText(wrapper, '确认删除')!
    expect(confirmButton.attributes('disabled')).toBeDefined()

    await wrapper.find('select').setValue(1)
    expect(confirmButton.attributes('disabled')).toBeUndefined()

    await confirmButton.trigger('click')
    await flushPromises()

    const { deleteModel } = await import('../../api/models')
    expect(vi.mocked(deleteModel)).toHaveBeenCalledWith(2, 1)
  })

  it('删除普通模型直接确认，无需选择新默认', async () => {
    vi.mocked(listModels).mockResolvedValue([
      makeModel({ id: 1, displayName: 'A', isDefault: true }),
      makeModel({ id: 2, displayName: 'B', isDefault: false }),
    ])
    const wrapper = await mountView()

    const rows = wrapper.findAll('tbody tr')
    await rows[1]!
      .findAll('button')
      .find((button) => button.text() === '删除')!
      .trigger('click')

    expect(wrapper.text()).not.toContain('请选择新的默认模型')
    const confirmButton = findButtonByText(wrapper, '确认删除')!
    expect(confirmButton.attributes('disabled')).toBeUndefined()

    await confirmButton.trigger('click')
    await flushPromises()

    const { deleteModel } = await import('../../api/models')
    expect(vi.mocked(deleteModel)).toHaveBeenCalledWith(2, undefined)
  })
})
