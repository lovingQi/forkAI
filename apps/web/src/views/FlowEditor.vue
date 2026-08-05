<template>
  <div class="flow-editor">
    <div class="topbar">
      <el-button size="small" @click="goBack">返回</el-button>
      <el-input
        v-model="flowName"
        placeholder="流程名（支持中文）"
        style="width: 220px"
        :disabled="engineBusy"
      />
      <el-button size="small" @click="onNew" :disabled="engineBusy">新建</el-button>
      <el-select
        v-model="selectedFlowId"
        placeholder="加载已有流程"
        size="small"
        style="width: 180px"
        :disabled="engineBusy"
        @change="onLoad"
      >
        <el-option v-for="f in flowList" :key="f.id" :label="`${f.name}（${f.nodes}节点）`" :value="f.id" />
      </el-select>
      <el-button size="small" type="primary" @click="onSave" :disabled="engineBusy">保存</el-button>
      <el-button size="small" type="danger" plain @click="onDelete" :disabled="engineBusy || !flowId">删除</el-button>
      <el-divider direction="vertical" />
      <el-button size="small" type="success" @click="onStart" :disabled="engineBusy || !flowId">执行</el-button>
      <el-button size="small" type="warning" @click="onPause" :disabled="session.flowStatus !== 'running'">暂停</el-button>
      <el-button size="small" type="warning" plain @click="onResume" :disabled="session.flowStatus !== 'paused'">继续</el-button>
      <el-button size="small" type="danger" @click="onCancel" :disabled="!engineBusy">取消</el-button>
      <el-tag :type="statusTagType" size="small" effect="dark">{{ session.flowStatus }}</el-tag>
    </div>

    <div class="body">
      <div class="palette">
        <div class="palette-title">节点</div>
        <el-button
          v-for="t in NODE_TYPES"
          :key="t.type"
          size="small"
          class="palette-btn"
          :disabled="engineBusy"
          @click="addNode(t.type)"
        >
          {{ t.label }}
        </el-button>
      </div>

      <div class="canvas">
        <VueFlow
          v-model:nodes="nodes"
          v-model:edges="edges"
          class="vue-flow"
          fit-view-on-init
          :nodes-draggable="!engineBusy"
          :nodes-connectable="!engineBusy"
          :edges-updatable="!engineBusy"
          :delete-key-code="engineBusy ? null : ['Backspace', 'Delete']"
          @connect="onConnect"
          @node-click="onNodeClick"
          @edge-click="onEdgeClick"
          @pane-click="selectedId = ''"
        >
          <Background pattern-color="#d1d5db" :gap="16" />
          <Controls />
          <template #node-task="nodeProps">
            <TaskNode v-bind="nodeProps" />
          </template>
        </VueFlow>
      </div>

      <div class="side">
        <NodePanel
          :node="selectedNode"
          :schemas="schemas"
          :disabled="engineBusy"
          @update-params="onUpdateParams"
        />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { VueFlow } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import '@vue-flow/controls/dist/style.css'
import TaskNode from '@/components/flow/TaskNode.vue'
import NodePanel from '@/components/flow/NodePanel.vue'
import {
  createFlow,
  deleteFlow,
  getEngineStatus,
  getFlow,
  getTaskSchemas,
  listFlows,
  pauseFlow,
  resumeFlow,
  cancelFlow,
  startFlow,
  updateFlow,
  type FlowDef,
  type FlowSummary,
  type NodeSchema
} from '@/api/flows'
import { useSessionStore } from '@/stores/session'

const session = useSessionStore()

const NODE_TYPES = [
  { type: 'focklift', label: '货叉升降' },
  { type: 'head', label: '原地旋转' },
  { type: 'follow_back', label: '点到点/盲叉' },
  { type: 'get_pallet', label: '栈板识别取货' },
  { type: 'charge', label: '自动充电' },
  { type: 'drive', label: '定时点动' }
]

const flowId = ref('')
const flowName = ref('')
const flowList = ref<FlowSummary[]>([])
const selectedFlowId = ref('')
const nodes = ref<any[]>([])
const edges = ref<any[]>([])
const selectedId = ref('')
const schemas = ref<Record<string, NodeSchema>>({})
let nodeSeq = 1

const engineBusy = computed(() => ['running', 'paused'].includes(session.flowStatus))
const selectedNode = computed(() => nodes.value.find((n) => n.id === selectedId.value) || null)

const statusTagType = computed(() => {
  switch (session.flowStatus) {
    case 'running': return 'primary'
    case 'paused': return 'warning'
    case 'succeeded': return 'success'
    case 'failed': return 'danger'
    default: return 'info'
  }
})

function defaultParams(type: string) {
  const out: Record<string, any> = {}
  const ps = schemas.value[type]?.params || {}
  for (const [k, spec] of Object.entries(ps)) {
    if (spec.default !== undefined) out[k] = spec.default
  }
  return out
}

function addNode(type: string) {
  const id = `n${nodeSeq++}`
  nodes.value = [
    ...nodes.value,
    {
      id,
      type: 'task',
      position: { x: 80 + (nodeSeq % 5) * 60, y: 60 + (nodeSeq % 5) * 70 },
      data: { taskType: type, label: NODE_TYPES.find((t) => t.type === type)?.label || type, params: defaultParams(type), status: 'pending' }
    }
  ]
  selectedId.value = id
}

function outEdgeCount(sourceId: string, on: string) {
  return edges.value.filter((e) => e.source === sourceId && e.data?.on === on).length
}

function onConnect(conn: any) {
  // 每节点每类出边限一条；新连线默认 on=success
  if (outEdgeCount(conn.source, 'success') >= 1) {
    ElMessage.warning('该节点已有"成功"出边，每类出边限一条')
    return
  }
  if (conn.source === conn.target) return
  edges.value = [
    ...edges.value,
    makeEdge(conn.source, conn.target, 'success')
  ]
}

function makeEdge(source: string, target: string, on: 'success' | 'fail') {
  return {
    id: `e-${source}-${target}-${on}`,
    source,
    target,
    label: on === 'success' ? '成功' : '失败',
    animated: on === 'fail',
    style: on === 'fail'
      ? { stroke: '#dc2626', strokeDasharray: '6 3' }
      : { stroke: '#16a34a' },
    labelStyle: { fill: on === 'fail' ? '#dc2626' : '#16a34a', fontSize: 11 },
    data: { on }
  }
}

function onNodeClick(ev: any) {
  selectedId.value = ev?.node?.id || ''
}

function onEdgeClick(ev: any) {
  if (engineBusy.value) return
  const edge = ev?.edge
  if (!edge) return
  const next = edge.data?.on === 'success' ? 'fail' : 'success'
  // 切换前校验：目标类别不能已有出边
  const others = edges.value.filter((e) => e.id !== edge.id && e.source === edge.source && e.data?.on === next)
  if (others.length) {
    ElMessage.warning(`该节点已有"${next === 'fail' ? '失败' : '成功'}"出边，每类限一条`)
    return
  }
  edges.value = edges.value.map((e) => (e.id === edge.id ? makeEdge(e.source, e.target, next) : e))
}

function onUpdateParams(nodeId: string, params: Record<string, any>) {
  nodes.value = nodes.value.map((n) =>
    n.id === nodeId ? { ...n, data: { ...n.data, params } } : n
  )
}

function onNew() {
  flowId.value = ''
  flowName.value = ''
  selectedFlowId.value = ''
  nodes.value = []
  edges.value = []
  selectedId.value = ''
}

async function refreshList() {
  try {
    flowList.value = await listFlows()
  } catch (e: any) {
    ElMessage.error(e?.message || '获取流程列表失败')
  }
}

async function onLoad(id: string) {
  if (!id) return
  try {
    const flow = await getFlow(id)
    applyFlow(flow)
  } catch (e: any) {
    ElMessage.error(e?.message || '加载失败')
  }
}

function applyFlow(flow: FlowDef) {
  flowId.value = flow.id || ''
  flowName.value = flow.name || ''
  const positions = flow.ui?.positions || {}
  nodes.value = (flow.nodes || []).map((n, i) => ({
    id: n.id,
    type: 'task',
    position: positions[n.id] || { x: 80 + i * 180, y: 120 },
    data: {
      taskType: n.type,
      label: NODE_TYPES.find((t) => t.type === n.type)?.label || n.type,
      params: n.params || {},
      status: 'pending'
    }
  }))
  edges.value = (flow.edges || []).map((e) => makeEdge(e.from, e.to, e.on))
  nodeSeq = nodes.value.length + 1
  selectedId.value = ''
}

async function onSave() {
  if (!flowName.value.trim()) {
    ElMessage.warning('请填写流程名')
    return
  }
  if (!nodes.value.length) {
    ElMessage.warning('至少需要一个节点')
    return
  }
  const positions: Record<string, { x: number; y: number }> = {}
  for (const n of nodes.value) positions[n.id] = { x: n.position.x, y: n.position.y }
  const flow: FlowDef = {
    name: flowName.value.trim(),
    nodes: nodes.value.map((n) => ({ id: n.id, type: n.data.taskType, params: n.data.params || {} })),
    edges: edges.value.map((e) => ({ from: e.source, to: e.target, on: e.data?.on || 'success' })),
    ui: { positions }
  }
  try {
    if (flowId.value) {
      await updateFlow(flowId.value, flow)
    } else {
      const res = await createFlow(flow)
      flowId.value = res.id
    }
    ElMessage.success('已保存')
    refreshList()
  } catch (e: any) {
    const errs = e?.response?.data?.errors
    if (Array.isArray(errs) && errs.length) {
      errs.forEach((er: string) => ElMessage.error(er))
    } else {
      ElMessage.error(e?.response?.data?.error || e?.message || '保存失败')
    }
  }
}

async function onDelete() {
  if (!flowId.value) return
  try {
    await ElMessageBox.confirm(`删除流程「${flowName.value}」？`, '确认', { type: 'warning' })
    await deleteFlow(flowId.value)
    ElMessage.success('已删除')
    onNew()
    refreshList()
  } catch {
    /* 取消 */
  }
}

async function onStart() {
  try {
    await startFlow(flowId.value)
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.error || '启动失败')
  }
}

async function onPause() {
  try {
    await pauseFlow()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.error || '暂停失败')
  }
}

async function onResume() {
  try {
    await resumeFlow()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.error || '继续失败')
  }
}

async function onCancel() {
  try {
    await cancelFlow()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.error || '取消失败')
  }
}

function goBack() {
  window.location.hash = ''
}

// 引擎状态 → 节点色环 + 终态 toast
watch(
  () => session.flowNodeStates,
  (states) => {
    nodes.value = nodes.value.map((n) =>
      states[n.id] ? { ...n, data: { ...n.data, status: states[n.id] } } : n
    )
  },
  { deep: true }
)

let lastFlowStatus = 'idle'
watch(
  () => session.flowStatus,
  (st, prev) => {
    if (['succeeded', 'failed', 'cancelled'].includes(st) && st !== lastFlowStatus) {
      const map: Record<string, string> = { succeeded: '任务流执行完成', failed: '任务流失败', cancelled: '任务流已取消' }
      ElMessage({ type: st === 'succeeded' ? 'success' : st === 'failed' ? 'error' : 'info', message: map[st] || st })
    }
    lastFlowStatus = st
  }
)

onMounted(async () => {
  session.connectEvents()
  refreshList()
  try {
    schemas.value = await getTaskSchemas()
  } catch {
    ElMessage.warning('节点参数 schema 获取失败')
  }
  try {
    const st = await getEngineStatus()
    session.flowStatus = st.flowStatus
    session.flowId = st.flowId || ''
    session.flowNodeStates = st.nodeStates || {}
  } catch {
    /* 忽略 */
  }
})

onBeforeUnmount(() => {
  /* 事件订阅由 session store 统一管理，无需单独取消 */
})
</script>

<style scoped>
.flow-editor {
  height: 100%;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.topbar {
  display: flex;
  align-items: center;
  gap: 8px;
  background: #fff;
  padding: 8px 12px;
  border-radius: 8px;
  flex-wrap: wrap;
}
.body {
  flex: 1;
  min-height: 0;
  display: flex;
  gap: 8px;
}
.palette {
  width: 120px;
  background: #fff;
  border-radius: 8px;
  padding: 8px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.palette-title {
  font-size: 12px;
  color: #6b7280;
  margin-bottom: 4px;
}
.palette-btn {
  margin-left: 0 !important;
}
.canvas {
  flex: 1;
  min-width: 0;
  background: #fff;
  border-radius: 8px;
  overflow: hidden;
}
.vue-flow {
  height: 100%;
}
.side {
  width: 260px;
  background: #fff;
  border-radius: 8px;
  overflow-y: auto;
}
</style>
