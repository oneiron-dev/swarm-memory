import { memo } from 'preact/compat'
import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { useWidth } from '../chart'
import type { Finding, Index } from '../data'
import { buildHouses, type Houses } from './houses'
import { GOAL_COLOUR, GREY_INK, HUMAN_INK, SCORE_WORD, shortName, useChapterAdherence, useChapterMemory } from './loom'
import { moodColour, type Cuts } from './mood'
import { lensAgent, type Chapter, type Lens, type Score } from './types'

// The tapestry: the whole year as one cloth. Houses are strands, days are rows, chapters are seams with their titles
// stitched across. Strands share the width and make room as houses are founded; an empty house is a faint dotted line;
// a house nobody returns to ends. The weave renders once as a static layer; hover and selection live in an overlay.

const TOP = 40, BOTTOM = 60, RIGHT = 36, SEAM = 30, ROW = 7, TITLE_X = 16
const INK = 'rgba(31, 29, 26, 0.9)'
// the title column narrows on a phone-width cloth
const titleWidth = (w: number) => (w < 700 ? 104 : 230)
const leftOf = (w: number) => titleWidth(w) + (w < 700 ? 26 : 50)
const shortTitle = (t: string, w: number) => (w < 700 && t.length > 20 ? t.slice(0, 19).trimEnd() + '…' : t)

interface Thread { id: string; human: boolean; n: number }
interface Cell { agent: string | null; second: string | null; chat: number; v: number | null; a: number | null }
interface Geometry {
  days: number[]; rows: number[]
  seams: { chapter: Chapter; y: number; row: number }[]
  threads: Thread[]; xs: Map<string, Float32Array>; alive: Map<string, { from: number; to: number }>
  cells: Map<string, Cell[]>; maxChat: number
  wefts: { i: number; a: string; b: string; w: number; k: number; of: number }[]; maxWeft: number
  knots: { thread: string; i: number; agent: string; announced: boolean }[]
  starts: { thread: string; i: number; agent: string }[]
  swings: { thread: string; i: number; agent: string; dv: number; v: number; a: number }[]
  h: number
}

// the finding's day and strand: the earliest cite's day, the agent named in its appendix link if any
export function pinOf(f: Finding, houses: Houses, names?: Map<string, string>): { day: number | null; agent: string | null } {
  const days = f.cites.map((c) => c.day).filter((d): d is number => d != null)
  const day = days.length ? Math.min(...days) : null
  const m = /^#(?:memory|agents|rooms|timeline)\/([^/]+)/.exec(f.view ?? '')
  let agent = m && houses.houseOf.has(m[1]) ? m[1] : null
  // a finding about one agent names it in its title: pin it to that strand
  if (!agent && names) {
    const title = f.title.toLowerCase()
    let best: string | null = null, bestLen = 0
    for (const [id, name] of names) {
      const n = name.toLowerCase()
      if (n.length > bestLen && houses.houseOf.has(id) && new RegExp(`(^|[^a-z0-9])${n.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}([^a-z0-9]|$)`).test(title)) { best = id; bestLen = n.length }
    }
    agent = best
  }
  return { day, agent }
}

function geometry(idx: Index, chapters: Chapter[], houses: Houses, w: number): Geometry {
  const days = idx.b.days.map((d) => d.day)
  const dayIndex = new Map(days.map((d, i) => [d, i]))
  const seamAt = new Map<number, Chapter>()
  for (const c of chapters) { const i = dayIndex.get(c.days[0]); if (i != null) seamAt.set(i, c) }
  const rows: number[] = []
  const seams: Geometry['seams'] = []
  let y = TOP
  days.forEach((_, i) => {
    const c = seamAt.get(i)
    if (c) { y += SEAM; seams.push({ chapter: c, y: y - SEAM / 2, row: i }) }
    rows.push(y); y += ROW
  })
  const h = y - ROW + BOTTOM

  const t = idx.b.views.timeline, di = new Map(t.days.map((d, i) => [d, i]))
  const chatOn = (agent: string, d: number) => { const i = di.get(d); return i == null ? 0 : (t.series.chat?.[agent]?.[i] ?? 0) }
  const m = idx.b.views.mood, mi = m ? new Map(m.days.map((d, i) => [d, i])) : null
  const moodOn = (agent: string, d: number) => { const k = mi?.get(d), s = m?.agents[agent]; return k == null || !s ? { v: null, a: null } : { v: s.v[k], a: s.a[k] } }
  const threads: Thread[] = [{ id: 'human', human: true, n: 0 }, ...houses.houses.map((hs) => ({ id: hs.id, human: false, n: hs.n }))]
  const activeByDay = new Map(idx.b.days.map((d) => [d.day, d.active_agents]))
  let maxChat = 1
  const cells = new Map<string, Cell[]>()
  for (const th of threads) {
    const row: Cell[] = days.map((d) => {
      if (th.human) { const c = chatOn('human', d); if (c > maxChat) maxChat = c; return { agent: c > 0 ? 'human' : null, second: null, chat: c, v: null, a: null } }
      const active = (activeByDay.get(d) ?? []).filter((a) => houses.houseOf.get(a)?.id === th.id)
      if (!active.length) return { agent: null, second: null, chat: 0, v: null, a: null }
      // the newest resident holds the house from its first day; a leaver still active is the thin strand beside it
      const ranked = active.map((a) => ({ a, c: chatOn(a, d), f: houses.first.get(a) ?? 0 })).sort((x, y) => y.f - x.f)
      if (ranked[0].c > maxChat) maxChat = ranked[0].c
      const mood = moodOn(ranked[0].a, d)
      return { agent: ranked[0].a, second: ranked[1]?.a ?? null, chat: ranked[0].c, v: mood.v, a: mood.a }
    })
    cells.set(th.id, row)
  }
  // when each house is alive: from its founding row to its last resident's last row (the humans: the whole year)
  const alive = new Map<string, { from: number; to: number }>()
  for (const th of threads) {
    if (th.human) { alive.set(th.id, { from: 0, to: days.length - 1 }); continue }
    const row = cells.get(th.id)!
    let from = -1, to = -1
    row.forEach((c, i) => { if (c.agent) { if (from < 0) from = i; to = i } })
    if (from >= 0) alive.set(th.id, { from, to })
  }
  // strands share the width: at each row the living houses are spread evenly, and a strand eases toward its place
  const xs = new Map<string, Float32Array>()
  for (const th of threads) xs.set(th.id, new Float32Array(days.length).fill(NaN))
  const LEFT = leftOf(w)
  const span = Math.max(1, w - LEFT - RIGHT)
  for (let i = 0; i < days.length; i++) {
    const living = threads.filter((th) => { const a = alive.get(th.id); return a && a.from <= i && i <= a.to })
    living.forEach((th, k) => {
      const target = living.length === 1 ? LEFT + span / 2 : LEFT + (span * k) / (living.length - 1)
      const arr = xs.get(th.id)!
      const prev = i > 0 ? arr[i - 1] : NaN
      arr[i] = Number.isNaN(prev) ? target : prev + (target - prev) * 0.22
    })
  }
  const xAt = (th: string, i: number) => xs.get(th)![i]
  // knots, starts, swings
  const knots: Geometry['knots'] = [], starts: Geometry['starts'] = [], swings: Geometry['swings'] = []
  const jumps: Geometry['swings'] = []
  for (const th of threads) {
    if (th.human) continue
    const row = cells.get(th.id)!
    let prev: string | null = null, prevV: number | null = null
    row.forEach((c, i) => {
      if (c.agent && !prev) starts.push({ thread: th.id, i, agent: c.agent })
      else if (c.agent && prev && c.agent !== prev) {
        const res = houses.houseOf.get(c.agent)?.residents.find((r) => r.agent === c.agent)
        knots.push({ thread: th.id, i, agent: c.agent, announced: !!res?.announced })
        prevV = null
      }
      if (c.agent && c.v != null && c.a != null) {
        if (prevV != null && c.agent === prev) jumps.push({ thread: th.id, i, agent: c.agent, dv: c.v - prevV, v: c.v, a: c.a })
        prevV = c.v
      } else if (!c.agent) prevV = null
      if (c.agent) prev = c.agent
    })
  }
  if (jumps.length > 10) {
    const mean = jumps.reduce((s, j) => s + j.dv, 0) / jumps.length
    const sd = Math.sqrt(jumps.reduce((s, j) => s + (j.dv - mean) ** 2, 0) / jumps.length) || 1
    for (const j of jumps) if (Math.abs(j.dv - mean) >= 2.5 * sd) swings.push(j)
  }
  // wefts: who talked with whom, per day, both directions folded, drawn between the two houses at that row
  const houseOfAgent = (agent: string) => (agent === 'human' ? 'human' : houses.houseOf.get(agent)?.id ?? null)
  const per = new Map<number, Map<string, { w: number; a: string; b: string }>>()
  for (const e of idx.b.views.interactions.edges) {
    const ha = houseOfAgent(e.from), hb = houseOfAgent(e.to)
    if (!ha || !hb || ha === hb) continue
    for (const [d, wgt] of e.by_day) {
      const i = dayIndex.get(d)
      if (i == null || Number.isNaN(xAt(ha, i)) || Number.isNaN(xAt(hb, i))) continue
      const key = e.from < e.to ? `${e.from}|${e.to}` : `${e.to}|${e.from}`
      const r = per.get(i) ?? new Map()
      const cur = r.get(key)
      r.set(key, { w: (cur?.w ?? 0) + wgt, a: e.from, b: e.to })
      per.set(i, r)
    }
  }
  let maxWeft = 1
  const wefts: Geometry['wefts'] = []
  for (const [i, r] of per) {
    const top = [...r.values()].sort((x, y) => y.w - x.w).slice(0, 5)
    top.forEach((e, k) => { wefts.push({ i, a: e.a, b: e.b, w: e.w, k, of: top.length }); if (e.w > maxWeft) maxWeft = e.w })
  }
  return { days, rows, seams, threads, xs, alive, cells, maxChat, wefts, maxWeft, knots, starts, swings, h }
}

// The static weave: everything that does not change with the pointer.
const Weave = memo(function Weave({ g, w, lens, cuts, houses, adh, mem, memCuts, nameOf, current }: {
  g: Geometry; w: number; lens: Lens; cuts: Cuts; houses: Houses
  adh: Map<string, Map<number, { score: Score; note: string }>>; mem: Map<string, Map<number, number>>; memCuts: { lo: number; hi: number }
  nameOf: (id: string) => string; current: number | null
}) {
  const focus = lensAgent(lens)
  const emphasised = (agent: string | null) => (focus ? agent === focus : lens === 'humans' ? agent === 'human' : true)
  const weftShown = (a: string, b: string) => (focus ? a === focus || b === focus : lens === 'humans' ? a === 'human' || b === 'human' : true)
  const hx = (agent: string, i: number) => g.xs.get(agent === 'human' ? 'human' : houses.houseOf.get(agent)!.id)![i]
  return (
    <svg class="cloth" width={w} height={g.h} viewBox={`0 0 ${w} ${g.h}`} aria-hidden="true">
      <g class="seams">
        {g.seams.map((s) => (
          <g class={current === s.chapter.n ? 'seam on' : 'seam'} data-seam={s.chapter.n}>
            <line x1={TITLE_X} x2={w - RIGHT + 20} y1={s.y} y2={s.y} />
            <text x={TITLE_X} y={s.y - 7}>{shortTitle(s.chapter.title, w)}</text>
          </g>
        ))}
      </g>
      <g class="ink">
        <g class="wefts">
          {g.wefts.map((r) => {
            const show = weftShown(r.a, r.b)
            const xa = hx(r.a, r.i), xb = hx(r.b, r.i)
            const y = g.rows[r.i] + (r.of > 1 ? ((r.k / (r.of - 1)) - 0.5) * 4 : 0)
            const o = (show ? 0.08 + 0.34 * Math.sqrt(r.w / g.maxWeft) : 0.02) * 0.45
            return <line x1={Math.min(xa, xb)} x2={Math.max(xa, xb)} y1={y} y2={y} stroke={INK} stroke-opacity={o} stroke-width={0.7} />
          })}
        </g>
        <g class="threads">
          {g.threads.map((th) => {
            const row = g.cells.get(th.id)!
            const xr = g.xs.get(th.id)!
            const a = g.alive.get(th.id)
            if (!a) return null
            const segs = []
            for (let i = a.from; i <= a.to; i++) {
              const c = row[i]
              const present = c.agent != null
              const x0 = i === a.from ? xr[i] : xr[i - 1], x1 = xr[i]
              const y0 = i === a.from ? g.rows[i] - 4 : g.rows[i - 1], y1 = g.rows[i]
              const on = present && emphasised(c.agent)
              const kept = lens === 'memory' && c.agent && !th.human ? mem.get(c.agent)?.get(g.days[i]) ?? null : null
              const score: Score | null = lens === 'goal' && c.agent && !th.human ? adh.get(c.agent)?.get(g.days[i])?.score ?? null : null
              const width = !present ? 0.7 : score === 'idle' ? 1.2 : 1.5 + 4 * Math.sqrt(c.chat / g.maxChat)
              const colour = !present ? 'rgba(31, 29, 26, 0.32)'
                : !on ? GREY_INK
                  : th.human ? (lens === 'goal' ? GREY_INK : HUMAN_INK)
                    : lens === 'goal' ? (score ? GOAL_COLOUR[score] : GREY_INK)
                      : lens === 'memory' && kept != null
                        ? (kept >= memCuts.hi ? 'oklch(0.34 0.03 60)' : kept >= memCuts.lo ? 'oklch(0.52 0.06 45)' : 'oklch(0.55 0.17 25)')
                        : moodColour(c.v, c.a, cuts)
              // an empty house is a dotted hairline; a frayed memory a short dash
              const dash = !present ? '1 3' : lens === 'memory' && kept != null && kept < memCuts.lo ? '2 2' : undefined
              segs.push(<line x1={x0} x2={x1} y1={y0} y2={y1} stroke={colour} stroke-width={width} stroke-linecap="round" stroke-dasharray={dash} />)
              if (lens !== 'goal' && present && !th.human && on) {
                const sc = adh.get(c.agent!)?.get(g.days[i])?.score
                if (sc === 'off') segs.push(<line class="tick-off" x1={x1 + 3.5} x2={x1 + 6.5} y1={y1 - 2} y2={y1 - 2} />)
              }
            }
            return <g class="thread">{segs}</g>
          })}
        </g>
        <g class="swings">
          {g.swings.map((s) => <circle cx={g.xs.get(s.thread)![s.i]} cy={g.rows[s.i]} r={2.6} fill="none" stroke={moodColour(s.v, s.a, cuts)} stroke-width={1.3} />)}
        </g>
        <g class="marks">
          {g.starts.map((s, k) => {
            const x = g.xs.get(s.thread)![s.i] + 2, y = g.rows[s.i] - 6 - (k % 2) * 9
            return <text class="label start" x={x} y={y} transform={`rotate(-64 ${x} ${y})`}>{nameOf(s.agent)}</text>
          })}
          {g.knots.map((k) => (
            <g class="knot">
              <circle cx={g.xs.get(k.thread)![k.i]} cy={g.rows[k.i]} r={3} class={k.announced ? 'knot-ring announced' : 'knot-ring'} />
              <text class="label swap" x={g.xs.get(k.thread)![k.i] + 6} y={g.rows[k.i] + 3.5}>{nameOf(k.agent)}</text>
            </g>
          ))}
          <text class="label start" x={g.xs.get('human')![0] + 2} y={g.rows[0] - 6} transform={`rotate(-64 ${g.xs.get('human')![0] + 2} ${g.rows[0] - 6})`}>humans</text>
        </g>
      </g>
    </svg>
  )
})

export function Tapestry({ idx, chapters, lens, cuts, day, open, current, findings, onCurrent, onPick, windowed, eyeDay, box }: {
  idx: Index; chapters: Chapter[]; lens: Lens; cuts: Cuts; day: number | null; open: string | null
  current: number | null; findings: Finding[] | null; onCurrent: (n: number) => void; onPick: (day: number, agent: string | null) => void
  // in a window: the page's scroll does not pick the chapter; the reader's eye (a day) lights its row, the rows ahead stay pale, and the box pans to it
  windowed?: boolean; eyeDay?: number | null; box?: { current: HTMLElement | null }
}) {
  const [ref, w] = useWidth<HTMLDivElement>(700)
  const houses = useMemo(() => buildHouses(idx.b), [idx])
  const g = useMemo(() => geometry(idx, chapters, houses, w), [idx, chapters, houses, w])
  const nameOf = (id: string) => (id === 'human' ? 'humans' : shortName(idx.agent.get(id)?.name ?? id))
  const residentIds = useMemo(() => idx.b.agents.filter((a) => a.id !== 'human').map((a) => a.id), [idx])
  const [wantAdh, setWantAdh] = useState(lens === 'goal')
  useEffect(() => { const t = window.setTimeout(() => setWantAdh(true), 800); return () => window.clearTimeout(t) }, [])
  const adh = useChapterAdherence(idx, wantAdh || lens === 'goal' ? residentIds : [])
  const names = useMemo(() => new Map(idx.b.agents.filter((a) => a.id !== 'human').map((a) => [a.id, a.name])), [idx])
  const pins = useMemo(() => (findings ?? []).map((f, k) => ({ f, k, ...pinOf(f, houses, names) })).filter((p) => p.day != null && g.days.includes(p.day)), [findings, houses, names, g])
  const mem = useChapterMemory(idx, lens === 'memory' ? residentIds : [])
  const memCuts = useMemo(() => {
    const vals: number[] = []
    for (const byDay of mem.values()) for (const v of byDay.values()) vals.push(v)
    vals.sort((a, b) => a - b)
    const q = (p: number) => (vals.length ? vals[Math.min(vals.length - 1, Math.floor(p * (vals.length - 1)))] : 0.5)
    return { lo: q(1 / 3), hi: q(2 / 3) }
  }, [mem])

  // in a window the cloth follows the reader: the eye's row sits at 42% of the box; a hand on the box pauses the pan for a moment
  const eyeRow = eyeDay == null ? -1 : g.days.indexOf(eyeDay)
  const pausedUntil = useRef(0)
  useEffect(() => {
    const el = box?.current
    if (!windowed || !el) return
    const pause = () => { pausedUntil.current = performance.now() + 1500 }
    el.addEventListener('wheel', pause, { passive: true })
    el.addEventListener('touchmove', pause, { passive: true })
    return () => { el.removeEventListener('wheel', pause); el.removeEventListener('touchmove', pause) }
  }, [windowed, box?.current])
  useEffect(() => {
    const el = box?.current, fig = ref.current
    if (!windowed || !el || !fig || eyeRow < 0) return
    const figTop = fig.getBoundingClientRect().top - el.getBoundingClientRect().top + el.scrollTop
    const target = Math.max(0, figTop + g.rows[eyeRow] - el.clientHeight * 0.42)
    const still = matchMedia('(prefers-reduced-motion: reduce)').matches
    let frame = 0
    const step = () => {
      if (performance.now() < pausedUntil.current) { frame = requestAnimationFrame(step); return }
      const d = target - el.scrollTop
      if (Math.abs(d) < 0.6 || still) { el.scrollTop = target; return }
      el.scrollTop += d * 0.16
      frame = requestAnimationFrame(step)
    }
    frame = requestAnimationFrame(step)
    return () => cancelAnimationFrame(frame)
  }, [windowed, box?.current, eyeRow, g])

  // which chapter is under the eye: the last seam above the viewport's upper third; read once per frame
  useEffect(() => {
    if (windowed) return
    let frame = 0
    const on = () => {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(() => {
        const el = ref.current
        if (!el) return
        const top = el.getBoundingClientRect().top
        const probe = window.innerHeight * 0.38 - top
        let best: number | null = null
        for (const s of g.seams) if (s.y <= probe) best = s.chapter.n
        if (best == null && g.seams.length) best = g.seams[0].chapter.n
        if (best != null && best !== current) onCurrent(best)
      })
    }
    on()
    window.addEventListener('scroll', on, { passive: true })
    return () => { window.removeEventListener('scroll', on); cancelAnimationFrame(frame) }
  }, [g, current, onCurrent, windowed])

  const [hover, setHover] = useState<{ row: number; thread: string | null; px: number; py: number } | null>(null)
  const svgRef = useRef<SVGSVGElement>(null)
  const locate = (e: MouseEvent) => {
    const el = svgRef.current
    if (!el) return null
    const r = el.getBoundingClientRect()
    const x = e.clientX - r.left, y = e.clientY - r.top
    let row = 0, bestDy = Infinity
    for (let i = 0; i < g.rows.length; i++) { const dy = Math.abs(g.rows[i] - y); if (dy < bestDy) { bestDy = dy; row = i } }
    let thread: string | null = null, best = 7
    for (const th of g.threads) { const tx = g.xs.get(th.id)![row]; if (Number.isNaN(tx)) continue; const d = Math.abs(tx - x); if (d < best) { best = d; thread = th.id } }
    return { row, thread, px: x, py: y }
  }
  const residentAt = (thread: string | null, row: number) => (thread ? g.cells.get(thread)?.[row]?.agent ?? null : null)
  const dayRow = day == null ? -1 : g.days.indexOf(day)
  const openThread = open ? (open === 'human' ? 'human' : houses.houseOf.get(open)?.id ?? null) : null

  return (
    <figure class="tapestry" ref={ref}>
      <div class="cloth-stack" style={{ height: `${g.h}px`, ...(eyeRow >= 0 ? { '--eye-y': `${g.rows[eyeRow] + ROW / 2}px` } : {}) }}>
        <Weave g={g} w={w} lens={lens} cuts={cuts} houses={houses} adh={adh} mem={mem} memCuts={memCuts} nameOf={nameOf} current={current} />
        <svg
          ref={svgRef} class="overlay" width={w} height={g.h} viewBox={`0 0 ${w} ${g.h}`}
          onMouseMove={(e) => setHover(locate(e as unknown as MouseEvent))}
          onMouseLeave={() => setHover(null)}
          onClick={(e) => { const p = locate(e as unknown as MouseEvent); if (p) onPick(g.days[p.row], residentAt(p.thread, p.row)) }}
        >
          {eyeRow >= 0 ? <rect class="pale" x={0} y={g.rows[eyeRow] + ROW / 2} width={w} height={Math.max(0, g.h - g.rows[eyeRow] - ROW / 2)} /> : null}
          {eyeRow >= 0 && eyeRow !== dayRow ? <rect class="row-eye" x={TITLE_X} y={g.rows[eyeRow] - ROW / 2} width={w - TITLE_X - RIGHT + 20} height={ROW} /> : null}
          {dayRow >= 0 ? <rect class="row-on" x={TITLE_X} y={g.rows[dayRow] - ROW / 2} width={w - TITLE_X - RIGHT + 20} height={ROW} /> : null}
          {hover && hover.row !== dayRow ? <rect class="row-hover" x={TITLE_X} y={g.rows[hover.row] - ROW / 2} width={w - TITLE_X - RIGHT + 20} height={ROW} /> : null}
          {openThread && dayRow >= 0 && !Number.isNaN(g.xs.get(openThread)![dayRow]) ? <circle class="strand-on" cx={g.xs.get(openThread)![dayRow]} cy={g.rows[dayRow]} r={7} /> : null}
          {dayRow >= 0 ? <text class="daynum on" x={TITLE_X + titleWidth(w)} y={g.rows[dayRow] + 3.5} text-anchor="end">{g.days[dayRow]}</text> : null}
          {hover && hover.row !== dayRow ? <text class="daynum" x={TITLE_X + titleWidth(w)} y={g.rows[hover.row] + 3.5} text-anchor="end">{g.days[hover.row]}</text> : null}
        </svg>
        {pins.map((p) => {
          const i = g.days.indexOf(p.day!)
          const hid = p.agent ? houses.houseOf.get(p.agent)!.id : null
          const x = hid && !Number.isNaN(g.xs.get(hid)![i]) ? g.xs.get(hid)![i] : TITLE_X + titleWidth(w) + 16
          return (
            <button type="button" class={`pin${hid ? '' : ' loose'}`} style={{ left: `${x}px`, top: `${g.rows[i]}px` }} title={`Found here: ${p.f.title}`} aria-label={`a finding starts here: ${p.f.title}`}
              onClick={(e) => { e.stopPropagation(); onPick(p.day!, null) }}>
              <span aria-hidden="true" />{hid ? null : <em>found here</em>}
            </button>
          )
        })}
        {hover ? (() => {
          const agent = residentAt(hover.thread, hover.row)
          const th = g.threads.find((x) => x.id === hover.thread)
          const c = hover.thread ? g.cells.get(hover.thread)?.[hover.row] : undefined
          const sc = agent && lens === 'goal' ? adh.get(agent)?.get(g.days[hover.row]) : undefined
          const left = Math.min(hover.px + 14, w - 240)
          return (
            <div class="cloth-tip" style={{ left: `${left}px`, top: `${hover.py - 10}px` }}>
              {agent ? <span>{nameOf(agent)}{th && !th.human ? `, house ${th.n}` : ''} · day {g.days[hover.row]} · {(c?.chat ?? 0).toLocaleString('en-US')} messages{sc?.score ? ` · ${SCORE_WORD[sc.score]}` : ''}</span>
                : <span class="faint">day {g.days[hover.row]}{th ? (th.human ? ' · the humans' : ` · house ${th.n}, standing empty`) : ''}</span>}
            </div>
          )
        })() : null}
      </div>
    </figure>
  )
}
