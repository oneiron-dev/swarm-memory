import { useEffect, useState } from 'preact/hooks'

// Types follow docs/schema.md section 4 (swarm-bundle/1).

export interface Cite { n: number; ref: string; day?: number }
export interface Summary {
  id: string; scope: 'agent' | 'room' | 'village'; key: string; day: number; date: string
  text: string; cites: Cite[]; model?: string; truncated?: boolean
}
export interface EntityCount { entity: string; count: number }
export interface Goal { name: string; short: string | null; description: string | null; start: string | null; end: string | null }
export interface Agent {
  id: string; name: string; model: string | null; family: string; participating: boolean
  first_day: number | null; last_day: number | null; goals: Goal[]
  stats: { messages: number; tokens: number; days_active: number; sessions: number; memories: number }
  top_entities: EntityCount[]
}
export interface Room { id: string; name: string; deleted: string | null; first_day: number | null; last_day: number | null; messages: number }
export interface Day {
  day: number; date: string; village_goal: string | null; active_agents: string[]
  counts: { chat: number; sessions: number; thinking: number; memories: number; mentions: number }
  top_entities: EntityCount[]; summary: string | null
}
export interface VillageGoal { goal: string; start: string; end: string; first_day: number; last_day: number }
export interface Entity {
  id: string; text: string; label: string; aliases: string[]; count: number; n_agents: number
  first_day: number; last_day: number; examples: string[]; example_days: number[]; agent?: string
}
export interface Edge { from: string; to: string; w: number; kinds: { mention: number; reply: number }; by_day: [number, number][] }
export interface Manifest {
  schema: string; generated: string; dataset: string; cite: string
  range: { first_day: number; last_day: number; first_date: string; last_date: string }
  counts: Record<string, number>
  tagger: { name: string; checkpoint: string; labels: string[]; heads: string[]; snap?: string }
  summarizer: string
}
export interface Bundle {
  manifest: Manifest; agents: Agent[]; rooms: Room[]; days: Day[]; goals: VillageGoal[]; entities: Entity[]
  summaries: { village: Summary[] }
  views: {
    timeline: { days: number[]; series: Record<string, Record<string, number[]>> }
    interactions: { nodes: { id: string; messages: number }[]; edges: Edge[] }
    mood?: { days: number[]; agents: Record<string, { v: (number | null)[]; a: (number | null)[]; d: (number | null)[] }> }
  }
}
export interface EntityDetail { id: string; agents: Record<string, number>; timeline: [number, number][] }
export interface MemoryDay {
  day: number; ref: string; chars: number; entities: string[]; added: string[]; dropped: string[]
  jaccard_prev: number | null; excerpt: string
}
export interface MemoryFile { agent: string; days: MemoryDay[] }
export interface Source { time: string; agent: string | null; room: string | null; kind: string; text: string; spans: [number, number, string][] }
export type SourcesFile = Record<string, Source>
export interface Finding { title: string; body: string; cites: Cite[]; view?: string }

// One fetch per path, shared by every caller; a missing file resolves to null.
const cache = new Map<string, Promise<unknown>>()
export function load<T>(path: string): Promise<T | null> {
  let p = cache.get(path)
  if (!p) {
    p = fetch('./data/' + path)
      .then((r) => (r.ok ? r.json() : null))
      .catch(() => null)
    cache.set(path, p)
  }
  return p as Promise<T | null>
}

export function useLoad<T>(path: string | null): { data: T | null; loading: boolean } {
  const [state, setState] = useState<{ path: string | null; data: T | null; loading: boolean }>({ path: null, data: null, loading: !!path })
  useEffect(() => {
    if (!path) { setState({ path, data: null, loading: false }); return }
    let live = true
    setState((s) => (s.path === path ? s : { path, data: null, loading: true }))
    load<T>(path).then((data) => { if (live) setState({ path, data, loading: false }) })
    return () => { live = false }
  }, [path])
  return state.path === path ? state : { data: null, loading: !!path }
}

// Indexes built once from the bundle.
export interface Index {
  b: Bundle
  agent: Map<string, Agent>
  entity: Map<string, Entity>
  day: Map<number, Day>
  village: Map<number, Summary>
  agentEntities: Map<string, Entity[]> // agent id -> its agent entities, largest first
}

export function buildIndex(b: Bundle): Index {
  const agentEntities = new Map<string, Entity[]>()
  for (const e of b.entities) {
    if (!e.agent) continue
    const list = agentEntities.get(e.agent) ?? []
    list.push(e)
    agentEntities.set(e.agent, list)
  }
  for (const list of agentEntities.values()) list.sort((x, y) => y.count - x.count)
  return {
    b,
    agent: new Map(b.agents.map((a) => [a.id, a])),
    entity: new Map(b.entities.map((e) => [e.id, e])),
    day: new Map(b.days.map((d) => [d.day, d])),
    village: new Map(b.summaries.village.map((s) => [s.day, s])),
    agentEntities,
  }
}

// Families fold to five groups: three validated hues, "other models" and the human pseudo-agent.
export type Group = 'anthropic' | 'openai' | 'google' | 'other' | 'human'
export const GROUPS: Group[] = ['anthropic', 'openai', 'google', 'other', 'human']
export const GROUP_NAME: Record<Group, string> = {
  anthropic: 'Anthropic', openai: 'OpenAI', google: 'Google', other: 'other labs', human: 'humans',
}
export const FAMILY_NAME: Record<string, string> = {
  anthropic: 'Anthropic', openai: 'OpenAI', google: 'Google', xai: 'xAI', deepseek: 'DeepSeek',
  moonshot: 'Moonshot', zai: 'Z.ai', meta: 'Meta', other: 'other', human: 'humans',
}
export function groupOf(family: string | undefined): Group {
  if (family === 'anthropic' || family === 'openai' || family === 'google' || family === 'human') return family
  return 'other'
}

export const LABEL_NAME: Record<string, string> = {
  PERSON: 'person', PLACE: 'place', ORG: 'organisation', DATE: 'date', EVENT: 'event',
  RELATIONSHIP_REF: 'relationship', EMOTION: 'emotion', MONEY: 'money', PRODUCT: 'product',
}
export const labelName = (l: string) => LABEL_NAME[l] ?? l.toLowerCase()

export const fmt = (n: number | null | undefined) => (n == null ? '–' : n.toLocaleString('en-US'))
export function short(n: number): string {
  if (n >= 1e9) return (n / 1e9).toFixed(1) + 'B'
  if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M'
  if (n >= 1e4) return Math.round(n / 1e3) + 'k'
  return n.toLocaleString('en-US')
}
export const slugOf = (entityId: string) => entityId.replace(/^e:/, '')
