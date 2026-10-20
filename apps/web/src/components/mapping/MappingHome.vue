<template>
  <div class="home">
    <div class="tabs">
      <button type="button" :class="{ on: dimension === '2d' }" @click="dimension = '2d'">2D</button>
      <button type="button" :class="{ on: dimension === '3d' }" @click="dimension = '3d'">3D</button>
    </div>
    <div class="grid">
      <button
        v-for="item in entries"
        :key="item.id"
        type="button"
        class="card"
        :disabled="item.id !== 'online' || !item.enabled"
        @click="item.id === 'online' && item.enabled && emit('enter', { id: item.id, dimension })"
      >
        <strong>{{ item.title }}</strong>
        <span>{{ item.enabled ? item.group : '页面未完成' }}</span>
      </button>
    </div>
    <h2>已保存的地图</h2>
    <p v-if="!maps.length" class="empty">还没有地图</p>
    <ul>
      <li v-for="map in maps" :key="map.projectId">
        <span>{{ map.name }}</span>
        <span class="meta">{{ map.dimension === '3d' ? '3D' : '2D' }}</span>
        <span class="meta">{{ map.createdAt || '' }}</span>
        <button type="button" @click="emit('export', map)">导出 ZIP</button>
        <button type="button" @click="emit('remove', map)">删除</button>
      </li>
    </ul>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'

const props = defineProps<{ capabilities: any; maps: { projectId: string; name: string; createdAt: string; dimension?: string }[] }>()
const emit = defineEmits<{ enter: [payload: { id: string; dimension: '2d' | '3d' }]; export: [map: any]; remove: [map: any] }>()
const dimension = ref<'2d' | '3d'>('2d')
const entries = computed(() => {
  const cap = props.capabilities?.[dimension.value] || {}
  return [
    { id: 'offline', title: '离线建图', group: '新建', enabled: false },
    { id: 'online', title: '在线建图', group: '新建', enabled: !!cap.online },
    { id: 'extend', title: '扩建', group: '增量', enabled: false },
    { id: 'patch', title: '更新局部', group: '增量', enabled: false }
  ]
})
const maps = computed(() => props.maps || [])
</script>

<style scoped>
.home { padding: 16px; overflow: auto; height: 100%; }
.tabs { display: flex; gap: 8px; margin-bottom: 16px; }
.tabs button, .card, li button {
  border: 1px solid var(--hud-line, rgba(182, 255, 59, 0.22));
  background: #101712;
  color: var(--hud-text, #e8f5e1);
}
.tabs button { width: 60px; height: 36px; }
.tabs button.on { border-color: var(--hud-accent, #b6ff3b); color: var(--hud-accent, #b6ff3b); }
.grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; max-width: 1000px; }
.card { min-height: 96px; text-align: left; padding: 16px; display: flex; flex-direction: column; gap: 8px; }
.card:disabled { opacity: 0.4; }
.card span, .meta, .empty { color: var(--hud-muted, #7f8f84); font-size: 12px; }
ul { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 8px; }
li { display: flex; gap: 8px; align-items: center; }
li span:first-child { flex: 1; }
h2 { font-size: 16px; margin: 24px 0 8px; }
</style>
