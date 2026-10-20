import { onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { consoleDrive, consoleDriveStop, consoleErrorMessage } from '@/api/console'

const HEARTBEAT_MS = 200

/** 按住行驶：心跳重发，松手、失焦、隐藏或卸载时立即停。同一时刻只有一个方向。 */
export function useHoldToRun(trans: () => number) {
  const holding = ref<string | null>(null)
  let active: string | null = null
  let timer: number | null = null

  async function sendStop() {
    try {
      await consoleDriveStop()
    } catch (e: any) {
      if (e?.response?.status && e.response.status !== 403) {
        ElMessage.error(consoleErrorMessage(e))
      }
    }
  }

  async function stop() {
    const was = active
    active = null
    holding.value = null
    if (timer !== null) {
      window.clearInterval(timer)
      timer = null
    }
    if (was) await sendStop()
  }

  async function start(direction: string) {
    if (active) await stop()
    active = direction
    holding.value = direction
    const tick = async (first: boolean) => {
      if (active !== direction) return
      try {
        await consoleDrive({ direction, trans: trans(), start: first })
      } catch (e: any) {
        ElMessage.error(consoleErrorMessage(e))
        await stop()
      }
    }
    await tick(true)
    if (active !== direction) return
    timer = window.setInterval(() => {
      void tick(false)
    }, HEARTBEAT_MS)
  }

  function onBlur() {
    void stop()
  }
  function onHide() {
    if (document.hidden) void stop()
  }

  onMounted(() => {
    window.addEventListener('blur', onBlur)
    document.addEventListener('visibilitychange', onHide)
  })
  onBeforeUnmount(() => {
    window.removeEventListener('blur', onBlur)
    document.removeEventListener('visibilitychange', onHide)
    void stop()
  })

  return { start, stop, holding }
}
