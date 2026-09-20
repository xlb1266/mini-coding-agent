import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import MarkdownContent from '../MarkdownContent.vue'
import { parseInline, parseMarkdown } from '../markdown'

describe('parseMarkdown', () => {
  it('解析标题、段落与围栏代码块', () => {
    const blocks = parseMarkdown('# 标题\n\n正文 **加粗**。\n\n```python\nprint(1)\n```')
    expect(blocks).toEqual([
      { type: 'heading', level: 1, inline: ['标题'] },
      { type: 'paragraph', inline: ['正文 ', { type: 'bold', children: ['加粗'] }, '。'] },
      { type: 'code', lang: 'python', text: 'print(1)' },
    ])
  })

  it('未闭合的围栏代码块也完整收集（容忍流式输出中途状态）', () => {
    const blocks = parseMarkdown('开头\n\n```js\nconst a = 1')
    expect(blocks[1]).toEqual({ type: 'code', lang: 'js', text: 'const a = 1' })
  })

  it('解析无序与有序列表，并支持一层嵌套', () => {
    const blocks = parseMarkdown('- 甲\n- 乙\n  - 乙一\n\n1. 第一\n2. 第二')
    expect(blocks).toHaveLength(2)
    const [ul, ol] = blocks as Extract<(typeof blocks)[number], { type: 'list' }>[]
    expect(ul.type).toBe('list')
    expect(ul.ordered).toBe(false)
    expect(ul.items).toHaveLength(2)
    expect(ul.items[1]!.children[0]).toMatchObject({ type: 'list', ordered: false })
    expect(ol!.ordered).toBe(true)
    expect(ol!.items.map((item) => item.inline)).toEqual([['第一'], ['第二']])
  })

  it('解析表格：表头、对齐与行', () => {
    const blocks = parseMarkdown('| 名称 | 数量 |\n| --- | :---: |\n| 苹果 | 3 |')
    expect(blocks[0]).toMatchObject({
      type: 'table',
      aligns: ['left', 'center'],
      header: [['名称'], ['数量']],
      rows: [[['苹果'], ['3']]],
    })
  })

  it('解析行内代码、加粗、斜体、删除线与链接', () => {
    expect(parseInline('`code` 和 **加粗** 与 *斜体* 与 ~~删除~~ 与 [文本](https://a.com)')).toEqual([
      { type: 'code', text: 'code' },
      ' 和 ',
      { type: 'bold', children: ['加粗'] },
      ' 与 ',
      { type: 'italic', children: ['斜体'] },
      ' 与 ',
      { type: 'del', children: ['删除'] },
      ' 与 ',
      { type: 'link', children: ['文本'], href: 'https://a.com' },
    ])
  })

  it('snake_case 与 3.5 不会被误判为斜体或列表', () => {
    expect(parseInline('变量 user_name 与 3.5 个')).toEqual(['变量 user_name 与 3.5 个'])
    expect(parseMarkdown('版本 3.5 发布')).toEqual([
      { type: 'paragraph', inline: ['版本 3.5 发布'] },
    ])
  })

  it('解析引用与分割线', () => {
    const blocks = parseMarkdown('> 引用一行\n\n---')
    expect(blocks[0]).toMatchObject({ type: 'blockquote', blocks: [{ type: 'paragraph' }] })
    expect(blocks[1]).toEqual({ type: 'hr' })
  })
})

describe('MarkdownContent 组件', () => {
  it('把 Markdown 渲染成对应 DOM 节点', () => {
    const wrapper = mount(MarkdownContent, {
      props: {
        text: '## 要点\n\n**重点**内容，见[文档](https://a.com)。\n\n- 第一条\n- 第二条\n\n```sql\nSELECT 1\n```',
      },
    })
    expect(wrapper.find('h2').text()).toBe('要点')
    expect(wrapper.find('strong').text()).toBe('重点')
    const link = wrapper.find('a')
    expect(link.attributes('href')).toBe('https://a.com')
    expect(link.attributes('target')).toBe('_blank')
    expect(wrapper.findAll('li')).toHaveLength(2)
    expect(wrapper.find('pre code').text()).toBe('SELECT 1')
    expect(wrapper.find('.md-lang').text()).toBe('sql')
  })

  it('不把内容插成 HTML（防注入）', () => {
    const wrapper = mount(MarkdownContent, {
      props: { text: '<img src=x onerror=alert(1)> **<script>1</script>**' },
    })
    expect(wrapper.find('img').exists()).toBe(false)
    expect(wrapper.find('script').exists()).toBe(false)
    expect(wrapper.text()).toContain('<img src=x onerror=alert(1)>')
  })

  it('cursor 为 true 时把光标放进最后一个段落内部', () => {
    const wrapper = mount(MarkdownContent, { props: { text: '正在生成', cursor: true } })
    const cursor = wrapper.find('.md-cursor')
    expect(cursor.exists()).toBe(true)
    expect(cursor.element.parentElement?.tagName).toBe('P')
  })
})
