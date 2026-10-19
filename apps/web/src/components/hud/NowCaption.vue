<template>
  <div v-if="entry" class="caption" :key="entry.id">
    <div class="msg">{{ entry.msg }}</div>
    <div class="en">{{ meta.id }} · {{ meta.en }}</div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { catMeta } from '@/api/trace'
import { useTraceStore } from '@/stores/trace'

const trace = useTraceStore()
const entry = computed(() => trace.caption)
const meta = computed(() => catMeta(entry.value?.cat || ''))
</script>

<style scoped>
.caption {
  background: rgba(5, 8, 6, 0.72);
  border: 1px solid var(--hud-line);
  padding: 8px 14px;
  backdrop-filter: blur(6px);
  animation: fade 0.35s ease;
}
.msg { font-size: 26px; font-weight: 700; line-height: 1.25; }
.en {
  margin-top: 2px;
  font-family: var(--hud-mono);
  font-size: 12px;
  letter-spacing: 0.08em;
  color: var(--hud-muted);
}
@keyframes fade {
  from { opacity: 0; transform: translateY(6px); }
  to { opacity: 1; transform: none; }
}
</style>
