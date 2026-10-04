import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { fmt, groupOf, labelName, slugOf, useLoad, LABEL_NAME, type Entity, type EntityDetail, type Index } from '../data'
import { GroupDot, Picks, Tip, linear, niceMax, useWidth } from '../chart'
import { go, href, type Route } from '../route'
import { SourceCard } from '../text'
import { dateOf } from './timeline'

// One table row: a plain entity, or every agent-name entity of one agent folded together.
interface Row { key: string; text: string; label: string; count: number; n_agents: number; first_day: number; last_day: number; lead: Entity; variants: Entity[]; agent?: string }

const rowsCache = new WeakMap<Index, Row[]>()
function rows(idx: Index): Row[] {
  const hit = rowsCache.get(idx)
  if (hit) return hit
  const out: Row[] = []
  for (const e of idx.b.entities) {
    if (e.agent) continue
    out.push({ key: e.id, text: e.text, label: e.label, count: e.count, n_agents: e.n_agents, first_day: e.first_day, last_day: e.last_day, lead: e, variants: [e] })
  }
  for (const [agent, list] of idx.agentEntities) {
    out.push({
      key: 'agent:' + agent,
      text: idx.agent.get(agent)?.name ?? list[0].text,
      label: 'AGENT',
      count: list.reduce((s, e) => s + e.count, 0),
      n_agents: Math.max(...list.map((e) => e.n_agents)),
      first_day: Math.min(...list.map((e) => e.first_day)),
      last_day: Math.max(...list.map((e) => e.last_day)),
      lead: list[0],
      variants: list,
      agent,
    })
  }
  out.sort((a, b) => b.count - a.count)
  rowsCache.set(idx, out)
  return out
}

type Sort = 'count' | 'n_agents' | 'first_day' | 'text'
const LABELS = ['all', 'AGENT', ...Object.keys(LABEL_NAME)]
const nameOfLabel = (l: string) => (l === 'all' ? 'all' : l === 'AGENT' ? 'agents' : labelName(l))

export function Entities({ idx, route }: { idx: Index; route: Route }) {
  const [q, setQ] = useState('')
  const [label, setLabel] = useState('all')
  const [sort, setSort] = useState<Sort>('count')
  const [shown, setShown] = useState(60)
  const all = rows(idx)
  const list = useMemo(() => {
    const needle = q.trim().toLowerCase()
    const out = all.filter((r) =>
      (label === 'all' || r.label === label) &&
      (!needle || r.text.toLowerCase().includes(needle) || r.variants.some((e) => e.aliases.some((a) => a.toLowerCase().includes(needle)))),
    )
    if (sort === 'text') out.sort((a, b) => a.text.localeCompare(b.text))
    else if (sort === 'first_day') out.sort((a, b) => a.first_day - b.first_day)
    else out.sort((a, b) => (b[sort] as number) - (a[sort] as number))
    return out
  }, [all, q, label, sort])

  const selected = route.arg ? idx.entity.get('e:' + route.arg) ?? null : null
  const selRow = selected ? all.find((r) => r.variants.includes(selected)) ?? null : null
  const counts = useMemo(() => {
    const m = new Map<string, number>()
    for (const r of all) m.set(r.label, (m.get(r.label) ?? 0) + 1)
    return m
  }, [all])

  const head = (id: Sort, name: string, n = false) => (
    <th class={n ? 'n' : ''} aria-sort={sort === id ? 'descending' : undefined}>
      <button type="button" class={sort === id ? 'on' : ''} onClick={() => setSort(id)}>{name}</button>
    </th>
  )

  return (
    <div class="cols wide">
      <section class="well">
        <div class="head">
          <h1 class="place">Entities</h1>
          <span class="fig">{fmt(list.length)} of {fmt(all.length)}</span>
        </div>
        <p class="read" style={{ fontSize: '14px' }}>
          The things the swarm named most often, as our tagger read them. Same name and same type make one entity. Mentions of an agent's own name are folded into one row per agent.
        </p>
        <input
          class="search mt"
          type="search"
          placeholder="find an entity"
          aria-label="find an entity"
          value={q}
          onInput={(e) => { setQ((e.target as HTMLInputElement).value); setShown(60) }}
        />
        <div class="mt-s">
          <Picks
            label="entity type"
            value={label}
            options={LABELS.filter((l) => l === 'all' || counts.has(l)).map((l) => ({ id: l, name: nameOfLabel(l) }))}
            onPick={(l) => { setLabel(l); setShown(60) }}
          />
        </div>
        <div class="table-wrap mt-s">
          <table>
            <thead>
              <tr>
                {head('text', 'entity')}
                <th>type</th>
                {head('count', 'mentions', true)}
                {head('n_agents', 'agents', true)}
                {head('first_day', 'days', true)}
              </tr>
            </thead>
            <tbody>
              {list.slice(0, shown).map((r) => (
                <tr class={selRow === r ? 'on' : ''} onClick={() => go('entities', slugOf(r.lead.id), null, true)}>
                  <td><a href={href('entities', slugOf(r.lead.id))} onClick={(e) => e.preventDefault()}>{r.text}</a></td>
                  <td class="quiet">{nameOfLabel(r.label)}</td>
                  <td class="n fig">{fmt(r.count)}</td>
                  <td class="n fig">{r.n_agents}</td>
                  <td class="n fig">{r.first_day}–{r.last_day}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!list.length && <p class="empty mt-s">No entity matches "{q}".</p>}
        {shown < list.length && <button class="verb mt-s" type="button" onClick={() => setShown(shown + 120)}>show more</button>}
      </section>
      <div class="stack sticky">
        {selected && selRow ? (
          <EntityPanel key={selected.id} idx={idx} e={selected} row={selRow} />
        ) : (
          <section class="well">
            <h2>Pick an entity</h2>
            <p class="read mt-s">Its mentions over time, which agents named it, and the sentences where the tagger found it.</p>
          </section>
        )}
      </div>
    </div>
  )
}

function EntityPanel({ idx, e, row }: { idx: Index; e: Entity; row: Row }) {
  const { data } = useLoad<EntityDetail>(`entities/${slugOf(e.id)}.json`)
  const ref = useRef<HTMLElement>(null)
  // on a narrow screen the detail sits under the long table: bring it into view when picked
  useEffect(() => {
    if (matchMedia('(max-width: 960px)').matches && location.hash.split('/').length > 1) ref.current?.scrollIntoView({ block: 'start' })
  }, [e.id])
  return (
    <section class="well" ref={ref}>
      <div class="head">
        <h2>{row.agent ? <><GroupDot g={groupOf(idx.agent.get(row.agent)?.family)} /> {row.text}</> : e.text}</h2>
        {row.agent && <a class="verb" href={href('agents', row.agent)}>open the agent</a>}
      </div>
      <p class="read" style={{ fontSize: '14px' }}>
        Tagged {labelName(e.label)}, <span class="fig">{fmt(e.count)}</span> mentions by <span class="fig">{e.n_agents}</span> speakers, day {e.first_day} to day {e.last_day}.
        {e.aliases.length > 0 && <> Also written {e.aliases.slice(0, 4).map((a, i) => <><code>{a}</code>{i < Math.min(4, e.aliases.length) - 1 ? ', ' : ''}</>)}.</>}
      </p>
      {row.variants.length > 1 && (
        <div class="picks mt-s" aria-label="tag variants">
          {row.variants.map((v) => (
            <a class={v.id === e.id ? 'pick on' : 'pick'} href={href('entities', slugOf(v.id))}>
              <span aria-hidden="true">{v.id === e.id ? '◆' : '◇'}</span> as {labelName(v.label)} <span class="fig">{fmt(v.count)}</span>
            </a>
          ))}
        </div>
      )}
      {data ? (
        <>
          <h3 class="mt">Mentions per day</h3>
          <Spark idx={idx} timeline={data.timeline} />
          <h3 class="mt">Who named it</h3>
          <AgentBars idx={idx} agents={data.agents} />
        </>
      ) : (
        <p class="loading">Loading its detail</p>
      )}
      <h3 class="mt">Where it was found</h3>
      <div class="mt-s">
        {e.examples.map((ref, i) => (
          <SourceCard idx={idx} refId={ref} day={e.example_days[i]} focus={e.text} />
        ))}
        {!e.examples.length && <p class="empty">No examples kept.</p>}
      </div>
    </section>
  )
}

function Spark({ idx, timeline }: { idx: Index; timeline: [number, number][] }) {
  const [ref, width] = useWidth<HTMLDivElement>(400)
  const [hover, setHover] = useState<[number, number] | null>(null)
  const range = idx.b.manifest.range
  const height = 70
  const x = linear(range.first_day - 0.5, range.last_day + 0.5, 0, width)
  const maxV = niceMax(Math.max(1, ...timeline.map((t) => t[1])))
  const y = linear(0, maxV, height - 14, 4)
  const bw = Math.max(1, width / (range.last_day - range.first_day + 1))
  const onMove = (ev: PointerEvent) => {
    const r = (ev.currentTarget as SVGElement).getBoundingClientRect()
    const px = ev.clientX - r.left
    let best: [number, number] | null = null
    for (const t of timeline) if (!best || Math.abs(x(t[0]) - px) < Math.abs(x(best[0]) - px)) best = t
    setHover(best)
  }
  return (
    <div class="chart mt-s" ref={ref}>
      <svg width={width} height={height} role="img" aria-label="mentions per day" onPointerMove={onMove} onPointerLeave={() => setHover(null)}>
        <line class="axis" x1={0} x2={width} y1={y(0) + 0.5} y2={y(0) + 0.5} />
        {timeline.map(([d, c]) => (
          <rect x={x(d) - bw / 2} y={y(c)} width={bw} height={Math.max(0.8, y(0) - y(c))} fill={hover && hover[0] === d ? 'var(--ink-1)' : 'var(--ink-2)'} />
        ))}
        <text x={0} y={height - 1}>day {range.first_day}</text>
        <text x={width} y={height - 1} text-anchor="end">day {range.last_day}</text>
        <text x={width} y={10} text-anchor="end">{fmt(maxV)}</text>
      </svg>
      {hover && (
        <Tip x={x(hover[0])} y={y(hover[1])} w={width}>
          <b>Day {hover[0]}</b> <span class="fig">{dateOf(idx, hover[0])}</span>
          <div class="r"><span>mentions</span><span class="fig">{fmt(hover[1])}</span></div>
        </Tip>
      )}
    </div>
  )
}

function AgentBars({ idx, agents }: { idx: Index; agents: Record<string, number> }) {
  const list = Object.entries(agents).sort((a, b) => b[1] - a[1])
  const top = list.slice(0, 10)
  const max = Math.max(1, ...top.map((t) => t[1]))
  return (
    <ul class="bars mt-s">
      {top.map(([id, n]) => {
        const g = groupOf(idx.agent.get(id)?.family)
        return (
          <li>
            <a href={href('agents', id)} class="bar-name">{id === 'human' ? 'humans' : idx.agent.get(id)?.name ?? id}</a>
            <span class="bar-track"><span class={`bar-fill g-${g}`} style={{ width: `${Math.max(1.5, (n / max) * 100)}%` }} /></span>
            <span class="fig">{fmt(n)}</span>
          </li>
        )
      })}
      {list.length > top.length && <li class="quiet" style={{ fontSize: '14px' }}>and {list.length - top.length} more</li>}
    </ul>
  )
}
