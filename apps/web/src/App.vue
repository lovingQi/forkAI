<template>
  <div class="app-root">
    <header class="app-header">
      <div class="brand"><span class="brand-mark">FORKAI</span><span class="brand-sub">// 玖物语音控制</span></div>
      <div class="meta">
        <span class="veh">{{ session.vehicleId || '未选车' }}</span>
        <el-tag :type="robot.connected ? 'success' : 'danger'" size="small" effect="dark">
          {{ robot.connected ? '车端已连接' : '车端未连接' }}
        </el-tag>
        <el-tag :type="session.paired ? 'success' : 'warning'" size="small">
          {{ session.paired ? '已配对' : '未配对' }}
        </el-tag>
        <el-tag v-if="session.iAmHolder" type="success" size="small">
          现场 {{ remainMin }} 分钟
        </el-tag>
        <template v-if="session.paired">
          <el-button size="small" @click="openSite">现场解锁</el-button>
          <el-button v-if="session.iAmHolder" size="small" @click="endSite">结束现场</el-button>
          <el-button v-if="view !== 'flow'" size="small" @click="goFlow">任务流编辑器</el-button>
          <el-button size="small" @click="trace.hudVisible = !trace.hudVisible">HUD</el-button>
        </template>
        <el-button size="small" type="danger" class="estop" @click="onStop">停</el-button>
      </div>
    </header>

    <main class="app-main" :class="{ 'is-dash': session.paired && view !== 'flow' }">
      <FlowEditor v-if="session.paired && view === 'flow'" />
      <Dashboard v-else-if="session.paired" />
      <div v-else class="gate">
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
    </main>

    <el-dialog v-model="siteVisible" title="现场授权 · SITE AUTHORIZATION" width="420px" class="auth-dialog">
      <p>车端一次性码：<strong class="auth-code">{{ session.siteCode }}</strong></p>
      <p class="auth-hint">输入 6 位现场码或扫码 nonce 获取点动权</p>
      <el-input v-model="siteInput" placeholder="输入现场码或使用扫码 nonce" />
      <template #footer>
        <el-button @click="siteVisible = false">取消</el-button>
        <el-button type="primary" @click="doUnlock(false)">解锁</el-button>
        <el-button type="warning" @click="doUnlock(true)">强制抢占</el-button>
      </template>
    </el-dialog>

  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import Dashboard from '@/views/Dashboard.vue'
import FlowEditor from '@/views/FlowEditor.vue'
import { useRobotStore } from '@/stores/robot'
import { useSessionStore } from '@/stores/session'
import { getHealth, voiceStop } from '@/api/http'
import { useTraceStore } from '@/stores/trace'

const robot = useRobotStore()
const session = useSessionStore()
const trace = useTraceStore()
const coreLine = ref('[ .. ] 正在检查 core 服务')
const inputCode = ref('')
const siteVisible = ref(false)
const siteInput = ref('')
const now = ref(Date.now())
// 轻量 hash 路由（项目未启用 vue-router）：#/flow → 任务流编辑器
const view = ref(window.location.hash === '#/flow' ? 'flow' : 'dashboard')
window.addEventListener('hashchange', () => {
  view.value = window.location.hash === '#/flow' ? 'flow' : 'dashboard'
})
let tick: number | null = null

const pairLine = computed(() =>
  session.pairCode ? '[ OK ] 已获取配对码' : '[ .. ] 等待配对码'
)

const remainMin = computed(() => {
  if (!session.siteExpiresAt) return 0
  return Math.max(0, Math.round((session.siteExpiresAt - now.value) / 60000))
})

async function bootCheck() {
  try {
    const h = await getHealth()
    coreLine.value = `[ OK ] core 服务在线 · vehicle=${h?.vehicleId || ''}`
  } catch {
    coreLine.value = '[FAIL] core 服务不可达'
  }
}

function goFlow() {
  window.location.hash = '#/flow'
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
    ElMessage.success('配对成功')
    const q = session.applyQueryParams()
    if (q.siteNonce) {
      siteInput.value = q.siteNonce
      siteVisible.value = true
    }
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.error || '配对失败')
  }
}

function openSite() {
  session.refreshSite().catch(() => {})
  siteVisible.value = true
}

async function doUnlock(force: boolean) {
  try {
    const codeOrNonce = siteInput.value.trim() || undefined
    const body = codeOrNonce?.length === 6
      ? { code: codeOrNonce, force }
      : { nonce: codeOrNonce || session.siteNonce, force }
    const r = await session.doUnlock(body)
    if (r === 'held') {
      await ElMessageBox.confirm('其他设备持有点动权，是否抢占？', '确认', { type: 'warning' })
      await session.doUnlock({ ...body, force: true })
    }
    siteVisible.value = false
    ElMessage.success('现场已解锁')
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.error || e?.message || '解锁失败')
  }
}

async function endSite() {
  await session.doEndSite()
  ElMessage.success('已结束现场模式')
}

async function onStop() {
  try {
    await voiceStop()
  } catch (e: any) {
    ElMessage.error(e?.message || '停失败')
  }
}

function onUnpaired() {
  session.logout()
  robot.disconnectWs()
}

onMounted(async () => {
  tick = window.setInterval(() => {
    now.value = Date.now()
  }, 10000)
  window.addEventListener('forkai-unpaired', onUnpaired)
  session.applyQueryParams()
  if (session.paired) {
    session.connectEvents()
    try {
      await session.refreshSite()
      await robot.loadInitial()
      robot.connectWs()
    } catch {
      session.logout()
    }
  } else {
    bootCheck().catch(() => {})
    genCode().catch(() => {})
  }
})

onBeforeUnmount(() => {
  window.removeEventListener('forkai-unpaired', onUnpaired)
  if (tick) clearInterval(tick)
  robot.disconnectWs()
})
</script>

<style>
html,
body,
#app {
  margin: 0;
  height: 100%;
  background: var(--hud-bg, #050806);
  color: var(--hud-text, #e8f5e1);
  font-family: var(--hud-sans, 'PingFang SC', 'Microsoft YaHei', sans-serif);
}
.app-root {
  height: 100%;
  display: flex;
  flex-direction: column;
}
.app-header {
  height: 52px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 16px;
  background: rgba(5, 8, 6, 0.92);
  color: var(--hud-text);
  border-bottom: 1px solid var(--hud-line, rgba(182, 255, 59, 0.22));
  z-index: 5;
}
.brand { display: flex; align-items: baseline; gap: 8px; }
.brand-mark {
  font-family: var(--hud-mono, Consolas, monospace);
  font-weight: 800;
  letter-spacing: 0.14em;
  color: var(--hud-accent, #b6ff3b);
}
.brand-sub { color: var(--hud-muted, #7f8f84); font-size: 13px; }
.veh { font-family: var(--hud-mono, Consolas, monospace); font-size: 12px; }
.meta { display: flex; align-items: center; gap: 8px; }
.estop {
  min-width: 88px;
  height: 36px;
  font-weight: 800;
  box-shadow: 0 0 12px rgba(255, 77, 79, 0.45);
}
.app-main { flex: 1; min-height: 0; padding: 12px; }
.app-main.is-dash { padding: 0; }
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
