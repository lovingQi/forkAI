<template>
  <div class="boot" :class="{ 'is-intro': phase === 'intro' }">
    <BootIntro v-if="phase === 'intro'" @done="beginCheck" @fallback="beginCheck" />
    <div ref="cardEl" class="card" data-testid="boot-card">
      <div class="mark">FORKAI</div>
      <div class="line">{{ lines.core }}</div>
      <div class="line">{{ lines.jarvis }}</div>
      <div class="line">{{ lines.ros }}</div>
      <p v-if="error" class="fail">{{ error }}</p>
      <el-button v-if="error" @click="run">重试</el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { getSelfCheck } from '@/api/console'
import BootIntro from '@/components/boot/BootIntro.vue'
import { useConsoleStore } from '@/stores/console'
import { useRobotStore } from '@/stores/robot'
import { useSessionStore } from '@/stores/session'

const router = useRouter()
const consoleStore = useConsoleStore()
const session = useSessionStore()
const robot = useRobotStore()
const params = new URLSearchParams(window.location.search)
const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
const phase = ref<'intro' | 'check'>(reduceMotion ? 'check' : 'intro')
const cardEl = ref<HTMLElement | null>(null)
const error = ref('')
const lines = reactive({
  core: '[ .. ] core',
  jarvis: '[ .. ] jarvis',
  ros: '[ .. ] rosbridge'
})

function stripAuthQuery() {
  const url = new URL(window.location.href)
  url.searchParams.delete('kiosk')
  url.searchParams.delete('join')
  const search = url.searchParams.toString()
  window.history.replaceState({}, '', url.pathname + (search ? `?${search}` : '') + url.hash)
}

async function enter() {
  if (session.paired) {
    try {
      await consoleStore.loadSetup()
    } catch {
      /* 守卫会再取一次 */
    }
    session.connectEvents()
    try {
      await robot.loadInitial()
      robot.connectWs()
    } catch {
      /* 车端未就绪时主页仍可打开 */
    }
  }
  consoleStore.finishBoot()
  stripAuthQuery()
  const raw = router.currentRoute.value.query.next
  const next = typeof raw === 'string' ? raw : ''
  const safe = next.startsWith('/') && !next.startsWith('//') && !next.startsWith('/boot')
  await router.replace(safe ? next : session.paired ? '/' : '/pair')
}

async function run() {
  error.value = ''
  const kiosk = params.get('kiosk') || (consoleStore.isKiosk ? consoleStore.kioskToken : '')
  const join = params.get('join')
  try {
    if (kiosk) {
      if (!session.paired) await session.pairAsLocal(kiosk)
      consoleStore.setKiosk(kiosk)
    } else if (join && !session.paired) {
      await session.pairByJoin(join)
    }
  } catch (e: any) {
    error.value = e?.response?.data?.error === 'invalid_kiosk' ? '车载屏口令无效' : '接入失败'
    if (kiosk) consoleStore.clearKiosk()
    return
  }
  try {
    const check = await getSelfCheck()
    consoleStore.selfcheck = check
    lines.core = check.core.ok ? '[ OK ] core' : '[FAIL] core'
    lines.jarvis = check.jarvis.ok
      ? `[ OK ] jarvis ${check.jarvis.latencyMs ?? 0}ms`
      : `[FAIL] jarvis ${check.jarvis.error || ''}`
    lines.ros = `[ -- ] rosbridge ${check.rosbridge.detail || '二期接入'}`
    if (!check.ok) {
      error.value = '自检未通过'
      return
    }
  } catch {
    lines.core = '[FAIL] core'
    error.value = '自检未通过'
    return
  }
  window.setTimeout(() => {
    void enter()
  }, 700)
}

function beginCheck() {
  if (phase.value === 'check') return
  cardEl.value?.removeAttribute('style')
  phase.value = 'check'
  void run()
}

onMounted(() => {
  if (phase.value === 'check') void run()
})
</script>

<style scoped>
.boot {
  height: 100%;
  display: grid;
  place-items: center;
  position: relative;
}
.boot.is-intro .card {
  opacity: 0;
  pointer-events: none;
}
.card {
  width: min(560px, 92vw);
  padding: 28px;
  border: 1px solid var(--hud-line-strong, rgba(182, 255, 59, 0.55));
  font-family: var(--hud-mono, Consolas, monospace);
  background: var(--hud-bg, #050806);
}
.mark {
  color: var(--hud-accent, #b6ff3b);
  letter-spacing: 0.2em;
  font-size: 28px;
  margin-bottom: 16px;
}
.line {
  margin: 6px 0;
}
.fail {
  color: var(--hud-danger, #ff4d4f);
}
</style>
