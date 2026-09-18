import { describe, expect, it } from 'vitest'

import { ENV_VALUE_MASK, type McpServerItem } from '../../api/mcp'
import {
  buildMcpPayload,
  commandPreview,
  emptyFormValues,
  formValuesFromImport,
  formValuesFromServer,
  hasFormContent,
  isMaskedValue,
  parseMcpImport,
  validateMcpForm,
  type McpFormValues,
} from '../mcp-form'

function serverFixture(overrides: Partial<McpServerItem> = {}): McpServerItem {
  return {
    id: 1,
    name: '文件工具服务',
    description: '读写本地文件',
    command: 'uvx',
    args: ['-y', 'mcp-server-files'],
    env: { FILES_API_KEY: ENV_VALUE_MASK },
    enabled: false,
    lastTestStatus: 'untested',
    lastTestAt: null,
    lastTestErrorType: null,
    lastTestError: null,
    lastTestToolCount: null,
    lastTestTools: null,
    createdAt: '2026-09-11T00:00:00Z',
    updatedAt: '2026-09-11T00:00:00Z',
    ...overrides,
  }
}

function validValues(overrides: Partial<McpFormValues> = {}): McpFormValues {
  return {
    ...emptyFormValues(),
    name: '文件工具服务',
    command: 'uvx',
    args: ['-y', 'mcp-server-files'],
    envRows: [{ key: 'FILES_API_KEY', value: 'sk-secret-123' }],
    ...overrides,
  }
}

describe('validateMcpForm', () => {
  it('合法表单无错误', () => {
    expect(validateMcpForm(validValues())).toEqual({})
  })

  it('名称与启动命令必填', () => {
    const errors = validateMcpForm(validValues({ name: '  ', command: '' }))
    expect(errors.name).toBeTruthy()
    expect(errors.command).toBeTruthy()
  })

  it('整行为空的环境变量视为未填写，合法', () => {
    const errors = validateMcpForm(validValues({ envRows: [{ key: '', value: '' }] }))
    expect(errors.env).toBeUndefined()
  })

  it.each([
    [{ key: '', value: 'v' }, '变量名'],
    [{ key: 'A=b', value: 'v' }, '等号'],
    [{ key: 'A b', value: 'v' }, '空格'],
    [{ key: 'K', value: '' }, '值'],
  ])('非法环境变量行 %# 报 env 错误', (row) => {
    const errors = validateMcpForm(validValues({ envRows: [row] }))
    expect(errors.env).toBeTruthy()
  })

  it('环境变量名重复时报错', () => {
    const errors = validateMcpForm(
      validValues({
        envRows: [
          { key: 'K', value: '1' },
          { key: 'K', value: '2' },
        ],
      }),
    )
    expect(errors.env).toContain('重复')
  })

  it('掩码值是合法的变量值（表示保留原值）', () => {
    const errors = validateMcpForm(validValues({ envRows: [{ key: 'K', value: ENV_VALUE_MASK }] }))
    expect(errors.env).toBeUndefined()
  })
})

describe('buildMcpPayload', () => {
  it('去除首尾空白并丢弃空的参数与环境变量行', () => {
    const payload = buildMcpPayload(
      validValues({
        name: ' 名称 ',
        args: ['-y', ' ', 'mcp-server-files', ''],
        envRows: [
          { key: ' K ', value: ' v ' },
          { key: '', value: '' },
        ],
      }),
    )
    expect(payload.name).toBe('名称')
    expect(payload.args).toEqual(['-y', 'mcp-server-files'])
    expect(payload.env).toEqual({ K: 'v' })
  })

  it('掩码值原样提交，由后端保留已保存的原值', () => {
    const payload = buildMcpPayload(
      validValues({ envRows: [{ key: 'FILES_API_KEY', value: ENV_VALUE_MASK }] }),
    )
    expect(payload.env.FILES_API_KEY).toBe(ENV_VALUE_MASK)
  })

  it('顺序保留：参数与环境变量都按填写顺序提交', () => {
    const payload = buildMcpPayload(
      validValues({
        args: ['a', 'b', 'c'],
        envRows: [
          { key: 'A', value: '1' },
          { key: 'B', value: '2' },
        ],
      }),
    )
    expect(payload.args).toEqual(['a', 'b', 'c'])
    expect(Object.keys(payload.env)).toEqual(['A', 'B'])
  })
})

describe('formValuesFromServer', () => {
  it('环境变量只以掩码出现，参数保留顺序', () => {
    const values = formValuesFromServer(serverFixture())
    expect(values.name).toBe('文件工具服务')
    expect(values.args).toEqual(['-y', 'mcp-server-files'])
    expect(values.envRows).toEqual([{ key: 'FILES_API_KEY', value: ENV_VALUE_MASK }])
  })

  it('无参数/无环境变量时保留一行空行便于添加', () => {
    const values = formValuesFromServer(
      serverFixture({ args: [], env: {} }),
    )
    expect(values.args).toEqual([''])
    expect(values.envRows).toEqual([{ key: '', value: '' }])
  })

  it('编辑表单的行与服务器数据互不影响', () => {
    const server = serverFixture()
    const values = formValuesFromServer(server)
    values.args.push('extra')
    values.envRows.push({ key: 'X', value: '1' })
    expect(server.args).toEqual(['-y', 'mcp-server-files'])
    expect(Object.keys(server.env)).toEqual(['FILES_API_KEY'])
  })
})

describe('展示辅助', () => {
  it('命令预览按顺序拼接命令与参数', () => {
    expect(commandPreview('uvx', ['-y', 'mcp-server-fetch'])).toBe('uvx -y mcp-server-fetch')
    expect(commandPreview('node', [])).toBe('node')
  })

  it('掩码判断只识别统一的掩码常量', () => {
    expect(isMaskedValue(ENV_VALUE_MASK)).toBe(true)
    expect(isMaskedValue('sk-real')).toBe(false)
  })
})

describe('parseMcpImport', () => {
  const amapConfig = JSON.stringify({
    mcpServers: {
      'amap-maps': {
        command: 'npx',
        args: ['-y', '@amap/amap-maps-mcp-server'],
        env: { AMAP_MAPS_API_KEY: '你的key' },
      },
    },
  })

  it('解析常见 mcpServers 写法（含命令、参数与环境变量）', () => {
    const result = parseMcpImport(amapConfig)

    expect(result.ok).toBe(true)
    if (!result.ok) return
    expect(result.servers).toEqual([
      {
        name: 'amap-maps',
        description: '',
        command: 'npx',
        args: ['-y', '@amap/amap-maps-mcp-server'],
        env: [{ key: 'AMAP_MAPS_API_KEY', value: '你的key' }],
      },
    ])
  })

  it('支持配置里包含多个 Server', () => {
    const result = parseMcpImport(
      JSON.stringify({
        mcpServers: {
          one: { command: 'uvx', args: ['a'] },
          two: { command: 'node', args: ['b'] },
        },
      }),
    )

    expect(result.ok).toBe(true)
    if (!result.ok) return
    expect(result.servers.map((server) => server.name)).toEqual(['one', 'two'])
  })

  it('支持 VS Code 的 servers 写法与 type 字段', () => {
    const result = parseMcpImport(
      JSON.stringify({ servers: { fs: { type: 'stdio', command: 'uvx', args: ['mcp-fs'] } } }),
    )

    expect(result.ok).toBe(true)
    if (!result.ok) return
    expect(result.servers[0]?.command).toBe('uvx')
  })

  it('支持直接粘贴单个 Server 对象（名称留空由表单沿用）', () => {
    const result = parseMcpImport('{"command": "node", "args": ["server.js"]}')

    expect(result.ok).toBe(true)
    if (!result.ok) return
    expect(result.servers[0]?.name).toBe('')
    expect(result.servers[0]?.args).toEqual(['server.js'])
  })

  it('数字与布尔参数、变量值转成字符串，空值变量跳过', () => {
    const result = parseMcpImport(
      JSON.stringify({
        mcpServers: {
          s: {
            command: 'node',
            args: [8080, true],
            env: { PORT: 8080, DEBUG: false, EMPTY: null },
          },
        },
      }),
    )

    expect(result.ok).toBe(true)
    if (!result.ok) return
    expect(result.servers[0]?.args).toEqual(['8080', 'true'])
    expect(result.servers[0]?.env).toEqual([
      { key: 'PORT', value: '8080' },
      { key: 'DEBUG', value: 'false' },
    ])
  })

  it('导入的说明字段会被采用', () => {
    const result = parseMcpImport(
      JSON.stringify({ mcpServers: { s: { command: 'node', description: '本地文件服务' } } }),
    )

    expect(result.ok).toBe(true)
    if (!result.ok) return
    expect(result.servers[0]?.description).toBe('本地文件服务')
  })

  it.each([
    ['', '粘贴'],
    ['{ not json', '有效的 JSON'],
    ['[1, 2]', '对象'],
    ['{"mcpServers": {}}', '没有任何 MCP Server'],
    ['{"mcpServers": {"s": {"url": "https://example.com/mcp"}}}', 'stdio'],
    ['{"mcpServers": {"s": {"type": "sse", "url": "https://example.com/sse"}}}', 'stdio'],
    ['{"mcpServers": {"s": {"args": ["-y"]}}}', 'command'],
    ['{"mcpServers": {"s": {"command": "node", "args": "-y"}}}', 'args'],
    ['{"mcpServers": {"s": {"command": "node", "env": ["A=1"]}}}', 'env'],
    ['{"mcpServers": {"s": {"command": "node", "args": [{"a": 1}]}}}', '非文本参数'],
    ['{"description": "没有 Server 列表"}', 'mcpServers'],
  ])('非法配置 %# 给出可读错误', (text, keyword) => {
    const result = parseMcpImport(text)

    expect(result.ok).toBe(false)
    if (result.ok) return
    expect(result.error).toContain(keyword)
  })
})

describe('formValuesFromImport', () => {
  it('把导入结果填成表单值，空参数/空环境变量保留一行空行', () => {
    const values = formValuesFromImport(
      { name: 'amap-maps', description: '', command: 'npx', args: [], env: [] },
      { name: '原名称', description: '原说明' },
    )

    expect(values.name).toBe('amap-maps')
    expect(values.description).toBe('原说明')
    expect(values.command).toBe('npx')
    expect(values.args).toEqual([''])
    expect(values.envRows).toEqual([{ key: '', value: '' }])
  })

  it('导入没有名称时沿用表单现有名称', () => {
    const values = formValuesFromImport(
      { name: '', description: '导入的说明', command: 'node', args: ['a'], env: [{ key: 'K', value: 'v' }] },
      { name: '原名称', description: '原说明' },
    )

    expect(values.name).toBe('原名称')
    expect(values.description).toBe('导入的说明')
    expect(values.envRows).toEqual([{ key: 'K', value: 'v' }])
  })

  it('导入的环境变量为明文，保存后由后端加密与掩码展示', () => {
    const values = formValuesFromImport(
      { name: 's', description: '', command: 'npx', args: ['-y'], env: [{ key: 'K', value: 'sk-plain' }] },
      { name: '', description: '' },
    )

    expect(values.envRows[0]?.value).toBe('sk-plain')
    expect(isMaskedValue(values.envRows[0]?.value ?? '')).toBe(false)
  })
})

describe('hasFormContent', () => {
  it('空表单为 false，任意字段有内容为 true', () => {
    expect(hasFormContent(emptyFormValues())).toBe(false)
    expect(hasFormContent(validValues({ name: '' }))).toBe(true)
    expect(hasFormContent(emptyFormValues())).toBe(false)
    expect(
      hasFormContent({ ...emptyFormValues(), envRows: [{ key: 'K', value: 'v' }] }),
    ).toBe(true)
  })
})
