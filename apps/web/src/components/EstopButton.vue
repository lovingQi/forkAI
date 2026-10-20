<template>
  <span class="estop-wrap">
    <el-button
      size="small"
      :type="latched ? 'warning' : 'danger'"
      class="estop"
      @click="onClick"
    >
      {{ latched ? '解除急停' : '急停' }}
    </el-button>
    <span class="estop-note">软件急停，不替代物理急停</span>
  </span>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { consoleErrorMessage, consoleEstop, consoleEstopRelease } from '@/api/console'
import { useConsoleStore } from '@/stores/console'
import { useSessionStore } from '@/stores/session'

const consoleStore = useConsoleStore()
const session = useSessionStore()
const latched = computed(() => !!consoleStore.status?.estopLatched)

async function onClick() {
  try {
    if (!latched.value) {
      await consoleEstop()
      await consoleStore.loadStatus()
      ElMessage.warning('已软件急停')
      return
    }
    if (!session.iAmHolder) {
      ElMessage.warning('解除急停需要控制权')
      return
    }
    await ElMessageBox.confirm('确认重新使能电机并解除软件急停？', '解除急停', {
      type: 'warning',
      confirmButtonText: '确定',
      cancelButtonText: '取消'
    })
    await consoleEstopRelease(true)
    await consoleStore.loadStatus()
    ElMessage.success('已解除软件急停')
  } catch (e: any) {
    if (e === 'cancel' || e === 'close') return
    ElMessage.error(consoleErrorMessage(e))
  }
}
</script>

<style scoped>
.estop-wrap {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.estop {
  min-width: 88px;
  height: 36px;
  font-weight: 800;
}
.estop-note {
  color: var(--hud-danger, #ff4d4f);
  font-size: 12px;
  max-width: 8em;
  line-height: 1.2;
}
</style>
