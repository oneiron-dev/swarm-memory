import { afterEach, beforeEach, describe, expect, test } from 'bun:test'
import { DOOR, GATE_TURN, INK, MAX_HOUSES, MOMENT_DUR, MOMENT_KINDS, PROP_H, PROP_KINDS, PUFF_EVERY, boardRect, boardScaffold, boardSign, boardTrophy, buildBox, buildFixture, buildSpot, castOf, gatherSpot, drawVillage, failAngle, failSignPts, fitBoard, gateTurn, groundSlots, houseHeight, layoutHouses, letterW, momentFigures, momentTime, pairSpots, puffs, roadAt, stageFigures, stageSpots, tagHeight, visitorSpot, walkerPos, wrapBoard, type HouseScene, type Lab, type Moment, type MomentKind, type Prop, type VillageScene, type WalkKind } from '../src/book/village'
import { hatchSegments, noise1, rngOf, wobble } from '../src/book/pen'

// ---------------------------------------------------------------- a recording canvas (bun has no canvas)
// Path2D and the 2D context are faked just far enough to record every coordinate drawn and every style used.

type Op = { op: 'fill' | 'stroke'; style: string; alpha: number; width: number; dash: string; pts: number[]; subs: number[][] }
class FakePath2D {
  pts: number[] = []
  subs: number[][] = []   // one list per moveTo: a limb, a diamond, a wall
  private add(...xy: number[]) { this.pts.push(...xy); (this.subs[this.subs.length - 1] ?? (this.subs[0] = [])).push(...xy) }
  moveTo(x: number, y: number) { this.subs.push([]); this.add(x, y) }
  lineTo(x: number, y: number) { this.add(x, y) }
  quadraticCurveTo(cx: number, cy: number, x: number, y: number) { this.add(cx, cy, x, y) }
  arc(x: number, y: number, r: number) { this.add(x - r, y - r, x + r, y + r) }
  closePath() {}
}
/** A stand-in for text measure: half the size per character. `measure` replaces it (a face that runs wider when small,
 * a fallback face narrower than the real one). */
type Measure = (text: string, font: string) => number
const pxOf = (font: string) => Number(/(\d+(?:\.\d+)?)px/.exec(font)?.[1]) || 10
function fakeContext(measure: Measure = (t, font) => t.length * 0.5 * pxOf(font)) {
  const ops: Op[] = []
  const calls: string[] = []
  let depth = 0
  const ctx = {
    globalAlpha: 1, fillStyle: '' as unknown, strokeStyle: '' as unknown, lineWidth: 1, lineCap: 'butt', lineJoin: 'miter',
    _dash: [] as number[],
    save() { depth++ },
    restore() { depth-- },
    setLineDash(d: number[]) { this._dash = d },
    createLinearGradient() { return { stops: [] as [number, string][], addColorStop(o: number, c: string) { this.stops.push([o, c]) } } },
    fill(p: FakePath2D) { ops.push({ op: 'fill', style: styleOf(this.fillStyle), alpha: this.globalAlpha, width: 0, dash: '', pts: p.pts.slice(), subs: p.subs.map((q) => q.slice()) }) },
    stroke(p: FakePath2D) { ops.push({ op: 'stroke', style: styleOf(this.strokeStyle), alpha: this.globalAlpha, width: this.lineWidth, dash: this._dash.join(','), pts: p.pts.slice(), subs: p.subs.map((q) => q.slice()) }) },
    font: '', textBaseline: 'alphabetic',
    texts: [] as { text: string; x: number; y: number; font: string }[],
    translate() {}, rotate() {},
    measureText(t: string) { return { width: measure(t, this.font) } },
    fillText(text: string, x: number, y: number) { this.texts.push({ text, x, y, font: this.font }) },
    clearRect() { calls.push('clearRect') },
    fillRect() { calls.push('fillRect') },
    drawImage() { calls.push('drawImage') },
  }
  return { ctx: ctx as unknown as CanvasRenderingContext2D, ops, calls, depth: () => depth, texts: ctx.texts }
}
function styleOf(s: unknown): string {
  if (typeof s === 'string') return s
  const g = s as { stops?: [number, string][] }
  return g.stops ? `gradient(${g.stops.map(([o, c]) => `${o} ${c}`).join(', ')})` : String(s)
}

const g = globalThis as unknown as { Path2D: unknown }
let realPath2D: unknown
beforeEach(() => { realPath2D = g.Path2D; g.Path2D = FakePath2D })
afterEach(() => { g.Path2D = realPath2D })

// ---------------------------------------------------------------- synthetic scenes (no dataset rows)

const LABS: Lab[] = ['anthropic', 'openai', 'google', 'other']
function scene(count: number, over: Partial<VillageScene> = {}, each: (n: number) => Partial<HouseScene> = () => ({})): VillageScene {
  return {
    lens: 'village', visitor: 0.5, t: 0.5, board: null, pairs: [], ...over,
    houses: Array.from({ length: count }, (_, i) => ({
      n: i + 1, present: true, lab: LABS[i % 4], window: `oklch(0.7 0.12 ${(i * 37) % 360})`, smoke: 1,
      arriving: false, leaving: false, ghost: false, mark: null, away: false, idle: false, ...each(i + 1),
    })),
  }
}
const SIZES: [number, number][] = [[440, 280], [360, 200], [880, 560]]

// ---------------------------------------------------------------- layout

describe('layoutHouses', () => {
  test('is deterministic: the same count and size give the same geometry, cache or no cache', () => {
    const a = JSON.stringify(layoutHouses(22, 440, 280))
    for (let k = 0; k < 60; k++) layoutHouses(1 + (k % 40), 300 + k, 200)   // pushes the first entry out of the cache
    const b = JSON.stringify(layoutHouses(22, 440, 280))
    expect(b).toBe(a)
  })

  test('every house stands inside the margins, in x order, for 1 to 40 houses at every size', () => {
    for (const [w, h] of SIZES) {
      for (let count = 1; count <= MAX_HOUSES; count++) {
        const lay = layoutHouses(count, w, h)
        expect(lay.houses.length).toBe(count)
        let prev = -Infinity
        for (const s of lay.houses) {
          expect(s.left).toBeGreaterThanOrEqual(lay.pad.x - 1e-6)
          expect(s.right).toBeLessThanOrEqual(w - lay.pad.x + 1e-6)
          expect(s.top).toBeGreaterThanOrEqual(lay.pad.top)
          expect(s.y).toBeLessThanOrEqual(h - lay.pad.bottom)
          expect(s.x).toBeGreaterThan(prev)
          prev = s.x
          // a door and a window that are really there, inside the walls
          expect(s.door.w).toBeGreaterThan(0.9 * lay.unit)
          expect(s.win.w).toBeGreaterThan(0.9 * lay.unit)
          expect(s.door.x).toBeGreaterThanOrEqual(s.x - s.wallW / 2)
          expect(s.win.x + s.win.w).toBeLessThanOrEqual(s.x + s.wallW / 2 + 1e-6)
        }
        // the gate and the visitor stand left of the first house, the road stays on the plate
        expect(lay.gate.x + lay.gate.w).toBeLessThan(lay.houses[0].left + lay.gate.w)
        expect(lay.visitor[0]).toBeGreaterThan(lay.pad.x)
        for (const p of [...lay.road.back, ...lay.road.front]) expect(p[1]).toBeLessThanOrEqual(h - lay.pad.bottom + 1e-6)
      }
    }
  })

  test('houses in one row never overlap; neighbours in two rows stagger front and back', () => {
    for (const [w, h] of SIZES) {
      for (let count = 1; count <= MAX_HOUSES; count++) {
        const lay = layoutHouses(count, w, h)
        for (const row of [0, 1] as const) {
          const r = lay.houses.filter((s) => s.row === row)
          for (let i = 1; i < r.length; i++) expect(r[i].left).toBeGreaterThan(r[i - 1].right)
        }
        if (lay.rows === 2) lay.houses.forEach((s, i) => expect(s.row).toBe(i % 2 === 1 ? 1 : 0))
      }
    }
  })

  test('four houses are large, forty are small, and forty still fit', () => {
    const four = layoutHouses(4, 440, 280), forty = layoutHouses(40, 440, 280)
    expect(four.rows).toBe(1)
    expect(forty.rows).toBe(2)
    expect(four.houseW).toBeGreaterThan(3.5 * forty.houseW)
    // the notice board takes its zone by the gate; forty houses still read as houses with a door, a window, a chimney
    expect(forty.houseW).toBeGreaterThan(10)
    expect(layoutHouses(40, 360, 200).houseW).toBeGreaterThan(8.5)
  })

  test('clamps the count to 1..40', () => {
    expect(layoutHouses(0, 440, 280).houses.length).toBe(1)
    expect(layoutHouses(55, 440, 280).houses.length).toBe(40)
  })
})

// ---------------------------------------------------------------- the drawing

describe('drawVillage', () => {
  test('the same scene and size draw the same strokes and fills', () => {
    const s = scene(22, { t: 0.4 }, (n) => ({ arriving: n === 8, leaving: n === 15, mark: n === 11 ? 'pin' : n === 17 ? 'swing' : null, present: n !== 6 }))
    const a = fakeContext(), b = fakeContext()
    drawVillage(a.ctx, s, 440, 280)
    drawVillage(b.ctx, structuredClone(s), 440, 280)
    expect(a.ops.length).toBeGreaterThan(5)
    expect(JSON.stringify(b.ops)).toBe(JSON.stringify(a.ops))
  })

  test('nothing is clipped: every point lands on the plate, a margin in from the edge, for 1 to 40 houses', () => {
    for (const [w, h] of SIZES) {
      const m = 0.5 * Math.min(w / 440, h / 280)
      for (let count = 1; count <= MAX_HOUSES; count++) {
        for (const t of [0, 0.5, 1]) {
          const s = scene(count, { t, visitor: 1, lens: t === 1 ? 'humans' : 'village' },
            (n) => ({ mark: n % 2 ? 'pin' : 'swing', arriving: n % 3 === 0, leaving: n % 3 === 1 }))
          const f = fakeContext()
          drawVillage(f.ctx, s, w, h)
          for (const op of f.ops) {
            for (let i = 0; i < op.pts.length; i += 2) {
              const x = op.pts[i], y = op.pts[i + 1]
              if (!(x >= m && x <= w - m && y >= m && y <= h - m)) throw new Error(`${count} houses at ${w}x${h}, t ${t}: (${x.toFixed(1)}, ${y.toFixed(1)}) off the plate in a ${op.op} of ${op.style}`)
            }
          }
        }
      }
    }
  })

  test('paints no background, leaves the context as it found it, never uses Math.random or the clock', () => {
    const rnd = Math.random, now = Date.now, perf = performance.now
    Math.random = () => { throw new Error('Math.random') }
    Date.now = () => { throw new Error('Date.now') }
    performance.now = () => { throw new Error('performance.now') }
    try {
      const f = fakeContext()
      drawVillage(f.ctx, scene(40, { lens: 'goal', visitor: 0 }, (n) => ({ ghost: n === 9 || n === 30, present: n % 7 !== 0 })), 440, 280)
      expect(f.calls).toEqual([])
      expect(f.depth()).toBe(0)
    } finally { Math.random = rnd; Date.now = now; performance.now = perf }
  })

  test('the colours: ink lines at 0.85 at most, no pure black or white, doors in their lab colour', () => {
    const f = fakeContext()
    drawVillage(f.ctx, scene(12, {}, (n) => ({ mark: n === 3 ? 'pin' : null })), 440, 280)
    const banned = /^(#000|#000000|#fff|#ffffff|black|white)$/i
    for (const op of f.ops) {
      expect(banned.test(op.style)).toBe(false)
      if (op.op === 'stroke' && op.style === INK) expect(op.alpha).toBeLessThanOrEqual(0.85 + 1e-9)
    }
    const fills = new Set(f.ops.filter((o) => o.op === 'fill').map((o) => o.style))
    for (const lab of LABS) expect(fills.has(DOOR[lab])).toBe(true)
    expect(fills.has('#b7482a')).toBe(true)
  })

  test('an empty house has no window, no smoke and no door colour', () => {
    const f = fakeContext()
    drawVillage(f.ctx, scene(4, {}, () => ({ present: false, lab: null, window: 'oklch(0.7 0.1 50)', smoke: 1 })), 440, 280)
    const fills = new Set(f.ops.filter((o) => o.op === 'fill').map((o) => o.style))
    expect(fills.has('oklch(0.7 0.1 50)')).toBe(false)
    for (const lab of LABS) expect(fills.has(DOOR[lab])).toBe(false)
    // its outline is dotted
    expect(f.ops.some((o) => o.op === 'stroke' && o.dash !== '')).toBe(true)
  })

  test('ghosts draw at ink alpha 0.25', () => {
    const f = fakeContext()
    drawVillage(f.ctx, scene(4, { visitor: 0 }, () => ({ ghost: true, smoke: 0 })), 440, 280)
    const houseInk = f.ops.filter((o) => o.op === 'stroke' && o.style === INK && o.width > 0 && o.alpha < 0.3)
    expect(houseInk.length).toBeGreaterThan(0)
    expect(Math.max(...houseInk.map((o) => o.alpha))).toBeCloseTo(0.25, 5)
  })

  test('t moves the walkers and nothing else', () => {
    const still = (t: number) => { const f = fakeContext(); drawVillage(f.ctx, scene(22, { t }), 440, 280); return JSON.stringify(f.ops) }
    expect(still(0.1)).toBe(still(0.9))
    const walking = (t: number) => { const f = fakeContext(); drawVillage(f.ctx, scene(22, { t }, (n) => ({ arriving: n === 5 })), 440, 280); return JSON.stringify(f.ops) }
    expect(walking(0.1)).not.toBe(walking(0.9))
  })

  test('smoke: none at 0, more ink as the chat volume rises', () => {
    const smokeInk = (v: number) => {
      const f = fakeContext()
      drawVillage(f.ctx, scene(1, { visitor: 0 }, () => ({ smoke: v })), 440, 280)
      return f.ops.filter((o) => o.op === 'stroke').reduce((n, o) => n + o.pts.length, 0)
    }
    expect(smokeInk(0)).toBeLessThan(smokeInk(0.2))
    expect(smokeInk(0.2)).toBeLessThan(smokeInk(0.5))
    expect(smokeInk(0.5)).toBeLessThan(smokeInk(1))
  })

  test('a walker never closes its legs: its two feet stand apart at every moment of the day', () => {
    for (const [w, h] of SIZES) {
      for (const count of [4, 9, 22, 40]) {
        const lay = layoutHouses(count, w, h)
        for (let k = 0; k <= 20; k++) {
          const t = k / 20
          for (const arriving of [true, false]) {
            const f = fakeContext()
            drawVillage(f.ctx, scene(count, { t, visitor: 0 }, () => ({ arriving, leaving: !arriving, smoke: 0 })), w, h)
            // with no visitor, pairs or marks the walkers are one stroke: per figure a body, two legs, two arms
            const fig = f.ops.filter((o) => o.op === 'stroke' && o.subs.length === count * 5 && o.dash === '').pop()!
            expect(fig.op).toBe('stroke')
            expect(fig.subs.length).toBe(count * 5)
            for (let j = 0; j < count; j++) {
              const a = fig.subs[j * 5 + 1], b = fig.subs[j * 5 + 2]
              const gap = Math.abs(a[a.length - 2] - b[b.length - 2])
              if (gap < 0.15 * lay.figure) throw new Error(`${count} houses at ${w}x${h}, t ${t}, house ${j + 1} ${arriving ? 'arriving' : 'leaving'}: feet ${gap.toFixed(2)} apart, under 0.15 x ${lay.figure.toFixed(1)}`)
            }
          }
        }
      }
    }
  })

  test('a front-row pin never sits on a back-row house: it clears their walls, roofs and chimneys', () => {
    const outside = (p: [number, number], poly: [number, number][]) => {
      // the distance from p to a convex polygon, negative when inside
      let inside = true, d = Infinity
      for (let k = 0; k < poly.length; k++) {
        const a = poly[k], b = poly[(k + 1) % poly.length]
        const ex = b[0] - a[0], ey = b[1] - a[1], L2 = ex * ex + ey * ey
        const f = Math.max(0, Math.min(1, ((p[0] - a[0]) * ex + (p[1] - a[1]) * ey) / L2))
        d = Math.min(d, Math.hypot(p[0] - a[0] - f * ex, p[1] - a[1] - f * ey))
        if (ex * (p[1] - a[1]) - ey * (p[0] - a[0]) < 0) inside = false
      }
      // the polygons below wind clockwise on screen (y down), so every cross product is >= 0 inside
      return inside ? -d : d
    }
    const box = (pts: [number, number][]): [number, number][] => {
      const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1])
      const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys)
      return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
    }
    let checked = 0
    for (const [w, h] of [...SIZES, [600, 300], [320, 180], [520, 240]] as [number, number][]) {
      for (let count = 3; count <= MAX_HOUSES; count++) {
        const lay = layoutHouses(count, w, h)
        if (lay.rows !== 2) continue
        const f = fakeContext()
        drawVillage(f.ctx, scene(count, { visitor: 0 }, () => ({ mark: 'pin', smoke: 0 })), w, h)
        const pins = f.ops.find((o) => o.op === 'fill' && o.style === '#b7482a')!
        expect(pins.subs.length).toBe(count)   // one diamond per house, in house order
        const back = lay.houses.filter((s) => s.row === 1)
        lay.houses.forEach((s, i) => {
          if (s.row !== 0) return
          const d = pins.subs[i]
          const T: [number, number] = [d[0], d[1]], R: [number, number] = [d[2], d[3]], L: [number, number] = [d[6], d[7]]
          for (const b of back) {
            const walls: [number, number][] = [[b.x - b.wallW / 2, b.y - b.wallH], [b.x + b.wallW / 2, b.y - b.wallH], [b.x + b.wallW / 2, b.y], [b.x - b.wallW / 2, b.y]]
            const roof: [number, number][] = [b.apex, b.eaveR, b.eaveL]
            const parts = [['wall', walls], ['roof', roof], ['chimney', box(b.chimney)]] as const
            // every point of the diamond, its outline and inside, stands half a plate unit clear of each part
            for (let a = 0; a <= 10; a++) {
              for (let c = 0; c <= 10; c++) {
                const p: [number, number] = [T[0] + (a / 10) * (R[0] - T[0]) + (c / 10) * (L[0] - T[0]), T[1] + (a / 10) * (R[1] - T[1]) + (c / 10) * (L[1] - T[1])]
                for (const [name, poly] of parts) {
                  const dist = outside(p, poly as [number, number][])
                  if (dist < 0.5 * lay.unit) throw new Error(`${count} houses at ${w}x${h}: the pin of house ${s.n} is ${dist.toFixed(2)} from the ${name} of back house ${b.n}`)
                }
              }
            }
          }
          checked++
        })
      }
    }
    expect(checked).toBeGreaterThan(1500)
  })

  test('the visitor appears only when humans wrote, larger under the humans lens', () => {
    const extent = (visitor: number, lens: VillageScene['lens']) => {
      const f = fakeContext()
      drawVillage(f.ctx, scene(4, { visitor, lens }, () => ({ smoke: 0 })), 440, 280)
      // the visitor's ink is the only stroke at alpha 0.35 + 0.55 x visitor
      const fig = f.ops.filter((o) => o.op === 'stroke' && Math.abs(o.alpha - (0.35 + 0.55 * visitor)) < 1e-9)
      const ys = fig.flatMap((o) => o.pts.filter((_, i) => i % 2 === 1))
      return ys.length ? Math.max(...ys) - Math.min(...ys) : 0
    }
    expect(extent(0, 'village')).toBe(0)
    expect(extent(0.6, 'humans')).toBeGreaterThan(extent(0.6, 'village') * 1.3)
  })
})


// ---------------------------------------------------------------- life: the clock

const GOAL = 'Write a field guide to the birds that visit the valley'
// real weekly goals from the village's year, 50 to 58 characters
const GOALS = [GOAL, "Holiday: do whatever you'd like! Next goal will begin soon", 'Choose a charity and raise as much money as you can for it', 'Develop a turn-based RPG together while voting out Easter…']
/** A lived day: a goal on the board, three pairs (one across the gate), two away, one idle, one walking in, one out. */
function lived(count: number, over: Partial<VillageScene> = {}): VillageScene {
  const n = (k: number) => Math.max(1, Math.min(count, k))
  return scene(count, {
    board: GOAL, visitor: 0.6,
    pairs: count >= 4 ? [{ a: 0, b: n(2), w: 0.7 }, { a: n(1), b: n(4), w: 1 }, { a: n(3), b: count, w: 0.3 }] : [],
    ...over,
  }, (k) => ({ away: k === n(3) || k === count - 1, idle: k === n(2), arriving: k === n(4) && count > 4, leaving: k === count - 2, smoke: (k % 5) / 4 }))
}
const CLOCKS = Array.from({ length: 49 }, (_, k) => k * 0.25)   // 0 to 12 s

describe('life by the clock', () => {
  test('the geometry helpers are pure: the same layout, scene and clock give the same answer', () => {
    for (const [w, h] of SIZES) {
      const lay = layoutHouses(22, w, h)
      for (const c of [0, 1.3, 4.99, 7.25, 11.75]) {
        for (const kind of ['arriving', 'leaving', 'away', 'visit'] as WalkKind[]) {
          expect(JSON.stringify(walkerPos(lay, 7, kind, c, 1))).toBe(JSON.stringify(walkerPos(layoutHouses(22, w, h), 7, kind, c, 1)))
        }
        expect(JSON.stringify(puffs(lay, 5, 0.8, c))).toBe(JSON.stringify(puffs(lay, 5, 0.8, c)))
      }
      expect(JSON.stringify(boardRect(lay))).toBe(JSON.stringify(boardRect(layoutHouses(22, w, h))))
      const sc = lived(22)
      expect(JSON.stringify(pairSpots(lay, sc))).toBe(JSON.stringify(pairSpots(lay, structuredClone(sc))))
    }
  })

  test('the same scene, size and clock draw the same strokes, fills and words; the clock moves the life', () => {
    const sc = lived(12)
    const at = (c: number | undefined) => { const f = fakeContext(); drawVillage(f.ctx, sc, 440, 280, c); return JSON.stringify([f.ops, f.texts]) }
    expect(at(3.7)).toBe(at(3.7))
    expect(at(3.7)).not.toBe(at(5.2))
    // no clock draws the first moment's life, with the walkers where t puts them
    expect(at(undefined)).toBe(at(undefined))
  })

  test('walkers keep their timetable: in by 5 s, out from 1.5 s to 6 s, away down the road, then still', () => {
    const lay = layoutHouses(10, 440, 280)
    const s = lay.houses[4]
    expect(walkerPos(lay, 4, 'arriving', 0).at).toEqual(s.path[0])
    expect(walkerPos(lay, 4, 'arriving', 5).at).toEqual(s.path[s.path.length - 1])
    expect(walkerPos(lay, 4, 'arriving', 9).pose).toBe('stand')
    expect(walkerPos(lay, 4, 'leaving', 1).at).toEqual(s.path[s.path.length - 1])
    expect(walkerPos(lay, 4, 'leaving', 1).pose).toBe('stand')
    expect(walkerPos(lay, 4, 'leaving', 3).pose).toBe('walk')
    expect(walkerPos(lay, 4, 'leaving', 6).at).toEqual(s.path[0])
    const away = walkerPos(lay, 4, 'away', 12)
    expect(away.pose).toBe('look')
    expect(away.facing).toBe(1)
    expect(away.at[0]).toBeGreaterThan(s.x)
    // the farthest spot goes to rank 0, the next a step nearer
    expect(walkerPos(lay, 4, 'away', 12, 1).at[0]).toBeLessThan(away.at[0])
    // a breath is all that moves once they stand
    expect(walkerPos(lay, 4, 'away', 10).at).toEqual(walkerPos(lay, 4, 'away', 12).at)
  })

  test('a walker never closes its legs: its two feet stand apart at every clock from 0 to 12 s', () => {
    for (const [w, h] of SIZES) {
      for (const count of [4, 9, 22, 40]) {
        const lay = layoutHouses(count, w, h)
        for (const c of CLOCKS) {
          for (const k of [0, 1, 2, 3]) {
            const f = fakeContext()
            const s = scene(count, { visitor: 0 }, () => ({ arriving: k === 0 || k === 3, leaving: k === 1 || k === 3, away: k === 2, smoke: 0 }))
            drawVillage(f.ctx, s, w, h, c)
            const fig = f.ops.filter((o) => o.op === 'stroke' && o.subs.length === count * 5 && o.dash === '').pop()!
            expect(fig).toBeDefined()
            for (let j = 0; j < count; j++) {
              const a = fig.subs[j * 5 + 1], b = fig.subs[j * 5 + 2]
              const gap = Math.abs(a[a.length - 2] - b[b.length - 2])
              if (gap < 0.15 * lay.figure) throw new Error(`${count} houses at ${w}x${h}, clock ${c}, house ${j + 1} kind ${k}: feet ${gap.toFixed(2)} apart`)
            }
          }
        }
      }
    }
  })

  test('smoke: a new puff every 2.4 s, as many in flight as the volume says, rising and fading', () => {
    const lay = layoutHouses(4, 440, 280)
    const count = (v: number, c: number) => puffs(lay, 0, v, c).filter((p) => p.a > 0.05).length
    for (const c of [0.3, 1.9, 4.4, 8.8]) {
      expect(count(0, c)).toBe(0)
      expect(count(0.2, c)).toBeGreaterThanOrEqual(1)
      expect(count(0.2, c)).toBeLessThanOrEqual(2)
      expect(count(1, c)).toBeGreaterThanOrEqual(3)
      expect(count(1, c)).toBeLessThanOrEqual(4)
    }
    // the same puff, a second later: higher, larger
    const a = puffs(lay, 0, 1, 3), b = puffs(lay, 0, 1, 4)
    const pa = a.find((p) => b.some((q) => q.id === p.id))!, pb = b.find((q) => q.id === pa.id)!
    expect(pb.y).toBeLessThan(pa.y)
    expect(pb.r).toBeGreaterThan(pa.r)
    // the column keeps the puffs' rhythm: a puff later, the same number at the same stages of their rise
    const stages = (c: number) => puffs(lay, 0, 1, c).map((p) => p.a.toFixed(9))
    expect(stages(2 + PUFF_EVERY)).toEqual(stages(2))
  })

  test('the board: its words wrap to at most 3 lines, never under 9 px, an ellipsis when they run over', () => {
    const measure = (px: number) => (s: string) => s.length * 0.5 * px
    for (const width of [40, 60, 90, 140]) {
      const r = wrapBoard(GOAL, width, measure(9))
      expect(r.lines.length).toBeLessThanOrEqual(3)
      for (const l of r.lines) expect(measure(9)(l)).toBeLessThanOrEqual(width + 1e-9)
      if (!r.fits) expect(r.lines[r.lines.length - 1].endsWith('…')).toBe(true)
    }
    expect(wrapBoard('one two', 200, measure(9))).toEqual({ lines: ['one two'], fits: true })
    const fit = fitBoard(GOAL, 90, 40, (s, px) => s.length * 0.5 * px)
    expect(fit.px).toBeGreaterThanOrEqual(9)
    expect(fit.lines.length).toBeLessThanOrEqual(3)
    // drawn: three lines at most, at 9 px or more, inside the sheet; a narrow board scribbles instead
    const f = fakeContext()
    drawVillage(f.ctx, lived(12), 440, 280, 2)
    expect(f.texts.length).toBeGreaterThan(0)
    expect(f.texts.length).toBeLessThanOrEqual(3)
    for (const t of f.texts) expect(Number(/(\d+(?:\.\d+)?)px/.exec(t.font)![1])).toBeGreaterThanOrEqual(9)
    const small = fakeContext()
    drawVillage(small.ctx, lived(12), 260, 160, 2)
    expect(boardRect(layoutHouses(12, 260, 160)).panel.w).toBeLessThan(64)
    expect(small.texts.length).toBe(0)
  })

  test('the board measures its words at the size it draws them: a face that runs wider when small still fits the sheet', () => {
    // an optical-size face (the book's Source Serif 4): under 16 px a line runs up to 22% wider than its 100 px width
    // scaled down, so a fit from one big measure spills past the sheet
    const opsz: Measure = (t, font) => t.length * 0.5 * pxOf(font) * (1 + 0.22 * Math.max(0, Math.min(1, (16 - pxOf(font)) / 7)))
    let drawn = 0
    for (const [w, h] of [[440, 280], [360, 200], [354, 250], [650, 370]] as [number, number][]) {
      for (const count of [4, 12, 40]) {
        const B = boardRect(layoutHouses(count, w, h))
        if (B.panel.w < 64) continue
        const box = B.sheet.w * 0.84   // the sheet less its side margins
        for (const goal of GOALS) {
          const f = fakeContext(opsz)
          drawVillage(f.ctx, lived(count, { board: goal }), w, h, 2)
          expect(f.texts.length).toBeGreaterThan(0)
          expect(f.texts.length).toBeLessThanOrEqual(3)
          for (const t of f.texts) {
            expect(pxOf(t.font)).toBeGreaterThanOrEqual(9)
            if (t.x + opsz(t.text, t.font) > box + 1e-6) throw new Error(`${count} houses at ${w}x${h}: "${t.text}" at ${pxOf(t.font).toFixed(2)} px is ${(t.x + opsz(t.text, t.font)).toFixed(1)} wide on a ${box.toFixed(1)} px line`)
          }
          drawn++
        }
      }
    }
    expect(drawn).toBeGreaterThan(30)
  })

  test("the board refits when the book's face arrives: a first frame drawn in the fallback face is not kept", () => {
    const G = globalThis as unknown as { document?: unknown; getComputedStyle?: unknown }
    const saved = { document: G.document, getComputedStyle: G.getComputedStyle }
    let loaded = false
    G.document = { documentElement: {}, fonts: { check: () => loaded, load: () => Promise.resolve([]) } }
    G.getComputedStyle = () => ({ getPropertyValue: (k: string) => (k === '--serif' ? '"Source Serif 4", Georgia, serif' : '') })
    try {
      // until the italic face loads the canvas draws in Georgia, which runs narrower than Source Serif 4
      const face: Measure = (t, font) => t.length * 0.5 * pxOf(font) * (loaded && font.includes('Source Serif 4') ? 1.24 : 1)
      const box = boardRect(layoutHouses(12, 440, 280)).sheet.w * 0.84
      const sc = lived(12, { board: GOALS[1] })
      const before = fakeContext(face)
      drawVillage(before.ctx, sc, 440, 280, 1)
      expect(before.texts[0].font).toContain('Source Serif 4')
      loaded = true
      const after = fakeContext(face)
      drawVillage(after.ctx, sc, 440, 280, 1)
      expect(JSON.stringify(after.texts)).not.toBe(JSON.stringify(before.texts))
      for (const t of after.texts) expect(t.x + face(t.text, t.font)).toBeLessThanOrEqual(box + 1e-6)
    } finally {
      G.document = saved.document
      G.getComputedStyle = saved.getComputedStyle
    }
  })

  test('residents who talk with the humans take turns at the gate: one at a time, never behind the board', () => {
    // the opening chapter: three residents talk with the humans on each of the first eight days
    const pairs = [{ a: 0, b: 2, w: 0.4 }, { a: 3, b: 0, w: 1 }, { a: 0, b: 1, w: 0.7 }]
    for (const [w, h] of [[440, 280], [360, 200]] as [number, number][]) {
      for (const count of [4, 12, 22, 40]) {
        const lay = layoutHouses(count, w, h)
        const P = boardRect(lay).panel
        const sc = scene(count, { board: GOAL, visitor: 0.6, pairs }, () => ({ smoke: 0 }))
        const spots = pairSpots(lay, sc)
        expect(spots.length).toBe(3)
        // the strongest talks first, then the others by weight; they share one spot and one bubble
        expect(spots.map((p) => [p.b, p.turn, p.turns])).toEqual([[3, 0, 3], [1, 1, 3], [2, 2, 3]])
        for (const p of spots) {
          expect(p.gate).toBe(true)
          for (const at of [p.left, p.right]) expect(at[0] < P.x || at[0] > P.x + P.w).toBe(true)
          expect(p.right[0] + p.size * 0.3).toBeLessThanOrEqual(P.x)
          expect(p.right).toEqual(spots[0].right)
          // one bubble over the gate: the same place, its size by how much they talk
          expect(p.bubble.x).toBe(spots[0].bubble.x)
          expect(p.bubble.y).toBeLessThan(lay.gate.y - lay.gate.h)
        }
        // drawn, mid-turn: nothing in full ink stands where the board covers it (the road and ground run behind it in a
        // lighter ink; the bubbles come after the board)
        for (const c of [0, 2, 7.5, 12.5, 18]) {
          const f = fakeContext()
          drawVillage(f.ctx, sc, w, h, c)
          const board = f.ops.findIndex((o) => o.op === 'fill' && o.style === '#8a6a45')
          expect(board).toBeGreaterThan(0)
          for (const op of f.ops.slice(0, board)) {
            if (op.alpha < 0.8 || op.style.startsWith('gradient')) continue
            for (let i = 0; i < op.pts.length; i += 2) {
              const x = op.pts[i], y = op.pts[i + 1]
              if (x > P.x + 0.5 && x < P.x + P.w - 0.5 && y > P.y && y < P.y + P.h) throw new Error(`${count} houses at ${w}x${h}, clock ${c}: a ${op.op} of ${op.style} at (${x.toFixed(1)}, ${y.toFixed(1)}) behind the board`)
            }
          }
        }
      }
    }
    // the turns: the strongest at the gate from the first moment; each holds it for one exchange, fading out at its end
    // while the next fades in; with one resident at the gate it never leaves
    expect(gateTurn(0, 3)).toEqual({ k: 0, local: 0, a: 1 })
    expect(gateTurn(2, 3).a).toBe(1)
    expect(gateTurn(GATE_TURN + 2.6, 3).k).toBe(1)
    expect(gateTurn(2 * GATE_TURN + 2.6, 3).k).toBe(2)
    expect(gateTurn(3 * GATE_TURN + 2.6, 3).k).toBe(0)
    expect(gateTurn(GATE_TURN - 0.02, 3).a).toBeLessThan(0.05)
    expect(gateTurn(GATE_TURN + 0.02, 3).a).toBeLessThan(0.05)
    expect(gateTurn(13, 1)).toEqual({ k: 0, local: 13, a: 1 })
    for (let c = 0; c <= 40; c += 0.1) {
      const t = gateTurn(c, 3)
      expect(t.a).toBeGreaterThanOrEqual(0)
      expect(t.a).toBeLessThanOrEqual(1)
      expect(JSON.stringify(gateTurn(c, 3))).toBe(JSON.stringify(t))
    }
  })

  test('pairs: two figures inside the canvas, on the road, apart, a bubble between their heads', () => {
    for (const [w, h] of [[440, 280], [360, 200]] as [number, number][]) {
      for (const count of [4, 10, 22, 40]) {
        const lay = layoutHouses(count, w, h)
        const sc = lived(count)
        const spots = pairSpots(lay, sc)
        expect(spots.length).toBe(3)
        for (const p of spots) {
          for (const at of [p.left, p.right]) {
            expect(at[0] - p.size * 0.3).toBeGreaterThanOrEqual(0)
            expect(at[0] + p.size * 0.3).toBeLessThanOrEqual(w)
            expect(at[1] - p.size).toBeGreaterThanOrEqual(0)
            expect(at[1]).toBeLessThanOrEqual(h - lay.pad.bottom)
          }
          expect(p.right[0]).toBeGreaterThan(p.left[0])
          const b = p.bubble
          expect(b.x - b.rx * 1.06).toBeGreaterThanOrEqual(0)
          expect(b.x + b.rx * 1.06).toBeLessThanOrEqual(w)
          expect(b.y - b.ry * 1.06).toBeGreaterThanOrEqual(0)
          expect(b.y).toBeLessThan(Math.min(p.left[1], p.right[1]) - p.size * 0.8)
          if (!p.gate) {
            // in front of the board, never behind it, and on the road
            expect(p.left[0]).toBeGreaterThan(lay.board.panel.x + lay.board.panel.w)
            expect(p.left[1]).toBeGreaterThan(lay.houses[0].y)
          }
        }
        // two pairs between houses never stand on each other
        const road = spots.filter((p) => !p.gate)
        for (let i = 0; i < road.length; i++) for (let j = i + 1; j < road.length; j++) {
          const A = road[i], B = road[j]
          const o = Math.min(A.right[0], B.right[0]) - Math.max(A.left[0], B.left[0])
          expect(o).toBeLessThan(0)
        }
      }
    }
  })

  test('nothing is clipped at any clock, with the board, pairs, walkers, idle and away houses', () => {
    for (const [w, h] of SIZES) {
      const m = 0.5 * Math.min(w / 440, h / 280)
      for (const count of [1, 4, 10, 22, 40]) {
        for (const c of [0, 1.7, 4.9, 8.3, 11.6]) {
          for (const lens of ['village', 'humans'] as const) {
            const f = fakeContext()
            drawVillage(f.ctx, lived(count, { lens }), w, h, c)
            for (const op of f.ops) {
              for (let i = 0; i < op.pts.length; i += 2) {
                const x = op.pts[i], y = op.pts[i + 1]
                if (!(x >= m && x <= w - m && y >= m && y <= h - m)) throw new Error(`${count} houses at ${w}x${h}, clock ${c}, ${lens}: (${x.toFixed(1)}, ${y.toFixed(1)}) off the plate in a ${op.op} of ${op.style}`)
              }
            }
          }
        }
      }
    }
  })

  test('an idle house keeps no lamp and no smoke; its shutter is two strokes across the window', () => {
    const lit = 'oklch(0.7 0.1 50)'
    const draw = (idle: boolean) => { const f = fakeContext(); drawVillage(f.ctx, scene(1, { visitor: 0 }, () => ({ idle, window: lit, smoke: 1 })), 440, 280, 3); return f }
    const on = draw(false), off = draw(true)
    expect(on.ops.some((o) => o.op === 'fill' && o.style === lit)).toBe(true)
    expect(off.ops.some((o) => o.op === 'fill' && o.style === lit)).toBe(false)
    // the shutter: a stroke from the window's top left past its bottom right corner, and one the other way
    const win = layoutHouses(1, 440, 280).houses[0].win
    const crosses = (f: ReturnType<typeof fakeContext>) => f.ops.filter((o) => o.op === 'stroke').flatMap((o) => o.subs).filter((q) => {
      const x0 = q[0], y0 = q[1], x1 = q[q.length - 2], y1 = q[q.length - 1]
      return q.length <= 40 && x0 < win.x && x1 > win.x + win.w && Math.abs(y1 - y0) > win.h * 0.9
    }).length
    expect(crosses(off)).toBe(2)
    expect(crosses(on)).toBe(0)
    // no smoke: the puffs' paper washes are gone
    const washes = (f: ReturnType<typeof fakeContext>) => f.ops.filter((o) => o.op === 'fill' && o.style === '#f3ece0').length
    expect(washes(off)).toBeLessThan(washes(on))
  })

  test('the visitor sways: the same clock a period apart draws the same figure', () => {
    const vis = (c: number) => { const f = fakeContext(); drawVillage(f.ctx, scene(4, { visitor: 1 }, () => ({ smoke: 0 })), 440, 280, c); return JSON.stringify(f.ops.filter((o) => Math.abs(o.alpha - 0.9) < 1e-9)) }
    expect(vis(0.75)).not.toBe(vis(1.5))
    expect(vis(0.75)).toBe(vis(3.75))
  })
})

// ---------------------------------------------------------------- the stage: props per chapter, moments per beat

const LABEL: Record<string, string> = { stall: 'Bake sale', mailbox: 'Newsletter', ballot: 'Leader vote', scaffold: 'New hub', gallery: 'Museum', chessboard: 'Tournament', stage: 'Quiz night', press: 'Daily paper', garden: 'Park cleanup', lanterns: 'Holiday week', cloud: 'Outages', signpost: 'New plan' }
const propsOf = (kinds: string[]): Prop[] => kinds.map((k) => ({ kind: k as Prop['kind'], label: LABEL[k] }))
/** Which houses a moment names in a village of `count`: spread along the row, some in the back row. */
const named = (count: number, k: number, how = 3) => Array.from({ length: how }, (_, j) => 1 + ((k * 5 + j * 7) % count)).filter((n, j, a) => a.indexOf(n) === j)
const momentOf = (kind: MomentKind, count: number, k: number, start = 0): Moment => ({ kind, houses: named(count, k, kind === 'play' ? 2 : 4), label: null, start })
/** A quiet staged village: no smoke, no visitor, no walkers, no pairs, a bare board, so only the stage moves. */
function staged(count: number, props: Prop[], moments: Moment[], over: Partial<VillageScene> = {}): VillageScene {
  return scene(count, { visitor: 0, board: null, props, moments, ...over }, () => ({ smoke: 0 }))
}
const STAGE_COUNTS = [4, 10, 22, 40]
const STAGE_SIZES: [number, number][] = [[440, 280], [360, 200]]
const HALF_CLOCKS = Array.from({ length: 25 }, (_, k) => k * 0.5)   // 0 to 12 s by 0.5
const rounded = (ops: unknown) => JSON.stringify(ops, (_, v) => (typeof v === 'number' ? Math.round(v * 1e4) / 1e4 : v))

describe('the stage', () => {
  test('ground slots stand inside the plate, right of the board, in the grass in front of the road, clear of every footpath', () => {
    for (const [w, h] of [...STAGE_SIZES, [880, 560] as [number, number]]) {
      for (const count of STAGE_COUNTS) {
        const lay = layoutHouses(count, w, h)
        const slots = groundSlots(lay)
        expect(slots.length).toBe(5)
        const boardR = lay.board.panel.x + lay.board.panel.w
        for (const [k, sl] of slots.entries()) {
          // the widest prop is 1.45 sizes across, the tallest one size tall
          const x0 = sl.x - 0.725 * sl.size, x1 = sl.x + 0.725 * sl.size, y0 = sl.y - sl.size, y1 = sl.y + 0.1 * sl.size
          expect(x0).toBeGreaterThan(boardR)
          expect(x1).toBeLessThanOrEqual(w - lay.pad.x + 1e-6)
          expect(y1).toBeLessThanOrEqual(h - lay.pad.bottom * 0.5)
          if (k) expect(x0).toBeGreaterThanOrEqual(slots[k - 1].x + 0.725 * slots[k - 1].size - 1e-6)
          // never up on the road's walking lane: the top stays below its near lane at every x the prop spans
          for (let j = 0; j <= 8; j++) { const x = lerpN(x0, x1, j / 8); expect(y0).toBeGreaterThan(roadAt(lay, x, 0.75)) }
          // no footpath enters the prop's box
          for (const s of lay.houses) {
            for (let q = 0; q < s.path.length - 1; q++) {
              for (let j = 0; j <= 10; j++) {
                const px = lerpN(s.path[q][0], s.path[q + 1][0], j / 10), py = lerpN(s.path[q][1], s.path[q + 1][1], j / 10)
                expect(px >= x0 && px <= x1 && py >= y0 && py <= y1).toBe(false)
              }
            }
          }
          // about a figure and a half tall
          expect(sl.size).toBeLessThanOrEqual(1.9 * lay.figure + 1e-9)
        }
      }
    }
  })

  test('props take spaced slots; a vote or a game with no prop of its own brings a fixture into a free slot', () => {
    const lay = layoutHouses(12, 440, 280)
    expect(stageSpots(lay, propsOf(['stall']), []).map((s) => s.k)).toEqual([2])
    expect(stageSpots(lay, propsOf(['stall', 'mailbox']), []).map((s) => s.k)).toEqual([1, 3])
    expect(stageSpots(lay, propsOf(['stall', 'mailbox', 'lanterns', 'cloud']), []).map((s) => s.k)).toEqual([0, 2, 4])
    const withVote = stageSpots(lay, propsOf(['stall', 'mailbox']), [momentOf('vote', 12, 1), momentOf('play', 12, 2)])
    expect(withVote.map((s) => s.kind)).toEqual(['stall', 'mailbox', 'ballot', 'chessboard'])
    expect(new Set(withVote.map((s) => s.k)).size).toBe(4)
    expect(stageSpots(lay, propsOf(['ballot']), [momentOf('vote', 12, 1)]).length).toBe(1)
  })

  test('every prop kind draws, in ink and washes only; a label hangs as a tag at 9 px or more, whole words only', () => {
    const base = fakeContext()
    drawVillage(base.ctx, staged(12, [], []), 440, 280, 0)
    for (let k = 0; k < PROP_KINDS.length; k += 3) {
      const kinds = PROP_KINDS.slice(k, k + 3)
      const f = fakeContext()
      drawVillage(f.ctx, staged(12, propsOf(kinds), []), 440, 280, 0)
      expect(f.ops.length).toBeGreaterThan(base.ops.length)
      const banned = /^(#000|#000000|#fff|#ffffff|black|white)$/i
      for (const op of f.ops) expect(banned.test(op.style)).toBe(false)
      const words = f.texts.map((t) => t.text).join(' ')
      for (const kd of kinds) for (const wd of LABEL[kd].split(' ')) expect(words.split(/\s+/)).toContain(wd)
      for (const t of f.texts) expect(pxOf(t.font)).toBeGreaterThanOrEqual(9)
    }
    // one prop alone draws more than none, for every kind
    for (const kd of PROP_KINDS) {
      const f = fakeContext()
      drawVillage(f.ctx, staged(12, [{ kind: kd, label: null }], []), 440, 280, 0)
      expect(f.ops.reduce((n, o) => n + o.pts.length, 0)).toBeGreaterThan(base.ops.reduce((n, o) => n + o.pts.length, 0))
      expect(f.texts.length).toBe(0)
    }
  })

  test("each moment's figures stand inside the plate at every clock from 0 to 12 s by 0.5", () => {
    for (const [w, h] of STAGE_SIZES) {
      for (const count of STAGE_COUNTS) {
        const lay = layoutHouses(count, w, h)
        const m0 = 0.5 * lay.unit
        for (const [k, kind] of MOMENT_KINDS.entries()) {
          const m = momentOf(kind, count, k)
          const sc = staged(count, [], [m])
          let seen = 0
          for (const c of HALF_CLOCKS) {
            for (const fg of momentFigures(lay, sc, m, c)) {
              seen++
              const [x, y] = fg.at
              if (!(x - 0.3 * fg.size >= m0 && x + 0.3 * fg.size <= w - m0 && y - fg.hop - 1.15 * fg.size >= m0 && y <= h - m0)) throw new Error(`${kind} at ${count} houses ${w}x${h}, clock ${c}: figure at (${x.toFixed(1)}, ${y.toFixed(1)}) size ${fg.size.toFixed(1)}`)
            }
          }
          if (kind !== 'send' && kind !== 'win') expect(seen).toBeGreaterThan(0)
        }
      }
    }
  })

  test('nothing is clipped while the moments play: every stroke and fill on the plate, 0 to 12 s by 0.5', () => {
    for (const [w, h] of STAGE_SIZES) {
      const m0 = 0.5 * Math.min(w / 440, h / 280)
      for (const count of STAGE_COUNTS) {
        for (const [g, group] of [['send', 'build', 'celebrate'], ['win', 'fail', 'gather'], ['vote', 'play', 'celebrate']].entries()) {
          const moments = group.map((kind, k) => momentOf(kind as MomentKind, count, k + g * 3, k * 0.7))
          const props = propsOf([['mailbox', 'stall', 'lanterns'], ['cloud', 'scaffold', 'signpost'], ['gallery', 'press', 'garden']][g])
          for (const c of HALF_CLOCKS) {
            const f = fakeContext()
            drawVillage(f.ctx, staged(count, props, moments, { board: GOAL, visitor: 0.6 }), w, h, c)
            for (const op of f.ops) {
              for (let i = 0; i < op.pts.length; i += 2) {
                const x = op.pts[i], y = op.pts[i + 1]
                if (!(x >= m0 && x <= w - m0 && y >= m0 && y <= h - m0)) throw new Error(`${group.join('+')} at ${count} houses ${w}x${h}, clock ${c}: (${x.toFixed(1)}, ${y.toFixed(1)}) off the plate in a ${op.op} of ${op.style}`)
              }
            }
          }
        }
      }
    }
  })

  test('deterministic: the same scene and clock draw the same pixels and place the same figures', () => {
    for (const c of [0, 1.25, 3.5, 5.9, 9]) {
      const moments = MOMENT_KINDS.slice(0, 6).map((kind, k) => momentOf(kind, 22, k, k * 0.5))
      const sc = staged(22, propsOf(['mailbox', 'lanterns', 'cloud']), moments, { visitor: 0.5, board: GOAL })
      const a = fakeContext(), b = fakeContext()
      drawVillage(a.ctx, sc, 440, 280, c)
      drawVillage(b.ctx, structuredClone(sc), 440, 280, c)
      expect(JSON.stringify(b.ops)).toBe(JSON.stringify(a.ops))
      const lay = layoutHouses(22, 440, 280)
      for (const m of moments) expect(JSON.stringify(momentFigures(lay, structuredClone(sc), m, c))).toBe(JSON.stringify(momentFigures(lay, sc, m, c)))
    }
  })

  test('a moment holds its end state: the plate at 30 s is the plate at 12 s', () => {
    for (const count of [4, 22, 40]) {
      for (const [k, kind] of MOMENT_KINDS.entries()) {
        const m = momentOf(kind, count, k, 1)
        const props = propsOf(kind === 'send' ? ['mailbox', 'lanterns'] : kind === 'play' ? ['chessboard', 'cloud'] : ['stall'])
        const draw = (c: number) => { const f = fakeContext(); drawVillage(f.ctx, staged(count, props, [m]), 440, 280, c); return rounded(f.ops) }
        expect(draw(30)).toBe(draw(12))
        const lay = layoutHouses(count, 440, 280)
        expect(rounded(momentFigures(lay, staged(count, props, [m]), m, 30))).toBe(rounded(momentFigures(lay, staged(count, props, [m]), m, 12)))
      }
    }
    expect(momentTime({ kind: 'win', houses: [1], label: null, start: 2 }, 1)).toBeNull()
    expect(momentTime({ kind: 'win', houses: [1], label: null, start: 2 }, 40)).toBe(MOMENT_DUR)
    expect(failAngle(MOMENT_DUR)).toBeCloseTo(failAngle(30), 9)
  })

  test('before its start a moment draws nothing; once it plays, each kind changes the plate', () => {
    for (const [k, kind] of MOMENT_KINDS.entries()) {
      const m = momentOf(kind, 12, k, 3)
      const draw = (moments: Moment[], c: number) => { const f = fakeContext(); drawVillage(f.ctx, staged(12, [], moments), 440, 280, c); return rounded(f.ops) }
      expect(draw([m], 2.5)).toBe(draw([], 2.5))
      expect(draw([m], 3 + 3.2)).not.toBe(draw([], 3 + 3.2))
    }
  })

  test("a moment's residents are only founded, present houses, never the visitor; nobody is drawn twice", () => {
    const lay = layoutHouses(12, 440, 280)
    const sc = staged(12, [], [], {}) ; sc.houses[4].present = false
    const m: Moment = { kind: 'gather', houses: [0, 5, 7, 99], label: null, start: 0 }
    sc.moments = [m]
    expect(momentFigures(lay, sc, m, 8).map((f) => f.n)).toEqual([7])
    // a resident in a pair and in a moment shows in the moment only
    const both = staged(12, [], [{ kind: 'celebrate', houses: [3], label: null, start: 0 }], { pairs: [{ a: 3, b: 4, w: 1 }] })
    const none = staged(12, [], [{ kind: 'celebrate', houses: [3], label: null, start: 0 }], { pairs: [] })
    const f1 = fakeContext(), f2 = fakeContext()
    drawVillage(f1.ctx, both, 440, 280, 2)
    drawVillage(f2.ctx, none, 440, 280, 2)
    expect(rounded(f1.ops)).toBe(rounded(f2.ops))
  })

  test('the talking pairs keep clear of the ground a moment uses', () => {
    for (const count of [10, 22, 40]) {
      const lay = layoutHouses(count, 440, 280)
      const m: Moment = { kind: 'fail', houses: [5], label: null, start: 0 }
      const sc = staged(count, [], [m], { pairs: [{ a: 4, b: 6, w: 1 }] })
      const free = pairSpots(lay, { ...sc, moments: [] })[0], kept = pairSpots(lay, sc)[0]
      const s = lay.houses[4], p = s.path
      const lo = Math.min(p[0][0], p[1][0]) - 0.3 * lay.figure, hi = Math.max(p[0][0], p[1][0]) + 0.3 * lay.figure
      const overlap = (q: typeof kept) => Math.max(0, Math.min(hi, q.right[0] + 0.35 * lay.figure) - Math.max(lo, q.left[0] - 0.35 * lay.figure))
      expect(overlap(kept)).toBeLessThanOrEqual(overlap(free) + 1e-9)
    }
  })

  // two convex polygons overlap: no axis (an edge normal of either) separates their shadows
  type P2 = [number, number][]
  const overlaps = (A: P2, B: P2): boolean => {
    for (const P of [A, B]) {
      for (let k = 0; k < P.length; k++) {
        const p = P[k], q = P[(k + 1) % P.length]
        const nx = q[1] - p[1], ny = p[0] - q[0]
        if (Math.hypot(nx, ny) < 1e-9) continue
        let a0 = Infinity, a1 = -Infinity, b0 = Infinity, b1 = -Infinity
        for (const v of A) { const d = v[0] * nx + v[1] * ny; a0 = Math.min(a0, d); a1 = Math.max(a1, d) }
        for (const v of B) { const d = v[0] * nx + v[1] * ny; b0 = Math.min(b0, d); b1 = Math.max(b1, d) }
        if (b0 >= a1 || a0 >= b1) return false
      }
    }
    return true
  }
  const wallsOf = (s: ReturnType<typeof layoutHouses>['houses'][number]): P2 => [[s.x - s.wallW / 2, s.y], [s.x - s.wallW / 2, s.y - s.wallH], [s.x + s.wallW / 2, s.y - s.wallH], [s.x + s.wallW / 2, s.y]]
  const roofOf = (s: ReturnType<typeof layoutHouses>['houses'][number]): P2 => [s.eaveL, s.apex, s.eaveR]
  const boxOf = (pts: number[][]): P2 => {
    const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1])
    const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys)
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
  }
  const PLATES: [number, number][] = [[440, 280], [360, 200], [880, 560], [300, 190], [600, 300]]

  test("a build's scaffold stays on its own house's ground: clear of every other house's walls and roof, front row or back", () => {
    for (const [w, h] of PLATES) {
      for (let count = 4; count <= 40; count += 2) {
        const lay = layoutHouses(count, w, h)
        for (const [i, s] of lay.houses.entries()) {
          const bs = buildSpot(lay, i)
          const boxes = buildBox(bs) as P2[]
          for (const [k, o] of lay.houses.entries()) {
            if (k === i) continue
            // a same-row neighbour's walls and eaves; for a back-row house also the front roofs in front of it
            const same = o.row === s.row
            if (!same && s.row === 0) continue
            for (const box of boxes) if (overlaps(box, wallsOf(o)) || overlaps(box, roofOf(o))) throw new Error(`${count} houses ${w}x${h}: house ${i + 1}'s frame ${box[0][0].toFixed(1)}-${box[1][0].toFixed(1)} meets house ${k + 1}`)
          }
          expect(bs.x0).toBeGreaterThanOrEqual(lay.board.panel.x + lay.board.panel.w)
          expect(boxes[1][0][1]).toBeGreaterThanOrEqual(0)
          // the builder stands at its own frame
          expect(bs.builder[0]).toBeGreaterThanOrEqual(bs.x0 - lay.figure * 0.3)
          expect(bs.builder[0]).toBeLessThanOrEqual(bs.x1 + lay.figure * 0.3)
        }
      }
    }
  })

  test('moments that share residents draw each resident once: the latest-started moment that shows them has them', () => {
    const lay = layoutHouses(12, 440, 280)
    for (const starts of [[0, 2, 4], [4, 2, 0], [0, 0, 0], [3, 3, 1]]) {
      const moments: Moment[] = [
        { kind: 'gather', houses: [3, 5, 7], label: null, start: starts[0] },
        { kind: 'play', houses: [3, 5], label: null, start: starts[1] },
        { kind: 'celebrate', houses: [3, 7], label: null, start: starts[2] },
      ]
      const sc = staged(12, propsOf(['chessboard']), moments)
      for (let c = 0; c <= 12; c += 0.5) {
        const seen = new Map<number, number>()
        for (const m of moments) for (const fg of momentFigures(lay, sc, m, c)) if (fg.a > 0) seen.set(fg.n, (seen.get(fg.n) ?? 0) + 1)
        for (const [n, k] of seen) if (k > 1) throw new Error(`starts ${starts.join(',')}, clock ${c}: house ${n} drawn ${k} times`)
        // the drawing reads the same answer
        expect(rounded(stageFigures(lay, sc, c))).toBe(rounded(moments.map((m) => momentFigures(lay, sc, m, c))))
      }
    }
    // the gather and the game hold: after the celebration, 7 is back at the board and 3 and 5 sit at the table
    const moments: Moment[] = [
      { kind: 'gather', houses: [3, 5, 7], label: null, start: 0 },
      { kind: 'play', houses: [3, 5], label: null, start: 2 },
      { kind: 'celebrate', houses: [3, 7], label: null, start: 4 },
    ]
    const sc = staged(12, propsOf(['chessboard']), moments)
    expect(momentFigures(lay, sc, moments[0], 12).map((f) => f.n)).toEqual([7])
    expect(momentFigures(lay, sc, moments[1], 12).map((f) => f.n).sort()).toEqual([3, 5])
    expect(momentFigures(lay, sc, moments[2], 12).length).toBe(0)
  })

  test('a send with no mailbox brings one; with every slot taken the letters lie low by the gate post, never in the gate', () => {
    const LETTER_FILL = '#f6efe2'
    for (const [w, h] of PLATES) {
      for (const count of [4, 12, 22, 40]) {
        const lay = layoutHouses(count, w, h)
        const send = momentOf('send', count, 1)
        expect(stageSpots(lay, [], [send]).map((s) => [s.kind, s.fixture])).toEqual([['mailbox', 'send']])
        for (const props of [[], propsOf(['stall', 'lanterns', 'cloud'])]) {
          const moments = props.length ? [send, momentOf('vote', count, 2), momentOf('play', count, 3)] : [send]
          const sc = staged(count, props, moments, { visitor: 0.6, pairs: [{ a: 0, b: 2, w: 1 }] })
          const f = fakeContext()
          drawVillage(f.ctx, sc, w, h, 12)
          const letters = f.ops.filter((o) => o.op === 'fill' && o.style === LETTER_FILL && Math.abs(o.alpha - 0.97) < 1e-9).flatMap((o) => o.subs)
          if (!props.length) expect(letters.length).toBe(Math.min(8, 2 * send.houses.length))
          else expect(letters.length).toBeLessThanOrEqual(4 + 2)   // up to 4 letters, a slip at the ballot
          const g = lay.gate, v = visitorSpot(lay, sc.lens)
          const gatePair = pairSpots(lay, sc).find((p) => p.gate)!
          for (const sub of letters) {
            const pts: P2 = []
            for (let k = 0; k < sub.length; k += 2) pts.push([sub[k], sub[k + 1]])
            const box = boxOf(pts)
            const opening: P2 = [[g.x - g.w / 2, g.y - g.h], [g.x + g.w / 2, g.y - g.h], [g.x + g.w / 2, g.y], [g.x - g.w / 2, g.y]]
            const visitor = boxOf([[v.at[0] - 0.35 * v.size, v.at[1] - 1.15 * v.size], [v.at[0] + 0.35 * v.size, v.at[1]]])
            // between the visitor and the gate resident, up to their bubble
            const talk = boxOf([[gatePair.left[0], gatePair.bubble.y - gatePair.bubble.ry], [gatePair.right[0] + 0.3 * gatePair.size, gatePair.right[1]]])
            for (const [name, zone] of [['gate', opening], ['visitor', visitor], ['talk', talk]] as const) {
              if (overlaps(box, zone)) throw new Error(`${count} houses ${w}x${h} ${props.length} props: a letter at ${box[0][0].toFixed(1)},${box[0][1].toFixed(1)} is in the ${name}`)
            }
          }
        }
      }
    }
  })

  test("a back-row house's fallen sign lies in view: down by the road, clear of every front-row house", () => {
    for (const [w, h] of PLATES) {
      for (let count = 4; count <= 40; count += 2) {
        const lay = layoutHouses(count, w, h)
        if (lay.rows !== 2) continue
        for (const [i, s] of lay.houses.entries()) {
          if (s.row !== 1) continue
          // fallen: from 3.6 s on it lies on the road (standing, it is in front of the front walls' corners, nearer the reader)
          for (const T of [MOMENT_DUR, 4, 3.6]) {
            const sg = failSignPts(lay, i, T)
            const box = boxOf([...sg.post, ...sg.board])
            for (const o of lay.houses) {
              if (o.row !== 0) continue
              if (overlaps(box, wallsOf(o)) || overlaps(box, roofOf(o))) throw new Error(`${count} houses ${w}x${h}, T ${T}: house ${i + 1}'s sign meets front house ${o.n}`)
            }
            for (const [x, y] of box) { expect(x).toBeGreaterThan(0); expect(x).toBeLessThan(w); expect(y).toBeGreaterThan(0); expect(y).toBeLessThan(h) }
          }
        }
      }
    }
  })

  // ---- the second pass: the book's plate (760 x 372), moments that name nobody, sizes that read
  const BOOK: [number, number][] = [[760, 372], [440, 280], [360, 200]]
  const nameless = (kind: MomentKind, start = 0): Moment => ({ kind, houses: [], label: null, start })

  test('at the book size props stand a figure and a half tall below the road, their one-line tags whole inside the plate', () => {
    for (const count of STAGE_COUNTS) {
      const lay = layoutHouses(count, 760, 372)
      for (const sl of groundSlots(lay)) {
        expect(sl.size).toBeGreaterThanOrEqual(PROP_H * lay.figure * 0.97)
        expect(sl.y - sl.size).toBeGreaterThan(roadAt(lay, sl.x, 0.8))
        expect(sl.y + 0.04 * sl.size + tagHeight(sl.size, lay.unit)).toBeLessThanOrEqual(372 - lay.pad.bottom * 0.6 + 1e-6)
      }
    }
    for (const [w, h] of BOOK) {
      for (const count of STAGE_COUNTS) {
        const f = fakeContext()
        drawVillage(f.ctx, staged(count, propsOf(['stall', 'mailbox', 'lanterns']), [], { board: GOAL }), w, h, 0)
        const tags = f.texts.filter((t) => t.text !== '' && !GOAL.includes(t.text.replace('…', '')))
        // one line each at the book's size; a narrow plate may wrap one onto a second line, still inside the plate
        if (w === 760) expect(tags.length).toBe(3)
        else expect(tags.length).toBeGreaterThanOrEqual(3)
        for (const t of tags) { expect(t.y).toBeLessThan(h - 1); expect(t.y - pxOf(t.font)).toBeGreaterThan(0) }
      }
    }
  })

  test('a moment that names nobody still plays: gather and vote take the six nearest the board, celebrate everyone, play two, send three', () => {
    for (const count of [4, 12, 22, 40]) {
      const lay = layoutHouses(count, 760, 372)
      const sc = staged(count, propsOf(['chessboard']), [])
      const houses = sc.houses
      const spots = stageSpots(lay, sc.props, [nameless('play')])
      const xs = (c: number[]) => c.map((i) => lay.houses[i].x)
      const g = castOf(lay, houses, nameless('gather'), spots)
      expect(g.length).toBe(Math.min(6, count))
      // nobody left out stands nearer the board than anyone taken
      const far = Math.max(...xs(g))
      for (let i = 0; i < count; i++) if (!g.includes(i)) expect(lay.houses[i].x).toBeGreaterThanOrEqual(far)
      expect(castOf(lay, houses, nameless('vote'), spots)).toEqual(g)
      expect(castOf(lay, houses, nameless('celebrate'), spots).length).toBe(count)
      expect(castOf(lay, houses, nameless('send'), spots).length).toBe(Math.min(3, count))
      const p = castOf(lay, houses, nameless('play'), spots)
      expect(p.length).toBe(2)
      for (const k of ['build', 'win', 'fail'] as MomentKind[]) expect(castOf(lay, houses, nameless(k), spots)).toEqual([])
      // an absent named resident falls back the same way; a present one is kept
      const absent = staged(count, [], []); absent.houses[0].present = false
      expect(castOf(lay, absent.houses, { kind: 'gather', houses: [1], label: null, start: 0 }, []).length).toBe(Math.min(6, count - 1))
      expect(castOf(lay, houses, { kind: 'gather', houses: [2], label: null, start: 0 }, [])).toEqual([1])
      // every kind changes the plate once it has played, and holds it: 30 s is 12 s
      for (const kind of MOMENT_KINDS) {
        const props = propsOf(kind === 'play' ? ['chessboard'] : kind === 'build' ? ['scaffold'] : ['stall'])
        const draw = (ms: Moment[], c: number) => { const f = fakeContext(); drawVillage(f.ctx, staged(count, props, ms), 760, 372, c); return rounded(f.ops) }
        expect(draw([nameless(kind, 1)], 7.5)).not.toBe(draw([], 7.5))
        expect(draw([nameless(kind, 1)], 30)).toBe(draw([nameless(kind, 1)], 12))
        expect(draw([nameless(kind, 1)], 0.5)).toBe(draw([], 0.5))
      }
    }
  })

  test('moments that name nobody stay on the plate at every clock, in every combination the stage allows', () => {
    const groups: MomentKind[][] = [['send', 'build', 'celebrate'], ['win', 'fail', 'build'], ['gather', 'vote', 'play'], ['gather', 'build', 'fail']]
    for (const [w, h] of BOOK) {
      const m0 = 0.5 * Math.min(w / 440, h / 280)
      for (const count of STAGE_COUNTS) {
        for (const [gi, group] of groups.entries()) {
          for (const props of [[], propsOf(['scaffold', 'mailbox']), propsOf(['stall', 'cloud', 'press'])]) {
            const moments = group.map((k, j) => nameless(k, j * 0.8))
            for (const c of [0, 1, 2.5, 4, 5.5, 8, 30]) {
              const f = fakeContext()
              drawVillage(f.ctx, staged(count, props, moments, { board: GOAL, visitor: 0.6 }), w, h, c)
              for (const op of f.ops) for (let i = 0; i < op.pts.length; i += 2) {
                const x = op.pts[i], y = op.pts[i + 1]
                if (!(x >= m0 && x <= w - m0 && y >= m0 && y <= h - m0)) throw new Error(`group ${gi} ${props.length} props, ${count} houses ${w}x${h}, clock ${c}: (${x.toFixed(1)}, ${y.toFixed(1)}) off the plate in a ${op.op} of ${op.style}`)
              }
            }
            // and each resident shows once
            const lay = layoutHouses(count, w, h)
            const sc = staged(count, props, moments)
            for (const c of [2, 4, 9]) { const ns = stageFigures(lay, sc, c).flat().filter((fg) => fg.a > 0).map((fg) => fg.n); expect(new Set(ns).size).toBe(ns.length) }
          }
        }
      }
    }
  })

  test('the moments read at their size: letters a third of a figure, frames a house tall, a trophy half a figure, a fallen sign a figure long', () => {
    for (const [w, h] of BOOK) {
      for (const count of STAGE_COUNTS) {
        const lay = layoutHouses(count, w, h), f = lay.figure
        const tall = (bs: { base: number; h: number }) => bs.h >= houseHeight(lay) - 1e-6 || bs.base - 1.34 * bs.h <= lay.pad.top + 1.01 * lay.unit
        expect(letterW(lay) * 0.64).toBeGreaterThanOrEqual(f / 3)
        for (const [i, s] of lay.houses.entries()) if (s.row === 0 || i < 6) expect(tall(buildSpot(lay, i))).toBe(true)
        expect(tall(boardScaffold(lay))).toBe(true)
        const slotted = buildFixture(lay, stageSpots(lay, propsOf(['scaffold']), []))
        expect(tall(slotted)).toBe(true)
        expect(boardTrophy(lay).h).toBeGreaterThanOrEqual(0.5 * f)
        expect(boardSign(lay).len).toBeGreaterThanOrEqual(f)
        // fallen, the board's sign lies flat: lower than it is long
        const sg = failSignPts(lay, -1, MOMENT_DUR)
        const box = boxOf([...sg.post, ...sg.board])
        expect(box[2][1] - box[0][1]).toBeLessThan(0.5 * (box[1][0] - box[0][0]))
        // the board's trophy stands over the board's roof, inside the plate
        const tr = boardTrophy(lay)
        expect(tr.y).toBeLessThanOrEqual(lay.board.roof[1][1] + 1e-6)
        expect(tr.y - 1.6 * tr.h).toBeGreaterThan(0)
      }
    }
  })

  test("a nameless build stands at the board's side, or in a free slot when a group gathers at the board", () => {
    const lay = layoutHouses(12, 760, 372)
    const atBoard = stageSpots(lay, [], [nameless('build')])
    expect(atBoard.length).toBe(0)
    expect(buildFixture(lay, atBoard).x0).toBeGreaterThan(lay.board.panel.x + lay.board.panel.w)
    const withGather = stageSpots(lay, [], [nameless('build'), nameless('gather')])
    expect(withGather.map((s) => s.kind)).toEqual(['scaffold'])
    const bs = buildFixture(lay, withGather)
    for (let k = 0; k < 6; k++) { const [x] = gatherSpot(lay, k, 6); expect(x + 0.3 * lay.figure < bs.x0 || x - 0.3 * lay.figure > bs.x1).toBe(true) }
  })
})
const lerpN = (a: number, b: number, t: number) => a + (b - a) * t

// ---------------------------------------------------------------- the pen

describe('pen', () => {
  test('seeded and pure', () => {
    const a = rngOf('house-3'), b = rngOf('house-3')
    for (let i = 0; i < 50; i++) expect(a()).toBe(b())
    const n = noise1(7)
    expect(n(3.25)).toBe(noise1(7)(3.25))
    expect(Math.abs(n(10.5))).toBeLessThanOrEqual(1)
    const line: [number, number][] = [[0, 0], [100, 0]]
    expect(wobble(line, { seed: 4 })).toEqual(wobble(line, { seed: 4 }))
    // the wobble tapers to nothing at the ends, so joins stay tight
    const w = wobble(line, { seed: 4, amp: 3 })
    expect(w[0]).toEqual([0, 0])
    expect(w[w.length - 1]).toEqual([100, 0])
  })

  test('hatch segments stay inside their convex shape', () => {
    const tri: [number, number][] = [[0, 100], [50, 0], [100, 100], [0, 100]]
    const segs = hatchSegments(tri, { angle: 120, spacing: 6, seed: 9, jitter: 0 })
    expect(segs.length).toBeGreaterThan(8)
    for (const s of segs) for (const p of [s.a, s.b]) {
      expect(p[1]).toBeLessThanOrEqual(100 + 1e-6)
      expect(p[1]).toBeGreaterThanOrEqual(2 * Math.abs(p[0] - 50) - 1e-6)
    }
  })
})
