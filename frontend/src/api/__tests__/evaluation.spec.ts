import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  createCase,
  createDataset,
  createTask,
  deleteDataset,
  getDataset,
  getTask,
  listDatasets,
  listTasks,
  startTask,
  updateCase,
  updateDataset,
} from '../evaluation'

const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('evaluation API', () => {
  it('listDatasets 请求 /api/evaluations/datasets', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse([]))
    vi.stubGlobal('fetch', fetchMock)

    await expect(listDatasets()).resolves.toEqual([])
    expect(fetchMock).toHaveBeenCalledWith('/api/evaluations/datasets', expect.anything())
  })

  it('createDataset 以 POST 提交到数据集端点', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ id: 1 }, 201))
    vi.stubGlobal('fetch', fetchMock)

    await createDataset({ name: '数据集', description: '说明' })

    const [path, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(path).toBe('/api/evaluations/datasets')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body as string)).toEqual({ name: '数据集', description: '说明' })
  })

  it('getDataset / updateDataset / deleteDataset 指向对应资源路径', async () => {
    const fetchMock = vi.fn().mockImplementation(() => jsonResponse({ id: 3, cases: [] }))
    vi.stubGlobal('fetch', fetchMock)

    await getDataset(3)
    expect(fetchMock.mock.calls[0][0]).toBe('/api/evaluations/datasets/3')

    await updateDataset(3, { name: '新名', description: '' })
    const [, putInit] = fetchMock.mock.calls[1] as [string, RequestInit]
    expect(fetchMock.mock.calls[1][0]).toBe('/api/evaluations/datasets/3')
    expect(putInit.method).toBe('PUT')

    await deleteDataset(3)
    const [, delInit] = fetchMock.mock.calls[2] as [string, RequestInit]
    expect(fetchMock.mock.calls[2][0]).toBe('/api/evaluations/datasets/3')
    expect(delInit.method).toBe('DELETE')
  })

  it('createCase / updateCase 指向数据集下的用例路径', async () => {
    const fetchMock = vi.fn().mockImplementation(() => jsonResponse({ id: 9 }, 201))
    vi.stubGlobal('fetch', fetchMock)
    const payload = { question: '问题', expectedAnswer: '期望', scoringRequirements: '要求' }

    await createCase(5, payload)
    const [postPath, postInit] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(postPath).toBe('/api/evaluations/datasets/5/cases')
    expect(postInit.method).toBe('POST')
    expect(JSON.parse(postInit.body as string)).toEqual(payload)

    await updateCase(5, 9, payload)
    const [putPath, putInit] = fetchMock.mock.calls[1] as [string, RequestInit]
    expect(putPath).toBe('/api/evaluations/datasets/5/cases/9')
    expect(putInit.method).toBe('PUT')
  })

  it('listTasks / getTask / createTask / startTask 指向任务端点', async () => {
    const fetchMock = vi.fn().mockImplementation(() => jsonResponse({ id: 2 }))
    vi.stubGlobal('fetch', fetchMock)

    await listTasks()
    expect(fetchMock.mock.calls[0][0]).toBe('/api/evaluations/tasks')

    await getTask(2)
    expect(fetchMock.mock.calls[1][0]).toBe('/api/evaluations/tasks/2')

    const payload = {
      agentId: 1,
      datasetId: 2,
      scorerModelId: 3,
      passScore: 60,
      allowTools: false,
    }
    await createTask(payload)
    const [postPath, postInit] = fetchMock.mock.calls[2] as [string, RequestInit]
    expect(postPath).toBe('/api/evaluations/tasks')
    expect(postInit.method).toBe('POST')
    expect(JSON.parse(postInit.body as string)).toEqual(payload)

    await startTask(2)
    const [startPath, startInit] = fetchMock.mock.calls[3] as [string, RequestInit]
    expect(startPath).toBe('/api/evaluations/tasks/2/start')
    expect(startInit.method).toBe('POST')
  })
})
