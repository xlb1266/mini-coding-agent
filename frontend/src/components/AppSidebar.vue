<script setup lang="ts">
import { ref } from 'vue'

interface MenuItem {
  path: string
  label: string
  icon: string
}

// 收起状态记忆（跨刷新）：收起后变 56px 图标栏，悬停菜单项仍可看名称
const COLLAPSE_KEY = 'mini-agent:sidebar-collapsed'

function readCollapsed(): boolean {
  try {
    return localStorage.getItem(COLLAPSE_KEY) === '1'
  } catch {
    return false
  }
}

const collapsed = ref(readCollapsed())

function toggleCollapsed(): void {
  collapsed.value = !collapsed.value
  try {
    localStorage.setItem(COLLAPSE_KEY, collapsed.value ? '1' : '0')
  } catch {
    // 存不了就只在本页生效
  }
}

const icon = (paths: string): string =>
  `<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round">${paths}</svg>`

const menus: MenuItem[] = [
  {
    path: '/chat',
    label: '聊天',
    icon: icon('<path d="M3 3.5h10a1 1 0 0 1 1 1V10a1 1 0 0 1-1 1H8.2L5 13.4V11H3a1 1 0 0 1-1-1V4.5a1 1 0 0 1 1-1Z"/>'),
  },
  {
    path: '/agents',
    label: 'Agent 管理',
    icon: icon(
      '<circle cx="8" cy="2.6" r="1"/><path d="M8 3.6V5"/><rect x="3" y="5" width="10" height="7" rx="1.6"/><circle cx="5.9" cy="8.5" r="0.9" fill="currentColor" stroke="none"/><circle cx="10.1" cy="8.5" r="0.9" fill="currentColor" stroke="none"/>',
    ),
  },
  {
    path: '/models',
    label: '模型管理',
    icon: icon('<path d="M8 2.4 13.8 5.2 8 8 2.2 5.2Z"/><path d="m2.2 8.4 5.8 2.8 5.8-2.8"/><path d="m2.2 11.2 5.8 2.8 5.8-2.8"/>'),
  },
  {
    path: '/tools',
    label: '工具管理',
    icon: icon(
      '<path d="M2.5 4.5h11M2.5 8h11M2.5 11.5h11"/><circle cx="6" cy="4.5" r="1.4"/><circle cx="10.2" cy="8" r="1.4"/><circle cx="5" cy="11.5" r="1.4"/>',
    ),
  },
  {
    path: '/mcp',
    label: 'MCP 管理',
    icon: icon('<path d="M6 2.4V5.4M10 2.4V5.4"/><path d="M4.4 5.4h7.2v2.2a3.6 3.6 0 0 1-7.2 0Z"/><path d="M8 11.2v2.4"/>'),
  },
  {
    path: '/skills',
    label: 'Skills 管理',
    icon: icon('<path d="M3 3.6A1.6 1.6 0 0 1 4.6 2H13v10.4H4.6A1.6 1.6 0 0 0 3 14Z"/><path d="M3 12.4a1.6 1.6 0 0 1 1.6-1.6H13"/>'),
  },
  {
    path: '/runs',
    label: '运行记录',
    icon: icon('<circle cx="8" cy="8" r="5.6"/><path d="M8 5.2V8l2.1 1.5"/>'),
  },
  {
    path: '/evaluation',
    label: 'Agent 评测',
    icon: icon('<path d="M2.6 13.4h10.8M4.4 13.4V9.6M8 13.4V5.4M11.6 13.4V7.6"/>'),
  },
]
</script>

<template>
  <aside class="sidebar" :class="{ 'is-collapsed': collapsed }">
    <div class="brand">
      <span class="brand-mark">m</span>
      <span class="brand-name">mini-agent</span>
    </div>
    <nav class="nav">
      <RouterLink
        v-for="menu in menus"
        :key="menu.path"
        :to="menu.path"
        class="nav-item"
        :title="menu.label"
      >
        <span class="nav-icon" v-html="menu.icon" />
        <span class="nav-label">{{ menu.label }}</span>
      </RouterLink>
    </nav>
    <button
      class="collapse-toggle"
      type="button"
      :title="collapsed ? '展开侧边栏' : '收起侧边栏'"
      @click="toggleCollapsed"
    >
      <span class="toggle-icon" aria-hidden="true">{{ collapsed ? '»' : '«' }}</span>
      <span v-if="!collapsed" class="toggle-label">收起</span>
    </button>
  </aside>
</template>
