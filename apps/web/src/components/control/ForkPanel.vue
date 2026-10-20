<template>
  <section class="fork">
    <h3>货叉</h3>
    <div class="axis">
      <header>
        <span>升降</span>
        <strong>{{ height }} mm</strong>
      </header>
      <div class="ops">
        <el-button :disabled="disabled" @click="step('lift', liftStep)">升 {{ liftStep }}</el-button>
        <el-button :disabled="disabled" @click="step('lift', -liftStep)">降 {{ liftStep }}</el-button>
        <el-input-number v-model="liftTarget" :disabled="disabled" :min="liftMin" :max="liftMax" />
        <el-button :disabled="disabled" @click="go('lift', liftTarget)">到此高度</el-button>
      </div>
    </div>
    <div v-if="extendShown" class="axis">
      <header>
        <span>伸缩</span>
        <strong>{{ reach }} mm</strong>
      </header>
      <div class="ops">
        <el-button :disabled="disabled" @click="step('extend', extStep)">伸 {{ extStep }}</el-button>
        <el-button :disabled="disabled" @click="step('extend', -extStep)">缩 {{ extStep }}</el-button>
        <el-input-number v-model="extTarget" :disabled="disabled" :min="0" :max="extMax" />
        <el-button :disabled="disabled" @click="go('extend', extTarget)">到此位置</el-button>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { consoleErrorMessage, consoleFork } from '@/api/console'
import { useConsoleStore } from '@/stores/console'
import { useRobotStore } from '@/stores/robot'

const props = defineProps<{ disabled?: boolean }>()
const robot = useRobotStore()
const consoleStore = useConsoleStore()
const height = computed(() => Number(robot.forkInfo?.fork_height ?? 0))
const reach = computed(() => Number(robot.forkInfo?.fork_cur_dis ?? 0))
const extendShown = computed(() => robot.forkInfo && Object.prototype.hasOwnProperty.call(robot.forkInfo, 'fork_cur_dis'))
const liftMin = computed(() => consoleStore.status?.limits.forkLift.min ?? 75)
const liftMax = computed(() => consoleStore.status?.limits.forkLift.max ?? 210)
const liftStep = computed(() => consoleStore.status?.limits.forkLift.step ?? 50)
const extMax = computed(() => {
  const reported = Number(robot.forkInfo?.fork_max_dis ?? consoleStore.status?.limits.forkExtend.max ?? 640)
  const cap = consoleStore.status?.limits.forkExtend.max ?? 640
  return Math.min(reported, cap)
})
const extStep = computed(() => consoleStore.status?.limits.forkExtend.step ?? 50)
const liftTarget = ref(0)
const extTarget = ref(0)
watch(height, (v) => (liftTarget.value = v), { immediate: true })
watch(reach, (v) => (extTarget.value = v), { immediate: true })

async function send(body: { axis: 'lift' | 'extend'; mode: 'step' | 'target'; delta?: number; pos?: number }, confirm = false) {
  if (props.disabled) return
  try {
    await consoleFork({ ...body, confirm })
  } catch (e: any) {
    if (e?.response?.data?.error === 'confirm_required') {
      const current = e.response.data.current
      const target = e.response.data.target
      try {
        await ElMessageBox.confirm(`目标 ${target} mm，当前 ${current} mm，确认执行？`, '货叉确认', {
          type: 'warning',
          confirmButtonText: '确定',
          cancelButtonText: '取消'
        })
      } catch {
        return
      }
      try {
        await consoleFork({ ...body, confirm: true })
      } catch (err: any) {
        ElMessage.error(consoleErrorMessage(err))
      }
      return
    }
    ElMessage.error(consoleErrorMessage(e))
  }
}
function step(axis: 'lift' | 'extend', delta: number) {
  return send({ axis, mode: 'step', delta })
}
function go(axis: 'lift' | 'extend', pos: number) {
  return send({ axis, mode: 'target', pos })
}
</script>

<style scoped>
.fork h3 {
  margin: 0 0 8px;
  font-size: 14px;
  color: var(--hud-accent, #b6ff3b);
}
.axis {
  margin-bottom: 12px;
}
.axis header {
  display: flex;
  justify-content: space-between;
  margin-bottom: 6px;
}
.ops {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}
</style>
