<template>
  <div class="editor" data-testid="map-editor" @keydown="onKey">
    <canvas
      ref="canvas"
      data-testid="editor-canvas"
      @pointerdown="onDown"
      @pointermove="onMove"
      @pointerup="onUp"
      @wheel.prevent="onWheel"
    ></canvas>
    <div v-if="hoverText" class="hover" data-testid="hover-label" :style="{ left: `${hoverAt.x + 12}px`, top: `${hoverAt.y + 12}px` }">{{ hoverText }}</div>
    <aside>
      <p class="state" data-testid="save-state">{{ saveLabel }}</p>
      <div class="tools" data-testid="toolbar">
        <template v-if="toolbarOpen">
          <button v-for="item in tools" :key="item.id" type="button" :data-testid="`tool-${item.id}`" :class="{ on: tool === item.id }" @click="chooseTool(item.id)">{{ item.label }}</button>
          <button type="button" data-testid="tool-transform" :class="{ on: transformOpen }" @click="openTransform">整图变换</button>
          <button type="button" data-testid="tool-align" @click="onAlign">对齐到第一象限</button>
          <button type="button" data-testid="tool-collapse" @click="collapse">收起</button>
          <button v-for="group in addGroups" :key="group" type="button" :data-testid="`add-${group}`" :class="{ on: pending === group }" @click="pending = pending === group ? '' : group; tool = 'pointer'">{{ label(group) }}</button>
        </template>
        <button v-else type="button" data-testid="tool-expand" @click="toolbarOpen = true">展开</button>
        <button type="button" data-testid="tool-undo" @click="run(() => session?.undo())">撤销</button>
        <button type="button" data-testid="tool-redo" @click="run(() => session?.redo())">重做</button>
        <label>直径 <input data-testid="diameter" type="number" :step="stepMm" :min="stepMm" :max="2000" v-model.number="diameter" @change="snapDiameter" /></label>
        <button type="button" data-testid="layer-all-on" @click="setLayers(true)">全部显示</button>
        <button type="button" data-testid="layer-all-off" @click="setLayers(false)">全部隐藏</button>
        <label v-for="group in addGroups" :key="`layer-${group}`"><input type="checkbox" :data-testid="`layer-${group}`" :checked="layers[group] !== false" @change="toggleLayer(group, $event)" />{{ label(group) }}</label>
      </div>
      <form v-if="transformOpen" class="props" data-testid="transform-form" @submit.prevent="applyTransform">
        <label>X<input data-testid="transform-x" v-model="tx" /></label>
        <label>Y<input data-testid="transform-y" v-model="ty" /></label>
        <label>朝向<input data-testid="transform-yaw" v-model="yawDeg" /></label>
        <button type="submit" data-testid="transform-apply">应用</button>
        <button type="button" @click="transformOpen = false">取消</button>
      </form>
      <form v-if="selected" class="props" data-testid="property-panel" @submit.prevent="applyProps">
        <p>{{ label(selected.group) }}</p>
        <label>名称<input data-testid="prop-name" v-model="draft.name" /></label>
        <label>X<input data-testid="prop-x" v-model="draft.x" /></label>
        <label>Y<input data-testid="prop-y" v-model="draft.y" /></label>
        <label v-if="selected.kind !== 'feature-line' && selected.kind !== 'zone'">朝向<input data-testid="prop-yaw" v-model="draft.yaw" :disabled="selected.kind === 'reflector' || selected.kind === 'pole'" /></label>
        <template v-if="selected.kind === 'zone'">
          <label>角点2 X<input data-testid="prop-x2" v-model="draft.x2" /></label>
          <label>角点2 Y<input data-testid="prop-y2" v-model="draft.y2" /></label>
        </template>
        <label v-if="selected.kind === 'goal'"><input data-testid="prop-pass" type="checkbox" v-model="draft.pass" />允许穿过</label>
        <div v-if="selected.kind === 'path'" data-testid="connections">
          <p>路径连接</p>
          <div v-for="(conn, index) in draft.connections" :key="index">
            <input :data-testid="`conn-name-${index}`" v-model="conn.name" placeholder="目标" />
            <input :data-testid="`conn-cost-${index}`" v-model="conn.cost" placeholder="cost" />
            <input :data-testid="`conn-posture-${index}`" v-model="conn.posture" placeholder="posture" />
            <input :data-testid="`conn-type-${index}`" v-model="conn.type" placeholder="type" />
            <input :data-testid="`conn-x1-${index}`" v-model="conn.x1" placeholder="x1" />
            <input :data-testid="`conn-y1-${index}`" v-model="conn.y1" placeholder="y1" />
            <input :data-testid="`conn-x2-${index}`" v-model="conn.x2" placeholder="x2" />
            <input :data-testid="`conn-y2-${index}`" v-model="conn.y2" placeholder="y2" />
          </div>
          <button type="button" data-testid="conn-add" @click="draft.connections.push({ name: '', cost: '', posture: '', type: '', x1: '', y1: '', x2: '', y2: '' })">添加连接</button>
        </div>
        <button type="submit" data-testid="prop-apply">更新</button>
        <button type="button" data-testid="prop-locate" @click="locate">定位</button>
        <button type="button" data-testid="prop-delete" @click="removeSelected">删除</button>
      </form>
      <p v-if="message" class="msg" data-testid="editor-message">{{ message }}</p>
      <button type="button" data-testid="save-button" :disabled="saveState === 'saving'" @click="save">{{ saveLabel }}</button>
      <button type="button" data-testid="editor-back" @click="back">返回</button>
    </aside>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { ElMessageBox } from 'element-plus'
import { saveMapRevision } from '@/api/mapping'
import { groupLabel, type Pt, type Sem } from '@/editor/umclObjects'
import { ALL_ADD_GROUPS, MapEditorSession, objectCenter } from '@/editor/mapEditorSession'

const props = defineProps<{ projectId: string; name: string; map: Record<string, any>; transform: { txMm: number; tyMm: number; yawRad: number } }>()
const emit = defineEmits<{ saved: [project: { projectId: string; name: string }]; back: [] }>()

const canvas = ref<HTMLCanvasElement | null>(null)
const session = ref<MapEditorSession | null>(null)
const tool = ref('pointer')
const toolbarOpen = ref(true)
const transformOpen = ref(false)
const pending = ref('')
const diameter = ref(600)
const layers = reactive<Record<string, boolean>>({})
const selectedId = ref('')
const hoverText = ref('')
const hoverAt = reactive({ x: 0, y: 0 })
const message = ref('')
const saveState = ref<'saved' | 'unsaved' | 'saving'>('saved')
const tx = ref('0')
const ty = ref('0')
const yawDeg = ref('0')
const draft = reactive({ name: '', x: '', y: '', yaw: '', x2: '', y2: '', pass: false, connections: [] as { name: string; cost: string; posture: string; type: string; x1: string; y1: string; x2: string; y2: string }[] })
const view = { cx: 0, cy: 0, scale: 0.2 }
const tools = [
  { id: 'pointer', label: '浏览' },
  { id: 'box', label: '矩形删除' },
  { id: 'eraser', label: '橡皮' },
  { id: 'brush', label: '画障碍' },
  { id: 'line', label: '画墙' }
]
const addGroups = ALL_ADD_GROUPS
const stepMm = computed(() => session.value?.resolution || 50)
const selected = computed(() => session.value?.objects.find((object) => object.uid === selectedId.value))
const saveLabel = computed(() => saveState.value === 'saving' ? '保存中' : saveState.value === 'unsaved' ? '未保存' : '已保存')
let drag: { kind: string; start: Pt; last: Pt; path: Pt[]; handle?: string } | null = null
let lastPick = ''

function label(group: string) { return groupLabel(group) }
function chooseTool(id: string) {
  tool.value = id
  pending.value = ''
  transformOpen.value = false
}
function collapse() {
  toolbarOpen.value = false
  tool.value = 'pointer'
  transformOpen.value = false
  pending.value = ''
}
function toggleLayer(group: string, ev: Event) {
  layers[group] = (ev.target as HTMLInputElement).checked
  draw()
}
function setLayers(visible: boolean) {
  for (const group of addGroups) layers[group] = visible
  draw()
}
function snapDiameter() {
  const step = stepMm.value
  diameter.value = Math.min(2000, Math.max(step, Math.round(diameter.value / step) * step))
}
function run(action: () => boolean | void | undefined) {
  action()
  saveState.value = session.value?.dirty ? 'unsaved' : saveState.value
  draw()
}
function visible(object: Sem) { return layers[object.group] !== false }
function worldFromEvent(ev: PointerEvent): Pt {
  const rect = canvas.value!.getBoundingClientRect()
  const sx = ev.clientX - rect.left
  const sy = ev.clientY - rect.top
  return { x: (sx - rect.width / 2) / view.scale + view.cx, y: (rect.height / 2 - sy) / view.scale + view.cy }
}
function screenFromWorld(x: number, y: number, width: number, height: number) {
  return [width / 2 + (x - view.cx) * view.scale, height / 2 - (y - view.cy) * view.scale]
}
function onDown(ev: PointerEvent) {
  const world = worldFromEvent(ev)
  drag = { kind: tool.value, start: world, last: world, path: [world] }
  if (tool.value === 'pointer') {
    if (pending.value) {
      const uid = session.value?.addObject(pending.value, world) || ''
      selectedId.value = uid
      pending.value = ''
      saveState.value = 'unsaved'
      syncDraft()
      draw()
      return
    }
    const found = session.value?.pick(world, 12 / view.scale, lastPick, visible)
    const handle = hitHandle(world)
    // 控制柄优先于当前选中对象的平移，但重叠轮选时要让位给下一个对象
    if (handle && (!found || found.uid === selectedId.value)) {
      drag.kind = 'handle'
      drag.handle = handle
      session.value?.beginObjectDrag()
      return
    }
    lastPick = found?.uid || ''
    selectedId.value = found?.uid || ''
    if (found) {
      drag.kind = 'move'
      session.value?.beginObjectDrag()
    }
    syncDraft()
  }
  draw()
}
function onMove(ev: PointerEvent) {
  const world = worldFromEvent(ev)
  const rect = canvas.value!.getBoundingClientRect()
  hoverAt.x = ev.clientX - rect.left
  hoverAt.y = ev.clientY - rect.top
  if (tool.value === 'pointer' && !drag) {
    const found = session.value?.pick(world, 12 / view.scale, undefined, visible)
    hoverText.value = found ? `${label(found.group)} ${found.name}` : ''
  } else hoverText.value = ''
  if (!drag || !session.value) return
  if (drag.kind === 'pointer') {
    view.cx -= ev.movementX / view.scale
    view.cy += ev.movementY / view.scale
    draw()
    return
  }
  if (drag.kind === 'move') {
    moveSelection(world.x - drag.last.x, world.y - drag.last.y, 'body')
    drag.last = world
  } else if (drag.kind === 'handle' && drag.handle) {
    moveSelection(world.x - drag.last.x, world.y - drag.last.y, drag.handle)
    drag.last = world
  } else if (drag.kind === 'eraser' || drag.kind === 'brush') {
    drag.path.push(world)
  }
  drag.last = world
  draw()
}
function onUp() {
  if (!drag || !session.value) return
  const current = drag
  drag = null
  if (current.kind === 'box') session.value.deleteInRect(current.start, current.last)
  if (current.kind === 'eraser') {
    const result = session.value.erase(current.path, diameter.value)
    message.value = result.message
  }
  if (current.kind === 'brush' || current.kind === 'line') {
    const result = session.value.paint(current.kind === 'line' ? [current.start, current.last] : current.path, diameter.value, current.kind === 'line' ? 'line' : 'paint')
    message.value = result.message
  }
  if (current.kind === 'move' || current.kind === 'handle') session.value.endObjectDrag()
  if (session.value.dirty) saveState.value = 'unsaved'
  draw()
}
function onWheel(ev: WheelEvent) {
  view.scale = Math.max(0.02, Math.min(5, view.scale * (ev.deltaY > 0 ? 0.9 : 1.1)))
  draw()
}
function hitHandle(world: Pt) {
  const object = selected.value
  if (!object) return ''
  const tolerance = 14 / view.scale
  const near = (point?: Pt) => !!point && Math.hypot(point.x - world.x, point.y - world.y) <= tolerance
  if (object.kind === 'feature-line') {
    if (near(object.start)) return 'start'
    if (near(object.end)) return 'end'
  }
  if (object.kind === 'zone') {
    if (near(object.min)) return 'min'
    if (near(object.max)) return 'max'
  }
  return ''
}
function moveSelection(dx: number, dy: number, handle: string) {
  const object = session.value?.objects.find((item) => item.uid === selectedId.value)
  if (!object) return
  if (handle === 'start' && object.start) { object.start = { x: object.start.x + dx, y: object.start.y + dy }; return }
  if (handle === 'end' && object.end) { object.end = { x: object.end.x + dx, y: object.end.y + dy }; return }
  if (handle === 'min' && object.min) { object.min = { x: object.min.x + dx, y: object.min.y + dy }; return }
  if (handle === 'max' && object.max) { object.max = { x: object.max.x + dx, y: object.max.y + dy }; return }
  if (object.position && object.kind !== 'zone') object.position = { x: object.position.x + dx, y: object.position.y + dy }
  if (object.kind === 'feature-line' && object.start && object.end && handle === 'body') {
    object.start = { x: object.start.x + dx, y: object.start.y + dy }
    object.end = { x: object.end.x + dx, y: object.end.y + dy }
  }
  if (object.kind === 'zone' && object.min && object.max && handle === 'body') {
    object.min = { x: object.min.x + dx, y: object.min.y + dy }
    object.max = { x: object.max.x + dx, y: object.max.y + dy }
  }
}
function syncDraft() {
  const object = selected.value
  if (!object) return
  const center = objectCenter(object)
  draft.name = object.name
  draft.x = String(Math.round(center.x))
  draft.y = String(Math.round(center.y))
  draft.yaw = String(object.yaw || 0)
  draft.x2 = String(Math.round(object.max?.x || object.end?.x || center.x))
  draft.y2 = String(Math.round(object.max?.y || object.end?.y || center.y))
  draft.pass = !!object.allowPassingThrough
  draft.connections = (object.connections || []).map((conn) => ({
    name: conn.name,
    cost: num(conn.cost),
    posture: conn.posture || '',
    type: num(conn.type),
    x1: num(conn.x1),
    y1: num(conn.y1),
    x2: num(conn.x2),
    y2: num(conn.y2),
  }))
}
function num(value?: number) { return value === undefined ? '' : String(value) }
function applyProps() {
  const object = selected.value
  if (!object || !session.value) return
  const x = Number(draft.x)
  const y = Number(draft.y)
  const patch: Partial<Sem> = { name: draft.name }
  if (object.kind === 'feature-line' && object.start && object.end) {
    const cx = (object.start.x + object.end.x) / 2
    const cy = (object.start.y + object.end.y) / 2
    patch.start = { x: object.start.x + x - cx, y: object.start.y + y - cy }
    patch.end = { x: object.end.x + x - cx, y: object.end.y + y - cy }
  } else if (object.kind === 'zone') {
    patch.min = { x: Math.min(x, Number(draft.x2)), y: Math.min(y, Number(draft.y2)) }
    patch.max = { x: Math.max(x, Number(draft.x2)), y: Math.max(y, Number(draft.y2)) }
  } else {
    patch.position = { x, y }
    if (object.kind === 'qr-code' || object.kind === 'dock' || object.kind === 'goal' || object.kind === 'path') patch.yaw = Number(draft.yaw)
  }
  if (object.kind === 'goal') patch.allowPassingThrough = draft.pass
  if (object.kind === 'path') {
    patch.connections = draft.connections.filter((conn) => conn.name).map((conn) => ({
      name: conn.name,
      ...(conn.cost === '' ? {} : { cost: Number(conn.cost) }),
      ...(conn.posture === '' ? {} : { posture: conn.posture }),
      ...(conn.type === '' ? {} : { type: Number(conn.type) }),
      ...(conn.x1 === '' ? {} : { x1: Number(conn.x1) }),
      ...(conn.y1 === '' ? {} : { y1: Number(conn.y1) }),
      ...(conn.x2 === '' ? {} : { x2: Number(conn.x2) }),
      ...(conn.y2 === '' ? {} : { y2: Number(conn.y2) }),
    }))
  }
  session.value.updateObject(object.uid, patch)
  saveState.value = 'unsaved'
  draw()
}
function locate() {
  const object = selected.value
  if (!object) return
  const center = objectCenter(object)
  view.cx = center.x
  view.cy = center.y
  draw()
}
function removeSelected() {
  if (!selected.value || !session.value) return
  session.value.removeObject(selected.value.uid)
  selectedId.value = ''
  saveState.value = 'unsaved'
  draw()
}
function openTransform() {
  transformOpen.value = !transformOpen.value
  tool.value = 'pointer'
}
function applyTransform() {
  const result = session.value?.transformBy(Number(tx.value), Number(ty.value), Number(yawDeg.value))
  message.value = result?.message || ''
  if (result?.ok) saveState.value = 'unsaved'
  draw()
}
function onAlign() {
  const result = session.value?.align()
  message.value = result?.message || (result?.ok ? '' : '地图已对齐，无需修改。')
  if (result?.ok) saveState.value = 'unsaved'
  draw()
}
async function save() {
  if (!session.value) return
  let name = props.name
  try {
    const answer = await ElMessageBox.prompt('新版本名称', '另存地图', { inputValue: props.name, confirmButtonText: '保存', cancelButtonText: '取消' })
    name = answer.value
  } catch { return }
  saveState.value = 'saving'
  try {
    const payload = session.value.toMap()
    const saved = await saveMapRevision(props.projectId, { name, ...payload, editLog: [{ kind: 'edit', affectedPointCount: session.value.points.length }] })
    session.value.dirty = false
    saveState.value = 'saved'
    emit('saved', saved)
  } catch (error: any) {
    saveState.value = 'unsaved'
    message.value = error?.response?.data?.error?.message || '保存失败'
  }
}
async function back() {
  if (saveState.value === 'unsaved') {
    try { await ElMessageBox.confirm('有未保存的修改。', '离开编辑', { confirmButtonText: '离开', cancelButtonText: '留下' }) }
    catch { return }
  }
  emit('back')
}
function onKey(ev: KeyboardEvent) {
  if (ev.key === 'Escape') { tool.value = 'pointer'; pending.value = ''; return }
  if (ev.key === 'Delete' && selected.value) { removeSelected(); return }
  const meta = ev.ctrlKey || ev.metaKey
  if (meta && ev.key.toLowerCase() === 'z' && ev.shiftKey) { run(() => session.value?.redo()); return }
  if (meta && ev.key.toLowerCase() === 'z') { run(() => session.value?.undo()); return }
  if (meta && ev.key.toLowerCase() === 'y') run(() => session.value?.redo())
}
function onLeave(ev: BeforeUnloadEvent) {
  if (saveState.value === 'unsaved') { ev.preventDefault(); ev.returnValue = '' }
}
function draw() {
  const el = canvas.value
  const current = session.value
  if (!el || !current) return
  const ctx = el.getContext('2d')
  if (!ctx) return
  const width = el.clientWidth
  const height = el.clientHeight
  el.width = width
  el.height = height
  ctx.fillStyle = '#10140f'
  ctx.fillRect(0, 0, width, height)
  const to = (x: number, y: number) => screenFromWorld(x, y, width, height)
  ctx.fillStyle = '#d5d8dc'
  for (const point of current.points) {
    const [sx, sy] = to(point.x, point.y)
    ctx.fillRect(sx, sy, 2, 2)
  }
  for (const object of current.objects) {
    if (!visible(object)) continue
    const active = object.uid === selectedId.value
    ctx.strokeStyle = active ? '#ffe082' : color(object.kind)
    ctx.fillStyle = color(object.kind)
    if (object.kind === 'feature-line' && object.start && object.end) {
      const [ax, ay] = to(object.start.x, object.start.y)
      const [bx, by] = to(object.end.x, object.end.y)
      ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by); ctx.stroke()
    } else if (object.kind === 'zone' && object.min && object.max) {
      const [ax, ay] = to(object.min.x, object.max.y)
      const [bx, by] = to(object.max.x, object.min.y)
      ctx.globalAlpha = 0.25
      ctx.fillRect(ax, ay, bx - ax, by - ay)
      ctx.globalAlpha = 1
      ctx.strokeRect(ax, ay, bx - ax, by - ay)
    } else if (object.position) {
      const [sx, sy] = to(object.position.x, object.position.y)
      ctx.beginPath(); ctx.arc(sx, sy, 5, 0, Math.PI * 2); ctx.stroke()
    }
  }
  if (drag?.kind === 'box') {
    const [ax, ay] = to(drag.start.x, drag.start.y)
    const [bx, by] = to(drag.last.x, drag.last.y)
    ctx.strokeStyle = '#7fd1ff'
    ctx.strokeRect(ax, ay, bx - ax, by - ay)
  }
}
function color(kind: string) {
  if (kind === 'feature-line') return '#7fd1ff'
  if (kind === 'reflector') return '#ffe082'
  if (kind === 'pole') return '#c9a0ff'
  if (kind === 'qr-code') return '#8ab4ff'
  if (kind === 'zone') return '#8dff9a'
  return '#f2f5f3'
}
function fit() {
  const current = session.value
  const el = canvas.value
  if (!current || !el || !current.points.length) return
  const xs = current.points.map((point) => point.x)
  const ys = current.points.map((point) => point.y)
  view.cx = (Math.min(...xs) + Math.max(...xs)) / 2
  view.cy = (Math.min(...ys) + Math.max(...ys)) / 2
  view.scale = Math.max(0.05, Math.min(el.clientWidth / Math.max(1, Math.max(...xs) - Math.min(...xs)), el.clientHeight / Math.max(1, Math.max(...ys) - Math.min(...ys))) * 0.8)
  draw()
}
watch(selectedId, syncDraft)
onMounted(() => {
  session.value = new MapEditorSession(props.map, { ...props.transform })
  diameter.value = Math.max(600, session.value.resolution)
  for (const group of addGroups) layers[group] = true
  window.addEventListener('keydown', onKey)
  window.addEventListener('beforeunload', onLeave)
  fit()
})
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKey)
  window.removeEventListener('beforeunload', onLeave)
})
</script>

<style scoped>
.editor { height: 100%; min-height: 0; display: grid; grid-template-columns: 1fr 320px; background: #10140f; color: #e8f5e1; }
canvas { width: 100%; height: 100%; display: block; touch-action: none; }
aside { overflow: auto; padding: 12px; display: flex; flex-direction: column; gap: 8px; border-left: 1px solid rgba(182, 255, 59, 0.22); }
.tools, .props { display: flex; flex-wrap: wrap; gap: 6px; }
button, input { background: #101712; color: inherit; border: 1px solid rgba(182, 255, 59, 0.22); min-height: 28px; }
button.on { border-color: #b6ff3b; color: #b6ff3b; }
label { display: flex; gap: 4px; align-items: center; font-size: 12px; }
.hover { position: absolute; background: #172017; padding: 4px 6px; pointer-events: none; }
.msg, .state { font-size: 12px; color: #b7c4b8; }
</style>
