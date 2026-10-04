// pen.ts: the "drawn in code" primitive set, ported from the plate-film pen (pen.js) for the browser canvas.
// Everything here is DETERMINISTIC: same seed and same inputs give identical pixels. No Math.random, no Date.
// Seeds are explicit and stable per element. makePaper, regMarks and vignette stay behind: the book's ground is painted.

export type Pt = [number, number]
export type Pts = Pt[]
/** Anything a path can be traced into: the context itself, or a Path2D batched and stroked once. */
export type PathSink = CanvasRenderingContext2D | Path2D

// ---------------------------------------------------------------- randomness
export function mulberry32(seed: number): () => number {
  let a = seed
  return function () {
    a |= 0; a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}
/** Hash a string to a stable 32-bit seed so every element can name its own grain. */
export function seedOf(str: string): number {
  let h = 2166136261
  for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 16777619) }
  return h >>> 0
}
export const rngOf = (name: string): (() => number) => mulberry32(seedOf(name))

// one lattice value in -1..1 for (seed, i): an integer hash, so a noise needs no table and costs nothing to make
function lattice(seed: number, i: number): number {
  let h = (seed ^ Math.imul(i & 255, 0x27d4eb2d)) | 0
  h = Math.imul(h ^ (h >>> 15), 0x85ebca6b)
  h = Math.imul(h ^ (h >>> 13), 0xc2b2ae35)
  h ^= h >>> 16
  return ((h >>> 0) / 4294967296) * 2 - 1
}
/** Smooth 1-D value noise (period 256): stroke wander that drifts instead of buzzing. */
export function noise1(seed: number): (x: number) => number {
  const s = seed | 0
  return (x: number) => {
    const i = Math.floor(x), f = x - i
    const a = lattice(s, i), b = lattice(s, i + 1)
    const u = f * f * (3 - 2 * f)
    return a + (b - a) * u
  }
}

// ---------------------------------------------------------------- easing
export const ease = {
  linear: (t: number) => t,
  out: (t: number) => 1 - Math.pow(1 - t, 3),
  outQuint: (t: number) => 1 - Math.pow(1 - t, 5),
  in: (t: number) => t * t * t,
  inOut: (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2),
  outBack: (t: number) => 1 + 2.2 * Math.pow(t - 1, 3) + 1.2 * Math.pow(t - 1, 2),
  outElastic: (t: number) => t === 0 ? 0 : t === 1 ? 1
    : Math.pow(2, -9 * t) * Math.sin((t * 10 - 0.75) * (2 * Math.PI / 3)) + 1,
}
export const clamp01 = (v: number): number => (v < 0 ? 0 : v > 1 ? 1 : v)
export const lerp = (a: number, b: number, t: number): number => a + (b - a) * t
/** Normalised progress through a window, eased. */
export function span(t: number, t0: number, dur: number, fn: (t: number) => number = ease.out): number {
  return fn(clamp01((t - t0) / dur))
}

// ---------------------------------------------------------------- colour (hex in, as in pen.js)
export function hexToRgb(hex: string): [number, number, number] {
  let h = hex.replace('#', '')
  if (h.length === 3) h = h.split('').map((c) => c + c).join('')
  return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)]
}
export const rgbToHex = (r: number, g: number, b: number): string =>
  '#' + [r, g, b].map((v) => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, '0')).join('')
export function mix(a: string, b: string, t: number): string {
  const A = hexToRgb(a), B = hexToRgb(b)
  return rgbToHex(lerp(A[0], B[0], t), lerp(A[1], B[1], t), lerp(A[2], B[2], t))
}
export function alpha(hex: string, a: number): string {
  const [r, g, b] = hexToRgb(hex)
  return `rgba(${r},${g},${b},${a})`
}

// ---------------------------------------------------------------- the pen
// A hand pen never draws a true straight line. Every stroke is resampled and pushed off-axis by smooth noise,
// then drawn as a quadratic through midpoints.

function resample(pts: Pts, step: number): Pts {
  const out: Pts = []
  for (let i = 0; i < pts.length - 1; i++) {
    const [x1, y1] = pts[i], [x2, y2] = pts[i + 1]
    const d = Math.hypot(x2 - x1, y2 - y1)
    const n = Math.max(1, Math.round(d / step))
    for (let k = 0; k < n; k++) out.push([lerp(x1, x2, k / n), lerp(y1, y2, k / n)])
  }
  out.push(pts[pts.length - 1])
  return out
}

export interface WobbleOpts { amp?: number; scale?: number; seed?: number; step?: number }
/** Push a polyline off its own normal with smooth noise. This is the wobble; it tapers at the ends so joins stay tight. */
export function wobble(pts: Pts, { amp = 1.1, scale = 0.06, seed = 1, step = 9 }: WobbleOpts = {}): Pts {
  if (pts.length < 2) return pts
  const P = resample(pts, step)
  const n1 = noise1(seed), n2 = noise1(seed ^ 0x9e3779b9)
  const out: Pts = []
  for (let i = 0; i < P.length; i++) {
    const p = P[i]
    const prev = P[Math.max(0, i - 1)], next = P[Math.min(P.length - 1, i + 1)]
    let dx = next[0] - prev[0], dy = next[1] - prev[1]
    const L = Math.hypot(dx, dy) || 1
    dx /= L; dy /= L
    const endFade = Math.min(1, Math.min(i, P.length - 1 - i) / 3)
    const w = n1(i * scale * 10) * amp * endFade
    const along = n2(i * scale * 6) * amp * 0.35 * endFade
    out.push([p[0] - dy * w + dx * along, p[1] + dx * w + dy * along])
  }
  return out
}

/** Trace a polyline into a sink as quadratics through midpoints, without beginPath: many strokes batch into one path. */
export function appendPath(sink: PathSink, pts: Pts, close = false): void {
  if (!pts.length) return
  sink.moveTo(pts[0][0], pts[0][1])
  for (let i = 1; i < pts.length - 1; i++) {
    const mx = (pts[i][0] + pts[i + 1][0]) / 2, my = (pts[i][1] + pts[i + 1][1]) / 2
    sink.quadraticCurveTo(pts[i][0], pts[i][1], mx, my)
  }
  const L = pts[pts.length - 1]
  sink.lineTo(L[0], L[1])
  if (close) sink.closePath()
}

export function tracePath(ctx: CanvasRenderingContext2D, pts: Pts, close = false): void {
  if (!pts.length) return
  ctx.beginPath()
  appendPath(ctx, pts, close)
}

export interface StrokeOpts {
  color?: string; width?: number; amp?: number; seed?: number; close?: boolean; passes?: number
  alpha?: number; grow?: number; cap?: CanvasLineCap; join?: CanvasLineJoin; dash?: number[] | null; step?: number
}
/** The workhorse: a polyline as an inked pen stroke. passes > 1 re-inks with fresh wobble; grow < 1 draws the first part. */
export function stroke(ctx: CanvasRenderingContext2D, pts: Pts, opts: StrokeOpts = {}): void {
  const {
    color = '#1a1726', width = 1.6, amp = 1.0, seed = 1, close = false,
    passes = 1, alpha: a = 1, grow = 1, cap = 'round', join = 'round', dash = null, step = 9,
  } = opts
  if (pts.length < 2 || grow <= 0) return
  let P = pts
  if (grow < 1) P = partial(pts, grow)
  ctx.save()
  ctx.lineCap = cap; ctx.lineJoin = join
  ctx.globalAlpha = a
  if (dash) ctx.setLineDash(dash)
  for (let k = 0; k < passes; k++) {
    const w = wobble(P, { amp, seed: seed + k * 977, step })
    ctx.strokeStyle = color
    ctx.lineWidth = width * (k === 0 ? 1 : 0.78)
    ctx.globalAlpha = a * (k === 0 ? 1 : 0.5)
    tracePath(ctx, w, close)
    ctx.stroke()
  }
  ctx.restore()
}

/** Truncate a polyline to a fraction of its arc length. */
export function partial(pts: Pts, f: number): Pts {
  if (f >= 1) return pts
  if (f <= 0) return []
  let total = 0
  const segs: number[] = []
  for (let i = 0; i < pts.length - 1; i++) {
    const d = Math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
    segs.push(d); total += d
  }
  const want = total * f
  const out: Pts = [pts[0]]
  let acc = 0
  for (let i = 0; i < segs.length; i++) {
    if (acc + segs[i] >= want) {
      const r = (want - acc) / (segs[i] || 1)
      out.push([lerp(pts[i][0], pts[i + 1][0], r), lerp(pts[i][1], pts[i + 1][1], r)])
      return out
    }
    acc += segs[i]
    out.push(pts[i + 1])
  }
  return out
}

/** The point at a fraction of a polyline's arc length (0 = first point, 1 = last). */
export function pointAt(pts: Pts, f: number): Pt {
  if (pts.length === 1 || f <= 0) return pts[0]
  if (f >= 1) return pts[pts.length - 1]
  const P = partial(pts, f)
  return P[P.length - 1]
}

export function fillShape(ctx: CanvasRenderingContext2D, pts: Pts, color: string,
  { amp = 0.9, seed = 3, a = 1 }: { amp?: number; seed?: number; a?: number } = {}): void {
  const w = wobble(pts, { amp, seed, step: 11 })
  ctx.save()
  ctx.globalAlpha = a
  ctx.fillStyle = color
  tracePath(ctx, w, true)
  ctx.fill()
  ctx.restore()
}

// ---------------------------------------------------------------- hatching
export interface HatchOpts {
  angle?: number; spacing?: number; color?: string; width?: number; seed?: number; a?: number
  jitter?: number; gap?: number; grow?: number; crosshatch?: boolean; crossAngle?: number | null
}
/** Pen hatching, the biggest hand-drawn tell: parallel wandering broken strokes clipped to a shape. */
export function hatch(ctx: CanvasRenderingContext2D, clipPts: Pts, opts: HatchOpts = {}): void {
  const {
    angle = -45, spacing = 7, color = '#1a1726', width = 0.9, seed = 5,
    a = 0.5, jitter = 1.2, gap = 0.12, grow = 1, crosshatch = false, crossAngle = null,
  } = opts
  if (grow <= 0) return
  ctx.save()
  const w = wobble(clipPts, { amp: 0.8, seed, step: 11 })
  tracePath(ctx, w, true)
  ctx.clip()

  const xs = clipPts.map((p) => p[0]), ys = clipPts.map((p) => p[1])
  const x0 = Math.min(...xs), x1 = Math.max(...xs)
  const y0 = Math.min(...ys), y1 = Math.max(...ys)
  const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2
  const R = Math.hypot(x1 - x0, y1 - y0) / 2 + spacing * 2

  const runs = crosshatch ? [angle, crossAngle ?? angle + 78] : [angle]
  const rnd = mulberry32(seed)
  runs.forEach((ang, ri) => {
    const rad = (ang * Math.PI) / 180
    const dx = Math.cos(rad), dy = Math.sin(rad)
    const nx = -dy, ny = dx
    const n = Math.ceil((R * 2) / spacing)
    const limit = Math.floor(n * grow)
    for (let i = 0; i <= n; i++) {
      if (i > limit) break
      const off = -R + i * spacing + (rnd() - 0.5) * spacing * 0.35
      const mx = cx + nx * off, my = cy + ny * off
      const cuts = rnd() < gap ? 2 : 1
      for (let c = 0; c < cuts; c++) {
        const t0 = cuts === 1 ? -1 : (c === 0 ? -1 : 0.12 + rnd() * 0.2)
        const t1 = cuts === 1 ? 1 : (c === 0 ? -0.15 - rnd() * 0.2 : 1)
        const j = () => (rnd() - 0.5) * jitter
        const p0: Pt = [mx + dx * R * t0 + j(), my + dy * R * t0 + j()]
        const p1: Pt = [mx + dx * R * t1 + j(), my + dy * R * t1 + j()]
        const mid: Pt = [(p0[0] + p1[0]) / 2 + j() * 2.2, (p0[1] + p1[1]) / 2 + j() * 2.2]
        ctx.beginPath()
        ctx.moveTo(p0[0], p0[1])
        ctx.quadraticCurveTo(mid[0], mid[1], p1[0], p1[1])
        ctx.strokeStyle = color
        ctx.globalAlpha = a * (0.68 + rnd() * 0.32) * (ri ? 0.72 : 1)
        ctx.lineWidth = width * (0.8 + rnd() * 0.45)
        ctx.lineCap = 'round'
        ctx.stroke()
      }
    }
  })
  ctx.restore()
}

/** A hatch line already clipped to its shape: two ends and the bowed control point between them. */
export interface HatchSeg { a: Pt; c: Pt; b: Pt; strong: boolean }
/**
 * Hatching as geometry, for batching: lines at `angle` (degrees) across a CONVEX polygon, clipped analytically (no
 * canvas clip), each with the pen's spacing jitter, end jitter, bow and occasional lift. `strong` splits them into two
 * alpha groups so a whole village of roofs strokes in two calls.
 */
export function hatchSegments(poly: Pts, { angle = -45, spacing = 7, seed = 5, jitter = 1.2, gap = 0.12, inset = 0 }:
  { angle?: number; spacing?: number; seed?: number; jitter?: number; gap?: number; inset?: number } = {}): HatchSeg[] {
  const rnd = mulberry32(seed)
  const rad = (angle * Math.PI) / 180
  const dx = Math.cos(rad), dy = Math.sin(rad)
  const nx = -dy, ny = dx
  let lo = Infinity, hi = -Infinity
  for (const p of poly) { const o = p[0] * nx + p[1] * ny; if (o < lo) lo = o; if (o > hi) hi = o }
  const out: HatchSeg[] = []
  for (let off = lo + spacing * (0.35 + rnd() * 0.4); off < hi - spacing * 0.2; off += spacing * (0.85 + rnd() * 0.3)) {
    // intersect the line {p : p.n = off} with each polygon edge
    let tMin = Infinity, tMax = -Infinity
    for (let i = 0; i < poly.length - 1; i++) {
      const p = poly[i], q = poly[i + 1]
      const op = p[0] * nx + p[1] * ny - off, oq = q[0] * nx + q[1] * ny - off
      if ((op > 0) === (oq > 0) || op === oq) continue
      const r = op / (op - oq)
      const x = p[0] + (q[0] - p[0]) * r, y = p[1] + (q[1] - p[1]) * r
      const t = x * dx + y * dy
      if (t < tMin) tMin = t
      if (t > tMax) tMax = t
    }
    tMin += inset; tMax -= inset
    if (!(tMax - tMin > spacing * 0.4)) continue
    const at = (t: number): Pt => [nx * off + dx * t, ny * off + dy * t]
    const j = () => (rnd() - 0.5) * jitter
    const strong = rnd() < 0.55
    const pieces: [number, number][] = rnd() < gap
      ? [[tMin, lerp(tMin, tMax, 0.35 + rnd() * 0.2)], [lerp(tMin, tMax, 0.62 + rnd() * 0.12), tMax]]
      : [[tMin, tMax]]
    for (const [t0, t1] of pieces) {
      const A = at(t0 + Math.abs(j()) * 0.5), B = at(t1 - Math.abs(j()) * 0.5)
      A[0] += j() * 0.4; A[1] += j() * 0.4; B[0] += j() * 0.4; B[1] += j() * 0.4
      const C: Pt = [(A[0] + B[0]) / 2 + j() * 0.9, (A[1] + B[1]) / 2 + j() * 0.9]
      out.push({ a: A, c: C, b: B, strong })
    }
  }
  return out
}

/** Stipple: dots inside a shape. Good for soil, noise floors, particle fields. */
export function stipple(ctx: CanvasRenderingContext2D, clipPts: Pts,
  opts: { count?: number; color?: string; seed?: number; a?: number; r?: number; grow?: number } = {}): void {
  const { count = 300, color = '#1a1726', seed = 7, a = 0.5, r = 1.1, grow = 1 } = opts
  ctx.save()
  const w = wobble(clipPts, { amp: 0.8, seed, step: 11 })
  tracePath(ctx, w, true); ctx.clip()
  const xs = clipPts.map((p) => p[0]), ys = clipPts.map((p) => p[1])
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys)
  const rnd = mulberry32(seed)
  const n = Math.floor(count * clamp01(grow))
  ctx.fillStyle = color
  for (let i = 0; i < n; i++) {
    const x = lerp(x0, x1, rnd()), y = lerp(y0, y1, rnd())
    ctx.globalAlpha = a * (0.35 + rnd() * 0.65)
    ctx.beginPath()
    ctx.arc(x, y, r * (0.5 + rnd()), 0, Math.PI * 2)
    ctx.fill()
  }
  ctx.restore()
}

// ---------------------------------------------------------------- shapes
export function rectPts(x: number, y: number, w: number, h: number): Pts {
  return [[x, y], [x + w, y], [x + w, y + h], [x, y + h], [x, y]]
}
export function circlePts(cx: number, cy: number, r: number, n = 64, phase = 0): Pts {
  const p: Pts = []
  for (let i = 0; i <= n; i++) {
    const a = phase + (i / n) * Math.PI * 2
    p.push([cx + Math.cos(a) * r, cy + Math.sin(a) * r])
  }
  return p
}
export function arcPts(cx: number, cy: number, r: number, a0: number, a1: number, n = 48): Pts {
  const p: Pts = []
  for (let i = 0; i <= n; i++) p.push([cx + Math.cos(lerp(a0, a1, i / n)) * r, cy + Math.sin(lerp(a0, a1, i / n)) * r])
  return p
}

/** A clean (non-wobbled) circle, for instrument rings that should read as machined. */
export function ring(ctx: CanvasRenderingContext2D, cx: number, cy: number, r: number,
  { color = '#1a1726', width = 1, a = 1, dash = null, from = -Math.PI / 2, sweep = Math.PI * 2 }:
  { color?: string; width?: number; a?: number; dash?: number[] | null; from?: number; sweep?: number } = {}): void {
  ctx.save()
  ctx.globalAlpha = a; ctx.strokeStyle = color; ctx.lineWidth = width
  if (dash) ctx.setLineDash(dash)
  ctx.beginPath(); ctx.arc(cx, cy, r, from, from + sweep)
  ctx.stroke(); ctx.restore()
}
export function dot(ctx: CanvasRenderingContext2D, x: number, y: number, r: number, color: string, a = 1): void {
  ctx.save(); ctx.globalAlpha = a; ctx.fillStyle = color
  ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill(); ctx.restore()
}

// ---------------------------------------------------------------- annotation
export function arrowHead(ctx: CanvasRenderingContext2D, x: number, y: number, angle: number, size: number, color: string, a = 1): void {
  ctx.save(); ctx.globalAlpha = a; ctx.fillStyle = color
  ctx.translate(x, y); ctx.rotate(angle)
  ctx.beginPath(); ctx.moveTo(0, 0)
  ctx.lineTo(-size, size * 0.42); ctx.lineTo(-size * 0.74, 0); ctx.lineTo(-size, -size * 0.42)
  ctx.closePath(); ctx.fill(); ctx.restore()
}

/** Right-angle elbow leader: a dot on the subject, an elbow, then a horizontal run to the label. */
export function leader(ctx: CanvasRenderingContext2D, from: Pt, to: Pt,
  opts: { color?: string; width?: number; a?: number; grow?: number; dotR?: number; elbow?: number } = {}): void {
  const { color = '#1a1726', width = 1, a = 1, grow = 1, dotR = 2.6, elbow = 0.55 } = opts
  const midX = lerp(from[0], to[0], elbow)
  const pts: Pts = [[from[0], from[1]], [midX, from[1]], [midX, to[1]], [to[0], to[1]]]
  stroke(ctx, pts, { color, width, alpha: a, amp: 0.35, seed: seedOf(`ld${from[0]}${to[1]}`), grow })
  if (grow > 0.02) dot(ctx, from[0], from[1], dotR, color, a)
}

/** Dimension line with ticks and arrows at both ends: the engineering-drawing tell. */
export function dimLine(ctx: CanvasRenderingContext2D, x1: number, y1: number, x2: number, y2: number,
  opts: { color?: string; width?: number; a?: number; tick?: number; grow?: number } = {}): void {
  const { color = '#1a1726', width = 1, a = 1, tick = 5, grow = 1 } = opts
  const ang = Math.atan2(y2 - y1, x2 - x1)
  const nx = -Math.sin(ang) * tick, ny = Math.cos(ang) * tick
  const gx = lerp(x1, x2, grow), gy = lerp(y1, y2, grow)
  stroke(ctx, [[x1, y1], [gx, gy]], { color, width, alpha: a, amp: 0.25, seed: 41 })
  stroke(ctx, [[x1 - nx, y1 - ny], [x1 + nx, y1 + ny]], { color, width, alpha: a, amp: 0.2, seed: 42 })
  if (grow > 0.96) {
    stroke(ctx, [[x2 - nx, y2 - ny], [x2 + nx, y2 + ny]], { color, width, alpha: a, amp: 0.2, seed: 43 })
    arrowHead(ctx, x1, y1, ang + Math.PI, 6, color, a)
    arrowHead(ctx, x2, y2, ang, 6, color, a)
  }
}
