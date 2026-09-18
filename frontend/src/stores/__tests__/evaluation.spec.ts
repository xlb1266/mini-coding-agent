import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  deleteDataset,
  getDataset,
  getTask,
  listDatasets,
  listTasks,
  startTask,
  type EvalDatasetDetail,
  type EvalDatasetItem,
  type EvalTaskDetail,
  type EvalTaskItem,
} from '../../api/evaluation'
import { useEvaluationStore } from '../evaluation'

vi.mock('../../api/evaluation', () => ({
  listDatasets: vi.fn(),
  getDataset: vi.fn(),
  createDataset: vi.fn(),
  updateDataset: vi.fn(),
  deleteDataset: vi.fn(),
  createCase: vi.fn(),
  updateCase: vi.fn(),
  deleteCase: vi.fn(),
  listTasks: vi.fn(),
  getTask: vi.fn(),
  createTask: vi.fn(),
  startTask: vi.fn(),
}))

function makeDataset(overrides: Partial<EvalDatasetItem> = {}): EvalDatasetItem {
  return {
    id: 1,
    name: '示例数据集',
    description: '',
    caseCount: 2,
    isExample: true,
    createdAt: '2026-09-15T00:00:00+00:00',
    updatedAt: '2026-09-15T00:00:00+00:00',
    ...overrides,
  }
}

function makeDatasetDetail(id = 1): EvalDatasetDetail {
  return {
    ...makeDataset({ id }),
    cases: [
      {
        id: 11,
        datasetId: id,
        question: '问题一',
        expectedAnswer: '答案一',
        scoringRequirements: '要求一',
        createdAt: '2026-09-15T00:00:00+00:00',
        updatedAt: '2026-09-15T00:00:00+00:00',
      },
    ],
  }
}

function makeTask(overrides: Partial<EvalTaskItem> = {}): EvalTaskItem {
  return {
    id: 1,
    datasetId: 1,
    datasetName: '示例数据集',
    agentId: 1,
    agentName: '助手',
    scorerModelId: 2,
    scorerModelName: '评分模型',
    scorerModelIdentifier: 'scorer',
    passScore: 60,
    allowTools: false,
    status: 'completed',
    stats: {
      totalCases: 2,
      completedCases: 2,
      passedCount: 1,
      failedCount: 1,
      execFailedCount: 0,
      scoringFailedCount: 0,
      pendingCases: 0,
      executingCases: 0,
      scoringCases: 0,
      averageScore: 70,
    },
    createdAt: '2026-09-15T00:00:00+00:00',
    startedAt: null,
    finishedAt: null,
    ...overrides,
  }
}

function makeTaskDetail(id = 1): EvalTaskDetail {
  return {
    ...makeTask({ id }),
    agentConfig: { promptVersion: 1 },
    results: [],
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

describe('evaluation store', () => {
  it('fetchDatasets 保存列表', async () => {
    vi.mocked(listDatasets).mockResolvedValue([makeDataset()])
    const store = useEvaluationStore()
    await store.fetchDatasets()
    expect(store.datasets).toHaveLength(1)
    expect(store.datasetsLoaded).toBe(true)
  })

  it('fetchDataset 缓存详情，force 时重新拉取', async () => {
    vi.mocked(getDataset).mockResolvedValue(makeDatasetDetail())
    const store = useEvaluationStore()

    await store.fetchDataset(1)
    await store.fetchDataset(1)
    expect(getDataset).toHaveBeenCalledTimes(1)

    await store.fetchDataset(1, true)
    expect(getDataset).toHaveBeenCalledTimes(2)
    expect(store.datasetDetails[1]!.cases[0]!.question).toBe('问题一')
  })

  it('removeDataset 后刷新列表并清理详情缓存', async () => {
    vi.mocked(getDataset).mockResolvedValue(makeDatasetDetail())
    vi.mocked(listDatasets).mockResolvedValue([])
    vi.mocked(deleteDataset).mockResolvedValue(undefined)
    const store = useEvaluationStore()
    await store.fetchDataset(1)

    await store.removeDataset(1)
    expect(deleteDataset).toHaveBeenCalledWith(1)
    expect(store.datasetDetails[1]).toBeUndefined()
    expect(store.datasets).toHaveLength(0)
  })

  it('hasRunningTask 依据任务列表判断', async () => {
    vi.mocked(listTasks).mockResolvedValue([
      makeTask({ id: 1, status: 'completed' }),
      makeTask({ id: 2, status: 'running' }),
    ])
    const store = useEvaluationStore()
    await store.fetchTasks()
    expect(store.hasRunningTask).toBe(true)
    expect(store.runningTaskIds).toEqual([2])
  })

  it('fetchTask 缓存详情；pollRunning 强制刷新运行中的任务', async () => {
    vi.mocked(getTask)
      .mockResolvedValueOnce(makeTaskDetail(2))
      .mockResolvedValueOnce(makeTaskDetail(2))
    vi.mocked(listTasks).mockResolvedValue([
      makeTask({ id: 1, status: 'completed' }),
      makeTask({ id: 2, status: 'running' }),
    ])
    const store = useEvaluationStore()
    await store.fetchTasks()
    await store.fetchTask(2)
    expect(getTask).toHaveBeenCalledTimes(1)

    await store.pollRunning()
    // 只刷新运行中的任务（id=2），已完成的（id=1）不动
    expect(getTask).toHaveBeenCalledTimes(2)
    expect(getTask).toHaveBeenLastCalledWith(2)
  })

  it('startTask 成功后刷新任务列表；失败时写入错误信息', async () => {
    vi.mocked(listTasks).mockResolvedValue([makeTask({ id: 1, status: 'running' })])
    vi.mocked(startTask).mockResolvedValue(makeTask({ id: 1, status: 'running' }))
    const store = useEvaluationStore()

    await store.startTask(1)
    expect(startTask).toHaveBeenCalledWith(1)
    expect(store.tasks[0]!.status).toBe('running')
    expect(store.taskActionError).toBe('')

    vi.mocked(startTask).mockRejectedValue(new Error('任务已完成，不能再次启动'))
    await expect(store.startTask(1)).rejects.toThrow()
    expect(store.taskActionError).toBe('任务已完成，不能再次启动')
  })
})
