<template>
  <div class="dashboard">
    <el-row :gutter="16" class="dash-row">
      <el-col :span="16">
        <el-card shadow="never" class="canvas-card" body-class="canvas-card-body">
          <CanvasView :show-map="true" />
        </el-card>
      </el-col>
      <el-col :span="8" class="side">
        <el-card shadow="never" class="status-card">
          <StatusPanel />
        </el-card>
        <el-card shadow="never" class="voice-card">
          <template #header>语音控制</template>
          <VoiceBar />
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import CanvasView from '@/components/CanvasView.vue'
import StatusPanel from '@/components/StatusPanel.vue'
import VoiceBar from '@/components/VoiceBar.vue'
</script>

<style scoped>
.dashboard,
.dash-row {
  height: 100%;
  min-height: 0;
}
.dash-row :deep(.el-col) {
  height: 100%;
  min-height: 0;
}
.side {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.canvas-card {
  height: 100%;
  min-height: 0;
}
.canvas-card :deep(.el-card__body) {
  height: 100%;
  padding: 8px;
}
.status-card {
  flex: 1;
  min-height: 0;
  overflow: hidden;
}
.status-card :deep(.el-card__body) {
  height: 100%;
  overflow-y: auto;
}
.voice-card {
  flex: 0 0 auto;
}
</style>
