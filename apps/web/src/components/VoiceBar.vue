<template>
  <div class="voice-bar">
    <div class="row">
      <el-tag :type="session.iAmHolder ? 'success' : 'info'" size="small">
        {{ session.iAmHolder ? '现场点动已解锁' : '未持有点动权' }}
      </el-tag>
      <el-tag v-if="session.wakeArmed" type="warning" size="small">唤醒武装中</el-tag>
      <span class="sub">按住空格说话，松开结束；速度指令走启停式；看门狗默认 2s</span>
    </div>

    <div class="row actions">
      <el-button
        type="primary"
        :class="{ holding: pttDown }"
        @mousedown.prevent="startPtt"
        @mouseup.prevent="endPtt"
        @mouseleave="endPtt"
        @touchstart.prevent="startPtt"
        @touchend.prevent="endPtt"
      >
        {{ pttDown ? '松开结束' : '按住说话 (空格)' }}
      </el-button>
      <el-select
        v-model="asrModel"
        class="model-select"
        placeholder="ASR"
        @visible-change="onAsrVisible"
        @change="onAsrChange"
      >
        <el-option
          v-for="it in asrItems"
          :key="it.id"
          :label="optionLabel(it)"
          :value="it.id"
        />
      </el-select>
      <el-select
        v-model="llmModel"
        class="model-select"
        placeholder="LLM"
        @visible-change="onLlmVisible"
        @change="onLlmChange"
      >
        <el-option
          v-for="it in llmItems"
          :key="it.id"
          :label="optionLabel(it)"
          :value="it.id"
        />
      </el-select>
      <el-button type="danger" @click="onStop">停</el-button>
      <el-button @click="showText = !showText">文本调试</el-button>
    </div>

    <div v-if="showText" class="row">
      <el-input
        v-model="debugText"
        :placeholder="textPlaceholder"
        @keyup.enter="sendDebug"
      />
      <el-button type="primary" @click="sendDebug">发送</el-button>
    </div>

    <div v-if="pttDown" class="partial">
      {{ partialText || '聆听中…' }}
    </div>
    <div v-else-if="recognizing" class="partial">识别中…</div>
    <div class="utter">{{ session.lastUtterance || '等待指令…' }}</div>
    <div class="logs">
      <div v-for="(l, i) in session.logs.slice(0, 8)" :key="i">{{ l }}</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useSessionStore } from '@/stores/session'
import { getPairToken, getVoiceProviders, setVoiceProviders, voiceStop, type VoiceProviderItem } from '@/api/http'
import { AudioWs, MicCapture, type AudioFinalMessage } from '@/api/audio'

const session = useSessionStore()
const pttDown = ref(false)
const showText = ref(true)
const debugText = ref('')
const recognizing = ref(false)
const partialText = ref('')
const asrModel = ref('')
const llmModel = ref('')
const asrItems = ref<VoiceProviderItem[]>([])
const llmItems = ref<VoiceProviderItem[]>([])
const textPlaceholder = computed(() => {
  if (pttDown.value) return '聆听中…'
  if (recognizing.value) return '识别中…'
  return '例如：前进 / 玖物，玖物 / 电量多少'
})

function showAsrInBox(text: string) {
  showText.value = true
  debugText.value = text
}

let capture: MicCapture | null = null
let audioWs: AudioWs | null = null
let micBroken = false
let ignoreChange = false
let loadSeq = 0
let loadAbort: AbortController | null = null
let pttGen = 0
let pttStreaming = false
let pttReady = false
let pttFinishing = false
const pttPrebuf: ArrayBuffer[] = []

function optionLabel(it: VoiceProviderItem) {
  if (it.latencyMs != null) return `${it.name}（${it.latencyMs}ms）`
  if (it.error) return `${it.name}（${it.error}）`
  return it.name
}

function shortName(id: string) {
  return id.split('/').pop() || id
}

function ensureSelected(items: VoiceProviderItem[], selected: string): VoiceProviderItem[] {
  if (!selected || items.some((it) => it.id === selected)) return items
  return [{ id: selected, name: shortName(selected), latencyMs: null, error: null }, ...items]
}

function mergeLatencies(current: VoiceProviderItem[], incoming: VoiceProviderItem[]): VoiceProviderItem[] {
  const byId = new Map<string, VoiceProviderItem>()
  for (const it of current) byId.set(it.id, { ...it })
  for (const it of incoming) {
    const prev = byId.get(it.id)
    byId.set(it.id, prev ? { ...prev, name: it.name || prev.name, latencyMs: it.latencyMs, error: it.error } : { ...it })
  }
  const out: VoiceProviderItem[] = []
  const seen = new Set<string>()
  for (const it of incoming) {
    const merged = byId.get(it.id)
    if (merged && !seen.has(it.id)) {
      out.push(merged)
      seen.add(it.id)
    }
  }
  for (const it of current) {
    if (!seen.has(it.id)) {
      out.push(byId.get(it.id)!)
      seen.add(it.id)
    }
  }
  return out
}

async function withIgnoreChange(fn: () => void) {
  ignoreChange = true
  try {
    fn()
    await nextTick()
  } finally {
    ignoreChange = false
  }
}

async function loadProviders(probe: boolean) {
  if (!getPairToken()) return
  loadAbort?.abort()
  loadAbort = new AbortController()
  const seq = ++loadSeq
  const signal = loadAbort.signal
  try {
    const data = await getVoiceProviders(probe, signal)
    if (seq !== loadSeq) return
    await withIgnoreChange(() => {
      if (probe) {
        asrItems.value = ensureSelected(mergeLatencies(asrItems.value, data.asr.items || []), asrModel.value)
        llmItems.value = ensureSelected(mergeLatencies(llmItems.value, data.llm.items || []), llmModel.value)
      } else {
        asrItems.value = ensureSelected(data.asr.items || [], data.asr.selected || asrModel.value)
        llmItems.value = ensureSelected(data.llm.items || [], data.llm.selected || llmModel.value)
        if (data.asr.selected) asrModel.value = data.asr.selected
        if (data.llm.selected) llmModel.value = data.llm.selected
      }
    })
  } catch (e: any) {
    if (seq !== loadSeq || signal.aborted || e?.code === 'ERR_CANCELED' || e?.name === 'CanceledError') return
    if (probe) ElMessage.warning(e?.message || '延迟探测失败')
  }
}

function onAsrVisible(open: boolean) {
  if (open) void loadProviders(true)
}

function onLlmVisible(open: boolean) {
  if (open) void loadProviders(true)
}

async function onAsrChange(id: string) {
  if (ignoreChange || !id) return
  try {
    await setVoiceProviders({ asrModel: id })
  } catch (e: any) {
    ElMessage.error(e?.message || '保存 ASR 失败')
  }
}

async function onLlmChange(id: string) {
  if (ignoreChange || !id) return
  try {
    await setVoiceProviders({ llmModel: id })
  } catch (e: any) {
    ElMessage.error(e?.message || '保存 LLM 失败')
  }
}

function onMicPcm(pcm: ArrayBuffer) {
  if (pttStreaming && audioWs) {
    audioWs.sendPcm(pcm)
    return
  }
  if (pttDown.value || pttFinishing) pttPrebuf.push(pcm)
}

function flushPttToWs() {
  capture?.flush()
  if (audioWs) {
    for (const chunk of pttPrebuf) audioWs.sendPcm(chunk)
  }
  pttPrebuf.length = 0
}

async function ensureMic(): Promise<boolean> {
  if (micBroken) return false
  if (!capture) capture = new MicCapture()
  capture.setHandler(onMicPcm)
  if (capture.running) return true
  try {
    await capture.start(onMicPcm)
    return true
  } catch {
    ElMessage.warning('无法开麦（无权限或无设备），已回退文本输入')
    micBroken = true
    showText.value = true
    capture.stop()
    capture = null
    return false
  }
}

function onFinal(msg: AudioFinalMessage, ws: AudioWs) {
  recognizing.value = false
  partialText.value = ''
  pttStreaming = false
  if (msg.text) {
    showAsrInBox(msg.text)
    session.pushLog(`我说: ${msg.text}`)
  }
  if (msg.utterance) session.lastUtterance = msg.utterance
  if (msg.intent?.name) session.lastIntent = msg.intent.name
  if (audioWs === ws) {
    ws.close()
    audioWs = null
  }
}

async function connectAudioWs(): Promise<boolean> {
  const ws = new AudioWs(
    (t) => {
      partialText.value = t
      if (t) showAsrInBox(t)
    },
    (msg) => onFinal(msg, ws),
    () => {
      if (audioWs === ws) recognizing.value = false
    }
  )
  audioWs = ws
  try {
    await ws.connect(getPairToken(), 'ptt')
    return audioWs === ws
  } catch (e: any) {
    ElMessage.warning(`语音通道不可用：${e?.message || e}，请用文本调试`)
    if (audioWs === ws) audioWs = null
    return false
  }
}

function finishPttUtterance() {
  if (pttFinishing) return
  pttFinishing = true
  pttStreaming = false
  pttReady = false
  session.listening = false
  flushPttToWs()
  if (audioWs) {
    recognizing.value = true
    audioWs.end()
    const ws = audioWs
    setTimeout(() => {
      if (audioWs === ws && recognizing.value) {
        recognizing.value = false
        ws.close()
        if (audioWs === ws) audioWs = null
      }
    }, 20000)
  }
}

async function startPtt() {
  if (pttDown.value) return
  if (micBroken) {
    ElMessage.info('麦克风不可用，请用文本调试')
    return
  }
  const gen = ++pttGen
  pttFinishing = false
  pttReady = false
  pttStreaming = false
  pttPrebuf.length = 0
  pttDown.value = true
  session.listening = true
  partialText.value = ''
  debugText.value = ''
  showText.value = true
  const [micOk, wsOk] = await Promise.all([ensureMic(), connectAudioWs()])
  if (gen !== pttGen) return
  if (!micOk || !wsOk) {
    pttDown.value = false
    session.listening = false
    pttPrebuf.length = 0
    audioWs?.close()
    audioWs = null
    return
  }
  flushPttToWs()
  pttReady = true
  if (!pttDown.value) {
    finishPttUtterance()
    return
  }
  pttStreaming = true
}

async function endPtt() {
  if (!pttDown.value) return
  pttDown.value = false
  session.listening = false
  if (pttReady) finishPttUtterance()
}

async function sendDebug() {
  const text = debugText.value.trim()
  if (!text) return
  try {
    await session.sendText(text, 'ptt')
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.error || e?.message || '发送失败')
  }
}

async function onStop() {
  try {
    await voiceStop()
  } catch (e: any) {
    ElMessage.error(e?.message || '停失败')
  }
}

function isTypingTarget(el: EventTarget | null): boolean {
  if (!(el instanceof HTMLElement)) return false
  const tag = el.tagName
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true
  if (el.isContentEditable) return true
  if (el.closest('input, textarea, select, [contenteditable="true"], .el-input, .el-select, .el-textarea')) {
    return true
  }
  return false
}

function isSpaceKey(e: KeyboardEvent): boolean {
  return e.code === 'Space' || e.key === ' '
}

function onSpaceDown(e: KeyboardEvent) {
  if (!isSpaceKey(e) || e.repeat) return
  if (isTypingTarget(e.target)) return
  e.preventDefault()
  void startPtt()
}

function onSpaceUp(e: KeyboardEvent) {
  if (!isSpaceKey(e)) return
  if (isTypingTarget(e.target)) return
  e.preventDefault()
  void endPtt()
}

function onWindowBlur() {
  void endPtt()
}

onMounted(() => {
  void loadProviders(false)
  if (getPairToken()) void ensureMic()
  window.addEventListener('keydown', onSpaceDown)
  window.addEventListener('keyup', onSpaceUp)
  window.addEventListener('blur', onWindowBlur)
})

onBeforeUnmount(() => {
  loadAbort?.abort()
  window.removeEventListener('keydown', onSpaceDown)
  window.removeEventListener('keyup', onSpaceUp)
  window.removeEventListener('blur', onWindowBlur)
  if (capture) {
    capture.stop()
    capture = null
  }
  if (audioWs) {
    audioWs.close()
    audioWs = null
  }
})
</script>

<style scoped>
.voice-bar {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.actions .holding {
  background: #2563eb;
}
.model-select {
  width: 220px;
}
.sub {
  color: #6b7280;
  font-size: 12px;
}
.partial {
  font-size: 14px;
  color: #2563eb;
  min-height: 20px;
}
.utter {
  font-size: 16px;
  font-weight: 600;
  color: #111827;
  min-height: 24px;
}
.logs {
  font-size: 12px;
  color: #6b7280;
  max-height: 120px;
  overflow: auto;
}
</style>
