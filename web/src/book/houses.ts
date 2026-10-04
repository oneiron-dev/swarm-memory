import type { Bundle } from '../data'

// The village has houses; models live in them and get swapped out. A house is a chain of residents: the explicit
// replacements AI Digest announced in its daily texts, plus handovers where one agent's last active day and another's
// first are within three days of each other. Everything else is a new house (the village grew from 4 seats to 32).

export interface Resident { agent: string; from: number; to: number; announced?: boolean }   // announced: AI Digest said so; else inferred from timing
export interface House { n: number; id: string; residents: Resident[] }
export interface Houses { houses: House[]; houseOf: Map<string, House>; first: Map<string, number>; last: Map<string, number> }

// announced in the AI Digest daily texts (display names; resolved to ids at runtime)
const ANNOUNCED: [string, string][] = [
  ['GPT-4o', 'GPT-4.1'],
  ['o1', 'o3'],
  ['Claude 3.5 Sonnet', 'Gemini 2.5 Pro'],
  ['GPT-4.1', 'o4-mini'],
  ['o4-mini', 'Claude Opus 4'],
]
const HANDOVER_DAYS = 3

export function buildHouses(b: Bundle): Houses {
  const first = new Map<string, number>(), last = new Map<string, number>()
  for (const d of b.days) for (const a of d.active_agents) { if (!first.has(a)) first.set(a, d.day); last.set(a, d.day) }
  const byName = new Map(b.agents.map((a) => [a.name, a.id]))
  const announcedNext = new Map<string, string>()
  for (const [from, to] of ANNOUNCED) { const f = byName.get(from), t = byName.get(to); if (f && t) announcedNext.set(t, f) }

  const agents = b.agents.filter((a) => a.id !== 'human' && first.has(a.id)).slice()
    .sort((x, y) => (first.get(x.id)! - first.get(y.id)!) || x.name.localeCompare(y.name))
  const houses: House[] = []
  const houseOf = new Map<string, House>()
  const continued = new Set<string>()   // houses already handed over
  for (const a of agents) {
    const f = first.get(a.id)!, l = last.get(a.id)!
    let house: House | undefined
    let announced = false
    const pred = announcedNext.get(a.id)
    if (pred && houseOf.has(pred)) { house = houseOf.get(pred); announced = true }
    if (!house) {
      // a handover: the last resident's last day and the newcomer's first day within three days
      let best: House | undefined, bestGap = Infinity
      const famOf = new Map(b.agents.map((x) => [x.id, x.family]))
      for (const h of houses) {
        if (continued.has(h.id)) continue
        const tail = h.residents[h.residents.length - 1]
        const gap = Math.abs(f - tail.to)
        // on a tie, the same lab's house (a Claude follows a Claude)
        const scored = gap - (famOf.get(tail.agent) === a.family ? 0.5 : 0)
        if (gap <= HANDOVER_DAYS && scored < bestGap && tail.to < l) { best = h; bestGap = scored }
      }
      house = best
    }
    if (!house) {
      // a move-in: the house has stood empty since its last resident left; the longest-empty house takes the newcomer
      let best: House | undefined, bestVacant = -Infinity
      for (const h of houses) {
        if (continued.has(h.id)) continue
        const tail = h.residents[h.residents.length - 1]
        const vacant = f - tail.to
        if (vacant > HANDOVER_DAYS && vacant > bestVacant) { best = h; bestVacant = vacant }
      }
      house = best
    }
    if (house) {
      continued.add(house.id)
      continued.delete(house.id)   // the house can be handed on again later
      house.residents.push({ agent: a.id, from: f, to: l, announced })
    } else {
      house = { n: houses.length + 1, id: `house-${houses.length + 1}`, residents: [{ agent: a.id, from: f, to: l }] }
      houses.push(house)
    }
    houseOf.set(a.id, house)
  }
  return { houses, houseOf, first, last }
}

export const roman = (n: number): string => {
  const t: [number, string][] = [[50, 'L'], [40, 'XL'], [10, 'X'], [9, 'IX'], [5, 'V'], [4, 'IV'], [1, 'I']]
  let s = ''
  for (const [v, r] of t) while (n >= v) { s += r; n -= v }
  return s
}
