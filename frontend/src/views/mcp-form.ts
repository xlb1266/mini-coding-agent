/** MCP 表单的纯函数校验与载荷构造，独立成模块便于单测。 */

import { ENV_VALUE_MASK, type McpServerItem, type McpServerPayload } from '../api/mcp'

/** 表单里一行环境变量的原始值。 */
export interface EnvRow {
  key: string
  value: string
}

export interface McpFormValues {
  name: string
  description: string
  command: string
  /** 每一项对应一个参数输入框，保留填写顺序；空行在提交时丢弃。 */
  args: string[]
  envRows: EnvRow[]
}

export type McpFormErrors = Partial<Record<'name' | 'description' | 'command' | 'env', string>>

export function emptyFormValues(): McpFormValues {
  return {
    name: '',
    description: '',
    command: '',
    args: [''],
    envRows: [{ key: '', value: '' }],
  }
}

/** 编辑时从已保存的 Server 构造表单：环境变量值只出现掩码。 */
export function formValuesFromServer(server: McpServerItem): McpFormValues {
  const envRows = Object.entries(server.env).map(([key, value]) => ({ key, value }))
  return {
    name: server.name,
    description: server.description,
    command: server.command,
    args: server.args.length > 0 ? [...server.args] : [''],
    envRows: envRows.length > 0 ? envRows : [{ key: '', value: '' }],
  }
}

function validateEnvRows(envRows: EnvRow[]): string | undefined {
  const seen = new Set<string>()
  for (const row of envRows) {
    const key = row.key.trim()
    const value = row.value.trim()
    if (!key && !value) continue // 整行为空表示未填写，跳过
    if (!key) return '环境变量名不能为空'
    if (key.includes('=') || /\s/.test(key)) return '环境变量名不能包含空格或等号'
    if (!value) return `请填写 ${key} 的值，不需要的变量请删除该行`
    if (seen.has(key)) return `环境变量 ${key} 重复`
    seen.add(key)
  }
  return undefined
}

/** 校验全部表单项，返回 {字段名: 错误原因}；空对象表示通过。 */
export function validateMcpForm(values: McpFormValues): McpFormErrors {
  const errors: McpFormErrors = {}
  if (!values.name.trim()) {
    errors.name = '请填写名称'
  }
  if (!values.command.trim()) {
    errors.command = '请填写启动命令'
  }
  const envError = validateEnvRows(values.envRows)
  if (envError) {
    errors.env = envError
  }
  return errors
}

/** 构造提交载荷；调用前应先通过 validateMcpForm。 */
export function buildMcpPayload(values: McpFormValues): McpServerPayload {
  const env: Record<string, string> = {}
  for (const row of values.envRows) {
    const key = row.key.trim()
    const value = row.value.trim()
    if (!key && !value) continue
    env[key] = value
  }

  return {
    name: values.name.trim(),
    description: values.description.trim(),
    command: values.command.trim(),
    args: values.args.map((arg) => arg.trim()).filter((arg) => arg !== ''),
    env,
  }
}

/** 列表展示：启动命令与参数按顺序拼接，仅作展示，实际执行始终分开传参。 */
export function commandPreview(command: string, args: string[]): string {
  return [command, ...args].join(' ')
}

/** 编辑时判断某行环境变量是否为已保存值的掩码（用于提示文案）。 */
export function isMaskedValue(value: string): boolean {
  return value === ENV_VALUE_MASK
}

/** 从 MCP JSON 配置里解析出的一条 Server 配置。 */
export interface ImportedMcpServer {
  /** 配置对象的键名；裸对象格式（没有键名）时为空字符串。 */
  name: string
  description: string
  command: string
  args: string[]
  env: EnvRow[]
}

export type McpImportResult =
  | { ok: true; servers: ImportedMcpServer[] }
  | { ok: false; error: string }

/** 常见客户端用来包裹 Server 列表的键名：Claude Desktop / Cursor 用 mcpServers，VS Code 用 servers。 */
const CONTAINER_KEYS = ['mcpServers', 'servers'] as const

/** 远程传输类型关键字：本阶段只支持本机 stdio。 */
const REMOTE_TYPES = ['http', 'sse', 'streamable-http', 'streamableHttp', 'websocket', 'ws']

type ParseServerResult = { ok: true; server: ImportedMcpServer } | { ok: false; error: string }

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/** 标量形式的参数/变量值转字符串；对象与数组不接受。 */
function toScalarText(value: unknown): string | null {
  if (typeof value === 'string') return value
  if (typeof value === 'number' && Number.isFinite(value)) return String(value)
  if (typeof value === 'boolean') return String(value)
  return null
}

function parseArgs(raw: unknown, label: string): { args: string[] } | { error: string } {
  if (raw === undefined || raw === null) return { args: [] }
  if (!Array.isArray(raw)) return { error: `${label}的 args 应为字符串数组` }
  const args: string[] = []
  for (const item of raw) {
    const text = toScalarText(item)
    if (text === null) return { error: `${label}的 args 里含有非文本参数，请手动填写` }
    args.push(text)
  }
  return { args }
}

function parseEnv(raw: unknown, label: string): { env: EnvRow[] } | { error: string } {
  if (raw === undefined || raw === null) return { env: [] }
  if (!isRecord(raw)) return { error: `${label}的 env 应为键值对象` }
  const env: EnvRow[] = []
  for (const [key, value] of Object.entries(raw)) {
    if (value === null || value === undefined) continue // 空值表示未填写，跳过
    const text = toScalarText(value)
    if (text === null) return { error: `${label}的环境变量 ${key} 的值不是文本，请手动填写` }
    env.push({ key, value: text })
  }
  return { env }
}

function parseServerEntry(name: string, raw: unknown): ParseServerResult {
  const label = name ? `「${name}」` : '该条目'
  if (!isRecord(raw)) return { ok: false, error: `${label}的配置应为对象` }

  const type = typeof raw.type === 'string' ? raw.type.toLowerCase() : ''
  if (typeof raw.url === 'string' || REMOTE_TYPES.includes(type)) {
    return {
      ok: false,
      error: `${label}看起来是远程 Server（url / ${type || 'http'}），本阶段只支持在本机通过 stdio 启动的 Server`,
    }
  }

  if (typeof raw.command !== 'string' || !raw.command.trim()) {
    return { ok: false, error: `${label}缺少 command（启动命令）` }
  }

  const argsResult = parseArgs(raw.args, label)
  if ('error' in argsResult) return { ok: false, error: argsResult.error }
  const envResult = parseEnv(raw.env, label)
  if ('error' in envResult) return { ok: false, error: envResult.error }

  return {
    ok: true,
    server: {
      name: name.trim(),
      description: typeof raw.description === 'string' ? raw.description.trim() : '',
      command: raw.command.trim(),
      args: argsResult.args,
      env: envResult.env,
    },
  }
}

/**
 * 解析 MCP 客户端导出的 JSON 配置，兼容三种写法：
 * 1. {"mcpServers": {"名称": {...}}}（Claude Desktop / Cursor 等，最常见）
 * 2. {"servers": {...}}（VS Code 风格）
 * 3. 直接粘贴单个 Server 对象 {"command": ..., "args": [...]}
 */
export function parseMcpImport(text: string): McpImportResult {
  const trimmed = text.trim()
  if (!trimmed) return { ok: false, error: '请先粘贴 JSON 配置' }

  let data: unknown
  try {
    data = JSON.parse(trimmed)
  } catch {
    return {
      ok: false,
      error: '这不是有效的 JSON，请检查是否完整复制（常见原因是缺少大括号、引号或逗号）',
    }
  }
  if (!isRecord(data)) return { ok: false, error: 'JSON 顶层应为一个对象' }

  const entries = containerEntries(data)
  if (entries === null) {
    return { ok: false, error: '没有找到 MCP Server 配置：需要有 mcpServers 字段，或直接粘贴单个 Server 对象' }
  }
  if (entries.length === 0) return { ok: false, error: '配置里没有任何 MCP Server' }

  const servers: ImportedMcpServer[] = []
  for (const [name, raw] of entries) {
    const parsed = parseServerEntry(name, raw)
    if (!parsed.ok) return { ok: false, error: parsed.error }
    servers.push(parsed.server)
  }
  return { ok: true, servers }
}

function containerEntries(data: Record<string, unknown>): [string, unknown][] | null {
  for (const key of CONTAINER_KEYS) {
    const value = data[key]
    if (isRecord(value)) return Object.entries(value)
  }
  // 裸 Server 对象：本身带 command 字段，键名留空由表单沿用现有名称
  if ('command' in data) return [['', data]]
  return null
}

/** 把导入结果填入表单；配置里没有名称/说明时沿用表单现有内容。 */
export function formValuesFromImport(
  server: ImportedMcpServer,
  fallback: { name: string; description: string },
): McpFormValues {
  return {
    name: server.name || fallback.name,
    description: server.description || fallback.description,
    command: server.command,
    args: server.args.length > 0 ? [...server.args] : [''],
    envRows: server.env.length > 0 ? server.env.map((row) => ({ ...row })) : [{ key: '', value: '' }],
  }
}

/** 表单是否已有内容（用于导入前提示会覆盖当前填写）。 */
export function hasFormContent(values: McpFormValues): boolean {
  return (
    values.name.trim() !== '' ||
    values.description.trim() !== '' ||
    values.command.trim() !== '' ||
    values.args.some((arg) => arg.trim() !== '') ||
    values.envRows.some((row) => row.key.trim() !== '' || row.value.trim() !== '')
  )
}
