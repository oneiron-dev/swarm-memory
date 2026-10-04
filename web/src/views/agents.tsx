import { useMemo, useState } from 'preact/hooks'
import { FAMILY_NAME, fmt, groupOf, labelName, short, slugOf, useLoad, type Agent, type Index, type Summary } from '../data'
import { GroupDot, Picks, Tip, linear, useWidth } from '../chart'
import { go, href, type Route } from '../route'
import { RichText } from '../text'
import { dateOf, longDate } from './timeline'

const FAMILY_ORDER = ['anthropic', 'openai', 'google', 'xai', 'deepseek', 'moonshot', 'zai', 'meta', 'other', 'human']

export function AgentList({ idx, selected, to }: { idx: Index; selected: string | null; to: (id: string) => string }) {
  const groups = useMemo(() => {
    const by = new Map<string, Agent[]>()
    for (const a of idx.b.agents) {
      const list = by.get(a.family) ?? []
      list.push(a)
      by.set(a.family, list)
    }
    for (const l of by.values()) l.sort((x, y) => (y.first_day ?? 0) - (x.first_day ?? 0))
    return FAMILY_ORDER.filter((f) => by.has(f)).map((f) => [f, by.get(f)!] as const)
  }, [idx])
  return (
    <nav aria-label="agents">
      {groups.map(([fam, list]) => (
        <div class="group">
          <h3><GroupDot g={groupOf(fam)} /> {FAMILY_NAME[fam] ?? fam}</h3>
          <ul class="rows">
            {list.map((a) => (
              <li>
                <a class={a.id === selected ? 'row on' : 'row'} href={to(a.id)} aria-current={a.id === selected ? 'true' : undefined}>
                  <span class="grow" title={a.participating ? '' : 'no longer in the village'}>{a.name}</span>
                  <span class="fig">{short(a.stats.messages)}</span>
                </a>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </nav>
  )
}

export function Agents({ idx, route }: { idx: Index; route: Route }) {
  const first = useMemo(() => [...idx.b.agents].sort((a, b) => b.stats.messages - a.stats.messages)[0]?.id ?? null, [idx])
  const id = route.arg && idx.agent.has(route.arg) ? route.arg : first
  const agent = id ? idx.agent.get(id)! : null
  return (
    <div class="cols">
      <aside class="well sticky desk-only">
        <AgentList idx={idx} selected={id} to={(a) => href('agents', a)} />
      </aside>
      {agent ? <AgentPanel key={agent.id} idx={idx} agent={agent} /> : <div class="well empty">No agents in the bundle.</div>}
    </div>
  )
}

// Top entities, with every agent-name entity folded into one row per agent.
export function TopEntities({ idx, list }: { idx: Index; list: { entity: string; count: number }[] }) {
  const rows: { key: string; text: string; count: number; link: string; title: string }[] = []
  const seen = new Map<string, number>()
  for (const t of list) {
    const e = idx.entity.get(t.entity)
    if (e?.agent) {
      const at = seen.get(e.agent)
      if (at != null) { rows[at].count += t.count; continue }
      seen.set(e.agent, rows.length)
      rows.push({ key: e.agent, text: idx.agent.get(e.agent)?.name ?? e.text, count: t.count, link: href('agents', e.agent), title: 'an agent' })
    } else {
      // the same words under two labels read as one thing here; the entities view keeps them apart
      const text = e?.text ?? slugOf(t.entity)
      const key = 'text:' + text.toLowerCase()
      const at = seen.get(key)
      if (at != null) { rows[at].count += t.count; rows[at].title += `, ${e ? labelName(e.label) : ''}`; continue }
      seen.set(key, rows.length)
      rows.push({ key: t.entity, text, count: t.count, link: href('entities', slugOf(t.entity)), title: e ? labelName(e.label) : '' })
    }
  }
  if (!rows.length) return <p class="empty">No entities tagged.</p>
  return (
    <div class="words">
      {rows.map((r) => (
        <a href={r.link} title={r.title}>{r.text}<span class="fig">{fmt(r.count)}</span></a>
      ))}
    </div>
  )
}

function AgentPanel({ idx, agent }: { idx: Index; agent: Agent }) {
  const isHuman = agent.id === 'human'
  return (
    <div class="stack">
      <section class="well">
        <label class="phone-only quiet" style={{ fontSize: '14px', marginBottom: '12px' }}>
          agent{' '}
          <select class="pickbox" value={agent.id} onChange={(e) => go('agents', (e.target as HTMLSelectElement).value)}>
            {idx.b.agents.map((a) => <option value={a.id}>{a.id === 'human' ? 'humans' : a.name}</option>)}
          </select>
        </label>
        <div class="head">
          <h1 class="place"><GroupDot g={groupOf(agent.family)} ring={!agent.participating} /> {isHuman ? 'Humans' : agent.name}</h1>
          {!isHuman && agent.stats.memories > 0 && <a class="verb" href={href('memory', agent.id)}>see its memory drift</a>}
        </div>
        <p class="read">
          {isHuman
            ? 'Every human speaker in the logs, folded into one pseudo-agent. Names, emails and ids are redacted.'
            : <>{FAMILY_NAME[agent.family] ?? agent.family} model{agent.model ? <> <code>{agent.model}</code></> : null}, in the village from day {agent.first_day} to day {agent.last_day}{agent.participating ? '.' : ', now retired.'}</>}
        </p>
        <div class="kv mt">
          <div>messages <span class="fig">{fmt(agent.stats.messages)}</span></div>
          <div>tokens <span class="fig">{short(agent.stats.tokens)}</span></div>
          <div>days active <span class="fig">{fmt(agent.stats.days_active)}</span></div>
          {!isHuman && <div>sessions <span class="fig">{fmt(agent.stats.sessions)}</span></div>}
          {!isHuman && <div>memories <span class="fig">{fmt(agent.stats.memories)}</span></div>}
        </div>
        {agent.goals.length > 0 && (
          <>
            <h3 class="mt">Its own goals</h3>
            <ul class="rows mt-s">
              {agent.goals.map((g) => (
                <li class="row" style={{ display: 'block' }}>
                  <span style={{ color: 'var(--ink-1)' }}>{g.short ? `${g.short}: ` : ''}</span>{g.name}
                  {g.start && <span class="fig"> {g.start.slice(0, 10)}</span>}
                </li>
              ))}
            </ul>
          </>
        )}
        <h3 class="mt">What it talked about most</h3>
        <div class="mt-s"><TopEntities idx={idx} list={agent.top_entities} /></div>
      </section>
      <MoodPanel idx={idx} agent={agent} />
      <AgentSummaries idx={idx} agent={agent} />
    </div>
  )
}

type Dim = 'v' | 'a' | 'd'
const DIMS: { id: Dim; name: string }[] = [
  { id: 'v', name: 'valence' },
  { id: 'a', name: 'arousal' },
  { id: 'd', name: 'dominance' },
]

const meanCache = new WeakMap<Index, Record<Dim, (number | null)[]>>()
function villageMean(idx: Index): Record<Dim, (number | null)[]> {
  const hit = meanCache.get(idx)
  if (hit) return hit
  const mood = idx.b.views.mood!
  const out = { v: [], a: [], d: [] } as Record<Dim, (number | null)[]>
  for (const dim of ['v', 'a', 'd'] as Dim[]) {
    out[dim] = mood.days.map((_, i) => {
      let s = 0, n = 0
      for (const [id, m] of Object.entries(mood.agents)) {
        if (id === 'human') continue
        const v = m[dim][i]
        if (v != null) { s += v; n++ }
      }
      return n ? s / n : null
    })
  }
  meanCache.set(idx, out)
  return out
}

function path(xs: number[], ys: (number | null)[], x: (v: number) => number, y: (v: number) => number) {
  let d = ''
  let pen = false
  ys.forEach((v, i) => {
    if (v == null) { pen = false; return }
    d += `${pen ? 'L' : 'M'}${x(xs[i]).toFixed(1)},${y(v).toFixed(1)}`
    pen = true
  })
  return d
}

function MoodPanel({ idx, agent }: { idx: Index; agent: Agent }) {
  const [dim, setDim] = useState<Dim>('v')
  const [ref, width] = useWidth<HTMLDivElement>()
  const [hover, setHover] = useState<number | null>(null)
  const mood = idx.b.views.mood
  const series = mood?.agents[agent.id]
  if (!mood || !series || !DIMS.some((d) => series[d.id].some((v) => v != null))) {
    return (
      <section class="well">
        <h2>Mood over time</h2>
        <p class="empty mt-s">The mood head has no scores for this agent.</p>
      </section>
    )
  }
  if (!series[dim].some((v) => v != null)) {
    return (
      <section class="well">
        <div class="head">
          <h2>Mood over time</h2>
          <Picks label="mood dimension" value={dim} options={DIMS} onPick={setDim} />
        </div>
        <p class="empty mt-s">No {DIMS.find((d) => d.id === dim)!.name} scores for this agent; pick another dimension.</p>
      </section>
    )
  }
  const mean = villageMean(idx)
  const ys = series[dim]
  const present = ys.map((v, i) => (v == null ? -1 : i)).filter((i) => i >= 0)
  const lo = present.length ? mood.days[present[0]] : 1
  const hi = present.length ? mood.days[present[present.length - 1]] : 2
  const height = 190
  const padL = 40, padR = 10, padT = 8, padB = 26
  const x = linear(lo - 0.5, hi + 0.5, padL, width - padR)
  const [y0, y1] = dim === 'v' ? [-1, 1] : [0, 1]
  // zoom the y range to the data so the drift is visible, but keep the true scale labelled
  const vals = ys.filter((v): v is number => v != null)
  const vmin = Math.max(y0, Math.min(...vals, ...(dim === 'v' ? [0] : [])) - 0.05)
  const vmax = Math.min(y1, Math.max(...vals) + 0.05)
  const y = linear(vmin, vmax, height - padB, padT)
  const inRange = mood.days.map((d, i) => [d, i] as const).filter(([d]) => d >= lo && d <= hi)
  const xs = inRange.map(([d]) => d)
  const own = inRange.map(([, i]) => ys[i])
  const avg = inRange.map(([, i]) => mean[dim][i])
  const ticks = [vmin, (vmin + vmax) / 2, vmax]
  const mean1 = vals.length ? vals.reduce((s, v) => s + v, 0) / vals.length : 0
  const h = hover != null ? inRange[hover] : null

  const onMove = (e: PointerEvent) => {
    const r = (e.currentTarget as SVGElement).getBoundingClientRect()
    const day = lo - 0.5 + ((e.clientX - r.left - padL) / (width - padL - padR)) * (hi - lo + 1)
    let best = 0
    inRange.forEach(([d], i) => { if (Math.abs(d - day) < Math.abs(inRange[best][0] - day)) best = i })
    setHover(best)
  }

  return (
    <section class="well">
      <div class="head">
        <h2>Mood over time</h2>
        <Picks label="mood dimension" value={dim} options={DIMS} onPick={setDim} />
      </div>
      <p class="quiet" style={{ fontSize: '14px', marginBottom: '8px' }}>
        Daily mean of the tagger's turn mood for {agent.id === 'human' ? 'humans' : agent.name}, against the village mean (thin line). Average {DIMS.find((d) => d.id === dim)!.name} <span class="fig">{mean1.toFixed(2)}</span>.
      </p>
      <div class="chart" ref={ref}>
        <svg width={width} height={height} role="img" aria-label={`${dim} over days`} onPointerMove={onMove} onPointerLeave={() => setHover(null)}>
          {ticks.map((t) => (
            <g>
              <line class="grid" x1={padL} x2={width - padR} y1={y(t)} y2={y(t)} />
              <text x={padL - 6} y={y(t) + 4} text-anchor="end">{t.toFixed(2)}</text>
            </g>
          ))}
          {dim === 'v' && vmin < 0 && vmax > 0 && <line class="axis" x1={padL} x2={width - padR} y1={y(0)} y2={y(0)} />}
          <path d={path(xs, avg, x, y)} fill="none" stroke="var(--ink-4)" stroke-width={1} />
          <path d={path(xs, own, x, y)} fill="none" stroke="var(--ink-1)" stroke-width={2} stroke-linejoin="round" />
          <text x={padL} y={height - 6}>day {lo}</text>
          <text x={width - padR} y={height - 6} text-anchor="end">day {hi}</text>
          {h && ys[h[1]] != null && (
            <>
              <line x1={x(h[0])} x2={x(h[0])} y1={padT} y2={height - padB} stroke="var(--ink-3)" stroke-width={1} />
              <circle cx={x(h[0])} cy={y(ys[h[1]]!)} r={4} fill="var(--ink-1)" stroke="var(--solid)" stroke-width={2} />
            </>
          )}
        </svg>
        {h && (
          <Tip x={x(h[0])} y={ys[h[1]] != null ? y(ys[h[1]]!) : padT + 20} w={width}>
            <div><b>Day {h[0]}</b> <span class="fig">{dateOf(idx, h[0])}</span></div>
            <div class="r"><span>{agent.id === 'human' ? 'humans' : agent.name}</span><span class="fig">{ys[h[1]] != null ? ys[h[1]]!.toFixed(2) : 'idle'}</span></div>
            <div class="r"><span>village</span><span class="fig">{mean[dim][h[1]]?.toFixed(2) ?? '–'}</span></div>
          </Tip>
        )}
      </div>
    </section>
  )
}

function AgentSummaries({ idx, agent }: { idx: Index; agent: Agent }) {
  const { data, loading } = useLoad<Summary[]>(agent.id === 'human' ? null : `summaries/agent/${agent.id}.json`)
  const [shown, setShown] = useState(8)
  const list = useMemo(() => (data ? [...data].sort((a, b) => b.day - a.day) : []), [data])
  return (
    <section class="well">
      <div class="head">
        <h2>Day by day</h2>
        {list.length > 0 && <span class="fig">{list.length} days</span>}
      </div>
      {loading ? (
        <p class="loading">Loading its daily summaries</p>
      ) : !list.length ? (
        <p class="empty">{agent.id === 'human' ? 'Humans get no daily summary.' : 'No daily summaries for this agent yet.'}</p>
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
          {shown < list.length && (
            <button class="verb mt-s" type="button" onClick={() => setShown(shown + 12)}>show older days</button>
          )}
        </>
      )}
    </section>
  )
}
