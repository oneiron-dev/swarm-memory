import { useMemo, useState } from 'preact/hooks'
import { fmt, groupOf, short, slugOf, useLoad, type Index, type MemoryDay, type MemoryFile } from '../data'
import { GroupDot, Tip, linear, niceMax, useWidth } from '../chart'
import { dayArg, go, href, type Route } from '../route'
import { Redacted, clean } from '../text'
import { dateOf, longDate } from './timeline'

const drift = (m: MemoryDay) => (m.jaccard_prev == null ? null : 1 - m.jaccard_prev)

export function Memory({ idx, route }: { idx: Index; route: Route }) {
  const withMem = useMemo(
    () => idx.b.agents.filter((a) => a.stats.memories > 0 && a.id !== 'human').sort((a, b) => b.stats.memories - a.stats.memories),
    [idx],
  )
  const id = route.arg && withMem.some((a) => a.id === route.arg) ? route.arg : withMem[0]?.id ?? null
  const agent = id ? idx.agent.get(id) : null
  const { data, loading } = useLoad<MemoryFile>(id ? `memory/${id}.json` : null)
  const sel = dayArg(route.sub, idx.b.manifest.range.first_day, idx.b.manifest.range.last_day)

  return (
    <div class="stack">
      <section class="well">
        <div class="head">
          <h1 class="place">Memory drift</h1>
          <label class="quiet" style={{ display: 'inline-flex', gap: '8px', alignItems: 'center', fontSize: '14px' }}>
            agent
            <select class="pickbox" value={id ?? ''} onChange={(e) => go('memory', (e.target as HTMLSelectElement).value, null, true)}>
              {withMem.map((a) => <option value={a.id}>{a.name} ({a.stats.memories})</option>)}
            </select>
          </label>
        </div>
        <p class="read">
          Each agent keeps a running memory that it rewrites as it works. We kept its last memory of each day, tagged it with oneiroNER, and compared its entities with the day before.
          Drift is the share of the entity set that changed: 0 means it kept the same things in mind, 1 means a clean slate.
        </p>
        {agent && (
          <p class="mt-s" style={{ fontSize: '14px' }}>
            <a href={href('agents', agent.id)}><GroupDot g={groupOf(agent.family)} /> {agent.name}</a>
          </p>
        )}
        {loading ? (
          <p class="loading">Loading its memories</p>
        ) : !data || !data.days.length ? (
          <p class="empty mt">No kept memories for this agent.</p>
        ) : (
          <DriftCharts key={id} idx={idx} file={data} sel={sel} />
        )}
      </section>
      {data && data.days.length > 0 && <MemoryDetail idx={idx} file={data} sel={sel} />}
    </div>
  )
}

function DriftCharts({ idx, file, sel }: { idx: Index; file: MemoryFile; sel: number | null }) {
  const [ref, width] = useWidth<HTMLDivElement>()
  const [hover, setHover] = useState<number | null>(null)
  const days = file.days
  const lo = days[0].day, hi = days[days.length - 1].day
  const padL = 44, padR = 8
  const x = linear(lo - 0.5, hi + 0.5, padL, width - padR)
  const bw = Math.max(1.5, Math.min(10, (width - padL - padR) / (hi - lo + 1) - 1))
  const h1 = 150, h2 = 70
  const y1 = linear(0, 1, h1 - 4, 6)
  const maxC = niceMax(Math.max(1, ...days.map((d) => d.chars)))
  const y2 = linear(0, maxC, h2 - 2, 4)
  const drifts = days.map(drift).filter((v): v is number => v != null)
  const avg = drifts.length ? drifts.reduce((s, v) => s + v, 0) / drifts.length : null
  const peak = days.reduce<MemoryDay | null>((best, d) => (drift(d) != null && (!best || drift(d)! > drift(best)!) ? d : best), null)
  const pick = (day: number) => go('memory', file.agent, day, true)

  const onMove = (e: PointerEvent) => {
    const r = (e.currentTarget as SVGElement).getBoundingClientRect()
    const px = e.clientX - r.left
    let best = 0
    days.forEach((d, i) => { if (Math.abs(x(d.day) - px) < Math.abs(x(days[best].day) - px)) best = i })
    setHover(best)
  }
  const h = hover != null ? days[hover] : null

  return (
    <div class="mt">
      <div class="kv" style={{ marginBottom: '14px' }}>
        <div>kept memories <span class="fig">{fmt(days.length)}</span></div>
        <div>mean drift <span class="fig">{avg == null ? '–' : avg.toFixed(2)}</span></div>
        <div>biggest change <span class="fig">{peak ? `day ${peak.day}` : '–'}</span></div>
        <div>last length <span class="fig">{short(days[days.length - 1].chars)} chars</span></div>
      </div>
      <div class="chart" ref={ref}>
        <svg width={width} height={h1 + h2 + 40} role="img" aria-label="memory drift per day" tabIndex={0}
          onPointerMove={onMove} onPointerLeave={() => setHover(null)} onClick={() => h && pick(h.day)} style={{ cursor: 'pointer' }}
          onKeyDown={(e) => {
            const cur = sel != null ? days.findIndex((d) => d.day === sel) : days.length - 1
            if (e.key === 'ArrowRight') { e.preventDefault(); pick(days[Math.min(days.length - 1, cur + 1)].day) }
            if (e.key === 'ArrowLeft') { e.preventDefault(); pick(days[Math.max(0, cur - 1)].day) }
          }}>
          {[0, 0.5, 1].map((t) => (
            <g>
              <line class="grid" x1={padL} x2={width - padR} y1={y1(t)} y2={y1(t)} />
              <text x={padL - 6} y={y1(t) + 4} text-anchor="end">{t}</text>
            </g>
          ))}
          <text x={padL} y={12} dx={4} class="clabel">drift</text>
          {days.map((d) => {
            const v = drift(d)
            const on = d.day === sel || (h && h.day === d.day)
            if (v == null) return <rect x={x(d.day) - bw / 2} y={y1(0) - 2} width={bw} height={2} fill="var(--ink-4)" />
            return <rect x={x(d.day) - bw / 2} y={y1(v)} width={bw} height={Math.max(1, y1(0) - y1(v))} fill={on ? 'var(--ink-1)' : 'var(--ink-3)'} />
          })}
          <g transform={`translate(0, ${h1 + 14})`}>
            <line class="grid" x1={padL} x2={width - padR} y1={y2(maxC)} y2={y2(maxC)} />
            <text x={padL - 6} y={y2(maxC) + 4} text-anchor="end">{short(maxC)}</text>
            <text x={padL} y={y2(maxC) + 4} dx={4} class="clabel">memory length, chars</text>
            {days.map((d) => (
              <rect x={x(d.day) - bw / 2} y={y2(d.chars)} width={bw} height={Math.max(0.5, y2(0) - y2(d.chars))} fill={d.day === sel ? 'var(--ink-1)' : 'var(--ink-4)'} />
            ))}
            <line class="axis" x1={padL} x2={width - padR} y1={y2(0) + 0.5} y2={y2(0) + 0.5} />
            <text x={padL} y={h2 + 18}>day {lo}</text>
            <text x={width - padR} y={h2 + 18} text-anchor="end">day {hi}</text>
          </g>
          {sel != null && <line x1={x(sel)} x2={x(sel)} y1={0} y2={h1 + h2 + 16} stroke="var(--safelight)" stroke-width={1.5} />}
        </svg>
        {h && (
          <Tip x={x(h.day)} y={drift(h) != null ? y1(drift(h)!) : y1(0)} w={width}>
            <div><b>Day {h.day}</b> <span class="fig">{dateOf(idx, h.day)}</span></div>
            <div class="r"><span>drift</span>{drift(h) == null ? <span class="quiet">none yet</span> : <span class="fig">{drift(h)!.toFixed(2)}</span>}</div>
            <div class="r"><span>entities kept</span><span class="fig">{h.entities.length}</span></div>
            <div class="r"><span>added, dropped</span><span class="fig">{h.added.length}, {h.dropped.length}</span></div>
            <div class="r"><span>length</span><span class="fig">{fmt(h.chars)}</span></div>
          </Tip>
        )}
      </div>
      <p class="quiet mt-s" style={{ fontSize: '14px' }}>
        {drifts.length
          ? 'A short grey mark means the memory had no entity set to compare. Pick a day to read that memory.'
          : "Drift bars fill in once oneiroNER's tags for this agent's memories are in the bundle. The memory lengths and texts are here now: pick a day to read one."}
      </p>
    </div>
  )
}

function EntityWords({ idx, ids, empty }: { idx: Index; ids: string[]; empty: string }) {
  if (!ids.length) return <p class="empty">{empty}</p>
  return (
    <div class="words">
      {ids.map((id) => {
        const e = idx.entity.get(id)
        return e?.agent
          ? <a href={href('agents', e.agent)}>{idx.agent.get(e.agent)?.name ?? e.text}</a>
          : <a href={href('entities', slugOf(id))}>{e?.text ?? slugOf(id)}</a>
      })}
    </div>
  )
}

function MemoryDetail({ idx, file, sel }: { idx: Index; file: MemoryFile; sel: number | null }) {
  const days = file.days
  const m = (sel != null ? days.find((d) => d.day === sel) : null) ?? days[days.length - 1]
  const core = useMemo(() => {
    const n = new Map<string, number>()
    for (const d of days) for (const e of d.entities) n.set(e, (n.get(e) ?? 0) + 1)
    return [...n.entries()].sort((a, b) => b[1] - a[1]).slice(0, 14)
  }, [file])
  const v = drift(m)
  return (
    <div class="cols">
      <section class="well">
        <h2>What it always kept</h2>
        <p class="quiet mt-s" style={{ fontSize: '14px' }}>Entities present in the most kept memories.</p>
        {core.length ? (
          <ul class="rows mt-s">
            {core.map(([id, n]) => {
              const e = idx.entity.get(id)
              const link = e?.agent ? href('agents', e.agent) : href('entities', slugOf(id))
              const text = e?.agent ? idx.agent.get(e.agent)?.name ?? e.text : e?.text ?? slugOf(id)
              return <li><a class="row" href={link}><span class="grow">{text}</span><span class="fig">{n} of {days.length}</span></a></li>
            })}
          </ul>
        ) : <p class="empty">No entities in its memories.</p>}
      </section>
      <section class="well">
        <div class="head">
          <h2>Day {m.day}, {longDate(dateOf(idx, m.day))}</h2>
          {v == null ? <span class="quiet" style={{ fontSize: '14px' }}>no earlier entity set</span> : <span style={{ fontSize: '14px' }}>drift <span class="fig hi">{v.toFixed(2)}</span></span>}
        </div>
        <div class="pcols">
          <div>
            <h3>Added</h3>
            <div class="mt-s"><EntityWords idx={idx} ids={m.added} empty="nothing new" /></div>
          </div>
          <div>
            <h3>Dropped</h3>
            <div class="mt-s"><EntityWords idx={idx} ids={m.dropped} empty="nothing dropped" /></div>
          </div>
        </div>
        <h3 class="mt">The memory, as it begins</h3>
        <blockquote class="excerpt mt-s"><Redacted text={clean(m.excerpt)} />{m.chars > m.excerpt.length ? <span class="quiet"> …</span> : null}</blockquote>
        <p class="ref fig" title="dataset row id">{m.ref} <span>{fmt(m.chars)} chars</span></p>
      </section>
    </div>
  )
}
