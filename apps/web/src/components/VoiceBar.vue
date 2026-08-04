<template>
  <div class="voice-bar">
    <div class="row">
      <el-tag :type="session.iAmHolder ? 'success' : 'info'" size="small">
        {{ session.iAmHolder ? '现场点动已解锁' : '未持有点动权' }}
      </el-tag>
      <el-tag v-if="session.wakeArmed" type="warning" size="small">唤醒武装中</el-tag>
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

    <div class="utter">{{ session.lastUtterance || '等待指令…' }}</div>
    <div class="logs">
      <div v-for="(l, i) in session.logs.slice(0, 8)" :key="i">{{ l }}</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useSessionStore } from '@/stores/session'
import { voiceStop } from '@/api/http'

const session = useSessionStore()
const pttDown = ref(false)
const showText = ref(true)
const debugText = ref('')
const channel = ref<'ptt' | 'cabin'>('ptt')

let media: MediaRecorder | null = null
let chunks: BlobPart[] = []

async function startPtt() {
  pttDown.value = true
  session.listening = true
  chunks = []
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    media = new MediaRecorder(stream)
    media.ondataavailable = (e) => {
      if (e.data.size) chunks.push(e.data)
    }
    media.start()
  } catch {
    ElMessage.warning('无法开麦，请用文本调试')
  }
}

async function endPtt() {
  if (!pttDown.value) return
  pttDown.value = false
  session.listening = false
  if (media && media.state !== 'inactive') {
    await new Promise<void>((resolve) => {
      media!.onstop = () => resolve()
      media!.stop()
      media!.stream.getTracks().forEach((t) => t.stop())
    })
  }
  media = null
  // V1 mock ASR: prompt user to use text; if debug text empty, hint
  if (!debugText.value) {
    ElMessage.info('ASR mock：请在文本框输入指令后发送（闸门 A 可用文本验收）')
    return
  }
  await sendDebug()
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
