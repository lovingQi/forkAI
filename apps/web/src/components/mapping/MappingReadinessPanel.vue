<template>
  <section class="ready">
    <header>
      <strong>{{ title }}</strong>
      <button type="button" :disabled="busy" @click="emit('check')">重新检查</button>
    </header>
    <p v-if="!checks.length && !busy" class="muted">还没有检查</p>
    <ul v-else-if="!busy">
      <li v-for="item in checks" :key="item.id" :class="item.status">
        <span>{{ item.label }}</span>
        <span>{{ item.status === 'ready' ? '通过' : '未通过' }}</span>
      </li>
    </ul>
    <button v-if="issues.length" type="button" class="link" @click="open = true">诊断信息</button>
    <div v-if="open" class="dialog" @click.self="open = false">
      <div>
        <h3>诊断信息</h3>
        <p v-for="(issue, index) in issues" :key="index">{{ issue.message }}</p>
        <button type="button" @click="open = false">关闭</button>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'

const props = defineProps<{
  busy: boolean
  checks: { id: string; status: string; label: string; detail: string }[]
  issues: { message: string }[]
}>()
const emit = defineEmits<{ check: [] }>()
const open = ref(false)
const title = computed(() => (props.busy ? '检查中' : props.issues.length ? '未通过' : props.checks.length ? '已就绪' : '待检查'))
watch(() => props.busy, (busy) => {
  if (busy) open.value = false
})
</script>

<style scoped>
.ready { border: 1px solid var(--hud-line, rgba(182, 255, 59, 0.22)); padding: 12px; }
header { display: flex; justify-content: space-between; align-items: center; }
ul { list-style: none; padding: 0; margin: 8px 0; }
li { display: flex; justify-content: space-between; font-size: 13px; padding: 4px 0; }
li.error { color: var(--hud-danger, #ff4d4f); }
.muted, .link { color: var(--hud-muted, #7f8f84); font-size: 12px; }
button { background: transparent; color: inherit; border: 1px solid var(--hud-line, rgba(182, 255, 59, 0.22)); }
.dialog { position: fixed; inset: 0; background: rgba(0, 0, 0, 0.55); display: grid; place-items: center; }
.dialog div { background: #101712; padding: 16px; width: min(420px, 90vw); }
</style>
