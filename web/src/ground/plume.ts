export interface PlumeState {
  grains: number
  palette: string[]
  wind: number
  settle: number
  source: 'left' | 'bottom' | 'band'
  opacity: number
  seed: number
}

export interface Plume {
  set(next: Partial<PlumeState>): void
  resize(): void
  pause(): void
  resume(): void
  destroy(): void
}

export interface GrainBuffers {
  x: Float32Array
  y: Float32Array
  vx: Float32Array
  vy: Float32Array
  /** Palette slot * 13 + lightness jitter (0..12, centred on 6). */
  c: Uint8Array
  /** 0 = unborn, 1 = airborne, 2 = deposited this step; recycled next step. */
  alive: Uint8Array
  heights: Float32Array
  n: number
}

const LIMIT = 8000
const STEP = 1 / 60
const TRANSITION = 1.5
const SOURCES = ['left', 'bottom', 'band'] as const
const SOURCE_WEIGHTS = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v))
const mix = (a: number, b: number, t: number) => a + (b - a) * t
const capBits = new Float32Array(1), capWords = new Uint32Array(capBits.buffer)

/** Greatest Float32 value not above h (h > 0): a Float32 heap can never round past the floor line. */
function heapCeiling(h: number): number {
  capBits[0] = h
  if (capBits[0] > h) capWords[0]--
  return capBits[0]
}

export function defaultState(): PlumeState {
  return { grains: 6000, palette: ['#996078', '#597b91', '#b79760'], wind: 0.35,
    settle: 0.35, source: 'band', opacity: 0.5, seed: 1701 }
}

export function mulberry32(seed: number): () => number {
  let value = seed | 0
  return () => {
    value = (value + 0x6d2b79f5) | 0
    let t = Math.imul(value ^ (value >>> 15), 1 | value)
    t ^= t + Math.imul(t ^ (t >>> 7), 61 | t)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

function hash(x: number, y: number, seed: number): number {
  let n = Math.imul(x, 374761393) ^ Math.imul(y, 668265263) ^ seed
  n = Math.imul(n ^ (n >>> 13), 1274126177)
  return ((n ^ (n >>> 16)) >>> 0) / 4294967296
}

/**
 * dt is seconds (capped at 50 ms). Buffers and rng contain all replay state.
 * The public core samples a stationary curl field; the canvas controller passes
 * an explicit scrolling phase to the same integrator. No DOM or hidden clocks.
 * Consume alive=2 deposits before the next call. Heights are in CSS pixels.
 */
export function stepGrains(buf: GrainBuffers, state: PlumeState, w: number, h: number,
  dt: number, rng: () => number): void {
  advance(buf, state, w, h, dt, rng, 0, SOURCE_WEIGHTS[SOURCES.indexOf(state.source)])
}

function advance(buf: GrainBuffers, state: PlumeState, w: number, h: number,
  dt: number, rng: () => number, phase: number, sources: readonly number[]): void {
  if (!(w > 0 && h > 0 && dt > 0) || !Number.isFinite(dt + w + h)) return
  dt = Math.min(dt, 0.05)
  const count = Math.min(buf.n, Math.round(state.grains), buf.x.length)
  const cols = buf.heights.length
  const columnWidth = w / cols
  const drag = Math.exp(-1.4 * dt)
  const wind = state.wind * 95
  const ceiling = heapCeiling(h)
  for (let i = 0; i < count; i++) {
    if (buf.alive[i] !== 1) {
      const pick = rng()
      const source = pick < sources[0] ? 0 : pick < sources[0] + sources[1] ? 1 : 2
      buf.x[i] = source === 0 ? Math.min(1, w / 2) : rng() * w
      buf.y[i] = source === 0 ? h * (0.2 + rng() * 0.65)
        : source === 1 ? Math.max(0, h - (cols ? buf.heights[Math.min(cols - 1, Math.floor(buf.x[i] / columnWidth))] : 0) - Math.min(12, h * 0.08))
        : h * (0.6 + rng() * 0.04)
      const launch = source === 0 ? 12 + rng() * 16 : (rng() - 0.5) * 18
      // Leave enough room for the damped launch even on a very narrow canvas.
      buf.vx[i] = clamp(launch, -buf.x[i], w - buf.x[i])
      buf.vy[i] = -Math.min(12 + rng() * 18, buf.y[i])
      buf.c[i] = Math.floor(rng() * state.palette.length) * 13 + Math.floor(rng() * 13)
      buf.alive[i] = 1
    }
    const falling = hash(i, 19, state.seed) < state.settle
    let fx = 0, fy = 0
    if (wind > 0) {
      const nx = buf.x[i] / 150 + phase * 0.055
      const ny = buf.y[i] / 150 - phase * 0.035
      const ix = Math.floor(nx), iy = Math.floor(ny)
      const u = nx - ix, v = ny - iy
      const sx = u * u * (3 - 2 * u), sy = v * v * (3 - 2 * v)
      const a = hash(ix, iy, state.seed), b = hash(ix + 1, iy, state.seed)
      const c = hash(ix, iy + 1, state.seed), d = hash(ix + 1, iy + 1, state.seed)
      // Analytic curl (dN/dy, -dN/dx) of smooth bilinear value noise.
      fx = 6 * v * (1 - v) * mix(c - a, d - b, sx) * wind
      fy = -6 * u * (1 - u) * mix(b - a, d - c, sy) * wind
    }
    const nearSource = sources[0] * Math.max(0, 1 - buf.x[i] / 100)
      + sources[1] * Math.max(0, 1 - (h - buf.y[i]) / 100)
      + sources[2] * Math.max(0, 1 - Math.abs(buf.y[i] - h * 0.62) / 75)
    buf.vx[i] = (buf.vx[i] + (fx + state.wind * 12) * dt) * drag
    buf.vy[i] = (buf.vy[i] + (fy + (falling ? 65 : -9) - nearSource * 17) * dt) * drag
    let x = buf.x[i] + buf.vx[i] * dt
    let y = buf.y[i] + buf.vy[i] * dt
    if (falling && cols && x >= 0 && x <= w) {
      let col = Math.min(cols - 1, Math.floor(x / columnWidth))
      if (y >= h - buf.heights[col]) {
        // Route the NEW grain, never subtract settled material. Both neighbours
        // compete for the deposit once the local slope reaches three pixels.
        const left = col > 0 ? buf.heights[col - 1] : Infinity
        const right = col + 1 < cols ? buf.heights[col + 1] : Infinity
        if (buf.heights[col] - Math.min(left, right) >= 3) col += left <= right ? -1 : 1
        const size = i % 4 === 0 ? 2 : 1
        const oldHeight = buf.heights[col]
        buf.heights[col] = Math.min(ceiling, oldHeight + size * size / columnWidth)
        x = Math.min(w, (col + 0.5) * columnWidth)
        y = h - buf.heights[col]
        buf.vx[i] = buf.vy[i] = 0
        buf.alive[i] = buf.heights[col] > oldHeight ? 2 : 0
      }
    }
    if (x < 0 || x > w || y < 0 || y > h) buf.alive[i] = 0
    buf.x[i] = clamp(x, 0, w)
    buf.y[i] = clamp(y, 0, h)
  }
  buf.alive.fill(0, Math.max(0, count), buf.n)
}

function makeBuffers(w: number): GrainBuffers {
  return { x: new Float32Array(LIMIT), y: new Float32Array(LIMIT),
    vx: new Float32Array(LIMIT), vy: new Float32Array(LIMIT),
    c: new Uint8Array(LIMIT), alive: new Uint8Array(LIMIT),
    heights: new Float32Array(Math.max(1, Math.ceil(w / 2))), n: 0 }
}

function normalize(next: Partial<PlumeState>, base: PlumeState): PlumeState {
  const bounded = (v: number | undefined, fallback: number, max: number) =>
    v !== undefined && Number.isFinite(v) ? clamp(v, 0, max) : fallback
  return {
    grains: Math.round(bounded(next.grains, base.grains, LIMIT)),
    palette: next.palette && next.palette.length >= 2 && next.palette.length <= 4
      ? [...next.palette] : [...base.palette],
    wind: bounded(next.wind, base.wind, 1), settle: bounded(next.settle, base.settle, 1),
    source: next.source && SOURCES.includes(next.source) ? next.source : base.source,
    opacity: bounded(next.opacity, base.opacity, 1),
    seed: next.seed !== undefined && Number.isFinite(next.seed) ? Math.trunc(next.seed) : base.seed,
  }
}

function context(canvas: HTMLCanvasElement): CanvasRenderingContext2D {
  const ctx = canvas.getContext('2d')
  if (!ctx) throw new Error('Plume needs a 2D canvas context')
  return ctx
}

/** Four RGBA slots (0..1), flat: slot * 4 + channel. Parsed by the canvas at set() time only. */
function readPalette(palette: string[], probe: CanvasRenderingContext2D, out: Float64Array): void {
  for (let i = 0; i < 4; i++) {
    probe.clearRect(0, 0, 1, 1)
    probe.fillStyle = '#808080'
    probe.fillStyle = palette[i % palette.length]
    probe.fillRect(0, 0, 1, 1)
    const data = probe.getImageData(0, 0, 1, 1).data
    for (let k = 0; k < 4; k++) out[i * 4 + k] = data[k] / 255
  }
}

/** CSS hsl() to one RGB channel (n = 0 red, 8 green, 4 blue); hue in degrees. */
function hslChannel(n: number, hue: number, saturation: number, lightness: number): number {
  const k = (n + hue / 30) % 12
  return lightness - saturation * Math.min(lightness, 1 - lightness) * Math.max(-1, Math.min(k - 3, 9 - k, 1))
}

/** Writes the 52 swatches (slot * 13 + lightness jitter) as RGBA bytes, without allocating. */
function writeSwatches(colors: Float64Array, data: Uint8ClampedArray): void {
  for (let s = 0; s < 4; s++) {
    const r = colors[s * 4], g = colors[s * 4 + 1], b = colors[s * 4 + 2], alpha = colors[s * 4 + 3]
    const hi = Math.max(r, g, b), lo = Math.min(r, g, b), delta = hi - lo
    const lightness = (hi + lo) / 2
    const saturation = delta === 0 ? 0 : delta / (1 - Math.abs(2 * lightness - 1))
    const hue = delta === 0 ? 0 : 60 * (hi === r ? ((g - b) / delta + 6) % 6
      : hi === g ? (b - r) / delta + 2 : (r - g) / delta + 4)
    for (let j = 0; j < 13; j++) {
      const l = clamp(lightness * (1 + (j - 6) / 100), 0, 1), o = (s * 13 + j) * 4
      data[o] = 255 * hslChannel(0, hue, saturation, l)
      data[o + 1] = 255 * hslChannel(8, hue, saturation, l)
      data[o + 2] = 255 * hslChannel(4, hue, saturation, l)
      data[o + 3] = 255 * alpha
    }
  }
}

function samePalette(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false
  return true
}

/** Fields that change the simulated picture; opacity only changes compositing. */
function sameScene(a: PlumeState, b: PlumeState): boolean {
  return a.grains === b.grains && a.wind === b.wind && a.settle === b.settle && a.source === b.source
    && a.seed === b.seed && samePalette(a.palette, b.palette)
}

export function createPlume(canvas: HTMLCanvasElement, initial: Partial<PlumeState>): Plume {
  const ctx = context(canvas)
  const doc = canvas.ownerDocument
  const win = doc.defaultView!
  const layer = doc.createElement('canvas'), strata = context(layer)
  const previous = doc.createElement('canvas'), previousCtx = context(previous)
  const probe = doc.createElement('canvas')
  probe.width = probe.height = 1
  const probeCtx = context(probe)
  // Grains are 1x1 crops of this swatch strip, so palette easing rewrites bytes, never strings.
  const atlas = doc.createElement('canvas')
  atlas.width = 52; atlas.height = 1
  const atlasCtx = context(atlas), swatches = atlasCtx.createImageData(52, 1)
  const reduced = win.matchMedia('(prefers-reduced-motion: reduce)')
  let target = normalize(initial, defaultState())
  let current = { ...target }, from = { ...target }
  const colors = new Float64Array(16), fromColors = new Float64Array(16), targetColors = new Float64Array(16)
  readPalette(target.palette, probeCtx, targetColors)
  colors.set(targetColors)
  let colorShift = false
  paintSwatches()
  let sources = [...SOURCE_WEIGHTS[SOURCES.indexOf(target.source)]]
  let fromSources = [...sources]
  let transition = TRANSITION, seedFade = TRANSITION
  let w = 0, h = 0, dpr = 1
  let buf = makeBuffers(1), rng = mulberry32(target.seed)
  let clock = 0, accumulator = 0, last = 0
  let frame = 0, resizeTimer = 0
  let paused = false, destroyed = false, hasSize = false
  const heads = new Int32Array(52), links = new Int32Array(LIMIT)

  function paintSwatches(): void {
    writeSwatches(colors, swatches.data)
    atlasCtx.putImageData(swatches, 0, 0)
  }

  function deposit(): void {
    for (let i = 0; i < buf.n; i++) if (buf.alive[i] === 2) {
      const size = Math.max(1, Math.round((i % 4 === 0 ? 2 : 1) * dpr))
      strata.drawImage(atlas, buf.c[i], 0, 1, 1, clamp(Math.round(buf.x[i] * dpr - size / 2), 0, layer.width - size),
        clamp(Math.round(buf.y[i] * dpr), 0, layer.height - size), size, size)
    }
  }

  function simulate(): void {
    clock += STEP
    advance(buf, current, w, h, STEP, rng, clock, sources)
    deposit()
  }

  function draw(): void {
    ctx.clearRect(0, 0, canvas.width, canvas.height)
    const oldShare = seedFade < TRANSITION ? (1 - seedFade / TRANSITION) ** 3 : 0
    ctx.globalAlpha = current.opacity * (1 - oldShare)
    ctx.drawImage(layer, 0, 0)
    heads.fill(-1)
    for (let i = 0; i < buf.n; i++) if (buf.alive[i] === 1) {
      const c = buf.c[i]
      links[i] = heads[c]
      heads[c] = i
    }
    for (let c = 0; c < heads.length; c++) {
      for (let i = heads[c]; i >= 0; i = links[i]) {
        const size = Math.max(1, Math.round((i % 4 === 0 ? 2 : 1) * dpr))
        ctx.drawImage(atlas, c, 0, 1, 1, clamp(Math.round(buf.x[i] * dpr - size / 2), 0, canvas.width - size),
          clamp(Math.round(buf.y[i] * dpr), 0, canvas.height - size), size, size)
      }
    }
    if (seedFade < TRANSITION) {
      // The old complete picture covers a freshly seeded simulation, then eases away.
      ctx.globalAlpha = oldShare
      ctx.drawImage(previous, 0, 0, canvas.width, canvas.height)
    }
    ctx.globalAlpha = 1
  }

  function resetAir(): void {
    rng = mulberry32(target.seed)
    clock = 0
    accumulator = 0
    buf.alive.fill(0)
    buf.n = Math.round(current.grains)
  }

  function advanceStill(): void {
    for (let i = 0; i < 600; i++) simulate()
  }

  /** The canonical reduced-motion still: empty strata and heaps, seeded RNG, clock 0, then 600 steps. */
  function replayStill(): void {
    strata.clearRect(0, 0, layer.width, layer.height)
    buf.x.fill(0); buf.y.fill(0); buf.vx.fill(0); buf.vy.fill(0)
    buf.c.fill(0); buf.heights.fill(0)
    resetAir()
    advanceStill()
  }

  function ease(dt: number): void {
    if (transition < TRANSITION) {
      transition = Math.min(TRANSITION, transition + dt)
      const t = 1 - (1 - transition / TRANSITION) ** 3
      current.grains = mix(from.grains, target.grains, t)
      current.wind = mix(from.wind, target.wind, t)
      current.settle = mix(from.settle, target.settle, t)
      current.opacity = mix(from.opacity, target.opacity, t)
      if (colorShift) {
        if (transition === TRANSITION) { colors.set(targetColors); colorShift = false }
        else for (let k = 0; k < 16; k++) colors[k] = mix(fromColors[k], targetColors[k], t)
        paintSwatches()
      }
      const targetSource = SOURCES.indexOf(target.source)
      for (let i = 0; i < 3; i++) sources[i] = mix(fromSources[i], i === targetSource ? 1 : 0, t)
    }
    const n = Math.round(current.grains)
    if (n < buf.n) buf.alive.fill(0, n, buf.n)
    buf.n = n
    seedFade = Math.min(TRANSITION, seedFade + dt)
  }

  function tick(now: number): void {
    frame = 0
    if (destroyed || paused || doc.hidden || reduced.matches || !hasSize) return
    const dt = last ? Math.min((now - last) / 1000, 0.05) : STEP
    last = now
    ease(dt)
    accumulator += dt
    while (accumulator >= STEP) {
      simulate()
      accumulator -= STEP
    }
    draw()
    schedule()
  }

  function stop(): void {
    if (frame) win.cancelAnimationFrame(frame)
    frame = 0
    last = 0
  }

  function schedule(): void {
    if (!frame && !destroyed && !paused && !doc.hidden && !reduced.matches && hasSize) frame = win.requestAnimationFrame(tick)
  }

  function resize(): void {
    if (destroyed) return
    const rect = canvas.getBoundingClientRect()
    const nw = Math.max(0, rect.width), nh = Math.max(0, rect.height)
    const ndpr = win.devicePixelRatio || 1
    hasSize = nw > 0 && nh > 0
    if (!hasSize) { stop(); return }
    if (nw === w && nh === h && ndpr === dpr) { schedule(); return }
    const oldW = w, oldH = h
    const saved = doc.createElement('canvas')
    saved.width = layer.width; saved.height = layer.height
    context(saved).drawImage(layer, 0, 0)
    w = nw; h = nh; dpr = ndpr
    canvas.width = layer.width = Math.max(1, Math.round(w * dpr))
    canvas.height = layer.height = Math.max(1, Math.round(h * dpr))
    ctx.imageSmoothingEnabled = strata.imageSmoothingEnabled = false
    const heights = new Float32Array(Math.max(1, Math.ceil(w / 2)))
    if (oldW && oldH) {
      strata.drawImage(saved, 0, 0, layer.width, layer.height)
      const ceiling = heapCeiling(h)
      for (let i = 0; i < heights.length; i++) heights[i] = Math.min(ceiling,
        buf.heights[Math.min(buf.heights.length - 1, Math.floor(i / heights.length * buf.heights.length))] * h / oldH)
      for (let i = 0; i < buf.n; i++) {
        buf.x[i] *= w / oldW; buf.y[i] *= h / oldH
        buf.vx[i] *= w / oldW; buf.vy[i] *= h / oldH
      }
      buf.heights = heights
    } else {
      buf = makeBuffers(w)
      resetAir()
      advanceStill()
    }
    draw()
    schedule()
  }

  function onResize(): void {
    win.clearTimeout(resizeTimer)
    resizeTimer = win.setTimeout(resize, 100)
  }

  function onVisibility(): void {
    if (doc.hidden) stop()
    else schedule()
  }

  function still(replay: boolean): void {
    current = { ...target }; transition = seedFade = TRANSITION
    sources = [...SOURCE_WEIGHTS[SOURCES.indexOf(target.source)]]
    colors.set(targetColors); colorShift = false; paintSwatches()
    ease(0)
    if (replay) replayStill()
    draw()
  }

  function onMotion(): void {
    stop()
    if (reduced.matches) still(true)
    else schedule()
  }

  const plume: Plume = {
    set(next) {
      if (destroyed) return
      const old = target, goal = normalize(next, old)
      // A repeated target neither restarts easing nor disturbs a reduced-motion still.
      if (goal.opacity === old.opacity && sameScene(goal, old)) return
      target = goal
      if (!samePalette(goal.palette, old.palette)) readPalette(target.palette, probeCtx, targetColors)
      if (reduced.matches) { still(!sameScene(goal, old)); return }
      from = { ...current }
      fromColors.set(colors)
      fromSources = [...sources]
      current.palette = target.palette
      current.source = target.source
      colorShift = false
      for (let k = 0; k < 16; k++) colorShift ||= colors[k] !== targetColors[k]
      transition = 0
      if (target.seed !== old.seed) {
        previous.width = canvas.width; previous.height = canvas.height
        previousCtx.drawImage(canvas, 0, 0)
        current.seed = target.seed
        seedFade = 0
        resetAir()
      }
      schedule()
    },
    resize,
    pause() { if (!destroyed) { paused = true; stop() } },
    resume() { if (!destroyed) { paused = false; schedule() } },
    destroy() {
      if (destroyed) return
      destroyed = true
      stop()
      win.clearTimeout(resizeTimer)
      win.removeEventListener('resize', onResize)
      doc.removeEventListener('visibilitychange', onVisibility)
      reduced.removeEventListener('change', onMotion)
    },
  }
  win.addEventListener('resize', onResize)
  doc.addEventListener('visibilitychange', onVisibility)
  reduced.addEventListener('change', onMotion)
  resize()
  return plume
}
