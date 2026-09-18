<script setup lang="ts">
defineProps<{
  title: string
  description?: string
}>()
</script>

<template>
  <header class="page-header">
    <h1 class="page-title">{{ title }}</h1>
    <p v-if="description" class="page-description">{{ description }}</p>
  </header>
</template>

<style scoped>
.page-header {
  margin-bottom: 16px;
}

.page-title {
  font-family: var(--font-display);
  font-size: 18px;
  font-weight: 650;
  color: var(--text-primary);
}

.page-description {
  margin-top: 3px;
  font-size: 12px;
  color: var(--text-secondary);
}
</style>
