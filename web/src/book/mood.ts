import type { Bundle } from '../data'

// The village's mood for a day: the mean over the agents the mood head saw that day (the human pseudo-agent excluded).
export interface DayMood { day: number; v: number | null; a: number | null; d: number | null; n: number }

export function villageMood(b: Bundle): Map<number, DayMood> {
  const out = new Map<number, DayMood>()
  const m = b.views.mood
  if (!m) return out
  m.days.forEach((day, i) => {
    let v = 0, a = 0, d = 0, n = 0
    for (const [id, s] of Object.entries(m.agents)) {
      if (id === 'human') continue
      const vi = s.v[i], ai = s.a[i], di = s.d[i]
      if (vi == null || ai == null) continue
      v += vi; a += ai; d += di ?? 0; n++
    }
    out.set(day, n ? { day, v: v / n, a: a / n, d: d / n, n } : { day, v: null, a: null, d: null, n: 0 })
  })
  return out
}

// Rank cuts over all days: valence terciles, arousal and dominance medians. Keys and colours are relative to the village's own year.
export interface Cuts { v1: number; v2: number; a1: number; d1: number; vLo: number; vHi: number; aLo: number; aHi: number }

function q(sorted: number[], p: number): number {
  if (!sorted.length) return 0
  const i = Math.min(sorted.length - 1, Math.max(0, Math.floor(p * (sorted.length - 1))))
  return sorted[i]
}

export function moodCuts(moods: Iterable<DayMood>): Cuts {
  const vs: number[] = [], as: number[] = [], ds: number[] = []
  for (const m of moods) {
    if (m.v != null) vs.push(m.v)
    if (m.a != null) as.push(m.a)
    if (m.d != null) ds.push(m.d)
  }
  vs.sort((x, y) => x - y); as.sort((x, y) => x - y); ds.sort((x, y) => x - y)
  return {
    v1: q(vs, 1 / 3), v2: q(vs, 2 / 3), a1: q(as, 0.5), d1: q(ds, 0.5),
    vLo: q(vs, 0.03), vHi: q(vs, 0.97), aLo: q(as, 0.03), aHi: q(as, 0.97),
  }
}

// Cuts over every agent-day read, for colouring one agent's thread or note (agents swing wider than the village mean).
export function agentCuts(b: Bundle): Cuts {
  const rows: DayMood[] = []
  const m = b.views.mood
  if (m) for (const [id, s] of Object.entries(m.agents)) {
    if (id === 'human') continue
    s.v.forEach((v, i) => { const a = s.a[i]; if (v != null && a != null) rows.push({ day: i, v, a, d: s.d[i] ?? null, n: 1 }) })
  }
  return moodCuts(rows)
}

export type MoodKey ='v-hi_a-hi' | 'v-hi_a-lo' | 'v-mid_a-hi' | 'v-mid_a-lo' | 'v-lo_a-hi' | 'v-lo_a-lo' | 'neutral'

export function moodKey(v: number | null | undefined, a: number | null | undefined, c: Cuts): MoodKey {
  if (v == null || a == null) return 'neutral'
  const vk = v >= c.v2 ? 'hi' : v >= c.v1 ? 'mid' : 'lo'
  const ak = a >= c.a1 ? 'hi' : 'lo'
  return `v-${vk}_a-${ak}` as MoodKey
}

const clamp01 = (x: number) => Math.min(1, Math.max(0, x))
export const vRank = (v: number, c: Cuts) => (c.vHi === c.vLo ? 0.5 : clamp01((v - c.vLo) / (c.vHi - c.vLo)))
export const aRank = (a: number, c: Cuts) => (c.aHi === c.aLo ? 0.5 : clamp01((a - c.aLo) / (c.aHi - c.aLo)))

// Pigment: hue runs from a cool indigo (low valence) to a warm vermilion (high); arousal adds chroma.
export function moodColour(v: number | null | undefined, a: number | null | undefined, c: Cuts, alpha = 1): string {
  if (v == null || a == null) return `oklch(0.62 0.02 80 / ${alpha})`
  // indigo (265) through teal and olive to vermilion (35); the arousal sets how much pigment is on the thread
  const h = 265 - 230 * vRank(v, c)
  const ch = 0.07 + 0.13 * aRank(a, c)
  const l = 0.42 + 0.2 * vRank(v, c)
  return `oklch(${l.toFixed(3)} ${ch.toFixed(3)} ${h.toFixed(0)} / ${alpha})`
}

// Plain words for a drawer: three of them, relative to the village's year.
export function moodWords(v: number | null, a: number | null, d: number | null, c: Cuts): string {
  if (v == null || a == null) return 'no mood read'
  const w1 = v >= c.v2 ? 'warm' : v >= c.v1 ? 'even' : 'cool'
  const w2 = a >= c.a1 ? 'charged' : 'calm'
  const w3 = d == null ? '' : d >= c.d1 ? 'leading' : 'yielding'
  return [w1, w2, w3].filter(Boolean).join(', ')
}

// Mean mood over a set of days (a chapter).
export function meanMood(days: number[], moods: Map<number, DayMood>): { v: number | null; a: number | null } {
  let v = 0, a = 0, n = 0
  for (const d of days) {
    const m = moods.get(d)
    if (!m || m.v == null || m.a == null) continue
    v += m.v; a += m.a; n++
  }
  return n ? { v: v / n, a: a / n } : { v: null, a: null }
}
