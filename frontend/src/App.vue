<script setup lang="ts">
import { RouterView, useRoute } from 'vue-router'
import { computed, onMounted } from 'vue'

import AppSidebar from './components/AppSidebar.vue'
import { useAppStore } from './stores/app'

const appStore = useAppStore()
const route = useRoute()

// 聊天页需要占满整列高度（左右分栏各自滚动），其余页面保持常规文档流
const isChatRoute = computed(() => route.name === 'chat')

onMounted(() => {
  void appStore.checkBackend()
})
</script>

<template>
  <div class="app-shell">
    <AppSidebar />
    <main class="app-main">
      <div class="page" :class="{ 'page-fill': isChatRoute }">
        <RouterView />
      </div>
    </main>
  </div>
</template>
