import * as THREE from 'three'

export interface AvoidInput {
  widthMm: number
  lengthFrontMm: number
  lengthRearMm: number
  frontMin: number
  frontMax: number
  backMin: number
  backMax: number
  sideMin: number
  sideMax: number
  slow: number
  fast: number
}

export interface LaserInput {
  posX: number
  posY: number
  posTh: number
  angleMin: number
  angleMax: number
  ignore: string
  rangeMin: number
  rangeMax: number
  flipped: boolean
}

const BODY = 0x94a3b8
const INNER = 0xf87171
const OUTER = 0xfacc15

export function parseIgnore(text: string): [number, number][] {
  const source = text.trim()
  if (!source) return []
  const ranges: [number, number][] = []
  for (const part of source.split(',')) {
    const match = part.trim().match(/^(-?\d+(?:\.\d+)?)~(-?\d+(?:\.\d+)?)$/)
    if (!match) return []
    ranges.push([Number(match[1]), Number(match[2])])
  }
  return ranges
}

export function makeLabel(text: string): THREE.Sprite {
  const canvas = document.createElement('canvas')
  canvas.width = 640
  canvas.height = 128
  const ctx = canvas.getContext('2d')
  if (ctx) {
    ctx.clearRect(0, 0, canvas.width, canvas.height)
    ctx.font = '600 44px sans-serif'
    ctx.fillStyle = '#f8fafc'
    ctx.textAlign = 'center'
    ctx.textBaseline = 'middle'
    ctx.fillText(text, 320, 64)
  }
  const texture = new THREE.CanvasTexture(canvas)
  texture.colorSpace = THREE.SRGBColorSpace
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: texture, transparent: true, depthWrite: false }))
  sprite.scale.set(1.8, 0.36, 1)
  sprite.userData.labelTexture = texture
  return sprite
}

function finite(value: number): boolean {
  return Number.isFinite(value)
}

function ring(front: number, back: number, side: number, y: number): THREE.Vector3[] {
  return [
    new THREE.Vector3(side, y, front),
    new THREE.Vector3(-side, y, front),
    new THREE.Vector3(-side, y, -back),
    new THREE.Vector3(side, y, -back),
    new THREE.Vector3(side, y, front)
  ]
}

function line(points: THREE.Vector3[], color: number, dashed: boolean): THREE.Line {
  const geometry = new THREE.BufferGeometry().setFromPoints(points)
  const material = dashed
    ? new THREE.LineDashedMaterial({ color, dashSize: 0.08, gapSize: 0.05, transparent: true })
    : new THREE.LineBasicMaterial({ color, transparent: true })
  const loop = new THREE.Line(geometry, material)
  if (dashed) loop.computeLineDistances()
  return loop
}

export function buildAvoidBoxes(input: AvoidInput, dashed: boolean): THREE.Group {
  const group = new THREE.Group()
  group.name = 'avoid'
  const front = input.lengthFrontMm / 1000
  const back = input.lengthRearMm / 1000
  const half = input.widthMm / 2000
  if (![front, back, half].every(finite)) return group

  const body = line(ring(front, back, half, 0.012), BODY, dashed)
  body.userData.role = 'body'
  body.userData.section = 'robot'
  group.add(body)

  if ([input.frontMin, input.backMin, input.sideMin].every(finite)) {
    const inner = line(
      ring(front + input.frontMin / 1000, back + input.backMin / 1000, half + input.sideMin / 1000, 0.02),
      INNER,
      dashed
    )
    inner.userData.role = 'inner'
    inner.userData.section = 'avoid'
    group.add(inner)
    const label = makeLabel(`低于 ${input.slow} mm/s：内圈`)
    label.position.set(0, 0.2, front + input.frontMin / 1000)
    label.userData.role = 'speed'
    label.userData.section = 'avoid'
    group.add(label)
  }
  if ([input.frontMax, input.backMax, input.sideMax].every(finite)) {
    const outer = line(
      ring(front + input.frontMax / 1000, back + input.backMax / 1000, half + input.sideMax / 1000, 0.028),
      OUTER,
      dashed
    )
    outer.userData.role = 'outer'
    outer.userData.section = 'avoid'
    group.add(outer)
    const label = makeLabel(`高于 ${input.fast} mm/s：外圈`)
    label.position.set(0, 0.45, front + input.frontMax / 1000)
    label.userData.role = 'speed'
    label.userData.section = 'avoid'
    group.add(label)
  }
  return group
}

function keptArcs(start: number, end: number, cuts: [number, number][]): [number, number][] {
  const dir = Math.sign(end - start) || 1
  const span = Math.abs(end - start)
  const along = (angle: number) => (angle - start) * dir
  let pieces: [number, number][] = [[0, span]]
  for (const [from, to] of cuts) {
    let a = along(from)
    let b = along(to)
    if (a > b) [a, b] = [b, a]
    const next: [number, number][] = []
    for (const [pieceStart, pieceEnd] of pieces) {
      if (b <= pieceStart || a >= pieceEnd) {
        next.push([pieceStart, pieceEnd])
        continue
      }
      if (a > pieceStart) next.push([pieceStart, a])
      if (b < pieceEnd) next.push([b, pieceEnd])
    }
    pieces = next.filter(([pieceStart, pieceEnd]) => pieceEnd - pieceStart > 0.01)
  }
  return pieces.map(([pieceStart, pieceEnd]) => [start + dir * pieceStart, start + dir * pieceEnd])
}

function sector(start: number, end: number, inner: number, outer: number, color: number): THREE.Mesh {
  const sweep = end - start
  const steps = Math.max(2, Math.ceil(Math.abs(sweep) / (Math.PI / 24)))
  const positions: number[] = []
  const point = (angle: number, radius: number) => [Math.sin(angle) * radius, 0.05, Math.cos(angle) * radius]
  for (let index = 0; index < steps; index += 1) {
    const a0 = start + (sweep * index) / steps
    const a1 = start + (sweep * (index + 1)) / steps
    const p0 = point(a0, inner)
    const p1 = point(a0, outer)
    const p2 = point(a1, outer)
    const p3 = point(a1, inner)
    positions.push(...p0, ...p1, ...p2, ...p0, ...p2, ...p3)
  }
  const geometry = new THREE.BufferGeometry()
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3))
  geometry.computeVertexNormals()
  return new THREE.Mesh(
    geometry,
    new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.28, depthWrite: false, side: THREE.DoubleSide })
  )
}

function arcLine(start: number, end: number, radius: number, color: number, dashed: boolean): THREE.Line {
  const sweep = end - start
  const steps = Math.max(2, Math.ceil(Math.abs(sweep) / (Math.PI / 36)))
  const points: THREE.Vector3[] = []
  for (let index = 0; index <= steps; index += 1) {
    const angle = start + (sweep * index) / steps
    points.push(new THREE.Vector3(Math.sin(angle) * radius, 0.06, Math.cos(angle) * radius))
  }
  return line(points, color, dashed)
}

export function buildLaserFan(input: LaserInput, scaleK: number, dashed: boolean, color: number): THREE.Group {
  const group = new THREE.Group()
  const flip = input.flipped ? -1 : 1
  const origin = (input.posTh * Math.PI) / 180
  const start = origin + (flip * input.angleMin * Math.PI) / 180
  const end = origin + (flip * input.angleMax * Math.PI) / 180
  const cuts = parseIgnore(input.ignore).map(([from, to]) => [
    origin + (flip * from * Math.PI) / 180,
    origin + (flip * to * Math.PI) / 180
  ] as [number, number])
  const inner = Math.max(0.02, input.rangeMin * scaleK)
  const outer = Math.max(inner + 0.05, input.rangeMax * scaleK)
  const marker = new THREE.Mesh(
    new THREE.CylinderGeometry(0.045, 0.045, 0.14, 16),
    new THREE.MeshBasicMaterial({ color, transparent: true })
  )
  marker.position.set(input.posY / 1000, 0.12, input.posX / 1000)
  group.add(marker)
  for (const [arcStart, arcEnd] of keptArcs(start, end, cuts)) {
    const fan = sector(arcStart, arcEnd, inner, outer, color)
    fan.position.set(input.posY / 1000, 0, input.posX / 1000)
    group.add(fan)
    const edge = arcLine(arcStart, arcEnd, outer, color, dashed)
    edge.position.copy(fan.position)
    group.add(edge)
  }
  const mid = (start + end) / 2
  const label = makeLabel(`${input.rangeMin}–${input.rangeMax} m`)
  label.position.set(input.posY / 1000 + Math.sin(mid) * outer, 0.3, input.posX / 1000 + Math.cos(mid) * outer)
  group.add(label)
  return group
}

export function laserScale(ranges: number[]): number {
  const max = Math.max(...ranges.filter((value) => Number.isFinite(value) && value > 0), 0)
  return max > 0 ? 3 / max : 1
}
