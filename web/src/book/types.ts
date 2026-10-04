import type { Bundle, Cite } from '../data'

// data/book.json (schema v1.3): 51 chapters, one per village goal, and 404 day paragraphs. Luna wrote them; every sentence cites.
export interface Beat { text: string; cites: number[] }
export interface Figure { value: string; label: string; cites: number[] }
export interface Chapter {
  n: number; goal: string; title: string
  first_day: number; last_day: number; first_date: string; last_date: string
  days: number[]; agents: string[]
  stats: { days: number; chat: number; sessions: number; memories: number; human_chat: number; agents: number }
  text: string; cites: Cite[]
  // schema 1.1: a hook line, the paragraph as short beats, the one number the chapter turns on
  hook?: string | null; beats?: Beat[] | null; figure?: Figure | null
  // schema 1.2: the stage, cast by the model from the chapter text (briefs/stage-catalogue.md)
  stage?: Stage | null
}
export type PropKind = 'stall' | 'mailbox' | 'ballot' | 'scaffold' | 'gallery' | 'chessboard' | 'stage' | 'press' | 'garden' | 'lanterns' | 'cloud' | 'signpost'
export type MomentKind = 'celebrate' | 'build' | 'send' | 'vote' | 'win' | 'fail' | 'gather' | 'play'
export interface Stage { props: { kind: PropKind; label: string | null }[]; moments: { beat: number; kind: MomentKind; agents: string[]; label: string | null }[] }

// A beat as the page reads it: its own cites only (RichText appends receipts for unmarked ones), and the day it is about.
export interface BeatView { text: string; cites: Cite[]; day: number | null }
export function beatsOf(c: Chapter): BeatView[] {
  const byN = new Map(c.cites.map((x) => [x.n, x]))
  const mk = (text: string): BeatView => {
    const ns = [...text.matchAll(/\[(\d+)\]/g)].map((m) => Number(m[1]))
    const cites = ns.map((n) => byN.get(n)).filter((x): x is Cite => !!x)
    const days = cites.map((x) => x.day).filter((d): d is number => d != null && c.days.includes(d))
    return { text, cites, day: days.length ? Math.min(...days) : null }
  }
  if (c.beats?.length) return c.beats.map((b) => mk(b.text))
  if (!c.text) return []
  // until the beats are written: the paragraph's sentences, paired up so there are never more than six
  const sentences = c.text.split(/(?<=\.)\s+(?=[A-Z"“])/).map((s) => s.trim()).filter(Boolean)
  const per = Math.max(1, Math.ceil(sentences.length / 6))
  const out: string[] = []
  for (let i = 0; i < sentences.length; i += per) out.push(sentences.slice(i, i + per).join(' '))
  return out.map(mk)
}
export const hookOf = (c: Chapter): string => c.hook?.trim() || c.title
export interface DayPara { day: number; date: string; chapter: number; text: string; cites: Cite[] }
export interface BookFile { schema: string; generated?: string; model?: string; chapters: Chapter[]; days: DayPara[] }

export type Score = 'on' | 'partly' | 'off' | 'idle' | null
export interface AdherenceFile { agent: string; days: { day: number; chapter: number; score: Score; note: string }[] }
export interface TalkFile { agent: string; days: { day: number; partners: [string, number][] }[] }

export interface PairTalk { a: string; b: string; w: number; text: string | null; cites: Cite[] }
export interface PairsFile { schema: string; generated?: string; model?: string; days: Record<string, PairTalk[]> }

export type Lens = 'village' | 'memory' | 'humans' | 'goal' | `agent:${string}`

export function parseLens(raw: string | null, agents: Set<string>): Lens {
  if (raw === 'memory' || raw === 'humans' || raw === 'goal') return raw
  if (raw && raw.startsWith('agent:') && agents.has(raw.slice(6))) return raw as Lens
  return 'village'
}
export const lensAgent = (l: Lens): string | null => (l.startsWith('agent:') ? l.slice(6) : null)

// When book.json is not there yet, the chapters still exist: the village goals and the days that carry them.
export function deriveChapters(b: Bundle): Chapter[] {
  const byGoal = new Map<string, number[]>()
  for (const d of b.days) if (d.village_goal) byGoal.set(d.village_goal, [...(byGoal.get(d.village_goal) ?? []), d.day])
  const dayOf = new Map(b.days.map((d) => [d.day, d]))
  return b.goals.map((g, i) => {
    const days = byGoal.get(g.goal) ?? []
    const first = days[0] ?? g.first_day, last = days[days.length - 1] ?? g.last_day
    const agents = new Set<string>()
    let chat = 0, sessions = 0, memories = 0
    for (const dn of days) {
      const d = dayOf.get(dn)
      if (!d) continue
      d.active_agents.forEach((a) => agents.add(a))
      chat += d.counts.chat; sessions += d.counts.sessions; memories += d.counts.memories
    }
    return {
      n: i + 1, goal: g.goal, title: g.goal.replace(/[.!]+\s*$/, ''),
      first_day: first, last_day: last,
      first_date: dayOf.get(first)?.date ?? g.start.slice(0, 10), last_date: dayOf.get(last)?.date ?? g.end.slice(0, 10),
      days, agents: [...agents],
      stats: { days: days.length, chat, sessions, memories, human_chat: 0, agents: agents.size },
      text: '', cites: [],
    }
  })
}

export const DAY_MS = 86400000
export function fmtDate(iso: string, withYear = true): string {
  const d = new Date(iso.slice(0, 10) + 'T00:00:00Z')
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'long', ...(withYear ? { year: 'numeric' } : {}), timeZone: 'UTC' })
}
export function weekday(iso: string): string {
  const d = new Date(iso.slice(0, 10) + 'T00:00:00Z')
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleDateString('en-GB', { weekday: 'long', timeZone: 'UTC' })
}
export function fmtRange(a: string, b: string): string {
  const sameYear = a.slice(0, 4) === b.slice(0, 4)
  return `${fmtDate(a, !sameYear)} to ${fmtDate(b)}`
}
