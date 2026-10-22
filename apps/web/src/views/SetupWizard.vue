<template>
  <div class="wizard">
    <el-steps :active="step" finish-status="success" align-center>
      <el-step title="车辆信息" />
      <el-step title="避障参数" />
      <el-step title="传感器" />
      <el-step title="完成" />
    </el-steps>
    <section v-if="step === 0" class="panel">
      <p>名称：{{ robot.name || '—' }}</p>
      <p>车型：{{ robot.robotType || '—' }}</p>
      <p>地址：{{ robot.ip || '—' }}</p>
      <p>电量：{{ robot.battery }}%</p>
      <p>地图：{{ robot.mapName || '—' }}</p>
    </section>
    <section v-else-if="step === 1" class="panel">
      <p class="hint">默认只读。要修改需持有控制权，保存前会对比、备份并写入日志。</p>
      <el-button :type="editing ? 'primary' : 'default'" :disabled="!session.iAmHolder" @click="editing = !editing">修改</el-button>
      <div class="split">
      <ParamEditor
        v-if="sections.avoid"
        section="avoid"
        :items="sections.avoid.items"
        :editing="editing && session.iAmHolder"
        @submit="(c) => save('avoid', c)"
        @draft="(c) => onDraft('avoid', c)"
        @focus="(key) => (focus = { section: 'avoid', key })"
      />
      <VehicleParamView :sections="sections" :drafts="drafts" :focus="focus" />
      </div>
    </section>
    <section v-else-if="step === 2" class="panel">
      <div class="split">
      <el-collapse v-model="openSensors" class="sensors">
        <el-collapse-item v-for="name in sensorSections" :key="name" :name="name" :title="name">
          <ParamEditor
            :section="name"
            :items="sections[name].items"
            :editing="editing && session.iAmHolder"
            @submit="(c) => save(name, c)"
            @draft="(c) => onDraft(name, c)"
            @focus="(key) => (focus = { section: name, key })"
          />
        </el-collapse-item>
      </el-collapse>
      <VehicleParamView :sections="sections" :drafts="drafts" :focus="focus" />
      </div>
    </section>
    <section v-else class="panel">
      <p>确认后进入系统。之后可在参数页重新运行本向导。</p>
      <el-button type="primary" @click="finish">完成并进入系统</el-button>
    </section>
    <footer>
      <el-button :disabled="step === 0" @click="step -= 1">上一步</el-button>
      <el-button v-if="step < 3" type="primary" @click="step += 1">下一步</el-button>
    </footer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import ParamEditor from '@/components/ParamEditor.vue'
import VehicleParamView from '@/components/vehicle/VehicleParamView.vue'
import {
  applyConsoleParams,
  completeSetup,
  consoleErrorMessage,
  getConsoleParams,
  type ParamSection
} from '@/api/console'
import { useConsoleStore } from '@/stores/console'
import { useRobotStore } from '@/stores/robot'
import { useSessionStore } from '@/stores/session'

const step = ref(0)
const editing = ref(false)
const drafts = ref<Record<string, Record<string, any>>>({})
const focus = ref<{ section: string; key: string } | null>(null)
const openSensors = ref<string[]>([])
const sections = ref<Record<string, ParamSection>>({})
const robot = useRobotStore()
const session = useSessionStore()
const consoleStore = useConsoleStore()
const router = useRouter()
const sensorSections = computed(() =>
  Object.keys(sections.value).filter((name) => name === 'robot' || name.startsWith('laser'))
)

function onDraft(section: string, changes: Record<string, any>) {
  if (Object.keys(changes).length === 0) delete drafts.value[section]
  else drafts.value[section] = changes
}
async function load() {
  const data = await getConsoleParams()
  sections.value = data.sections || {}
  drafts.value = {}
}
async function save(section: string, changes: Record<string, any>) {
  try {
    await applyConsoleParams({ [section]: changes }, true)
    ElMessage.success('已保存')
    await load()
  } catch (e: any) {
    ElMessage.error(consoleErrorMessage(e))
  }
}
async function finish() {
  try {
    await completeSetup()
    consoleStore.setupCompleted = true
    await router.replace('/')
  } catch (e: any) {
    ElMessage.error(consoleErrorMessage(e))
  }
}
watch(openSensors, (names) => {
  const name = names[names.length - 1]
  if (name) focus.value = { section: name, key: '' }
})
onMounted(() => {
  robot.loadInitial().catch(() => {})
  load().catch((e) => ElMessage.error(consoleErrorMessage(e)))
})
</script>

<style scoped>
.wizard { height: 100%; overflow: auto; padding: 16px; display: flex; flex-direction: column; gap: 16px; }
.panel { flex: 1; min-height: 0; overflow: auto; }
.split { display: grid; grid-template-columns: minmax(280px, 1fr) minmax(320px, 1fr); gap: 16px; align-items: start; }
.split > :last-child { height: 420px; }
@media (max-width: 900px) {
  .split { grid-template-columns: 1fr; }
  .split > :last-child { height: 320px; }
}
.hint { color: var(--hud-muted, #7f8f84); }
footer { display: flex; justify-content: flex-end; gap: 8px; }
</style>
