<template>
  <div class="status-panel">
    <section>
      <div class="group">SYSTEM · 运行状态</div>
      <div class="kv-grid">
        <span>设备名称</span><b>{{ store.name || '-' }}</b>
        <span>IP 地址</span><b>{{ store.ip || '-' }}</b>
        <span>车型</span><b>{{ store.robotType || '-' }}</b>
        <span>地图</span><b>{{ store.mapName || '-' }}</b>
        <span>模式/状态</span><b>{{ store.status || '-' }}</b>
        <span>告警</span><b :class="{ bad: alarmBad }">{{ alarmText }}</b>
      </div>
    </section>
    <section>
      <div class="group">TELEMETRY · 实时数据</div>
      <div class="kv-grid">
        <span>电量</span><b>{{ store.battery }}%</b>
        <span>定位分</span><b>{{ store.score }}</b>
        <span>充电</span><b>{{ store.charging ? '是' : '否' }}</b>
        <span>电机</span><b>{{ store.motor ? '使能' : '断开' }}</b>
        <span>X (mm)</span><b>{{ store.pose[0].toFixed(0) }}</b>
        <span>Y (mm)</span><b>{{ store.pose[1].toFixed(0) }}</b>
        <span>θ (°)</span><b>{{ store.pose[2].toFixed(1) }}</b>
        <span>V (mm/s)</span><b>{{ store.vel[0].toFixed(0) }}</b>
        <span>W</span><b>{{ store.vel[1].toFixed(1) }}</b>
        <span>安全模式</span><b>{{ store.safe ? '开' : '关' }}</b>
      </div>
    </section>
    <section v-if="hasFork">
      <div class="group">FORK · 叉车信息</div>
      <div class="kv-grid">
        <span>叉高 (mm)</span><b>{{ store.forkInfo.fork_height ?? '-' }}</b>
        <span>最高 (mm)</span><b>{{ store.forkInfo.fork_up ?? '-' }}</b>
        <span>挡板</span><b>{{ store.forkInfo.flap ? '启用' : '关闭' }}</b>
        <span>控制权</span><b>{{ store.forkInfo.ctrl_flag ? '有' : '无' }}</b>
      </div>
    </section>
    <section>
      <div class="group">IO</div>
      <div class="io-row">
        <span class="io-label">输入</span>
        <span v-for="(bit, i) in store.io.input" :key="'in' + i" class="io-bit" :class="{ on: bit }">{{ i }}</span>
      </div>
      <div class="io-row">
        <span class="io-label">输出</span>
        <span v-for="(bit, i) in store.io.output" :key="'out' + i" class="io-bit" :class="{ on: bit }">{{ i }}</span>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRobotStore } from '@/stores/robot'

const store = useRobotStore()
const hasFork = computed(() => store.forkInfo && Object.keys(store.forkInfo).length > 0)
const alarmBad = computed(() => !!store.alarm && store.alarm !== 'normal')
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
</script>

<style scoped>
.status-panel { display: flex; flex-direction: column; gap: 10px; }
.group {
  font-family: var(--hud-mono);
  font-size: 11px;
  letter-spacing: 0.08em;
  color: var(--hud-accent);
  margin-bottom: 4px;
}
.kv-grid {
  display: grid;
  grid-template-columns: 72px 1fr 72px 1fr;
  gap: 3px 8px;
  font-size: 12px;
}
.kv-grid span { color: var(--hud-muted); }
.kv-grid b { font-family: var(--hud-mono); font-weight: 600; color: var(--hud-text); }
.kv-grid b.bad { color: var(--hud-danger); }
.io-row { display: flex; align-items: center; flex-wrap: wrap; gap: 4px; margin-bottom: 4px; }
.io-label { width: 36px; color: var(--hud-muted); font-size: 12px; }
.io-bit {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  font-size: 10px;
  border: 1px solid var(--hud-line);
  color: var(--hud-muted);
}
.io-bit.on { background: var(--hud-accent); color: #06100a; border-color: var(--hud-accent); }
</style>
