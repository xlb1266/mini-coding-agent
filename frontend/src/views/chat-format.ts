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
