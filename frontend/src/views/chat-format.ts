/** 聊天工具卡片的纯展示逻辑：参数/结果格式化与截断，便于单测。
 *
 * 工具返回内容一律按不可信文本处理：这里只做字符串整理，渲染端
 * 使用文本插值（不拼 HTML、不执行脚本）。
 */

/** 工具卡片展开后结果默认展示的长度；更长内容先截断并提供“展开全部”。 */
export const RESULT_PREVIEW_LIMIT = 1200

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
