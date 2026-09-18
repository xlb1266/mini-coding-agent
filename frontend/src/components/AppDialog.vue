<script setup lang="ts">
defineProps<{
  open: boolean
  title: string
  width?: number
}>()

defineEmits<{
  close: []
}>()
</script>

<template>
  <div v-if="open" class="dialog-overlay" @click.self="$emit('close')">
    <div class="dialog-card card" :style="{ width: `${width ?? 560}px` }" role="dialog" :aria-label="title">
      <div class="dialog-head">
        <h2 class="dialog-title">{{ title }}</h2>
        <button class="dialog-close" type="button" aria-label="关闭" @click="$emit('close')">×</button>
      </div>
      <div class="dialog-body">
        <slot />
      </div>
      <div v-if="$slots.actions" class="dialog-actions">
        <slot name="actions" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.dialog-overlay {
  position: fixed;
  inset: 0;
  z-index: 40;
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding: 48px 16px;
  background: rgba(45, 32, 23, 0.35);
}

.dialog-card {
  display: flex;
  flex-direction: column;
  max-width: calc(100vw - 32px);
  max-height: calc(100vh - 96px);
}

.dialog-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 18px;
  border-bottom: 1px solid var(--border);
}

.dialog-title {
  font-family: var(--font-display);
  font-size: 14.5px;
  font-weight: 650;
}

.dialog-close {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--text-tertiary);
  font-size: 14px;
  cursor: pointer;
}

.dialog-close:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}

.dialog-body {
  padding: 16px 18px;
  overflow-y: auto;
}

.dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 12px 18px;
  border-top: 1px solid var(--border);
}
</style>
