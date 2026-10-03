import { useMemo, useState } from 'preact/hooks'
import { GROUPS, GROUP_NAME, fmt, groupOf, short, useLoad, type Group, type Index, type Summary } from '../data'
import { TopEntities } from './agents'
import { Legend, Picks, Tip, GroupDot, linear, nearestIndex, niceMax, useWidth } from '../chart'
import { dayArg, go, href, type Route } from '../route'
import { RichText } from '../text'

export type Metric = 'chat' | 'sessions' | 'thinking' | 'mentions'
const METRICS: { id: Metric; name: string }[] = [
  { id: 'chat', name: 'messages' },
  { id: 'sessions', name: 'computer sessions' },
  { id: 'thinking', name: 'thinking' },
  { id: 'mentions', name: 'entity mentions' },
]

export function dateOf(idx: Index, day: number): string {
  const d = idx.day.get(day)
  if (d) return d.date
  const t = new Date(Date.UTC(2025, 3, 1 + day))
  return Number.isFinite(t.getTime()) ? t.toISOString().slice(0, 10) : ''
}
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
export const longDate = (iso: string) => `${Number(iso.slice(8, 10))} ${MONTHS[Number(iso.slice(5, 7)) - 1]} ${iso.slice(0, 4)}`
const monthYear = (iso: string) => `${MONTHS[Number(iso.slice(5, 7)) - 1]} ${iso.slice(0, 4)}`

interface Stack { day: number; parts: Record<Group, number>; total: number }

const stackCache = new WeakMap<Index, Map<Metric, Stack[]>>()
export function stacks(idx: Index, metric: Metric): Stack[] {
  let m = stackCache.get(idx)
  if (!m) stackCache.set(idx, (m = new Map()))
  const hit = m.get(metric)
  if (hit) return hit
  const tl = idx.b.views.timeline
  const series = tl.series[metric] ?? {}
  const out: Stack[] = tl.days.map((day) => ({ day, parts: { anthropic: 0, openai: 0, google: 0, other: 0, human: 0 }, total: 0 }))
  for (const [key, vals] of Object.entries(series)) {
    // mentions are keyed by label, not by agent: fold them into one neutral series
    const g: Group = metric === 'mentions' ? 'other' : groupOf(idx.agent.get(key)?.family)
    vals.forEach((v, i) => {
      if (!v) return
      out[i].parts[g] += v
      out[i].total += v
    })
  }
  m.set(metric, out)
  return out
}

export function ActivityChart({ idx, metric, selected, onPick, height = 220, compact = false }: {
  idx: Index; metric: Metric; selected?: number | null; onPick?: (day: number) => void; height?: number; compact?: boolean
}) {
  const [ref, width] = useWidth<HTMLDivElement>()
  const [hover, setHover] = useState<{ i: number; x: number; y: number } | null>(null)
  const data = stacks(idx, metric)
  const range = idx.b.manifest.range
  const goals = idx.b.goals
  const padL = compact ? 4 : 44
  const padR = 8
  const bandH = compact ? 6 : 10
  const axisH = compact ? 0 : 34
  const plotH = height - bandH - axisH - 8
  const x = linear(range.first_day - 0.5, range.last_day + 0.5, padL, width - padR)
  const maxV = niceMax(Math.max(1, ...data.map((d) => d.total)))
  const y = linear(0, maxV, plotH, 4)
  const bw = Math.max(1, (width - padL - padR) / (range.last_day - range.first_day + 1) - (width > 900 ? 0.4 : 0))
  const order: Group[] = metric === 'mentions' ? ['other'] : GROUPS
  const ticks = [0, maxV / 2, maxV]
  const dayTicks = (width < 560 ? [1, 200, 400] : [1, 100, 200, 300, 400, 500]).filter((d) => d >= range.first_day && d <= range.last_day)

  const onMove = (e: PointerEvent) => {
    const r = (e.currentTarget as SVGElement).getBoundingClientRect()
    const px = e.clientX - r.left
    const i = nearestIndex(data, (px - padL) / ((width - padL - padR) / (range.last_day - range.first_day + 1)) + range.first_day - 0.5)
    if (i < 0) return
    setHover({ i, x: x(data[i].day), y: Math.max(20, y(data[i].total)) })
  }
  const onKey = (e: KeyboardEvent) => {
    if (!onPick || !data.length) return
    const cur = selected != null ? data.findIndex((d) => d.day >= selected) : data.length - 1
    if (e.key === 'ArrowRight') { e.preventDefault(); onPick(data[Math.min(data.length - 1, cur + 1)].day) }
    if (e.key === 'ArrowLeft') { e.preventDefault(); onPick(data[Math.max(0, cur - 1)].day) }
  }
  const h = hover ? data[hover.i] : null
  const goalAt = (day: number) => goals.find((g) => day >= g.first_day && day <= g.last_day)

  if (!data.length) {
    return <div class="chart" ref={ref}><p class="empty">No daily activity in this bundle yet.</p></div>
  }
  return (
    <div class="chart" ref={ref}>
      <svg
        width={width}
        height={height}
        role="img"
        aria-label={`Daily ${metric} over ${data.length} active days`}
        tabIndex={onPick ? 0 : undefined}
        onPointerMove={onMove}
        onPointerLeave={() => setHover(null)}
        onClick={() => h && onPick?.(h.day)}
        onKeyDown={onKey}
        style={{ cursor: onPick ? 'pointer' : 'default' }}
      >
        {!compact &&
          ticks.map((t) => (
            <g>
              <line class="grid" x1={padL} x2={width - padR} y1={y(t)} y2={y(t)} />
              <text x={padL - 6} y={y(t) + 4} text-anchor="end">{short(t)}</text>
            </g>
          ))}
        {data.map((d) => {
          let acc = 0
          return order.map((g) => {
            const v = d.parts[g]
            if (!v) return null
            const y0 = y(acc)
            acc += v
            const y1 = y(acc)
            return <rect x={x(d.day) - bw / 2} y={y1} width={bw} height={Math.max(0.6, y0 - y1)} fill={`var(--g-${g})`} />
          })
        })}
        <line class="axis" x1={padL} x2={width - padR} y1={plotH + 0.5} y2={plotH + 0.5} />
        {goals.map((g, i) => {
          const sel = selected != null && selected >= g.first_day && selected <= g.last_day
          const hov = h && h.day >= g.first_day && h.day <= g.last_day
          return (
            <rect
              x={x(g.first_day - 0.5) + 0.5}
              y={plotH + 4}
              width={Math.max(1, x(g.last_day + 0.5) - x(g.first_day - 0.5) - 1)}
              height={bandH}
              fill={sel || hov ? 'var(--ink-2)' : i % 2 ? 'var(--ink-4)' : 'var(--ink-3)'}
              opacity={sel || hov ? 0.9 : 0.55}
            >
              <title>{g.goal}</title>
            </rect>
          )
        })}
        {!compact &&
          dayTicks.map((d) => (
            <g>
              <text x={x(d)} y={plotH + bandH + 18} text-anchor={d === 1 ? 'start' : 'middle'}>day {d}</text>
              <text x={x(d)} y={plotH + bandH + 31} text-anchor={d === 1 ? 'start' : 'middle'}>{monthYear(dateOf(idx, d))}</text>
            </g>
          ))}
        {selected != null && (
          <line x1={x(selected)} x2={x(selected)} y1={0} y2={plotH + bandH + 4} stroke="var(--safelight)" stroke-width={1.5} />
        )}
        {h && <line x1={x(h.day)} x2={x(h.day)} y1={0} y2={plotH} stroke="var(--ink-2)" stroke-width={1} opacity={0.6} />}
      </svg>
      {h && hover && (
        <Tip x={hover.x} y={hover.y} w={width}>
          <div><b>Day {h.day}</b> <span class="fig">{dateOf(idx, h.day)}</span></div>
          {order.filter((g) => h.parts[g]).map((g) => (
            <div class="r">
              <span>{metric === 'mentions' ? 'mentions' : <><GroupDot g={g} /> {GROUP_NAME[g]}</>}</span>
              <span class="fig">{fmt(h.parts[g])}</span>
            </div>
          ))}
          {goalAt(h.day) && <div class="quiet" style={{ marginTop: '4px', fontSize: '12.5px' }}>{goalAt(h.day)!.goal}</div>}
        </Tip>
      )}
    </div>
  )
}

export function Timeline({ idx, route }: { idx: Index; route: Route }) {
  const [metric, setMetric] = useState<Metric>('chat')
  const days = idx.b.days
  const fallback = useMemo(() => {
    const withSummary = days.filter((d) => idx.village.has(d.day))
    return (withSummary[withSummary.length - 1] ?? days[days.length - 1])?.day ?? null
  }, [idx])
  const sel = dayArg(route.arg, idx.b.manifest.range.first_day, idx.b.manifest.range.last_day) ?? fallback
  const pick = (day: number) => go('timeline', day, null, true)
  const active = idx.b.views.timeline.days.length
  const span = idx.b.manifest.range.last_day - idx.b.manifest.range.first_day + 1

  return (
    <div class="stack">
      <section class="well">
        <div class="head">
          <h1 class="place">Timeline</h1>
          <Picks label="series" value={metric} options={METRICS} onPick={setMetric} />
        </div>
        {metric === 'mentions' ? (
          <p class="quiet" style={{ fontSize: '14px', marginBottom: '10px' }}>Entity mentions found by oneiroNER, all labels together.</p>
        ) : (
          <div style={{ marginBottom: '10px' }}><Legend /></div>
        )}
        <ActivityChart idx={idx} metric={metric} selected={sel} onPick={pick} height={250} />
        <p class="quiet mt-s" style={{ fontSize: '14px' }}>
          {active} active days out of {span}. The strip under the bars marks each village goal. Pick a day to read it.
        </p>
      </section>
      {sel != null && <DayPanel idx={idx} day={sel} />}
    </div>
  )
}

function DayPanel({ idx, day }: { idx: Index; day: number }) {
  const d = idx.day.get(day)
  const preview = idx.village.get(day)
  const [full, setFull] = useState(false)
  const whole = useLoad<Summary>(full ? `summaries/village/${day}.json` : null)
  const date = dateOf(idx, day)
  const goal = idx.b.goals.find((g) => day >= g.first_day && day <= g.last_day)
  const summary = full && whole.data ? whole.data : preview
  const agents = (d?.active_agents ?? []).map((id) => idx.agent.get(id)).filter(Boolean)

  return (
    <div class="cols">
      <section class="well">
        <h2>Day {day}, {longDate(date)}</h2>
        {goal && <p class="read mt-s">{goal.goal.trim()}</p>}
        {d ? (
          <>
            <div class="kv mt">
              <div>messages <span class="fig">{fmt(d.counts.chat)}</span></div>
              <div>sessions <span class="fig">{fmt(d.counts.sessions)}</span></div>
              <div>thinking <span class="fig">{fmt(d.counts.thinking)}</span></div>
              <div>memories <span class="fig">{fmt(d.counts.memories)}</span></div>
              <div>mentions <span class="fig">{fmt(d.counts.mentions)}</span></div>
            </div>
            <h3 class="mt">Agents at work</h3>
            <div class="words mt-s">
              {agents.map((a) => (
                <a class="who" href={href('agents', a!.id)}>
                  <GroupDot g={groupOf(a!.family)} /> {a!.name}
                </a>
              ))}
            </div>
            <h3 class="mt">Most mentioned</h3>
            <div class="mt-s"><TopEntities idx={idx} list={d.top_entities} /></div>
          </>
        ) : (
          <p class="empty mt">No activity was sliced for this day.</p>
        )}
      </section>
      <section class="well">
        <div class="head">
          <h2>What happened</h2>
          {preview?.truncated && !full && (
            <button class="verb" type="button" onClick={() => setFull(true)}>read the whole day</button>
          )}
        </div>
        {summary ? (
          <div class="summary txt" style={{ paddingTop: 0 }}>
            <RichText text={summary.text} cites={summary.cites} day={day} block />
            {full && whole.loading && <p class="quiet">loading the full text</p>}
          </div>
        ) : (
          <p class="empty">No village summary for this day.</p>
        )}
        {summary && <p class="quiet mt" style={{ fontSize: '14px' }}>{summary.cites.some((c) => c.ref.startsWith('sum:')) ? 'The daily summary AI Digest wrote.' : `Written by ${summary.model ?? 'the summarizer'}.`} Numbers in brackets open the cited source.</p>}
      </section>
    </div>
  )
}
