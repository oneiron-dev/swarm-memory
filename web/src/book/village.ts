// village.ts: the village plate. A row of small ink houses on the page's painted valley, drawn as a pure function of
// (scene, w, h, clock). It paints no background and clears nothing: ink and a few washes over a transparent canvas.
//
// The scene is the day's STATE (who lives where, who is out, the week's goal, who talks with whom); it changes when the
// reader scrolls. The clock is the LIFE (smoke, walking, swaying, breathing): seconds since the day came under the eye.
// Everything that moves, moves by the clock only.
//
// Units: w and h are in the context's current user units (CSS pixels when the caller has scaled the context by the
// device pixel ratio, or device pixels when it has not). Every length scales with the plate, so both give the same
// picture. Every wobble is seeded from the house number: the same scene, size and clock draw the same pixels.

import { alpha, appendPath, clamp01, ease, hatchSegments, lerp, noise1, pointAt, rngOf, seedOf, wobble, type Pt, type Pts } from './pen'

export type Lab = 'anthropic' | 'openai' | 'google' | 'other'
export interface HouseScene {
  n: number                 // house number, 1-based, stable for the year (position in the row)
  present: boolean          // someone lives here today
  lab: Lab | null           // the resident's lab: the door's colour
  window: string | null     // a CSS colour for the lit window, or null = dark
  smoke: number             // 0..1 chat volume today
  arriving: boolean         // a figure walks from the road to the door during t
  leaving: boolean          // a figure walks from the door to the road during t
  ghost: boolean            // drawn faint (another lens is looking elsewhere)
  mark: 'pin' | 'swing' | null   // a finding starts here / a mood swing today
  away: boolean             // elsewhere today: the resident is out on the road, walked off toward the far end, standing and looking out
  idle: boolean             // idle today: no lamp, no smoke, a closed shutter stroke across the window
}
export interface VillageScene {
  houses: HouseScene[]      // in house order; may be 1..40 long; empty houses are present:false
  visitor: number           // 0..1 the humans' share of the day's chat; > 0 draws a visitor at the gate
  t: number                 // 0..1 progress through the day (drives the walking figures when no clock is given)
  lens: 'village' | 'goal' | 'memory' | 'humans' | 'agent'
  board: string | null                          // the week's goal, at most 60 characters, hand-lettered on a notice board by the gate
  pairs: { a: number; b: number; w: number }[]  // today's talking pairs by house number (0 = the visitor at the gate), w in 0..1, at most 3
  props?: Prop[]            // the chapter's props: at most 3 stand in the grass in front of the road, right of the board
  moments?: Moment[]        // the beats' moments: each plays once from its start, then holds its end state
}

/** The stage catalogue's props (briefs/stage-catalogue.md): what stands in the grass for the whole chapter. */
export type PropKind = 'stall' | 'mailbox' | 'ballot' | 'scaffold' | 'gallery' | 'chessboard' | 'stage' | 'press' | 'garden' | 'lanterns' | 'cloud' | 'signpost'
/** The stage catalogue's moments: what the residents do when a beat lights. */
export type MomentKind = 'celebrate' | 'build' | 'send' | 'vote' | 'win' | 'fail' | 'gather' | 'play'
export interface Prop { kind: PropKind; label: string | null }
/** start: the clock second it began; it plays over about 6 s from start, then holds the end state. houses: house numbers
 * (n) in scene.houses order, 0 = the visitor. */
export interface Moment { kind: MomentKind; houses: number[]; label: string | null; start: number }
export const PROP_KINDS: PropKind[] = ['stall', 'mailbox', 'ballot', 'scaffold', 'gallery', 'chessboard', 'stage', 'press', 'garden', 'lanterns', 'cloud', 'signpost']
export const MOMENT_KINDS: MomentKind[] = ['celebrate', 'build', 'send', 'vote', 'win', 'fail', 'gather', 'play']

export const INK = '#1f1d1a'
export const VERMILION = '#b7482a'
export const DOOR: Record<Lab, string> = { anthropic: '#a8700e', openai: '#2a6fc0', google: '#157f58', other: '#78715f' }
const PAPER = '#f3ece0'           // the wall wash: warm paper, never white
const INK_A = 0.85
const GHOST_A = 0.25
export const MAX_HOUSES = 40

// ---------------------------------------------------------------- layout (pure geometry of count, w, h)

export interface Rect { x: number; y: number; w: number; h: number }
export interface HouseSlot {
  n: number                 // 1-based position; seeds the house's shape
  row: 0 | 1                // 0 = front row, 1 = back row
  x: number                 // centre of the walls
  y: number                 // baseline: where the walls meet the ground
  wallW: number; wallH: number; roofH: number; overhang: number
  apex: Pt; eaveL: Pt; eaveR: Pt
  chimney: Pts              // the chimney outline, from the slope up and back down to it
  chimneyTop: Pt            // where the smoke leaves
  door: Rect
  win: Rect
  left: number; right: number; top: number   // the house's extent: eaves and the chimney or apex
  path: Pts                 // the footpath from the road to the door step
}
export interface VillageLayout {
  count: number; w: number; h: number
  unit: number              // plate units: 1 at 440 x 280
  rows: 1 | 2
  pad: { x: number; top: number; bottom: number }
  houseW: number            // the nominal wall width before each house's own variation
  houses: HouseSlot[]
  ground: Pts               // the front ground line
  hill: Pts | null          // the back row's ground line, two rows only
  road: { back: Pts; front: Pts }
  gate: { x: number; y: number; h: number; w: number }   // x = centre, y = feet
  visitor: Pt               // where the visitor's feet stand
  figure: number            // a walking figure's height at the front row
  sky: number               // the highest y anything may reach (smoke, pins)
  gateEnd: number           // the gate zone's right edge: the notice board stands from here to the first house
  board: BoardSpot          // the notice board just inside the gate
}
/** The notice board: a panel on two posts with a little roof, and the sheet pinned to it. */
export interface BoardSpot {
  panel: Rect               // the wooden board
  sheet: Rect               // the pinned sheet, before its tilt
  roof: Pts                 // the little gable over the panel
  posts: [Pts, Pts]         // two posts from the panel down to the ground
  tilt: number              // the sheet's tilt, radians
}

const layoutCache = new Map<string, VillageLayout>()

/** Where every house stands, for a row of `count` houses on a w x h plate. Deterministic; cached by (count, w, h). */
export function layoutHouses(count: number, w: number, h: number): VillageLayout {
  const c = Math.max(1, Math.min(MAX_HOUSES, Math.floor(count)))
  const key = `${c}|${w}|${h}`
  const hit = layoutCache.get(key)
  if (hit) return hit
  const lay = computeLayout(c, w, h)
  if (layoutCache.size > 48) layoutCache.clear()
  layoutCache.set(key, lay)
  return lay
}

// a house is at most ~1.42 wall-widths tall (walls + roof); smoke and pins need ~0.6 of a house above it; the back row
// stands on the line of the front row's ridges (~1.15 wall-widths up), so nothing of the front row covers its doors
const H_PER_W = 1.42, SKY = 0.6, ROW_OFF = 1.2

function computeLayout(count: number, w: number, h: number): VillageLayout {
  const u = Math.min(w / 440, h / 280)
  const pad = { x: Math.max(8 * u, 0.03 * w), top: 6 * u, bottom: 7 * u }
  const roadH = 13 * u, yard = 5 * u
  const room = h - pad.bottom - roadH - yard - 3.2 * u - pad.top   // 3.2 u: the ground's own wave
  const wCap1 = Math.min(0.3 * h, room / (H_PER_W * (1 + SKY)))
  const wCap2 = Math.min(0.3 * h, room / (H_PER_W * (1 + SKY) + ROW_OFF))

  // a house reaches ~0.62 W either side of its centre (eaves, chimney cap): the first and last eaves sit on the margins
  const HALF = 0.62
  const sizeFor = (gateZone: number) => {
    const span = w - pad.x * 2 - gateZone
    const one = Math.min(wCap1, (0.8 * span) / count)
    const rows: 1 | 2 = one >= 34 * u || count <= 2 ? 1 : 2
    // two rows: neighbours in one row stand two steps apart and must clear 1.2 W, eave to eave
    const W = rows === 1 ? one : Math.min(wCap2, (2 * span * 0.97) / (1.2 * count + 2 * HALF * 2 * 1.03 - 1.2))
    // a few houses keep together (at most 1.6 W apart) and the group stands centred in the span
    const dx = count > 1 ? Math.min(1.6 * W, (span - 2 * HALF * W) / (count - 1)) : 0
    const offset = (span - 2 * HALF * W - (count - 1) * dx) / 2
    return { span, dx, W, rows, offset }
  }
  const figureFor = (W: number) => Math.max(10 * u, Math.min(24 * u, W * 0.4))
  // the notice board's own zone, right of the gate: it stands in the foreground grass in front of the road, near the
  // reader, so it can carry words in a readable size without towering over the houses behind it. It is always there
  // (a bare board when no goal is posted), so the row never jumps when a week's goal comes or goes. A resident who
  // talks across the gate stands between the gate and the board.
  const boardW = Math.min(0.23 * w, 116 * u)
  let s = sizeFor(30 * u + boardW + 6 * u + 0.75 * figureFor(sizeFor(30 * u + boardW).W))
  const gateZone = Math.max(30 * u, figureFor(s.W) * 2.1)
  const boardZone = boardW + 6 * u + 0.75 * figureFor(s.W)
  s = sizeFor(gateZone + boardZone)
  const { dx, W, rows, offset } = s
  const figure = figureFor(W)
  const small = 1 - clamp01((W / u - 14) / 50)   // 1 for the smallest houses: they get taller walls, bigger doors and windows

  // the block (sky, houses, yard, road) sits low, with foreground left under the road: enough for the stage's props (a
  // figure and a half tall, standing below the road's near lane) and their one-line tags under their feet, taken from
  // the sky when the sky has it to spare
  const need = W * (H_PER_W * (1 + SKY) + (rows === 2 ? ROW_OFF : 0))
  const spare = Math.max(0, room - need)
  const fg = stageDepth(figure, u, roadH, pad.bottom)
  const yF = h - pad.bottom - roadH - yard - 3.2 * u - Math.max(spare * 0.3, Math.min(spare, fg - pad.bottom))
  const rowOff = rows === 2 ? W * (1.04 + 0.12 * small) : 0
  const gAmp = 2.4 * u
  const g = (x: number) => yF + gAmp * (Math.sin((x / w) * Math.PI * 1.8 + 0.6) * 0.7 + Math.sin((x / w) * Math.PI * 4.2 + 1.9) * 0.3)
  const hillY = (x: number) => g(x) - rowOff + 1.6 * u * Math.sin((x / w) * Math.PI * 2.6 + 2.2)
  const roadC = (x: number) => g(x) + yard + roadH / 2 + 0.8 * u * Math.sin((x / w) * Math.PI * 1.3)

  const gateEnd = pad.x + gateZone
  const x0 = gateEnd + boardZone
  const houses: HouseSlot[] = []
  for (let i = 0; i < count; i++) {
    const n = i + 1
    const rnd = rngOf(`house-${n}`)
    const row: 0 | 1 = rows === 2 && i % 2 === 1 ? 1 : 0
    const x = x0 + offset + HALF * W + i * dx
    const y = row === 1 ? hillY(x) : g(x)
    const wallW = W * (0.9 + 0.12 * rnd())
    const wallH = wallW * (0.56 + 0.1 * rnd() + 0.12 * small)
    const roofH = wallW * (0.42 + 0.12 * rnd())
    const overhang = wallW * 0.08
    const apexDx = (rnd() - 0.5) * wallW * 0.08
    const eaveL: Pt = [x - wallW / 2 - overhang, y - wallH]
    const eaveR: Pt = [x + wallW / 2 + overhang, y - wallH]
    const apex: Pt = [x + apexDx, y - wallH - roofH]
    // the chimney stands on the right slope and rises a little above it, never a tower; in the front row of two it
    // stands by the ridge, so its smoke climbs between the back row's houses instead of over their doors
    const cf = rows === 2 && row === 0 ? 0.06 + 0.1 * rnd() : 0.34 + 0.26 * rnd()
    const cw = Math.max(1.8 * u, wallW * 0.12)
    const cxL = lerp(apex[0], eaveR[0], cf)
    const cxR = cxL + cw
    const slopeY = (xx: number) => lerp(apex[1], eaveR[1], (xx - apex[0]) / (eaveR[0] - apex[0]))
    const cTop = slopeY(cxL) - wallW * (0.17 + 0.07 * rnd()) - 1.2 * u
    const lip = cw > 4 * u ? cw * 0.16 : 0
    const capH = lip ? Math.max(1.2 * u, cw * 0.22) : 0
    const chimney: Pts = lip
      ? [[cxL, slopeY(cxL)], [cxL, cTop], [cxL - lip, cTop], [cxL - lip, cTop - capH], [cxR + lip, cTop - capH], [cxR + lip, cTop], [cxR, cTop], [cxR, slopeY(cxR)]]
      : [[cxL, slopeY(cxL)], [cxL, cTop], [cxR, cTop], [cxR, slopeY(cxR)]]
    const doorLeft = rnd() < 0.72
    const dw = wallW * (0.24 + 0.05 * small), dh = wallH * (0.62 + 0.04 * small)
    const ws = wallW * (0.24 + 0.06 * small)
    const doorCx = x + (doorLeft ? -1 : 1) * wallW * 0.2
    const winCx = x + (doorLeft ? 1 : -1) * wallW * 0.21
    const door: Rect = { x: doorCx - dw / 2, y: y - dh, w: dw, h: dh }
    const win: Rect = { x: winCx - ws / 2, y: y - wallH * 0.6 - ws / 2, w: ws, h: ws }
    // the footpath: from the road a little to one side, onto the road's back edge, up to the door step
    const side = doorLeft ? -1 : 1
    const startX = Math.max(x0, Math.min(w - pad.x - 6 * u, doorCx + side * Math.max(Math.min(dx, W) * 0.9, wallW * 0.45)))
    // a back-row house is reached up the gap between the two front houses below it, then along its own ground
    const path: Pts = row === 1
      ? [[startX, roadC(startX)], [x, roadC(x) - roadH * 0.3], [x, y + 0.6 * u], [doorCx, y - 0.3 * u]]
      : [[startX, roadC(startX)], [doorCx, roadC(doorCx) - roadH * 0.3], [doorCx, y - 0.3 * u]]
    const top = Math.min(apex[1], cTop - capH)
    houses.push({
      n, row, x, y, wallW, wallH, roofH, overhang, apex, eaveL, eaveR, chimney, chimneyTop: [(cxL + cxR) / 2, cTop - capH],
      door, win, left: eaveL[0], right: Math.max(eaveR[0], cxR + lip), top, path,
    })
  }

  const sample = (f: (x: number) => number, xa: number, xb: number): Pts => {
    const n = Math.max(2, Math.ceil((xb - xa) / (18 * u)))
    const out: Pts = []
    for (let k = 0; k <= n; k++) { const x = lerp(xa, xb, k / n); out.push([x, f(x)]) }
    return out
  }
  const gateH = Math.max(15 * u, figure * 1.5)
  const gateW = gateH * 0.6
  const gateX = gateEnd - Math.max(8 * u, gateW * 0.5 + 3 * u)
  const visitorX = gateX - gateW / 2 - Math.max(6 * u, figure * 0.42)
  const lastRight = houses[houses.length - 1].right
  const top = Math.min(...houses.map((s) => s.top))
  // the board: right-aligned in its zone, its posts planted in the grass in front of the road
  const bw = boardW, bh = bw >= 64 ? Math.max(bw * 0.5, 50) : bw * 0.5
  const bx = x0 - 4 * u - bw, bcx = bx + bw / 2
  const front = (x: number) => roadC(x) + roadH / 2
  const feet = (x: number) => Math.min(h - pad.bottom - 1.5 * u, front(x) + 3 * u + 0.3 * Math.max(0, h - pad.bottom - front(x) - 3 * u))
  const roofH = bw * 0.12
  const bBottom = Math.max(pad.top + roofH + bh + 1 * u, feet(bcx) - Math.max(6 * u, bh * 0.3))
  const panel: Rect = { x: bx, y: bBottom - bh, w: bw, h: bh }
  const roof: Pts = [[bx - bw * 0.05, panel.y + 0.4 * u], [bcx, panel.y - roofH], [bx + bw * 1.05, panel.y + 0.4 * u]]
  const pxs = [bx + bw * 0.2, bx + bw * 0.8]
  const posts = pxs.map((x): Pts => [[x, bBottom], [x, Math.max(bBottom + 2 * u, feet(x))]]) as [Pts, Pts]
  const inset = bw * 0.05
  const sheet: Rect = { x: bx + inset, y: panel.y + bh * 0.09, w: bw - inset * 2, h: bh * 0.84 }
  return {
    count, w, h, unit: u, rows, pad, houseW: W, houses,
    ground: sample(g, gateX + gateW * 0.5, Math.min(w - pad.x, lastRight + 10 * u)),
    hill: rows === 2 ? sample(hillY, x0 - 4 * u, Math.min(w - pad.x, lastRight + 6 * u)) : null,
    road: {
      back: sample((x) => roadC(x) - roadH / 2, pad.x, w - pad.x),
      front: sample((x) => roadC(x) + roadH / 2, pad.x + 2 * u, w - pad.x),
    },
    gate: { x: gateX, y: roadC(gateX) + roadH * 0.4, h: gateH, w: gateW },
    visitor: [visitorX, roadC(visitorX) + roadH * 0.2],
    figure,
    sky: Math.max(pad.top, top - W * H_PER_W * SKY),
    gateEnd,
    board: { panel, sheet, roof, posts, tilt: -0.018 },
  }
}

// ---------------------------------------------------------------- the finding pin (pure geometry of the layout)

/** A pin: a diamond centred at (x, y), 0.8 r wide either side and 1.25 r tall either side, on a stem down to `tip`. */
export interface PinSpot { x: number; y: number; r: number; tip: Pt }

/** The diamond's four corners: top, right, bottom, left. */
export const pinPts = (p: PinSpot): Pts =>
  [[p.x, p.y - p.r * 1.25], [p.x + p.r * 0.8, p.y], [p.x, p.y + p.r * 1.25], [p.x - p.r * 0.8, p.y]]

const pinCache = new WeakMap<VillageLayout, Map<number, PinSpot>>()

/**
 * Where the pin of house `i` (0-based slot) stands. One row, or the back row of two: centred over the house, above its
 * chimney or ridge. The front row of two has the back row's houses above it, so its pin climbs from its own ridge up
 * the gap between the two back houses beside it and stops at the lowest spot where the diamond clears their walls,
 * roofs and chimneys (and its own chimney) by a margin: low in a wide gap, up between the back roofs in a narrow one.
 * The stem always runs down to its own ridge, so the pin reads as this house's. Pure; cached with the layout.
 */
export function pinSpot(lay: VillageLayout, i: number): PinSpot {
  let byHouse = pinCache.get(lay)
  if (!byHouse) { byHouse = new Map(); pinCache.set(lay, byHouse) }
  const hit = byHouse.get(i)
  if (hit) return hit
  const spot = computePin(lay, i)
  byHouse.set(i, spot)
  return spot
}

function computePin(lay: VillageLayout, i: number): PinSpot {
  const s = lay.houses[i], u = lay.unit
  const r0 = Math.max(4 * u, s.wallW * 0.11)
  const stem = Math.max(2.5 * u, s.roofH * 0.16)
  const lift = Math.max(1.5 * u, s.roofH * 0.1)
  if (lay.rows === 1 || s.row === 1) {
    const tip: Pt = [s.apex[0], s.top - lift]
    return { x: s.apex[0], y: tip[1] - stem - r0 * 1.25, r: r0, tip }
  }
  const tip: Pt = [s.apex[0], s.apex[1] - lift]
  // a longer stem than one row's: the diamond stands among the back row, so the stem is what ties it to this house
  const stem2 = Math.max(4 * u, s.roofH * 0.3)
  // the two back houses beside it first: they are what a pin fails on, so the search rejects a spot early
  const near = [i - 1, i + 1, i - 2, i + 2].map((k) => lay.houses[k]).filter((h): h is HouseSlot => h !== undefined)
  const obstacles: Shape[] = near.flatMap(silhouette).map(shape)
  const neighbours = obstacles.length
  obstacles.push(shape(boxPts(s.chimney)))
  const a = lay.houses[i - 1], b = lay.houses[i + 1]
  const c0 = a && b ? (a.eaveR[0] + b.eaveL[0]) / 2 : s.apex[0]
  const xs = [c0]
  for (let k = 1; k <= 3; k++) xs.push(c0 + k * 0.4 * u, c0 - k * 0.4 * u)
  for (const f of [1, 0.85, 0.7, 0.6]) {
    const r = Math.max(2.4 * u, r0 * f)
    const m = Math.max(1.2 * u, 0.3 * r)
    for (let foot = tip[1] - stem2; foot - 2.5 * r >= lay.pad.top; foot -= 0.4 * u) {
      for (const x of xs) {
        const spot: PinSpot = { x, y: foot - 1.25 * r, r, tip }
        const d = shape(pinPts(spot)), st = shape([[x, foot], tip])
        // the stem may rise beside its own chimney (it is the same house); the diamond clears everything
        if (obstacles.every((o, k) => apart(d, o, m) && (k >= neighbours || apart(st, o, 0.4 * u)))) return spot
      }
    }
  }
  return { x: s.apex[0], y: tip[1] - stem - r0 * 1.25, r: r0, tip }   // never reached in any layout the tests sweep
}

/** A convex shape and its box. */
interface Shape { pts: Pts; x0: number; y0: number; x1: number; y1: number }
function shape(pts: Pts): Shape {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity
  for (const [x, y] of pts) { if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y }
  return { pts, x0, y0, x1, y1 }
}
/** A house's solid parts as convex shapes: the walls, the roof, the chimney's box. */
function silhouette(h: HouseSlot): Pts[] {
  const L = h.x - h.wallW / 2, R = h.x + h.wallW / 2, top = h.y - h.wallH
  return [[[L, h.y], [L, top], [R, top], [R, h.y]], [h.eaveL, h.apex, h.eaveR], boxPts(h.chimney)]
}
function boxPts(pts: Pts): Pts {
  const b = shape(pts)
  return [[b.x0, b.y0], [b.x1, b.y0], [b.x1, b.y1], [b.x0, b.y1]]
}
/**
 * Whether two convex shapes stand at least m apart: some axis (the two boxes' axes, then every edge normal) shows a
 * gap of m or more between their shadows. A shadow's gap is never more than the true distance, so `true` is a promise;
 * a two-point shape is a segment.
 */
function apart(A: Shape, B: Shape, m: number): boolean {
  if (B.x0 - A.x1 >= m || A.x0 - B.x1 >= m || B.y0 - A.y1 >= m || A.y0 - B.y1 >= m) return true
  for (const P of [A.pts, B.pts]) {
    for (let k = 0; k < P.length; k++) {
      const p = P[k], q = P[(k + 1) % P.length]
      let nx = q[1] - p[1], ny = p[0] - q[0]
      const len = Math.hypot(nx, ny)
      if (len < 1e-9) continue
      nx /= len; ny /= len
      let aMin = Infinity, aMax = -Infinity, bMin = Infinity, bMax = -Infinity
      for (const v of A.pts) { const d = v[0] * nx + v[1] * ny; if (d < aMin) aMin = d; if (d > aMax) aMax = d }
      for (const v of B.pts) { const d = v[0] * nx + v[1] * ny; if (d < bMin) bMin = d; if (d > bMax) bMax = d }
      if (bMin - aMax >= m || aMin - bMax >= m) return true
    }
  }
  return false
}

// ---------------------------------------------------------------- life (pure geometry of the layout, the scene and the clock)

/** The road's y at x: f = 0 its back edge, 1 its front edge. */
export function roadAt(lay: VillageLayout, x: number, f = 0.5): number {
  return lerp(sampleY(lay.road.back, x), sampleY(lay.road.front, x), f)
}
function sampleY(pts: Pts, x: number): number {
  if (x <= pts[0][0]) return pts[0][1]
  for (let i = 1; i < pts.length; i++) {
    if (x <= pts[i][0]) { const a = pts[i - 1], b = pts[i]; return lerp(a[1], b[1], (x - a[0]) / (b[0] - a[0] || 1)) }
  }
  return pts[pts.length - 1][1]
}

/** The board by the gate. */
export const boardRect = (lay: VillageLayout): BoardSpot => lay.board

export type WalkKind = 'arriving' | 'leaving' | 'away' | 'visit'
/** A walker at one moment: feet, which way it faces (0 = toward the reader), the stride's phase, and its pose. */
export interface WalkerPose { at: Pt; facing: -1 | 0 | 1; phase: number; pose: 'walk' | 'stand' | 'look'; depth: number }

/** The walk's timetable in seconds: arriving walks in over 5 s; leaving waits 1.5 s at the door and is out by 6 s; a
 * one-day resident (arriving and leaving) does both; away walks off toward the far end over ~6 s. */
export const WALK = { arrive: 5, wait: 1.5, leave: 4.5, away: 6 } as const

/** The path a walker of house `i` takes, in walking order. `rank` orders the away walkers: 0 = the farthest spot. */
export function walkerPath(lay: VillageLayout, i: number, kind: WalkKind, rank = 0): Pts {
  const s = lay.houses[i]
  if (kind === 'arriving' || kind === 'visit') return s.path
  const out = s.path.slice().reverse()
  if (kind === 'leaving') return out
  const f = lay.figure
  const endX = Math.max(lay.board.panel.x + lay.board.panel.w + f, lay.w - lay.pad.x - 0.5 * f - rank * 0.85 * f)
  const start = out[out.length - 1]
  const lane: Pt = [start[0] + Math.sign(endX - start[0]) * Math.min(Math.abs(endX - start[0]), 0.6 * f), roadAt(lay, start[0], 0.7)]
  return [...out, lane, [endX, roadAt(lay, endX, 0.7)]]
}

const walkCache = new WeakMap<VillageLayout, Map<string, { path: Pts; len: number }>>()
function pathOf(lay: VillageLayout, i: number, kind: WalkKind, rank: number) {
  let m = walkCache.get(lay)
  if (!m) { m = new Map(); walkCache.set(lay, m) }
  const key = `${i}|${kind}|${rank}`
  let hit = m.get(key)
  if (!hit) { const path = walkerPath(lay, i, kind, rank); hit = { path, len: pathLength(path) }; m.set(key, hit) }
  return hit
}

/** Where the walker of house `i` stands at `clock` seconds. Pure: the same layout, kind, clock and rank give the same pose. */
export function walkerPos(lay: VillageLayout, i: number, kind: WalkKind, clock: number, rank = 0): WalkerPose {
  const { path, len } = pathOf(lay, i, kind, rank)
  const c = Math.max(0, clock)
  let p: number, moving: boolean, back = false
  if (kind === 'arriving') { p = ease.inOut(clamp01(c / WALK.arrive)); moving = c < WALK.arrive }
  else if (kind === 'leaving') { const q = (c - WALK.wait) / WALK.leave; p = ease.inOut(clamp01(q)); moving = q > 0 && q < 1 }
  else if (kind === 'visit') {
    const out0 = WALK.arrive + WALK.wait
    if (c < out0) { p = ease.inOut(clamp01(c / WALK.arrive)); moving = c < WALK.arrive }
    else { const q = (c - out0) / WALK.leave; p = 1 - ease.inOut(clamp01(q)); moving = q < 1; back = true }
  } else {
    // a long road takes a little longer, a short one a little less, so every away walker keeps a gentle pace
    const dur = WALK.away * Math.max(0.75, Math.min(1.35, len / (12 * lay.figure)))
    p = ease.inOut(clamp01(c / dur)); moving = c < dur
  }
  const at = pointAt(path, p)
  const s = lay.houses[i]
  const depth = s.row === 1 ? clamp01((s.path[0][1] - at[1]) / Math.max(1, s.path[0][1] - s.path[s.path.length - 1][1])) : 0
  if (!moving) {
    if (kind === 'away') return { at, facing: 1, phase: 0, pose: 'look', depth }
    return { at, facing: 0, phase: 0, pose: 'stand', depth }
  }
  const ahead = pointAt(path, Math.min(1, p + 0.02)), behind = pointAt(path, Math.max(0, p - 0.02))
  let hx = ahead[0] - behind[0]
  const hy = ahead[1] - behind[1]
  if (back) hx = -hx
  const facing = Math.abs(hx) > Math.abs(hy) * 0.8 ? (hx < 0 ? -1 : 1) : 0
  // the stride follows the distance walked, so the legs keep time with the feet whatever the easing does
  const phase = Math.PI / 2 + ((p * len) / (lay.figure * 0.42)) * Math.PI
  return { at, facing, phase, pose: 'walk', depth }
}

/** A puff of smoke: centre, radius, its own alpha (0..1, multiplied into the ink), a wobble phase, and its birth number. */
export interface Puff { x: number; y: number; r: number; a: number; ph: number; id: number }
/** A new puff every PUFF_EVERY seconds; `smoke` sets how many are in flight (1 to 3) and how big they grow. */
export const PUFF_EVERY = 2.4

/**
 * The puffs over the chimney of house `i` at `clock` seconds, newest (lowest) first. Each is born at the chimney,
 * rises and drifts a little downwind with a noise wobble, grows, and fades as it climbs. The column always stays on
 * the plate: it shortens under the top margin and leans back from the right one. Pure.
 */
export function puffs(lay: VillageLayout, i: number, smoke: number, clock: number): Puff[] {
  const v = clamp01(smoke)
  if (v <= 0) return []
  const s = lay.houses[i], u = lay.unit, w = lay.w
  const count = v < 0.34 ? 1 : v < 0.67 ? 2 : 3
  const front2 = lay.rows === 2 && s.row === 0
  const S = (s.wallH + s.roofH) * (0.2 + 0.2 * v) * (front2 ? 0.78 : 1)
  const top = s.chimneyTop
  // the column's reach, scaled down together when the sky above is short
  let rise = S * (front2 ? 1.25 : 1.6), r0 = S * 0.27, r1 = S * 0.58
  const avail = top[1] - 0.6 * u - lay.pad.top * 0.6
  const need = rise + r1 * 2.1 + r0
  if (need > avail) { const k = Math.max(0.2, avail / need); rise *= k; r0 *= k; r1 *= k }
  const wob = S * 0.1
  const xMax = w - lay.pad.x * 0.6 - r1 * 1.12 - wob, xMin = lay.pad.x * 0.6 + r1 * 1.12 + wob
  const drift = Math.max(xMin - top[0], Math.min(front2 ? S * 0.12 : S * 0.75, xMax - top[0]))
  const life = count * PUFF_EVERY + PUFF_EVERY * 0.5
  const off = ((s.n * 0.618034) % 1) * PUFF_EVERY   // houses breathe out of step
  const nz = noise1(seedOf(`house-${s.n}-smoke`))
  const out: Puff[] = []
  const jLo = Math.ceil((clock - off - life) / PUFF_EVERY), jHi = Math.floor((clock - off) / PUFF_EVERY)
  for (let j = jHi; j >= jLo; j--) {
    const age = clock - off - j * PUFF_EVERY
    if (age < 0 || age >= life) continue
    const q = age / life
    const h = (((j * 2654435761) ^ (s.n * 40503)) >>> 0) / 4294967296   // the puff's own draw: size and phase
    const r = lerp(r0, r1, Math.pow(q, 0.7)) * (0.9 + 0.2 * h)
    const climb = rise * (1 - Math.pow(1 - q, 1.3))
    const x = top[0] + drift * Math.pow(q, 1.3) + nz(j * 3.7 + age * 0.55) * wob * Math.min(1, q * 2)
    const y = top[1] - 0.6 * u - r * 0.85 - climb
    const a = smooth(0, 0.08, q) * (1 - smooth(0.6, 1, q))
    out.push({ x, y, r, a, ph: h * Math.PI * 2, id: j })
  }
  return out
}
const smooth = (e0: number, e1: number, x: number) => { const t = clamp01((x - e0) / (e1 - e0)); return t * t * (3 - 2 * t) }

/** A talking pair: two figures' feet (left, right), their height, the speech bubble between their heads. */
export interface PairSpot {
  a: number; b: number; w: number
  left: Pt; right: Pt; size: number
  gate: boolean              // the left figure is the visitor at the gate
  turn: number               // at the gate: this resident's place in the turns (0 = the strongest talks first); 0 on the road
  turns: number              // at the gate: how many residents take turns there today; 1 on the road
  bubble: { x: number; y: number; rx: number; ry: number }
}

/** The seconds one resident holds the gate: the visitor speaks, the resident answers (two breaths of the bubble). */
export const GATE_TURN = 5.2
const GATE_FADE = 0.4

/**
 * Who stands at the gate at `clock` when `n` residents take turns there: the turn's index `k`, the seconds into that
 * turn, and how present the resident is (0..1). Each resident holds the gate for one exchange, fades out at its end, and
 * the next fades in where they stood; the first is there in full from the first moment. One resident never leaves. Pure.
 */
export function gateTurn(clock: number, n: number): { k: number; local: number; a: number } {
  const c = Math.max(0, clock)
  if (n <= 1) return { k: 0, local: c, a: 1 }
  const j = Math.floor(c / GATE_TURN)
  const local = c - j * GATE_TURN
  const fadeIn = j === 0 ? 1 : smooth(0, GATE_FADE, local)
  const fadeOut = 1 - smooth(GATE_TURN - GATE_FADE, GATE_TURN, local)
  return { k: j % n, local, a: Math.min(fadeIn, fadeOut) }
}

/** The visitor's feet and height under the lens. */
export function visitorSpot(lay: VillageLayout, lens: VillageScene['lens']): { at: Pt; size: number } {
  const u = lay.unit
  const humans = lens === 'humans'
  const size = humans ? Math.max(lay.figure * 1.6, 22 * u) : lay.figure
  const vx = Math.max(lay.pad.x + size * 0.25, lay.visitor[0] - (size - lay.figure) * 0.25)
  return { at: [vx, lay.visitor[1]], size }
}

const pairCache = new WeakMap<VillageLayout, Map<string, PairSpot[]>>()
/**
 * Where today's talking pairs stand. Two residents meet on the road between their houses (in front of a back-row
 * house, never up on the hill), turned toward each other; a resident who talks with the humans comes to the gate and
 * talks across it with the visitor. A pair keeps clear of the footpaths of today's walkers and of the other pairs when
 * it can, and stands as near its houses as it can. Pure; cached per layout by the scene's walkers and pairs.
 */
export function pairSpots(lay: VillageLayout, scene: Pick<VillageScene, 'houses' | 'pairs' | 'lens'> & Partial<Pick<VillageScene, 'moments' | 'props'>>): PairSpot[] {
  const houses = scene.houses.slice(0, lay.count)
  const pairs = (scene.pairs ?? []).slice(0, 3)
  if (!pairs.length) return []
  const stage = `${(scene.moments ?? []).map((m) => `${m.kind}:${(m.houses ?? []).join('.')}`).join(',')}|${(scene.props ?? []).map((p) => p.kind).join(',')}`
  const key = `${scene.lens}|${pairs.map((p) => `${p.a}-${p.b}-${p.w}`).join(',')}|${houses.map((h) => `${h.n}${h.present ? '' : 'e'}${h.arriving ? 'a' : ''}${h.leaving ? 'l' : ''}${h.away ? 'w' : ''}`).join(',')}|${stage}`
  let m = pairCache.get(lay)
  if (!m) { m = new Map(); pairCache.set(lay, m) }
  const hit = m.get(key)
  if (hit) return hit
  if (m.size > 64) m.clear()
  const out = computePairs(lay, houses, pairs, scene.lens, momentBlocks(lay, houses, scene.moments, scene.props))
  m.set(key, out)
  return out
}

function computePairs(lay: VillageLayout, houses: HouseScene[], pairs: VillageScene['pairs'], lens: VillageScene['lens'], stage: [number, number][] = []): PairSpot[] {
  const u = lay.unit, f = lay.figure
  const slotOf = (n: number) => houses.findIndex((h) => h.n === n)
  // the road stretches today's walkers use: the foot of each footpath, and the stretches the moments' residents use
  const blocked: [number, number][] = [...stage]
  houses.forEach((h, i) => {
    if (!h.arriving && !h.leaving && !h.away) return
    const p = lay.houses[i].path
    blocked.push([Math.min(p[0][0], p[1][0]) - 0.3 * f, Math.max(p[0][0], p[1][0]) + 0.3 * f])
  })
  const gap = f * 0.95
  const lo = lay.board.panel.x + lay.board.panel.w + 0.5 * f + gap / 2, hi = lay.w - lay.pad.x - 0.4 * f - gap / 2
  const head = (feet: Pt, size: number): Pt => [feet[0], feet[1] - size * 0.87]
  const bubbleOver = (l: Pt, r: Pt, size: number, w: number) => {
    const rx = Math.max(3.2 * u, size * (0.3 + 0.2 * clamp01(w)))
    const ry = rx * 0.66
    const hl = head(l, size), hr = head(r, size)
    const y = Math.max(lay.pad.top + ry * 1.3 + 0.5 * u, Math.min(hl[1], hr[1]) - size * 0.22 - ry)
    const x = Math.max(lay.pad.x + rx * 1.3, Math.min(lay.w - lay.pad.x - rx * 1.3, (hl[0] + hr[0]) / 2))
    return { x, y, rx, ry }
  }
  // across the gate: the visitor outside, one resident inside past the open leaf, clear of the board. There is room for
  // one only, so when several residents talk with the humans today they take turns at that spot (see gateTurn), the
  // strongest talk first; they share the spot and the bubble, so nobody stands behind the board or under an empty bubble
  const gateIn = pairs
    .map((pr, k) => ({ pr, k, b0: Math.max(pr.a, pr.b) }))
    .filter(({ pr, b0 }) => Math.min(pr.a, pr.b) === 0 && b0 > 0 && slotOf(b0) >= 0)
    .sort((p, q) => q.pr.w - p.pr.w || p.k - q.k)
  const out: PairSpot[] = []
  if (gateIn.length) {
    const v = visitorSpot(lay, lens)
    const g = lay.gate
    const rx = Math.min(g.x + g.w / 2 + g.w * 0.58 + 0.45 * f, lay.board.panel.x - 0.4 * f)
    const right: Pt = [rx, roadAt(lay, rx, 0.55)]
    const arch = g.y - g.h - g.w * 0.25
    gateIn.forEach(({ pr, b0 }, turn) => {
      const bub = bubbleOver(v.at, right, f, pr.w)
      bub.y = Math.max(lay.pad.top + bub.ry * 1.3 + 0.5 * u, Math.min(bub.y, arch - bub.ry * 1.2))
      out.push({ a: 0, b: b0, w: pr.w, left: v.at, right, size: f, gate: true, turn, turns: gateIn.length, bubble: bub })
    })
  }
  for (const pr of pairs) {
    const a0 = Math.min(pr.a, pr.b), b0 = Math.max(pr.a, pr.b)
    if (a0 === b0 || a0 === 0) continue
    const ia = slotOf(a0), ib = slotOf(b0)
    if (ia < 0 || ib < 0) continue
    const da = lay.houses[ia].door, db = lay.houses[ib].door
    const mid = Math.max(lo, Math.min(hi, (da.x + da.w / 2 + db.x + db.w / 2) / 2))
    const taken = [...blocked, ...out.filter((o) => !o.gate).map((o): [number, number] => [o.left[0] - 0.35 * f, o.right[0] + 0.35 * f])]
    const overlap = (x: number) => {
      const x0 = x - gap / 2 - 0.35 * f, x1 = x + gap / 2 + 0.35 * f
      let o = 0
      for (const [p, q] of taken) o += Math.max(0, Math.min(x1, q) - Math.max(x0, p))
      return o
    }
    let best = mid, bestO = Infinity
    for (let k = 0; k <= 40; k++) {
      const d = Math.ceil(k / 2) * 0.35 * f * (k % 2 ? 1 : -1)
      const x = mid + d
      if (x < lo || x > hi) continue
      const o = overlap(x)
      if (o < bestO - 1e-9) { best = x; bestO = o }
      if (o === 0) break
    }
    const lx = best - gap / 2, rx = best + gap / 2
    const left: Pt = [lx, roadAt(lay, lx, 0.32)], right: Pt = [rx, roadAt(lay, rx, 0.32)]
    out.push({ a: a0, b: b0, w: pr.w, left, right, size: f, gate: false, turn: 0, turns: 1, bubble: bubbleOver(left, right, f, pr.w) })
  }
  return out
}

// ---------------------------------------------------------------- the stage (pure geometry of the layout, the scene and the clock)
// Props stand in the foreground grass in front of the road, right of the board, for the whole chapter. Moments play
// once when their beat lights (from `start`, over MOMENT_DUR seconds) and then hold their end state.

/** Seconds a moment plays from its start; after that it holds its end state. */
export const MOMENT_DUR = 6
/** The gather zone right of the board, in figures: up to four residents stand there; the props stand past it. */
const GATHER_W = 2.6
const boardRight = (lay: VillageLayout) => lay.board.panel.x + lay.board.panel.w

/** A ground slot in the foreground grass: centre x, feet y, the height of what it holds, the slot's width. */
export interface GroundSlot { x: number; y: number; size: number; w: number }
/** A prop's height, in figures. */
export const PROP_H = 1.5
/** A prop's tag: its type size (9 to 12 px by the prop's size) and its height on one line (or scribbles). */
export const tagPx = (S: number) => Math.max(9, Math.min(12, S * 0.42))
export const tagHeight = (S: number, u: number) => Math.max(tagPx(S) * 1.54, 5 * u, 0.4 * S)
/** How deep the foreground under the road's front edge must be for a prop and its tag: the prop stands below the road's
 * near lane (a fifth of the road above its front edge), the tag hangs under its feet, a margin under the tag. */
function stageDepth(f: number, u: number, roadH: number, padBottom: number): number {
  const S = PROP_H * f
  return S * 1.04 + tagHeight(S, u) + padBottom * 0.6 + 2 * u - 0.2 * roadH
}
const slotCache = new WeakMap<VillageLayout, GroundSlot[]>()
/**
 * The five ground slots past the gather zone, evenly across the grass in front of the road. A slot's thing stands a
 * figure and a half tall, its feet low enough that its top stays under the road's walking lane and high enough that its
 * one-line tag hangs whole inside the plate; never wider than its slot. Pure.
 */
export function groundSlots(lay: VillageLayout): GroundSlot[] {
  const hit = slotCache.get(lay)
  if (hit) return hit
  const u = lay.unit, f = lay.figure
  const x0 = boardRight(lay) + GATHER_W * f, x1 = lay.w - lay.pad.x
  const sw = (x1 - x0) / 5
  const S0 = Math.min(PROP_H * f, (sw * 0.94) / 1.45)
  const out: GroundSlot[] = []
  for (let k = 0; k < 5; k++) {
    const x = x0 + (k + 0.5) * sw
    const low = lay.h - lay.pad.bottom * 0.6 - tagHeight(S0, u) - 0.04 * S0 - 0.5 * u
    // never up on the road's near lane anywhere along the prop's width (the road waves)
    let lane = -Infinity
    for (let j = 0; j <= 8; j++) lane = Math.max(lane, roadAt(lay, x + (j / 8 - 0.5) * 1.46 * S0, 0.8))
    // the feet a little under the lane when the grass is deep, never so low that the tag leaves the plate
    const y = Math.min(low, lane + S0 + 1 * u + 0.35 * Math.max(0, low - lane - S0 - 1 * u))
    const size = Math.max(3 * u, Math.min(S0, y - lane - 1 * u))
    out.push({ x, y, size, w: sw })
  }
  slotCache.set(lay, out)
  return out
}

/** A thing on the stage: a chapter's prop, or a fixture a moment brings when the chapter has no such prop. */
export interface StageSpot { kind: PropKind; label: string | null; slot: GroundSlot; k: number; fixture: MomentKind | null }
const PROP_SLOTS = [[], [2], [1, 3], [0, 2, 4]]
/**
 * Where the props stand (one in the middle, two apart, three spaced), and the fixtures: a vote with no ballot prop
 * brings a ballot box, a game with no chessboard brings a table, a send with no mailbox brings a mailbox, and a build
 * that names nobody in a chapter where a group gathers at the board brings its scaffold, each into a free slot. Pure.
 */
export function stageSpots(lay: VillageLayout, props: Prop[] | undefined, moments: Moment[] | undefined): StageSpot[] {
  const slots = groundSlots(lay)
  const ps = (props ?? []).filter((p) => PROP_KINDS.includes(p.kind)).slice(0, 3)
  const ks = PROP_SLOTS[ps.length]
  const out: StageSpot[] = ps.map((p, j) => ({ kind: p.kind, label: p.label ?? null, slot: slots[ks[j]], k: ks[j], fixture: null }))
  const used = new Set(out.map((o) => o.k))
  const ms = moments ?? []
  // a build that names nobody raises its frame at the board's side; when a group gathers at the board, in a free slot
  const frameOut = ms.some((m) => m.kind === 'gather') && ms.some((m) => m.kind === 'build' && !(m.houses ?? []).some((n) => n > 0))
  for (const [mk, pk] of [['vote', 'ballot'], ['play', 'chessboard'], ['send', 'mailbox'], ['build', 'scaffold']] as const) {
    if (mk === 'build' && !frameOut) continue
    if (!ms.some((m) => m.kind === mk) || out.some((o) => o.kind === pk)) continue
    const k = [1, 3, 2, 0, 4].find((q) => !used.has(q))
    if (k === undefined) continue
    used.add(k)
    out.push({ kind: pk, label: null, slot: slots[k], k, fixture: mk })
  }
  return out
}

/** The slot indices of a moment's residents: founded, present houses only, the visitor (0) left out, at most `max`. */
function residentsOf(houses: HouseScene[], m: Moment, max = 4): number[] {
  const out: number[] = []
  for (const n of m.houses ?? []) {
    if (!(n > 0)) continue
    const i = houses.findIndex((h) => h.n === n)
    if (i < 0 || !houses[i].present || out.includes(i)) continue
    out.push(i)
    if (out.length >= max) break
  }
  return out
}

/**
 * A moment's cast: the residents it names (founded, present, at most 4). A moment that names nobody present (the
 * writing model could not name anyone) still plays: gather and vote take the present residents nearest the board (up to
 * 6), celebrate takes everyone present, play the two nearest the table (or the board), send the three nearest the gate.
 * A build, a win or a fail that names nobody plays at the stage itself (the scaffold prop or the board) with nobody: [].
 */
export function castOf(lay: VillageLayout, houses: HouseScene[], m: Moment, spots: StageSpot[]): number[] {
  const named = residentsOf(houses, m)
  if (named.length) return named
  const present: number[] = []
  for (let i = 0; i < Math.min(houses.length, lay.count); i++) if (houses[i].present) present.push(i)
  const byX = (x: number) => present.slice().sort((p, q) => Math.abs(lay.houses[p].x - x) - Math.abs(lay.houses[q].x - x) || p - q)
  switch (m.kind) {
    case 'gather': case 'vote': return byX(boardRight(lay)).slice(0, 6)
    case 'celebrate': return present
    case 'send': return byX(lay.gate.x).slice(0, 3)
    case 'play': {
      const t = spots.find((sp) => sp.kind === 'chessboard')
      return byX(t ? t.slot.x : boardRight(lay)).slice(0, 2)
    }
    default: return []
  }
}

/** How far into its play a moment is: null before it starts, then seconds up to MOMENT_DUR (held there). */
export function momentTime(m: Moment, clock: number): number | null {
  const local = clock - (Number.isFinite(m.start) ? m.start : 0)
  if (local < 0) return null
  return Math.min(MOMENT_DUR, local)
}

export type FigPose = 'walk' | 'stand' | 'talk' | 'look' | 'cheer' | 'sit' | 'hammer' | 'reach'
/** A resident in a moment: feet, height, pose, presence (0..1, in eighths), and the layer it is drawn in (1 = with the
 * back row, 0 = with the front row, 2 = on the ground in front of the road). */
export interface MomentFigure { n: number; at: Pt; size: number; facing: -1 | 0 | 1; pose: FigPose; phase: number; gesture: number; hop: number; a: number; layer: 0 | 1 | 2 }

/**
 * The scaffold a build raises by its house: the frame's x span, its foot, its height, which side its flag flies, where
 * the builder stands and faces. A front-row house with clear grass beside its walls on the window side (or the other
 * side) gets the frame there, when the frame, its overhanging planks and its flag clear every other house by a margin.
 * Otherwise (always for a back-row house, whose sides lie behind the front roofs) the frame stands against the house's
 * own wall over the window, inside its own footprint, the builder at its foot on the door side. Pure.
 */
export interface BuildSpot { x0: number; x1: number; base: number; h: number; side: 1 | -1; builder: Pt; face: -1 | 1 }
const buildCache = new WeakMap<VillageLayout, Map<number, BuildSpot>>()
export function buildSpot(lay: VillageLayout, i: number): BuildSpot {
  let m = buildCache.get(lay)
  if (!m) { m = new Map(); buildCache.set(lay, m) }
  const hit = m.get(i)
  if (hit) return hit
  const bs = computeBuild(lay, i)
  m.set(i, bs)
  return bs
}
/** The frame's two boxes: the posts with the planks overhanging 0.1 of its width, and the flag's pole and pennant on the
 * outer post. */
export function buildBox(bs: BuildSpot): [Pts, Pts] {
  const fw = bs.x1 - bs.x0, top = bs.base - bs.h
  const l = bs.x0 - 0.1 * fw, r = bs.x1 + 0.1 * fw
  const px = bs.side > 0 ? bs.x1 - 0.1 * fw : bs.x0 + 0.1 * fw, fx = px + bs.side * 0.55 * fw
  const fl = Math.min(px, fx), fr = Math.max(px, fx), ft = top - bs.h * 0.32
  return [[[l, top], [r, top], [r, bs.base], [l, bs.base]], [[fl, ft], [fr, ft], [fr, top], [fl, top]]]
}
function computeBuild(lay: VillageLayout, i: number): BuildSpot {
  const s = lay.houses[i], u = lay.unit, f = lay.figure
  const doorSide = s.door.x + s.door.w / 2 < s.x ? -1 : 1
  const winSide = (-doorSide) as 1 | -1
  const gap = Math.max(0.6 * u, s.wallW * 0.1)
  const lo = boardRight(lay) + 0.6 * f, hi = lay.w - lay.pad.x - 0.6 * f
  const L = s.x - s.wallW / 2, R = s.x + s.wallW / 2, base = s.y + 0.4 * u
  if (s.row === 0) {
    const fh = frameH(lay, base, s.wallH + s.roofH)
    const margin = Math.max(1.5 * u, 0.12 * s.wallW)
    const near = [i - 2, i - 1, i + 1, i + 2].map((k) => lay.houses[k]).filter((h): h is HouseSlot => h !== undefined)
    const others = near.flatMap(silhouette).map(shape)
    for (const side of [winSide, (-winSide) as 1 | -1]) {
      for (const fr of [0.55, 0.45, 0.36]) {
        const fw = Math.max(6 * u, s.wallW * fr)
        const [x0, x1] = side > 0 ? [R + gap, R + gap + fw] : [L - gap - fw, L - gap]
        if (x0 < lo || x1 > hi) continue
        const bx = side > 0 ? x0 + 0.12 * fw : x1 - 0.12 * fw
        const bs: BuildSpot = { x0, x1, base, h: fh, side, builder: [bx, s.y + 1.6 * u], face: side }
        const boxes = buildBox(bs).map(shape)
        if (boxes[1].y0 < lay.pad.top) continue
        if (others.every((o) => boxes.every((bx) => apart(bx, o, margin)))) return bs
      }
    }
  }
  // against its own wall, over the window: the frame from near the middle of the wall to just inside its far corner. A
  // back-row house's corners can dip behind a front roof's ridge, so the frame narrows (or crosses to the door side)
  // until its foot clears every other house
  const h = frameH(lay, base, s.wallH + s.roofH)
  const own = (side: 1 | -1, reach: number): BuildSpot => {
    const inner = s.x + side * s.wallW * 0.05, outer = s.x + side * s.wallW * reach
    const bx = inner + side * 0.04 * s.wallW
    return { x0: Math.min(inner, outer), x1: Math.max(inner, outer), base, h, side, builder: [bx, s.y + 1.6 * u], face: side }
  }
  const near = [i - 2, i - 1, i + 1, i + 2].map((k) => lay.houses[k]).filter((o): o is HouseSlot => o !== undefined)
  const others = near.flatMap(silhouette).map(shape)
  for (const side of [winSide, (-winSide) as 1 | -1]) {
    for (const reach of [0.44, 0.38, 0.32, 0.27]) {
      const bs = own(side, reach)
      if (others.every((o) => buildBox(bs).every((bx) => apart(shape(bx), o, 0.3 * u)))) return bs
    }
  }
  return own(winSide, 0.44)
}

/** The front row's mean house height, walls and roof: what a build's scaffold reaches at least. */
export function houseHeight(lay: VillageLayout): number {
  const fr = lay.houses.filter((s) => s.row === 0)
  return fr.reduce((a, s) => a + s.wallH + s.roofH, 0) / Math.max(1, fr.length)
}
/** The scale of what a moment raises by the board (a frame, a trophy, a sign): the board stands in the foreground near
 * the reader at the same size whatever the count, so these keep to it rather than to a small row's figures. */
export const boardScale = (lay: VillageLayout) => Math.max(lay.figure, 0.42 * lay.board.panel.h)
/** A frame's height: at least a house tall, never so tall that its flag leaves the plate's top margin. */
const frameH = (lay: VillageLayout, base: number, h: number) => Math.min(Math.max(h, houseHeight(lay)), (base - lay.pad.top - 1 * lay.unit) / 1.34)
/**
 * The scaffold of a build that names nobody when the chapter has no scaffold prop: at the board's side, in the grass
 * just right of the board, a house tall, its flag on the far post. Pure.
 */
export function boardScaffold(lay: VillageLayout): BuildSpot {
  const f = lay.figure
  const base = gatherSpot(lay, 0)[1]
  const h = frameH(lay, base, 1.5 * boardScale(lay))
  const fw = Math.min(Math.max((GATHER_W - 0.6) * f, 0.9 * boardScale(lay)), Math.max(0.8 * f, 0.5 * h))
  const x0 = boardRight(lay) + 0.3 * f
  return { x0, x1: x0 + fw, base, h, side: 1, builder: [x0, base], face: 1 }
}
/** The scaffold prop as a build that names nobody raises it: the prop's own width, a house tall. */
export function propScaffold(lay: VillageLayout, slot: GroundSlot): BuildSpot {
  const S = slot.size
  return { x0: slot.x - 0.42 * S, x1: slot.x + 0.42 * S, base: slot.y, h: frameH(lay, slot.y, S), side: 1, builder: [slot.x, slot.y], face: 1 }
}
/** The trophy of a win that names nobody: it rises from behind the board and stands over its roof. */
export function boardTrophy(lay: VillageLayout): { x: number; y: number; h: number; from: number } {
  const P = lay.board.panel, u = lay.unit
  const h = Math.max(8 * u, 0.75 * boardScale(lay))
  const y = Math.max(lay.pad.top + h * 1.7, lay.board.roof[1][1] - 0.06 * h)
  return { x: P.x + P.w / 2, y, h, from: P.y + P.h * 0.5 }
}

/** The hammer's swing: 0 = the blow, 1 = raised; four blows at 1.6, 2.5, 3.4 and 4.3 s, one per plank. */
const hammerSwing = (T: number) => (T < 1.45 || T > 4.45 ? 0.25 : 0.5 - 0.5 * Math.cos((TAU * (T - 1.6)) / 0.9))
const eighths = (a: number) => Math.round(clamp01(a) * 8) / 8

/** Where the k-th of a group of n stands at the board: in the grass right of the board, the first nearest it; a group of
 * five or six stands closer. */
export function gatherSpot(lay: VillageLayout, k: number, n = 4): Pt {
  const f = lay.figure, u = lay.unit
  const x = boardRight(lay) + 0.5 * f + k * (n > 4 ? 0.42 : 0.52) * f
  return [x, Math.min(lay.h - lay.pad.bottom - 1.2 * u, roadAt(lay, x, 1) + 3 * u + (k % 2) * 1.4 * u)]
}

/** Seconds between voters setting out: five or six keep within the play time, the last home before it ends. */
const voteStagger = (n: number) => (n > 4 ? 0.16 : 0.25)
/** Where the k-th voter stands at the ballot box: alternately left and right of it, the later ones a step further out. */
export function voteSpot(lay: VillageLayout, box: StageSpot, k: number): Pt {
  const f = lay.figure, u = lay.unit, side = k % 2 ? 1 : -1
  return [box.slot.x + side * (0.36 * box.slot.size + 0.3 * f + Math.floor(k / 2) * 0.4 * f), box.slot.y + (k % 2) * 0.8 * u]
}

const groundPathCache = new WeakMap<VillageLayout, Map<string, { path: Pts; len: number }>>()
/** From house i's door down its footpath, along the road's near lane, and out into the grass to `spot`. */
function groundPath(lay: VillageLayout, i: number, spot: Pt): { path: Pts; len: number } {
  let m = groundPathCache.get(lay)
  if (!m) { m = new Map(); groundPathCache.set(lay, m) }
  const key = `${i}|${spot[0].toFixed(3)}|${spot[1].toFixed(3)}`
  let hit = m.get(key)
  if (!hit) {
    const out = lay.houses[i].path.slice().reverse()
    const path: Pts = [...out, [spot[0], roadAt(lay, spot[0], 0.62)], spot]
    hit = { path, len: pathLength(path) }
    if (m.size > 128) m.clear()
    m.set(key, hit)
  }
  return hit
}
/** A figure along a path at eased progress q: feet, facing, stride. */
function along(lay: VillageLayout, path: Pts, len: number, q: number): { at: Pt; facing: -1 | 0 | 1; phase: number } {
  const at = pointAt(path, q)
  const ahead = pointAt(path, Math.min(1, q + 0.02)), behind = pointAt(path, Math.max(0, q - 0.02))
  const hx = ahead[0] - behind[0], hy = ahead[1] - behind[1]
  const facing = Math.abs(hx) > Math.abs(hy) * 0.8 ? (hx < 0 ? -1 : 1) : 0
  return { at, facing, phase: Math.PI / 2 + ((q * len) / (lay.figure * 0.42)) * Math.PI }
}
/** A walk from t0 lasting dur, in a moment's seconds T: where the walker is, and whether it is still walking. */
function walkOf(lay: VillageLayout, g: { path: Pts; len: number }, T: number, t0: number, dur: number) {
  const q = clamp01((T - t0) / dur)
  const moving = q > 0 && q < 1
  const w = along(lay, g.path, g.len, ease.inOut(q))
  return { ...w, moving }
}
const walkDur = (lay: VillageLayout, len: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, len / (lay.figure * 2.2)))

/**
 * The residents a moment shows at `clock`, each with its pose. Pure: the same layout, scene, moment and clock give the
 * same figures, and from MOMENT_DUR on the answer no longer changes (the end state).
 */
export function momentFigures(lay: VillageLayout, scene: Pick<VillageScene, 'houses' | 'props' | 'moments'>, m: Moment, clock: number): MomentFigure[] {
  const ms = (scene.moments ?? []).slice(0, 6)
  let mi = ms.indexOf(m)
  if (mi < 0) { const key = momentKey(m); mi = ms.findIndex((x) => momentKey(x) === key) }
  if (mi < 0) return figuresOf(lay, scene, m, clock, new Set())
  return stageFigures(lay, scene, clock)[mi]
}
const momentKey = (m: Moment) => `${m.kind}|${(m.houses ?? []).join('.')}|${m.label}|${m.start}`
const startOf = (m: Moment) => (Number.isFinite(m.start) ? m.start : 0)

/**
 * Every moment's figures at `clock` (the first six moments, in scene order), each resident drawn once: a resident two
 * moments share belongs to the latest-started moment that shows them (a later index breaks a tie); the earlier moments
 * leave them out (out of the gather group, off the table's stools). The busy set and the drawing both read this. Pure.
 */
export function stageFigures(lay: VillageLayout, scene: Pick<VillageScene, 'houses' | 'props' | 'moments'>, clock: number): MomentFigure[][] {
  const ms = (scene.moments ?? []).slice(0, 6)
  const order = ms.map((_, k) => k).sort((p, q) => startOf(ms[q]) - startOf(ms[p]) || q - p)
  const claimed = new Set<number>()
  const out: MomentFigure[][] = ms.map(() => [])
  for (const k of order) {
    const figs = figuresOf(lay, scene, ms[k], clock, claimed)
    out[k] = figs
    for (const fg of figs) if (fg.a > 0) claimed.add(fg.n)
  }
  return out
}

/** One moment's figures, leaving out the residents in `skip` (house numbers n another moment shows). */
function figuresOf(lay: VillageLayout, scene: Pick<VillageScene, 'houses' | 'props' | 'moments'>, m: Moment, clock: number, skip: Set<number>): MomentFigure[] {
  const T = momentTime(m, clock)
  if (T === null) return []
  const houses = scene.houses.slice(0, lay.count)
  const f = lay.figure
  const out: MomentFigure[] = []
  const fig = (i: number, o: Partial<MomentFigure> & { at: Pt }): MomentFigure => {
    const s = lay.houses[i]
    const depth = s.row === 1 && (o.layer ?? s.row) !== 2 ? 0.12 : 0
    return { n: houses[i].n, size: f * (1 - depth), facing: 0, pose: 'stand', phase: 0, gesture: 0, hop: 0, a: 1, layer: s.row, ...o }
  }
  const spots = stageSpots(lay, scene.props, scene.moments)
  const all = castOf(lay, houses, m, spots)
  const free = (i: number) => !skip.has(houses[i].n)
  const who = all.filter(free)
  if (!who.length) return out
  switch (m.kind) {
    case 'build': {
      // the frame rises by the first named house; its builder only when no later moment has them
      const i = all[0]
      if (!free(i)) break
      const bs = buildSpot(lay, i)
      const a = eighths(1 - smooth(5.2, 6, T))
      if (a > 0) out.push(fig(i, { at: bs.builder, facing: bs.face, pose: 'hammer', gesture: hammerSwing(T), a }))
      break
    }
    case 'celebrate':
    case 'win': {
      // they step out of the door, raise their arms and bounce, and go back in; a winner steps out once the trophy is up
      const t00 = m.kind === 'win' ? 1.8 : 0
      ;(m.kind === 'win' ? all.slice(0, 1).filter(free) : who).forEach((i, k) => {
        const s = lay.houses[i]
        const rev = s.path.slice().reverse()
        const d = Math.min(0.6 * f, pathLength(rev) * 0.5)
        const step = { path: rev, len: pathLength(rev) }
        const qOut = Math.min(1, d / step.len)
        const t0 = t00 + 0.15 * k
        if (T < t0) return
        const outQ = clamp01((T - t0) / 0.9), backQ = clamp01((T - 4.8 - 0.075 * k) / 0.8)
        const q = qOut * ease.inOut(outQ) * (1 - ease.inOut(backQ))
        const w = along(lay, step.path, step.len, q)
        const walking = (outQ > 0 && outQ < 1) || (backQ > 0 && backQ < 1)
        const a = eighths(1 - smooth(5.6, 6, T))
        if (a <= 0) return
        if (walking) out.push(fig(i, { at: w.at, facing: backQ > 0 ? (w.facing === 0 ? 0 : (-w.facing as -1 | 1)) : w.facing, pose: 'walk', phase: w.phase, a }))
        else if (T >= t0 + 0.9 && backQ === 0) {
          const bounce = Math.max(0, Math.sin((TAU * (T - t0 - 0.9)) / 0.62 + k))
          out.push(fig(i, { at: w.at, pose: 'cheer', gesture: 0.5 + 0.5 * Math.sin((TAU * T) / 0.62 + k), hop: bounce * 0.09 * f, a }))
        } else out.push(fig(i, { at: w.at, pose: 'stand', a }))
      })
      break
    }
    case 'fail': {
      const i = all[0], s = lay.houses[i]
      if (!free(i)) break
      const a = eighths(smooth(1.8, 2.2, T) * (1 - smooth(5.4, 6, T)))
      if (a > 0) out.push(fig(i, { at: [s.door.x + s.door.w / 2, s.y + 0.16 * f], pose: 'sit', gesture: 1, a }))
      break
    }
    case 'gather': {
      // each keeps its own place in the group (by the whole group's order), so nobody shuffles when one is called away;
      // those a later moment keeps for good (a game, another group) stand at the far end, so the group that stays keeps
      // together
      const ms = (scene.moments ?? []).slice(0, 6), mi = ms.indexOf(m)
      const later = new Set<number>()
      ms.forEach((x, k) => {
        if (x === m || (x.kind !== 'play' && x.kind !== 'gather')) return
        if (startOf(x) > startOf(m) || (startOf(x) === startOf(m) && k > mi)) for (const i of castOf(lay, houses, x, spots)) later.add(i)
      })
      const byX = (p: number, q: number) => lay.houses[p].x - lay.houses[q].x
      const order = [...all.filter((i) => !later.has(i)).sort(byX), ...all.filter((i) => later.has(i)).sort(byX)]
      order.forEach((i, k) => {
        if (!free(i)) return
        const spot = gatherSpot(lay, k, order.length)
        const g = groundPath(lay, i, spot)
        const t0 = 0.2 * k
        const w = walkOf(lay, g, T, t0, walkDur(lay, g.len, 2.4, 5.2))
        if (T < t0) out.push(fig(i, { at: g.path[0], pose: 'stand', layer: 2 }))
        else if (w.moving) out.push(fig(i, { at: w.at, facing: w.facing, pose: 'walk', phase: w.phase, layer: 2 }))
        else out.push(fig(i, { at: w.at, facing: -1, pose: 'talk', layer: 2 }))
      })
      break
    }
    case 'vote': {
      const box = spots.find((p) => p.kind === 'ballot')
      if (!box) break
      all.forEach((i, k) => {
        if (!free(i)) return
        const side = k % 2 ? 1 : -1
        const spot = voteSpot(lay, box, k)
        const g = groundPath(lay, i, spot)
        const t0 = voteStagger(all.length) * k, dur = walkDur(lay, g.len, 2.2, 3.4)
        const w = walkOf(lay, g, T, t0, dur)
        const arrive = t0 + dur
        const a = eighths(1 - smooth(arrive + 1.0, arrive + 1.6, T))
        if (a <= 0) return
        if (T < t0) out.push(fig(i, { at: g.path[0], pose: 'stand', layer: 2 }))
        else if (w.moving) out.push(fig(i, { at: w.at, facing: w.facing, pose: 'walk', phase: w.phase, layer: 2 }))
        else out.push(fig(i, { at: w.at, facing: (-side) as -1 | 1, pose: 'reach', gesture: smooth(arrive, arrive + 0.35, T) * (1 - smooth(arrive + 0.7, arrive + 1.0, T)), a, layer: 2 }))
      })
      break
    }
    case 'play': {
      const table = spots.find((p) => p.kind === 'chessboard')
      if (!table) break
      // two stools: each named player keeps their own; a stool whose player another moment has is taken by the next named
      const seats: (number | undefined)[] = all.slice(0, 2).map((i) => (free(i) ? i : undefined))
      const spare = all.slice(2).filter(free)
      for (let k = 0; k < 2; k++) if (seats[k] === undefined) seats[k] = spare.shift()
      seats.forEach((i, k) => {
        if (i === undefined) return
        const side = k === 0 ? -1 : 1
        const spot: Pt = [table.slot.x + side * (0.55 * table.slot.size + 0.16 * f), table.slot.y]
        const g = groundPath(lay, i, spot)
        const t0 = 0.3 * k
        const w = walkOf(lay, g, T, t0, walkDur(lay, g.len, 2.2, 3.6))
        if (T < t0) out.push(fig(i, { at: g.path[0], pose: 'stand', layer: 2 }))
        else if (w.moving) out.push(fig(i, { at: w.at, facing: w.facing, pose: 'walk', phase: w.phase, layer: 2 }))
        else out.push(fig(i, { at: w.at, facing: (-side) as -1 | 1, pose: 'sit', gesture: 0, layer: 2 }))
      })
      break
    }
    default: break
  }
  return out
}

/** The road stretches a moment's residents use, so the talking pairs stand clear of them. */
function momentBlocks(lay: VillageLayout, houses: HouseScene[], moments: Moment[] | undefined, props: Prop[] | undefined): [number, number][] {
  const f = lay.figure
  const out: [number, number][] = []
  const spots = stageSpots(lay, props, moments)
  for (const m of moments ?? []) {
    const who = castOf(lay, houses, m, spots)
    if (m.kind !== 'send') {
      for (const i of who) {
        const p = lay.houses[i].path
        out.push([Math.min(p[0][0], p[1][0]) - 0.3 * f, Math.max(p[0][0], p[1][0]) + 0.3 * f])
      }
    }
    if (m.kind === 'gather') out.push([boardRight(lay), boardRight(lay) + GATHER_W * f])
    if (m.kind === 'fail') { const sg = who.length ? failSign(lay, who[0]) : boardSign(lay, signShift(lay, houses, moments ?? [], spots)); out.push([sg.at[0] - 1.3 * sg.len, sg.at[0] + 1.3 * sg.len]) }
    if (m.kind === 'build') {
      const bs = who.length ? buildSpot(lay, who[0]) : buildFixture(lay, spots)
      out.push([bs.x0 - 0.3 * f, bs.x1 + 0.3 * f])
    }
    if (m.kind === 'vote' || m.kind === 'play') {
      const spot = spots.find((s) => s.kind === (m.kind === 'vote' ? 'ballot' : 'chessboard'))
      if (spot) out.push([spot.slot.x - 0.8 * spot.slot.size - f, spot.slot.x + 0.8 * spot.slot.size + f])
    }
  }
  return out
}
/** The frame of a build that names nobody: the scaffold prop raised a house tall, or a frame at the board's side. */
export function buildFixture(lay: VillageLayout, spots: StageSpot[]): BuildSpot {
  const sc = spots.find((sp) => sp.kind === 'scaffold')
  return sc ? propScaffold(lay, sc.slot) : boardScaffold(lay)
}

// ---------------------------------------------------------------- the board's words

/**
 * Wrap `text` into lines no wider than `width` (as `measure` reports it), at most `maxLines`; the last line ends in an
 * ellipsis when the words run over. A word wider than a line is cut by characters.
 */
export function wrapBoard(text: string, width: number, measure: (s: string) => number, maxLines = 3): { lines: string[]; fits: boolean } {
  const words = text.trim().split(/\s+/).filter(Boolean)
  const lines: string[] = []
  let cur = ''
  const push = () => { if (cur) lines.push(cur); cur = '' }
  for (const word of words) {
    const next = cur ? `${cur} ${word}` : word
    if (measure(next) <= width) { cur = next; continue }
    push()
    if (measure(word) <= width) { cur = word; continue }
    let piece = ''
    for (const ch of word) {
      if (measure(piece + ch) > width && piece) { lines.push(piece); piece = '' }
      piece += ch
    }
    cur = piece
  }
  push()
  if (lines.length <= maxLines) return { lines, fits: true }
  const kept = lines.slice(0, maxLines)
  // the last line keeps whole words and ends in an ellipsis; one long word is cut by characters
  let last = `${kept[maxLines - 1]} ${lines[maxLines]}`
  while (last.includes(' ') && measure(`${last}…`) > width) last = last.slice(0, last.lastIndexOf(' ')).trimEnd()
  while (last.length > 1 && measure(`${last}…`) > width) last = last.slice(0, -1).trimEnd()
  kept[maxLines - 1] = `${last}…`
  return { lines: kept, fits: false }
}

/** The board's words at the largest size from the sheet's own scale down to 9 px that fits in 3 lines. */
export function fitBoard(text: string, sheetW: number, sheetH: number, measureAt: (s: string, px: number) => number): { px: number; lines: string[] } {
  const maxPx = Math.max(9, Math.min(sheetH * 0.27, 22))
  for (let px = maxPx; px >= 9; px -= 0.5) {
    const r = wrapBoard(text, sheetW, (s) => measureAt(s, px))
    if (r.fits && r.lines.length * px * 1.18 <= sheetH) return { px, lines: r.lines }
  }
  return { px: 9, lines: wrapBoard(text, sheetW, (s) => measureAt(s, 9)).lines }
}

let serifFamily: string | null = null
/** The page's serif, read once from --serif (kept once the page has set it); Georgia until then, or with no page. */
function serif(): string {
  if (serifFamily !== null) return serifFamily
  let fam = ''
  try { if (typeof document !== 'undefined') fam = getComputedStyle(document.documentElement).getPropertyValue('--serif').trim() } catch { fam = '' }
  if (fam) serifFamily = fam
  return fam || 'Georgia, serif'
}

let faceReady: string | null = null
const faceAsked = new Set<string>()
/**
 * Whether the italic face of `fam` has loaded. Until it has, the canvas draws the board in a fallback face, so a fit made
 * then must not outlive the load: the answer is part of the fit's cache key. Asks the page to load the face the first
 * time it is missing (nothing else on the page may use the italic). True when there is no font loading to wait for.
 */
function italicReady(fam: string): boolean {
  if (faceReady === fam) return true
  try {
    const fonts = typeof document !== 'undefined' ? (document as Document).fonts : undefined
    if (!fonts || typeof fonts.check !== 'function') return true
    const probe = `italic 16px ${fam}`
    if (fonts.check(probe)) { faceReady = fam; return true }
    if (!faceAsked.has(probe) && typeof fonts.load === 'function') { faceAsked.add(probe); fonts.load(probe).catch(() => undefined) }
    return false
  } catch { return true }
}

// fits are kept per context (a plate keeps one context for its life), keyed by the words, the sheet, the face and
// whether the face has loaded
const fitCaches = new WeakMap<CanvasRenderingContext2D, Map<string, { px: number; lines: string[] }>>()
function boardWords(ctx: CanvasRenderingContext2D, text: string, sw: number, sh: number): { px: number; lines: string[]; font: string } {
  const fam = serif()
  const ready = italicReady(fam)
  const key = `${text}|${sw.toFixed(2)}|${sh.toFixed(2)}|${fam}|${ready ? 1 : 0}`
  let fitCache = fitCaches.get(ctx)
  if (!fitCache) { fitCache = new Map(); fitCaches.set(ctx, fitCache) }
  let hit = fitCache.get(key)
  if (!hit) {
    // every candidate size is measured at that size: the book's serif has an optical-size axis, so its small sizes run
    // wider than a big measure scaled down (about a fifth wider at 9 px). Cached per (size, string) for this one fit.
    const widths = new Map<string, number>()
    let set = Number.NaN
    const at = (s: string, px: number) => {
      const k = `${px}|${s}`
      let v = widths.get(k)
      if (v === undefined) {
        if (px !== set) { ctx.font = `italic ${px}px ${fam}`; set = px }
        v = ctx.measureText(s).width
        widths.set(k, v)
      }
      return v
    }
    hit = fitBoard(text, sw, sh, at)
    if (fitCache.size > 32) fitCache.clear()
    fitCache.set(key, hit)
  }
  return { ...hit, font: `italic ${hit.px}px ${fam}` }
}

// ---------------------------------------------------------------- batching
// A village is hundreds of short strokes. Each style collects its strokes into one Path2D and strokes once, so a
// 40-house scene costs a few dozen canvas calls instead of a thousand.

interface InkStyle { a: number; width: number; dash: number[] | null; color: string }
class Batch {
  private strokes = new Map<string, { style: InkStyle; path: Path2D }>()
  private fills = new Map<string, { color: string; a: number; path: Path2D }>()
  line(key: string, style: InkStyle): Path2D {
    let g = this.strokes.get(key)
    if (!g) { g = { style, path: new Path2D() }; this.strokes.set(key, g) }
    return g.path
  }
  fill(color: string, a: number): Path2D {
    const key = `${color}|${a}`
    let g = this.fills.get(key)
    if (!g) { g = { color, a, path: new Path2D() }; this.fills.set(key, g) }
    return g.path
  }
  flush(ctx: CanvasRenderingContext2D): void {
    for (const g of this.fills.values()) {
      ctx.globalAlpha = g.a
      ctx.fillStyle = g.color
      ctx.fill(g.path)
    }
    const keys = [...this.strokes.keys()].sort((p, q) => rank(p) - rank(q))
    for (const k of keys) {
      const g = this.strokes.get(k)!
      ctx.globalAlpha = g.style.a
      ctx.strokeStyle = g.style.color
      ctx.lineWidth = g.style.width
      ctx.setLineDash(g.style.dash ?? [])
      ctx.stroke(g.path)
    }
    ctx.setLineDash([])
    this.strokes.clear()
    this.fills.clear()
  }
}
const LAYERS = ['hatch', 'hatch2', 'fine', 'out2', 'out', 'smoke', 'mark', 'fig']
const rank = (key: string) => { const i = LAYERS.indexOf(key.split(':')[0]); return i < 0 ? LAYERS.length : i }

/** A straight-edged polygon (washes sit under the pen line, so they need no wobble of their own). */
function poly(sink: Path2D, pts: Pts): void {
  sink.moveTo(pts[0][0], pts[0][1])
  for (let i = 1; i < pts.length; i++) sink.lineTo(pts[i][0], pts[i][1])
  sink.closePath()
}
function shortestSide(pts: Pts): number {
  let m = Infinity
  for (let i = 0; i < pts.length - 1; i++) {
    const d = Math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
    if (d > 1e-6 && d < m) m = d
  }
  return m
}
/** Split a polyline into pen runs with lifts between them: a hand-ruled edge, not a ruler's. */
function runs(pts: Pts, seed: number, run: number, gap: number): Pts[] {
  const rnd = rngOf(`runs-${seed}`)
  const out: Pts[] = []
  let cur: Pts = [pts[0]]
  let left = run * (0.6 + 0.8 * rnd()), lifting = false
  for (let i = 0; i < pts.length - 1; i++) {
    let a = pts[i]
    const b = pts[i + 1]
    let d = Math.hypot(b[0] - a[0], b[1] - a[1])
    while (d > left) {
      const f = left / d
      const m: Pt = [lerp(a[0], b[0], f), lerp(a[1], b[1], f)]
      if (lifting) { cur = [m]; left = run * (0.6 + 0.8 * rnd()) } else { cur.push(m); if (cur.length > 1) out.push(cur); left = gap * (0.6 + 0.8 * rnd()) }
      lifting = !lifting
      d -= Math.hypot(m[0] - a[0], m[1] - a[1])
      a = m
    }
    left -= d
    if (!lifting) cur.push(b)
  }
  if (!lifting && cur.length > 1) out.push(cur)
  return out
}

// ---------------------------------------------------------------- drawing

type Variant = 'on' | 'ghost' | 'empty' | 'emptyGhost'
const WOOD = '#8a6a45'           // the notice board's wash
const TAU = Math.PI * 2

/**
 * Draw the village for one moment of one day. Clears nothing, paints no background. `clock` is seconds since the day
 * came under the reader's eye: smoke, walkers, the visitor's sway, the board's sheet and the talk all move by it.
 * Without a clock the walkers stand where `scene.t` puts them and everything else is drawn at its first moment.
 */
export function drawVillage(ctx: CanvasRenderingContext2D, scene: VillageScene, w: number, h: number, clock?: number): void {
  const houses = scene.houses.slice(0, MAX_HOUSES)
  if (!houses.length || !(w > 0) || !(h > 0)) return
  const lay = layoutHouses(houses.length, w, h)
  const u = lay.unit
  const big = clamp01((lay.houseW / u - 14) / 50)           // 0 = the smallest houses, 1 = the largest
  const wOut = u * (0.62 + 0.55 * big)
  const amp = u * (0.22 + 0.8 * big)
  const step = u * (3 + 6 * big)
  const t = clamp01(scene.t)
  const timed = clock !== undefined && Number.isFinite(clock)
  const c = timed ? Math.max(0, clock as number) : 0

  ctx.save()
  ctx.lineCap = 'round'
  ctx.lineJoin = 'round'
  const b = new Batch()

  const style = (layer: string, variant: Variant): InkStyle => {
    const fade = variant === 'ghost' || variant === 'emptyGhost' ? GHOST_A : INK_A
    const empty = variant === 'empty' || variant === 'emptyGhost'
    const dash = empty ? [Math.max(1.2 * u, wOut * 1.6), Math.max(1.6 * u, wOut * 2.2)] : null
    switch (layer) {
      case 'out': return { a: fade * (empty ? 0.75 : 1), width: wOut, dash, color: INK }
      case 'out2': return { a: fade * 0.5, width: wOut * 0.78, dash, color: INK }
      case 'fine': return { a: fade * (empty ? 0.75 : 0.95), width: wOut * 0.8, dash, color: INK }
      case 'hatch': return { a: fade * 0.52, width: wOut * 0.62, dash: null, color: INK }
      case 'hatch2': return { a: fade * 0.3, width: wOut * 0.58, dash: null, color: INK }
      case 'smoke': return { a: fade * 0.6, width: wOut * 0.7, dash: null, color: INK }
      default: return { a: fade, width: wOut, dash: null, color: INK }
    }
  }
  const line = (layer: string, variant: Variant) => b.line(`${layer}:${variant}`, style(layer, variant))
  /** Ink a shape with the pen; the resample step never exceeds a third of its shortest side, so small corners stay square. */
  const ink = (path: Path2D, pts: Pts, seed: number, close = false, a = amp, st = step) =>
    appendPath(path, wobble(pts, { amp: Math.min(a, shortestSide(pts) * 0.12 + a * 0.3), seed, step: Math.min(st, shortestSide(pts) / 3) }), close)

  const drawRow = (row: 0 | 1) => {
    for (let i = 0; i < houses.length; i++) {
      const s = lay.houses[i]
      if (s.row === row) drawHouse(houses[i], s, i)
    }
    b.flush(ctx)
  }

  const drawHouse = (hs: HouseScene, s: HouseSlot, i: number) => {
    const seed = seedOf(`house-${s.n}`)
    const variant: Variant = hs.present ? (hs.ghost ? 'ghost' : 'on') : (hs.ghost ? 'emptyGhost' : 'empty')
    const fade = hs.ghost ? GHOST_A / INK_A : 1
    const idle = hs.idle === true
    const L = s.x - s.wallW / 2, R = s.x + s.wallW / 2, eaveY = s.y - s.wallH
    // washes: the walls, roof and chimney in paper, so the house reads on the valley and hides the row behind it
    const paper = b.fill(PAPER, (hs.present ? 0.88 : 0.5) * (hs.ghost ? 0.45 : 1))
    poly(paper, [[L, s.y], [L, eaveY], s.eaveL, s.apex, s.eaveR, [R, eaveY], [R, s.y]])
    poly(paper, s.chimney)
    const roof: Pts = [s.eaveL, s.apex, s.eaveR, s.eaveL]
    const walls: Pts = [[L, eaveY], [L, s.y], [R, s.y], [R, eaveY]]
    const d = s.door, wn = s.win
    const doorPts: Pts = [[d.x, d.y + d.h], [d.x, d.y], [d.x + d.w, d.y], [d.x + d.w, d.y + d.h]]
    const winPts: Pts = [[wn.x, wn.y], [wn.x + wn.w, wn.y], [wn.x + wn.w, wn.y + wn.h], [wn.x, wn.y + wn.h], [wn.x, wn.y]]

    if (!hs.present) {
      // an empty house: the outline only, dotted, and the shape of its door; no window, no smoke
      const out = line('out', variant), fine = line('fine', variant)
      ink(out, roof, seed + 1, true, amp * 0.6)
      ink(out, walls, seed + 2, false, amp * 0.6)
      ink(out, s.chimney, seed + 3, false, amp * 0.4)
      ink(fine, doorPts, seed + 4, false, amp * 0.4)
      return
    }
    // the roof, hatched
    const spacing = Math.max(2.1 * u, s.wallW * 0.085)
    const ang = 118 + ((seed >>> 8) % 17)
    const segs = hatchSegments(roof, { angle: ang, spacing, seed: seed + 11, jitter: spacing * 0.22, inset: wOut * 0.9 })
    const h1 = line('hatch', variant), h2 = line('hatch2', variant)
    for (const g of segs) { const p = g.strong ? h1 : h2; p.moveTo(g.a[0], g.a[1]); p.quadraticCurveTo(g.c[0], g.c[1], g.b[0], g.b[1]) }
    // the door in its lab's colour, the window lit in the lens's colour or dark; an idle house keeps no lamp
    poly(b.fill(hs.lab ? DOOR[hs.lab] : DOOR.other, 0.86 * fade), doorPts)
    if (hs.window && !idle) poly(b.fill(hs.window, 0.74 * fade), winPts)
    else poly(b.fill(INK, (idle ? 0.3 : 0.24) * fade), winPts)
    // outlines: roof and walls twice over, the chimney, the door and window once
    const out = line('out', variant), out2 = line('out2', variant), fine = line('fine', variant)
    ink(out, roof, seed + 1, true)
    ink(out, walls, seed + 2)
    ink(out2, roof, seed + 977, true)
    ink(out2, walls, seed + 978)
    ink(fine, s.chimney, seed + 3, false, amp * 0.6)
    ink(fine, doorPts, seed + 4, false, amp * 0.5)
    ink(fine, winPts, seed + 5, true, amp * 0.5)
    if (idle) {
      // the shutter is closed: two short strokes across the window, a little past its frame
      const o = Math.max(0.5 * u, wn.w * 0.1)
      const sh = line('out', variant)
      ink(sh, [[wn.x - o, wn.y - o * 0.4], [wn.x + wn.w + o, wn.y + wn.h + o * 0.4]], seed + 8, false, amp * 0.3)
      ink(sh, [[wn.x - o, wn.y + wn.h + o * 0.4], [wn.x + wn.w + o, wn.y - o * 0.4]], seed + 9, false, amp * 0.3)
    } else if (wn.w > 9 * u) {
      const cx = wn.x + wn.w / 2, cy = wn.y + wn.h / 2
      ink(fine, [[cx, wn.y], [cx, wn.y + wn.h]], seed + 6, false, amp * 0.3)
      ink(fine, [[wn.x, cy], [wn.x + wn.w, cy]], seed + 7, false, amp * 0.3)
    }
    if (d.w > 8 * u) {
      const r = Math.max(0.6 * u, d.w * 0.06)
      b.fill(INK, 0.7 * fade).moveTo(d.x + d.w * 0.74 + r, d.y + d.h * 0.55)
      b.fill(INK, 0.7 * fade).arc(d.x + d.w * 0.74, d.y + d.h * 0.55, r, 0, Math.PI * 2)
    }
    if (!idle) drawSmoke(hs, i, seed, variant)
  }

  const drawSmoke = (hs: HouseScene, i: number, seed: number, variant: Variant) => {
    // a billow that lives: puffs leave the chimney one by one, climb downwind, grow and fade; each lower puff's
    // outline is lighter where the puff above covers it, as much as that puff has faded in
    const ps = puffs(lay, i, hs.smoke, c)
    if (!ps.length) return
    const lv = (a: number) => Math.round(clamp01(a) * 8) / 8
    const gfade = hs.ghost ? 0.45 : 1
    const lump = (p: Puff, a0: number, a1: number, n: number): Pts => {
      const pts: Pts = []
      for (let j = 0; j <= n; j++) {
        const a = lerp(a0, a1, j / n)
        const rr = p.r * (1 + 0.1 * Math.sin(3 * a + p.ph))
        pts.push([p.x + Math.cos(a) * rr, p.y + Math.sin(a) * rr])
      }
      return pts
    }
    const pen = (a: number) => {
      const st = style('smoke', variant)
      return b.line(`smoke:${variant}:${a}`, { ...st, a: st.a * a })
    }
    const wob = (pts: Pts, p: Puff, k: number) => wobble(pts, { amp: Math.min(amp * 0.3, p.r * 0.1), seed: seed + 31 + (((p.id % 97) + 97) % 97) + k, step: Math.max(0.8 * u, p.r * 0.35) })
    ps.forEach((p, k) => {
      const a = lv(p.a)
      if (a <= 0) return
      poly(b.fill(PAPER, 0.62 * gfade * a), lump(p, 0, TAU, 18))
      const up = ps[k + 1]
      const dx = up ? up.x - p.x : 0, dy = up ? up.y - p.y : 0, d = Math.hypot(dx, dy)
      if (up && d < p.r + up.r && d > 1e-6) {
        // the arc of this puff the one above hides, where the two circles cross
        const cosA = Math.max(-1, Math.min(1, (p.r * p.r + d * d - up.r * up.r) / (2 * p.r * d)))
        const hide = Math.acos(cosA), dir = Math.atan2(dy, dx)
        appendPath(pen(a), wob(lump(p, dir + hide, dir + TAU - hide, 16), p, 0))
        const under = lv(a * (1 - up.a))
        if (under > 0) appendPath(pen(under), wob(lump(p, dir - hide, dir + hide, 8), p, 1))
      } else appendPath(pen(a), wob(lump(p, p.ph, p.ph + Math.PI * 1.9, 18), p, 0))
    })
  }

  // back to front: the hill, the back row, the road and ground, the front row, then the gate, walkers, the talking
  // pairs, the visitor and marks, the board in the foreground, and last the speech bubbles over everything
  const plain = (key: string, a: number, width: number, dash: number[] | null = null) => b.line(key, { a, width, dash, color: INK })
  const pen: Pen = { lay, u, wOut, amp, step, b, c, ink, plain }
  const staged = (scene.moments?.length ?? 0) + (scene.props?.length ?? 0) > 0
  const stage = (layer: 'row1' | 'row0' | 'ground' | 'air') => { if (staged) drawStage(ctx, pen, scene, houses, layer) }
  if (lay.hill) { appendPath(plain('out:hill', INK_A * 0.42, wOut * 0.75), wobble(lay.hill, { amp: u * 0.9, seed: 101, step: 7 * u })); b.flush(ctx) }
  if (lay.rows === 2) { drawRow(1); stage('row1') }
  {
    // the road: a pale wash that fades out at both ends, its back edge broken, its front edge firmer
    const band = new Path2D()
    poly(band, [...lay.road.back, ...lay.road.front.slice().reverse()])
    const x0 = lay.road.back[0][0], x1 = lay.road.back[lay.road.back.length - 1][0]
    const grad = ctx.createLinearGradient(x0, 0, x1, 0)
    grad.addColorStop(0, alpha(PAPER, 0))
    grad.addColorStop(0.1, alpha(PAPER, 0.26))
    grad.addColorStop(0.85, alpha(PAPER, 0.26))
    grad.addColorStop(1, alpha(PAPER, 0))
    ctx.globalAlpha = 1
    ctx.fillStyle = grad
    ctx.fill(band)
    appendPath(plain('out:ground', INK_A * 0.72, wOut * 0.9), wobble(lay.ground, { amp: u * 0.9, seed: 102, step: 7 * u }))
    const back = plain('out:roadback', INK_A * 0.4, wOut * 0.7), front = plain('out:roadfront', INK_A * 0.75, wOut * 0.92)
    for (const [k, r] of runs(lay.road.back, 103, 70 * u, 7 * u).entries()) appendPath(back, wobble(r, { amp: u * 0.7, seed: 1030 + k, step: 7 * u }))
    for (const [k, r] of runs(lay.road.front, 104, 150 * u, 5 * u).entries()) appendPath(front, wobble(r, { amp: u * 0.9, seed: 1040 + k, step: 7 * u }))
    b.flush(ctx)
  }
  drawRow(0)
  stage('row0')

  // the gate at the left end of the road: two posts with knobs, an arch, a leaf swung open toward the village
  {
    const g = lay.gate
    const out = plain('out:gate', INK_A, wOut * 1.05), fine = plain('fine:gate', INK_A * 0.82, wOut * 0.78)
    const lx = g.x - g.w / 2, rx = g.x + g.w / 2, top = g.y - g.h
    const gink = (p: Path2D, pts: Pts, seed: number) => ink(p, pts, seed, false, amp * 0.45, step * 0.6)
    gink(out, [[lx, g.y], [lx, top]], 201)
    gink(out, [[rx, g.y + 0.6 * u], [rx, top]], 202)
    const arch: Pts = []
    for (let k = 0; k <= 8; k++) { const f = k / 8; arch.push([lerp(lx, rx, f), top + g.h * 0.1 - Math.sin(Math.PI * f) * g.w * 0.34]) }
    gink(out, arch, 203)
    const kr = Math.max(0.9 * u, g.w * 0.07)
    const knobs = b.fill(INK, INK_A)
    knobs.moveTo(lx + kr, top - kr * 0.6); knobs.arc(lx, top - kr * 0.6, kr, 0, Math.PI * 2)
    knobs.moveTo(rx + kr, top - kr * 0.6); knobs.arc(rx, top - kr * 0.6, kr, 0, Math.PI * 2)
    const lt = top + g.h * 0.4, lb = g.y - g.h * 0.06, lw = g.w * 0.58, sk = g.h * 0.06
    gink(fine, [[rx, lt], [rx + lw, lt + sk], [rx + lw, lb + sk * 0.4], [rx, lb]], 205)
    gink(fine, [[rx, lb], [rx + lw, lt + sk]], 206)
    gink(fine, [[rx + lw * 0.5, lt + sk * 0.5], [rx + lw * 0.5, lb + sk * 0.2]], 207)
  }

  // a resident a moment has brought out is drawn there, never twice: their walk and their talk wait while they are busy
  const busy = new Set<number>()
  if (staged) for (const figs of stageFigures(lay, scene, c)) for (const fg of figs) if (fg.a > 0) busy.add(fg.n)

  // the walkers: a figure on its way in or out along the footpath, or off down the road toward the far end
  const figPen = (ghost: boolean) => plain(`fig:${ghost ? 'g' : 'o'}`, ghost ? GHOST_A : INK_A, Math.max(wOut, u * 0.9))
  const awayIdx = houses.map((hs, i) => (hs.away === true && !hs.arriving && !hs.leaving && hs.present ? i : -1)).filter((i) => i >= 0)
  for (let i = 0; i < houses.length; i++) {
    const hs = houses[i], s = lay.houses[i]
    const away = awayIdx.includes(i)
    if (!hs.arriving && !hs.leaving && !away) continue
    if (busy.has(hs.n)) continue
    const kind: WalkKind = hs.arriving && hs.leaving ? 'visit' : hs.arriving ? 'arriving' : hs.leaving ? 'leaving' : 'away'
    const rank = away ? awayIdx.length - 1 - awayIdx.indexOf(i) : 0
    const a = hs.ghost ? GHOST_A : INK_A
    const seed = seedOf(`house-${s.n}-walker`)
    const path = walkerPath(lay, i, kind, rank)
    appendPath(plain(`fine:trail${hs.ghost ? 'g' : ''}`, a * 0.5, wOut * 0.6, [u * 1.4, u * 2.2]), path)
    if (timed) {
      const pose = walkerPos(lay, i, kind, c, rank)
      const size = lay.figure * (1 - 0.12 * pose.depth)
      const breath = pose.pose === 'walk' ? 0 : 0.5 * u * Math.sin((TAU * c) / 3 + s.n)
      figure(figPen(hs.ghost), b.fill(INK, a), pose.at[0], pose.at[1], size,
        { facing: pose.facing, phase: pose.phase, pose: pose.pose, breath }, seed, amp)
      continue
    }
    // no clock: the walker stands where t puts it, mid-stride, as the plate drew it before it had a clock
    const p = kind === 'leaving' ? 1 - t : t
    const along = kind === 'leaving' ? s.path : path
    const at = pointAt(along, p)
    const ahead = pointAt(along, Math.min(1, p + 0.02)), behind = pointAt(along, Math.max(0, p - 0.02))
    let hx = ahead[0] - behind[0]
    const hy = ahead[1] - behind[1]
    if (kind === 'leaving') hx = -hx
    const facing = Math.abs(hx) > Math.abs(hy) * 0.8 ? Math.sign(hx) : 0
    const end = s.path[s.path.length - 1]
    const depth = s.row === 1 && kind !== 'away' ? clamp01((s.path[0][1] - at[1]) / Math.max(1, s.path[0][1] - end[1])) : 0
    const size = lay.figure * (1 - 0.12 * depth)
    // an even number of half-strides along the path, so the figure is mid-stride at both ends and at the middle of the
    // day (t 0.5, the book's resting moment); the figure itself never lets its legs close (see `figure`)
    const steps = 2 * Math.max(1, Math.round(pathLength(along) / (lay.figure * 0.84)))
    const phase = Math.PI / 2 + p * steps * Math.PI
    figure(figPen(hs.ghost), b.fill(INK, a), at[0], at[1], size, { facing, phase, pose: 'walk' }, seed, amp)
  }

  // the talking pairs: two residents on the road turned toward each other, or one at the gate talking with the visitor.
  // At the gate the residents who talk with the humans today take turns, one exchange each (gateTurn).
  const spots = pairSpots(lay, { houses, pairs: scene.pairs ?? [], lens: scene.lens, moments: scene.moments, props: scene.props }).filter((p) => !busy.has(p.a) && !busy.has(p.b))
  const ghostOf = (n: number) => houses.find((x) => x.n === n)?.ghost === true
  const turn = gateTurn(c, spots.filter((p) => p.gate).length)
  // how present a pair is now, in eighths: a gate resident only in its own turn, fading at the hand-over; a road pair always
  const presence = (p: PairSpot) => (p.gate ? (p.turn === turn.k ? Math.round(clamp01(turn.a) * 8) / 8 : 0) : 1)
  const gatePair = spots.find((p) => p.gate && p.turn === turn.k)
  // who speaks (0 = the left figure) swaps with each breath of the bubble: the talk goes back and forth. At the gate the
  // visitor speaks first and the resident answers, and the next resident steps up.
  const speakerOf = (p: PairSpot, k: number) => Math.floor((p.gate ? turn.local : c + k * 1.3) / 2.6) % 2
  const figInk = (ghost: boolean, on: number) => {
    const a = (ghost ? GHOST_A : INK_A) * on
    return { pen: on < 1 ? plain(`fig:${ghost ? 'g' : 'o'}${on}`, a, Math.max(wOut, u * 0.9)) : figPen(ghost), fill: b.fill(INK, a) }
  }
  spots.forEach((p, k) => {
    const on = presence(p)
    if (on <= 0) return
    const speaker = speakerOf(p, k)
    const gest = (who: number) => (who === speaker ? 0.55 + 0.45 * Math.sin((TAU * c) / 1.3 + k) : 0)
    const br = (n: number) => 0.5 * u * Math.sin((TAU * c) / 3 + n)
    if (!p.gate) {
      const fa = figInk(ghostOf(p.a), on)
      figure(fa.pen, fa.fill, p.left[0], p.left[1], p.size, { facing: 1, phase: 0, pose: 'talk', breath: br(p.a), gesture: gest(0) }, seedOf(`house-${p.a}-talk`), amp)
    }
    const fb = figInk(ghostOf(p.b), on)
    figure(fb.pen, fb.fill, p.right[0], p.right[1], p.size, { facing: -1, phase: 0, pose: 'talk', breath: br(p.b), gesture: gest(1) }, seedOf(`house-${p.b}-talk`), amp)
  })

  // the visitor at the gate, swaying a little; when a resident talks across the gate it turns to them
  const anyGate = spots.some((p) => p.gate)
  const visitorA = scene.visitor > 0 ? 0.35 + 0.55 * clamp01(scene.visitor) : anyGate ? 0.35 : 0
  if (visitorA > 0) {
    const humans = scene.lens === 'humans'
    const v = visitorSpot(lay, scene.lens)
    const tilt = (3 * Math.PI / 180) * Math.sin((TAU * c) / 3)
    const k = gatePair ? spots.indexOf(gatePair) : 0
    const gesture = gatePair && speakerOf(gatePair, k) === 0 ? 0.55 + 0.45 * Math.sin((TAU * c) / 1.3 + k) : 0
    figure(plain('fig:visitor', visitorA, Math.max(wOut, u * 0.9) * (humans ? 1.25 : 1)), b.fill(INK, visitorA),
      v.at[0], v.at[1], v.size, { facing: 1, phase: 0, pose: anyGate ? 'talk' : 'stand', tilt, gesture }, seedOf('visitor'), amp)
  }

  // marks: a pin over the house where a finding starts, a pen circle round the window on a mood swing
  for (let i = 0; i < houses.length; i++) {
    const hs = houses[i], s = lay.houses[i]
    if (!hs.mark) continue
    const seed = seedOf(`house-${s.n}-mark`)
    const fade = hs.ghost ? GHOST_A / INK_A : 1
    if (hs.mark === 'pin') {
      const pin = pinSpot(lay, i)
      const dia = pinPts(pin)
      poly(b.fill(VERMILION, 0.92 * fade), dia)
      ink(plain(`mark:p${hs.ghost ? 'g' : 'o'}`, INK_A * 0.75 * fade, wOut * 0.7), [...dia, dia[0]], seed + 1, true, amp * 0.2, step * 0.5)
      // the stem carries the pin to its own ridge, so it is inked a little firmer than the diamond's outline
      ink(plain(`mark:t${hs.ghost ? 'g' : 'o'}`, INK_A * 0.9 * fade, wOut * 0.8), [dia[2], pin.tip], seed, false, amp * 0.2)
    } else {
      const cx = s.win.x + s.win.w / 2, cy = s.win.y + s.win.h / 2
      const r = s.win.w * 0.95 + 1.2 * u
      // a pen circling something: a loop that overshoots its start
      const loop: Pts = []
      for (let j = 0; j <= 26; j++) { const a = -2.2 + (j / 22) * Math.PI * 2; const rr = r * (1 + 0.08 * Math.max(0, j - 20) / 6); loop.push([cx + Math.cos(a) * rr, cy + Math.sin(a) * rr * 0.92]) }
      ink(plain(`mark:s${hs.ghost ? 'g' : 'o'}`, INK_A * fade, wOut * 0.95), loop, seed, false, amp * 0.5, step * 0.6)
    }
  }
  b.flush(ctx)

  // the stage in the grass in front of the road: the chapter's props, and the residents a moment brings out there
  stage('ground')

  // the notice board in the foreground grass, in front of the road
  drawBoard(ctx, b, lay, scene.board ?? null, c, ink, plain, wOut, amp, step)

  // the speech bubbles, over everything: a scribbled loop that breathes, a tail toward whoever speaks, a line or three
  // of scribble inside (more when they talk more)
  spots.forEach((p, k) => {
    const on = presence(p)
    if (on <= 0) return
    // at a hand-over the gate's bubble shrinks and fades with the resident leaving, and grows back with the next
    const sc = (1 + 0.06 * Math.sin((TAU * c) / 2.6 + k * 1.7)) * (0.6 + 0.4 * on)
    const { x, y } = p.bubble, rx = p.bubble.rx * sc, ry = p.bubble.ry * sc
    const seed = seedOf(`pair-${p.a}-${p.b}`)
    const faint = (p.gate ? false : ghostOf(p.a)) && ghostOf(p.b)
    const a = (faint ? GHOST_A : INK_A) * on
    const lvl = on < 1 ? String(on) : ''
    const speaker = speakerOf(p, k)
    const toward = speaker === 0 ? p.left : p.right
    const loop: Pts = []
    for (let j = 0; j <= 30; j++) {
      const th = 1.9 + (j / 26) * TAU
      const rr = 1 + 0.06 * Math.sin(th * 3 + seed) + 0.05 * Math.max(0, j - 26) / 4
      loop.push([x + Math.cos(th) * rx * rr, y + Math.sin(th) * ry * rr])
    }
    const wash = b.fill(PAPER, (faint ? 0.4 : 0.92) * on)
    poly(wash, loop.slice(0, 27))
    const pen = plain(`mark:b${faint ? 'g' : 'o'}${lvl}`, a, wOut * 0.8)
    ink(pen, loop, seed, false, amp * 0.25, Math.max(1.2 * u, rx * 0.4))
    // the tail: a small notch from the bubble's underside toward the speaker's head
    const dir = Math.sign(toward[0] - x) || 1
    const bx = x + dir * rx * 0.28, by = y + ry * 0.92
    const tip: Pt = [x + dir * rx * 0.62, y + ry * 1.55]
    ink(pen, [[bx - dir * rx * 0.16, by], tip, [bx + dir * rx * 0.12, by - ry * 0.05]], seed + 1, false, amp * 0.15, Math.max(1, rx * 0.3))
    poly(wash, [[bx - dir * rx * 0.16, by - ry * 0.12], tip, [bx + dir * rx * 0.12, by - ry * 0.16]])
    // the talk itself: short wavy scribbles, one to three by how much they talk
    const lines = rx < 4.5 * u ? 1 : 1 + Math.round(clamp01(p.w) * 2)
    const sp = plain(`mark:w${faint ? 'g' : 'o'}${lvl}`, a * 0.8, wOut * 0.7)
    for (let l = 0; l < lines; l++) {
      const ly = y + (l - (lines - 1) / 2) * ry * 0.5
      const half = rx * (0.6 - 0.12 * Math.abs(l - (lines - 1) / 2)) * (l === lines - 1 && lines > 1 ? 0.7 : 1)
      const pts: Pts = []
      const x0 = x - rx * 0.6, n = 7
      for (let j = 0; j <= n; j++) pts.push([x0 + (j / n) * half * 2 * 0.95, ly + Math.sin(j * 2.1 + l + seed) * ry * 0.09])
      appendPath(sp, pts)
    }
  })
  b.flush(ctx)
  // letters in flight and falling confetti, over everything
  stage('air')
  ctx.restore()
}

/**
 * The notice board just inside the gate: a wooden panel on two posts under a little roof, a sheet pinned to it with the
 * week's goal hand-lettered in the page's serif. The sheet's lower corner lifts and settles in the breeze. Too small a
 * board (under 64 px) carries three pen scribbles instead of words; no goal this week leaves the board bare, the tack
 * still in it.
 */
function drawBoard(ctx: CanvasRenderingContext2D, b: Batch, lay: VillageLayout, text: string | null, c: number,
  ink: (path: Path2D, pts: Pts, seed: number, close?: boolean, a?: number, st?: number) => void,
  plain: (key: string, a: number, width: number, dash?: number[] | null) => Path2D, wOut: number, amp: number, step: number): void {
  const u = lay.unit
  const { panel: P, sheet: S, roof, posts, tilt } = lay.board
  // posts first, then the panel's wash and its planks, the roof, the frame
  const post = plain('out:board', INK_A * 0.85, wOut * 1.05)
  ink(post, posts[0], 301, false, amp * 0.3, step * 0.6)
  ink(post, posts[1], 302, false, amp * 0.3, step * 0.6)
  const panelPts: Pts = [[P.x, P.y], [P.x + P.w, P.y], [P.x + P.w, P.y + P.h], [P.x, P.y + P.h], [P.x, P.y]]
  // a posted sheet hides most of the board, so it can stand dark; a bare board stays light, or it reads as a hole
  const posted = text !== null && text.trim() !== ''
  poly(b.fill(WOOD, posted ? 0.8 : 0.34), panelPts)
  poly(b.fill(PAPER, posted ? 0.25 : 0.4), panelPts)
  poly(b.fill(PAPER, 0.88), [roof[0], roof[1], roof[2]])
  const planks = plain('hatch2:board', INK_A * 0.36, wOut * 0.6)
  for (let k = 1; k <= 3; k++) {
    const y = P.y + (P.h * k) / 4
    ink(planks, [[P.x + 0.8 * u, y], [P.x + P.w - 0.8 * u, y + 0.3 * u]], 310 + k, false, amp * 0.3, step)
  }
  ink(post, panelPts, 303, true, amp * 0.4, step)
  ink(post, roof, 304, false, amp * 0.4, step * 0.8)
  b.flush(ctx)   // the board under the sheet: its planks never run across the paper
  const fine = plain('fine:board', INK_A * 0.9, wOut * 0.8)
  const tackR = Math.max(0.8 * u, P.w * 0.018)
  const tack: Pt = [S.x + S.w / 2, S.y + Math.max(1.4 * u, S.h * 0.06)]
  if (!posted || text === null) {
    // a bare board: the tack and a torn scrap under it
    const sc: Pts = [[tack[0] - S.w * 0.08, tack[1] - tackR * 1.6], [tack[0] + S.w * 0.07, tack[1] - tackR * 1.2], [tack[0] + S.w * 0.04, tack[1] + S.h * 0.12], [tack[0] - S.w * 0.05, tack[1] + S.h * 0.09]]
    poly(b.fill(PAPER, 0.85), sc)
    ink(fine, [...sc, sc[0]], 320, true, amp * 0.2, step * 0.4)
    b.fill(INK, INK_A).moveTo(tack[0] + tackR, tack[1]); b.fill(INK, INK_A).arc(tack[0], tack[1], tackR, 0, TAU)
    b.flush(ctx)
    return
  }
  // the sheet, tilted a touch, its lower right corner lifting: f is how far the corner has curled up
  const f = Math.max(0.8 * u, S.h * 0.06) + (0.6 + 0.4 * Math.sin(c * 1.9)) * 2.4 * u * Math.pow(0.5 - 0.5 * Math.cos((TAU * c) / 4), 2)
  const rot = (p: Pt): Pt => {
    const cx = S.x + S.w / 2, cy = S.y
    const dx = p[0] - cx, dy = p[1] - cy, cs = Math.cos(tilt), sn = Math.sin(tilt)
    return [cx + dx * cs - dy * sn, cy + dx * sn + dy * cs]
  }
  const x1 = S.x + S.w, y1 = S.y + S.h
  const sheet: Pts = ([[S.x, S.y], [x1, S.y], [x1, y1 - f], [x1 - f, y1], [S.x, y1]] as Pts).map(rot)
  const flap: Pts = ([[x1 - f, y1], [x1, y1 - f], [x1 - f * 1.05, y1 - f * 1.05]] as Pts).map(rot)
  poly(b.fill(INK, 0.16), ([[x1 - f, y1], [x1, y1 - f], [x1 + 0.6 * u, y1 + 0.6 * u]] as Pts).map(rot))   // the board seen under the corner
  poly(b.fill(PAPER, 0.96), sheet)
  poly(b.fill('#d9cdb8', 0.95), flap)
  ink(fine, [...sheet, sheet[0]], 330, true, amp * 0.25, step * 0.5)
  ink(fine, [flap[0], flap[2], flap[1]], 331, false, amp * 0.1, step * 0.3)
  const tk = rot(tack)
  b.fill(VERMILION, 0.85).moveTo(tk[0] + tackR, tk[1]); b.fill(VERMILION, 0.85).arc(tk[0], tk[1], tackR, 0, TAU)
  const padX = S.w * 0.08, top = S.y + Math.max(2.6 * u, S.h * 0.14)
  const tw = S.w - padX * 2, th = y1 - top - S.h * 0.06
  if (P.w < 64) {
    // too small to read: three lines of pen scribble, the last one short
    const sc = plain('fine:scrib', INK_A * 0.8, wOut * 0.7)
    for (let l = 0; l < 3; l++) {
      const y = top + (th * (l + 0.5)) / 3
      const len = tw * (l === 2 ? 0.55 : 0.92 - 0.08 * l)
      const pts: Pts = []
      for (let j = 0; j <= 9; j++) pts.push(rot([S.x + padX + (j / 9) * len, y + Math.sin(j * 2.3 + l * 1.7) * Math.min(1.2 * u, th * 0.06)]))
      appendPath(sc, pts)
    }
    b.flush(ctx)
    return
  }
  b.flush(ctx)
  // the words: the page's serif, italic, in ink, flush left, the block centred on the sheet's height
  const words = boardWords(ctx, text, tw, th)
  ctx.save()
  const o = rot([S.x + padX, top])
  ctx.translate(o[0], o[1])
  ctx.rotate(tilt)
  ctx.font = words.font
  ctx.fillStyle = INK
  ctx.globalAlpha = INK_A
  ctx.textBaseline = 'alphabetic'
  const lh = words.px * 1.18
  const y0 = (th - words.lines.length * lh) / 2 + words.px * 0.92
  words.lines.forEach((ln, k) => ctx.fillText(ln, ((k * 37) % 5) * 0.12 * u, y0 + k * lh))
  ctx.restore()
}

// ---------------------------------------------------------------- the stage: drawing

const AMBER = '#c9962a'           // brass, lantern light, a trophy
const LEAF = '#5f7f3a'
const SOIL = '#7a5a3a'
const STONE = '#9a978f'
const RAIN = '#8f8c86'
const LETTER = '#f6efe2'          // a letter's paper: warm, never white
const CONFETTI = [VERMILION, AMBER, DOOR.openai, DOOR.google, '#7a5c9e']

/** What every stage drawing needs from the plate: the layout, the batch, the pen and the clock. */
interface Pen {
  lay: VillageLayout; u: number; wOut: number; amp: number; step: number; b: Batch; c: number
  ink: (path: Path2D, pts: Pts, seed: number, close?: boolean, a?: number, st?: number) => void
  plain: (key: string, a: number, width: number, dash?: number[] | null) => Path2D
}

const ellipsePts = (cx: number, cy: number, rx: number, ry: number, a0 = 0, a1 = TAU, n = 14): Pts => {
  const out: Pts = []
  for (let j = 0; j <= n; j++) { const a = lerp(a0, a1, j / n); out.push([cx + Math.cos(a) * rx, cy + Math.sin(a) * ry]) }
  return out
}

/** One prop at a ground slot, in the houses' ink, hatching and washes. `tally` marks a ballot box's votes. */
function drawProp(pen: Pen, kind: PropKind, slot: GroundSlot, seed: number, tally = 0, glow = 0): void {
  const { u, wOut, b } = pen
  const S = slot.size, x = slot.x, y = slot.y
  const P = (dx: number, dy: number): Pt => [x + dx * S, y - dy * S]
  const R = (x0: number, y0: number, x1: number, y1: number): Pts => [P(x0, y0), P(x1, y0), P(x1, y1), P(x0, y1)]
  const out = pen.plain('out:prop', INK_A, wOut * 0.92)
  const fine = pen.plain('fine:prop', INK_A * 0.85, wOut * 0.7)
  const hat = pen.plain('hatch:prop', INK_A * 0.42, wOut * 0.55)
  const am = pen.amp * 0.3, st = Math.max(1.1 * u, S * 0.1)
  const ink = (p: Path2D, pts: Pts, k: number, close = false) => pen.ink(p, pts, seed + k, close, am, st)
  const wash = (color: string, a: number, pts: Pts) => poly(b.fill(color, a), pts)
  const dot = (p: Pt, r: number, color: string, a = 0.9) => { const f = b.fill(color, a); f.moveTo(p[0] + r, p[1]); f.arc(p[0], p[1], r, 0, TAU) }
  const shadow = (half: number) => wash(INK, 0.07, ellipsePts(x, y + 0.02 * S, half * S, 0.07 * S))
  switch (kind) {
    case 'stall': {
      shadow(0.64)
      const counter = R(-0.52, 0.44, 0.52, 0)
      wash(PAPER, 0.92, counter); wash(WOOD, 0.32, counter)
      const aw: Pts = [P(-0.6, 1), P(0.6, 1), P(0.66, 0.74), P(-0.66, 0.74)]
      wash(PAPER, 0.95, aw)
      for (let j = 0; j < 5; j++) {
        const t0 = j / 5, t1 = (j + 1) / 5
        const tl = P(lerp(-0.6, 0.6, t0), 1), tr = P(lerp(-0.6, 0.6, t1), 1), br = P(lerp(-0.66, 0.66, t1), 0.74), bl = P(lerp(-0.66, 0.66, t0), 0.74)
        const scal = ellipsePts((bl[0] + br[0]) / 2, bl[1], (br[0] - bl[0]) / 2, 0.07 * S, 0, Math.PI, 6)
        if (j % 2 === 0) { wash(VERMILION, 0.72, [tl, tr, br, bl]); wash(VERMILION, 0.72, scal) } else wash(PAPER, 0.95, scal)
        ink(fine, scal, 10 + j)
        if (j) ink(hat, [tl, bl], 20 + j)
      }
      ink(out, aw, 1, true)
      ink(out, [P(-0.48, 0.74), P(-0.48, 0.44)], 2); ink(out, [P(0.48, 0.74), P(0.48, 0.44)], 3)
      ink(out, counter, 4, true)
      ink(out, [P(-0.58, 0.45), P(0.58, 0.45)], 5)
      for (let j = 0; j < 3; j++) ink(hat, [P(-0.3 + j * 0.3, 0.38), P(-0.3 + j * 0.3, 0.06)], 30 + j)
      dot(P(-0.28, 0.5), 0.065 * S, AMBER); dot(P(0.0, 0.51), 0.065 * S, LEAF); dot(P(0.27, 0.5), 0.065 * S, VERMILION)
      break
    }
    case 'mailbox': {
      shadow(0.34)
      const post = R(-0.05, 0.57, 0.05, 0)
      wash(WOOD, 0.75, post)
      ink(out, post, 1, true)
      const box: Pts = [P(-0.34, 0.56), P(0.34, 0.56), ...ellipsePts(x, y - 0.76 * S, 0.34 * S, 0.17 * S, 0, -Math.PI, 8)]
      wash(PAPER, 0.92, box); wash(DOOR.openai, 0.5, box)
      ink(out, box, 2, true)
      ink(fine, [P(0.24, 0.58), P(0.24, 0.86)], 3)
      ink(out, [P(-0.27, 0.7), P(-0.27, 1)], 4)
      const flag = R(-0.27, 1, -0.08, 0.89)
      wash(VERMILION, 0.85, flag); ink(fine, flag, 5, true)
      const env: Pts = [P(0.28, 0.63), P(0.54, 0.67), P(0.52, 0.8), P(0.26, 0.76)]
      wash(LETTER, 0.96, env); ink(fine, env, 6, true)
      ink(fine, [env[3], [lerp(env[0][0], env[1][0], 0.5), lerp(env[0][1], env[2][1], 0.45)], env[2]], 7)
      break
    }
    case 'ballot': {
      shadow(0.38)
      const seat = R(-0.34, 0.46, 0.34, 0.4)
      wash(WOOD, 0.75, seat); ink(out, seat, 1, true)
      ink(out, [P(-0.28, 0.4), P(-0.36, 0)], 2); ink(out, [P(0.28, 0.4), P(0.36, 0)], 3); ink(fine, [P(0.02, 0.4), P(0.03, 0.06)], 4)
      const top: Pts = [P(-0.27, 0.86), P(0.27, 0.86), P(0.21, 0.92), P(-0.21, 0.92)]
      const box = R(-0.27, 0.86, 0.27, 0.46)
      wash(PAPER, 0.95, box); wash(WOOD, 0.16, box); wash(PAPER, 0.95, top)
      ink(out, box, 5, true); ink(fine, top, 6, true)
      pen.ink(pen.plain('out:slot', INK_A, wOut * 1.3), [P(-0.1, 0.89), P(0.09, 0.89)], seed + 7, false, am * 0.5, st)
      const slip: Pts = [P(-0.05, 0.89), P(0.07, 0.89), P(0.11, 1.0), P(-0.01, 1.0)]
      wash(LETTER, 0.96, slip); ink(fine, slip, 8, true)
      if (tally > 0) {
        for (let t = 0; t < tally; t++) {
          const g = Math.floor(t / 5), r = t % 5
          if (r === 4) ink(fine, [P(-0.21 + g * 0.3, 0.53), P(-0.0 + g * 0.3, 0.66)], 40 + t)
          else ink(fine, [P(-0.18 + g * 0.3 + r * 0.055, 0.52), P(-0.18 + g * 0.3 + r * 0.055, 0.68)], 40 + t)
        }
      } else ink(fine, [P(-0.1, 0.66), P(-0.03, 0.58), P(0.12, 0.77)], 9)
      break
    }
    case 'scaffold': {
      shadow(0.46)
      drawFrame(pen, x - 0.42 * S, x + 0.42 * S, y, S, MOMENT_DUR, seed, false)
      break
    }
    case 'gallery': {
      shadow(0.72)
      ink(out, [P(-0.68, 0), P(-0.68, 0.94)], 1); ink(out, [P(0.68, 0), P(0.68, 0.94)], 2)
      ink(out, [P(-0.74, 0.9), P(0.74, 0.9)], 3)
      const frames: [number, number, number][] = [[-0.42, 0.34, 0.3], [0, 0.28, 0.4], [0.42, 0.34, 0.3]]
      frames.forEach(([cx, fw, fh], j) => {
        const t = 0.8, bo = t - fh
        ink(fine, [P(cx - fw * 0.3, t), P(cx, 0.88), P(cx + fw * 0.3, t)], 10 + j)
        const outer = R(cx - fw / 2, t, cx + fw / 2, bo), inner = R(cx - fw / 2 + 0.045, t - 0.045, cx + fw / 2 - 0.045, bo + 0.045)
        wash(WOOD, 0.8, outer); wash(PAPER, 0.96, inner)
        const il = cx - fw / 2 + 0.045, ir = cx + fw / 2 - 0.045, ib = bo + 0.045
        if (j === 0) { wash(LEAF, 0.55, [P(il, ib), P(cx - 0.02, ib + fh * 0.42), P(ir, ib + fh * 0.18), P(ir, ib)]); dot(P(ir - 0.06, t - 0.1), 0.035 * S, AMBER) }
        else if (j === 1) { dot(P(cx, ib + fh * 0.36), 0.075 * S, VERMILION, 0.8); ink(hat, [P(cx, ib + 0.02), P(cx, ib + fh * 0.28)], 50) }
        else { wash(DOOR.openai, 0.45, R(il, ib + fh * 0.32, ir, ib)); wash(PAPER, 0.9, [P(cx - 0.02, ib + fh * 0.3), P(cx + 0.03, ib + fh * 0.62), P(cx + 0.09, ib + fh * 0.3)]) }
        ink(out, outer, 20 + j, true); ink(fine, inner, 30 + j, true)
      })
      break
    }
    case 'chessboard': {
      shadow(0.56)
      drawTable(pen, x, y, S, seed, true)
      break
    }
    case 'stage': {
      shadow(0.76)
      const front = R(-0.7, 0.3, 0.7, 0), top: Pts = [P(-0.7, 0.3), P(0.7, 0.3), P(0.62, 0.38), P(-0.62, 0.38)]
      wash(PAPER, 0.92, front); wash(WOOD, 0.62, front); wash(PAPER, 0.92, top); wash(WOOD, 0.3, top)
      for (let j = -2; j <= 2; j++) ink(hat, [P(j * 0.25, 0.27), P(j * 0.25 + 0.01, 0.03)], 40 + j)
      ink(out, [P(-0.6, 0.36), P(-0.6, 1)], 1); ink(out, [P(0.6, 0.36), P(0.6, 1)], 2); ink(out, [P(-0.67, 0.98), P(0.67, 0.98)], 3)
      for (const sd of [-1, 1]) {
        const c: Pts = [P(-0.6 * sd, 0.97), P(-0.27 * sd, 0.97), P(-0.36 * sd, 0.8), P(-0.47 * sd, 0.62), P(-0.53 * sd, 0.39), P(-0.6 * sd, 0.39)]
        wash(VERMILION, 0.74, c); ink(fine, c, 5 + sd, true)
        ink(hat, [P(-0.5 * sd, 0.95), P(-0.56 * sd, 0.42)], 8 + sd); ink(hat, [P(-0.4 * sd, 0.95), P(-0.5 * sd, 0.62)], 11 + sd)
        dot(P(-0.47 * sd, 0.62), 0.03 * S, AMBER)
      }
      ink(out, front, 14, true); ink(fine, top, 15, true)
      break
    }
    case 'press': {
      shadow(0.56)
      const top = R(-0.52, 0.5, 0.52, 0.44)
      wash(PAPER, 0.92, top); wash(WOOD, 0.6, top)
      ink(out, top, 1, true)
      ink(out, [P(-0.45, 0.44), P(-0.45, 0)], 2); ink(out, [P(0.45, 0.44), P(0.45, 0)], 3)
      ink(fine, R(-0.43, 0.44, -0.04, 0.3), 4, true)
      dot(P(-0.235, 0.37), 0.022 * S, INK, 0.7)
      for (let j = 0; j < 5; j++) {
        const yy = 0.5 + j * 0.06, off = j % 2 ? 0.025 : -0.015
        const sh = R(-0.42 + off, yy + 0.06, 0.06 + off, yy)
        wash(LETTER, 0.96, sh)
        ink(fine, [P(-0.42 + off, yy + 0.06), P(0.06 + off, yy + 0.06), P(0.06 + off, yy)], 10 + j)
      }
      ink(hat, [P(-0.36, 0.84), P(-0.02, 0.84)], 20)
      const bell: Pts = [...ellipsePts(x + 0.27 * S, y - 0.5 * S, 0.13 * S, 0.14 * S, Math.PI, TAU, 8)]
      wash(AMBER, 0.85, bell); ink(out, bell, 21, true)
      dot(P(0.27, 0.66), 0.035 * S, INK, 0.8)
      break
    }
    case 'garden': {
      shadow(0.72)
      const bed = ellipsePts(x - 0.2 * S, y, 0.5 * S, 0.17 * S, Math.PI, TAU, 10)
      wash(SOIL, 0.5, bed); ink(out, bed, 1, true)
      for (let j = 0; j < 3; j++) ink(hat, [P(-0.5 + j * 0.25, 0.04), P(-0.4 + j * 0.25, 0.1)], 2 + j)
      const heads = [VERMILION, AMBER, '#7a5c9e', VERMILION, AMBER]
      for (let j = 0; j < 5; j++) {
        const fx = -0.52 + j * 0.16, tp = 0.38 + 0.1 * ((j * 7) % 3)
        ink(fine, [P(fx, 0.1), P(fx + 0.02, tp)], 10 + j)
        ink(fine, [P(fx + 0.01, 0.2), P(fx + 0.07, 0.26)], 20 + j)
        dot(P(fx + 0.02, tp), 0.065 * S, heads[j], 0.85)
      }
      ink(out, [P(0.44, 0.04), P(0.48, 0.8)], 30); ink(out, [P(0.41, 0.8), P(0.55, 0.8)], 31)
      const blade: Pts = [P(0.38, 0.17), P(0.5, 0.18), P(0.49, 0.02), P(0.4, 0.01)]
      wash(STONE, 0.75, blade); ink(fine, blade, 32, true)
      const bag: Pts = [P(0.53, 0), P(0.72, 0), P(0.71, 0.22), P(0.67, 0.29), P(0.58, 0.29), P(0.55, 0.22)]
      wash(PAPER, 0.92, bag); wash(SOIL, 0.25, bag); ink(out, bag, 33, true); ink(fine, [P(0.56, 0.24), P(0.7, 0.24)], 34)
      break
    }
    case 'lanterns': {
      shadow(0.66)
      ink(out, [P(-0.62, 0), P(-0.62, 1)], 1); ink(out, [P(0.62, 0), P(0.62, 1)], 2)
      const str: Pts = []
      for (let j = 0; j <= 12; j++) { const t = j / 12; str.push(P(lerp(-0.62, 0.62, t), 0.95 - 0.72 * t * (1 - t))) }
      ink(fine, str, 3)
      ;[0.2, 0.4, 0.6, 0.8].forEach((t, j) => {
        const an = P(lerp(-0.62, 0.62, t), 0.95 - 0.72 * t * (1 - t))
        const sway = 0.025 * S * Math.sin((TAU * pen.c) / 3 + j * 1.3)
        const cx = an[0] + sway, cy = an[1] + 0.17 * S
        wash(AMBER, 0.14 + 0.22 * glow, ellipsePts(cx, cy, (0.19 + 0.09 * glow) * S, (0.19 + 0.09 * glow) * S, 0, TAU, 10))
        const body = ellipsePts(cx, cy, 0.085 * S, 0.11 * S, 0, TAU, 10)
        wash(j % 2 ? AMBER : VERMILION, 0.85, body)
        ink(fine, [an, [cx, cy - 0.11 * S]], 10 + j)
        ink(fine, body, 20 + j, true)
        ink(hat, [[cx - 0.05 * S, cy], [cx + 0.05 * S, cy]], 30 + j)
      })
      break
    }
    case 'cloud': {
      const pud = ellipsePts(x, y, 0.4 * S, 0.055 * S)
      wash(DOOR.openai, 0.16, pud); ink(fine, ellipsePts(x, y, 0.4 * S, 0.055 * S, 0.3, Math.PI - 0.3, 8), 1)
      const rain = pen.plain('fine:rain', INK_A * 0.75, wOut * 0.7)
      for (let j = 0; j < 7; j++) {
        const ph = ((pen.c / 1.5 + j * 0.37) % 1 + 1) % 1
        const rx = -0.36 + j * 0.12, top = 0.68 - ph * 0.45
        const bot = Math.max(0.05, top - 0.24)
        if (top - bot > 0.04) ink(rain, [P(rx, top), P(rx - 0.06 * (top - bot) / 0.24, bot)], 10 + j)
      }
      const bumps: [number, number, number][] = [[-0.33, 0.79, 0.14], [-0.12, 0.88, 0.18], [0.13, 0.87, 0.17], [0.35, 0.79, 0.13]]
      const cl: Pts = []
      const n = 18
      for (let j = 0; j <= n; j++) {
        const dx = lerp(-0.47, 0.48, j / n)
        let top = 0.7
        for (const [bx, by, r] of bumps) { const d = dx - bx; if (Math.abs(d) < r) top = Math.max(top, by + Math.sqrt(r * r - d * d)) }
        cl.push(P(dx, top))
      }
      cl.push(P(0.44, 0.68), P(-0.43, 0.68))
      wash(PAPER, 0.92, cl); wash(RAIN, 0.38, cl)
      ink(out, cl, 2, true)
      break
    }
    case 'signpost': {
      shadow(0.42)
      const post = R(-0.04, 1, 0.04, 0)
      wash(WOOD, 0.75, post); ink(out, post, 1, true)
      const a1: Pts = [P(-0.1, 0.9), P(0.42, 0.93), P(0.54, 0.85), P(0.42, 0.77), P(-0.1, 0.78)]
      const a2: Pts = [P(0.1, 0.66), P(-0.42, 0.64), P(-0.54, 0.565), P(-0.42, 0.49), P(0.1, 0.53)]
      for (const [k, a] of [a1, a2].entries()) { wash(PAPER, 0.92, a); wash(WOOD, 0.5, a); ink(out, a, 2 + k, true); ink(hat, [a[0], a[1]].map(([px, py]) => [px, py + 0.05 * S] as Pt), 6 + k) }
      for (const [k, sx] of [-0.14, 0.13].entries()) { const st0 = ellipsePts(x + sx * S, y - 0.03 * S, 0.08 * S, 0.045 * S, 0, TAU, 8); wash(STONE, 0.6, st0); ink(fine, st0, 10 + k, true) }
      break
    }
  }
}

/** A table, its top seen from a little above; with a board, a 4 by 4 chequer and two pieces on it. */
function drawTable(pen: Pen, x: number, y: number, S: number, seed: number, board: boolean): void {
  const { b, wOut } = pen
  const P = (dx: number, dy: number): Pt => [x + dx * S, y - dy * S]
  const out = pen.plain('out:prop', INK_A, wOut * 0.92), fine = pen.plain('fine:prop', INK_A * 0.85, wOut * 0.7)
  const am = pen.amp * 0.3, st = Math.max(1.1 * pen.u, S * 0.1)
  const ink = (p: Path2D, pts: Pts, k: number, close = false) => pen.ink(p, pts, seed + k, close, am, st)
  ink(fine, [P(-0.3, 0.5), P(-0.31, 0.12)], 1); ink(fine, [P(0.3, 0.5), P(0.31, 0.12)], 2)
  ink(out, [P(-0.4, 0.28), P(-0.42, 0)], 3); ink(out, [P(0.4, 0.28), P(0.42, 0)], 4)
  const top: Pts = [P(-0.5, 0.32), P(0.5, 0.32), P(0.36, 0.56), P(-0.36, 0.56)]
  const apron: Pts = [P(-0.5, 0.32), P(0.5, 0.32), P(0.5, 0.26), P(-0.5, 0.26)]
  poly(b.fill(PAPER, 0.92), top); poly(b.fill(WOOD, 0.55), top); poly(b.fill(WOOD, 0.8), apron)
  ink(out, top, 5, true); ink(fine, apron, 6, true)
  if (!board) return
  const A = P(-0.38, 0.35), B = P(0.38, 0.35), C = P(0.27, 0.53), D = P(-0.27, 0.53)
  const at = (i: number, j: number): Pt => {
    const l: Pt = [lerp(A[0], D[0], j / 4), lerp(A[1], D[1], j / 4)], r: Pt = [lerp(B[0], C[0], j / 4), lerp(B[1], C[1], j / 4)]
    return [lerp(l[0], r[0], i / 4), lerp(l[1], r[1], i / 4)]
  }
  poly(b.fill(PAPER, 0.95), [A, B, C, D])
  for (let i = 0; i < 4; i++) for (let j = 0; j < 4; j++) if ((i + j) % 2 === 0) poly(b.fill(INK, 0.5), [at(i, j), at(i + 1, j), at(i + 1, j + 1), at(i, j + 1)])
  ink(fine, [A, B, C, D], 7, true)
  const piece = (p: Pt, dark: boolean, k: number) => {
    const r = 0.05 * S
    const f = b.fill(dark ? INK : LETTER, dark ? 0.85 : 0.96)
    f.moveTo(p[0] + r, p[1] - 0.12 * S); f.arc(p[0], p[1] - 0.12 * S, r, 0, TAU)
    ink(fine, [[p[0], p[1]], [p[0], p[1] - 0.08 * S]], 20 + k)
    if (!dark) ink(fine, ellipsePts(p[0], p[1] - 0.12 * S, r, r, 0, TAU, 8), 30 + k, true)
  }
  const c1 = at(1.5, 2.5), c2 = at(2.5, 1.5)
  piece(c1, true, 0); piece(c2, false, 1)
}

/**
 * A frame of planks from x0 to x1 standing on `base`, h tall, as it stands at T seconds into a build: the uprights rise
 * (0.2 to 1.4 s), three planks drop in one by one with the hammer's blows (1.6, 2.5, 3.4 s), the brace (4.3 s), and a
 * flag unfurls on top (4.6 to 5.4 s) when `flag`.
 */
function drawFrame(pen: Pen, x0: number, x1: number, base: number, h: number, T: number, seed: number, flag: boolean, side: 1 | -1 = 1): void {
  const { b, wOut, u } = pen
  const out = pen.plain('out:prop', INK_A, wOut * 0.92), fine = pen.plain('fine:prop', INK_A * 0.85, wOut * 0.7)
  const am = pen.amp * 0.3, st = Math.max(1.1 * u, h * 0.1)
  const ink = (p: Path2D, pts: Pts, k: number, close = false) => pen.ink(p, pts, seed + k, close, am, st)
  const fw = x1 - x0, inset = fw * 0.1, ov = fw * 0.1
  const g1 = smooth(0.2, 1.3, T), g2 = smooth(0.35, 1.45, T)
  if (g1 > 0) ink(out, [[x0 + inset, base], [x0 + inset, base - h * g1]], 1)
  if (g2 > 0) ink(out, [[x1 - inset, base], [x1 - inset, base - h * g2]], 2)
  const th = Math.max(1.1 * u, h * 0.055)
  for (let k = 0; k < 3; k++) {
    const tk = 1.6 + 0.9 * k
    if (T < tk - 0.3) continue
    const drop = (1 - smooth(tk - 0.3, tk, T)) * h * 0.22
    const yy = base - h * (0.33 + 0.32 * k) - drop
    const pl: Pts = [[x0 - ov, yy], [x1 + ov, yy + 0.2 * u], [x1 + ov, yy + th], [x0 - ov, yy + th - 0.2 * u]]
    poly(b.fill(PAPER, 0.92), pl); poly(b.fill(WOOD, 0.6), pl)
    ink(fine, pl, 10 + k, true)
  }
  if (T >= 4.0) {
    const q = smooth(4.0, 4.3, T)
    ink(fine, [[x0 + inset, base - 0.4 * u], [lerp(x0 + inset, x1 - inset, q), base - lerp(0.4 * u, h * 0.64, q)]], 20)
  }
  if (flag && T >= 4.6) {
    const px = side > 0 ? x1 - inset : x0 + inset
    const top = base - h, pole = h * 0.32
    ink(out, [[px, top], [px, top - pole]], 30)
    const q = smooth(4.6, 5.4, T), fl = fw * 0.55 * q, dir = side > 0 ? 1 : -1
    if (fl > 0.3 * u) {
      const wave = 0.06 * pole * Math.sin(TAU * q)
      const pen1: Pts = [[px, top - pole], [px + dir * fl, top - pole * 0.82 + wave], [px, top - pole * 0.62]]
      poly(b.fill(VERMILION, 0.85), pen1)
      ink(fine, pen1, 31, true)
    }
  }
}

/** A letter: a small envelope centred at p, w wide, tilted by `rot`. */
function envelope(pen: Pen, p: Pt, w: number, rot: number, seed: number): void {
  const hh = w * 0.32, hw = w / 2, cs = Math.cos(rot), sn = Math.sin(rot)
  const T = (dx: number, dy: number): Pt => [p[0] + dx * cs - dy * sn, p[1] + dx * sn + dy * cs]
  const body: Pts = [T(-hw, -hh), T(hw, -hh), T(hw, hh), T(-hw, hh)]
  poly(pen.b.fill(LETTER, 0.97), body)
  const fine = pen.plain('fine:letter', INK_A * 0.85, pen.wOut * 0.62)
  pen.ink(fine, body, seed, true, pen.amp * 0.15, Math.max(0.8 * pen.u, w * 0.3))
  pen.ink(fine, [T(-hw, -hh), T(0, hh * 0.15), T(hw, -hh)], seed + 1, false, pen.amp * 0.1, Math.max(0.8 * pen.u, w * 0.3))
}

/** A letter's width: two thirds of a figure (its height about two fifths), never under 4.5 units. */
export const letterW = (lay: VillageLayout) => Math.max(4.5 * lay.unit, 0.66 * lay.figure)
/** The letters of a send: from each named house's door, one after another, toward the mailbox or the gate. */
function sendLetters(lay: VillageLayout, houses: HouseScene[], m: Moment, spots: StageSpot[]) {
  const who = castOf(lay, houses, m, spots)
  const u = lay.unit
  const ew = letterW(lay), eh = ew * 0.64
  const mb = spots.find((s) => s.kind === 'mailbox')
  // at the mailbox (a prop, or the fixture a send brings); with every slot taken, on the grass in front of the road below
  // the gate, clear of the gate's opening and the visitor, a smaller stack
  const g = lay.gate
  const gx = g.x + g.w * 0.5 + ew * 0.95
  const bottom = lay.h - lay.pad.bottom
  const gy = Math.min(bottom - 1.2 * u, Math.max(roadAt(lay, gx, 1) + 1.5 * u + eh, g.y + 1 * u + 1.42 * eh))
  const dst: Pt = mb ? [mb.slot.x - 0.55 * mb.slot.size, mb.slot.y] : [gx, gy]
  const L = Math.min(mb ? 8 : 4, who.length * 2)
  const letters: { src: Pt; land: Pt; t0: number; rot: number }[] = []
  for (let q = 0; q < L; q++) {
    const s = lay.houses[who[q % who.length]]
    const src: Pt = [s.door.x + s.door.w / 2, s.door.y + s.door.h * 0.3]
    const h = (((q + 1) * 2654435761) >>> 0) / 4294967296
    // at the mailbox one stack; on the grass a low pile two letters wide
    const land: Pt = mb
      ? [dst[0] + (h - 0.5) * ew * 0.3, dst[1] - eh * 0.5 - q * eh * 0.42]
      : [dst[0] + ((q % 2) - 0.5) * ew * 0.8 + (h - 0.5) * ew * 0.1, dst[1] - eh * 0.5 - Math.floor(q / 2) * eh * 0.42]
    letters.push({ src, land, t0: 0.2 + (L > 1 ? (q * 3.6) / (L - 1) : 0), rot: (h - 0.5) * 0.3 })
  }
  return { letters, ew, eh }
}
const FLIGHT = 1.6

/** The trophy of a win: centre x, its foot, its height, and the door it rose from. */
function trophySpot(lay: VillageLayout, i: number): { x: number; y: number; h: number; from: number } {
  const s = lay.houses[i], u = lay.unit
  const x = s.door.x + s.door.w / 2
  const roofY = x <= s.apex[0] ? lerp(s.eaveL[1], s.apex[1], (x - s.eaveL[0]) / (s.apex[0] - s.eaveL[0])) : lerp(s.apex[1], s.eaveR[1], (x - s.apex[0]) / (s.eaveR[0] - s.apex[0]))
  const h = Math.max(7 * u, 0.42 * s.wallW, 0.6 * lay.figure)
  const y = Math.max(lay.pad.top + h * 1.6, roofY + h * 0.1)
  return { x, y, h, from: s.door.y + s.door.h * 0.6 }
}
function drawTrophy(pen: Pen, cx: number, yb: number, th: number, seed: number): void {
  const P = (dx: number, dy: number): Pt => [cx + dx * th, yb - th + dy * th]
  const cup: Pts = [P(-0.36, 0), P(0.36, 0), P(0.3, 0.3), P(0.1, 0.52), P(0.06, 0.62), P(0.06, 0.8), P(0.24, 0.86), P(0.26, 1), P(-0.26, 1), P(-0.24, 0.86), P(-0.06, 0.8), P(-0.06, 0.62), P(-0.1, 0.52), P(-0.3, 0.3)]
  poly(pen.b.fill(AMBER, 0.92), cup)
  const out = pen.plain('out:trophy', INK_A, pen.wOut * 0.9), fine = pen.plain('fine:trophy', INK_A * 0.85, pen.wOut * 0.65)
  const st = Math.max(0.9 * pen.u, th * 0.12)
  pen.ink(out, cup, seed, true, pen.amp * 0.15, st)
  for (const sd of [-1, 1]) pen.ink(fine, [P(0.34 * sd, 0.06), P(0.5 * sd, 0.14), P(0.46 * sd, 0.3), P(0.24 * sd, 0.4)], seed + 2 + sd, false, pen.amp * 0.1, st)
  pen.ink(fine, [P(-0.2, 0.08), P(-0.15, 0.3)], seed + 5, false, pen.amp * 0.1, st)
}

/**
 * The fail's sign by the house: its foot, post length, which way it falls (away from the door), and the row layer it is
 * drawn with. A back-row house's sign stands where its footpath meets the road, in the gap between the two front houses
 * below it, a little off the path, and is drawn with the front row: so it falls in view, never behind a front roof.
 */
export function failSign(lay: VillageLayout, i: number): { at: Pt; len: number; side: 1 | -1; row: 0 | 1 } {
  const s = lay.houses[i], u = lay.unit
  const doorSide = s.door.x + s.door.w / 2 < s.x ? -1 : 1
  const side: 1 | -1 = doorSide > 0 ? -1 : 1
  const len = signLen(lay)
  if (s.row === 1) {
    // fallen, the board's top lies about half a post above the foot: low enough on the road to clear the front walls' feet
    const x = s.path[1][0] + side * 0.22 * lay.figure
    const feet = Math.max(...[i - 1, i + 1].map((k) => lay.houses[k]?.y ?? -Infinity))
    const y = Math.min(roadAt(lay, x, 0.75), Math.max(roadAt(lay, x, 0.3), feet + 0.5 * len + 1 * u))
    return { at: [x, y], len, side, row: 0 }
  }
  return { at: [s.x + side * s.wallW * 0.3, s.y + 1.4 * u], len, side, row: 0 }
}
const signLen = (lay: VillageLayout) => Math.max(9 * lay.unit, 1.25 * lay.figure)
/** The sign of a fail that names nobody: by the board, in the grass just right of it (past a frame raised at the board's
 * side when the scene has one: `shift`), falling away from it. */
export function boardSign(lay: VillageLayout, shift = 0): { at: Pt; len: number; side: 1 | -1; row: 0 | 1 } {
  const len = Math.max(signLen(lay), 1.15 * boardScale(lay))
  const y = Math.min(lay.h - lay.pad.bottom - 2 * lay.unit, gatherSpot(lay, 0)[1] + 0.12 * len)
  return { at: [boardRight(lay) + 0.3 * lay.figure + shift, y], len, side: 1, row: 0 }
}
/** How far a board-side sign stands past the board: past the board-side frame when the scene raises one. */
export function signShift(lay: VillageLayout, houses: HouseScene[], moments: Moment[], spots: StageSpot[]): number {
  const frame = moments.some((m) => m.kind === 'build' && castOf(lay, houses, m, spots).length === 0) && !spots.some((sp) => sp.kind === 'scaffold')
  if (!frame) return 0
  const bs = boardScaffold(lay)
  return bs.x1 - boardRight(lay) + 0.1 * lay.figure
}
/**
 * The sign at T: its post and its board, as drawn (house -1 = the board's sign). As it falls its board turns flat and
 * comes to lie on the ground beyond the post, a plank on the grass, never a crate standing on its end. `B(across, along)`
 * is a point on the board: across -0.5..0.5 of its width, along 0..1 from its top edge.
 */
export function failSignPts(lay: VillageLayout, i: number, T: number, shift = 0): { post: Pts; board: Pts; Rt: (dx: number, dy: number) => Pt; B: (a: number, v: number) => Pt; L: number; bw: number; bh: number } {
  const sg = i < 0 ? boardSign(lay, shift) : failSign(lay, i)
  const ang = failAngle(T), th = ang * sg.side, cs = Math.cos(th), sn = Math.sin(th)
  const Rt = (dx: number, dy: number): Pt => [sg.at[0] + dx * cs - dy * sn, sg.at[1] + dx * sn + dy * cs]
  const L = sg.len, bh = L * 0.46
  const k = clamp01((ang - 0.32) / 1.18)
  const bw = L * 0.78 * (1 - 0.72 * k), off = -sg.side * (bw / 2) * k
  const B = (a: number, v: number): Pt => Rt(off + a * bw, -L + bh * 0.1 + v * bh)
  const post: Pts = [Rt(0, 0), Rt(0, -L)]
  const board: Pts = [B(-0.5, 0), B(0.5, 0), B(0.5, 1), B(-0.5, 1)]
  return { post, board, Rt, B, L, bw, bh }
}
/** The sign's angle from upright at T: it wobbles, leans, falls, bounces once and lies there. */
export function failAngle(T: number): number {
  const wob = T < 1.2 ? 0.08 * Math.sin(T * 9) * (T / 1.2) : 0
  const lean = smooth(1.2, 2.6, T) * 0.32
  const fall = T < 2.6 ? 0 : ease.in(clamp01((T - 2.6) / 0.55)) * (1.5 - 0.32)
  const bounce = T > 3.15 ? Math.sin(clamp01((T - 3.15) / 0.4) * Math.PI) * 0.14 : 0
  return wob + lean + fall - bounce
}

/** The bunting a celebration leaves: from the first named house's ridge to a neighbour's in its own row (or along its own
 * eaves when it stands alone). */
function buntingSpan(lay: VillageLayout, who: number[]): { a: Pt; b: Pt; sag: number; row: 0 | 1 } {
  const i = who[0], s = lay.houses[i]
  const stepN = lay.rows === 2 ? 2 : 1
  let j = who.slice(1).find((k) => Math.abs(k - i) === stepN)
  if (j === undefined) j = lay.houses[i + stepN] ? i + stepN : lay.houses[i - stepN] ? i - stepN : undefined
  if (j === undefined) return { a: s.eaveL, b: s.eaveR, sag: s.wallH * 0.18, row: s.row }
  const t = lay.houses[j]
  const [l, r] = s.x < t.x ? [s, t] : [t, s]
  const a: Pt = [l.apex[0] + l.wallW * 0.1, l.apex[1] + l.roofH * 0.2], bb: Pt = [r.apex[0] - r.wallW * 0.1, r.apex[1] + r.roofH * 0.2]
  return { a, b: bb, sag: Math.abs(bb[0] - a[0]) * 0.16, row: s.row }
}

/** A win at T: the trophy rises from `from` to its spot (0.3 to 2 s), glints as it settles, and a flag unfurls beside it. */
function drawWin(pen: Pen, tr: { x: number; y: number; h: number; from: number }, T: number, seed: number): void {
  const { b, u } = pen
  const q = smooth(0.3, 2.0, T)
  if (q <= 0) return
  const yb = lerp(tr.from, tr.y, ease.outBack(q))
  if (T >= 2.0 && T < 3.2) {
    // the glint as it settles: short rays that reach out and fade
    const g = smooth(2.0, 2.4, T) * (1 - smooth(2.7, 3.2, T))
    const rays = pen.plain(`mark:glint${eighths(g)}`, INK_A * eighths(g), pen.wOut * 0.7)
    const cx = tr.x, cy = yb - tr.h * 0.7
    for (let k = 0; k < 6; k++) {
      const a = -Math.PI / 2 + (k - 2.5) * 0.55, r0 = tr.h * 0.75, r1 = tr.h * (0.95 + 0.25 * g)
      appendPath(rays, [[cx + Math.cos(a) * r0, cy + Math.sin(a) * r0], [cx + Math.cos(a) * r1, cy + Math.sin(a) * r1]])
    }
  }
  if (T >= 2.2) {
    const fq = smooth(2.2, 3.2, T)
    const px = tr.x + tr.h * 0.3, top = yb - tr.h, pole = tr.h * 0.6
    pen.ink(pen.plain('out:trophy', INK_A, pen.wOut * 0.9), [[px, top + tr.h * 0.05], [px, top - pole]], seed + 7, false, pen.amp * 0.1, Math.max(0.9 * u, pole * 0.3))
    const fl = tr.h * 0.5 * fq
    if (fl > 0.3 * u) {
      const pn: Pts = [[px, top - pole], [px + fl, top - pole * 0.8 + 0.08 * pole * Math.sin(TAU * fq)], [px, top - pole * 0.6]]
      poly(b.fill(VERMILION, 0.85), pn)
    }
  }
  drawTrophy(pen, tr.x, yb, tr.h, seed)
}

/** A fail's sign at T, house i (-1 = the board's sign): it wobbles, leans, falls flat with a puff of dust. */
function drawSign(pen: Pen, lay: VillageLayout, i: number, T: number, seed: number, shift = 0): void {
  const { b, u } = pen
  const { post, board, B, L, bh } = failSignPts(lay, i, T, shift)
  const outp = pen.plain('out:sign', INK_A, pen.wOut * 0.9), finep = pen.plain('fine:sign', INK_A * 0.85, pen.wOut * 0.65)
  pen.ink(pen.plain('out:signpost', INK_A, pen.wOut * 1.5), post, seed + 1, false, pen.amp * 0.2, Math.max(1 * u, L * 0.2))
  poly(b.fill(PAPER, 0.92), board); poly(b.fill(WOOD, 0.5), board)
  pen.ink(outp, board, seed + 2, true, pen.amp * 0.15, Math.max(1 * u, bh * 0.3))
  for (let k = 0; k < 2; k++) {
    const ln: Pts = []
    for (let j = 0; j <= 5; j++) ln.push(B(-0.32 + (j / 5) * (k ? 0.4 : 0.64), 0.28 + 0.3 * k + Math.sin(j * 2.2 + k) * 0.06))
    appendPath(finep, ln)
  }
  if (T >= 3.15 && T < 3.9) {
    const d = eighths(1 - smooth(3.4, 3.9, T))
    const dust = pen.plain(`fine:dust${d}`, INK_A * 0.6 * d, pen.wOut * 0.6)
    const land = B(0, 0.5)
    for (let k = -1; k <= 1; k++) appendPath(dust, [[land[0] + k * 0.25 * L, land[1] - 0.05 * L], [land[0] + k * 0.38 * L, land[1] - 0.22 * L]])
  }
}

/** Every tag a prop carries: fitted at 9 px or more in the board's serif, at most 2 lines; scribbles when too small. */
const tagCaches = new WeakMap<CanvasRenderingContext2D, Map<string, { px: number; lines: string[] } | null>>()
function tagWords(ctx: CanvasRenderingContext2D, text: string, maxW: number, px0: number): { px: number; lines: string[]; font: string } | null {
  const fam = serif()
  const ready = italicReady(fam)
  const key = `${text}|${maxW.toFixed(1)}|${px0}|${fam}|${ready ? 1 : 0}`
  let cache = tagCaches.get(ctx)
  if (!cache) { cache = new Map(); tagCaches.set(ctx, cache) }
  let hit = cache.get(key)
  if (hit === undefined) {
    hit = null
    const words = text.split(/\s+/).filter(Boolean)
    // one line at any size down to 9 px first (the foreground holds one line under the prop), two lines only after
    for (const maxLines of [1, 2]) {
      for (let px = px0; px >= 9 && !hit; px -= 0.5) {
        ctx.font = `italic ${px}px ${fam}`
        // whole words only: a tag that would cut a word in two scribbles instead
        if (!words.every((w) => ctx.measureText(w).width <= maxW)) continue
        const r = wrapBoard(text, maxW, (s) => ctx.measureText(s).width, maxLines)
        if (r.fits) hit = { px, lines: r.lines }
      }
      if (hit) break
    }
    if (cache.size > 48) cache.clear()
    cache.set(key, hit)
  }
  return hit ? { ...hit, font: `italic ${hit.px}px ${fam}` } : null
}

/**
 * The stage, one layer at a time: 'row1' and 'row0' draw what stands by a house (a scaffold, a trophy, the bunting, a
 * fallen sign, a sitter on the step) with that row, so the front row stands in front of the back row's; 'ground' draws
 * the props, the fixtures and the residents who come out to the grass, and the props' tags; 'air' draws letters in
 * flight and falling confetti over everything.
 */
function drawStage(ctx: CanvasRenderingContext2D, pen: Pen, scene: VillageScene, houses: HouseScene[], layer: 'row1' | 'row0' | 'ground' | 'air'): void {
  const { lay, u, b, c } = pen
  const f = lay.figure
  const moments = (scene.moments ?? []).slice(0, 6)
  const props = scene.props ?? []
  if (!moments.length && !props.length) return
  const spots = stageSpots(lay, props, moments)
  const figs = (ghost: boolean, a: number) => ({
    pen: pen.plain(`fig:${ghost ? 'g' : 'o'}${a < 1 ? a : ''}`, (ghost ? GHOST_A : INK_A) * a, Math.max(pen.wOut, u * 0.9)),
    fill: b.fill(INK, (ghost ? GHOST_A : INK_A) * a),
  })
  // each resident once: the moment that owns them now draws them, the others leave them out (stageFigures)
  const owned = stageFigures(lay, scene, c)
  const drawFigs = (mi: number, m: Moment, want: 0 | 1 | 2, after?: (fg: MomentFigure, hand: Pt) => void) => {
    for (const fg of owned[mi] ?? []) {
      if (fg.layer !== want || fg.a <= 0) continue
      const hs = houses.find((h) => h.n === fg.n)
      const ink = figs(hs?.ghost === true, fg.a)
      const breath = fg.pose === 'walk' ? 0 : 0.5 * u * Math.sin((TAU * c) / 3 + fg.n)
      const hand = figure(ink.pen, ink.fill, fg.at[0], fg.at[1] - fg.hop, fg.size, { facing: fg.facing, phase: fg.phase, pose: fg.pose, breath, gesture: fg.gesture }, seedOf(`house-${fg.n}-moment-${m.kind}`), pen.amp)
      after?.(fg, hand)
    }
  }
  const rowOf = (i: number) => lay.houses[i].row

  if (layer === 'row0' || layer === 'row1') {
    const row = layer === 'row0' ? 0 : 1
    for (const [mi, m] of moments.entries()) {
      const T = momentTime(m, c)
      if (T === null) continue
      const who = castOf(lay, houses, m, spots)
      if (!who.length) continue
      const seed = seedOf(`moment-${mi}-${m.kind}`)
      if (m.kind === 'build' && rowOf(who[0]) === row) {
        const bs = buildSpot(lay, who[0])
        drawFrame(pen, bs.x0, bs.x1, bs.base, bs.h, T, seed, true, bs.side)
        drawFigs(mi, m, row, (fg, hand) => {
          // the hammer: a short handle on from the hand, its head across the end
          const sh: Pt = [fg.at[0], fg.at[1] - fg.size * 0.76]
          const dx = hand[0] - sh[0], dy = hand[1] - sh[1], d = Math.hypot(dx, dy) || 1
          const e: Pt = [hand[0] + (dx / d) * fg.size * 0.2, hand[1] + (dy / d) * fg.size * 0.2]
          const nx = -dy / d, ny = dx / d, hl = fg.size * 0.09
          const hp = pen.plain(`out:hammer${fg.a < 1 ? fg.a : ''}`, INK_A * fg.a, pen.wOut * 1.25)
          appendPath(hp, [hand, e]); appendPath(hp, [[e[0] - nx * hl, e[1] - ny * hl], [e[0] + nx * hl, e[1] + ny * hl]])
        })
      } else if (m.kind === 'win' && rowOf(who[0]) === row) {
        drawWin(pen, trophySpot(lay, who[0]), T, seed)
        drawFigs(mi, m, row)
      } else if (m.kind === 'celebrate') {
        const light = smooth(0.4, 0.9, T) * (1 - smooth(5.2, 5.8, T))
        for (const i of who) {
          if (rowOf(i) !== row || light <= 0) continue
          const s = lay.houses[i]
          const dx = s.door.x + s.door.w / 2
          const L = eighths(light)
          // lantern light pouring out of the open door
          for (const [k, r] of [1.25, 0.85, 0.5].entries()) poly(b.fill(AMBER, 0.2 * L), ellipsePts(dx, s.door.y + s.door.h * 0.5, r * f, r * f * 0.85, 0, TAU, 14 + k))
          poly(b.fill(AMBER, 0.85 * L), [[s.door.x, s.door.y + s.door.h], [s.door.x, s.door.y], [s.door.x + s.door.w, s.door.y], [s.door.x + s.door.w, s.door.y + s.door.h]])
          const wn = s.win
          poly(b.fill(AMBER, 0.75 * L), [[wn.x, wn.y], [wn.x + wn.w, wn.y], [wn.x + wn.w, wn.y + wn.h], [wn.x, wn.y + wn.h]])
          poly(b.fill(AMBER, 0.16 * L), ellipsePts(wn.x + wn.w / 2, wn.y + wn.h / 2, wn.w * 1.1, wn.h * 1.1, 0, TAU, 12))
        }
        const bt = buntingSpan(lay, who)
        if (bt.row === row && T >= 2.4) {
          const q = smooth(2.4, 3.6, T)
          const n = Math.max(4, Math.min(10, Math.round(Math.hypot(bt.b[0] - bt.a[0], bt.b[1] - bt.a[1]) / (0.5 * f))))
          const at = (t: number): Pt => [lerp(bt.a[0], bt.b[0], t), lerp(bt.a[1], bt.b[1], t) + 4 * bt.sag * t * (1 - t)]
          const str: Pts = []
          for (let k = 0; k <= 16; k++) { const t = (k / 16) * q; str.push(at(t)) }
          pen.ink(pen.plain('fine:bunting', INK_A * 0.85, pen.wOut * 0.6), str, seed + 3, false, pen.amp * 0.15, Math.max(1 * u, f * 0.3))
          const fw = Math.hypot(bt.b[0] - bt.a[0], bt.b[1] - bt.a[1]) / n
          for (let k = 0; k < n; k++) {
            const t0 = (k + 0.15) / n, t1 = (k + 0.85) / n
            if (t1 > q) break
            const p0 = at(t0), p1 = at(t1), mid = at((t0 + t1) / 2)
            const tri: Pts = [p0, p1, [mid[0], mid[1] + fw * 0.75]]
            poly(b.fill(CONFETTI[k % CONFETTI.length], 0.85), tri)
            pen.ink(pen.plain('fine:bunting', INK_A * 0.85, pen.wOut * 0.6), tri, seed + 10 + k, true, pen.amp * 0.05, Math.max(0.6 * u, fw * 0.3))
          }
        }
        drawFigs(mi, m, row)
      } else if (m.kind === 'fail') {
        // the sign with its own layer (a back-row house's stands down by the road, drawn with the front row); the
        // resident sits on the step with the house's row
        if (rowOf(who[0]) === row) drawFigs(mi, m, row)
        if (failSign(lay, who[0]).row !== row) continue
        drawSign(pen, lay, who[0], T, seed)
      }
    }
    b.flush(ctx)
    return
  }

  if (layer === 'ground') {
    // the props and the fixtures, then the residents out on the grass, then the tags
    const tally = (m: Moment, T: number) => {
      const who = castOf(lay, houses, m, spots)
      let n = 0
      who.forEach((i, k) => {
        const box = spots.find((p) => p.kind === 'ballot')!
        const spot = voteSpot(lay, box, k)
        const g = groundPath(lay, i, spot)
        const arrive = voteStagger(who.length) * k + walkDur(lay, g.len, 2.2, 3.4)
        if (T >= arrive + 0.6) n++
      })
      return n
    }
    const tags: { spot: StageSpot; maxW: number }[] = []
    // the moments that name nobody play at the stage itself: a build raises the scaffold prop (or a frame at the board's
    // side), a win's trophy rises from behind the board, a fail's sign falls by the board
    const nobody = moments.map((m, mi) => ({ m, mi, T: momentTime(m, c) })).filter((x) => x.T !== null && castOf(lay, houses, x.m, spots).length === 0)
    const raising = nobody.find((x) => x.m.kind === 'build' && spots.some((sp) => sp.kind === 'scaffold'))
    for (const { m, mi, T } of nobody) {
      const seed = seedOf(`moment-${mi}-${m.kind}`)
      if (m.kind === 'build' && !raising) { const bs = boardScaffold(lay); drawFrame(pen, bs.x0, bs.x1, bs.base, bs.h, T!, seed, true, bs.side) }
      else if (m.kind === 'win') drawWin(pen, boardTrophy(lay), T!, seed)
      else if (m.kind === 'fail') drawSign(pen, lay, -1, T!, seed, signShift(lay, houses, moments, spots))
    }
    for (const sp of spots) {
      let votes = 0
      if (sp.fixture) {
        const ms = moments.filter((m) => m.kind === sp.fixture && momentTime(m, c) !== null)
        if (!ms.length) continue
      }
      if (sp.kind === 'ballot') for (const m of moments) { const T = momentTime(m, c); if (m.kind === 'vote' && T !== null) votes += tally(m, T) }
      if (sp.kind === 'scaffold' && raising) {
        // a build that names nobody raises the scaffold prop, plank by plank, a house tall, and flies its flag
        const T = momentTime(raising.m, c)!
        const bs = propScaffold(lay, sp.slot)
        poly(b.fill(INK, 0.07), ellipsePts(sp.slot.x, sp.slot.y + 0.02 * sp.slot.size, 0.46 * sp.slot.size, 0.07 * sp.slot.size))
        drawFrame(pen, bs.x0, bs.x1, bs.base, bs.h, T, seedOf(`moment-${raising.mi}-build`), true, bs.side)
      } else {
        // the lanterns burn brighter while a celebration plays
        let glow = 0
        if (sp.kind === 'lanterns') for (const m of moments) { const T = momentTime(m, c); if (m.kind === 'celebrate' && T !== null) glow = Math.max(glow, eighths(smooth(0.4, 0.9, T) * (1 - smooth(5.2, 5.8, T)))) }
        drawProp(pen, sp.kind, sp.slot, seedOf(`prop-${sp.kind}-${sp.k}`), votes, glow)
      }
      if (sp.label && sp.label.trim()) {
        // a tag hangs below the feet, so only another tag can crowd it
        const others = spots.filter((o) => o !== sp && o.label && o.label.trim()).map((o) => Math.abs(o.k - sp.k))
        const gap = others.length ? Math.min(...others) : 2.4
        tags.push({ spot: sp, maxW: Math.min(2.4, gap) * sp.slot.w * 0.92 })
      }
    }
    for (const [mi, m] of moments.entries()) {
      const T = momentTime(m, c)
      if (T === null) continue
      if (m.kind === 'send') {
        const { letters, ew } = sendLetters(lay, houses, m, spots)
        letters.forEach((l, q) => { if (T >= l.t0 + FLIGHT) envelope(pen, l.land, ew, l.rot, seedOf(`letter-${q}`)) })
      }
      if (m.kind === 'gather' || m.kind === 'vote' || m.kind === 'play') {
        drawFigs(mi, m, 2, (fg, hand) => {
          if (m.kind === 'play' && fg.pose === 'sit') {
            // a stool under the sitter
            const sy = fg.at[1] - fg.size * 0.25, sx = fg.at[0] - fg.facing * fg.size * 0.04
            const sp = pen.plain('out:stool', INK_A, pen.wOut * 0.85)
            appendPath(sp, [[sx - fg.size * 0.14, sy], [sx + fg.size * 0.14, sy]])
            appendPath(sp, [[sx - fg.size * 0.11, sy], [sx - fg.size * 0.13, fg.at[1]]])
            appendPath(sp, [[sx + fg.size * 0.11, sy], [sx + fg.size * 0.13, fg.at[1]]])
          }
          if (m.kind === 'vote' && fg.pose === 'reach' && fg.gesture > 0.05) {
            const box = spots.find((p) => p.kind === 'ballot')
            if (box) {
              const slotPt: Pt = [box.slot.x, box.slot.y - 0.9 * box.slot.size]
              const q = clamp01((fg.gesture - 0.4) / 0.6)
              const p: Pt = [lerp(hand[0], slotPt[0], q * 0.6), lerp(hand[1], slotPt[1], q * 0.6)]
              const sw = Math.max(1.6 * u, 0.22 * fg.size), sh = sw * 0.7
              const slip: Pts = [[p[0] - sw / 2, p[1] - sh / 2], [p[0] + sw / 2, p[1] - sh / 2], [p[0] + sw / 2, p[1] + sh / 2], [p[0] - sw / 2, p[1] + sh / 2]]
              poly(b.fill(LETTER, 0.97), slip)
              appendPath(pen.plain('fine:letter', INK_A * 0.85, pen.wOut * 0.62), [...slip, slip[0]])
            }
          }
        })
      }
    }
    b.flush(ctx)
    // the tags: a paper tag pinned at the prop's foot, the label in the board's serif at 9 px or more, or scribbles
    for (const { spot, maxW } of tags) {
      const S = spot.slot.size
      const px0 = tagPx(S)
      const words = maxW >= 26 * u ? tagWords(ctx, spot.label!.trim(), maxW - px0 * 0.9, px0) : null
      let tw: number, th: number
      if (words) {
        ctx.font = words.font
        const lw = Math.max(...words.lines.map((ln) => ctx.measureText(ln).width))
        tw = lw + words.px * 0.9; th = words.lines.length * words.px * 1.12 + words.px * 0.42
      } else { tw = Math.min(maxW, Math.max(1.1 * S, 14 * u)); th = Math.max(5 * u, 0.4 * S) }
      const tx = Math.max(lay.pad.x, Math.min(lay.w - lay.pad.x - tw, spot.slot.x - tw / 2))
      // hung just under the prop's feet, so the prop stands whole above it; pushed up only where the plate ends
      const ty = Math.min(spot.slot.y + 0.04 * S, lay.h - lay.pad.bottom * 0.6 - th)
      const rect: Pts = [[tx, ty], [tx + tw, ty + 0.15 * u], [tx + tw, ty + th], [tx, ty + th - 0.15 * u]]
      poly(b.fill(PAPER, 0.97), rect)
      const tf = pen.plain('fine:tag', INK_A * 0.85, pen.wOut * 0.62)
      pen.ink(tf, rect, seedOf(`tag-${spot.k}`), true, pen.amp * 0.1, Math.max(1 * u, th * 0.4))
      const pin = b.fill(INK, 0.8), pr = Math.max(0.6 * u, th * 0.06), pc: Pt = [tx + tw / 2, ty + Math.max(0.9 * u, th * 0.1)]
      pin.moveTo(pc[0] + pr, pc[1]); pin.arc(pc[0], pc[1], pr, 0, TAU)
      if (!words) {
        const sc = pen.plain('fine:scrib', INK_A * 0.8, pen.wOut * 0.6)
        for (let l = 0; l < 2; l++) {
          const yy = ty + th * (0.45 + 0.28 * l), len = tw * (l ? 0.5 : 0.78)
          const pts: Pts = []
          for (let j = 0; j <= 7; j++) pts.push([tx + tw * 0.11 + (j / 7) * len, yy + Math.sin(j * 2.3 + l) * Math.min(0.8 * u, th * 0.06)])
          appendPath(sc, pts)
        }
      }
      b.flush(ctx)
      if (words) {
        ctx.save()
        ctx.font = words.font
        ctx.fillStyle = INK
        ctx.globalAlpha = INK_A
        ctx.textBaseline = 'alphabetic'
        const lh = words.px * 1.12
        words.lines.forEach((ln, k) => {
          const lw = ctx.measureText(ln).width
          ctx.fillText(ln, tx + (tw - lw) / 2, ty + words.px * 0.25 + words.px * 0.92 + k * lh)
        })
        ctx.restore()
      }
    }
    return
  }

  // the air: letters in flight, and the confetti falling over a celebration
  for (const [mi, m] of moments.entries()) {
    const T = momentTime(m, c)
    if (T === null) continue
    if (m.kind === 'send') {
      const { letters, ew, eh } = sendLetters(lay, houses, m, spots)
      letters.forEach((l, q) => {
        const tau = (T - l.t0) / FLIGHT
        if (tau <= 0 || tau >= 1) return
        const lift = 0.3 * Math.abs(l.land[0] - l.src[0]) + f
        let cy = Math.min(l.src[1], l.land[1]) - lift
        cy = Math.max(cy, (lay.pad.top + eh * 1.2 - 0.25 * (l.src[1] + l.land[1])) / 0.5)
        const c1: Pt = [(l.src[0] + l.land[0]) / 2, cy]
        const t = smooth(0, 1, tau) * 0.85 + tau * 0.15
        const mt = 1 - t
        const p: Pt = [mt * mt * l.src[0] + 2 * mt * t * c1[0] + t * t * l.land[0], mt * mt * l.src[1] + 2 * mt * t * c1[1] + t * t * l.land[1]]
        const vx = 2 * mt * (c1[0] - l.src[0]) + 2 * t * (l.land[0] - c1[0]), vy = 2 * mt * (c1[1] - l.src[1]) + 2 * t * (l.land[1] - c1[1])
        // the trail: the arc flown so far, dotted, fading toward the house
        const trail: Pts = []
        const t0 = Math.max(0, t - 0.45)
        for (let k = 0; k <= 10; k++) {
          const tt = lerp(t0, t, k / 10), m1 = 1 - tt
          trail.push([m1 * m1 * l.src[0] + 2 * m1 * tt * c1[0] + tt * tt * l.land[0], m1 * m1 * l.src[1] + 2 * m1 * tt * c1[1] + tt * tt * l.land[1]])
        }
        appendPath(pen.plain('fine:trail', INK_A * 0.45, pen.wOut * 0.6, [0.6 * u, 1.6 * u]), trail)
        envelope(pen, p, ew, Math.atan2(vy, vx) * 0.3 + l.rot, seedOf(`letter-${q}`))
      })
    } else if (m.kind === 'celebrate') {
      const who = castOf(lay, houses, m, spots)
      if (!who.length) continue
      const hs = who.map((i) => lay.houses[i])
      const xa = Math.max(lay.pad.x + f, Math.min(...hs.map((s) => s.left)) - 0.3 * lay.houseW)
      const xb = Math.min(lay.w - lay.pad.x - f, Math.max(...hs.map((s) => s.right)) + 0.3 * lay.houseW)
      const yTop = Math.max(lay.pad.top + 2 * u, Math.min(...hs.map((s) => s.top)) - 0.25 * lay.houseW)
      const yBot = Math.max(...hs.map((s) => s.y))
      const r = Math.max(0.9 * u, 0.085 * f)
      for (let j = 0; j < 26; j++) {
        const rnd = rngOf(`confetti-${mi}-${j}`)
        const hs4 = [0, rnd(), rnd(), rnd(), rnd()]
        const h = (k: number) => hs4[k]
        const t0 = 0.8 + h(1) * 3.2, tau = (T - t0) / 1.8
        if (tau <= 0 || tau >= 1) continue
        const x = lerp(xa, xb, h(2)) + Math.sin(tau * 5 + h(3) * 6) * 0.25 * f
        const y = lerp(yTop, yBot, tau)
        const a = eighths(1 - smooth(0.75, 1, tau))
        if (a <= 0) continue
        const d = b.fill(CONFETTI[Math.floor(h(4) * CONFETTI.length)], 0.9 * a)
        d.moveTo(x + r, y); d.arc(x, y, r, 0, TAU)
      }
    }
  }
  b.flush(ctx)
}

function pathLength(pts: Pts): number {
  let L = 0
  for (let i = 0; i < pts.length - 1; i++) L += Math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
  return L
}

interface FigOpts {
  facing: number            // -1 / 1 faces left / right, 0 toward the reader
  phase: number             // the stride's swing (walk only)
  pose: FigPose
  tilt?: number             // a sway about the feet, radians
  breath?: number           // the head and shoulders rise by this much
  gesture?: number          // 0..1 how far the near hand rises while talking; the arms' bounce cheering; the hammer's
                            // swing (0 = the blow, 1 = raised); how far the hand reaches out; how far a sitter bows
}

/**
 * A small figure: a head, a body, two legs, two arms (six strokes). Feet at (x, y), `size` tall. Walking: `facing`
 * -1/1 walks left/right in profile, 0 walks toward or away (legs alternate); `phase` swings the stride. Standing:
 * facing the reader. Talking: turned toward the other, the near hand rising with the talk. Looking out: turned to the
 * far end, a hand at the brow.
 */
function figure(lines: Path2D, fills: Path2D, x: number, y: number, size: number, o: FigOpts, seed: number, amp: number): Pt {
  const r = size * 0.13
  const f = o.facing
  const br = o.breath ?? 0
  if (o.pose === 'sit') return sitter(lines, fills, x, y, size, o, seed, amp)
  const tilt = o.tilt ?? 0
  const cs = Math.cos(tilt), sn = Math.sin(tilt)
  const R = (p: Pt): Pt => (tilt ? [x + (p[0] - x) * cs - (p[1] - y) * sn, y + (p[0] - x) * sn + (p[1] - y) * cs] : p)
  const turned = o.pose === 'talk' || o.pose === 'look'
  const lean = o.pose === 'walk' ? f * size * 0.04 : o.pose === 'talk' ? f * size * 0.025 : 0
  const head = R([x + lean * 1.4 + (turned ? f * r * 0.3 : 0), y - size + r - br])
  const neck: Pt = R([x + lean, y - size + r * 2.1 - br])
  const hip: Pt = R([x, y - size * 0.44])
  const sh: Pt = [lerp(neck[0], hip[0], 0.18), lerp(neck[1], hip[1], 0.18)]
  fills.moveTo(head[0] + r, head[1])
  fills.arc(head[0], head[1], r, 0, Math.PI * 2)
  const a = amp * 0.25
  const st = Math.max(1, size * 0.2)
  const limb = (to: Pt, from: Pt, k: number) => appendPath(lines, wobble([from, to], { amp: a, seed: seed + k, step: st }))
  limb(hip, neck, 0)
  const handY = y - size * 0.47
  if (o.pose === 'stand') {
    limb(R([x - size * 0.11, y]), hip, 1)
    limb(R([x + size * 0.11, y]), hip, 2)
    limb(R([x - size * 0.17, handY]), sh, 3)
    limb(R([x + size * 0.17, handY]), sh, 4)
    return [x + size * 0.17, handY]
  }
  if (o.pose === 'cheer') {
    // feet apart, both arms flung up over the head, bouncing with the cheer
    const g = clamp01(o.gesture ?? 0)
    limb(R([x - size * 0.14, y]), hip, 1)
    limb(R([x + size * 0.14, y]), hip, 2)
    const up = y - size * (0.98 + 0.06 * g)
    limb(R([x - size * (0.2 + 0.04 * g), up]), sh, 3)
    limb(R([x + size * (0.2 + 0.04 * g), up]), sh, 4)
    return [x + size * 0.24, up]
  }
  if (o.pose === 'talk' || o.pose === 'hammer' || o.pose === 'reach') {
    // weight on the back foot, the far arm hanging; the near hand comes up as they speak, swings a hammer, or reaches out
    const g = clamp01(o.gesture ?? 0)
    limb(R([x - f * size * 0.06, y]), hip, 1)
    limb(R([x + f * size * 0.13, y]), hip, 2)
    limb(R([x - f * size * 0.07, handY + size * 0.02]), sh, 3)
    let hand: Pt
    if (o.pose === 'hammer') {
      // the arm swings about the shoulder from the blow (forward, a little down) to raised over the head
      const th = lerp(-0.45, 1.85, g), L = size * 0.32
      hand = [sh[0] + f * Math.cos(th) * L, sh[1] - Math.sin(th) * L]
    } else if (o.pose === 'reach') hand = [sh[0] + f * size * (0.14 + 0.16 * g), sh[1] + size * (0.2 - 0.12 * g)]
    else hand = R([x + f * size * (0.17 + 0.07 * g), lerp(handY, y - size * 0.7, g)])
    limb(hand, sh, 4)
    return hand
  }
  if (o.pose === 'look') {
    // standing at the far end, turned to it, a hand at the brow
    limb(R([x - size * 0.1, y]), hip, 1)
    limb(R([x + size * 0.1, y]), hip, 2)
    limb(R([x - f * size * 0.08, handY]), sh, 3)
    const hand: Pt = [head[0] + f * r * 1.1, head[1] - r * 0.15]
    limb(hand, sh, 4)
    return hand
  }
  const sw = Math.sin(o.phase)
  if (f !== 0) {
    // seen side-on the legs and arms never close onto the body: at the passing pose (sw = 0) they still stand apart by
    // 0.35 of a full stride, so the walker always reads as a head, a body and two legs, never a stick with a ball
    const s = (sw < 0 ? -1 : 1) * (0.35 + 0.65 * Math.abs(sw))
    const stride = size * 0.27
    limb(R([x + s * stride, y]), hip, 1)
    limb(R([x - s * stride, y]), hip, 2)
    limb(R([sh[0] - s * stride * 0.8, handY]), sh, 3)
    limb(R([sh[0] + s * stride * 0.8, handY]), sh, 4)
    return [sh[0] + s * stride * 0.8, handY]
  }
  const lift = size * 0.09
  limb(R([x - size * 0.1, y - Math.max(0, sw) * lift]), hip, 1)
  limb(R([x + size * 0.1, y - Math.max(0, -sw) * lift]), hip, 2)
  limb(R([x - size * 0.18, handY + sw * size * 0.05]), sh, 3)
  limb(R([x + size * 0.18, handY - sw * size * 0.05]), sh, 4)
  return [x + size * 0.18, handY - sw * size * 0.05]
}

/**
 * A seated figure, feet at (x, y). Side-on (facing -1/1): thighs forward, shins down, hands toward the table. Facing the
 * reader (0): knees apart, elbows on the knees. `gesture` bows the head and rounds the back (1 = sitting on the step,
 * head down).
 */
function sitter(lines: Path2D, fills: Path2D, x: number, y: number, size: number, o: FigOpts, seed: number, amp: number): Pt {
  const r = size * 0.13, f = o.facing, bow = clamp01(o.gesture ?? 0), br = o.breath ?? 0
  const a = amp * 0.25, st = Math.max(1, size * 0.2)
  const limb = (pts: Pts, k: number) => appendPath(lines, wobble(pts, { amp: a, seed: seed + k, step: st }))
  const hip: Pt = [x - f * size * 0.04, y - size * 0.27]
  const lean = f !== 0 ? f : 0
  const neck: Pt = [hip[0] + lean * size * (0.05 + 0.08 * bow), hip[1] - size * (0.4 - 0.08 * bow) - br]
  const head: Pt = [neck[0] + lean * r * (0.2 + 0.6 * bow), neck[1] - r * (1.05 - 0.45 * bow)]
  fills.moveTo(head[0] + r, head[1])
  fills.arc(head[0], head[1], r, 0, Math.PI * 2)
  limb([neck, hip], 0)
  const sh: Pt = [lerp(neck[0], hip[0], 0.18), lerp(neck[1], hip[1], 0.18)]
  if (f !== 0) {
    const knee: Pt = [x + f * size * 0.17, y - size * 0.28], foot: Pt = [x + f * size * 0.19, y]
    limb([hip, knee, foot], 1)
    limb([[hip[0] - f * size * 0.02, hip[1]], [knee[0] - f * size * 0.04, knee[1] + size * 0.02], [foot[0] - f * size * 0.07, y]], 2)
    const hand: Pt = [x + f * size * 0.3, y - size * (0.4 - 0.1 * bow)]
    limb([sh, [x + f * size * 0.12, y - size * 0.36], hand], 3)
    limb([sh, [x + f * size * 0.06, y - size * 0.34], [hand[0] - f * size * 0.05, hand[1] + size * 0.03]], 4)
    return hand
  }
  const kl: Pt = [x - size * 0.13, y - size * 0.22], kr: Pt = [x + size * 0.13, y - size * 0.22]
  limb([hip, kl, [x - size * 0.11, y]], 1)
  limb([hip, kr, [x + size * 0.11, y]], 2)
  limb([sh, [kl[0] + size * 0.04, kl[1] - size * 0.03]], 3)
  limb([sh, [kr[0] - size * 0.04, kr[1] - size * 0.03]], 4)
  return kr
}
