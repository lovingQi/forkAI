<template>
  <div class="status-panel">
    <el-descriptions title="运行状态" :column="1" border size="small">
      <el-descriptions-item label="设备名称">{{ store.name || '-' }}</el-descriptions-item>
      <el-descriptions-item label="IP 地址">{{ store.ip || '-' }}</el-descriptions-item>
      <el-descriptions-item label="车型">{{ store.robotType || '-' }}</el-descriptions-item>
      <el-descriptions-item label="地图">{{ store.mapName || '-' }}</el-descriptions-item>
      <el-descriptions-item label="模式/状态">{{ store.status || '-' }}</el-descriptions-item>
      <el-descriptions-item label="告警">
        <el-tag :type="alarmType" size="small" effect="dark">{{ alarmText }}</el-tag>
      </el-descriptions-item>
    </el-descriptions>

    <el-descriptions title="实时数据" :column="2" border size="small" class="mt">
      <el-descriptions-item label="电量">{{ store.battery }}%</el-descriptions-item>
      <el-descriptions-item label="定位分">{{ store.score }}</el-descriptions-item>
      <el-descriptions-item label="充电">
        <el-tag :type="store.charging ? 'success' : 'info'" size="small">{{ store.charging ? '是' : '否' }}</el-tag>
      </el-descriptions-item>
      <el-descriptions-item label="电机">
        <el-tag :type="store.motor ? 'success' : 'danger'" size="small">{{ store.motor ? '使能' : '断开' }}</el-tag>
      </el-descriptions-item>
      <el-descriptions-item label="X (mm)">{{ store.pose[0].toFixed(0) }}</el-descriptions-item>
      <el-descriptions-item label="Y (mm)">{{ store.pose[1].toFixed(0) }}</el-descriptions-item>
      <el-descriptions-item label="θ (°)">{{ store.pose[2].toFixed(1) }}</el-descriptions-item>
      <el-descriptions-item label="V (mm/s)">{{ store.vel[0].toFixed(0) }}</el-descriptions-item>
      <el-descriptions-item label="W">{{ store.vel[1].toFixed(1) }}</el-descriptions-item>
      <el-descriptions-item label="安全模式">
        <el-tag :type="store.safe ? 'warning' : 'info'" size="small">{{ store.safe ? '开' : '关' }}</el-tag>
      </el-descriptions-item>
    </el-descriptions>

    <el-descriptions v-if="hasFork" title="叉车信息" :column="2" border size="small" class="mt">
      <el-descriptions-item label="叉高 (mm)">{{ store.forkInfo.fork_height ?? '-' }}</el-descriptions-item>
      <el-descriptions-item label="最高 (mm)">{{ store.forkInfo.fork_up ?? '-' }}</el-descriptions-item>
      <el-descriptions-item label="挡板">{{ store.forkInfo.flap ? '启用' : '关闭' }}</el-descriptions-item>
      <el-descriptions-item label="控制权">{{ store.forkInfo.ctrl_flag ? '有' : '无' }}</el-descriptions-item>
    </el-descriptions>

    <div class="mt">
      <div class="io-title">IO 状态</div>
      <div class="io-row">
        <span class="io-label">输入</span>
        <span v-for="(b, i) in store.io.input" :key="'in' + i" class="io-bit" :class="{ on: b }">{{ i }}</span>
      </div>
      <div class="io-row">
        <span class="io-label">输出</span>
        <span v-for="(b, i) in store.io.output" :key="'out' + i" class="io-bit" :class="{ on: b }">{{ i }}</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRobotStore } from '@/stores/robot'

const store = useRobotStore()

const hasFork = computed(() => store.forkInfo && Object.keys(store.forkInfo).length > 0)

const alarmText = computed(() => {
  switch (store.alarm) {
    case 'estop':
      return '急停'
    case 'lost':
      return '定位丢失'
    case 'stuck':
      return '受困'
    case 'normal':
      return '正常'
    default:
      return store.alarm || '正常'
  }
})

const alarmType = computed(() => (store.alarm === 'normal' || !store.alarm ? 'success' : 'danger'))
</script>

<style scoped>
.status-panel {
  height: 100%;
  overflow-y: auto;
}
.mt {
  margin-top: 14px;
}
.io-title {
  font-size: 14px;
  font-weight: 600;
  margin-bottom: 8px;
  color: #374151;
}
.io-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
  margin-bottom: 6px;
}
.io-label {
  width: 36px;
  color: #6b7280;
  font-size: 13px;
}
.io-bit {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  font-size: 11px;
  border-radius: 4px;
  background: #e5e7eb;
  color: #9ca3af;
}
.io-bit.on {
  background: #22c55e;
  color: #fff;
}
</style>
