<template>
  <div class="result">
    <h2>{{ name }}</h2>
    <img v-if="projectId" :src="url" alt="地图预览" />
    <p>{{ createdAt }}</p>
    <p v-if="engineVersion">引擎 {{ engineVersion }}</p>
    <button type="button" @click="emit('export')">导出 ZIP</button>
    <button type="button" @click="emit('home')">返回建图首页</button>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { mappingPreviewUrl } from '@/api/mapping'

const props = defineProps<{ projectId: string; name: string; createdAt?: string; engineVersion?: string }>()
const emit = defineEmits<{ export: []; home: [] }>()
const url = computed(() => mappingPreviewUrl(props.projectId))
</script>

<style scoped>
.result { padding: 16px; display: flex; flex-direction: column; gap: 12px; }
img { max-width: 100%; background: #101712; }
button { width: fit-content; background: #101712; color: inherit; border: 1px solid var(--hud-line, rgba(182, 255, 59, 0.22)); min-height: 36px; }
</style>
