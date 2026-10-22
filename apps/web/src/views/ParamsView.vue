<template>
  <div class="params">
    <header>
      <h1>车端参数</h1>
      <div>
        <el-button :type="editing ? 'primary' : 'default'" :disabled="!session.iAmHolder" @click="editing = true">修改</el-button>
        <el-button @click="backupOpen = true">备份与恢复</el-button>
        <el-button @click="logOpen = true">修改记录</el-button>
        <el-button @click="rerun">重新运行初始向导</el-button>
      </div>
    </header>
    <div class="layout">
    <el-collapse v-model="open" class="list">
      <el-collapse-item v-for="name in editableNames" :key="name" :name="name" :title="name">
        <ParamEditor
          :key="`${name}-${editing && session.iAmHolder}`"
          :section="name"
          :items="sections[name].items"
          :editing="editing && session.iAmHolder"
          @submit="(c) => save(name, c)"
          @draft="(c) => onDraft(name, c)"
          @focus="(key) => (focus = { section: name, key })"
        />
      </el-collapse-item>
      <el-collapse-item name="__advanced" title="高级（只读）">
        <el-collapse>
          <el-collapse-item v-for="name in readonlyNames" :key="name" :title="name">
            <ParamEditor :section="name" :items="sections[name].items" :editing="false" />
          </el-collapse-item>
        </el-collapse>
      </el-collapse-item>
    </el-collapse>
    <VehicleParamView class="preview" :sections="sections" :drafts="drafts" :focus="focus" />
    </div>

    <el-drawer v-model="backupOpen" title="备份与恢复" size="420px">
      <div v-for="item in backups" :key="item.id" class="backup">
        <span>{{ new Date(item.ts).toLocaleString() }}</span>
        <el-button size="small" @click="restore(item.id)">恢复</el-button>
      </div>
    </el-drawer>
    <el-drawer v-model="logOpen" title="修改记录" size="480px">
      <pre v-for="(row, i) in logs" :key="i" class="log">{{ JSON.stringify(row, null, 2) }}</pre>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import ParamEditor from '@/components/ParamEditor.vue'
import VehicleParamView from '@/components/vehicle/VehicleParamView.vue'
import {
  applyConsoleParams,
  consoleErrorMessage,
  getConsoleParams,
  getParamBackups,
  getParamLog,
  resetSetup,
  restoreParamBackup,
  type ParamSection
} from '@/api/console'
import { useConsoleStore } from '@/stores/console'
import { useSessionStore } from '@/stores/session'

const session = useSessionStore()
const consoleStore = useConsoleStore()
const router = useRouter()
const sections = ref<Record<string, ParamSection>>({})
const editing = ref(false)
const open = ref<string[]>([])
const backupOpen = ref(false)
const logOpen = ref(false)
const drafts = ref<Record<string, Record<string, any>>>({})
const focus = ref<{ section: string; key: string } | null>(null)
const backups = ref<{ id: string; ts: number; clientId: string }[]>([])
const logs = ref<Record<string, any>[]>([])
const editableNames = computed(() => Object.keys(sections.value).filter((name) => sections.value[name].editable))
const readonlyNames = computed(() => Object.keys(sections.value).filter((name) => !sections.value[name].editable))

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
async function loadBackups() {
  backups.value = (await getParamBackups()).backups || []
}
async function restore(id: string) {
  try {
    await ElMessageBox.confirm('用这份备份覆盖车端参数？', '恢复备份', {
      type: 'warning',
      confirmButtonText: '确定',
      cancelButtonText: '取消'
    })
    await restoreParamBackup(id, true)
    ElMessage.success('已恢复')
    await load()
  } catch (e: any) {
    if (e === 'cancel' || e === 'close') return
    ElMessage.error(consoleErrorMessage(e))
  }
}
async function rerun() {
  try {
    await resetSetup()
    consoleStore.setupCompleted = false
    await router.push(consoleStore.isKiosk ? '/welcome' : '/setup')
  } catch (e: any) {
    ElMessage.error(consoleErrorMessage(e))
  }
}
watch(open, (names) => {
  const name = names[names.length - 1]
  if (name && name !== '__advanced') focus.value = { section: name, key: '' }
})
watch(backupOpen, (v) => {
  if (v) loadBackups().catch((e) => ElMessage.error(consoleErrorMessage(e)))
})
watch(logOpen, (v) => {
  if (v) getParamLog().then((d) => (logs.value = d.log || [])).catch((e) => ElMessage.error(consoleErrorMessage(e)))
})
onMounted(() => {
  load().catch((e) => ElMessage.error(consoleErrorMessage(e)))
})
</script>

<style scoped>
.params { height: 100%; overflow: auto; padding: 16px; }
.layout { display: grid; grid-template-columns: minmax(320px, 1fr) minmax(360px, 1fr); gap: 16px; align-items: start; }
.preview { position: sticky; top: 0; height: 560px; }
@media (max-width: 900px) {
  .layout { grid-template-columns: 1fr; }
  .preview { position: static; height: 320px; }
}
header { display: flex; justify-content: space-between; gap: 12px; align-items: center; margin-bottom: 12px; }
header h1 { margin: 0; font-size: 18px; }
header div { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.backup { display: flex; justify-content: space-between; gap: 8px; padding: 8px 0; border-bottom: 1px solid var(--hud-line, rgba(182, 255, 59, 0.22)); }
.log { white-space: pre-wrap; font-size: 12px; }
</style>
