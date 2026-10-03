import type { ComponentChildren } from 'preact'
import { useEffect, useLayoutEffect, useRef, useState } from 'preact/hooks'
import { GROUPS, GROUP_NAME, type Group } from './data'

// Width of a container, kept current with a ResizeObserver.
export function useWidth<T extends HTMLElement>(fallback = 640) {
  const ref = useRef<T>(null)
  const [w, setW] = useState(fallback)
  // measure before first paint so charts never draw at the fallback width and jump
  useLayoutEffect(() => {
    const el = ref.current
    if (el) setW(Math.max(200, Math.round(el.getBoundingClientRect().width)))
  }, [])
  useEffect(() => {
    const el = ref.current
    if (!el) return
    let frame = 0
    // defer to the next frame so a re-render never lands inside the observer's own pass
    const ro = new ResizeObserver(([e]) => {
      cancelAnimationFrame(frame)
      const next = Math.max(200, Math.round(e.contentRect.width))
      frame = requestAnimationFrame(() => setW((cur) => (Math.abs(cur - next) >= 1 ? next : cur)))
    })
    ro.observe(el)
    return () => { cancelAnimationFrame(frame); ro.disconnect() }
  }, [])
  return [ref, w] as const
}

export const linear = (d0: number, d1: number, r0: number, r1: number) => (v: number) =>
  d1 === d0 ? (r0 + r1) / 2 : r0 + ((v - d0) / (d1 - d0)) * (r1 - r0)

export function niceMax(v: number) {
  if (v <= 0) return 1
  const p = Math.pow(10, Math.floor(Math.log10(v)))
  for (const m of [1, 2, 2.5, 5, 10]) if (m * p >= v) return m * p
  return 10 * p
}

// Index of the point whose day is nearest to `day` (points sorted by day); -1 when there are none.
export function nearestIndex(points: { day: number }[], day: number): number {
  if (!points.length) return -1
  let lo = 0, hi = points.length - 1
  while (lo < hi) {
    const mid = (lo + hi) >> 1
    if (points[mid].day < day) lo = mid + 1
    else hi = mid
  }
  if (lo > 0 && Math.abs(points[lo - 1].day - day) < Math.abs(points[lo].day - day)) lo--
  return lo
}

export function Tip({ x, y, w, children }: { x: number; y: number; w: number; children: ComponentChildren }) {
  const left = Math.min(Math.max(8, x + 14), Math.max(8, w - 228))
  return (
    <div class="tip" style={{ left: `${left}px`, top: `${Math.max(0, y - 8)}px` }} role="status">
      {children}
    </div>
  )
}

export function GroupDot({ g, ring }: { g: Group; ring?: boolean }) {
  return <span class={`dot g-${g}${ring ? ' ring' : ''}`} aria-hidden="true" />
}

export function Legend({ groups = GROUPS }: { groups?: Group[] }) {
  return (
    <ul class="legend" aria-label="legend">
      {groups.map((g) => (
        <li><GroupDot g={g} /> {GROUP_NAME[g]}</li>
      ))}
    </ul>
  )
}

// A pick row: ◆ chosen, ◇ at rest. Click applies.
export function Picks<T extends string>({ value, options, onPick, label }: {
  value: T; options: { id: T; name: string }[]; onPick: (v: T) => void; label: string
}) {
  return (
    <div class="picks" role="radiogroup" aria-label={label}>
      {options.map((o) => (
        <button type="button" role="radio" aria-checked={o.id === value} class={o.id === value ? 'pick on' : 'pick'} onClick={() => onPick(o.id)}>
          <span aria-hidden="true">{o.id === value ? '◆' : '◇'}</span> {o.name}
        </button>
      ))}
    </div>
  )
}
