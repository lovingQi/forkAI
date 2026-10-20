<template>
  <aside class="run">
    <p class="status">{{ label }}</p>
    <p v-if="task.stopReason && task.status === 'ready-to-save'" class="warn">输入中断：{{ task.stopReason }}。可以保存已有部分。</p>
    <p class="stats">接收 {{ task.stats?.received || 0 }} · 已提交 {{ task.stats?.submitted || 0 }} · 丢弃 {{ task.stats?.rejected || 0 }}</p>
    <div class="actions">
      <button type="button" :disabled="!canFinish" @click="emit('finish')">完成并保存</button>
      <button type="button" @click="emit('cancel')">取消建图</button>
    </div>
    <div v-if="session.iAmHolder" class="jog">
      <DrivePad :disabled="padOff" :trans="speed" />
      <div class="speed"><span>速度</span><el-slider v-model="speed" :min="limits.min" :max="limits.max" :step="limits.step" /></div>
    </div>
    <div v-else class="mask">
      <p>当前没有控制权</p>
      <button type="button" @click="openSite">现场解锁</button>
    </div>
  </aside>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import DrivePad from '@/components/control/DrivePad.vue'
import { useConsoleStore } from '@/stores/console'
import { useSessionStore } from '@/stores/session'

const props = defineProps<{ task: any }>()
const emit = defineEmits<{ finish: []; cancel: [] }>()
const session = useSessionStore()
const consoleStore = useConsoleStore()
const speed = ref(500)
const limits = computed(() => consoleStore.status?.limits.trans || { min: 100, max: 1000, step: 50, default: 500 })
const padOff = computed(() => !!consoleStore.status?.estopLatched || !!consoleStore.status?.forkBusy)
const canFinish = computed(() => ['running', 'ready-to-save'].includes(props.task.status) && session.iAmHolder)
const label = computed(() => ({
  starting: '正在启动',
  running: '建图中',
  'ready-to-save': '待保存',
  finishing: '正在结束',
  exporting: '正在保存',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
  interrupted: '已中断'
} as Record<string, string>)[props.task.status] || props.task.status)
function openSite() {
  window.dispatchEvent(new Event('forkai-open-site'))
}
onMounted(async () => {
  try {
    const status = await consoleStore.loadStatus()
    speed.value = status.limits.trans.default
  } catch { /* 状态稍后由事件补上 */ }
})
</script>

<style scoped>
.run { padding: 12px; display: flex; flex-direction: column; gap: 12px; overflow: auto; }
.status { margin: 0; font-size: 18px; }
.stats, .warn { margin: 0; color: var(--hud-muted, #7f8f84); font-size: 13px; }
.warn { color: var(--hud-danger, #ff4d4f); }
.actions, .speed { display: flex; gap: 8px; align-items: center; }
button { background: #101712; color: var(--hud-text, #e8f5e1); border: 1px solid var(--hud-line, rgba(182, 255, 59, 0.22)); min-height: 36px; }
.speed { display: grid; grid-template-columns: 48px 1fr; }
.mask { display: grid; gap: 8px; justify-items: start; }
</style>
