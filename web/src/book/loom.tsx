import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { useWidth } from '../chart'
import { load, type Index, type MemoryFile } from '../data'
import { buildHouses } from './houses'
import { moodColour, type Cuts } from './mood'
import { lensAgent, type AdherenceFile, type Chapter, type Lens, type Score } from './types'

export const SCORE_WORD: Record<string, string> = { on: 'on the goal', partly: 'partly on the goal', off: 'elsewhere', idle: 'idle' }
// goal ink: on = ink, partly = faint, off = deep vermilion, idle = a thin grey
export const GOAL_COLOUR: Record<string, string> = { on: 'oklch(0.30 0.025 60)', partly: 'oklch(0.60 0.035 60)', off: 'oklch(0.55 0.17 30)', idle: 'oklch(0.62 0.02 60)' }
export const HUMAN_INK = '#3f7a74'
export const GREY_INK = 'rgba(31, 29, 26, 0.16)'

// The loom: the village's houses are threads running down the page, one row per day. A thread's colour is its resident's
// mood that day, its thickness how much it spoke; a weft between two threads means their residents talked that day. When a
// model is swapped out, the thread ties a knot and the new resident's name sits beside it. The humans are one teal thread.

const HUMAN = '#3f7a74'
const INK = 'rgba(31, 29, 26, 0.9)'
const GREY = 'rgba(31, 29, 26, 0.16)'
const TOP = 94, BOTTOM = 18, LEFT = 44, RIGHT = 56

export function shortName(name: string): string {
  return name.replace(/^Claude\s+/i, '').replace(/^\[Temporary\]\s*/i, '').replace(/\s+Preview$/i, '')
}

interface Thread { id: string; human: boolean; n: number }
interface Cell { agent: string | null; second: string | null; chat: number; chat2: number; v: number | null; a: number | null }

export function Loom({ idx, chapter, lens, cuts, day, open, onPick }: {
  idx: Index; chapter: Chapter; lens: Lens; cuts: Cuts; day: number | null; open: string | null
  onPick: (day: number, agent: string | null) => void
}) {
  const [ref, w] = useWidth<HTMLDivElement>(720)
  const days = chapter.days
  const n = days.length
  const rowH = Math.max(11, Math.min(56, Math.round(440 / Math.max(1, n))))
  const h = TOP + rowH * Math.max(1, n - 1) + BOTTOM + 8
  const focus = lensAgent(lens)
  const houses = useMemo(() => buildHouses(idx.b), [idx])
  const nameOf = (id: string) => (id === 'human' ? 'humans' : shortName(idx.agent.get(id)?.name ?? id))

  // the day's talk per agent, and the mood
  const t = idx.b.views.timeline
  const di = useMemo(() => new Map(t.days.map((d, i) => [d, i])), [t])
  const chatOn = (agent: string, d: number) => { const i = di.get(d); return i == null ? 0 : (t.series.chat?.[agent]?.[i] ?? 0) }
  const m = idx.b.views.mood
  const mi = useMemo(() => (m ? new Map(m.days.map((d, i) => [d, i])) : null), [m])
  const moodOn = (agent: string, d: number): { v: number | null; a: number | null } => {
    const k = mi?.get(d), s = m?.agents[agent]
    return k == null || !s ? { v: null, a: null } : { v: s.v[k], a: s.a[k] }
  }

  // which houses are in this chapter, and who lives in each on each day
  const { threads, cells, maxChat } = useMemo(() => {
    const present = new Map<string, Set<string>>()   // house id -> residents seen in the chapter
    for (const d of days) for (const a of idx.day.get(d)?.active_agents ?? []) {
      const hs = houses.houseOf.get(a)
      if (!hs) continue
      const set = present.get(hs.id) ?? new Set<string>()
      set.add(a); present.set(hs.id, set)
    }
    const list: Thread[] = houses.houses.filter((hs) => present.has(hs.id)).map((hs) => ({ id: hs.id, human: false, n: hs.n }))
    const humanTalk = days.some((d) => chatOn('human', d) > 0)
    if (humanTalk) list.push({ id: 'human', human: true, n: 0 })
    let maxChat = 1
    const cells = new Map<string, Cell[]>()
    for (const th of list) {
      const row: Cell[] = days.map((d) => {
        if (th.human) { const c = chatOn('human', d); if (c > maxChat) maxChat = c; return { agent: c > 0 ? 'human' : null, second: null, chat: c, chat2: 0, v: null, a: null } }
        const active = (idx.day.get(d)?.active_agents ?? []).filter((a) => houses.houseOf.get(a)?.id === th.id)
        if (!active.length) return { agent: null, second: null, chat: 0, chat2: 0, v: null, a: null }
        const ranked = active.map((a) => ({ a, c: chatOn(a, d), f: houses.first.get(a) ?? 0 })).sort((x, y) => y.f - x.f)
        const prime = ranked[0], sec = ranked[1]
        if (prime.c > maxChat) maxChat = prime.c
        const mood = moodOn(prime.a, d)
        return { agent: prime.a, second: sec?.a ?? null, chat: prime.c, chat2: sec?.c ?? 0, v: mood.v, a: mood.a }
      })
      cells.set(th.id, row)
    }
    return { threads: list, cells, maxChat }
  }, [idx, houses, days])

  const xOf = useMemo(() => {
    const map = new Map<string, number>()
    const span = Math.max(1, w - LEFT - RIGHT)
    threads.forEach((th, i) => map.set(th.id, threads.length === 1 ? LEFT + span / 2 : LEFT + (span * i) / (threads.length - 1)))
    return map
  }, [threads, w])
  const xOfAgent = (agent: string) => xOf.get(agent === 'human' ? 'human' : houses.houseOf.get(agent)?.id ?? '')
  const yOf = (i: number) => TOP + i * rowH

  // wefts: who talked with whom, per day, both directions folded, drawn between the two houses
  const wefts = useMemo(() => {
    const dayIndex = new Map(days.map((d, i) => [d, i]))
    const per = new Map<number, Map<string, { w: number; a: string; b: string }>>()
    for (const e of idx.b.views.interactions.edges) {
      const xa = xOfAgent(e.from), xb = xOfAgent(e.to)
      if (xa == null || xb == null || xa === xb) continue
      for (const [d, wgt] of e.by_day) {
        const i = dayIndex.get(d)
        if (i == null) continue
        const key = e.from < e.to ? `${e.from}|${e.to}` : `${e.to}|${e.from}`
        const row = per.get(i) ?? new Map()
        const cur = row.get(key)
        row.set(key, { w: (cur?.w ?? 0) + wgt, a: e.from, b: e.to })
        per.set(i, row)
      }
    }
    let max = 1
    const rows: { i: number; a: string; b: string; w: number; k: number; of: number }[] = []
    for (const [i, row] of per) {
      const top = [...row.values()].sort((x, y) => y.w - x.w).slice(0, 10)
      top.forEach((r, k) => { rows.push({ i, a: r.a, b: r.b, w: r.w, k, of: top.length }); if (r.w > max) max = r.w })
    }
    return { rows, max }
  }, [idx, days, xOf, houses])

  // memory lens: how much of yesterday's memory each resident kept, ranked within the chapter so every chapter shows contrast
  const residentIds = useMemo(() => { const s = new Set<string>(); for (const row of cells.values()) for (const c of row) { if (c.agent && c.agent !== 'human') s.add(c.agent) } return [...s] }, [cells])
  const mem = useChapterMemory(idx, lens === 'memory' ? residentIds : [])
  const adh = useChapterAdherence(idx, lens === 'goal' ? residentIds : [])
  const goalTally = useMemo(() => {
    const t = { on: 0, partly: 0, off: 0, idle: 0, n: 0 }
    for (const byDay of adh.values()) for (const d of days) { const r = byDay.get(d); if (r?.score) { t[r.score]++; t.n++ } }
    return t
  }, [adh, days])
  const memCuts = useMemo(() => {
    const vals: number[] = []
    for (const byDay of mem.values()) for (const d of days) { const k = byDay.get(d); if (k != null) vals.push(k) }
    vals.sort((a, b) => a - b)
    const q = (p: number) => (vals.length ? vals[Math.min(vals.length - 1, Math.floor(p * (vals.length - 1)))] : 0.5)
    return { lo: q(1 / 3), hi: q(2 / 3) }
  }, [mem, days])

  const [hover, setHover] = useState<{ row: number; thread: string | null } | null>(null)
  const svgRef = useRef<SVGSVGElement>(null)
  const locate = (e: MouseEvent): { row: number; thread: string | null } | null => {
    const el = svgRef.current
    if (!el) return null
    const r = el.getBoundingClientRect()
    const x = ((e.clientX - r.left) / r.width) * w, y = ((e.clientY - r.top) / r.height) * h
    const row = Math.max(0, Math.min(n - 1, Math.round((y - TOP) / rowH)))
    let thread: string | null = null, best = 9
    for (const [id, tx] of xOf) { const d = Math.abs(tx - x); if (d < best) { best = d; thread = id } }
    return { row, thread }
  }
  const residentAt = (thread: string | null, row: number): string | null => (thread ? cells.get(thread)?.[row]?.agent ?? null : null)

  const dayRow = day == null ? -1 : days.indexOf(day)
  const emphasised = (agent: string | null) => {
    if (focus) return agent === focus
    if (lens === 'humans') return agent === 'human'
    return true
  }
  const weftShown = (a: string, b: string) => {
    if (focus) return a === focus || b === focus
    if (lens === 'humans') return a === 'human' || b === 'human'
    return true
  }
  const dense = Math.min(1, Math.sqrt(10 / Math.max(1, n)))

  return (
    <figure class="loom" ref={ref}>
      <p class="loom-key">
        Downward is time. Each strand is a house of the village, named for the model living in it; a knot marks a new resident. Width follows how much it said; a crossing marks a mention or a reply between two.
        {lens === 'goal' ? ' Ink is on the goal, faint is partly, vermilion is elsewhere: the summarising model\'s classification, not the quality or value of the work.' : lens === 'memory' ? ' Dark kept most of the day-before\'s named things; carmine and frayed kept least.' : ' Colour is mood: cool to warm, pale to strong.'}
      </p>
      <svg
        key={`${chapter.n}:${lens}`} class="weave"
        ref={svgRef} width={w} height={h} viewBox={`0 0 ${w} ${h}`} role="img"
        aria-label={`${threads.length} threads over ${n} days: colour is mood, thickness is how much each spoke, a cross-thread means they talked`}
        onMouseMove={(e) => setHover(locate(e as unknown as MouseEvent))}
        onMouseLeave={() => setHover(null)}
        onClick={(e) => { const p = locate(e as unknown as MouseEvent); if (p) onPick(days[p.row], residentAt(p.thread, p.row)) }}
      >
        <defs>
          {/* the inked-charts "rule" grade: a little grain eaten out of the ink, a sub-pixel warp; rendered once, not per frame */}
          <filter id="loom-ink" x="-4%" y="-4%" width="108%" height="108%" color-interpolation-filters="sRGB">
            <feTurbulence type="fractalNoise" baseFrequency="0.72" numOctaves="2" seed="5" result="grain" />
            <feColorMatrix in="grain" type="luminanceToAlpha" result="ga" />
            <feComposite in="SourceGraphic" in2="ga" operator="arithmetic" k1="0" k2="1" k3="-0.12" k4="0" result="inked" />
            <feTurbulence type="fractalNoise" baseFrequency="0.07" numOctaves="2" seed="3" result="warp" />
            <feDisplacementMap in="inked" in2="warp" scale="0.9" xChannelSelector="R" yChannelSelector="G" />
          </filter>
        </defs>
        {dayRow >= 0 ? <rect class="row-on" x={0} y={yOf(dayRow) - rowH / 2} width={w} height={rowH} /> : null}
        {hover && hover.row !== dayRow ? <rect class="row-hover" x={0} y={yOf(hover.row) - rowH / 2} width={w} height={rowH} /> : null}
        <g class="ink" filter="url(#loom-ink)">
          <g class="wefts">
            {wefts.rows.map((r) => {
              const show = weftShown(r.a, r.b)
              const xa = xOfAgent(r.a)!, xb = xOfAgent(r.b)!
              const spread = Math.min(rowH - 3, 12)
              const y = yOf(r.i) + (r.of > 1 ? ((r.k / (r.of - 1)) - 0.5) * spread : 0)
              const chosen = r.i === dayRow
              const o = (show ? 0.12 + 0.45 * Math.sqrt(r.w / wefts.max) : 0.04) * (chosen ? 1 : dense) * (chosen ? 1.6 : 1)
              return <line x1={Math.min(xa, xb)} x2={Math.max(xa, xb)} y1={y} y2={y} stroke={INK} stroke-opacity={o} stroke-width={show ? 0.8 : 0.5} />
            })}
          </g>
          <g class="threads">
            {threads.map((th) => {
              const x = xOf.get(th.id)!
              const row = cells.get(th.id)!
              const hotThread = hover?.thread === th.id || (open != null && row.some((c) => c.agent === open))
              const first = row.find((c) => c.agent)?.agent ?? null
              const knots: { i: number; agent: string; announced: boolean }[] = []
              let prev: string | null = null
              row.forEach((c, i) => {
                if (c.agent && prev && c.agent !== prev && !th.human) {
                  const res = houses.houseOf.get(c.agent)?.residents.find((r) => r.agent === c.agent)
                  knots.push({ i, agent: c.agent, announced: !!res?.announced })
                }
                if (c.agent) prev = c.agent
              })
              return (
                <g class={hotThread ? 'thread hot' : 'thread'}>
                  {row.map((c, i) => {
                    // the segment that ends at this day's row; the first day gets a short lead-in, the last a short tail
                    const y0 = i === 0 ? yOf(0) - 8 : yOf(i - 1), y1 = i === n - 1 ? yOf(i) + 8 : yOf(i)
                    const present = c.agent != null
                    const on = present && emphasised(c.agent)
                    const kept = lens === 'memory' && c.agent && !th.human ? mem.get(c.agent)?.get(days[i]) ?? null : null
                    const score: Score | null = lens === 'goal' && c.agent && !th.human ? adh.get(c.agent)?.get(days[i])?.score ?? null : null
                    const width = !present ? 0.7 : score === 'idle' ? 1.1 : 1.2 + 5.6 * Math.sqrt(c.chat / maxChat)
                    const colour = !present ? 'rgba(31, 29, 26, 0.34)'
                      : !on ? GREY
                        : th.human ? (lens === 'goal' ? GREY : HUMAN)
                          : lens === 'goal'
                            ? (score ? GOAL_COLOUR[score] : GREY)
                            : lens === 'memory' && kept != null
                              ? (kept >= memCuts.hi ? 'oklch(0.34 0.03 60)' : kept >= memCuts.lo ? 'oklch(0.52 0.06 45)' : 'oklch(0.55 0.17 25)')
                              : moodColour(c.v, c.a, cuts)
                    const dash = lens === 'memory' && kept != null && kept < memCuts.lo ? '3 3' : undefined
                    const bow = (((th.n * 7 + i * 13) % 5) - 2) * 0.9
                    const d = n === 1 ? `M${x} ${yOf(0) - 10} L${x} ${yOf(0) + 10}` : `M${x} ${y0} Q${x + bow} ${(y0 + y1) / 2} ${x} ${y1}`
                    const second = c.second ? (
                      <path d={n === 1 ? `M${x + 5} ${yOf(0) - 10} L${x + 5} ${yOf(0) + 10}` : `M${x + 5} ${y0} L${x + 5} ${y1}`} fill="none"
                        stroke={emphasised(c.second) ? moodColour(moodOn(c.second, days[i]).v, moodOn(c.second, days[i]).a, cuts) : GREY}
                        stroke-width={1 + 3 * Math.sqrt(c.chat2 / maxChat)} stroke-linecap="round" />
                    ) : null
                    return (
                      <>
                        <path d={d} fill="none" stroke={colour} stroke-width={hotThread && present ? width + 1 : width} stroke-linecap="round" stroke-dasharray={dash} />
                        {second}
                      </>
                    )
                  })}
                  {knots.map((k) => (
                    <g class="knot">
                      {/* a filled knot: AI Digest announced the replacement; an open knot: a handover we inferred from the dates */}
                      <circle cx={x} cy={yOf(k.i)} r={3.6} class={k.announced ? 'knot-ring announced' : 'knot-ring'}><title>{k.announced ? 'announced replacement' : 'handover inferred from the dates'}</title></circle>
                      <text class="label swap" x={x + 8} y={yOf(k.i) + 4} text-anchor="start">{nameOf(k.agent)}</text>
                    </g>
                  ))}
                  <text class={`label${hotThread ? ' hot' : ''}${first && emphasised(first) ? '' : ' faint'}`} x={x + 3} y={TOP - 16} text-anchor="start" transform={`rotate(-50 ${x + 3} ${TOP - 16})`}>
                    {first ? nameOf(first) : ''}
                  </text>
                </g>
              )
            })}
          </g>
        </g>
        {dayRow >= 0 ? <text class="daynum on" x={LEFT - 12} y={yOf(dayRow) + 3.5} text-anchor="end">{days[dayRow]}</text> : null}
        {hover && hover.row !== dayRow ? <text class="daynum" x={LEFT - 12} y={yOf(hover.row) + 3.5} text-anchor="end">{days[hover.row]}</text> : null}
        {n > 1 ? <text class="daynum first" x={LEFT - 12} y={yOf(0) + 3.5} text-anchor="end">{dayRow === 0 || hover?.row === 0 ? '' : days[0]}</text> : null}
        {n > 1 ? <text class="daynum first" x={LEFT - 12} y={yOf(n - 1) + 3.5} text-anchor="end">{dayRow === n - 1 || hover?.row === n - 1 ? '' : days[n - 1]}</text> : null}
      </svg>
      <figcaption class="loom-note">
        {hover?.thread ? (() => {
          const agent = residentAt(hover.thread, hover.row)
          const th = threads.find((x) => x.id === hover.thread)
          const c = cells.get(hover.thread)?.[hover.row]
          const g = agent && lens === 'goal' ? adh.get(agent)?.get(days[hover.row]) : undefined
          return agent ? (
            <span>{nameOf(agent)}{th && !th.human ? `, house ${th.n}` : ''} · day {days[hover.row]} · {(c?.chat ?? 0).toLocaleString('en-US')} messages
              {g?.score ? <span>{' · '}<b class={`score-${g.score}`}>{SCORE_WORD[g.score]}</b>{g.note ? <span class="faint">: {g.note}</span> : null}</span> : null}</span>
          ) : (
            <span class="faint">{th && !th.human ? `house ${th.n}` : 'the humans'} · day {days[hover.row]} · nobody home</span>
          )
        })() : lens === 'goal' && goalTally.n ? (
          <span class="faint">Of {goalTally.n} classified agent-days this week, the summarising model judged <b class="score-on">{Math.round((100 * goalTally.on) / goalTally.n)}% on the goal</b>, {Math.round((100 * goalTally.partly) / goalTally.n)}% partly, <b class="score-off">{Math.round((100 * goalTally.off) / goalTally.n)}% elsewhere</b> and {Math.round((100 * goalTally.idle) / goalTally.n)}% idle. Elsewhere does not mean unproductive. Click a thread for its reason.</span>
        ) : <span class="faint">Touch a strand for a name and a day; click it to open that agent's day. Click a row to open the day.</span>}
      </figcaption>
    </figure>
  )
}

// goal adherence per agent per day, for the residents of one chapter (or the whole book)
const adhCache = new Map<string, Promise<AdherenceFile | null>>()
export function useChapterAdherence(idx: Index, ids: string[]): Map<string, Map<number, { score: Score; note: string }>> {
  const [m, setM] = useState<Map<string, Map<number, { score: Score; note: string }>>>(new Map())
  const key = ids.join(',')
  useEffect(() => {
    if (!ids.length) return
    let live = true
    Promise.all(ids.map((id) => {
      let p = adhCache.get(id)
      if (!p) { p = load<AdherenceFile>(`adherence/${id}.json`); adhCache.set(id, p) }
      return p.then((f) => [id, f] as const)
    })).then((rows) => {
      if (!live) return
      const out = new Map<string, Map<number, { score: Score; note: string }>>()
      for (const [id, f] of rows) {
        if (!f) continue
        out.set(id, new Map(f.days.map((d) => [d.day, { score: d.score, note: d.note }])))
      }
      setM(out)
    })
    return () => { live = false }
  }, [key, idx])
  return m
}

// memory kept per agent per day (jaccard with the day before), for the residents of one chapter
const memCache = new Map<string, Promise<MemoryFile | null>>()
export function useChapterMemory(idx: Index, ids: string[]): Map<string, Map<number, number>> {
  const [m, setM] = useState<Map<string, Map<number, number>>>(new Map())
  const key = ids.join(',')
  useEffect(() => {
    if (!ids.length) return
    let live = true
    Promise.all(ids.map((id) => {
      let p = memCache.get(id)
      if (!p) { p = load<MemoryFile>(`memory/${id}.json`); memCache.set(id, p) }
      return p.then((f) => [id, f] as const)
    })).then((rows) => {
      if (!live) return
      const out = new Map<string, Map<number, number>>()
      for (const [id, f] of rows) {
        if (!f) continue
        const byDay = new Map<number, number>()
        for (const d of f.days) if (d.jaccard_prev != null) byDay.set(d.day, d.jaccard_prev)
        out.set(id, byDay)
      }
      setM(out)
    })
    return () => { live = false }
  }, [key, idx])
  return m
}
