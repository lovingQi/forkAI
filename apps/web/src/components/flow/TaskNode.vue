<template>
  <div class="task-node" :class="`st-${data.status || 'pending'}`">
    <Handle type="target" :position="Position.Left" />
    <div class="ring" />
    <el-icon class="icon"><component :is="iconName" /></el-icon>
    <div class="meta">
      <div class="label">{{ data.label }}</div>
      <div class="summary">{{ summary }}</div>
    </div>
    <Handle type="source" :position="Position.Right" />
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { Handle, Position } from '@vue-flow/core'

const props = defineProps<{ data: any }>()

const ICONS: Record<string, string> = {
  focklift: 'Sort',
  head: 'RefreshRight',
  follow_back: 'Van',
  get_pallet: 'Aim',
  charge: 'Lightning',
  drive: 'Position'
}

const iconName = computed(() => ICONS[props.data.taskType] || 'Box')

const summary = computed(() => {
  const p = props.data.params || {}
  switch (props.data.taskType) {
    case 'focklift':
      return `pos=${p.pos ?? '?'}mm`
    case 'head':
      return `angle=${p.angle ?? '?'}°`
    case 'follow_back':
      return `${p.start_name ?? '?'}→${p.target_name ?? '?'}${p.get_pallet ? ' +取货' : ''}`
    case 'charge':
      return `goal=${p.goal ?? 'auto'}`
    case 'drive':
      return `${p.duration_s ?? '?'}s`
    case 'get_pallet':
      return '自动识别取货'
    default:
      return ''
  }
})
</script>

<style scoped>
.task-node {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: #fff;
  border: 1px solid #d1d5db;
  border-radius: 8px;
  min-width: 140px;
  font-size: 12px;
  position: relative;
}
.ring {
  position: absolute;
  inset: -3px;
  border-radius: 10px;
  border: 2px solid transparent;
  pointer-events: none;
}
.st-pending .ring { border-color: #9ca3af; }
.st-running .ring {
  border-color: #2563eb;
  animation: pulse 1.2s ease-in-out infinite;
}
.st-succeeded .ring { border-color: #16a34a; }
.st-failed .ring { border-color: #dc2626; }
.st-skipped .ring { border-color: #e5e7eb; }
.st-paused .ring { border-color: #f59e0b; }
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.35; }
}
.icon {
  font-size: 18px;
  color: #374151;
}
.label {
  font-weight: 600;
  color: #111827;
}
.summary {
  color: #6b7280;
  font-size: 11px;
}
</style>
