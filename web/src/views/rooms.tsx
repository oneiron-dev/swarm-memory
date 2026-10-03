import { useMemo, useState } from 'preact/hooks'
import { fmt, groupOf, useLoad, type Edge, type Index, type Summary } from '../data'
import { GroupDot, Legend, Picks, Tip, useWidth } from '../chart'
import { go, href, type Route } from '../route'
import { RichText } from '../text'
import { longDate } from './timeline'

type Kind = 'all' | 'mention' | 'reply'
const KINDS: { id: Kind; name: string }[] = [
  { id: 'all', name: 'both' },
  { id: 'mention', name: 'mentions' },
  { id: 'reply', name: 'replies' },
]
const MAX_EDGES = 600

interface Pos { x: number; y: number }

// A small deterministic force layout over the all-time graph, run once, so the map stays put
// while the day range changes which edges are lit.
const layoutCache = new WeakMap<Index, Map<string, Pos>>()
function layout(idx: Index): Map<string, Pos> {
  const hit = layoutCache.get(idx)
  if (hit) return hit
  const g = idx.b.views.interactions
  const ids = g.nodes.map((n) => n.id)
  const at = new Map(ids.map((id, i) => [id, i]))
  // undirected spring weights: both directions of a pair pull together
  const pair = new Map<string, number>()
  for (const e of g.edges) {
    if (!at.has(e.from) || !at.has(e.to) || e.from === e.to) continue
    const k = e.from < e.to ? `${e.from}|${e.to}` : `${e.to}|${e.from}`
    pair.set(k, (pair.get(k) ?? 0) + e.w)
  }
  const springs = [...pair.entries()].map(([k, w]) => {
    const [a, b] = k.split('|')
    return { i: at.get(a)!, j: at.get(b)!, w }
  })
  const maxW = Math.max(1, ...springs.map((s) => s.w))
  const n = ids.length
  // start on a circle, ordered by first day so eras begin apart
  const order = [...ids].sort((a, b) => (idx.agent.get(a)?.first_day ?? 0) - (idx.agent.get(b)?.first_day ?? 0))
  const px = new Float64Array(n), py = new Float64Array(n)
  order.forEach((id, k) => {
    const i = at.get(id)!
    const t = (k / n) * Math.PI * 2
    px[i] = Math.cos(t) * 0.5
    py[i] = Math.sin(t) * 0.5
  })
  const fx = new Float64Array(n), fy = new Float64Array(n)
  const ITER = 500
  for (let it = 0; it < ITER; it++) {
    const temp = 0.05 * (1 - it / ITER) + 0.002
    fx.fill(0); fy.fill(0)
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        let dx = px[i] - px[j], dy = py[i] - py[j]
        let d2 = dx * dx + dy * dy
        if (d2 < 1e-6) { dx = 0.01 * (i - j); dy = 0.01; d2 = 1e-4 }
        const f = 0.012 / d2
        fx[i] += dx * f; fy[i] += dy * f
        fx[j] -= dx * f; fy[j] -= dy * f
      }
    }
    for (const s of springs) {
      const dx = px[s.j] - px[s.i], dy = py[s.j] - py[s.i]
      const d = Math.sqrt(dx * dx + dy * dy) || 1e-3
      const f = d * d * (0.6 + 4 * Math.sqrt(s.w / maxW))
      fx[s.i] += (dx / d) * f; fy[s.i] += (dy / d) * f
      fx[s.j] -= (dx / d) * f; fy[s.j] -= (dy / d) * f
    }
    for (let i = 0; i < n; i++) {
      fx[i] -= px[i] * 0.35
      fy[i] -= py[i] * 0.35
      const m = Math.sqrt(fx[i] * fx[i] + fy[i] * fy[i]) || 1
      const step = Math.min(m, temp)
      px[i] += (fx[i] / m) * step
      py[i] += (fy[i] / m) * step
    }
  }
  // normalise into [0, 1] on robust bounds so one loner cannot squash the map
  const q = (arr: Float64Array, p: number) => [...arr].sort((a, b) => a - b)[Math.min(n - 1, Math.max(0, Math.round(p * (n - 1))))]
  const minX = q(px, 0.02), maxX = q(px, 0.98), minY = q(py, 0.02), maxY = q(py, 0.98)
  const clamp = (v: number) => Math.min(1, Math.max(0, v))
  const out = new Map<string, Pos>()
  ids.forEach((id, i) => out.set(id, {
    x: clamp((px[i] - minX) / (maxX - minX || 1)),
    y: clamp((py[i] - minY) / (maxY - minY || 1)),
  }))
  layoutCache.set(idx, out)
  return out
}

interface LitEdge { e: Edge; w: number }

function litEdges(idx: Index, from: number, to: number, kind: Kind): LitEdge[] {
  const out: LitEdge[] = []
  for (const e of idx.b.views.interactions.edges) {
    let w = 0
    for (const [d, c] of e.by_day) if (d >= from && d <= to) w += c
    if (!w) continue
    // by_day carries both kinds together; split by the edge's all-time kind share
    if (kind !== 'all') w = Math.round((w * e.kinds[kind]) / Math.max(1, e.w))
    if (w > 0) out.push({ e, w })
  }
  return out.sort((a, b) => b.w - a.w)
}

export function Rooms({ idx, route }: { idx: Index; route: Route }) {
  const range = idx.b.manifest.range
  const [kind, setKind] = useState<Kind>('all')
  const [from, setFrom] = useState(range.first_day)
  const [to, setTo] = useState(range.last_day)
  const [focus, setFocus] = useState<string | null>(null)
  const rooms = useMemo(() => [...idx.b.rooms].filter((r) => r.messages > 0).sort((a, b) => b.messages - a.messages), [idx])
  const room = route.arg && rooms.some((r) => r.id === route.arg) ? route.arg : rooms[0]?.id ?? null
  const goalValue = idx.b.goals.findIndex((g) => g.first_day === from && g.last_day === to)

  return (
    <div class="stack">
      <section class="well">
        <div class="head">
          <h1 class="place">Who talks to whom</h1>
          <Picks label="interaction kind" value={kind} options={KINDS} onPick={setKind} />
        </div>
        <div class="range">
          <label class="quiet">
            <span>village goal</span>
            <select
              class="pickbox"
              value={String(goalValue)}
              onChange={(e) => {
                const i = Number((e.target as HTMLSelectElement).value)
                const g = idx.b.goals[i]
                if (g) { setFrom(g.first_day); setTo(g.last_day) } else { setFrom(range.first_day); setTo(range.last_day) }
              }}
            >
              <option value="-1">the whole run</option>
              {idx.b.goals.map((g, i) => (
                <option value={String(i)}>days {g.first_day} to {g.last_day}: {g.goal.trim().slice(0, 70)}</option>
              ))}
            </select>
          </label>
          <div class="sliders">
            <label>
              <span>from day <span class="fig hi">{from}</span></span>
              <input type="range" min={range.first_day} max={range.last_day} value={from}
                onInput={(e) => { const v = Number((e.target as HTMLInputElement).value); setFrom(Math.min(v, to)) }} />
            </label>
            <label>
              <span>to day <span class="fig hi">{to}</span></span>
              <input type="range" min={range.first_day} max={range.last_day} value={to}
                onInput={(e) => { const v = Number((e.target as HTMLInputElement).value); setTo(Math.max(v, from)) }} />
            </label>
          </div>
        </div>
        <Network idx={idx} from={from} to={to} kind={kind} focus={focus} setFocus={setFocus} />
        <div class="mt-s"><Legend /></div>
        <p class="quiet mt-s" style={{ fontSize: '14px' }}>
          A line runs from the speaker to the agent it addressed. A mention is a message that names another agent; a reply is the next message in the same room, within ten minutes, from a different agent. Thicker lines carry more. Pick an agent to see its partners.
        </p>
      </section>
      <div class="cols">
        <aside class="well">
          <h2>Rooms</h2>
          <ul class="rows mt-s">
            {rooms.map((r) => (
              <li>
                <a class={r.id === room ? 'row on' : 'row'} href={href('rooms', r.id)}>
                  <span class="grow" title={r.deleted ? 'deleted' : ''}>{r.name}{r.deleted ? <span class="quiet"> (deleted)</span> : null}</span>
                  <span class="fig">{fmt(r.messages)}</span>
                </a>
              </li>
            ))}
          </ul>
        </aside>
        {room && <RoomSummaries idx={idx} room={room} from={from} to={to} />}
      </div>
    </div>
  )
}

function Network({ idx, from, to, kind, focus, setFocus }: {
  idx: Index; from: number; to: number; kind: Kind; focus: string | null; setFocus: (id: string | null) => void
}) {
  const [ref, width] = useWidth<HTMLDivElement>()
  const [hover, setHover] = useState<string | null>(null)
  const [hoverEdge, setHoverEdge] = useState<LitEdge | null>(null)
  const pos = layout(idx)
  const all = useMemo(() => litEdges(idx, from, to, kind), [idx, from, to, kind])
  const shown = all.slice(0, MAX_EDGES)
  const strength = new Map<string, number>()
  for (const { e, w } of all) {
    strength.set(e.from, (strength.get(e.from) ?? 0) + w)
    strength.set(e.to, (strength.get(e.to) ?? 0) + w)
  }
  const nodes = [...strength.entries()].sort((a, b) => b[1] - a[1])
  const maxS = Math.max(1, ...strength.values())
  const maxW = Math.max(1, ...shown.map((s) => s.w))
  const height = Math.round(Math.min(620, Math.max(360, width * 0.62)))
  const pad = 46
  const X = (id: string) => pad + (pos.get(id)?.x ?? 0.5) * (width - pad * 2)
  const Y = (id: string) => pad * 0.6 + (pos.get(id)?.y ?? 0.5) * (height - pad * 1.2)
  const R = (id: string) => 3.5 + Math.sqrt((strength.get(id) ?? 0) / maxS) * 15
  const lead = new Set(nodes.slice(0, width < 600 ? 8 : 16).map(([id]) => id))
  const active = hover ?? focus
  const name = (id: string) => (id === 'human' ? 'humans' : idx.agent.get(id)?.name ?? id)

  const curve = (a: string, b: string) => {
    const x1 = X(a), y1 = Y(a), x2 = X(b), y2 = Y(b)
    const mx = (x1 + x2) / 2, my = (y1 + y2) / 2
    const dx = x2 - x1, dy = y2 - y1
    const k = 0.14
    return `M${x1.toFixed(1)},${y1.toFixed(1)} Q${(mx - dy * k).toFixed(1)},${(my + dx * k).toFixed(1)} ${x2.toFixed(1)},${y2.toFixed(1)}`
  }
  const partners = (id: string) => {
    const out = all.filter((s) => s.e.from === id).slice(0, 6)
    const inn = all.filter((s) => s.e.to === id).slice(0, 6)
    return { out, inn }
  }
  const hp = hover ? partners(hover) : null

  if (!nodes.length) return <p class="empty mt">No interactions in this day range.</p>

  return (
    <div class="chart network mt" ref={ref}>
      <svg width={width} height={height} role="img" aria-label="network of agent interactions" onClick={(e) => { if ((e.target as Element).tagName === 'svg') setFocus(null) }}>
        <g>
          {shown.map((s) => {
            const on = active ? s.e.from === active || s.e.to === active : false
            const dim = active && !on
            return (
              <path
                d={curve(s.e.from, s.e.to)}
                fill="none"
                stroke={on ? 'var(--ink-1)' : 'var(--ink-3)'}
                stroke-width={0.6 + Math.sqrt(s.w / maxW) * 4.5}
                stroke-linecap="round"
                opacity={dim ? 0.06 : on ? 0.9 : 0.18 + 0.5 * (s.w / maxW)}
                onPointerEnter={() => setHoverEdge(s)}
                onPointerLeave={() => setHoverEdge(null)}
              />
            )
          })}
        </g>
        <g>
          {nodes.map(([id]) => {
            const g = groupOf(idx.agent.get(id)?.family)
            const dim = active && active !== id && !shown.some((s) => (s.e.from === active && s.e.to === id) || (s.e.to === active && s.e.from === id))
            return (
              <g
                class="node"
                opacity={dim ? 0.25 : 1}
                onPointerEnter={() => setHover(id)}
                onPointerLeave={() => setHover(null)}
                onClick={() => setFocus(focus === id ? null : id)}
                style={{ cursor: 'pointer' }}
              >
                <circle cx={X(id)} cy={Y(id)} r={R(id) + 6} fill="transparent" />
                <circle cx={X(id)} cy={Y(id)} r={R(id)} fill={`var(--g-${g})`} stroke="var(--solid)" stroke-width={2} />
                {(lead.has(id) || id === active) && (
                  <text class="nlabel" x={X(id) + R(id) + 4} y={Y(id) + 4}>{name(id)}</text>
                )}
              </g>
            )
          })}
        </g>
      </svg>
      {hover && hp && (
        <Tip x={X(hover)} y={Y(hover) - R(hover)} w={width}>
          <div><b>{name(hover)}</b> <span class="fig">{fmt(strength.get(hover))}</span></div>
          {hp.out.slice(0, 3).map((s) => <div class="r"><span>to {name(s.e.to)}</span><span class="fig">{fmt(s.w)}</span></div>)}
          {hp.inn.slice(0, 3).map((s) => <div class="r"><span>from {name(s.e.from)}</span><span class="fig">{fmt(s.w)}</span></div>)}
        </Tip>
      )}
      {!hover && hoverEdge && (
        <Tip x={(X(hoverEdge.e.from) + X(hoverEdge.e.to)) / 2} y={(Y(hoverEdge.e.from) + Y(hoverEdge.e.to)) / 2} w={width}>
          <div><b>{name(hoverEdge.e.from)}</b> to <b>{name(hoverEdge.e.to)}</b></div>
          <div class="r"><span>in range</span><span class="fig">{fmt(hoverEdge.w)}</span></div>
          <div class="r"><span>mentions, all time</span><span class="fig">{fmt(hoverEdge.e.kinds.mention)}</span></div>
          <div class="r"><span>replies, all time</span><span class="fig">{fmt(hoverEdge.e.kinds.reply)}</span></div>
        </Tip>
      )}
      {focus && <Partners idx={idx} id={focus} all={all} name={name} />}
    </div>
  )
}

function Partners({ idx, id, all, name }: { idx: Index; id: string; all: LitEdge[]; name: (id: string) => string }) {
  const out = all.filter((s) => s.e.from === id).slice(0, 8)
  const inn = all.filter((s) => s.e.to === id).slice(0, 8)
  const g = groupOf(idx.agent.get(id)?.family)
  return (
    <div class="partners">
      <div class="head" style={{ marginBottom: '6px' }}>
        <h2><GroupDot g={g} /> {name(id)}</h2>
        <a class="verb" href={href('agents', id)} onClick={(e) => { e.preventDefault(); go('agents', id) }}>open the agent</a>
      </div>
      <div class="pcols">
        <div>
          <h3>Speaks to</h3>
          <ul class="rows mt-s">
            {out.map((s) => <li class="row"><span class="grow">{name(s.e.to)}</span><span class="fig">{fmt(s.w)}</span></li>)}
            {!out.length && <li class="empty">nobody in this range</li>}
          </ul>
        </div>
        <div>
          <h3>Hears from</h3>
          <ul class="rows mt-s">
            {inn.map((s) => <li class="row"><span class="grow">{name(s.e.from)}</span><span class="fig">{fmt(s.w)}</span></li>)}
            {!inn.length && <li class="empty">nobody in this range</li>}
          </ul>
        </div>
      </div>
    </div>
  )
}

function RoomSummaries({ idx, room, from, to }: { idx: Index; room: string; from: number; to: number }) {
  const { data, loading } = useLoad<Summary[]>(`summaries/room/${room}.json`)
  const [shown, setShown] = useState(10)
  const list = useMemo(() => (data ?? []).filter((s) => s.day >= from && s.day <= to).sort((a, b) => b.day - a.day), [data, from, to])
  const r = idx.b.rooms.find((x) => x.id === room)
  return (
    <section class="well">
      <div class="head">
        <h2>{r?.name ?? room}, day by day</h2>
        {r && r.first_day != null && <span class="fig">days {r.first_day} to {r.last_day}</span>}
      </div>
      {loading ? (
        <p class="loading">Loading the room's summaries</p>
      ) : !data ? (
        <p class="empty">No daily summaries for this room.</p>
      ) : !list.length ? (
        <p class="empty">No summaries for this room between day {from} and day {to}.</p>
      ) : (
        <>
          {list.slice(0, shown).map((s) => (
            <article class="summary">
              <div class="when">
                <b><a href={href('timeline', s.day)}>Day {s.day}</a></b>
                <span class="fig">{longDate(s.date)}</span>
              </div>
              <p class="txt"><RichText text={s.text} cites={s.cites} day={s.day} /></p>
            </article>
          ))}
          {shown < list.length && <button class="verb mt-s" type="button" onClick={() => setShown(shown + 12)}>show older days</button>}
        </>
      )}
    </section>
  )
}
