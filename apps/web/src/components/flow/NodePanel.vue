<template>
  <div class="node-panel">
    <template v-if="node">
      <div class="head">
        <span class="title">{{ typeLabel(node.data.taskType) }}</span>
        <span class="nid">{{ node.id }}</span>
      </div>
      <el-form label-position="top" size="small" :disabled="disabled">
        <el-form-item v-for="(spec, key) in paramSpecs" :key="key">
          <template #label>
            <span>
              <span v-if="spec.required" class="req">*</span>
              {{ key }}
              <el-tag v-if="spec.safety" type="warning" size="small" class="safety">安全关键</el-tag>
            </span>
          </template>
          <el-input-number
            v-if="spec.type === 'number'"
            :model-value="numValue(key)"
            :placeholder="spec.ask || ''"
            :controls="false"
            style="width: 100%"
            @update:model-value="(v: any) => setParam(key, v)"
          />
          <el-switch
            v-else-if="spec.type === 'bool'"
            :model-value="boolValue(key)"
            @update:model-value="(v: any) => setParam(key, v)"
          />
          <el-input
            v-else
            :model-value="strValue(key)"
            :placeholder="spec.ask || ''"
            @update:model-value="(v: any) => setParam(key, v)"
          />
          <div v-if="spec.ask" class="ask">追问话术：{{ spec.ask }}</div>
        </el-form-item>
      </el-form>
    </template>
    <el-empty v-else description="选中节点编辑参数" :image-size="60" />
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { NodeSchema } from '@/api/flows'

const props = defineProps<{
  node: any | null
  schemas: Record<string, NodeSchema>
  disabled: boolean
}>()
const emit = defineEmits<{ (e: 'update-params', nodeId: string, params: Record<string, any>): void }>()

const TYPE_LABELS: Record<string, string> = {
  focklift: '货叉升降',
  head: '原地旋转',
  follow_back: '点到点/盲叉',
  get_pallet: '栈板识别取货',
  charge: '自动充电',
  drive: '定时点动'
}

function typeLabel(t: string) {
  return TYPE_LABELS[t] || t
}

const paramSpecs = computed(() => {
  if (!props.node) return {}
  return props.schemas[props.node.data.taskType]?.params || {}
})

function cur(): Record<string, any> {
  return (props.node?.data?.params as Record<string, any>) || {}
}

function numValue(key: string) {
  const v = cur()[key]
  return typeof v === 'number' ? v : v === undefined || v === null || v === '' ? undefined : Number(v)
}
function boolValue(key: string) {
  return !!cur()[key]
}
function strValue(key: string) {
  const v = cur()[key]
  return v === undefined || v === null ? '' : String(v)
}

function setParam(key: string, value: any) {
  if (!props.node) return
  emit('update-params', props.node.id, { ...cur(), [key]: value })
}
</script>

<style scoped>
.node-panel {
  padding: 8px;
  font-size: 12px;
}
.head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}
.title {
  font-weight: 700;
  font-size: 14px;
}
.nid {
  color: #9ca3af;
  font-size: 11px;
}
.req {
  color: #dc2626;
  margin-right: 2px;
}
.safety {
  margin-left: 6px;
}
.ask {
  color: #9ca3af;
  font-size: 11px;
  margin-top: 2px;
}
</style>
