<template>
  <div class="pad">
    <button
      v-for="item in cells"
      :key="item.id"
      type="button"
      class="pad-btn"
      :class="{ stop: item.stop, down: holding === item.id }"
      :disabled="disabled && !item.stop"
      @pointerdown.prevent="onDown(item, $event)"
      @pointerup="onUp"
      @pointercancel="onUp"
    >
      {{ item.label }}
    </button>
  </div>
</template>

<script setup lang="ts">
import { useHoldToRun } from '@/composables/useHoldToRun'

const props = defineProps<{ disabled?: boolean; trans: number }>()
const emit = defineEmits<{ stop: [] }>()
const { start, stop, holding } = useHoldToRun(() => props.trans)

const cells = [
  { id: 'spin_left', label: '原地左转' },
  { id: 'fwd', label: '前进' },
  { id: 'spin_right', label: '原地右转' },
  { id: 'fwd_left', label: '前进左转' },
  { id: 'stop', label: '停止', stop: true },
  { id: 'fwd_right', label: '前进右转' },
  { id: 'back_left', label: '后退左转' },
  { id: 'back', label: '后退' },
  { id: 'back_right', label: '后退右转' }
]

async function onDown(item: { id: string; stop?: boolean }, ev: PointerEvent) {
  if (item.stop) {
    await stop()
    emit('stop')
    return
  }
  if (props.disabled) return
  const el = ev.currentTarget as HTMLElement
  el.setPointerCapture?.(ev.pointerId)
  await start(item.id)
}
function onUp() {
  void stop()
}
</script>

<style scoped>
.pad {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
}
.pad-btn {
  min-height: 64px;
  border: 1px solid var(--hud-line, rgba(182, 255, 59, 0.22));
  background: #101712;
  color: var(--hud-text, #e8f5e1);
  font-size: 14px;
}
.pad-btn.down,
.pad-btn:active:not(:disabled) {
  border-color: var(--hud-accent, #b6ff3b);
  color: var(--hud-accent, #b6ff3b);
}
.pad-btn.stop {
  color: var(--hud-danger, #ff4d4f);
  font-weight: 700;
}
.pad-btn:disabled {
  opacity: 0.4;
}
</style>
