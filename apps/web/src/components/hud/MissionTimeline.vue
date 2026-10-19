<template>
  <HudFrame title="MISSION" sub="任务" :glow="running" class="mission-frame">
    <template #actions>
      <span class="src">{{ sourceLabel }}</span>
    </template>
    <div v-if="!mission" class="empty">暂无任务 · 说一句指令或执行任务流</div>
    <template v-else>
      <div class="head">
        <div class="title">{{ mission.title || '未命名任务' }}</div>
        <div class="meta">
          <span class="badge" :class="mission.status">{{ statusEn }}</span>
          <span class="elapsed">{{ elapsed }}</span>
        </div>
      </div>
      <div class="steps">
        <article
          v-for="s in mission.steps"
          :key="s.id"
          class="step"
          :class="[s.status, { inserted: s.inserted }]"
        >
          <div class="mark">{{ mark(s.status) }}</div>
          <div class="body">
            <div class="call">{{ s.call }}</div>
            <div class="label">{{ s.label }}</div>
          </div>
          <div class="side">
            <span v-if="s.inserted" class="ins">INSERTED · 插入</span>
            <span class="st">{{ stepEn(s.status) }}</span>
          </div>
          <div v-if="s.status === 'running'" class="bar" />
        </article>
      </div>
    </template>
  </HudFrame>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import HudFrame from '@/components/hud/HudFrame.vue'
import { useTraceStore } from '@/stores/trace'

const trace = useTraceStore()
const mission = computed(() => trace.mission)
const now = ref(Date.now())
let timer = 0

const running = computed(() => mission.value?.status === 'running' || mission.value?.status === 'waiting')
const sourceLabel = computed(() => {
  const s = mission.value?.source
  if (s === 'voice') return 'VOICE'
  if (s === 'flow') return 'FLOW'
  return 'SKILL TIMELINE'
})
const statusEn = computed(() => (mission.value?.status || 'idle').toUpperCase())
const elapsed = computed(() => {
  const m = mission.value
  if (!m?.startedTs) return ''
  const end = m.endedTs || now.value
  const sec = Math.max(0, Math.round((end - m.startedTs) / 1000))
  return `${sec}s`
})

function stepEn(st: string) {
  const map: Record<string, string> = {
    queued: 'QUEUED',
    running: 'RUNNING',
    waiting: 'WAITING',
    sent: 'SENT',
    done: 'DONE',
    failed: 'FAILED',
    skipped: 'SKIPPED',
    paused: 'PAUSED'
  }
  return map[st] || st.toUpperCase()
}

function mark(st: string) {
  if (st === 'done' || st === 'sent') return '●'
  if (st === 'failed') return '✕'
  if (st === 'running' || st === 'waiting') return '▸'
  if (st === 'paused') return 'Ⅱ'
  if (st === 'skipped') return '·'
  return '○'
}

onMounted(() => {
  timer = window.setInterval(() => {
    now.value = Date.now()
  }, 1000)
})
onBeforeUnmount(() => clearInterval(timer))
</script>

<style scoped>
.mission-frame { height: 100%; }
.mission-frame :deep(.hud-frame-b) { display: flex; flex-direction: column; gap: 8px; }
.src { font-family: var(--hud-mono); font-size: 11px; color: var(--hud-info); letter-spacing: 0.08em; }
.empty { color: var(--hud-muted); padding: 8px 2px; }
.head { display: flex; justify-content: space-between; gap: 8px; align-items: flex-start; }
.title { font-size: 22px; font-weight: 700; line-height: 1.25; }
.meta { display: flex; flex-direction: column; align-items: flex-end; gap: 4px; }
.badge, .st, .ins {
  font-family: var(--hud-mono);
  font-size: 10px;
  letter-spacing: 0.08em;
  border: 1px solid var(--hud-line);
  padding: 2px 6px;
}
.badge.running, .badge.waiting { color: var(--hud-accent); border-color: var(--hud-accent); }
.badge.succeeded { color: var(--hud-done); }
.badge.failed { color: var(--hud-danger); }
.badge.paused { color: var(--hud-warn); }
.elapsed { color: var(--hud-muted); font-family: var(--hud-mono); font-size: 12px; }
.steps { display: flex; flex-direction: column; gap: 8px; }
.step {
  position: relative;
  display: grid;
  grid-template-columns: 18px 1fr auto;
  gap: 8px;
  padding: 8px 10px 10px;
  border: 1px solid var(--hud-line);
  background: rgba(0, 0, 0, 0.25);
  overflow: hidden;
}
.step.running {
  border-color: var(--hud-accent);
  box-shadow: 0 0 12px rgba(182, 255, 59, 0.35);
}
.step.done, .step.sent { border-color: rgba(126, 231, 135, 0.45); }
.step.failed { border-color: var(--hud-danger); }
.step.waiting, .step.paused, .step.inserted { border-color: var(--hud-warn); }
.step.skipped { opacity: 0.55; }
.mark { color: var(--hud-accent); font-family: var(--hud-mono); }
.call { font-family: var(--hud-mono); font-size: 13px; color: var(--hud-accent); }
.label { color: var(--hud-text); font-size: 12px; margin-top: 2px; }
.side { display: flex; flex-direction: column; align-items: flex-end; gap: 4px; }
.ins { color: var(--hud-warn); }
.st { color: var(--hud-muted); }
.bar {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 2px;
  background: linear-gradient(90deg, transparent, var(--hud-accent), transparent);
  animation: slide 1.2s linear infinite;
}
@keyframes slide { from { transform: translateX(-60%); } to { transform: translateX(60%); } }
</style>
