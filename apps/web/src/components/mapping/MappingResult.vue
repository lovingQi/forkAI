<template>
  <div class="result">
    <h2>{{ name }}</h2>
    <img v-if="src" :src="src" alt="地图预览" />
    <p>{{ createdAt }}</p>
    <p v-if="engineVersion">引擎 {{ engineVersion }}</p>
    <button type="button" @click="emit('export')">导出 ZIP</button>
    <button type="button" @click="emit('home')">返回建图首页</button>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { http } from '@/api/http'

const props = defineProps<{ projectId: string; name: string; createdAt?: string; engineVersion?: string }>()
const emit = defineEmits<{ export: []; home: [] }>()
const src = ref('')

async function loadPreview(projectId: string) {
  if (src.value) URL.revokeObjectURL(src.value)
  src.value = ''
  try {
    const response = await http.get(`/mapping/maps/${projectId}/preview.png`, { responseType: 'blob' })
    src.value = URL.createObjectURL(response.data)
  } catch {
    src.value = ''
  }
}
watch(() => props.projectId, (projectId) => { void loadPreview(projectId) }, { immediate: true })
onBeforeUnmount(() => { if (src.value) URL.revokeObjectURL(src.value) })
</script>

<style scoped>
.result { padding: 16px; display: flex; flex-direction: column; gap: 12px; }
img { max-width: 100%; background: #101712; }
button { width: fit-content; background: #101712; color: inherit; border: 1px solid var(--hud-line, rgba(182, 255, 59, 0.22)); min-height: 36px; }
</style>
