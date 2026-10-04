import { useEffect, useId, useMemo, useRef, useState } from 'preact/hooks'
import type { JSX } from 'preact'
import type { Lens } from './types'
import '../styles/glyphs.css'

// The book's small drawn furniture: five lens glyphs, the mode switch built from them, a pen-ruled figure and an inked
// mood strip. Every mark is a pen stroke with a little hand wobble baked into its path data. The wobble is seeded, so the
// same input draws the same line on every render and every machine. The helpers below are pure and tested.

export type Pt = [number, number]
export type GlyphKind = 'village' | 'goal' | 'memory' | 'humans' | 'agent'

// ---------- pure helpers: randomness, noise, wobble, paths ----------

/** mulberry32: a small seeded PRNG, uniform in [0, 1). */
export function rand(seed: number): () => number {
  let a = seed >>> 0
  return () => {
    a = (a + 0x6d2b79f5) >>> 0
    let t = a
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

/** FNV-1a over a string, for seeds taken from a label. */
export function seedOf(s: string): number {
  let h = 0x811c9dc5
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 0x01000193)
  return h >>> 0
}

/** Smooth 1-D value noise in [-1, 1] along arc length; one lattice value every `period` units. */
export function noise1(seed: number, period: number): (s: number) => number {
  const r = rand(seed)
  const lattice: number[] = []
  return (s) => {
    const x = Math.max(0, (Number.isFinite(s) ? s : 0) / period)
    const i = Math.floor(x)
    const f = x - i
    while (lattice.length <= i + 1) lattice.push(r() * 2 - 1)
    const u = f * f * (3 - 2 * f)
    return lattice[i] * (1 - u) + lattice[i + 1] * u
  }
}

const r2 = (n: number): number => {
  const v = Math.round(n * 100) / 100
  return v === 0 ? 0 : v
}
const fmt = (p: Pt): string => `${r2(p[0])} ${r2(p[1])}`
const mid = (a: Pt, b: Pt): Pt => [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]

/**
 * A straight-sided pen line: every segment gets a few extra points pushed off its normal by smooth noise. The vertices
 * stay exactly where they were (corners and data points are not moved); the push tapers to nothing at each end of a
 * segment, so joins never come apart. Returns one point list per segment, each starting and ending on a vertex.
 */
export function wobbleLine(pts: Pt[], opt: { seed: number; amp: number; step: number }): Pt[][] {
  const n = noise1(opt.seed, Math.max(0.5, opt.step * 1.6))
  const out: Pt[][] = []
  let s = 0
  for (let i = 0; i + 1 < pts.length; i++) {
    const a = pts[i], b = pts[i + 1]
    const dx = b[0] - a[0], dy = b[1] - a[1]
    const len = Math.hypot(dx, dy)
    const seg: Pt[] = [a]
    const k = len > 0 ? Math.max(1, Math.min(64, Math.round(len / opt.step))) : 1
    const nx = len > 0 ? -dy / len : 0, ny = len > 0 ? dx / len : 0
    for (let j = 1; j < k; j++) {
      const t = j / k
      const d = opt.amp * n(s + len * t) * Math.sin(Math.PI * t)
      seg.push([a[0] + dx * t + nx * d, a[1] + dy * t + ny * d])
    }
    seg.push(b)
    out.push(seg)
    s += len
  }
  return out
}

/** Quadratic curves through the midpoints of a point list: passes through the first and last points, smooth between. */
export function smoothPath(pts: Pt[], closed = false): string {
  if (pts.length === 0) return ''
  if (pts.length === 1) return `M${fmt(pts[0])}`
  if (closed && pts.length > 2) {
    const m = pts.length
    let d = `M${fmt(mid(pts[m - 1], pts[0]))}`
    for (let i = 0; i < m; i++) d += `Q${fmt(pts[i])} ${fmt(mid(pts[i], pts[(i + 1) % m]))}`
    return d + 'Z'
  }
  let d = `M${fmt(pts[0])}`
  if (pts.length === 2) return d + `L${fmt(pts[1])}`
  for (let i = 1; i < pts.length - 2; i++) d += `Q${fmt(pts[i])} ${fmt(mid(pts[i], pts[i + 1]))}`
  return d + `Q${fmt(pts[pts.length - 2])} ${fmt(pts[pts.length - 1])}`
}

/** One path for a wobbled polyline: the segments join exactly on their vertices, so corners stay corners. */
export function linePath(segs: Pt[][]): string {
  if (segs.length === 0) return ''
  let d = ''
  for (const seg of segs) {
    const p = smoothPath(seg)
    d += d === '' ? p : p.replace(/^M[^QL]*/, '')
  }
  return d
}

/** Uniform Catmull-Rom through control points, `per` samples per span. The curve passes through every control point. */
export function catmull(ctrl: Pt[], per: number): Pt[] {
  if (ctrl.length < 3) return ctrl.slice()
  const p = [ctrl[0], ...ctrl, ctrl[ctrl.length - 1]]
  const out: Pt[] = []
  for (let i = 1; i + 2 < p.length; i++) {
    const [p0, p1, p2, p3] = [p[i - 1], p[i], p[i + 1], p[i + 2]]
    for (let j = 0; j < per; j++) {
      const t = j / per, t2 = t * t, t3 = t2 * t
      const f = (a: number, b: number, c: number, e: number) =>
        0.5 * (2 * b + (-a + c) * t + (2 * a - 5 * b + 4 * c - e) * t2 + (-a + 3 * b - 3 * c + e) * t3)
      out.push([f(p0[0], p1[0], p2[0], p3[0]), f(p0[1], p1[1], p2[1], p3[1])])
    }
  }
  out.push(ctrl[ctrl.length - 1])
  return out
}

/** A freehand curve: each point pushed off the local normal by smooth noise, tapered to nothing at the two ends. */
export function wobbleCurve(pts: Pt[], opt: { seed: number; amp: number; period?: number }): Pt[] {
  if (pts.length < 3) return pts.slice()
  const n = noise1(opt.seed, opt.period ?? 3)
  const acc = [0]
  for (let i = 1; i < pts.length; i++) acc.push(acc[i - 1] + Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]))
  const total = acc[acc.length - 1] || 1
  const ramp = Math.min(2.5, total * 0.2)
  return pts.map((p, i) => {
    if (i === 0 || i === pts.length - 1) return p
    const a = pts[i - 1], b = pts[i + 1]
    const dx = b[0] - a[0], dy = b[1] - a[1]
    const len = Math.hypot(dx, dy) || 1
    const edge = Math.min(acc[i], total - acc[i])
    const taper = ramp > 0 ? Math.min(1, edge / ramp) : 1
    const d = opt.amp * n(acc[i]) * taper
    return [p[0] - (dy / len) * d, p[1] + (dx / len) * d]
  })
}

/** A pen circle: the radius drifts a little and the stroke runs a touch past where it started, like a hand closing it. */
export function penCircle(cx: number, cy: number, r: number, opt: { seed: number; amp: number; start?: number; over?: number }): string {
  const n = noise1(opt.seed, 1.3)
  const a0 = opt.start ?? -2.2
  const over = opt.over ?? 0.32
  const steps = Math.max(10, Math.round(r * 3.2))
  const sweep = Math.PI * 2 + over
  const pts: Pt[] = []
  for (let i = 0; i <= steps; i++) {
    const t = i / steps
    const a = a0 + sweep * t
    // the end lands slightly inside the start, so the overlap reads as one stroke, not a ring with a tail
    const rr = r + opt.amp * n(t * 6) - (t > 0.92 ? (t - 0.92) * 3 * opt.amp : 0)
    pts.push([cx + Math.cos(a) * rr, cy + Math.sin(a) * rr])
  }
  return smoothPath(pts)
}

// ---------- the five lens glyphs (22 x 22 drawing space) ----------

export interface GlyphDef { d: string[]; dots: { x: number; y: number; r: number }[] }

function line(pts: Pt[], seed: number, amp = 0.34, step = 2.2): string {
  return linePath(wobbleLine(pts, { seed, amp, step }))
}
function curve(ctrl: Pt[], seed: number, amp = 0.3, per = 5): string {
  return smoothPath(wobbleCurve(catmull(ctrl, per), { seed, amp, period: 3.2 }))
}

function buildGlyph(kind: GlyphKind): GlyphDef {
  switch (kind) {
    case 'village':
      // three houses in a row, terraced, the middle one taller; a lit window in the middle one
      return {
        d: [
          line([[1.7, 18.7], [1.7, 12.6], [4.7, 9.3], [7.7, 12.6], [7.7, 11.2], [11, 7.2], [14.3, 11.2], [14.3, 12.9], [17.3, 9.8], [20.3, 12.9], [20.3, 18.7]], 11),
          line([[7.7, 12.6], [7.7, 18.7]], 12, 0.2),
          line([[14.3, 12.9], [14.3, 18.7]], 13, 0.2),
          line([[0.9, 18.8], [21.1, 18.6]], 14, 0.3, 3),
        ],
        dots: [{ x: 11, y: 12.9, r: 1.05 }],
      }
    case 'goal':
      // an archer's target and one arrow in the middle of it
      return {
        d: [
          penCircle(9.3, 12.7, 7.4, { seed: 21, amp: 0.32, start: -2.6 }),
          penCircle(9.3, 12.7, 3.5, { seed: 22, amp: 0.22, start: 0.6, over: 0.25 }),
          line([[20.3, 1.7], [10.1, 11.9]], 23, 0.22, 3),
          line([[18.2, 3.8], [18.3, 1.3]], 24, 0.12),
          line([[18.2, 3.8], [20.7, 3.7]], 25, 0.12),
        ],
        dots: [{ x: 9.3, y: 12.7, r: 1.15 }],
      }
    case 'memory':
      // a thread rising left to right that turns back over itself once, like a pen loop; the knot where it crosses.
      // the loop leans and the two ends differ, so it never reads as a head and shoulders
      return {
        d: [
          curve([[1.2, 19.3], [4.4, 18], [7.8, 15.6], [10.6, 12.6], [13.6, 9.6], [16, 6.4], [15.4, 3], [12.4, 1.8], [9.4, 3.2], [8.4, 6.6], [9.2, 10.2], [10.6, 12.6], [12.4, 15], [15.4, 16.4], [18.4, 15.6], [20.8, 13.6]], 31, 0.26, 4),
        ],
        dots: [{ x: 10.6, y: 12.6, r: 2.1 }],
      }
    case 'humans':
      // a small standing figure
      return {
        d: [
          penCircle(11, 4.4, 2.5, { seed: 41, amp: 0.16, start: -1.2, over: 0.2 }),
          line([[11, 7.4], [11.1, 13.9]], 42, 0.25),
          line([[6.9, 12.6], [11, 8.9], [15.1, 12.6]], 43, 0.22),
          line([[7.8, 20.3], [11.05, 13.9], [14.3, 20.3]], 44, 0.22),
        ],
        dots: [],
      }
    case 'agent':
      // one strand, top to bottom, with a knot tied in it and its loose end
      return {
        d: [
          curve([[11, 1.3], [10.8, 6], [11, 11], [11.2, 15.8], [11, 20.7]], 51, 0.34, 6),
          line([[12.2, 11.9], [15.2, 15]], 52, 0.18),
        ],
        dots: [{ x: 11, y: 11, r: 2.05 }],
      }
  }
}

const glyphCache = new Map<GlyphKind, GlyphDef>()
/** The path data for one glyph; deterministic, built once per kind. */
export function glyphDef(kind: GlyphKind): GlyphDef {
  let g = glyphCache.get(kind)
  if (!g) glyphCache.set(kind, (g = buildGlyph(kind)))
  return g
}

export const GLYPH_KINDS: readonly GlyphKind[] = ['village', 'goal', 'memory', 'humans', 'agent']
export const glyphKindOf = (lens: Lens): GlyphKind => (lens.startsWith('agent:') ? 'agent' : (lens as GlyphKind))

const KIND_WORD: Record<GlyphKind, string> = { village: 'the village', goal: 'the goal', memory: 'memory', humans: 'the humans', agent: 'one agent' }

/** The word the switch shows for a lens: the lens's name, or the chosen agent's name. */
export function lensWord(lens: Lens, agents: { id: string; name: string }[]): string {
  if (lens.startsWith('agent:')) {
    const id = lens.slice(6)
    return agents.find((a) => a.id === id)?.name ?? id
  }
  return KIND_WORD[lens as GlyphKind] ?? KIND_WORD.village
}

export function LensGlyph({ lens, size = 22 }: { lens: 'village' | 'goal' | 'memory' | 'humans' | 'agent'; size?: number }): JSX.Element {
  const g = glyphDef(lens)
  // 1.4 px at 22 px; a larger glyph gets a slightly heavier pen, not a proportionally fatter one
  const sw = 1.4 * Math.sqrt(22 / size)
  return (
    <svg class="g-glyph" width={size} height={size} viewBox="0 0 22 22" aria-hidden="true" focusable="false">
      <g fill="none" stroke="currentColor" stroke-width={sw} stroke-linecap="round" stroke-linejoin="round">
        {g.d.map((d) => <path d={d} />)}
      </g>
      {g.dots.map((p) => <circle cx={p.x} cy={p.y} r={p.r} fill="currentColor" />)}
    </svg>
  )
}

// ---------- the mode switch ----------

export function ModeSwitch({ lens, agents, onChange }: { lens: Lens; agents: { id: string; name: string }[]; onChange: (l: Lens) => void }): JSX.Element {
  const [open, setOpen] = useState(false)
  const pickRef = useRef<HTMLSelectElement>(null)
  const agentBtn = useRef<HTMLButtonElement>(null)
  const pickId = 'g-pick-' + useId().replace(/[^A-Za-z0-9_-]/g, '')
  const kind = glyphKindOf(lens)
  const current = lens.startsWith('agent:') ? lens.slice(6) : ''

  useEffect(() => {
    const sel = pickRef.current
    if (!open || !sel) return
    sel.focus()
    // the click that opened the picker still counts as a user gesture: open the list at once where the browser can
    try {
      sel.showPicker?.()
    } catch {
      /* not allowed or not supported: the focused select is enough */
    }
  }, [open])

  const close = (refocus: boolean) => {
    setOpen(false)
    if (refocus) agentBtn.current?.focus()
  }

  return (
    <div class="g-modes">
      <div class="g-modes-row" role="group" aria-label="read as">
        {GLYPH_KINDS.map((k) => {
          const word = KIND_WORD[k]
          const isAgent = k === 'agent'
          return (
            <button
              type="button"
              class="g-mode"
              ref={isAgent ? agentBtn : undefined}
              aria-label={word}
              title={word}
              aria-pressed={kind === k}
              aria-expanded={isAgent ? open : undefined}
              aria-controls={isAgent && open ? pickId : undefined}
              disabled={isAgent && agents.length === 0}
              onClick={() => {
                if (isAgent) return setOpen((o) => !o)
                setOpen(false)
                if (lens !== k) onChange(k)
              }}
            >
              <LensGlyph lens={k} />
            </button>
          )
        })}
      </div>
      {open ? (
        <select
          id={pickId}
          class="g-modes-pick"
          ref={pickRef}
          aria-label="one agent"
          value={current}
          onChange={(e) => {
            const id = e.currentTarget.value
            if (!id) return
            setOpen(false)
            onChange(`agent:${id}`)
          }}
          onKeyDown={(e) => {
            if (e.key === 'Escape') {
              e.preventDefault()
              close(true)
            }
          }}
          onBlur={() => setOpen(false)}
        >
          {current ? null : <option value="" disabled>choose an agent</option>}
          {agents.map((a) => <option value={a.id}>{a.name}</option>)}
        </select>
      ) : (
        <span class="g-modes-word" aria-live="polite">{lensWord(lens, agents)}</span>
      )}
    </div>
  )
}

// ---------- the figure ----------

/** The pen rule under a figure, in a 100 x 6 box stretched to the value's width; seeded by the value. */
export function figureRulePath(value: string): string {
  const r = rand(seedOf(value))
  const y0 = 3.1 + (r() - 0.5) * 0.8, y1 = 2.9 + (r() - 0.5) * 0.8
  return linePath(wobbleLine([[0.6, y0], [99.4, y1]], { seed: seedOf(value) ^ 0x5bd1, amp: 1.05, step: 7 }))
}

export function Figure({ value, label }: { value: string; label: string }): JSX.Element {
  const d = useMemo(() => figureRulePath(label || value), [label])
  return (
    <div class="g-figure">
      <span class="g-figure-top">
        <span class="g-figure-value">{value}</span>
        <svg class="g-figure-rule" viewBox="0 0 100 6" preserveAspectRatio="none" aria-hidden="true" focusable="false">
          <path d={d} fill="none" stroke="currentColor" stroke-width={1} stroke-linecap="round" vector-effect="non-scaling-stroke" />
        </svg>
      </span>
      <span class="g-figure-label">{label}</span>
    </div>
  )
}

// ---------- the mood strip ----------

export interface StripGeometry {
  /** one path per unbroken run of values */
  runs: string[]
  /** values with a gap on both sides: drawn as a dot, since a run of one has no length */
  dots: Pt[]
  /** the marked day: x always, y only when that day has a value */
  mark: { x: number; y: number | null } | null
}

const STRIP_PAD_X = 3, STRIP_PAD_Y = 4

/** Where value i sits in a w x h strip; values are clamped to 0..1, anything not a finite number is a gap (null). */
export function stripScale(values: readonly (number | null)[], w: number, h: number): (Pt | null)[] {
  const n = values.length
  const iw = Math.max(0, w - 2 * STRIP_PAD_X), ih = Math.max(0, h - 2 * STRIP_PAD_Y)
  return values.map((v, i) => {
    if (v == null || !Number.isFinite(v)) return null
    const c = Math.min(1, Math.max(0, v))
    const x = n <= 1 ? w / 2 : STRIP_PAD_X + (i * iw) / (n - 1)
    return [x, STRIP_PAD_Y + (1 - c) * ih]
  })
}

export function stripGeometry(values: readonly (number | null)[], mark: number | null, w: number, h: number): StripGeometry {
  const pts = stripScale(values, w, h)
  const runs: string[] = [], dots: Pt[] = []
  let run: Pt[] = []
  let k = 0
  const flush = () => {
    if (run.length === 1) dots.push(run[0])
    // vertices are the data and stay put; only the between-points wobble, by well under a pixel
    else if (run.length > 1) runs.push(linePath(wobbleLine(run, { seed: 0x9e37 + k * 7919 + values.length, amp: 0.45, step: 2.4 })))
    run = []
    k++
  }
  for (const p of pts) {
    if (p) run.push(p)
    else flush()
  }
  flush()
  let m: StripGeometry['mark'] = null
  if (mark != null && Number.isInteger(mark) && mark >= 0 && mark < values.length) {
    const n = values.length
    const x = n <= 1 ? w / 2 : STRIP_PAD_X + (mark * Math.max(0, w - 2 * STRIP_PAD_X)) / (n - 1)
    m = { x, y: pts[mark]?.[1] ?? null }
  }
  return { runs, dots, mark: m }
}

export function MoodStrip({ values, mark, width = 220, height = 36 }: { values: (number | null)[]; mark: number | null; width?: number; height?: number }): JSX.Element {
  const fid = 'g-ink-' + useId().replace(/[^A-Za-z0-9_-]/g, '')
  const g = useMemo(() => stripGeometry(values, mark, width, height), [values, mark, width, height])
  return (
    <svg class="g-strip" width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden="true" focusable="false">
      <defs>
        {/* the loom's inked grade: a little grain eaten out of the ink, a sub-pixel warp; one filter per strip */}
        <filter id={fid} filterUnits="userSpaceOnUse" x={0} y={0} width={width} height={height} color-interpolation-filters="sRGB">
          <feTurbulence type="fractalNoise" baseFrequency="0.8" numOctaves={2} seed={5} result="grain" />
          <feColorMatrix in="grain" type="luminanceToAlpha" result="ga" />
          <feComposite in="SourceGraphic" in2="ga" operator="arithmetic" k1={0} k2={1} k3={-0.14} k4={0} result="inked" />
          <feTurbulence type="fractalNoise" baseFrequency="0.09" numOctaves={2} seed={3} result="warp" />
          <feDisplacementMap in="inked" in2="warp" scale={0.7} xChannelSelector="R" yChannelSelector="G" />
        </filter>
      </defs>
      {g.mark ? <line class="g-strip-hair" x1={g.mark.x} x2={g.mark.x} y1={0.5} y2={height - 0.5} /> : null}
      <g class="g-strip-ink" filter={`url(#${fid})`}>
        {g.runs.map((d) => <path d={d} />)}
        {g.dots.map((p) => <circle cx={r2(p[0])} cy={r2(p[1])} r={1.1} />)}
      </g>
      {g.mark ? <circle class="g-strip-mark" cx={g.mark.x} cy={g.mark.y ?? height - STRIP_PAD_Y} r={2.3} /> : null}
    </svg>
  )
}
