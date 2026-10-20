<template>
  <div class="welcome">
    <h1>连接这台叉车</h1>
    <p v-if="consoleStore.joinNotice" class="notice">{{ consoleStore.joinNotice }}</p>
    <QrJoinPanel />
    <el-button type="primary" @click="goOn">在本屏继续</el-button>
  </div>
</template>

<script setup lang="ts">
import { useRouter } from 'vue-router'
import QrJoinPanel from '@/components/QrJoinPanel.vue'
import { useConsoleStore } from '@/stores/console'

const router = useRouter()
const consoleStore = useConsoleStore()
function goOn() {
  void router.push('/setup')
}
</script>

<style scoped>
.welcome {
  height: 100%;
  display: grid;
  align-content: center;
  justify-items: center;
  gap: 16px;
}
h1 { margin: 0; letter-spacing: 0.08em; }
.notice { color: var(--hud-accent, #b6ff3b); margin: 0; }
</style>
