import { useEffect, useState } from 'preact/hooks'

export type View = 'book' | 'overview' | 'timeline' | 'agents' | 'rooms' | 'memory' | 'entities' | 'questions'
// The six analytical views: the appendix. The book itself is the home route (`#book/<lens>/<chapter>/<day>`).
export const VIEWS: { id: View; name: string }[] = [
  { id: 'overview', name: 'overview' },
  { id: 'timeline', name: 'timeline' },
  { id: 'agents', name: 'agents' },
  { id: 'rooms', name: 'who talks to whom' },
  { id: 'memory', name: 'memory drift' },
  { id: 'entities', name: 'entities' },
  { id: 'questions', name: 'the questions' },
]

export interface Route { view: View; arg: string | null; sub: string | null; sub2: string | null; sub3: string | null }

const HOME: Route = { view: 'book', arg: null, sub: null, sub2: null, sub3: null }

// Hash segments, decoded; null when a segment has a malformed % escape.
function segments(hash: string): string[] | null {
  try {
    return hash.replace(/^#\/?/, '').split('/').map((p) => decodeURIComponent(p))
  } catch {
    return null
  }
}

export function parse(hash: string): Route {
  const parts = segments(hash)
  if (!parts) return HOME
  const view = parts[0] === 'book' || !parts[0] ? 'book' : ((VIEWS.find((v) => v.id === parts[0])?.id ?? 'book') as View)
  return { view, arg: parts[1] || null, sub: parts[2] || null, sub2: parts[3] || null, sub3: parts[4] || null }
}

export const href = (view: View, arg?: string | number | null, sub?: string | number | null, sub2?: string | number | null, sub3?: string | number | null) =>
  '#' + [view, arg, sub, sub2, sub3].filter((x) => x != null && x !== '').map((x) => encodeURIComponent(String(x))).join('/')

// A link that comes from data (findings.json `view`): only a hash naming one of our views, rebuilt by href(); anything else gets no link.
export function internalHref(raw: unknown): string | null {
  if (typeof raw !== 'string' || !/^#[\w/.:%~-]*$/.test(raw)) return null
  const parts = segments(raw)
  if (!parts || !(parts[0] === 'book' || VIEWS.some((v) => v.id === parts[0]))) return null
  return href(parts[0] as View, parts[1], parts[2], parts[3])
}

// A day from the hash: a whole number inside the bundle's range, else null so the view falls back to its default day.
export function dayArg(raw: string | null, first: number, last: number): number | null {
  if (raw == null || !/^\d+$/.test(raw)) return null
  const day = Number(raw)
  return day >= first && day <= last ? day : null
}

// go(view, arg, sub, replace) still works for the appendix views; the book passes a fourth segment.
export function go(view: View, arg?: string | number | null, sub?: string | number | null, sub2?: string | number | null | boolean, sub3?: string | number | null, replace = false) {
  if (typeof sub2 === 'boolean') { replace = sub2; sub2 = null }
  const h = href(view, arg, sub, sub2, sub3)
  if (replace) history.replaceState(null, '', h)
  else history.pushState(null, '', h)
  window.dispatchEvent(new HashChangeEvent('hashchange'))
}

export function useRoute(): Route {
  const [route, setRoute] = useState(() => parse(location.hash))
  useEffect(() => {
    const on = () => setRoute(parse(location.hash))
    window.addEventListener('hashchange', on)
    window.addEventListener('popstate', on)
    return () => {
      window.removeEventListener('hashchange', on)
      window.removeEventListener('popstate', on)
    }
  }, [])
  return route
}

// A tiny shared store for the snippet sheet (one open citation at a time).
export interface SnippetReq { ref: string; day: number; n?: number; highlight?: string }
type Listener = (s: SnippetReq | null) => void
let current: SnippetReq | null = null
const listeners = new Set<Listener>()
export function openSnippet(s: SnippetReq | null) {
  current = s
  listeners.forEach((l) => l(s))
}
export function useSnippet(): SnippetReq | null {
  const [s, set] = useState(current)
  useEffect(() => {
    listeners.add(set)
    return () => { listeners.delete(set) }
  }, [])
  return s
}
