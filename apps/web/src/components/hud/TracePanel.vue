<template>
  <HudFrame title="AGENT TRACE" sub="推理日志" class="trace-frame">
    <template #actions>
      <button
        v-for="c in CATS"
        :key="c.id"
        type="button"
        class="flt"
        :class="{ off: !on[c.id] }"
        :style="{ color: on[c.id] ? c.color : 'var(--hud-skip)' }"
        @click="on[c.id] = !on[c.id]"
      >
        {{ c.id }}
      </button>
    </template>
    <div ref="scroller" class="rows" @scroll="onScroll">
      <template v-for="(e, i) in shown" :key="e.id">
        <div v-if="i === 0 || e.traceId !== shown[i - 1].traceId" class="sep">
          ── #{{ e.traceId || '--------' }} ──
        </div>
        <div class="row" :class="e.status">
          <span class="ts">{{ fmt(e.ts) }}</span>
          <span class="tag" :style="{ color: catMeta(e.cat).color }">[{{ e.cat }}]</span>
          <span class="zh">{{ catMeta(e.cat).zh }}</span>
          <span class="msg">
            <i v-if="e.status === 'running'" class="dot" />
            {{ e.msg }}
          </span>
          <span class="ms">{{ e.durMs != null ? e.durMs + 'ms' : '' }}</span>
        </div>
      </template>
      <div v-if="!shown.length" class="empty">暂无日志 · 说一句指令后会出现在这里</div>
    </div>
  </HudFrame>
</template>

<script setup lang="ts">
import { computed, nextTick, reactive, ref, watch } from 'vue'
import HudFrame from '@/components/hud/HudFrame.vue'
import { CATS, catMeta, type TraceCat } from '@/api/trace'
import { useTraceStore } from '@/stores/trace'

const trace = useTraceStore()
const on = reactive<Record<string, boolean>>({})
for (const c of CATS) on[c.id] = true

const shown = computed(() =>
  trace.entries.filter((e) => on[e.cat as TraceCat] !== false).slice(-300)
)

const scroller = ref<HTMLElement | null>(null)
const stick = ref(true)

function fmt(ts: number) {
  const d = new Date(ts)
  const p = (n: number, w = 2) => String(n).padStart(w, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}.${p(Math.floor(d.getMilliseconds() / 100), 1)}`
}

function onScroll() {
  const el = scroller.value
  if (!el) return
  stick.value = el.scrollHeight - el.scrollTop - el.clientHeight < 40
}

watch(
  () => shown.value.length,
  async () => {
    if (!stick.value) return
    await nextTick()
    const el = scroller.value
    if (el) el.scrollTop = el.scrollHeight
  }
)
</script>

<style scoped>
.trace-frame { height: 100%; }
.trace-frame :deep(.hud-frame-h) { flex-wrap: wrap; }
.trace-frame :deep(.hud-frame-b) { padding: 0; display: flex; }
.flt {
  border: 0;
  background: transparent;
  font-family: var(--hud-mono);
  font-size: 10px;
  letter-spacing: 0.04em;
  cursor: pointer;
  padding: 0 2px;
}
.flt.off { opacity: 0.45; text-decoration: line-through; }
.rows {
  flex: 1;
  min-height: 0;
  overflow: auto;
  font-family: var(--hud-mono);
  font-size: 12px;
  padding: 6px 8px 8px;
}
.sep {
  color: var(--hud-muted);
  font-size: 10px;
  letter-spacing: 0.08em;
  margin: 8px 0 4px;
}
.row {
  display: grid;
  grid-template-columns: 78px 62px 36px 1fr auto;
  gap: 6px;
  align-items: baseline;
  padding: 2px 0;
  color: var(--hud-text);
}
.row.fail .msg { color: var(--hud-danger); }
.ts, .ms { color: var(--hud-muted); }
.tag { font-weight: 700; }
.zh { color: var(--hud-muted); }
.msg { word-break: break-all; }
.dot {
  display: inline-block;
  width: 6px;
  height: 6px;
  margin-right: 4px;
  border-radius: 50%;
  background: var(--hud-accent);
  animation: blink 1s steps(2) infinite;
}
@keyframes blink { 50% { opacity: 0.2; } }
.empty { color: var(--hud-muted); padding: 12px 4px; }
</style>
