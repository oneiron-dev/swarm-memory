import { describe, expect, test } from 'bun:test'
import { createPlume, defaultState, mulberry32, stepGrains, type GrainBuffers, type PlumeState } from '../src/ground/plume'

function buffers(n: number, w = 320): GrainBuffers {
  return { x: new Float32Array(n), y: new Float32Array(n), vx: new Float32Array(n),
    vy: new Float32Array(n), c: new Uint8Array(n), alive: new Uint8Array(n),
    heights: new Float32Array(Math.ceil(w / 2)), n }
}

describe('plume pure core', () => {
  test('replays the same seed identically for 200 steps', () => {
    const state = { ...defaultState(), grains: 256 }
    const a = buffers(256), b = buffers(256)
    const ra = mulberry32(state.seed), rb = mulberry32(state.seed)
    for (let frame = 0; frame < 200; frame++) {
      stepGrains(a, state, 320, 180, 1 / 60, ra)
      stepGrains(b, state, 320, 180, 1 / 60, rb)
    }
    expect(a).toEqual(b)
    expect(a.x.some(x => x > 0)).toBe(true)
  })

  test('keeps every buffer finite, bounded and heights monotone for all sources', () => {
    for (const source of ['left', 'bottom', 'band'] as const) {
      const state = { ...defaultState(), source, grains: 192, wind: 1, settle: 0.65 }
      const buf = buffers(192), rng = mulberry32(51)
      const previous = buf.heights.slice()
      let valid = true
      for (let step = 0; step < 1200; step++) {
        stepGrains(buf, state, 320, 180, 1 / 60, rng)
        for (let i = 0; i < buf.n; i++) {
          valid &&= Number.isFinite(buf.x[i] + buf.y[i] + buf.vx[i] + buf.vy[i])
            && buf.x[i] >= 0 && buf.x[i] <= 320 && buf.y[i] >= 0 && buf.y[i] <= 180
            && buf.alive[i] <= 2 && buf.c[i] < state.palette.length * 13
        }
        for (let i = 0; i < buf.heights.length; i++) {
          valid &&= Number.isFinite(buf.heights[i]) && buf.heights[i] >= previous[i] && buf.heights[i] <= 180
          previous[i] = buf.heights[i]
        }
      }
      expect(valid).toBe(true)
    }
  })

  test('never deposits with settle zero', () => {
    const buf = buffers(256), state = { ...defaultState(), grains: 256, settle: 0 }
    const rng = mulberry32(state.seed)
    for (let i = 0; i < 2000; i++) stepGrains(buf, state, 320, 180, 1 / 60, rng)
    expect(buf.heights.every(height => height === 0)).toBe(true)
    expect(buf.alive.includes(2)).toBe(false)
  })

  test('every grain settles within 2,000 steps in still air, for every source', () => {
    for (const source of ['left', 'bottom', 'band'] as const) {
      const state = { ...defaultState(), grains: 256, source, wind: 0, settle: 1 }
      const buf = buffers(256, 1440), rng = mulberry32(state.seed)
      const settled = new Set<number>()
      for (let step = 0; step < 2000 && settled.size < buf.n; step++) {
        stepGrains(buf, state, 1440, 900, 1 / 60, rng)
        for (let i = 0; i < buf.n; i++) if (buf.alive[i] === 2) settled.add(i)
      }
      expect(settled.size).toBe(buf.n)
    }
  })

  test('marks escaping grains dead, then respawns them at the source', () => {
    const buf = buffers(1), state = { ...defaultState(), grains: 1, wind: 0, settle: 0, source: 'band' as const }
    buf.x[0] = 319; buf.y[0] = 90; buf.vx[0] = 1000; buf.alive[0] = 1
    const rng = mulberry32(9)
    stepGrains(buf, state, 320, 180, 1 / 60, rng)
    expect(buf.alive[0]).toBe(0)
    stepGrains(buf, state, 320, 180, 1 / 60, rng)
    expect(buf.alive[0]).toBe(1)
    expect(buf.y[0]).toBeGreaterThan(100)
    expect(buf.y[0]).toBeLessThan(118)
  })

  test('spreads one incoming deposit sideways without lowering old strata', () => {
    const buf = buffers(1, 6), state = { ...defaultState(), grains: 1, settle: 1, wind: 0 }
    buf.heights.set([1, 5, 2]); buf.x[0] = 3; buf.y[0] = 15
    buf.vy[0] = 20; buf.alive[0] = 1; buf.c[0] = 17
    stepGrains(buf, state, 6, 20, 1 / 60, mulberry32(1))
    expect([...buf.heights]).toEqual([3, 5, 2])
    expect(buf.alive[0]).toBe(2)
    expect(buf.x[0]).toBe(1)
    expect(buf.y[0]).toBe(17)
    expect(buf.c[0]).toBe(17)
    expect(buf.vx[0] + buf.vy[0]).toBe(0)
    stepGrains(buf, { ...state, settle: 0 }, 6, 20, 1 / 60, mulberry32(2))
    expect(buf.alive[0]).not.toBe(2)
    expect([...buf.heights]).toEqual([3, 5, 2])
  })

  test('copied buffers replay without a hidden clock; zero dt does not consume RNG', () => {
    const state = { ...defaultState(), grains: 64 }, buf = buffers(64)
    const rng = mulberry32(44)
    for (let i = 0; i < 37; i++) stepGrains(buf, state, 320, 180, 1 / 60, rng)
    const copy = structuredClone(buf)
    let calls = 0
    stepGrains(buf, state, 320, 180, 0, () => { calls++; return 0.5 })
    expect(calls).toBe(0)
    expect(buf).toEqual(copy)
    const r1 = mulberry32(89), r2 = mulberry32(89)
    for (let i = 0; i < 200; i++) {
      stepGrains(buf, state, 320, 180, 1 / 60, r1)
      stepGrains(copy, state, 320, 180, 1 / 60, r2)
    }
    expect(buf).toEqual(copy)
  })

  test('defaults do not share a mutable palette and distinct seeds differ', () => {
    const a = defaultState(), b = defaultState()
    a.palette[0] = 'red'
    expect(a.palette).not.toEqual(b.palette)
    const r1 = mulberry32(7), r2 = mulberry32(8)
    expect(Array.from({ length: 8 }, r1)).not.toEqual(Array.from({ length: 8 }, r2))
  })

  test('a full heap does not emit deposits that would repaint old strata', () => {
    const buf = buffers(1, 6), state = { ...defaultState(), grains: 1, settle: 1, wind: 0 }
    buf.heights.fill(20); buf.x[0] = 3; buf.y[0] = 1; buf.alive[0] = 1
    stepGrains(buf, state, 6, 20, 1 / 60, mulberry32(3))
    expect([...buf.heights]).toEqual([20, 20, 20])
    expect(buf.alive[0]).toBe(0)
  })

  test('still-air source kicks cannot starve settling on a narrow canvas', () => {
    const state = { ...defaultState(), grains: 32, source: 'left' as const, wind: 0, settle: 1 }
    const buf = buffers(32, 8), rng = mulberry32(42), settled = new Set<number>()
    for (let frame = 0; frame < 2000 && settled.size < buf.n; frame++) {
      stepGrains(buf, state, 8, 900, 1 / 60, rng)
      for (let i = 0; i < buf.n; i++) if (buf.alive[i] === 2) settled.add(i)
    }
    expect(settled.size).toBe(32)
  })
})

describe('plume Float32 heap ceiling', () => {
  test('deposits cap heights at the greatest Float32 not above h, and a full heap stays put', () => {
    // [h, starting heap, expected ceiling]: rounds up, rounds down, exact, power-of-two edge, subnormal.
    const cases = [[20.1, 20, 20.099998474121094], [20.3, 20, 20.299999237060547], [16, 15, 16],
      [15.9999999, 15, 15.999999046325684], [1e-45, 0, 0]]
    for (const [h, heap, ceiling] of cases) {
      const buf = buffers(1, 2), state = { ...defaultState(), grains: 1, settle: 1, wind: 0 }
      buf.heights[0] = heap
      const drop = () => {
        buf.x[0] = 1; buf.y[0] = Math.max(0, h - buf.heights[0] - 0.05)
        buf.vx[0] = 0; buf.vy[0] = 20; buf.alive[0] = 1
        stepGrains(buf, state, 2, h, 1 / 60, mulberry32(5))
      }
      drop()
      expect(buf.heights[0]).toBe(ceiling)
      expect(buf.heights[0]).toBeLessThanOrEqual(h)
      expect(buf.alive[0]).toBe(ceiling > heap ? 2 : 0)
      drop()
      expect(buf.heights[0]).toBe(ceiling)
      expect(buf.alive[0]).toBe(0)
    }
  })
})

// A minimal 2D canvas: each context logs what the current picture is made of. A full-size
// clearRect or a width/height write empties the log; drawImage inlines its source.
function parseColor(css: string): number[] | null {
  const hex = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(css)
  if (hex) {
    const digits = hex[1].length === 3 ? hex[1].replace(/./g, d => d + d) : hex[1]
    return [0, 2, 4].map(i => parseInt(digits.slice(i, i + 2), 16)).concat(255)
  }
  const rgb = /^rgba?\(([^)]*)\)$/.exec(css)
  if (!rgb) return null
  const [r, g, b, a = 1] = rgb[1].split(',').map(Number)
  return [r, g, b, Math.round(a * 255)]
}

class FakeImageData {
  data: Uint8ClampedArray
  constructor(readonly width: number, readonly height: number) { this.data = new Uint8ClampedArray(width * height * 4) }
}

class FakeContext {
  ops: unknown[][] = []
  styleWrites = 0
  reads = 0
  created: FakeImageData[] = []
  puts: FakeImageData[] = []
  image: Uint8ClampedArray | null = null
  globalAlpha = 1
  imageSmoothingEnabled = true
  private style = '#000000'
  private pixel = [0, 0, 0, 0]
  constructor(readonly canvas: FakeCanvas) {}
  get fillStyle() { return this.style }
  set fillStyle(css: string) { this.styleWrites++; if (parseColor(css)) this.style = css }
  reset() { this.ops = []; this.image = null; this.globalAlpha = 1; this.style = '#000000'; this.pixel = [0, 0, 0, 0] }
  clearRect(x: number, y: number, w: number, h: number) {
    if (x <= 0 && y <= 0 && x + w >= this.canvas.width && y + h >= this.canvas.height) { this.ops = []; this.pixel = [0, 0, 0, 0] }
    else this.ops.push(['clearRect', x, y, w, h])
  }
  fillRect(x: number, y: number, w: number, h: number) {
    this.pixel = parseColor(this.style)!
    this.ops.push(['fillRect', this.style, this.globalAlpha, x, y, w, h])
  }
  getImageData() { this.reads++; return { data: new Uint8ClampedArray(this.pixel) } }
  createImageData(w: number, h: number) { const image = new FakeImageData(w, h); this.created.push(image); return image }
  putImageData(image: FakeImageData) { this.puts.push(image); this.image = image.data.slice() }
  drawImage(source: FakeCanvas, ...args: number[]) {
    const from = source.ctx
    if (args.length === 8 && from.image) {
      this.ops.push(['swatch', this.globalAlpha, ...from.image.slice(args[0] * 4, args[0] * 4 + 4), ...args.slice(4)])
    } else this.ops.push(['canvas', this.globalAlpha, from.picture(), ...args])
  }
  picture() { return JSON.stringify(this.ops) }
}

class FakeCanvas {
  ctx = new FakeContext(this)
  rect = { width: 0, height: 0 }
  private size = [300, 150]
  constructor(readonly ownerDocument: unknown) {}
  get width() { return this.size[0] }
  set width(v: number) { this.size[0] = v; this.ctx.reset() }
  get height() { return this.size[1] }
  set height(v: number) { this.size[1] = v; this.ctx.reset() }
  getContext() { return this.ctx }
  getBoundingClientRect() { return this.rect }
}

interface Options { reduced?: boolean; dpr?: number; width?: number; height?: number }

function mount(initial: Partial<PlumeState>, { reduced = false, dpr = 1, width = 320, height = 180 }: Options = {}) {
  const frames = new Map<number, (now: number) => void>()
  let nextFrame = 1, now = 1000
  const motion = {
    matches: reduced, listener: null as null | (() => void),
    addEventListener(_: string, fn: () => void) { motion.listener = fn },
    removeEventListener() { motion.listener = null },
  }
  const canvases: FakeCanvas[] = []
  const win = {
    devicePixelRatio: dpr, matchMedia: () => motion,
    requestAnimationFrame(cb: (now: number) => void) { frames.set(nextFrame, cb); return nextFrame++ },
    cancelAnimationFrame(id: number) { frames.delete(id) },
    setTimeout: () => 0, clearTimeout() {}, addEventListener() {}, removeEventListener() {},
  }
  const doc = {
    hidden: false, defaultView: win, addEventListener() {}, removeEventListener() {},
    createElement() { const c = new FakeCanvas(doc); canvases.push(c); return c },
  }
  const canvas = doc.createElement()
  canvas.rect = { width, height }
  const plume = createPlume(canvas as unknown as HTMLCanvasElement, initial)
  const sum = (key: 'styleWrites' | 'reads') => canvases.reduce((total, c) => total + c.ctx[key], 0)
  return {
    plume, canvas, frames, motion,
    picture: () => canvas.ctx.picture(),
    /** The context the module made its swatch ImageData on (none before the fix). */
    atlas: () => canvases.find(c => c.ctx.created.length)?.ctx,
    styleWrites: () => sum('styleWrites'),
    reads: () => sum('reads'),
    /** Alpha the strata layer was composited with, i.e. the eased opacity. */
    alpha: () => canvas.ctx.ops.find(op => op[0] === 'canvas')?.[1],
    run(n: number) {
      for (let k = 0; k < n; k++) {
        const due = [...frames.values()]
        frames.clear()
        for (const cb of due) cb(now)
        now += 1000 / 60
      }
    },
  }
}

const pixel = (image: Uint8ClampedArray, slot: number) => [...image.slice(slot * 4, slot * 4 + 4)]

describe('plume controller transitions (mock canvas)', () => {
  test('palette easing rewrites one reused ImageData and assigns no colour strings per frame', () => {
    const p = mount({ grains: 300, palette: ['#ff0000', '#0000ff'] })
    p.run(5)
    const atlas = p.atlas()!
    expect(atlas).toBeDefined()
    expect(atlas.created.length).toBe(1)
    const image = atlas.created[0], puts = atlas.puts.length
    p.plume.set({ palette: ['#00ff00', '#ffffff'] })
    const styles = p.styleWrites() // set() itself may parse CSS; frames may not
    const centre: number[][] = []
    for (let k = 0; k < 95; k++) { p.run(1); centre.push(pixel(atlas.image!, 6)) }
    expect(p.styleWrites()).toBe(styles)
    expect(atlas.created.length).toBe(1)
    expect(atlas.puts.length).toBeGreaterThanOrEqual(puts + 89)
    expect(atlas.puts.slice(puts).every(put => put === image)).toBe(true)
    // Gradual, then exactly the target, with ±6% lightness jitter around each slot's centre.
    expect(centre[0][1]).toBeGreaterThan(0)
    expect(centre[0][1]).toBeLessThan(20)
    expect(centre[44][1]).toBeGreaterThan(150)
    expect(centre[44][1]).toBeLessThan(255)
    expect(centre.at(-1)).toEqual([0, 255, 0, 255])
    expect(pixel(atlas.image!, 0)).toEqual([0, 240, 0, 255])
    expect(pixel(atlas.image!, 12)).toEqual([15, 255, 15, 255])
    expect(pixel(atlas.image!, 13 + 6)).toEqual([255, 255, 255, 255])
    expect(pixel(atlas.image!, 26 + 6)).toEqual([0, 255, 0, 255])
    const settled = atlas.puts.length
    p.run(10)
    expect(atlas.puts.length).toBe(settled)
  })

  test('numeric-only and same-palette updates neither re-read the palette nor repaint swatches', () => {
    const p = mount({ grains: 300 })
    p.run(3)
    const atlas = p.atlas()!
    expect(atlas).toBeDefined()
    const reads = p.reads(), puts = atlas.puts.length, styles = p.styleWrites()
    p.plume.set({ grains: 500, wind: 0.9, settle: 0.1, opacity: 0.9, source: 'left', palette: [...defaultState().palette] })
    p.run(100)
    expect(p.reads()).toBe(reads)
    expect(atlas.puts.length).toBe(puts)
    expect(p.styleWrites()).toBe(styles)
    expect(p.alpha()).toBe(0.9)
  })

  test('a repeated target does not restart easing', () => {
    const p = mount({ grains: 100, opacity: 0.5 })
    p.run(2)
    p.plume.set({ opacity: 1 })
    p.run(45)
    const midway = p.alpha() as number
    expect(midway).toBeGreaterThan(0.5)
    expect(midway).toBeLessThan(1)
    p.plume.set({ opacity: 1 })
    p.plume.set({ ...defaultState(), grains: 100, opacity: 1 })
    p.run(47)
    expect(p.alpha()).toBe(1)
  })

  test('an interrupted palette transition continues from the displayed colours', () => {
    const p = mount({ grains: 100, palette: ['#ff0000', '#0000ff'] })
    p.run(2)
    p.plume.set({ palette: ['#00ff00', '#00ff00'] })
    p.run(30)
    const atlas = p.atlas()!
    const shown = pixel(atlas.image!, 6)
    p.plume.set({ palette: ['#ffffff', '#000000', '#ff00ff', 'rgba(0, 0, 255, 0.5)'] })
    p.run(1)
    const next = pixel(atlas.image!, 6)
    for (let k = 0; k < 4; k++) expect(Math.abs(next[k] - shown[k])).toBeLessThanOrEqual(10)
    p.run(95)
    expect(pixel(atlas.image!, 6)).toEqual([255, 255, 255, 255])
    expect(pixel(atlas.image!, 39 + 6)).toEqual([0, 0, 255, 128])
  })
})

describe('plume reduced-motion still (mock canvas)', () => {
  test('no-op updates keep the still picture identical and schedule nothing', () => {
    const state: PlumeState = { seed: 1701, grains: 32, wind: 0, settle: 0, palette: ['#ff0000', '#0000ff'],
      source: 'band', opacity: 0.5 }
    const p = mount(state, { reduced: true, dpr: 2 })
    const still = p.picture()
    expect(p.canvas.ctx.ops.length).toBeGreaterThan(0)
    expect(p.frames.size).toBe(0)
    for (const update of [{}, { wind: 0 }, { wind: -1 }, { grains: 32.2 }, { ...state },
      { palette: ['#ff0000', '#0000ff'] }]) {
      p.plume.set(update)
      expect(p.picture()).toBe(still)
      expect(p.frames.size).toBe(0)
    }
  })

  test('real updates replay the seeded still: same picture as a fresh controller, A to B to A', () => {
    const a: PlumeState = { seed: 1701, grains: 300, wind: 0.4, settle: 0.6, source: 'band', opacity: 0.6,
      palette: ['#996078', '#597b91', '#b79760'] }
    const p = mount(a, { reduced: true, dpr: 2 })
    expect(p.picture()).toBe(mount(a, { reduced: true, dpr: 2 }).picture())
    let state = a
    for (const update of [{ wind: 0.8, settle: 0.3 }, { palette: ['#ff0000', 'rgba(0, 0, 255, 0.5)'] },
      { source: 'bottom' as const }, { seed: 99 }, { grains: 120 }, { opacity: 0.2 }, a]) {
      state = { ...state, ...update }
      p.plume.set(update)
      expect(p.picture()).toBe(mount(state, { reduced: true, dpr: 2 }).picture())
      expect(p.frames.size).toBe(0)
    }
  })

  test('entering reduced motion after animated history draws the canonical still', () => {
    const a = { seed: 7, grains: 400, wind: 0.5, settle: 0.5 }
    const update = { wind: 0.9, palette: ['#203040', '#c0a080'] }
    const p = mount(a)
    p.run(40)
    p.plume.set(update)
    p.run(20)
    p.motion.matches = true
    p.motion.listener!()
    expect(p.frames.size).toBe(0)
    expect(p.picture()).toBe(mount({ ...a, ...update }, { reduced: true }).picture())
  })

  test('resizing a full heap into a fractional height keeps Float32 heights <= h', () => {
    const Native = Float32Array, made: Float32Array[] = []
    const global = globalThis as { Float32Array: unknown }
    global.Float32Array = class extends Native {
      constructor(...args: ConstructorParameters<typeof Float32Array>) { super(...args); made.push(this) }
    }
    try {
      const p = mount({ grains: 8000, wind: 0, settle: 1, source: 'band' }, { reduced: true, width: 4, height: 20 })
      const full = made.filter(a => a.length === 2).at(-1)!
      expect([...full]).toEqual([20, 20])
      p.canvas.rect = { width: 4, height: 20.1 }
      p.plume.resize()
      const heights = made.filter(a => a.length === 2).at(-1)!
      expect(heights).not.toBe(full)
      expect([...heights]).toEqual([20.099998474121094, 20.099998474121094])
      expect(heights.every(v => v <= 20.1)).toBe(true)
    } finally {
      global.Float32Array = Native
    }
  })
})
