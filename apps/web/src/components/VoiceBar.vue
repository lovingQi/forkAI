<template>
  <div class="voice-bar">
    <div class="row">
      <el-tag :type="session.iAmHolder ? 'success' : 'info'" size="small">
        {{ session.iAmHolder ? '现场点动已解锁' : '未持有点动权' }}
      </el-tag>
      <el-tag v-if="session.wakeArmed" type="warning" size="small">唤醒武装中</el-tag>
      <el-tag v-if="cabinListening" type="danger" size="small">车载常听中</el-tag>
      <span class="sub">速度指令走启停式；看门狗默认 2s</span>
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
        {{ pttDown ? '松开结束' : '按住说话 (PTT)' }}
      </el-button>
      <el-button :type="cabinListening ? 'warning' : 'default'" @click="toggleCabin">
        {{ cabinListening ? '停止常听' : '车载常听' }}
      </el-button>
      <el-button type="danger" @click="onStop">停</el-button>
      <el-button @click="showText = !showText">文本调试</el-button>
    </div>

    <div v-if="showText" class="row">
      <el-input v-model="debugText" placeholder="例如：前进 / 玖物，玖物 / 电量多少" @keyup.enter="sendDebug" />
      <el-select v-model="channel" style="width: 110px">
        <el-option label="PTT" value="ptt" />
        <el-option label="车载" value="cabin" />
      </el-select>
      <el-button type="primary" @click="sendDebug">发送</el-button>
    </div>

    <div v-if="pttDown || cabinListening" class="partial">
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
import { onBeforeUnmount, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useSessionStore } from '@/stores/session'
import { getPairToken, voiceStop } from '@/api/http'
import { AudioWs, MicCapture, type AudioFinalMessage } from '@/api/audio'

const session = useSessionStore()
const pttDown = ref(false)
const showText = ref(true)
const debugText = ref('')
const channel = ref<'ptt' | 'cabin'>('ptt')
const recognizing = ref(false)
const partialText = ref('')
const cabinListening = ref(false)

let capture: MicCapture | null = null
let audioWs: AudioWs | null = null
let mode: 'ptt' | 'cabin' = 'ptt'
let micBroken = false

function onFinal(msg: AudioFinalMessage) {
  recognizing.value = false
  partialText.value = ''
  if (msg.text) session.pushLog(`我说: ${msg.text}`)
  if (msg.utterance) session.lastUtterance = msg.utterance
  if (msg.intent?.name) session.lastIntent = msg.intent.name
  if (mode === 'ptt') {
    // PTT 一次 final 后结束本次连接（服务端已 reset 流，可继续下一句）
    audioWs?.close()
    audioWs = null
  }
  // cabin 常听保持连接，final 后自动继续
}

async function openAudio(ch: 'ptt' | 'cabin'): Promise<boolean> {
  mode = ch
  audioWs = new AudioWs(
    (t) => (partialText.value = t),
    onFinal,
    () => {
      if (mode === 'cabin' && cabinListening.value) stopCabin()
    }
  )
  try {
    await audioWs.connect(getPairToken(), ch)
  } catch (e: any) {
    ElMessage.warning(`语音通道不可用：${e?.message || e}，请用文本调试`)
    audioWs = null
    return false
  }
  capture = new MicCapture()
  try {
    await capture.start((pcm) => audioWs?.sendPcm(pcm))
  } catch {
    ElMessage.warning('无法开麦（无权限或无设备），已回退文本输入')
    micBroken = true
    showText.value = true
    capture.stop()
    capture = null
    audioWs.close()
    audioWs = null
    return false
  }
  return true
}

async function startPtt() {
  if (pttDown.value || cabinListening.value) return
  if (micBroken) {
    ElMessage.info('麦克风不可用，请用文本调试')
    return
  }
  pttDown.value = true
  session.listening = true
  partialText.value = ''
  const ok = await openAudio(channel.value)
  if (!ok) {
    pttDown.value = false
    session.listening = false
  }
}

async function endPtt() {
  if (!pttDown.value) return
  pttDown.value = false
  session.listening = false
  if (capture) {
    capture.stop()
    capture = null
  }
  if (audioWs) {
    recognizing.value = true
    audioWs.end()
    // 兜底：8s 无 final 关闭连接
    const ws = audioWs
    setTimeout(() => {
      if (audioWs === ws && recognizing.value) {
        recognizing.value = false
        ws.close()
        if (audioWs === ws) audioWs = null
      }
    }, 8000)
  }
}

async function toggleCabin() {
  if (cabinListening.value) {
    stopCabin()
    return
  }
  if (micBroken) {
    ElMessage.info('麦克风不可用，请用文本调试')
    return
  }
  partialText.value = ''
  const ok = await openAudio('cabin')
  if (ok) {
    cabinListening.value = true
    session.listening = true
  }
}

function stopCabin() {
  cabinListening.value = false
  session.listening = false
  partialText.value = ''
  if (capture) {
    capture.stop()
    capture = null
  }
  if (audioWs) {
    audioWs.close()
    audioWs = null
  }
}

async function sendDebug() {
  const text = debugText.value.trim()
  if (!text) return
  try {
    await session.sendText(text, channel.value)
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

onBeforeUnmount(() => {
  stopCabin()
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
