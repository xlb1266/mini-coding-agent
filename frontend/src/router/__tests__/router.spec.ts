import { beforeAll, describe, expect, it } from 'vitest'

import router from '../index'

beforeAll(async () => {
  await router.push('/chat')
  await router.isReady()
})

describe('router 路由', () => {
  it.each([
    '/chat',
    '/agents',
    '/models',
    '/tools',
    '/tools/1',
    '/mcp',
    '/skills',
    '/skills/demo/edit',
    '/runs',
    '/evaluation',
  ])('注册了独立地址 %s', async (path) => {
    await router.push(path)
    expect(router.currentRoute.value.path).toBe(path)
  })

  it('根路径重定向到聊天页', async () => {
    await router.push('/')
    expect(router.currentRoute.value.path).toBe('/chat')
  })

  it('未匹配地址兜底回聊天页，不出现空白', async () => {
    await router.push('/no-such-page')
    expect(router.currentRoute.value.path).toBe('/chat')
  })
})
