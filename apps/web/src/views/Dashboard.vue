<template>
  <div class="dashboard">
    <div class="dash-map" :class="{ 'hud-on': trace.hudVisible }">
      <CanvasView :show-map="true" />
    </div>
    <template v-if="trace.hudVisible">
      <div class="hud-left">
        <MissionTimeline class="grow" />
        <HudFrame title="STATUS" sub="车况" collapsible class="status-frame">
          <StatusPanel />
        </HudFrame>
      </div>
      <div class="hud-right">
        <TracePanel />
      </div>
      <div class="hud-caption">
        <NowCaption />
      </div>
    </template>
    <div class="hud-bottom">
      <HudFrame title="VOICE" sub="语音控制" class="voice-frame">
        <VoiceBar />
      </HudFrame>
      <ModelPanel />
    </div>
  </div>
</template>

<script setup lang="ts">
import CanvasView from '@/components/CanvasView.vue'
import StatusPanel from '@/components/StatusPanel.vue'
import VoiceBar from '@/components/VoiceBar.vue'
import HudFrame from '@/components/hud/HudFrame.vue'
import MissionTimeline from '@/components/hud/MissionTimeline.vue'
import ModelPanel from '@/components/hud/ModelPanel.vue'
import NowCaption from '@/components/hud/NowCaption.vue'
import TracePanel from '@/components/hud/TracePanel.vue'
import { useTraceStore } from '@/stores/trace'

const trace = useTraceStore()
</script>

<style scoped>
.dashboard {
  position: relative;
  height: 100%;
  min-height: 0;
  overflow: hidden;
}
.dash-map {
  position: absolute;
  inset: 0;
}
.dash-map.hud-on :deep(.canvas-toolbar) {
  top: 16px;
  right: 492px;
  left: auto;
}
.dash-map.hud-on :deep(.canvas-legend) {
  left: 452px;
  bottom: 270px;
}
.hud-left,
.hud-right {
  position: absolute;
  top: 16px;
  bottom: 212px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-height: 0;
  z-index: 2;
}
.hud-left { left: 16px; width: 420px; }
.hud-right { right: 16px; width: 460px; }
.grow { flex: 1; min-height: 0; }
.status-frame { max-height: 300px; flex: 0 1 auto; }
.hud-caption {
  position: absolute;
  left: 452px;
  right: 492px;
  bottom: 224px;
  z-index: 2;
}
.hud-bottom {
  position: absolute;
  left: 16px;
  right: 16px;
  bottom: 16px;
  height: 180px;
  display: grid;
  grid-template-columns: 1fr 720px;
  gap: 10px;
  z-index: 3;
}
.voice-frame,
.hud-bottom :deep(.model-frame) {
  height: 100%;
  min-height: 0;
}
@media (max-width: 1599px) {
  .hud-left { width: 360px; }
  .hud-right { width: 400px; }
  .dash-map.hud-on :deep(.canvas-toolbar) { right: 432px; }
  .dash-map.hud-on :deep(.canvas-legend) { left: 392px; }
  .hud-caption { left: 392px; right: 432px; }
  .hud-bottom { grid-template-columns: 1fr 600px; }
}
@media (max-width: 900px) {
  .hud-left,
  .hud-right,
  .hud-caption { display: none; }
  .hud-bottom { grid-template-columns: 1fr; height: auto; max-height: 46%; }
}
</style>
