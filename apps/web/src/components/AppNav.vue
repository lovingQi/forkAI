<template>
  <nav class="app-nav" :class="{ bottom: bottom }" aria-label="页面">
    <a v-for="item in items" :key="item.to" :href="item.to" :class="{ on: route.path === item.path }">
      {{ item.label }}
    </a>
  </nav>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()
const bottom = ref(window.innerWidth < 900)

const items = [
  { label: '主页', to: '#/', path: '/' },
  { label: '建图', to: '#/mapping', path: '/mapping' },
  { label: '基本控制', to: '#/control', path: '/control' },
  { label: '参数', to: '#/params', path: '/params' },
  { label: '问题分析', to: '#/diagnosis', path: '/diagnosis' },
  { label: '任务流', to: '#/flow', path: '/flow' }
]

function onResize() {
  bottom.value = window.innerWidth < 900
}
onMounted(() => window.addEventListener('resize', onResize))
onBeforeUnmount(() => window.removeEventListener('resize', onResize))
</script>

<style scoped>
.app-nav {
  width: 168px;
  flex: none;
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 12px;
  border-right: 1px solid var(--hud-line, rgba(182, 255, 59, 0.22));
  background: rgba(5, 8, 6, 0.92);
}
.app-nav a {
  color: var(--hud-muted, #7f8f84);
  text-decoration: none;
  font-size: 14px;
  padding: 8px 10px;
  border: 1px solid transparent;
}
.app-nav a.on {
  color: var(--hud-accent, #b6ff3b);
  border-color: var(--hud-line-strong, rgba(182, 255, 59, 0.55));
}
.app-nav.bottom {
  width: auto;
  flex-direction: row;
  overflow-x: auto;
  border-right: 0;
  border-top: 1px solid var(--hud-line, rgba(182, 255, 59, 0.22));
  padding: 8px;
}
.app-nav.bottom a {
  white-space: nowrap;
}
</style>
