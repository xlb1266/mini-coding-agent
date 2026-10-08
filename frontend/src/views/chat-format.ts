/** 聊天工具卡片的纯展示逻辑：动作描述、主参数、时间线分组与截断，便于单测。
 *
 * 工具返回内容一律按不可信文本处理：这里只做字符串整理，渲染端
 * 使用文本插值（不拼 HTML、不执行脚本）。
 */

import type {
  ApprovalActivity,
  CompactActivity,
  GenerationSegment,
  ReasoningSegment,
  ToolActivity,
} from '../stores/chat'

/** 工具卡片展开后结果默认展示的长度；更长内容先截断并提供“展开全部”。 */
export const RESULT_PREVIEW_LIMIT = 1200

/** 折叠行展示文本的最大长度；超出截断加省略号。 */
export const ROW_TEXT_LIMIT = 72

/** 主参数键名的通用优先级；各工具另有自己的偏好（见 TOOL_ARGUMENT_KEYS）。 */
const PRIMARY_ARGUMENT_KEYS = [
  'path',
  'file_path',
  'filePath',
  'directory',
  'dir',
  'pattern',
  'query',
  'keyword',
  'command',
  'cmd',
  'url',
  'name',
]

/** 各内置工具最相关的主参数：搜索看 pattern、命令看 command、文件看 path。 */
const TOOL_ARGUMENT_KEYS: Record<string, string[]> = {
  builtin_grep: ['pattern', 'query', 'glob', 'path'],
  builtin_shell: ['command', 'cmd'],
  builtin_file: ['path'],
  builtin_edit_file: ['path'],
  builtin_list_dir: ['path'],
  load_skill: ['skill', 'skill_name', 'name'],
}

/** 常见扩展名的徽标写法；其余取扩展名前三个字母。 */
const EXTENSION_LABELS: Record<string, string> = {
  json: '{}',
  jsonc: '{}',
  yaml: 'YML',
  yml: 'YML',
  markdown: 'MD',
  html: 'HTM',
  python: 'PY',
}

function clip(text: string): string {
  const trimmed = text.trim()
  return trimmed.length > ROW_TEXT_LIMIT ? trimmed.slice(0, ROW_TEXT_LIMIT - 1) + '…' : trimmed
}

/** 参数值格式化：字符串保留原文与换行，其余 JSON 化（缩进便于查看字段和值）。 */
export function formatArgumentValue(value: unknown): string {
  if (typeof value === 'string') return value
  if (value === null || value === undefined) return '（空）'
  return JSON.stringify(value, null, 2)
}

/** 参数对象转展示行；null（参数不是合法 JSON）返回空数组。 */
export function argumentEntries(
  args: Record<string, unknown> | null | undefined,
): { key: string; value: string }[] {
  if (!args) return []
  return Object.entries(args).map(([key, value]) => ({ key, value: formatArgumentValue(value) }))
}

/** 取工具参数里适合放进折叠行 pill 的主参数：优先常见键名，否则第一个字符串值。
 *  没有可展示的字符串参数时返回 null（不显示 pill）。 */
export function primaryArgument(
  args: Record<string, unknown> | null | undefined,
): string | null {
  if (!args) return null
  const isText = (value: unknown): value is string =>
    typeof value === 'string' && value.trim().length > 0
  const entries = Object.entries(args)
  const picked =
    entries.find(([key, value]) => PRIMARY_ARGUMENT_KEYS.includes(key) && isText(value)) ??
    entries.find(([, value]) => isText(value))
  if (!picked || !isText(picked[1])) return null
  return clip(picked[1])
}

/** 工具调用行的展示文本：优先该工具最相关的主参数，其次参数里的字符串，
 *  都没有时退回结果摘要；确实没有可展示内容时返回 null。 */
export function toolRowText(
  tool: Pick<ToolActivity, 'toolName' | 'arguments' | 'summary'>,
): string | null {
  const args = tool.arguments
  if (args) {
    for (const key of TOOL_ARGUMENT_KEYS[tool.toolName] ?? []) {
      const value = args[key]
      if (typeof value === 'string' && value.trim()) return clip(value)
    }
    const generic = primaryArgument(args)
    if (generic) return generic
  }
  return tool.summary?.trim() ? clip(tool.summary) : null
}

/** 工具调用行的动作前缀与徽标（模仿轨迹区卡片的“类型徽标 + 动作前缀”）。 */
export interface ToolAction {
  /** 动作前缀，如“已读取：”“搜索文件” */
  prefix: string
  /** 徽标文字：文件类显示扩展名，其余显示工具图标 */
  badgeLabel: string
  /** 徽标风格：accent 用于文件/MCP，muted 用于其余小图标 */
  badgeTone: 'accent' | 'muted'
}

function fileBadge(args: Record<string, unknown> | null | undefined): string {
  const path = args?.['path'] ?? args?.['file_path'] ?? args?.['filePath']
  if (typeof path !== 'string') return '▤'
  const name = path.trim().split(/[\\/]/).pop() ?? ''
  const dot = name.lastIndexOf('.')
  if (dot <= 0 || dot === name.length - 1) return '▤'
  const ext = name.slice(dot + 1).toLowerCase()
  return EXTENSION_LABELS[ext] ?? ext.slice(0, 3).toUpperCase()
}

/** 工具调用的动作描述：内置工具用中文动作（与轨迹卡片一致），
 *  其余（MCP / Skill / 未知）用工具自身的展示名做主标题。 */
export function toolAction(
  tool: Pick<ToolActivity, 'toolName' | 'toolType' | 'displayName' | 'arguments'>,
): ToolAction {
  const args = tool.arguments ?? null
  switch (tool.toolName) {
    case 'builtin_file': {
      const action = typeof args?.['action'] === 'string' ? args['action'] : 'read'
      return {
        prefix: action === 'write' ? '已写入：' : '已读取：',
        badgeLabel: fileBadge(args),
        badgeTone: 'accent',
      }
    }
    case 'builtin_edit_file':
      return { prefix: '已编辑：', badgeLabel: fileBadge(args), badgeTone: 'accent' }
    case 'builtin_list_dir':
      return { prefix: '已列出：', badgeLabel: '▤', badgeTone: 'muted' }
    case 'builtin_grep':
      return { prefix: '搜索文件', badgeLabel: '⌕', badgeTone: 'muted' }
    case 'builtin_shell':
      return { prefix: '执行命令', badgeLabel: '>_', badgeTone: 'muted' }
    case 'builtin_current_time':
      return { prefix: '查询时间', badgeLabel: '◷', badgeTone: 'muted' }
    case 'load_skill':
      return { prefix: '加载 Skill', badgeLabel: '✦', badgeTone: 'accent' }
    default:
      return {
        prefix: tool.displayName || tool.toolName,
        badgeLabel: tool.toolType === 'mcp' ? '⚙' : '·',
        badgeTone: tool.toolType === 'mcp' ? 'accent' : 'muted',
      }
  }
}

/** 时间线展示项：连续的工具调用折叠为一组（“使用N个工具”），点击组卡片才展开。 */
export type ChatDisplayItem =
  | { kind: 'text'; key: string; text: string }
  | { kind: 'reasoning'; key: string; reasoning: ReasoningSegment }
  | { kind: 'toolGroup'; key: string; tools: ToolActivity[] }
  | { kind: 'approval'; key: string; approval: ApprovalActivity }
  | { kind: 'compact'; key: string; compact: CompactActivity }

/** 把时间线段落整理成展示项：相邻工具合并成一组，其余段落一一对应。 */
export function buildDisplayItems(segments: GenerationSegment[]): ChatDisplayItem[] {
  const items: ChatDisplayItem[] = []
  for (const segment of segments) {
    const last = items[items.length - 1]
    if (segment.kind === 'tool') {
      if (last && last.kind === 'toolGroup') {
        last.tools.push(segment.tool)
      } else {
        items.push({ kind: 'toolGroup', key: `group-${items.length}`, tools: [segment.tool] })
      }
      continue
    }
    const key = `${segment.kind}-${items.length}`
    if (segment.kind === 'text') items.push({ kind: 'text', key, text: segment.text })
    else if (segment.kind === 'reasoning') items.push({ kind: 'reasoning', key, reasoning: segment })
    else if (segment.kind === 'approval') items.push({ kind: 'approval', key, approval: segment.approval })
    else items.push({ kind: 'compact', key, compact: segment.compact })
  }
  return items
}

/** 结果文本整理：结构化内容（JSON）重新缩进，普通文本原样保留换行。 */
export function formatResultText(text: string): string {
  const trimmed = text.trim()
  if ((trimmed.startsWith('{') && trimmed.endsWith('}')) || (trimmed.startsWith('[') && trimmed.endsWith(']'))) {
    try {
      const parsed: unknown = JSON.parse(trimmed)
      if (parsed !== null && typeof parsed === 'object') {
        return JSON.stringify(parsed, null, 2)
      }
    } catch {
      // 不是合法 JSON，按原始文本展示
    }
  }
  return text
}

// ---------------------------------------------------------------------------
// Shell 结果徽章与文件写入聚合（阶段十五 demo）：纯展示逻辑，便于单测
// ---------------------------------------------------------------------------

/** 解析内置工具结果里的结构化数据（后端把结果 JSON 序列化后放进 result.text）。 */
export function parseToolResultJson(tool: ToolActivity): Record<string, unknown> | null {
  if (!tool.result || tool.status !== 'success') return null
  try {
    const parsed: unknown = JSON.parse(tool.result.text)
    return parsed !== null && typeof parsed === 'object' && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : null
  } catch {
    return null
  }
}

/** Shell 结果徽章的展示状态；非 Shell 工具返回 null。 */
export interface ShellBadge {
  /** 徽标配色：success 成功 / danger 失败（含超时）/ running 后台运行中 */
  tone: 'success' | 'danger' | 'running'
  icon: string
  label: string
  /** 后台任务的标识（长 ID 展示时由组件截断） */
  taskId: string | null
}

/** Shell 结果徽章（纯展示，不追踪真实任务状态）：
 *  - 超时被终止 → ✗ 超时终止；
 *  - 后台启动 / 前台超预算自动转后台 → ● 运行中 + taskId；
 *  - 其余按成功（✓）/ 失败（✗）展示；非 Shell 工具或没有结果时返回 null。 */
export function shellBadge(tool: ToolActivity): ShellBadge | null {
  if (tool.toolName !== 'builtin_shell') return null
  if (tool.status === 'running') return null
  const data = parseToolResultJson(tool)
  if (tool.status === 'cancelled' || tool.status === 'unknown') {
    return { tone: 'danger', icon: '✕', label: '已取消', taskId: null }
  }
  if (tool.status !== 'success') {
    return { tone: 'danger', icon: '✕', label: '失败', taskId: null }
  }
  if (data === null) return null
  const taskId = typeof data['taskId'] === 'string' && data['taskId'] ? data['taskId'] : null
  if (data['timedOut'] === true) {
    return { tone: 'danger', icon: '✗', label: '超时终止', taskId: null }
  }
  if (data['autoBackgrounded'] === true || data['action'] === 'background') {
    return { tone: 'running', icon: '●', label: '运行中', taskId }
  }
  if (data['action'] === 'stop' || data['action'] === 'list') {
    return { tone: 'success', icon: '✓', label: '完成', taskId: null }
  }
  if (data['success'] === false) {
    return { tone: 'danger', icon: '✗', label: '失败', taskId: null }
  }
  return { tone: 'success', icon: '✓', label: '完成', taskId: null }
}

/** 是否为文件写入类调用：builtin_file 的 write 动作与 builtin_edit_file 全部操作。 */
export function isFileWriteTool(tool: ToolActivity): boolean {
  if (tool.status === 'running') return false
  if (tool.toolName === 'builtin_edit_file') return true
  return tool.toolName === 'builtin_file' && tool.arguments?.['action'] === 'write'
}

/** 一次文件写入的展示信息：路径、结果（新建/覆盖/追加/替换 N 处）与 diff 或写入内容。 */
export interface FileWriteEntry {
  callId: string
  path: string
  /** 结果短标签：新建 / 覆盖 / 追加 / 替换 N 处 */
  outcomeLabel: string
  /** 是否带统一 diff（edit_file），否则展示写入内容 */
  isEdit: boolean
  diff: string
  content: string
  sizeBytes: number | null
  truncated: boolean
}

const OUTCOME_LABELS: Record<string, string> = {
  created: '新建',
  overwritten: '覆盖',
  appended: '追加',
}

function entryPath(tool: ToolActivity): string {
  const fromArgs = tool.arguments?.['path']
  if (typeof fromArgs === 'string' && fromArgs.trim()) return fromArgs.trim()
  return '（未知路径）'
}

/** 把一次文件写入调用转成展示信息；参数异常时返回 null。 */
export function fileWriteEntry(tool: ToolActivity): FileWriteEntry | null {
  if (!isFileWriteTool(tool)) return null
  const data = parseToolResultJson(tool)
  if (tool.toolName === 'builtin_edit_file') {
    const replacements = data?.['replacements']
    const diff = typeof data?.['diff'] === 'string' ? data['diff'] : ''
    return {
      callId: tool.callId,
      path: entryPath(tool),
      outcomeLabel:
        typeof replacements === 'number' && replacements > 0 ? `替换 ${replacements} 处` : '编辑',
      isEdit: true,
      diff,
      content: '',
      sizeBytes: typeof data?.['sizeBytes'] === 'number' ? data['sizeBytes'] : null,
      truncated: tool.result?.truncated === true,
    }
  }
  const outcome = typeof data?.['outcome'] === 'string' ? data['outcome'] : ''
  const content = typeof tool.arguments?.['content'] === 'string' ? tool.arguments['content'] : ''
  return {
    callId: tool.callId,
    path: entryPath(tool),
    outcomeLabel: OUTCOME_LABELS[outcome] ?? '写入',
    isEdit: false,
    diff: '',
    content,
    sizeBytes: typeof data?.['sizeBytes'] === 'number' ? data['sizeBytes'] : null,
    truncated: tool.result?.truncated === true,
  }
}

/** 组内展示分区：写入类调用全部聚合进一个 writes 节点（置于首个写入的位置），
 *  其余调用按原顺序展示——轨迹里"修改了 N 个文件"一张卡的来源。 */
export type GroupDisplayNode =
  | { kind: 'writes'; tools: ToolActivity[]; entries: FileWriteEntry[] }
  | { kind: 'tool'; tool: ToolActivity }

export function partitionGroupTools(tools: ToolActivity[]): GroupDisplayNode[] {
  const writeTools = tools.filter((tool) => isFileWriteTool(tool))
  if (writeTools.length === 0) {
    return tools.map((tool) => ({ kind: 'tool' as const, tool }))
  }
  const entries = writeTools
    .map((tool) => fileWriteEntry(tool))
    .filter((entry): entry is FileWriteEntry => entry !== null)
  const writeSet = new Set(writeTools.map((tool) => tool.callId))
  const nodes: GroupDisplayNode[] = []
  let writesPlaced = false
  for (const tool of tools) {
    if (writeSet.has(tool.callId)) {
      if (!writesPlaced) {
        writesPlaced = true
        nodes.push({ kind: 'writes', tools: writeTools, entries })
      }
      continue
    }
    nodes.push({ kind: 'tool', tool })
  }
  return nodes
}
