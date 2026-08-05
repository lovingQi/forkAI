<template>
  <div class="app-root">
    <header class="app-header">
      <div class="brand">forkAI · 玖物语音控制</div>
      <div class="meta">
        <span>{{ session.vehicleId || '未选车' }}</span>
        <el-tag :type="robot.connected ? 'success' : 'danger'" size="small" effect="dark">
          {{ robot.connected ? '车端已连接' : '车端未连接' }}
        </el-tag>
        <el-tag :type="session.paired ? 'success' : 'warning'" size="small">
          {{ session.paired ? '已配对' : '未配对' }}
        </el-tag>
        <el-tag v-if="session.iAmHolder" type="success" size="small">
          现场 {{ remainMin }} 分钟
        </el-tag>
        <el-button size="small" type="danger" @click="onStop">停</el-button>
      </div>
    </header>

    <main class="app-main">
      <FlowEditor v-if="session.paired && view === 'flow'" />
      <Dashboard v-else-if="session.paired" />
      <div v-else class="gate">
        <el-card class="gate-card">
          <h2>设备配对</h2>
          <p>在车载屏或本页生成配对码，输入后绑定本浏览器。</p>
          <div class="pair-row">
            <el-button @click="genCode">显示/刷新配对码</el-button>
            <strong v-if="session.pairCode" class="code">{{ session.pairCode }}</strong>
          </div>
          <el-input v-model="inputCode" placeholder="输入 6 位配对码" maxlength="6" />
          <el-button type="primary" class="mt" @click="doPair">确认配对</el-button>
        </el-card>
      </div>
    </main>

    <el-dialog v-model="siteVisible" title="现场解锁（点动）" width="420px">
      <p>车端一次性码：<strong>{{ session.siteCode }}</strong></p>
      <el-input v-model="siteInput" placeholder="输入现场码或使用扫码 nonce" />
      <template #footer>
        <el-button @click="siteVisible = false">取消</el-button>
        <el-button type="primary" @click="doUnlock(false)">解锁</el-button>
        <el-button type="warning" @click="doUnlock(true)">强制抢占</el-button>
      </template>
    </el-dialog>

    <div v-if="session.paired" class="fab">
      <el-button type="primary" @click="openSite">现场解锁</el-button>
      <el-button v-if="session.iAmHolder" @click="endSite">结束现场</el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import Dashboard from '@/views/Dashboard.vue'
import FlowEditor from '@/views/FlowEditor.vue'
import { useRobotStore } from '@/stores/robot'
import { useSessionStore } from '@/stores/session'
import { voiceStop } from '@/api/http'

const robot = useRobotStore()
const session = useSessionStore()
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

const remainMin = computed(() => {
  if (!session.siteExpiresAt) return 0
  return Math.max(0, Math.round((session.siteExpiresAt - now.value) / 60000))
})

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

onMounted(async () => {
  tick = window.setInterval(() => {
    now.value = Date.now()
  }, 10000)
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
    genCode().catch(() => {})
  }
})

onBeforeUnmount(() => {
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
  background: #f3f4f6;
  font-family: 'Segoe UI', 'PingFang SC', sans-serif;
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
  background: #111827;
  color: #f9fafb;
}
.brand {
  font-weight: 700;
  letter-spacing: 0.02em;
}
.meta {
  display: flex;
  align-items: center;
  gap: 8px;
}
.app-main {
  flex: 1;
  min-height: 0;
  padding: 12px;
}
.gate {
  height: 100%;
  display: grid;
  place-items: center;
}
.gate-card {
  width: min(420px, 92vw);
}
.pair-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 12px 0;
}
.code {
  font-size: 28px;
  letter-spacing: 0.2em;
}
.mt {
  margin-top: 12px;
  width: 100%;
}
.fab {
  position: fixed;
  right: 16px;
  bottom: 16px;
  display: flex;
  gap: 8px;
}
</style>
