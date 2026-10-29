export type Pt = { x: number; y: number }

export type Conn = {
  name: string
  cost?: number
  posture?: string
  type?: number
  x1?: number
  y1?: number
  x2?: number
  y2?: number
}

export type SemKind = 'feature-line' | 'reflector' | 'pole' | 'qr-code' | 'zone' | 'dock' | 'goal' | 'path'

export type Sem = {
  uid: string
  group: string
  name: string
  kind: SemKind
  start?: Pt
  end?: Pt
  position?: Pt
  yaw?: number
  min?: Pt
  max?: Pt
  allowPassingThrough?: boolean
  connections?: Conn[]
  source: Record<string, unknown>
}

export const ZONE_GROUPS = [
  'ReflectorSector',
  'LandmarkLocalizationSector',
  'LineSector',
  'QrcodeSector',
  'RelocationSector',
  'LaserIgnoreZone',
  'StableSector',
  'LocalizationMapExcludeZone'
] as const

export const FEATURE_GROUPS = ['FeatureLine', 'Reflector', 'Pole', 'QrCode'] as const

export const PLACE_GROUPS = ['Dock', 'GoalWithHeading', 'PathPoint'] as const

const POINT_GROUPS = new Set(['Reflector', 'Pole', 'QrCode', 'Dock', 'GoalWithHeading', 'PathPoint'])

export function resolutionMm(mapRes: number): number {
  return mapRes < 1 ? mapRes * 1000 : mapRes
}

export function groupLabel(group: string): string {
  const labels: Record<string, string> = {
    FeatureLine: '特征线',
    Reflector: '反光柱',
    Pole: '立柱',
    QrCode: '二维码',
    Dock: '停靠点',
    GoalWithHeading: '带朝向目标点',
    PathPoint: '路径点',
    ReflectorSector: '反光柱区域',
    LandmarkLocalizationSector: '地标定位区域',
    LineSector: '线区域',
    QrcodeSector: '二维码区域',
    RelocationSector: '重定位区域',
    LaserIgnoreZone: '激光忽略区域',
    StableSector: '稳定区域',
    LocalizationMapExcludeZone: '定位地图排除区域'
  }
  return labels[group] || group
}

let uidSeq = 1

export function parseUmcl(map: Record<string, any>): { points: Pt[]; objects: Sem[] } {
  const points: Pt[] = []
  for (const column of map.ObsPoints || []) {
    if (!Array.isArray(column) || column.length < 2) continue
    const x = Number(column[0])
    for (let index = 1; index < column.length; index += 1) points.push({ x, y: Number(column[index]) })
  }
  const objects: Sem[] = []
  const objs = map.Objs
  if (objs && typeof objs === 'object' && !Array.isArray(objs)) {
    for (const [group, entries] of Object.entries(objs)) {
      if (!Array.isArray(entries)) continue
      for (const entry of entries) {
        const parsed = parseEntry(group, entry)
        if (parsed) objects.push(parsed)
      }
    }
  }
  return { points, objects }
}

export function serializeUmcl(map: Record<string, any>, points: Pt[], objects: Sem[], resolution: number): Record<string, any> {
  const snapped = points.map((point) => ({ x: Math.round(point.x / resolution) * resolution, y: Math.round(point.y / resolution) * resolution }))
  const columns = new Map<number, Set<number>>()
  for (const point of snapped) {
    const bucket = columns.get(point.x) || new Set<number>()
    bucket.add(point.y)
    columns.set(point.x, bucket)
  }
  const obs = [...columns.entries()].sort((a, b) => a[0] - b[0]).map(([x, ys]) => [x, ...[...ys].sort((a, b) => a - b)])
  const used = obs.length ? obs : [[0, 0]]
  const xs = used.map((column) => column[0])
  const ys = used.flatMap((column) => column.slice(1))
  const grouped: Record<string, Record<string, unknown>[]> = {}
  for (const object of objects) {
    grouped[object.group] = grouped[object.group] || []
    grouped[object.group].push(serializeObject(object))
  }
  return {
    ...map,
    ObsPoints: used,
    NumPoints: used.reduce((sum, column) => sum + column.length - 1, 0),
    MinPose: `${Math.min(...xs)} ${Math.min(...ys)}`,
    MaxPose: `${Math.max(...xs)} ${Math.max(...ys)}`,
    Objs: grouped
  }
}

function parseEntry(group: string, entry: any): Sem | null {
  if (!entry || typeof entry !== 'object') return null
  const source = { ...entry }
  const base = { uid: `obj-${uidSeq++}`, group, name: typeof entry.name === 'string' ? entry.name : '', source }
  if (group === 'FeatureLine') {
    const points = pointList(entry.points)
    if (points.length !== 2) return null
    return { ...base, kind: 'feature-line', start: points[0], end: points[1] }
  }
  if (ZONE_GROUPS.includes(group as (typeof ZONE_GROUPS)[number])) {
    const points = pointList(entry.points)
    if (points.length !== 2) return null
    return {
      ...base,
      kind: 'zone',
      min: { x: Math.min(points[0].x, points[1].x), y: Math.min(points[0].y, points[1].y) },
      max: { x: Math.max(points[0].x, points[1].x), y: Math.max(points[0].y, points[1].y) },
      yaw: poseYaw(entry.pose)
    }
  }
  if (!POINT_GROUPS.has(group)) return { ...base, kind: 'reflector', position: { x: 0, y: 0 } }
  const position = posePoint(entry.pose)
  if (!position) return null
  const kind: SemKind = group === 'QrCode' ? 'qr-code' : group === 'GoalWithHeading' ? 'goal' : group === 'PathPoint' ? 'path' : group === 'Dock' ? 'dock' : group === 'Pole' ? 'pole' : 'reflector'
  return {
    ...base,
    kind,
    position,
    yaw: poseYaw(entry.pose),
    ...(typeof entry.allowPassingThrough === 'boolean' ? { allowPassingThrough: entry.allowPassingThrough } : {}),
    ...(group === 'PathPoint' ? { connections: parseConnections(entry) } : {})
  }
}

function serializeObject(object: Sem): Record<string, unknown> {
  const result: Record<string, unknown> = { ...object.source, name: object.name }
  if (object.kind === 'feature-line' && object.start && object.end) {
    result.points = fmtPoints([object.start.x, object.start.y, object.end.x, object.end.y])
    result.pose = fmtPose(
      (object.start.x + object.end.x) / 2,
      (object.start.y + object.end.y) / 2,
      Math.atan2(object.end.y - object.start.y, object.end.x - object.start.x) * 180 / Math.PI
    )
    return result
  }
  if (object.kind === 'zone' && object.min && object.max) {
    result.points = fmtPoints([object.min.x, object.min.y, object.max.x, object.max.y])
    result.pose = fmtPose((object.min.x + object.max.x) / 2, (object.min.y + object.max.y) / 2, object.yaw || 0)
    return result
  }
  if (object.position) {
    result.pose = fmtPose(object.position.x, object.position.y, object.yaw || 0)
    if (object.kind === 'reflector' || object.kind === 'pole') {
      result.points = fmtPoints([object.position.x, object.position.y, object.position.x, object.position.y])
    }
    if (object.kind === 'goal' && object.allowPassingThrough !== undefined) result.allowPassingThrough = object.allowPassingThrough
    if (object.kind === 'path') result.connections = (object.connections || []).map((conn) => ({ ...conn }))
  }
  return result
}

function parseConnections(entry: any): Conn[] {
  if (Array.isArray(entry.connections)) {
    return entry.connections.flatMap((conn: any) => {
      if (!conn || typeof conn.name !== 'string' || !conn.name) return []
      const item: Conn = { name: conn.name }
      for (const key of ['cost', 'posture', 'type', 'x1', 'y1', 'x2', 'y2'] as const) {
        if (conn[key] !== undefined) (item as any)[key] = conn[key]
      }
      return [item]
    })
  }
  if (typeof entry.vertex !== 'string') return []
  return entry.vertex.split(/\s+/).filter(Boolean).map((name: string) => ({ name }))
}

function pointList(value: unknown): Pt[] {
  if (typeof value !== 'string') return []
  const numbers = value.trim().split(/\s+/).map(Number)
  if (!numbers.length || numbers.length % 2 || numbers.some((item) => !Number.isFinite(item))) return []
  const points: Pt[] = []
  for (let index = 0; index < numbers.length; index += 2) points.push({ x: numbers[index], y: numbers[index + 1] })
  return points
}

function posePoint(value: unknown): Pt | null {
  const parts = String(value || '').trim().split(/\s+/)
  if (parts.length < 2) return null
  const x = Number(parts[0])
  const y = Number(parts[1])
  return Number.isFinite(x) && Number.isFinite(y) ? { x, y } : null
}

function poseYaw(value: unknown): number {
  const parts = String(value || '').trim().split(/\s+/)
  const yaw = parts.length > 2 ? Number(parts[2]) : 0
  return Number.isFinite(yaw) ? yaw : 0
}

function fmtPoints(values: number[]): string {
  return values.map((value) => String(Math.round(value))).join(' ')
}

function fmtPose(x: number, y: number, yaw: number): string {
  const text = (Math.round(yaw * 100) / 100).toString()
  return `${Math.round(x)} ${Math.round(y)} ${text}`
}
