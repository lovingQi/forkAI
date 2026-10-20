<template>
  <div class="gate">
    <div class="gate-card">
      <div class="term-bar">FORKAI // VOICE CONTROL TERMINAL · V2</div>
      <div class="boot">
        <div class="boot-line">{{ coreLine }}</div>
        <div class="boot-line d2">{{ pairLine }}</div>
        <div class="boot-line d3">[ .. ] 等待操作员确认配对<span class="blink">_</span></div>
      </div>
      <div class="pair-row">
        <el-button @click="genCode">显示/刷新配对码</el-button>
        <strong v-if="session.pairCode" class="code">{{ session.pairCode }}</strong>
      </div>
      <el-input v-model="inputCode" placeholder="输入 6 位配对码" maxlength="6" />
      <el-button type="primary" class="mt" @click="doPair">确认配对</el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { getHealth } from '@/api/http'
import { useConsoleStore } from '@/stores/console'
import { useRobotStore } from '@/stores/robot'
import { useSessionStore } from '@/stores/session'

const session = useSessionStore()
const consoleStore = useConsoleStore()
const robot = useRobotStore()
const router = useRouter()
const coreLine = ref('[ .. ] 正在检查 core 服务')
const inputCode = ref('')
const pairLine = computed(() => (session.pairCode ? '[ OK ] 已获取配对码' : '[ .. ] 等待配对码'))

async function bootCheck() {
  try {
    const h = await getHealth()
    coreLine.value = `[ OK ] core 服务在线 · vehicle=${h?.vehicleId || ''}`
  } catch {
    coreLine.value = '[FAIL] core 服务不可达'
  }
}
async function genCode() {
  try {
    await session.refreshPairCode()
  } catch (e: any) {
    ElMessage.error(e?.message || '无法生成配对码')
  }
}
async function doPair() {
  try {
    await session.doConfirmPair(inputCode.value.trim())
    await robot.loadInitial()
    robot.connectWs()
    await consoleStore.loadSetup().catch(() => {})
    ElMessage.success('配对成功')
    const q = session.applyQueryParams()
    await router.replace('/')
    if (q.siteNonce) {
      window.dispatchEvent(new CustomEvent('forkai-open-site', { detail: q.siteNonce }))
    }
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.error || '配对失败')
  }
}
onMounted(() => {
  bootCheck().catch(() => {})
  genCode().catch(() => {})
})
</script>

<style scoped>
.gate { height: 100%; display: grid; place-items: center; }
.gate-card {
  width: min(560px, 92vw);
  background: rgba(8, 12, 9, 0.92);
  border: 1px solid var(--hud-line-strong, rgba(182, 255, 59, 0.55));
  box-shadow: 0 0 28px rgba(182, 255, 59, 0.08);
  padding: 18px 18px 16px;
}
.term-bar {
  font-family: var(--hud-mono, Consolas, monospace);
  color: var(--hud-accent, #b6ff3b);
  letter-spacing: 0.08em;
  font-size: 13px;
  margin-bottom: 12px;
}
.boot { font-family: var(--hud-mono, Consolas, monospace); font-size: 13px; margin-bottom: 14px; }
.boot-line { opacity: 0; animation: boot-in 0.01s linear forwards; animation-delay: 0.15s; }
.boot-line.d2 { animation-delay: 0.3s; }
.boot-line.d3 { animation-delay: 0.45s; }
@keyframes boot-in { to { opacity: 1; } }
.blink { animation: blink 1s steps(2) infinite; }
@keyframes blink { 50% { opacity: 0; } }
.pair-row { display: flex; align-items: center; gap: 12px; margin: 12px 0; }
.code {
  font-family: var(--hud-mono, Consolas, monospace);
  font-size: 28px;
  letter-spacing: 0.2em;
  color: var(--hud-accent, #b6ff3b);
}
.mt { margin-top: 12px; width: 100%; }
</style>
