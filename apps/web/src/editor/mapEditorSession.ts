import {
  FEATURE_GROUPS,
  PLACE_GROUPS,
  ZONE_GROUPS,
  type Conn,
  type Pt,
  type Sem,
  parseUmcl,
  resolutionMm,
  serializeUmcl
} from './umclObjects'

export const MAX_POINTS = 2_000_000
export const MAX_RADIUS_MM = 2_000
export const MAX_STROKE_CELLS = 200_000

export type Rigid = { txMm: number; tyMm: number; yawRad: number }

type Command =
  | { kind: 'points'; undo: () => void; redo: () => void }
  | { kind: 'objects'; before: Sem[]; after: Sem[] }
  | { kind: 'xform'; before: Rigid; after: Rigid; undoPoints: Pt[]; redoPoints: Pt[] }

export class MapEditorSession {
  points: Pt[] = []
  objects: Sem[] = []
  transform: Rigid = { txMm: 0, tyMm: 0, yawRad: 0 }
  readonly base: Rigid
  readonly resolution: number
  readonly sourceMap: Record<string, any>
  dirty = false
  private undoStack: Command[] = []
  private redoStack: Command[] = []
  private uid = 1

  constructor(map: Record<string, any>, transform: Rigid) {
    const parsed = parseUmcl(map)
    this.points = parsed.points
    this.objects = parsed.objects
    this.sourceMap = map
    this.base = { ...transform }
    this.transform = { ...transform }
    this.resolution = resolutionMm(Number(map.MapRes) || 50)
    this.uid = this.objects.length + 1
  }

  undo() {
    const command = this.undoStack.pop()
    if (!command) return false
    this.apply(command, false)
    this.redoStack.push(command)
    this.dirty = true
    return true
  }

  redo() {
    const command = this.redoStack.pop()
    if (!command) return false
    this.apply(command, true)
    this.undoStack.push(command)
    this.dirty = true
    return true
  }

  deleteInRect(a: Pt, b: Pt) {
    const minX = Math.min(a.x, b.x)
    const maxX = Math.max(a.x, b.x)
    const minY = Math.min(a.y, b.y)
    const maxY = Math.max(a.y, b.y)
    const kept: Pt[] = []
    const removed: Pt[] = []
    for (const point of this.points) {
      if (point.x >= minX && point.x <= maxX && point.y >= minY && point.y <= maxY) removed.push(point)
      else kept.push(point)
    }
    if (!removed.length) return false
    this.pushPoints(kept, removed, true)
    return true
  }

  erase(path: Pt[], radius: number) {
    if (!validRadius(radius) || path.length === 0) return { ok: false, message: '橡皮直径无效' }
    const kept = this.points.filter((point) => !path.some((sample) => dist(point, sample) <= radius))
    if (kept.length === this.points.length) return { ok: false, message: '' }
    this.pushPoints(kept, this.points, false)
    return { ok: true, message: '' }
  }

  paint(path: Pt[], radius: number, mode: 'paint' | 'line') {
    if (!validRadius(radius) || path.length < (mode === 'line' ? 2 : 1)) return { ok: false, message: '尺寸无效' }
    const estimate = Math.ceil((pathLength(path) * radius) / (this.resolution * this.resolution)) + 1
    if (estimate > MAX_STROKE_CELLS || this.points.length + estimate > MAX_POINTS) {
      return { ok: false, message: mode === 'line' ? '这道墙会超过单笔或总点数上限' : '这一笔会超过单笔或总点数上限' }
    }
    const stamps = mode === 'line' ? rasterLine(path[0], path[path.length - 1], radius, this.resolution) : rasterPath(path, radius, this.resolution)
    const seen = new Set(this.points.map((point) => key(point)))
    const added = stamps.filter((point) => {
      const id = key(point)
      if (seen.has(id)) return false
      seen.add(id)
      return true
    })
    if (!added.length) return { ok: false, message: '' }
    if (this.points.length + added.length > MAX_POINTS) return { ok: false, message: '障碍点超过 200 万' }
    const next = this.points.concat(added)
    this.pushPoints(next, added, true)
    return { ok: true, message: '' }
  }

  transformBy(txMm: number, tyMm: number, yawDeg: number) {
    const yawRad = yawDeg * Math.PI / 180
    if (![txMm, tyMm, yawRad].every(Number.isFinite) || (txMm === 0 && tyMm === 0 && yawRad === 0)) {
      return { ok: false, message: '请输入平移或旋转' }
    }
    const after = compose({ txMm, tyMm, yawRad }, this.transform)
    if (this.hasZones() && !quarter(after.yawRad - this.base.yawRad)) {
      return { ok: false, message: '有功能区时整图旋转只能是 90° 的整数倍' }
    }
    this.applyTransform({ txMm, tyMm, yawRad }, after)
    return { ok: true, message: '' }
  }

  align() {
    if (!this.points.length) return { ok: false, message: '没有可对齐的障碍点' }
    const yawRad = longSideYaw(this.points)
    if (this.hasZones() && !quarter(yawRad)) {
      return { ok: false, message: '自动对齐需要任意角度旋转，但定位区域仅支持 90° 的整数倍旋转。请使用地图变换的 X/Y 平移。' }
    }
    const rotated = this.points.map((point) => rotate(point, yawRad))
    const minX = Math.min(...rotated.map((point) => point.x))
    const minY = Math.min(...rotated.map((point) => point.y))
    const margin = Math.max(this.resolution, 1)
    return this.transformBy(-minX + margin, -minY + margin, yawRad * 180 / Math.PI)
  }

  addObject(group: string, center: Pt) {
    const object = createObject(group, this.nextUid(), snap(center, this.resolution))
    const after = this.objects.concat(object)
    this.pushObjects(after)
    return object.uid
  }

  updateObject(uid: string, patch: Partial<Sem>) {
    const after = this.objects.map((object) => object.uid === uid ? { ...object, ...patch } : object)
    this.pushObjects(after)
  }

  removeObject(uid: string) {
    this.pushObjects(this.objects.filter((object) => object.uid !== uid))
  }

  beginObjectDrag() {
    this.dragBefore = cloneObjects(this.objects)
  }

  endObjectDrag() {
    if (!this.dragBefore) return false
    const after = cloneObjects(this.objects)
    const changed = JSON.stringify(after) !== JSON.stringify(this.dragBefore)
    this.objects = this.dragBefore
    this.dragBefore = null
    if (changed) this.pushObjects(after)
    return changed
  }

  private dragBefore: Sem[] | null = null

  pick(world: Pt, tolerance: number, previous?: string, allow: (object: Sem) => boolean = () => true): Sem | undefined {
    const handles = this.objects.filter((object) => allow(object) && nearObject(object, world, tolerance))
    const precise = handles.filter((object) => object.kind !== 'zone')
    const zones = handles.filter((object) => object.kind === 'zone').sort((a, b) => area(a) - area(b))
    const order = [...precise, ...zones]
    if (!order.length) return undefined
    if (!previous) return order[0]
    const index = order.findIndex((object) => object.uid === previous)
    return order[(index + 1) % order.length]
  }

  toMap() {
    return {
      map: serializeUmcl(this.sourceMap, this.points, this.objects, this.resolution),
      map2dFromLocalization: { ...this.transform }
    }
  }

  hasZones() {
    return this.objects.some((object) => object.kind === 'zone')
  }

  private nextUid() {
    this.uid += 1
    return `new-${this.uid}`
  }

  private pushPoints(next: Pt[], changed: Pt[], added: boolean) {
    const before = this.points
    this.points = next
    this.undoStack.push({
      kind: 'points',
      undo: () => { this.points = before },
      redo: () => { this.points = next }
    })
    this.redoStack = []
    this.dirty = true
    void changed
    void added
  }

  private pushObjects(after: Sem[]) {
    const before = cloneObjects(this.objects)
    const afterCopy = cloneObjects(after)
    this.objects = afterCopy
    this.undoStack.push({ kind: 'objects', before, after: afterCopy })
    this.redoStack = []
    this.dirty = true
  }

  private applyTransform(delta: Rigid, after: Rigid) {
    const undoPoints = this.points.map((point) => ({ ...point }))
    const beforeObjects = cloneObjects(this.objects)
    const before = { ...this.transform }
    this.points = this.points.map((point) => applyRigid(point, delta))
    this.objects = this.objects.map((object) => transformObject(object, delta))
    this.transform = after
    const redoPoints = this.points.map((point) => ({ ...point }))
    const afterObjects = cloneObjects(this.objects)
    this.undoStack.push({
      kind: 'points',
      undo: () => {
        this.points = undoPoints
        this.objects = beforeObjects
        this.transform = before
      },
      redo: () => {
        this.points = redoPoints
        this.objects = afterObjects
        this.transform = after
      }
    })
    this.redoStack = []
    this.dirty = true
    void afterObjects
  }

  private apply(command: Command, forward: boolean) {
    if (command.kind === 'points') {
      if (forward) command.redo()
      else command.undo()
      return
    }
    if (command.kind === 'objects') {
      this.objects = cloneObjects(forward ? command.after : command.before)
      return
    }
    this.points = (forward ? command.redoPoints : command.undoPoints).map((point) => ({ ...point }))
    this.transform = forward ? { ...command.after } : { ...command.before }
  }
}

export function createObject(group: string, uid: string, center: Pt): Sem {
  const source: Record<string, unknown> = {}
  if (group === 'FeatureLine') {
    return { uid, group, name: '', kind: 'feature-line', start: center, end: { x: center.x + 1000, y: center.y }, source }
  }
  if (group === 'Reflector') return { uid, group, name: '', kind: 'reflector', position: center, yaw: 0, source: { type: 0 } }
  if (group === 'Pole') return { uid, group, name: '', kind: 'pole', position: center, yaw: 0, source: { type: 2 } }
  if (group === 'QrCode') return { uid, group, name: '', kind: 'qr-code', position: center, yaw: 0, source: { type: 3 } }
  if (group === 'Dock') return { uid, group, name: '', kind: 'dock', position: center, yaw: 0, source }
  if (group === 'GoalWithHeading') return { uid, group, name: '', kind: 'goal', position: center, yaw: 0, source }
  if (group === 'PathPoint') return { uid, group, name: '', kind: 'path', position: center, yaw: 0, connections: [], source }
  return {
    uid,
    group,
    name: '',
    kind: 'zone',
    min: { x: center.x - 1000, y: center.y - 1000 },
    max: { x: center.x + 1000, y: center.y + 1000 },
    yaw: 0,
    source
  }
}

export function objectCenter(object: Sem): Pt {
  if (object.position) return object.position
  if (object.start && object.end) return { x: (object.start.x + object.end.x) / 2, y: (object.start.y + object.end.y) / 2 }
  if (object.min && object.max) return { x: (object.min.x + object.max.x) / 2, y: (object.min.y + object.max.y) / 2 }
  return { x: 0, y: 0 }
}

export const ALL_ADD_GROUPS = [...FEATURE_GROUPS, ...PLACE_GROUPS, ...ZONE_GROUPS]

function cloneObjects(objects: Sem[]): Sem[] {
  return objects.map((object) => ({
    ...object,
    start: object.start ? { ...object.start } : undefined,
    end: object.end ? { ...object.end } : undefined,
    position: object.position ? { ...object.position } : undefined,
    min: object.min ? { ...object.min } : undefined,
    max: object.max ? { ...object.max } : undefined,
    connections: object.connections?.map((conn) => ({ ...conn }))
  }))
}

function applyRigid(point: Pt, rigid: Rigid): Pt {
  const rotated = rotate(point, rigid.yawRad)
  return { x: rotated.x + rigid.txMm, y: rotated.y + rigid.tyMm }
}

function rotate(point: Pt, yaw: number): Pt {
  const cos = Math.cos(yaw)
  const sin = Math.sin(yaw)
  return { x: point.x * cos - point.y * sin, y: point.x * sin + point.y * cos }
}

function transformObject(object: Sem, rigid: Rigid): Sem {
  const next = { ...object }
  if (next.position) next.position = applyRigid(next.position, rigid)
  if (next.start) next.start = applyRigid(next.start, rigid)
  if (next.end) next.end = applyRigid(next.end, rigid)
  if (next.min && next.max) {
    const corners = [
      applyRigid(next.min, rigid),
      applyRigid({ x: next.min.x, y: next.max.y }, rigid),
      applyRigid({ x: next.max.x, y: next.min.y }, rigid),
      applyRigid(next.max, rigid)
    ]
    next.min = { x: Math.min(...corners.map((point) => point.x)), y: Math.min(...corners.map((point) => point.y)) }
    next.max = { x: Math.max(...corners.map((point) => point.x)), y: Math.max(...corners.map((point) => point.y)) }
  }
  if (next.yaw !== undefined) next.yaw = Math.round((next.yaw + rigid.yawRad * 180 / Math.PI) * 100) / 100
  return next
}

function compose(next: Rigid, previous: Rigid): Rigid {
  const cos = Math.cos(next.yawRad)
  const sin = Math.sin(next.yawRad)
  return {
    txMm: previous.txMm * cos - previous.tyMm * sin + next.txMm,
    tyMm: previous.txMm * sin + previous.tyMm * cos + next.tyMm,
    yawRad: previous.yawRad + next.yawRad
  }
}

function quarter(delta: number) {
  const turns = delta / (Math.PI / 2)
  return Math.abs(turns - Math.round(turns)) <= 1e-6
}

function validRadius(radius: number) {
  return Number.isFinite(radius) && radius > 0 && radius <= MAX_RADIUS_MM
}

function dist(a: Pt, b: Pt) {
  return Math.hypot(a.x - b.x, a.y - b.y)
}

function key(point: Pt) {
  return `${Math.round(point.x)}:${Math.round(point.y)}`
}

function snap(point: Pt, resolution: number): Pt {
  return { x: Math.round(point.x / resolution) * resolution, y: Math.round(point.y / resolution) * resolution }
}

function rasterPath(path: Pt[], radius: number, resolution: number): Pt[] {
  const points: Pt[] = []
  for (let index = 0; index < path.length; index += 1) {
    points.push(...disk(path[index], radius, resolution))
    if (index > 0) points.push(...rasterLine(path[index - 1], path[index], radius, resolution))
  }
  return points
}

function rasterLine(start: Pt, end: Pt, radius: number, resolution: number): Pt[] {
  const length = Math.max(dist(start, end), 1)
  const steps = Math.ceil(length / resolution)
  const points: Pt[] = []
  for (let step = 0; step <= steps; step += 1) {
    const t = step / steps
    points.push(...disk({ x: start.x + (end.x - start.x) * t, y: start.y + (end.y - start.y) * t }, radius, resolution))
  }
  return points
}

function disk(center: Pt, radius: number, resolution: number): Pt[] {
  const points: Pt[] = []
  const reach = Math.ceil(radius / resolution)
  const origin = snap(center, resolution)
  for (let ix = -reach; ix <= reach; ix += 1) {
    for (let iy = -reach; iy <= reach; iy += 1) {
      const point = { x: origin.x + ix * resolution, y: origin.y + iy * resolution }
      if (dist(point, center) <= radius) points.push(point)
    }
  }
  return points
}

function pathLength(path: Pt[]) {
  let total = 0
  for (let index = 1; index < path.length; index += 1) total += dist(path[index - 1], path[index])
  return total
}

function nearObject(object: Sem, world: Pt, tolerance: number) {
  if (object.kind === 'zone' && object.min && object.max) {
    return world.x >= object.min.x && world.x <= object.max.x && world.y >= object.min.y && world.y <= object.max.y
  }
  if (object.kind === 'feature-line' && object.start && object.end) return distanceToSegment(world, object.start, object.end) <= tolerance
  if (object.position) return dist(object.position, world) <= tolerance
  return false
}

function distanceToSegment(point: Pt, start: Pt, end: Pt) {
  const length2 = (end.x - start.x) ** 2 + (end.y - start.y) ** 2
  if (length2 === 0) return dist(point, start)
  const t = Math.max(0, Math.min(1, ((point.x - start.x) * (end.x - start.x) + (point.y - start.y) * (end.y - start.y)) / length2))
  return dist(point, { x: start.x + (end.x - start.x) * t, y: start.y + (end.y - start.y) * t })
}

function area(object: Sem) {
  if (!object.min || !object.max) return Infinity
  return Math.abs(object.max.x - object.min.x) * Math.abs(object.max.y - object.min.y)
}

function longSideYaw(points: Pt[]) {
  if (points.length < 2) return 0
  const hull = convexHull(points)
  let bestArea = Infinity
  let bestAngle = 0
  let bestWidth = 0
  let bestHeight = 0
  for (let index = 0; index < hull.length; index += 1) {
    const current = hull[index]
    const next = hull[(index + 1) % hull.length]
    const angle = Math.atan2(next.y - current.y, next.x - current.x)
    const rotated = hull.map((point) => rotate(point, -angle))
    const width = Math.max(...rotated.map((point) => point.x)) - Math.min(...rotated.map((point) => point.x))
    const height = Math.max(...rotated.map((point) => point.y)) - Math.min(...rotated.map((point) => point.y))
    if (width * height < bestArea) {
      bestArea = width * height
      bestAngle = angle
      bestWidth = width
      bestHeight = height
    }
  }
  const longSide = bestWidth >= bestHeight ? bestAngle : bestAngle + Math.PI / 2
  let yaw = -longSide
  while (yaw <= -Math.PI / 2) yaw += Math.PI
  while (yaw > Math.PI / 2) yaw -= Math.PI
  return yaw
}

function convexHull(points: Pt[]): Pt[] {
  const unique = [...new Map(points.map((point) => [`${point.x},${point.y}`, point])).values()]
    .sort((left, right) => left.x === right.x ? left.y - right.y : left.x - right.x)
  if (unique.length <= 2) return unique
  const lower: Pt[] = []
  const upper: Pt[] = []
  for (const point of unique) {
    while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], point) <= 0) lower.pop()
    lower.push(point)
  }
  for (let index = unique.length - 1; index >= 0; index -= 1) {
    const point = unique[index]
    while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], point) <= 0) upper.pop()
    upper.push(point)
  }
  lower.pop()
  upper.pop()
  return [...lower, ...upper]
}

function cross(origin: Pt, left: Pt, right: Pt) {
  return (left.x - origin.x) * (right.y - origin.y) - (left.y - origin.y) * (right.x - origin.x)
}
