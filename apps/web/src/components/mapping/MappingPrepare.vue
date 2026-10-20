<template>
  <div class="prepare">
    <button type="button" @click="emit('back')">返回</button>
    <h2>准备在线建图</h2>
    <label>建图参数
      <select v-model="profileId">
        <option v-for="item in profiles" :key="item.id" :value="item.id">{{ item.name }}</option>
      </select>
    </label>
    <label v-for="(item, index) in ranges" :key="item.sensorId">激光 {{ item.sensorId }}
      <input v-model="item.topic" />
    </label>
    <label v-if="odometry">里程计
      <input v-model="odometry.topic" />
    </label>
    <details>
      <summary>配置版本</summary>
      <p>仅供排查问题时核对，无需在此配置。</p>
      <p v-if="robot">URDF {{ robot.urdfSha256 }}</p>
    </details>
    <label>地图名称
      <input v-model="name" maxlength="64" />
    </label>
    <MappingReadinessPanel :busy="checking" :checks="checks" :issues="issues" @check="runCheck" />
    <button type="button" class="go" :disabled="!canStart" @click="emit('start', draft())">开始建图</button>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import MappingReadinessPanel from '@/components/mapping/MappingReadinessPanel.vue'
import { mappingRobot, validateDraft } from '@/api/mapping'
import { useSessionStore } from '@/stores/session'

const props = defineProps<{ profiles: any[]; mode?: string }>()
const emit = defineEmits<{ back: []; start: [draft: Record<string, unknown>] }>()
const session = useSessionStore()
const profileId = ref(props.profiles[0]?.id || 'dual-range-odom-2d')
const ranges = ref<{ sensorId: string; topic: string }[]>([])
const odometry = ref<{ topic: string } | null>(null)
const name = ref('新地图')
const robot = ref<any>(null)
const checking = ref(false)
const checks = ref<any[]>([])
const issues = ref<any[]>([])
const ready = ref(false)

const profile = computed(() => props.profiles.find((item) => item.id === profileId.value))
watch(profile, (item) => {
  const inputs = item?.inputs || {}
  ranges.value = (inputs.ranges || [{ sensorId: 'front-laser', topic: '/front/scan' }]).map((row: any) => ({ ...row }))
  odometry.value = inputs.odometry ? { topic: inputs.odometry.topic } : null
  ready.value = false
  checks.value = []
  issues.value = []
}, { immediate: true })

function draft() {
  return {
    dimension: '2d',
    mode: props.mode === 'offline' ? 'offline' : 'online',
    name: name.value.trim(),
    source: props.mode === 'offline' ? { kind: 'recording', recordingId: name.value.trim() } : { kind: 'online' },
    objective: props.mode === 'extend' || props.mode === 'patch'
      ? { kind: props.mode === 'patch' ? 'update-region' : 'extend', baseProjectId: '', initialPose: { xMm: 0, yMm: 0, yawRad: 0 } }
      : { kind: 'create' },
    profileId: profileId.value,
    inputs: { ranges: ranges.value, odometry: odometry.value }
  }
}
async function runCheck() {
  checking.value = true
  checks.value = []
  issues.value = []
  ready.value = false
  try {
    const result = await validateDraft(draft())
    checks.value = result.checks
    issues.value = result.issues
    ready.value = result.status === 'ready'
  } catch (error: any) {
    issues.value = [{ message: error?.response?.data?.error?.message || '检查失败' }]
  } finally {
    checking.value = false
  }
}
const canStart = computed(() => ready.value && session.iAmHolder && !!name.value.trim())
onMounted(async () => {
  try { robot.value = await mappingRobot() } catch { /* 建图服务未启动时留空 */ }
})
</script>

<style scoped>
.prepare { padding: 16px; display: flex; flex-direction: column; gap: 12px; max-width: 640px; overflow: auto; height: 100%; }
label { display: flex; flex-direction: column; gap: 4px; font-size: 14px; }
input, select, button { background: #101712; color: var(--hud-text, #e8f5e1); border: 1px solid var(--hud-line, rgba(182, 255, 59, 0.22)); min-height: 32px; }
.go { min-height: 40px; }
.go:disabled { opacity: 0.4; }
details { color: var(--hud-muted, #7f8f84); font-size: 12px; }
</style>
