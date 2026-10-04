import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import type { Finding, Index } from '../data'
import type { Houses } from './houses'
import { SCORE_WORD, useChapterAdherence, useChapterMemory } from './loom'
import type { Cuts } from './mood'
import { buildScene, type Moment, type VillageScene } from './scene'
import { pinOf } from './tapestry'
import type { Lens, Stage } from './types'

// data/pairs.json: one sentence per talking pair per day; missing until the data lands, and the page says who talked without it
let pairsCache: PairsFile | null | undefined
function usePairs(): PairsFile | null {
  const [p, setP] = useState<PairsFile | null>(pairsCache ?? null)
  useEffect(() => {
    if (pairsCache !== undefined) { setP(pairsCache); return }
    fetch('./data/pairs.json').then((r) => (r.ok ? r.json() : null)).then((j) => { pairsCache = j && j.days ? (j as PairsFile) : null; setP(pairsCache) }).catch(() => { pairsCache = null; setP(null) })
  }, [])
  return p
}
import { drawVillage, layoutHouses, pairSpots } from './village'
import type { PairsFile } from './types'

// The plate: the village drawn in ink on the painted ground. The scroll sets the state (the day, who lives where, who is
// away, the goal on the board, who talks); a clock runs the life (smoke, walking, swaying). A house or its figure is a
// click: the resident's margin note for that day. The scene comes from scene.ts; the pen (village.ts) only draws.
type Draw = (ctx: CanvasRenderingContext2D, scene: VillageScene, w: number, h: number, clock?: number) => void
const draw = drawVillage as unknown as Draw

export function Plate({ idx, houses, day, t, lens, cuts, findings, board, stage, beat, onPick }: {
  idx: Index; houses: Houses; day: number; t: number; lens: Lens; cuts: Cuts; findings: Finding[] | null
  board: string | null; stage?: Stage | null; beat?: number; onPick?: (day: number, agent: string | null) => void
}) {
  const ref = useRef<HTMLCanvasElement>(null)
  const residentIds = useMemo(() => idx.b.agents.filter((a) => a.id !== 'human').map((a) => a.id), [idx])
  const adh = useChapterAdherence(idx, residentIds)
  const mem = useChapterMemory(idx, lens === 'memory' ? residentIds : [])
  const memCuts = useMemo(() => {
    const vals: number[] = []
    for (const byDay of mem.values()) for (const v of byDay.values()) vals.push(v)
    vals.sort((a, b) => a - b)
    const q = (p: number) => (vals.length ? vals[Math.min(vals.length - 1, Math.floor(p * (vals.length - 1)))] : 0.5)
    return { lo: q(1 / 3), hi: q(2 / 3) }
  }, [mem])
  const pinDays = useMemo(() => {
    const m = new Map<number, string | null>()
    for (const f of findings ?? []) { const p = pinOf(f, houses); if (p.day != null && !m.has(p.day)) m.set(p.day, p.agent) }
    return m
  }, [findings, houses])
  // the stage: the chapter's props stand all week; a moment plays once its beat has lit, from the clock second it lit
  const start = useRef(performance.now())
  const lit = useRef<Map<string, number>>(new Map())
  const stageKey = stage ? JSON.stringify(stage) : ''
  useEffect(() => { lit.current = new Map() }, [stageKey, day])
  const curBeat = beat ?? -1
  const moments = useMemo<Moment[]>(() => {
    if (!stage?.moments?.length) return []
    const now = (performance.now() - start.current) / 1000
    const out: Moment[] = []
    for (const m of stage.moments) {
      if (m.beat > curBeat) continue
      const key = `${m.beat}:${m.kind}`
      if (!lit.current.has(key)) lit.current.set(key, m.beat === curBeat ? now : 0)
      const hs = m.agents.map((a) => houses.houseOf.get(a)?.n ?? (a === 'human' ? 0 : null)).filter((n): n is number => n != null)
      out.push({ kind: m.kind, houses: hs, label: m.label, start: lit.current.get(key)! })
    }
    return out
  }, [stageKey, curBeat, houses, day])
  // only the houses founded by this day stand: the four founders large at first, the village densifying across the year
  const scene = useMemo(() => {
    const s = buildScene({ idx, houses, day, t, lens, cuts, adh, mem, memCuts, pinDays, board, props: stage?.props ?? [], moments })
    return { ...s, houses: s.houses.filter((h) => h.founded) }
  }, [idx, houses, day, t, lens, cuts, adh, mem, memCuts, pinDays, board, stageKey, moments])

  // the clock starts when a day comes under the eye; the scene's own changes (a lens, the data arriving) keep it running
  useEffect(() => { start.current = performance.now() }, [day])
  const size = useRef({ w: 0, h: 0 })
  useEffect(() => {
    const c = ref.current
    const box = c?.parentElement
    if (!c || !box) return
    const still = matchMedia('(prefers-reduced-motion: reduce)').matches
    const paint = (clock: number) => {
      const r = box.getBoundingClientRect()
      const dpr = Math.min(2, window.devicePixelRatio || 1)
      const w = Math.max(10, Math.round(r.width)), h = Math.max(10, Math.round(r.height))
      if (c.width !== w * dpr || c.height !== h * dpr) { c.width = w * dpr; c.height = h * dpr; c.style.width = `${w}px`; c.style.height = `${h}px` }
      size.current = { w, h }
      const ctx = c.getContext('2d')
      if (!ctx) return
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      ctx.clearRect(0, 0, w, h)
      draw(ctx, scene, w, h, clock)
    }
    let frame = 0, last = 0
    const loop = (now: number) => {
      frame = requestAnimationFrame(loop)
      if (document.hidden || now - last < 33) return
      last = now
      paint((now - start.current) / 1000)
    }
    if (still) paint(8)
    else frame = requestAnimationFrame(loop)
    const ro = new ResizeObserver(() => paint(still ? 8 : (performance.now() - start.current) / 1000))
    ro.observe(box)
    return () => { cancelAnimationFrame(frame); ro.disconnect() }
  }, [scene])

  // a house, its figure, or the visitor under the pointer
  const pairsFile = usePairs()
  // i >= 0 a house; -1 the visitor; -2 - k the k-th talking pair
  const [hover, setHover] = useState<{ i: number; x: number; y: number } | null>(null)
  const locate = (e: MouseEvent): { i: number; x: number; y: number } | null => {
    const c = ref.current
    if (!c) return null
    const r = c.getBoundingClientRect()
    const x = e.clientX - r.left, y = e.clientY - r.top
    const { w, h } = size.current
    if (!w || !h || !scene.houses.length) return null
    const lay = layoutHouses(scene.houses.length, w, h)
    // talking pairs first: their bubble or either figure
    const spots = pairSpots(lay, scene)
    for (let k = 0; k < spots.length; k++) {
      const sp = spots[k]
      const inBubble = ((x - sp.bubble.x) / Math.max(1, sp.bubble.rx)) ** 2 + ((y - sp.bubble.y) / Math.max(1, sp.bubble.ry)) ** 2 <= 1.15
      const near = (pt: [number, number]) => Math.abs(x - pt[0]) <= sp.size * 0.4 && y <= pt[1] + 2 && y >= pt[1] - sp.size * 1.05
      if (inBubble || near(sp.left) || near(sp.right)) return { i: -2 - k, x, y }
    }
    for (let i = lay.houses.length - 1; i >= 0; i--) {
      const s = lay.houses[i]
      if (x >= s.left - 2 && x <= s.right + 2 && y >= s.top - 2 && y <= s.y + lay.figure * 0.25) return { i, x, y }
    }
    if (scene.visitor > 0 && Math.hypot(x - lay.visitor[0], y - (lay.visitor[1] - lay.figure * 0.5)) <= lay.figure * 0.8) return { i: -1, x, y }
    return null
  }
  const pairAt = (i: number) => {
    if (i > -2) return null
    const { w, h } = size.current
    const sp = pairSpots(layoutHouses(scene.houses.length, w, h), scene)[-2 - i]
    return sp ? scene.pairs.find((p) => (p.a === sp.a && p.b === sp.b) || (p.a === sp.b && p.b === sp.a)) ?? null : null
  }
  const agentAt = (i: number) => (i === -1 ? 'human' : i <= -2 ? (() => { const p = pairAt(i); return p ? (p.agents[0] === 'human' ? p.agents[1] : p.agents[0]) : null })() : scene.houses[i]?.agent ?? null)
  const name = (id: string) => (id === 'human' ? 'the humans' : idx.agent.get(id)?.name ?? id)
  const tip = (() => {
    if (!hover) return null
    if (hover.i <= -2) {
      const p = pairAt(hover.i)
      if (!p) return null
      const said = pairsFile?.days[String(day)]?.find((x) => (x.a === p.agents[0] && x.b === p.agents[1]) || (x.a === p.agents[1] && x.b === p.agents[0]))?.text
      return said ? `${name(p.agents[0])} and ${name(p.agents[1])}: ${said}` : `${name(p.agents[0])} and ${name(p.agents[1])} talked most that day`
    }
    const id = agentAt(hover.i)
    if (!id) return hover.i >= 0 ? `house ${scene.houses[hover.i].n}, standing empty` : null
    const k = idx.b.views.timeline.days.indexOf(day)
    const chat = k < 0 ? 0 : idx.b.views.timeline.series.chat?.[id]?.[k] ?? 0
    const sc = id !== 'human' ? adh.get(id)?.get(day)?.score : null
    return `${name(id)} · day ${day} · ${chat.toLocaleString('en-US')} messages${sc ? ` · ${SCORE_WORD[sc]}` : ''}`
  })()
  return (
    <>
      <canvas ref={ref} aria-hidden="true" class={hover ? 'hot' : ''}
        onMouseMove={(e) => setHover(locate(e as unknown as MouseEvent))}
        onMouseLeave={() => setHover(null)}
        onClick={(e) => { const p = locate(e as unknown as MouseEvent); const id = p ? agentAt(p.i) : null; if (id && onPick) onPick(day, id) }} />
      {hover && tip ? <div class="plate-tip" style={{ left: `${Math.min(hover.x + 12, size.current.w - 230)}px`, top: `${hover.y - 10}px` }}>{tip}</div> : null}
    </>
  )
}
