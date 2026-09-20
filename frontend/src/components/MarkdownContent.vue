<script lang="ts">
/** Markdown 渲染组件：用 markdown.ts 把文本解析为块级/行内树后渲染成 Vue 节点。
 *  全程不经过 v-html，不可信内容只作为纯文本插入，避免注入。
 *  cursor 为 true 时在末尾追加流式光标，优先放进最后一个段落/列表项内部。
 */
import { computed, defineComponent, h } from 'vue'
import type { VNode } from 'vue'

import { parseMarkdown, type Block, type Inline, type TableAlign } from './markdown'

function renderInline(nodes: Inline[]): (VNode | string)[] {
  const out: (VNode | string)[] = []
  for (const node of nodes) {
    if (typeof node === 'string') {
      out.push(node)
      continue
    }
    if (node.type === 'code') out.push(h('code', { class: 'md-code' }, node.text))
    else if (node.type === 'bold') out.push(h('strong', renderInline(node.children)))
    else if (node.type === 'italic') out.push(h('em', renderInline(node.children)))
    else if (node.type === 'del') out.push(h('del', renderInline(node.children)))
    else
      out.push(
        h(
          'a',
          {
            class: 'md-link',
            href: node.href,
            target: '_blank',
            rel: 'noopener noreferrer',
          },
          renderInline(node.children),
        ),
      )
  }
  return out
}

function alignClass(align: TableAlign | undefined): string | undefined {
  if (align === 'center') return 'is-center'
  if (align === 'right') return 'is-right'
  return undefined
}

function renderBlocks(blocks: Block[]): VNode[] {
  const out: VNode[] = []
  for (const block of blocks) {
    switch (block.type) {
      case 'heading':
        out.push(h(`h${block.level}`, { class: 'md-heading' }, renderInline(block.inline)))
        break
      case 'paragraph':
        out.push(h('p', { class: 'md-paragraph' }, renderInline(block.inline)))
        break
      case 'code':
        out.push(
          h('div', { class: 'md-pre-wrap' }, [
            block.lang ? h('span', { class: 'md-lang' }, block.lang) : null,
            h('pre', { class: 'md-pre' }, [h('code', block.text)]),
          ]),
        )
        break
      case 'hr':
        out.push(h('hr', { class: 'md-hr' }))
        break
      case 'blockquote':
        out.push(h('blockquote', { class: 'md-quote' }, renderBlocks(block.blocks)))
        break
      case 'list':
        out.push(
          h(
            block.ordered ? 'ol' : 'ul',
            { class: 'md-list' },
            block.items.map((item) =>
              h('li', [...renderInline(item.inline), ...renderBlocks(item.children)]),
            ),
          ),
        )
        break
      case 'table':
        out.push(
          h('div', { class: 'md-table-wrap' }, [
            h('table', { class: 'md-table' }, [
              h('thead', [
                h(
                  'tr',
                  block.header.map((cell, index) =>
                    h('th', { class: alignClass(block.aligns[index]) }, renderInline(cell)),
                  ),
                ),
              ]),
              h(
                'tbody',
                block.rows.map((row) =>
                  h(
                    'tr',
                    row.map((cell, index) =>
                      h('td', { class: alignClass(block.aligns[index]) }, renderInline(cell)),
                    ),
                  ),
                ),
              ),
            ]),
          ]),
        )
        break
    }
  }
  return out
}

function pushCursor(vnode: VNode, cursor: VNode): void {
  if (Array.isArray(vnode.children)) (vnode.children as (VNode | string)[]).push(cursor)
  else if (vnode.children === null || vnode.children === undefined)
    vnode.children = [cursor]
  else vnode.children = [vnode.children as string, cursor]
}

function appendCursor(children: VNode[]): void {
  const cursor = h('span', { class: 'md-cursor' })
  const last = children[children.length - 1]
  if (!last || typeof last.type !== 'string') {
    children.push(cursor)
    return
  }
  if (last.type === 'p' || last.type === 'li') {
    pushCursor(last, cursor)
    return
  }
  if ((last.type === 'ul' || last.type === 'ol') && Array.isArray(last.children)) {
    const items = last.children as VNode[]
    const lastItem = items[items.length - 1]
    if (lastItem && lastItem.type === 'li') {
      pushCursor(lastItem, cursor)
      return
    }
  }
  children.push(cursor)
}

export default defineComponent({
  name: 'MarkdownContent',
  props: {
    text: { type: String, required: true },
    cursor: { type: Boolean, default: false },
  },
  setup(props) {
    const body = computed(() => {
      const children = renderBlocks(parseMarkdown(props.text))
      if (props.cursor) appendCursor(children)
      return children
    })
    return () => h('div', { class: 'md-body' }, body.value)
  },
})
</script>

<!-- 不用 scoped：节点由渲染函数手工创建，全局样式统一挂在 .md-body 前缀下隔离 -->
<style>
.md-body {
  overflow-wrap: break-word;
  color: inherit;
  font-size: 12.5px;
  line-height: 1.55;
}

.md-body > :first-child {
  margin-top: 0;
}

.md-body > :last-child {
  margin-bottom: 0;
}

.md-body .md-heading {
  margin: 10px 0 4px;
  color: inherit;
  font-weight: 650;
}

.md-body h1.md-heading {
  font-size: 15px;
}

.md-body h2.md-heading {
  font-size: 14px;
}

.md-body h3.md-heading {
  font-size: 13.5px;
}

.md-body h4.md-heading,
.md-body h5.md-heading,
.md-body h6.md-heading {
  font-size: 13px;
}

.md-body .md-paragraph {
  margin: 4px 0;
  white-space: pre-wrap;
}

.md-body .md-list {
  margin: 4px 0;
  padding-left: 20px;
}

.md-body .md-list li {
  margin: 2px 0;
  white-space: pre-wrap;
}

.md-body .md-list ul,
.md-body .md-list ol {
  margin: 2px 0;
}

.md-body .md-pre-wrap {
  position: relative;
  margin: 6px 0;
}

.md-body .md-lang {
  position: absolute;
  top: 5px;
  right: 8px;
  color: var(--text-tertiary);
  font-family: var(--font-mono);
  font-size: 10px;
}

.md-body .md-pre {
  margin: 0;
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-deep);
  overflow-x: auto;
}

.md-body .md-pre code {
  color: var(--text-primary);
  font-family: var(--font-mono);
  font-size: 12px;
}

.md-body .md-code {
  padding: 1px 4px;
  border-radius: 4px;
  background: var(--bg-hover);
  font-family: var(--font-mono);
  font-size: 12px;
}

.md-body .md-link {
  color: var(--accent);
  text-decoration: underline;
}

.md-body .md-quote {
  margin: 4px 0;
  padding: 2px 10px;
  border-left: 3px solid var(--border);
  color: var(--text-secondary);
}

.md-body .md-hr {
  margin: 8px 0;
  border: none;
  border-top: 1px solid var(--border);
}

.md-body .md-table-wrap {
  margin: 6px 0;
  overflow-x: auto;
}

.md-body .md-table {
  border-collapse: collapse;
}

.md-body .md-table th,
.md-body .md-table td {
  border: 1px solid var(--border);
  padding: 3px 8px;
  font-size: 12px;
  text-align: left;
}

.md-body .md-table th {
  background: var(--bg-hover);
  font-weight: 600;
}

.md-body .md-table .is-center {
  text-align: center;
}

.md-body .md-table .is-right {
  text-align: right;
}

.md-body .md-cursor {
  display: inline-block;
  width: 7px;
  height: 13px;
  margin-left: 2px;
  vertical-align: -2px;
  background: var(--accent);
  animation: md-blink 1s step-end infinite;
}

@keyframes md-blink {
  50% {
    opacity: 0;
  }
}
</style>
