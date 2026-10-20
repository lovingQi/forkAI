<template>
  <div class="control">
    <div class="map">
      <CanvasView :show-map="true" :show-avoid-box="true" />
    </div>
    <aside class="side">
      <div class="status">
        <span>速度 {{ speed }} mm/s</span>
        <span>{{ consoleStore.status?.avoidEnabled === false ? '避障关' : '避障开' }}</span>
        <span>{{ consoleStore.status?.forkBusy ? '货叉动作中' : '货叉空闲' }}</span>
        <span>{{ consoleStore.status?.estopLatched ? '急停锁定' : '未急停' }}</span>
      </div>
      <div class="body">
        <template v-if="session.iAmHolder">
          <DrivePad :disabled="padOff" :trans="speed" @stop="onSoftStop" />
          <div class="speed">
            <span>速度</span>
            <el-slider v-model="speed" :min="limits.min" :max="speedMax" :step="limits.step" />
          </div>
          <div class="avoid">
            <span>避障</span>
            <el-switch :model-value="avoidOn" :disabled="!!consoleStore.status?.chassisMoving" @change="onAvoid" />
            <span class="hint">关闭需确认，并限制在 {{ limits.unsafeMax }} mm/s。松开或离开页面后恢复。</span>
          </div>
          <ForkPanel :disabled="!!consoleStore.status?.chassisMoving || !!consoleStore.status?.estopLatched" />
          <el-button @click="onSoftStop">停止</el-button>
        </template>
        <div v-else class="mask">
          <p>当前没有控制权</p>
          <el-button type="primary" @click="openSite">现场解锁</el-button>
        </div>
      </div>
    </aside>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import CanvasView from '@/components/CanvasView.vue'
import DrivePad from '@/components/control/DrivePad.vue'
import ForkPanel from '@/components/control/ForkPanel.vue'
import { consoleAvoid, consoleDriveStop, consoleErrorMessage, consoleStop } from '@/api/console'
import { useConsoleStore } from '@/stores/console'
import { useSessionStore } from '@/stores/session'

const session = useSessionStore()
const consoleStore = useConsoleStore()
const speed = ref(500)
const limits = computed(() => consoleStore.status?.limits.trans || { min: 100, max: 1000, step: 50, default: 500, unsafeMax: 300 })
const avoidOn = computed(() => consoleStore.status?.avoidEnabled !== false)
const speedMax = computed(() => (avoidOn.value ? limits.value.max : limits.value.unsafeMax))
const padOff = computed(() => !!consoleStore.status?.estopLatched || !!consoleStore.status?.forkBusy)
watch(speedMax, (max) => {
  if (speed.value > max) speed.value = max
})

function openSite() {
  window.dispatchEvent(new Event('forkai-open-site'))
}
async function onAvoid(enabled: boolean) {
  try {
    if (!enabled) {
      await ElMessageBox.confirm('关闭避障后仅允许低速，松手即恢复。确认关闭？', '关闭避障', {
        type: 'warning',
        confirmButtonText: '确定',
        cancelButtonText: '取消'
      })
    }
    await consoleAvoid(enabled, !enabled)
    await consoleStore.loadStatus()
  } catch (e: any) {
    if (e === 'cancel' || e === 'close') return
    ElMessage.error(consoleErrorMessage(e))
  }
}
async function onSoftStop() {
  try {
    await consoleStop()
    await consoleStore.loadStatus()
  } catch (e: any) {
    ElMessage.error(consoleErrorMessage(e))
  }
}
onMounted(async () => {
  try {
    const status = await consoleStore.loadStatus()
    speed.value = status.limits.trans.default
  } catch {
    /* 状态稍后再由事件补上 */
  }
})
onBeforeUnmount(() => {
  consoleDriveStop().catch(() => {})
})
</script>

<style scoped>
.control { height: 100%; display: grid; grid-template-columns: 1fr 420px; min-height: 0; }
.map { min-width: 0; min-height: 0; position: relative; }
.side { border-left: 1px solid var(--hud-line, rgba(182, 255, 59, 0.22)); display: flex; flex-direction: column; min-height: 0; }
.status { display: flex; flex-wrap: wrap; gap: 8px; padding: 10px; font-size: 12px; color: var(--hud-muted, #7f8f84); }
.body { position: relative; flex: 1; overflow: auto; padding: 12px; display: flex; flex-direction: column; gap: 14px; }
.speed, .avoid { display: grid; grid-template-columns: 48px 1fr; gap: 8px; align-items: center; }
.hint { grid-column: 1 / -1; color: var(--hud-muted, #7f8f84); font-size: 12px; }
.mask { position: absolute; inset: 0; display: grid; place-items: center; align-content: center; gap: 12px; background: rgba(5, 8, 6, 0.72); }
@media (max-width: 900px) {
  .control { grid-template-columns: 1fr; grid-template-rows: 240px 1fr; }
}
</style>
