import { useEffect, useRef } from 'preact/hooks'
import { createPlume, type Plume, type PlumeState } from '../ground/plume'

// Pigment drifting over the ground: a transparent canvas of grains behind the page. The module eases every change,
// pauses when the tab is hidden, and draws one still frame under reduced motion.
export function Pigment({ state }: { state: Partial<PlumeState> }) {
  const ref = useRef<HTMLCanvasElement>(null)
  const plume = useRef<Plume | null>(null)
  const key = JSON.stringify(state)
  useEffect(() => {
    if (!ref.current) return
    plume.current = createPlume(ref.current, state)
    return () => { plume.current?.destroy(); plume.current = null }
  }, [])
  useEffect(() => { plume.current?.set(state) }, [key])
  return <canvas ref={ref} class="pigment" aria-hidden="true" />
}

// The cover's pigment: the valley's own colours blown across the path.
export const COVER_PIGMENT: Partial<PlumeState> = {
  grains: 2600, palette: ['#e9a94f', '#df6a3c', '#c43f5e', '#6f4393'], wind: 0.38, settle: 0, source: 'band', opacity: 0.55, seed: 7,
}
