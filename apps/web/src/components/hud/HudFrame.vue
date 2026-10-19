<template>
  <section class="hud-frame" :class="{ glow, collapsed }">
    <i class="c c-tl" /><i class="c c-tr" /><i class="c c-bl" /><i class="c c-br" />
    <header class="hud-frame-h">
      <div class="titles">
        <span class="en">{{ title }}</span>
        <span v-if="sub" class="zh">{{ sub }}</span>
      </div>
      <div class="actions">
        <slot name="actions" />
      </div>
      <button v-if="collapsible" class="fold" type="button" @click="collapsed = !collapsed">
        {{ collapsed ? '展开' : '收起' }}
      </button>
    </header>
    <div v-show="!collapsed" class="hud-frame-b">
      <slot />
    </div>
  </section>
</template>

<script setup lang="ts">
import { ref } from 'vue'

defineProps<{ title: string; sub?: string; glow?: boolean; collapsible?: boolean }>()
const collapsed = ref(false)
</script>

<style scoped>
.hud-frame {
  position: relative;
  display: flex;
  flex-direction: column;
  min-height: 0;
  background: var(--hud-panel);
  border: 1px solid var(--hud-line);
  backdrop-filter: blur(6px);
  box-shadow: inset 0 0 0 1px rgba(0, 0, 0, 0.35);
}
.hud-frame.glow {
  border-color: var(--hud-line-strong);
  box-shadow: 0 0 18px rgba(182, 255, 59, 0.16);
}
.c {
  position: absolute;
  width: 14px;
  height: 14px;
  border: 2px solid var(--hud-accent);
  pointer-events: none;
  z-index: 2;
}
.c-tl { top: -1px; left: -1px; border-right: 0; border-bottom: 0; }
.c-tr { top: -1px; right: -1px; border-left: 0; border-bottom: 0; }
.c-bl { bottom: -1px; left: -1px; border-right: 0; border-top: 0; }
.c-br { bottom: -1px; right: -1px; border-left: 0; border-top: 0; }
.hud-frame-h {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px 6px;
  border-bottom: 1px solid var(--hud-line);
}
.collapsed .hud-frame-h { border-bottom: 0; }
.titles { display: flex; align-items: baseline; gap: 8px; min-width: 0; }
.en {
  font-family: var(--hud-mono);
  font-size: 12px;
  letter-spacing: 0.12em;
  color: var(--hud-accent);
  font-weight: 700;
}
.zh { font-size: 12px; color: var(--hud-muted); }
.actions { margin-left: auto; display: flex; align-items: center; gap: 6px; }
.fold {
  border: 1px solid var(--hud-line);
  background: transparent;
  color: var(--hud-muted);
  font-size: 11px;
  padding: 2px 6px;
  cursor: pointer;
}
.hud-frame-b {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 8px 10px 10px;
}
</style>
