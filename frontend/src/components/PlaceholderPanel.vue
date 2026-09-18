<script setup lang="ts">
defineProps<{
  module: string
}>()
</script>

<template>
  <section class="card placeholder-panel">
    <h2 class="placeholder-title">本模块将在后续阶段开发</h2>
    <p class="placeholder-text">「{{ module }}」的功能尚未开始实现，当前保留统一布局与页面入口。</p>
    <p class="placeholder-note">
      当前阶段（阶段一）请先完成：启动后端与前端服务，确认左侧菜单可以切换、
      刷新任意地址不出现空白页、聊天页中的 /api/health 状态为已连接。
    </p>
  </section>
</template>

<style scoped>
.placeholder-panel {
  padding: 18px 20px;
}

.placeholder-title {
  font-size: 14px;
  font-weight: 600;
}

.placeholder-text {
  margin-top: 6px;
  color: var(--text-secondary);
}

.placeholder-note {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--border);
  font-size: 11px;
  color: var(--text-tertiary);
}
</style>
