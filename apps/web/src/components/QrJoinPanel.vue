<template>
  <div class="qr-panel">
    <img v-if="image" :src="image" alt="接入二维码" class="qr" />
    <p class="url">{{ url }}</p>
    <p class="count">{{ remain }} 秒后刷新</p>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import QRCode from 'qrcode'
import { ElMessage } from 'element-plus'
import { consoleErrorMessage, getJoinTicket } from '@/api/console'
import { useConsoleStore } from '@/stores/console'

const consoleStore = useConsoleStore()
const image = ref('')
const url = ref('')
const remain = ref(0)
let timer: number | null = null
let expiresAt = 0

async function load() {
  try {
    const ticket = await getJoinTicket()
    url.value = ticket.url
    expiresAt = ticket.expiresAt
    image.value = await QRCode.toDataURL(ticket.url, {
      margin: 1,
      width: 280,
      color: { dark: '#b6ff3b', light: '#050806' }
    })
  } catch (e: any) {
    ElMessage.error(consoleErrorMessage(e))
  }
}

function tick() {
  remain.value = Math.max(0, Math.ceil((expiresAt - Date.now()) / 1000))
  if (expiresAt && remain.value <= 10) {
    expiresAt = 0
    void load()
  }
}

watch(
  () => consoleStore.joinSerial,
  () => {
    void load()
  }
)

void load()
timer = window.setInterval(tick, 1000)
onBeforeUnmount(() => {
  if (timer !== null) window.clearInterval(timer)
})
</script>

<style scoped>
.qr-panel {
  display: grid;
  justify-items: center;
  gap: 8px;
}
.qr {
  width: 220px;
  height: 220px;
  background: #050806;
}
.url {
  font-family: var(--hud-mono, Consolas, monospace);
  font-size: 12px;
  word-break: break-all;
  color: var(--hud-muted, #7f8f84);
  margin: 0;
}
.count {
  margin: 0;
  color: var(--hud-accent, #b6ff3b);
}
</style>
