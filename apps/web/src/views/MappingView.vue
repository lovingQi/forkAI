<template>
  <div class="mapping">
    <MappingHome
      v-if="step === 'home'"
      :capabilities="store.status?.capabilities"
      :maps="store.maps"
      @enter="onEnter"
      @export="onExport"
      @remove="onRemove"
    />
    <MappingPrepare
      v-else-if="step === 'prepare'"
      :profiles="store.profiles"
      :dimension="prepareDimension"
      @back="step = 'home'"
      @start="onStart"
    />
    <template v-else-if="step === 'running' && store.task">
      <LiveMap3D v-if="store.task?.dimension === '3d'" :points="cloudPoints" />
      <LiveMap2D v-else ref="live" :png="bitmap" :meta="meta" :trajectory="trajectory" :pose="pose" :scan="scan" />
      <MappingRunPanel :task="store.task" @finish="onFinish" @cancel="onCancel" />
    </template>
    <MappingResult
      v-else-if="step === 'result' && result"
      :project-id="result.projectId"
      :name="result.name"
      :created-at="result.createdAt"
      :engine-version="result.engineVersion"
      @export="onExport(result)"
      @home="step = 'home'"
    />
    <p v-else class="wait">建图服务未连接</p>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import MappingHome from '@/components/mapping/MappingHome.vue'
import MappingPrepare from '@/components/mapping/MappingPrepare.vue'
import MappingRunPanel from '@/components/mapping/MappingRunPanel.vue'
import MappingResult from '@/components/mapping/MappingResult.vue'
import LiveMap2D from '@/components/mapping/LiveMap2D.vue'
import LiveMap3D from '@/components/mapping/LiveMap3D.vue'
import { consoleDriveStop } from '@/api/console'
import { cancelMapping, deleteMappingMap, downloadMappingZip, finishMapping, openMappingLive, startMapping } from '@/api/mapping'
import { useMappingStore } from '@/stores/mapping'
import { useSessionStore } from '@/stores/session'

const store = useMappingStore()
const session = useSessionStore()
const step = ref<'home' | 'prepare' | 'running' | 'result'>('home')
const bitmap = ref<ImageBitmap | null>(null)
const meta = ref<any>(null)
const trajectory = ref<number[][]>([])
const pose = ref<any>(null)
const scan = ref<number[][]>([])
const cloudPoints = ref<Float32Array>(new Float32Array())
const result = ref<any>(null)
const live = ref<{ fit: () => void } | null>(null)
let closeLive: (() => void) | null = null
let pollTimer: number | null = null
const generation = { value: 0 }
let cursor = 0

const prepareDimension = ref<'2d' | '3d'>('2d')
function onEnter(payload: { id: string; dimension: '2d' | '3d' }) {
  if (payload.id !== 'online') return
  prepareDimension.value = payload.dimension
  step.value = 'prepare'
}
async function onStart(draft: Record<string, unknown>) {
  try {
    cloudPoints.value = new Float32Array()
    bitmap.value = null
    meta.value = null
    trajectory.value = []
    pose.value = null
    scan.value = []
    store.task = await startMapping(draft)
    step.value = 'running'
    bindLive()
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.error?.message || error?.response?.data?.error || '无法开始建图')
  }
}
function bindLive() {
  closeLive?.()
  if (!store.task) return
  closeLive = openMappingLive(store.task.id, {
    onJson(msg) {
      if (msg.type === 'pose') pose.value = msg
      if (msg.type === 'trajectory') trajectory.value = msg.points || []
      if (msg.type === 'scan') scan.value = msg.points || []
      if (msg.type === 'state' && msg.task) store.task = msg.task
      if (store.task && ['completed', 'failed', 'cancelled'].includes(store.task.status)) void finishStep()
    },
    onMap(buffer) {
      const view = new DataView(buffer)
      const headLen = view.getUint32(0, true)
      const head = JSON.parse(new TextDecoder().decode(new Uint8Array(buffer, 4, headLen)))
      if (head.kind === 'cloud') {
        const bytes = new Uint8Array(buffer, 4 + headLen)
        const copy = new Uint8Array(bytes.byteLength)
        copy.set(bytes)
        cloudPoints.value = new Float32Array(copy.buffer)
        return
      }
      const png = buffer.slice(4 + headLen)
      createImageBitmap(new Blob([png], { type: 'image/png' })).then((image) => {
        bitmap.value = image
        meta.value = head
        live.value?.fit()
      }).catch(() => {})
    },
    onStatus(ok) { store.connected = ok }
  })
}
async function finishStep() {
  const task = store.task
  if (!task) return
  if (task.status === 'completed' && task.projectId) {
    const projectId = task.projectId
    const name = task.name
    await store.refresh()
    const map = store.maps.find((item) => item.projectId === projectId)
    result.value = map || { projectId, name, createdAt: task.createdAt }
    step.value = 'result'
  } else if (task.status === 'failed') {
    ElMessage.error(task.error?.message || '建图失败')
    step.value = 'home'
  } else if (task.status === 'cancelled') {
    step.value = 'home'
  }
}
async function onFinish() {
  if (!store.task) return
  try { await finishMapping(store.task.id) } catch (error: any) {
    ElMessage.error(error?.response?.data?.error?.message || '保存失败')
  }
}
async function onCancel() {
  if (!store.task) return
  try {
    await ElMessageBox.confirm('取消后不保留这次地图。', '取消建图', {
      type: 'warning',
      confirmButtonText: '确定',
      cancelButtonText: '取消'
    })
    await cancelMapping(store.task.id)
  } catch (error: any) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(error?.response?.data?.error?.message || '取消失败')
  }
}
async function onExport(map: any) {
  try { await downloadMappingZip(map.projectId, map.name) } catch { ElMessage.error('导出失败') }
}
async function onRemove(map: any) {
  if (!session.iAmHolder) {
    ElMessage.warning('删除地图需要现场控制权')
    return
  }
  try {
    await ElMessageBox.confirm(`删除地图「${map.name}」？`, '删除地图', {
      type: 'warning',
      confirmButtonText: '确定',
      cancelButtonText: '取消'
    })
    await deleteMappingMap(map.projectId)
    await store.refresh()
  } catch (error: any) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error('删除失败')
  }
}
async function loop() {
  const token = ++generation.value
  while (generation.value === token) {
    if (store.task && !['completed', 'failed', 'cancelled', 'interrupted'].includes(store.task.status)) {
      cursor = await store.pollActivity(cursor, generation, token)
      if (store.task && ['completed', 'failed', 'cancelled'].includes(store.task.status)) await finishStep()
    }
    await new Promise((resolve) => { pollTimer = window.setTimeout(resolve, store.task ? 1000 : 2000) })
  }
}
onMounted(async () => {
  try {
    await store.refresh()
    if (store.task && !['completed', 'failed', 'cancelled', 'interrupted'].includes(store.task.status)) {
      step.value = 'running'
      bindLive()
    }
  } catch {
    store.status = null
  }
  void loop()
})
onBeforeUnmount(() => {
  generation.value++
  if (pollTimer !== null) window.clearTimeout(pollTimer)
  closeLive?.()
  consoleDriveStop().catch(() => {})
})
</script>

<style scoped>
.mapping { height: 100%; min-height: 0; display: grid; grid-template-columns: 1fr 420px; }
.mapping > :only-child { grid-column: 1 / -1; }
.wait { padding: 24px; color: var(--hud-muted, #7f8f84); }
@media (max-width: 900px) {
  .mapping { grid-template-columns: 1fr; grid-template-rows: 240px 1fr; }
}
</style>
