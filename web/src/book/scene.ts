import type { Index } from '../data'
import type { Houses } from './houses'
import { GOAL_COLOUR } from './loom'
import { moodColour, type Cuts } from './mood'
import { lensAgent, type Lens, type MomentKind, type PropKind, type Score } from './types'

// The village as a scene for the plate: one entry per house, the day's facts only. Drawing lives in village.ts (the pen);
// this file decides what is lit, who walks, and what is faint under the lens. Pure: the same inputs give the same scene.

export type Lab = 'anthropic' | 'openai' | 'google' | 'other'
export interface HouseScene {
  n: number; founded: boolean; present: boolean; lab: Lab | null; window: string | null; smoke: number
  arriving: boolean; leaving: boolean; ghost: boolean; mark: 'pin' | 'swing' | null
  away: boolean   // the summarising model read the day's posts as elsewhere: the resident is out on the road
  idle: boolean   // no messages recorded: shutters closed
  agent: string | null
}
export interface Pair { a: number; b: number; w: number; agents: [string, string] }   // house numbers; 0 = the visitor (the humans)
export interface Prop { kind: PropKind; label: string | null }
export interface Moment { kind: MomentKind; houses: number[]; label: string | null; start: number }
export interface VillageScene {
  houses: HouseScene[]; visitor: number; t: number; lens: 'village' | 'goal' | 'memory' | 'humans' | 'agent'
  board: string | null; pairs: Pair[]
  props: Prop[]; moments: Moment[]
}

// The week's goal as it fits a notice board: one clause, at most 60 characters.
export function boardText(goal: string): string {
  const g = goal.trim().replace(/[.!]+$/, '')
  if (g.length <= 60) return g
  const cut = g.slice(0, 58)
  return cut.slice(0, Math.max(30, cut.lastIndexOf(' '))) + '…'
}

// Today's talking pairs: mentions and replies between residents (both directions folded), the three strongest.
export function pairsOf(idx: Index, houses: Houses, day: number): Pair[] {
  const houseNo = (agent: string) => (agent === 'human' ? 0 : houses.houseOf.get(agent)?.n ?? null)
  const per = new Map<string, { w: number; a: string; b: string }>()
  for (const e of idx.b.views.interactions.edges) {
    const hit = e.by_day.find((x) => x[0] === day)
    if (!hit || e.from === e.to) continue
    const key = e.from < e.to ? `${e.from}|${e.to}` : `${e.to}|${e.from}`
    const cur = per.get(key)
    per.set(key, { w: (cur?.w ?? 0) + hit[1], a: e.from, b: e.to })
  }
  const top = [...per.values()].sort((x, y) => y.w - x.w).slice(0, 3)
  const max = top[0]?.w ?? 1
  return top.map((e) => ({ a: houseNo(e.a), b: houseNo(e.b), w: e.w / max, agents: [e.a, e.b] as [string, string] }))
    .filter((p): p is Pair => p.a != null && p.b != null && p.a !== p.b)
}

// What the room talked about most today: the day's top tagged entities, never a person.
const compact = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, '')
export function topicsOf(idx: Index, day: number, n = 3): string[] {
  // the residents are who, not what: an entity that names an agent is skipped, and so is a repeat of a chosen topic
  const who = new Set(idx.b.agents.flatMap((a) => [compact(a.name), compact(a.id), compact(a.model ?? '')]).filter(Boolean))
  const out: string[] = [], seen: string[] = []
  for (const t of idx.day.get(day)?.top_entities ?? []) {
    const e = idx.entity.get(t.entity)
    if (!e || !/^(ORG|PRODUCT|PLACE|EVENT)$/.test(e.label)) continue
    const c = compact(e.text)
    if (!c || who.has(c) || [...who].some((w) => w.length > 3 && (c.includes(w) || w.includes(c)))) continue
    if (seen.some((x) => x === c || x.startsWith(c) || c.startsWith(x))) continue
    seen.push(c); out.push(e.text)
    if (out.length >= n) break
  }
  return out
}

const LAMP = 'oklch(0.86 0.09 80)'   // a warm window
const labOf = (family: string | null | undefined): Lab => (family === 'anthropic' || family === 'openai' || family === 'google' ? family : 'other')

const maxChatCache = new WeakMap<Index, number>()
function maxChat(idx: Index): number {
  const hit = maxChatCache.get(idx)
  if (hit != null) return hit
  let m = 1
  for (const [agent, row] of Object.entries(idx.b.views.timeline.series.chat ?? {})) {
    if (agent === 'human') continue
    for (const v of row) if (v > m) m = v
  }
  maxChatCache.set(idx, m)
  return m
}

export function buildScene(a: {
  idx: Index; houses: Houses; day: number; t: number; lens: Lens; cuts: Cuts
  adh: Map<string, Map<number, { score: Score; note: string }>>
  mem: Map<string, Map<number, number>>; memCuts: { lo: number; hi: number }
  pinDays: Map<number, string | null>   // day → the agent a finding pins to (null = the day only)
  board?: string | null
  props?: Prop[]; moments?: Moment[]
}): VillageScene {
  const { idx, houses, day, lens } = a
  const focus = lensAgent(lens)
  const t = idx.b.views.timeline, k = t.days.indexOf(day)
  const chatOn = (agent: string) => (k < 0 ? 0 : t.series.chat?.[agent]?.[k] ?? 0)
  const m = idx.b.views.mood, mk = m ? m.days.indexOf(day) : -1
  const moodOn = (agent: string) => { const s = m?.agents[agent]; return mk < 0 || !s ? { v: null, a: null } : { v: s.v[mk], a: s.a[mk] } }
  const active = new Set(idx.day.get(day)?.active_agents ?? [])
  const top = maxChat(idx)
  const out: HouseScene[] = houses.houses.map((h) => {
    // the newest resident holds the house (as on the cloth)
    const here = h.residents.filter((r) => active.has(r.agent) && r.from <= day && day <= r.to).sort((x, y) => y.from - x.from)
    const r = here[0] ?? null
    const founded = h.residents.some((x) => x.from <= day)
    if (!r) return { n: h.n, founded, present: false, lab: null, window: null, smoke: 0, arriving: false, leaving: false, ghost: false, mark: null, away: false, idle: false, agent: null }
    const agent = r.agent
    const mood = moodOn(agent)
    const score = a.adh.get(agent)?.get(day)?.score ?? null
    const kept = a.mem.get(agent)?.get(day) ?? null
    let window: string | null = null
    if (lens === 'goal') window = score && score !== 'idle' ? GOAL_COLOUR[score] : null
    else if (lens === 'memory') window = kept == null ? null : kept >= a.memCuts.hi ? 'oklch(0.34 0.03 60)' : kept >= a.memCuts.lo ? 'oklch(0.52 0.06 45)' : 'oklch(0.55 0.17 25)'
    else if (lens === 'humans') window = null
    // the village lens shows occupancy, not a reading of feeling: a lamp is lit where someone worked today
    else if (focus) window = agent === focus ? (mood.v != null && mood.a != null ? moodColour(mood.v, mood.a, a.cuts) : LAMP) : null
    else window = chatOn(agent) > 0 ? LAMP : null
    const ghost = focus ? agent !== focus : lens === 'humans'
    const pinned = a.pinDays.get(day)
    return {
      n: h.n, founded, present: true, lab: labOf(idx.agent.get(agent)?.family), window, smoke: Math.sqrt(chatOn(agent) / top),
      arriving: r.from === day, leaving: r.to === day, ghost,
      mark: pinned !== undefined && (pinned === agent || (pinned === null && h.n === 1)) ? 'pin' : null,
      away: score === 'off', idle: score === 'idle' || (score == null && chatOn(agent) === 0), agent,
    }
  })
  const human = chatOn('human'), total = idx.day.get(day)?.counts.chat ?? 0
  const visitor = total > 0 ? Math.min(1, human / total) : 0
  const sceneLens: VillageScene['lens'] = focus ? 'agent' : lens === 'goal' || lens === 'memory' || lens === 'humans' ? lens : 'village'
  return { houses: out, visitor: lens === 'humans' ? Math.max(visitor, human > 0 ? 0.5 : 0) : visitor, t: a.t, lens: sceneLens, board: a.board ?? null, pairs: pairsOf(idx, houses, day), props: a.props ?? [], moments: a.moments ?? [] }
}
