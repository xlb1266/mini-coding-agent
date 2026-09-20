/** 轻量 Markdown 解析器：把 Agent 回复里常见的 Markdown 语法解析为块级/行内树，
 *  供 MarkdownContent 渲染成 Vue 节点。只输出数据结构、不产出 HTML，天然免注入。
 *  不追求完整 CommonMark，覆盖聊天回复常用语法即可：标题、段落、围栏代码块、
 *  列表（一层嵌套）、引用、表格、分割线，以及行内的代码/加粗/斜体/删除线/链接。
 */

export type Inline =
  | string
  | { type: 'code'; text: string }
  | { type: 'bold'; children: Inline[] }
  | { type: 'italic'; children: Inline[] }
  | { type: 'del'; children: Inline[] }
  | { type: 'link'; children: Inline[]; href: string }

export type TableAlign = 'left' | 'center' | 'right'

export interface ListItem {
  inline: Inline[]
  children: Block[]
}

export type Block =
  | { type: 'heading'; level: number; inline: Inline[] }
  | { type: 'paragraph'; inline: Inline[] }
  | { type: 'code'; lang: string; text: string }
  | { type: 'list'; ordered: boolean; items: ListItem[] }
  | { type: 'blockquote'; blocks: Block[] }
  | { type: 'hr' }
  | { type: 'table'; aligns: TableAlign[]; header: Inline[][]; rows: Inline[][][] }

const FENCE_PATTERN = /^\s{0,3}(`{3,}|~{3,})\s*(\S*)\s*$/
const HEADING_PATTERN = /^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$/
const HR_PATTERN = /^\s{0,3}([-*_])(\s*\1){2,}\s*$/
const QUOTE_PATTERN = /^\s{0,3}>/
const LIST_PATTERN = /^(\s*)([-*+]|\d{1,9}[.)])(\s+)(.*)$/

/** 行内语法统一用一个正则扫描，交替顺序决定优先级（加粗先于斜体等） */
const INLINE_PATTERN =
  /(`+)([^`]+?)\1|\*\*(?=\S)([\s\S]*?\S)\*\*|__(?=\S)([\s\S]*?\S)__|~~(?=\S)([\s\S]*?\S)~~|(?<![\w*])\*(?=\S)([^*\n]*?\S)\*(?![\w*])|(?<![\w_])_(?=\S)([^_\n]*?\S)_(?![\w_])|\[([^\]]+)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/

export function parseMarkdown(text: string): Block[] {
  const lines = text.replace(/\r\n?/g, '\n').split('\n')
  const blocks: Block[] = []
  let i = 0
  while (i < lines.length) {
    const line = lines[i]!
    if (line.trim() === '') {
      i++
      continue
    }
    const fence = line.match(FENCE_PATTERN)
    if (fence) {
      blocks.push(parseFence(lines, i, fence[1]!, fence[2] ?? ''))
      i = consumeFence(lines, i, fence[1]!)
      continue
    }
    const heading = line.match(HEADING_PATTERN)
    if (heading) {
      blocks.push({
        type: 'heading',
        level: heading[1]!.length,
        inline: parseInline(heading[2]!),
      })
      i++
      continue
    }
    if (HR_PATTERN.test(line)) {
      blocks.push({ type: 'hr' })
      i++
      continue
    }
    if (QUOTE_PATTERN.test(line)) {
      const quoted: string[] = []
      while (i < lines.length && QUOTE_PATTERN.test(lines[i]!)) {
        quoted.push(lines[i]!.replace(/^\s{0,3}>\s?/, ''))
        i++
      }
      blocks.push({ type: 'blockquote', blocks: parseMarkdown(quoted.join('\n')) })
      continue
    }
    if (line.includes('|') && i + 1 < lines.length && isTableSeparator(lines[i + 1]!)) {
      const aligns = splitTableRow(lines[i + 1]!).map(cellAlign)
      const header = splitTableRow(line).map(parseInline)
      const rows: Inline[][][] = []
      i += 2
      while (i < lines.length && lines[i]!.trim() !== '' && lines[i]!.includes('|')) {
        rows.push(splitTableRow(lines[i]!).map(parseInline))
        i++
      }
      blocks.push({ type: 'table', aligns, header, rows })
      continue
    }
    if (LIST_PATTERN.test(line)) {
      const parsed = parseList(lines, i)
      blocks.push(parsed.list)
      i = parsed.next
      continue
    }
    // 段落：连续收集直到空行或下一个块级语法开头
    const paragraph: string[] = []
    while (i < lines.length && lines[i]!.trim() !== '' && !isBlockStart(lines[i]!)) {
      paragraph.push(lines[i]!)
      i++
    }
    blocks.push({ type: 'paragraph', inline: parseInline(paragraph.join('\n')) })
  }
  return blocks
}

/** 行内解析：从左往右找最早的行内标记，标记内部递归解析 */
export function parseInline(text: string): Inline[] {
  const nodes: Inline[] = []
  let rest = text
  while (rest.length > 0) {
    const match = rest.match(INLINE_PATTERN)
    if (!match || match.index === undefined) {
      nodes.push(rest)
      break
    }
    if (match.index > 0) nodes.push(rest.slice(0, match.index))
    const [, _ticks, code, bold, boldU, del, emAsterisk, emUnderscore, linkText, href] = match
    if (code !== undefined) {
      // CommonMark 会去掉代码span内侧各一个空格
      nodes.push({ type: 'code', text: code.replace(/^ (.*) $/, '$1') })
    } else if (bold !== undefined) {
      nodes.push({ type: 'bold', children: parseInline(bold) })
    } else if (boldU !== undefined) {
      nodes.push({ type: 'bold', children: parseInline(boldU) })
    } else if (del !== undefined) {
      nodes.push({ type: 'del', children: parseInline(del) })
    } else if (emAsterisk !== undefined) {
      nodes.push({ type: 'italic', children: parseInline(emAsterisk) })
    } else if (emUnderscore !== undefined) {
      nodes.push({ type: 'italic', children: parseInline(emUnderscore) })
    } else {
      nodes.push({ type: 'link', children: parseInline(linkText ?? ''), href: href ?? '' })
    }
    rest = rest.slice(match.index + match[0].length)
  }
  return nodes
}

function parseFence(lines: string[], start: number, marker: string, lang: string): Block {
  const body: string[] = []
  let i = start + 1
  while (i < lines.length && !new RegExp(`^\\s{0,3}${marker[0]}{3,}\\s*$`).test(lines[i]!)) {
    body.push(lines[i]!)
    i++
  }
  return { type: 'code', lang, text: body.join('\n') }
}

/** 返回围栏代码块结束后的行号（未闭合时到末尾，容忍流式输出中途状态） */
function consumeFence(lines: string[], start: number, marker: string): number {
  let i = start + 1
  while (i < lines.length && !new RegExp(`^\\s{0,3}${marker[0]}{3,}\\s*$`).test(lines[i]!)) {
    i++
  }
  return i < lines.length ? i + 1 : lines.length
}

function isBlockStart(line: string): boolean {
  return (
    FENCE_PATTERN.test(line) ||
    HEADING_PATTERN.test(line) ||
    HR_PATTERN.test(line) ||
    QUOTE_PATTERN.test(line) ||
    LIST_PATTERN.test(line)
  )
}

/** 收集从 start 开始的连续列表行，按缩进组装成一层嵌套的列表块 */
function parseList(lines: string[], start: number): { list: Block; next: number } {
  const raw: { indent: number; ordered: boolean; text: string[] }[] = []
  let i = start
  while (i < lines.length && lines[i]!.trim() !== '') {
    const line = lines[i]!
    const match = line.match(LIST_PATTERN)
    if (match) {
      raw.push({
        indent: match[1]!.replace(/\t/g, '    ').length,
        ordered: /\d/.test(match[2]!),
        text: [match[4]!],
      })
    } else {
      const last = raw[raw.length - 1]
      const indent = line.replace(/\t/g, '    ').match(/^\s*/)![0].length
      // 缩进续行并入当前条目；顶格非列表行结束列表
      if (!last || indent < last.indent + 2) break
      last.text.push(line.trim())
    }
    i++
  }

  const rootIndent = raw[0]?.indent ?? 0
  const items: ListItem[] = []
  let current: ListItem | null = null
  let sub: { ordered: boolean; items: ListItem[] } | null = null
  for (const item of raw) {
    const entry: ListItem = { inline: parseInline(item.text.join('\n')), children: [] }
    if (item.indent > rootIndent + 1) {
      // 比根层级深的行归入前一个条目的子列表（同级或换序号时新开一个）
      if (!sub || sub.ordered !== item.ordered || current === null) {
        sub = { ordered: item.ordered, items: [entry] }
        current?.children.push({ type: 'list', ordered: item.ordered, items: sub.items })
      } else {
        sub.items.push(entry)
      }
    } else {
      sub = null
      current = entry
      items.push(entry)
    }
  }
  return { list: { type: 'list', ordered: raw[0]?.ordered ?? false, items }, next: i }
}

function splitTableRow(line: string): string[] {
  let text = line.trim()
  if (text.startsWith('|')) text = text.slice(1)
  if (text.endsWith('|')) text = text.slice(0, -1)
  return text
    .split(/(?<!\\)\|/)
    .map((cell) => cell.replace(/\\\|/g, '|').trim())
}

function isTableSeparator(line: string): boolean {
  if (!line.includes('-') || !line.includes('|')) return false
  const cells = splitTableRow(line)
  return cells.length > 0 && cells.every((cell) => /^:?-+:?$/.test(cell))
}

function cellAlign(cell: string): TableAlign {
  if (cell.startsWith(':') && cell.endsWith(':')) return 'center'
  if (cell.endsWith(':')) return 'right'
  return 'left'
}
