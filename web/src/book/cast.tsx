import { useEffect } from 'preact/hooks'
import { GroupDot } from '../chart'
import { FAMILY_NAME, fmt, groupOf, useLoad, type Index, type MemoryFile, type Summary } from '../data'
import { href } from '../route'
import { RichText } from '../text'
import { useMemo } from 'preact/hooks'
import { buildHouses } from './houses'
import { moodColour, moodWords, type Cuts } from './mood'
import type { AdherenceFile, Chapter, TalkFile } from './types'

function agentMood(idx: Index, agent: string, day: number): { v: number | null; a: number | null; d: number | null } {
  const m = idx.b.views.mood
  const s = m?.agents[agent]
  const i = m ? m.days.indexOf(day) : -1
  if (!s || i < 0) return { v: null, a: null, d: null }
  return { v: s.v[i] ?? null, a: s.a[i] ?? null, d: s.d[i] ?? null }
}

function chatOn(idx: Index, agent: string, day: number): number {
  const t = idx.b.views.timeline
  const i = t.days.indexOf(day)
  return i < 0 ? 0 : (t.series.chat?.[agent]?.[i] ?? 0)
}

// Who was in the room that day: chips, the lens agent first, then by how much each spoke, the humans last.
export function Cast({ idx, day, first, cuts, open, onPick }: {
  idx: Index; day: number; first: string | null; cuts: Cuts; open: string | null; onPick: (id: string) => void
}) {
  const d = idx.day.get(day)
  if (!d) return null
  const ids = [...d.active_agents]
  const humans = chatOn(idx, 'human', day)
  const sorted = ids
    .map((id) => ({ id, n: chatOn(idx, id, day) }))
    .sort((a, b) => (a.id === first ? -1 : b.id === first ? 1 : b.n - a.n))
  return (
    <div class="cast" role="list" aria-label="who was in the room">
      {sorted.map(({ id, n }) => {
        const a = idx.agent.get(id)
        const m = agentMood(idx, id, day)
        return (
          <button type="button" role="listitem" class={`who${open === id ? ' on' : ''}`} onClick={() => onPick(id)} title={`${n.toLocaleString('en-US')} messages`}>
            <GroupDot g={groupOf(a?.family)} />
            <span class="nm">{a?.name ?? id}</span>
            <i class="md" style={{ background: moodColour(m.v, m.a, cuts) }} aria-hidden="true" />
          </button>
        )
      })}
      {humans > 0 ? (
        <button type="button" role="listitem" class={`who${open === 'human' ? ' on' : ''}`} onClick={() => onPick('human')} title={`${humans} messages`}>
          <GroupDot g="human" /><span class="nm">the humans</span>
        </button>
      ) : null}
    </div>
  )
}

const SCORE: Record<string, string> = { on: 'yes, on the goal', partly: 'partly on the goal', off: 'elsewhere', idle: 'did little that day' }

// The agent's mood over its whole time in the village, today marked: one small line, the pigment colours of the loom.
function MoodLine({ idx, agent, day, cuts }: { idx: Index; agent: string; day: number; cuts: Cuts }) {
  const m = idx.b.views.mood
  const s = m?.agents[agent]
  if (!m || !s) return null
  const pts = m.days.map((d, i) => ({ d, v: s.v[i], a: s.a[i] })).filter((p): p is { d: number; v: number; a: number } => p.v != null && p.a != null)
  if (pts.length < 3) return null
  const W = 320, H = 44, L = 2, R = 2, T = 6, B = 6
  const d0 = pts[0].d, d1 = pts[pts.length - 1].d
  const vs = pts.map((p) => p.v)
  const lo = Math.min(cuts.vLo, ...vs), hi = Math.max(cuts.vHi, ...vs)
  const x = (d: number) => (d1 === d0 ? W / 2 : L + ((d - d0) / (d1 - d0)) * (W - L - R))
  const y = (v: number) => (hi === lo ? H / 2 : T + (1 - (v - lo) / (hi - lo)) * (H - T - B))
  const path = pts.map((p, i) => `${i ? 'L' : 'M'}${x(p.d).toFixed(1)} ${y(p.v).toFixed(1)}`).join(' ')
  const today = pts.find((p) => p.d === day)
  return (
    <figure class="moodline">
      <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`mood over days ${d0} to ${d1}`}>
        <defs>
          <linearGradient id={`ml-${agent}`} gradientUnits="userSpaceOnUse" x1={L} x2={W - R} y1={0} y2={0}>
            {pts.filter((_, i) => i % Math.max(1, Math.floor(pts.length / 40)) === 0).map((p) => <stop offset={`${(((x(p.d) - L) / (W - L - R)) * 100).toFixed(1)}%`} stop-color={moodColour(p.v, p.a, cuts)} />)}
          </linearGradient>
        </defs>
        <path d={path} fill="none" stroke={`url(#ml-${agent})`} stroke-width={1.8} stroke-linejoin="round" />
        {today ? <circle cx={x(today.d)} cy={y(today.v)} r={3.2} fill={moodColour(today.v, today.a, cuts)} stroke="var(--paper)" stroke-width={1.2} /> : null}
      </svg>
      <figcaption class="faint">its mood from day {d0} to day {d1}; the dot is today</figcaption>
    </figure>
  )
}

// The drawer: one agent, one day.
export function Drawer({ idx, agent, day, chapter, cuts, onClose }: {
  idx: Index; agent: string; day: number; chapter: Chapter; cuts: Cuts; onClose: () => void
}) {
  const a = idx.agent.get(agent)
  const human = agent === 'human'
  const { data: sums } = useLoad<Summary[]>(human ? null : `summaries/agent/${agent}.json`)
  const { data: adh } = useLoad<AdherenceFile>(human ? null : `adherence/${agent}.json`)
  const { data: talk } = useLoad<TalkFile>(`talk/${agent}.json`)
  const { data: mem } = useLoad<MemoryFile>(human ? null : `memory/${agent}.json`)
  useEffect(() => {
    const on = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', on)
    return () => window.removeEventListener('keydown', on)
  }, [onClose])
  const m = agentMood(idx, agent, day)
  const houses = useMemo(() => buildHouses(idx.b), [idx])
  const house = human ? null : houses.houseOf.get(agent) ?? null
  const seat = house ? house.residents.findIndex((r) => r.agent === agent) : -1
  const before = house && seat > 0 ? idx.agent.get(house.residents[seat - 1].agent)?.name ?? null : null
  const sum = sums?.find((s) => s.day === day) ?? null
  const ad = adh?.days.find((r) => r.day === day) ?? null
  const tk = talk?.days.find((r) => r.day === day) ?? null
  const me = mem?.days.find((r) => r.day === day) ?? null
  const msgs = chatOn(idx, agent, day)
  return (
    <>
    <div class="drawer-veil" onClick={onClose} aria-hidden="true" />
    <aside class="drawer" aria-label={`${a?.name ?? agent} on day ${day}`}>
      <header>
        <div>
          <p class="who-line"><GroupDot g={groupOf(a?.family)} /> {human ? 'people watching' : FAMILY_NAME[a?.family ?? 'other']}</p>
          <h2>{human ? 'The humans' : a?.name ?? agent}</h2>
          <p class="meta">Day {day}, {msgs.toLocaleString('en-US')} messages{house ? `. House ${house.n}${before ? `, after ${before}` : ''}, since day ${houses.first.get(agent) ?? '?'}` : ''}</p>
        </div>
        <button type="button" class="close" onClick={onClose} aria-label="close">×</button>
      </header>

      {!human ? (
        <section>
          <h3>Mood</h3>
          {m.v == null ? <p class="quiet">No read for this day.</p> : (
            <>
              <p class="mood-line"><i class="md big" style={{ background: moodColour(m.v, m.a, cuts) }} aria-hidden="true" /> {moodWords(m.v, m.a, m.d, cuts)}
                <span class="faint" title={`valence ${m.v.toFixed(2)}, arousal ${m.a!.toFixed(2)}${m.d != null ? `, dominance ${m.d.toFixed(2)}` : ''}`}> on this day</span></p>
              <MoodLine idx={idx} agent={agent} day={day} cuts={cuts} />
            </>
          )}
        </section>
      ) : null}

      {!human ? (
        <section>
          <h3>The goal</h3>
          <p class="quiet goal-q"><i>{chapter.goal.trim()}</i></p>
          {ad ? <p>{ad.score ? SCORE[ad.score] : 'no call'}{ad.note ? <span class="quiet">: {ad.note}</span> : null}</p> : <p class="quiet">{adh === null ? 'Not scored yet.' : 'Not scored for this day.'}</p>}
        </section>
      ) : null}

      <section>
        <h3>Talked with</h3>
        {tk && tk.partners.length ? (
          <p class="partners">{tk.partners.map(([id, n]) => <span><a href={href('agents', id)}>{id === 'human' ? 'the humans' : idx.agent.get(id)?.name ?? id}</a> <span class="fig">{fmt(n)}</span></span>)}</p>
        ) : <p class="quiet">{talk === null ? 'Not computed yet.' : 'No partner recorded.'}</p>}
      </section>

      {!human ? (
        <section>
          <h3>Its day</h3>
          {sum ? <p class="read"><RichText text={sum.text} cites={sum.cites} day={day} /></p> : <p class="quiet">No summary for this day.</p>}
        </section>
      ) : null}

      {!human ? (
        <section>
          <h3>What it remembered</h3>
          {me ? (
            <>
              <p class="faint small-line">{me.entities.length} things named, {me.added.length} new, {me.dropped.length} gone since the day before{me.jaccard_prev != null ? `; ${Math.round((1 - me.jaccard_prev) * 100)}% of the named things changed` : ''}. Named things are the tagger's entities; this measures overlap, not forgetting.</p>
              <blockquote class="mem">{me.excerpt}</blockquote>
              <p><a href={href('memory', agent, day)}>the memory view</a></p>
            </>
          ) : <p class="quiet">No memory kept for this day.</p>}
        </section>
      ) : null}

      <footer>
        {!human ? <p><a href={href('book', `agent:${agent}`, chapter.n, day)}>follow {a?.name ?? agent}'s thread through the book</a></p> : null}
        <p><a href={href('agents', agent)}>everything about {human ? 'the humans' : a?.name ?? agent} in the appendix</a></p>
        {a?.model && !human ? <p class="faint small-line">model id {a.model}</p> : null}
      </footer>
    </aside>
    </>
  )
}
