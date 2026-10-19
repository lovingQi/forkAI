<template>
  <HudFrame title="COMPUTE" sub="算力与模型" class="model-frame">
    <div class="cards">
      <article v-for="card in cards" :key="card.key" class="card">
        <header>
          <span class="k">{{ card.key }}</span>
          <i class="lamp" :class="{ on: card.live }" />
          <span class="where">{{ card.where }}</span>
        </header>
        <div class="name" :title="card.name">{{ card.name }}</div>
        <div class="sub">{{ card.detail }}</div>
      </article>
    </div>
    <div class="bars" aria-hidden="true">
      <span
        v-for="(b, i) in series"
        :key="i"
        class="bar"
        :style="{ height: b.h + '%', background: b.color }"
        :title="b.ms + 'ms'"
      />
      <span v-if="!series.length" class="none">暂无调用耗时</span>
    </div>
  </HudFrame>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import HudFrame from '@/components/hud/HudFrame.vue'
import { getVoiceProviders, type VoiceProviderItem } from '@/api/http'
import { catMeta } from '@/api/trace'
import { useRobotStore } from '@/stores/robot'
import { useTraceStore } from '@/stores/trace'

const trace = useTraceStore()
const robot = useRobotStore()
const now = ref(Date.now())
const asrName = ref('—')
const llmName = ref('—')
const rulesOn = ref(false)
const thinking = ref(false)
let timer = 0

function short(id: string) {
  return id.split('/').pop() || id
}

function pick(items: VoiceProviderItem[] | undefined, selected: string) {
  const hit = (items || []).find((it) => it.id === selected)
  return hit?.name || (selected ? short(selected) : '—')
}

async function load() {
  try {
    const data = await getVoiceProviders(false)
    asrName.value = pick(data.asr?.items, data.asr?.selected || '')
    llmName.value = pick(data.llm?.items, data.llm?.selected || '')
    rulesOn.value = !!data.nluRulesEnabled
    thinking.value = !!data.llmThinkingEnabled
  } catch {
    /* 未配对 */
  }
}

function metricOf(kind: 'asr' | 'llm' | 'rules' | 'tts') {
  return trace.lastMetric(kind)
}

function live(entryTs?: number) {
  if (!entryTs) return false
  return now.value - entryTs < 2000
}

const cards = computed(() => {
  const asr = metricOf('asr')
  const llm = metricOf('llm')
  const rules = metricOf('rules')
  const tts = metricOf('tts')
  const llmRunning = [...trace.entries].reverse().find((e) => e.cat === 'NLU' && e.status === 'running')
  const asrModel = asr?.data?.metric?.model
  const llmModel = llm?.data?.metric?.model
  return [
    {
      key: 'ASR',
      name: asrModel ? short(String(asrModel)) : asrName.value,
      where: 'CLOUD',
      live: live(asr?.ts),
      detail: asr?.durMs != null ? `${asr.durMs}ms · ${asr.status === 'fail' ? '失败' : '成功'}` : '—'
    },
    {
      key: 'LLM',
      name: llmModel ? short(String(llmModel)) : llmName.value,
      where: 'CLOUD',
      live: live(llm?.ts) || live(llmRunning?.ts),
      detail: `${thinking.value ? '思考开' : '思考关'} · ${llm?.durMs != null ? llm.durMs + 'ms' : '—'}`
    },
    {
      key: 'RULES',
      name: rulesOn.value ? '规则快路径 开' : '规则快路径 关',
      where: 'LOCAL',
      live: live(rules?.ts),
      detail: rules?.durMs != null ? `${rules.durMs}ms` : '—'
    },
    {
      key: 'TTS',
      name: tts?.data?.metric?.engine || '—',
      where: whereLabel(tts?.data?.metric?.where, tts?.data?.metric?.engine),
      live: live(tts?.ts),
      detail: '—'
    },
    {
      key: 'JARVIS',
      name: robot.connected ? '车端已连接' : '车端未连接',
      where: 'LINK',
      live: robot.connected && now.value - robot.lastUpdate < 3000,
      detail: robot.lastUpdate ? `${Math.max(0, Math.round((now.value - robot.lastUpdate) / 1000))}s 前` : '—'
    }
  ]
})

function whereLabel(where?: string, engine?: string) {
  if (engine === 'cloud-cache') return 'CACHE'
  if (where === 'local') return 'LOCAL'
  if (where === 'browser') return 'BROWSER'
  if (where === 'cloud') return 'CLOUD'
  return '—'
}

const series = computed(() => {
  const list = trace.metricSeries
  const max = Math.max(1, ...list.map((b) => b.ms))
  return list.map((b) => ({
    h: Math.max(8, Math.round((b.ms / max) * 100)),
    color: catMeta(b.cat).color,
    ms: b.ms
  }))
})

function onProviders() {
  void load()
}

onMounted(() => {
  void load()
  timer = window.setInterval(() => {
    now.value = Date.now()
  }, 1000)
  window.addEventListener('forkai-providers-changed', onProviders)
})
onBeforeUnmount(() => {
  clearInterval(timer)
  window.removeEventListener('forkai-providers-changed', onProviders)
})
</script>

<style scoped>
.model-frame { height: 100%; }
.model-frame :deep(.hud-frame-b) { display: flex; flex-direction: column; gap: 8px; }
.cards {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 6px;
}
.card {
  border: 1px solid var(--hud-line);
  padding: 6px 8px;
  min-width: 0;
}
.card header { display: flex; align-items: center; gap: 6px; }
.k { font-family: var(--hud-mono); font-size: 11px; color: var(--hud-accent); letter-spacing: 0.08em; }
.where { margin-left: auto; font-family: var(--hud-mono); font-size: 10px; color: var(--hud-muted); }
.lamp {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #243028;
  box-shadow: none;
}
.lamp.on { background: var(--hud-accent); box-shadow: 0 0 8px var(--hud-accent); }
.name {
  margin-top: 4px;
  font-size: 12px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.sub { color: var(--hud-muted); font-family: var(--hud-mono); font-size: 11px; }
.bars {
  height: 36px;
  display: flex;
  align-items: flex-end;
  gap: 3px;
}
.bar { width: 8px; display: block; opacity: 0.9; }
.none { color: var(--hud-muted); font-size: 11px; }
</style>
