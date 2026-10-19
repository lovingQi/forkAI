<template>
  <div class="task-node" :class="`st-${data.status || 'pending'}`">
    <Handle type="target" :position="Position.Left" />
    <div class="shell">
      <div class="inner">
        <el-icon class="icon"><component :is="iconName" /></el-icon>
        <div class="meta">
          <div class="label">{{ data.label }}</div>
          <div class="summary">{{ summary }}</div>
        </div>
        <span class="badge">{{ badge }}</span>
      </div>
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

const badge = computed(() => {
  const map: Record<string, string> = {
    pending: 'QUEUED',
    running: 'RUNNING',
    paused: 'PAUSED',
    succeeded: 'DONE',
    failed: 'FAILED',
    skipped: 'SKIPPED'
  }
  return map[props.data.status || 'pending'] || 'QUEUED'
})

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
  position: relative;
  min-width: 168px;
  font-size: 12px;
}
.shell, .inner {
  clip-path: polygon(0 0, calc(100% - 10px) 0, 100% 10px, 100% 100%, 10px 100%, 0 calc(100% - 10px));
}
.shell { background: #4b5563; padding: 1px; }
.inner {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  background: rgba(8, 12, 9, 0.96);
}
.st-pending .shell { background: #4b5563; }
.st-running .shell {
  background: #b6ff3b;
  box-shadow: 0 0 14px rgba(182, 255, 59, 0.65);
  animation: pulse 1.2s ease-in-out infinite;
}
.st-succeeded .shell { background: #7ee787; }
.st-failed .shell { background: #ff4d4f; }
.st-skipped .shell { background: #4b5563; }
.st-paused .shell { background: #ffc53d; }
@keyframes pulse {
  0%, 100% { box-shadow: 0 0 8px rgba(182, 255, 59, 0.35); }
  50% { box-shadow: 0 0 18px rgba(182, 255, 59, 0.85); }
}
.icon { font-size: 18px; color: #b6ff3b; }
.meta { flex: 1; min-width: 0; }
.label { font-weight: 700; color: #e8f5e1; }
.summary { color: #7f8f84; font-size: 11px; font-family: Consolas, monospace; }
.badge {
  font-family: Consolas, monospace;
  font-size: 9px;
  letter-spacing: 0.06em;
  color: #b6ff3b;
}
</style>
