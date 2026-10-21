<template>
  <div class="app-root">
    <header v-if="route.name !== 'boot'" class="app-header">
      <div class="brand"><span class="brand-mark">EASYFORK</span><span class="brand-sub">玖物智能叉车</span></div>
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
          <el-button v-if="consoleStore.isKiosk" size="small" @click="joinVisible = true">手机接入</el-button>
          <el-button
            v-if="consoleStore.isKiosk && !session.iAmHolder && session.siteHolderId"
            size="small"
            type="warning"
            @click="reclaim"
          >
            收回控制权
          </el-button>
          <el-button v-if="showHudToggle" size="small" @click="trace.hudVisible = !trace.hudVisible">HUD</el-button>
          <EstopButton />
        </template>
      </div>
    </header>

    <div class="app-body" :class="{ 'nav-bottom': navBottom }">
      <AppNav v-if="showNav && !navBottom" />
      <main class="app-main" :class="{ 'is-dash': isDash, 'is-boot': route.name === 'boot' }">
        <router-view />
      </main>
      <AppNav v-if="showNav && navBottom" />
    </div>

    <el-dialog v-model="siteVisible" title="现场授权 · SITE AUTHORIZATION" width="420px" class="auth-dialog">
      <p>车端一次性码：<strong class="auth-code">{{ session.siteCode }}</strong></p>
      <p class="auth-hint">输入 6 位现场码或扫码 nonce 获取点动权</p>
      <el-input v-model="siteInput" placeholder="输入现场码或使用扫码 nonce" />
      <template #footer>
        <el-button @click="siteVisible = false">取消</el-button>
        <el-button v-if="consoleStore.isKiosk" type="primary" @click="unlockLocal(false)">本屏解锁</el-button>
        <el-button type="primary" @click="doUnlock(false)">解锁</el-button>
        <el-button v-if="canForce" type="warning" @click="doUnlock(true)">强制抢占</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="joinVisible" title="手机接入" width="420px">
      <QrJoinPanel v-if="joinVisible" />
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import AppNav from '@/components/AppNav.vue'
import EstopButton from '@/components/EstopButton.vue'
import QrJoinPanel from '@/components/QrJoinPanel.vue'
import { useConsoleStore } from '@/stores/console'
import { useRobotStore } from '@/stores/robot'
import { useSessionStore } from '@/stores/session'
import { useTraceStore } from '@/stores/trace'

const robot = useRobotStore()
const session = useSessionStore()
const consoleStore = useConsoleStore()
const trace = useTraceStore()
const route = useRoute()
const router = useRouter()
const siteVisible = ref(false)
const siteInput = ref('')
const joinVisible = ref(false)
const now = ref(Date.now())
const navBottom = ref(window.innerWidth < 900)
let tick: number | null = null

const remainMin = computed(() => {
  if (!session.siteExpiresAt) return 0
  return Math.max(0, Math.round((session.siteExpiresAt - now.value) / 60000))
})
const showNav = computed(
  () => session.paired && consoleStore.setupCompleted === true && !['boot', 'pair', 'welcome', 'setup'].includes(String(route.name))
)
const isDash = computed(() => route.name === 'dashboard')
const showHudToggle = computed(() => route.name === 'dashboard')
const canForce = computed(() => consoleStore.isKiosk || !!consoleStore.status?.allowRemoteForce)

function onResize() {
  navBottom.value = window.innerWidth < 900
}
function openSite(nonce = '') {
  session.refreshSite().catch(() => {})
  if (typeof nonce === 'string' && nonce) siteInput.value = nonce
  siteVisible.value = true
}
function onOpenSite(ev: Event) {
  const detail = (ev as CustomEvent).detail
  openSite(typeof detail === 'string' ? detail : '')
}

async function doUnlock(force: boolean) {
  try {
    const codeOrNonce = siteInput.value.trim() || undefined
    const body = codeOrNonce?.length === 6
      ? { code: codeOrNonce, force }
      : { nonce: codeOrNonce || session.siteNonce, force }
    const r = await session.doUnlock(body)
    if (r === 'held') {
      if (!canForce.value) {
        ElMessage.warning('请到车载屏收回，或等待对方释放')
        return
      }
      await ElMessageBox.confirm('其他设备持有点动权，是否抢占？', '确认', {
        type: 'warning',
        confirmButtonText: '确定',
        cancelButtonText: '取消'
      })
      await session.doUnlock({ ...body, force: true })
    }
    siteVisible.value = false
    ElMessage.success('现场已解锁')
  } catch (e: any) {
    if (e === 'cancel' || e === 'close') return
    ElMessage.error(e?.response?.data?.error || e?.message || '解锁失败')
  }
}

async function unlockLocal(force: boolean) {
  try {
    const r = await session.doUnlock({ local: true, force })
    if (r === 'held') {
      await ElMessageBox.confirm('其他设备持有点动权，是否抢占？', '确认', {
        type: 'warning',
        confirmButtonText: '确定',
        cancelButtonText: '取消'
      })
      await session.doUnlock({ local: true, force: true })
    }
    siteVisible.value = false
    ElMessage.success('现场已解锁')
  } catch (e: any) {
    if (e === 'cancel' || e === 'close') return
    ElMessage.error(e?.response?.data?.error || e?.message || '解锁失败')
  }
}

async function reclaim() {
  try {
    await ElMessageBox.confirm('收回后，对方会立即失去控制。', '收回控制权', {
      type: 'warning',
      confirmButtonText: '确定',
      cancelButtonText: '取消'
    })
    await session.doUnlock({ local: true, force: true })
    ElMessage.success('已收回控制权')
  } catch (e: any) {
    if (e === 'cancel' || e === 'close') return
    ElMessage.error(e?.response?.data?.error || e?.message || '收回失败')
  }
}

async function endSite() {
  await session.doEndSite()
  ElMessage.success('已结束现场模式')
}

async function onUnpaired() {
  const kiosk = consoleStore.isKiosk ? consoleStore.kioskToken : ''
  session.logout()
  robot.disconnectWs()
  if (kiosk) {
    try {
      await session.pairAsLocal(kiosk)
      await robot.loadInitial()
      robot.connectWs()
      return
    } catch {
      consoleStore.clearKiosk()
    }
  }
  await router.replace('/pair')
}

onMounted(async () => {
  tick = window.setInterval(() => {
    now.value = Date.now()
  }, 10000)
  window.addEventListener('resize', onResize)
  window.addEventListener('forkai-unpaired', onUnpaired)
  window.addEventListener('forkai-open-site', onOpenSite)
  session.applyQueryParams()
  if (session.paired) {
    session.connectEvents()
    try {
      await session.refreshSite()
      await robot.loadInitial()
      robot.connectWs()
      await consoleStore.loadStatus()
    } catch {
      session.logout()
    }
  }
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  window.removeEventListener('forkai-unpaired', onUnpaired)
  window.removeEventListener('forkai-open-site', onOpenSite)
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
  min-height: 52px;
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
.meta { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }
.app-body { flex: 1; min-height: 0; display: flex; }
.app-body.nav-bottom { flex-direction: column; }
.app-main { flex: 1; min-width: 0; min-height: 0; padding: 12px; display: flex; flex-direction: column; }
.app-main.is-dash { padding: 0; }
.app-main.is-boot { padding: 0; }
.app-main > * { flex: 1; min-height: 0; }
.auth-code {
  font-family: var(--hud-mono, Consolas, monospace);
  letter-spacing: 0.14em;
  color: var(--hud-accent, #b6ff3b);
}
.auth-hint { color: var(--hud-muted, #7f8f84); font-size: 13px; }
</style>
