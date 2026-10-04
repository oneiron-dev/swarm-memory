import { render } from 'preact'
import { useState } from 'preact/hooks'
import '../src/styles/tokens.css'
import { Figure, GLYPH_KINDS, LensGlyph, ModeSwitch, MoodStrip, rand } from '../src/book/glyphs'
import type { Lens } from '../src/book/types'

// Every piece in every state, on paper and on a sample of the painting. Synthetic agents only.
const AGENTS = [
  { id: 'alpha', name: 'agent alpha' },
  { id: 'beta', name: 'agent beta' },
  { id: 'gamma', name: 'agent gamma' },
]
const STATES: Lens[] = ['village', 'goal', 'memory', 'humans', 'agent:beta']

function walk(n: number, seed: number, gaps: [number, number][]): (number | null)[] {
  const r = rand(seed)
  let v = 0.5
  const out: (number | null)[] = []
  for (let i = 0; i < n; i++) {
    v = Math.min(1, Math.max(0, v + (r() - 0.5) * 0.28))
    out.push(gaps.some(([a, b]) => i >= a && i <= b) ? null : v)
  }
  return out
}
const MOOD = walk(38, 7, [[9, 11], [24, 24], [26, 26]])
const LONG = walk(1000, 3, [[400, 430]])

function Live() {
  const [lens, setLens] = useState<Lens>('village')
  return (
    <div class="stack">
      <ModeSwitch lens={lens} agents={AGENTS} onChange={setLens} />
      <p class="note" id="live-lens">onChange: {lens}</p>
    </div>
  )
}

function Harness() {
  return (
    <div class="book">
      <h1>Glyphs</h1>
      <p class="note">The lens glyphs, the mode switch, the figure and the mood strip, in every state.</p>

      <h2>The five glyphs at 22 and 44 px, in ink-3 and ink</h2>
      <div class="grid paper" id="glyphs">
        {GLYPH_KINDS.map((k) => (
          <div class="cell">
            <span class="ink3"><LensGlyph lens={k} /></span>
            <span class="ink"><LensGlyph lens={k} /></span>
            <span class="ink"><LensGlyph lens={k} size={44} /></span>
            <span class="k">{k}</span>
          </div>
        ))}
      </div>

      <h2>At 22 px, three times enlarged (pixel check)</h2>
      <div class="grid paper" id="zoom">
        <div class="zoom">
          <div class="grid" style="gap: 6px">{GLYPH_KINDS.map((k) => <span class="ink3"><LensGlyph lens={k} /></span>)}</div>
        </div>
      </div>

      <h2>The switch, live</h2>
      <div class="paper" id="live"><Live /></div>

      <h2>The switch in each state, on paper</h2>
      <div class="stack paper" id="switch-paper">
        {STATES.map((l) => <ModeSwitch lens={l} agents={AGENTS} onChange={() => {}} />)}
      </div>

      <h2>The switch in each state, on the painting</h2>
      <div class="stack painting" id="switch-painting">
        {STATES.map((l) => <ModeSwitch lens={l} agents={AGENTS} onChange={() => {}} />)}
      </div>

      <h2>Figures</h2>
      <div class="figs paper" id="figures">
        <Figure value="$1,481" label="raised for Helen Keller International" />
        <Figure value="32" label="agents at once" />
        <Figure value="11 days" label="without a human message" />
      </div>
      <div class="figs painting b" style="margin-top: 12px">
        <Figure value="$1,481" label="raised for Helen Keller International" />
        <Figure value="32" label="agents at once" />
        <Figure value="11 days" label="without a human message" />
      </div>

      <h2>Mood strips</h2>
      <div class="strips paper" id="strips">
        <div class="cell"><MoodStrip values={MOOD} mark={17} /><span class="k">38 days, gaps, mark on day 18</span></div>
        <div class="cell"><MoodStrip values={MOOD} mark={10} /><span class="k">mark on a gap</span></div>
        <div class="cell"><MoodStrip values={MOOD} mark={null} width={440} height={48} /><span class="k">440 x 48, no mark</span></div>
      </div>
      <div class="strips painting" style="margin-top: 12px" id="strips-painting">
        <div class="cell"><MoodStrip values={MOOD} mark={30} /><span class="k">on the painting</span></div>
        <div class="cell"><MoodStrip values={LONG} mark={700} /><span class="k">1000 values</span></div>
        <div class="cell"><MoodStrip values={[0.4]} mark={0} /><span class="k">one value</span></div>
        <div class="cell"><MoodStrip values={[null, null, null]} mark={1} /><span class="k">all gaps</span></div>
        <div class="cell"><MoodStrip values={[]} mark={null} /><span class="k">empty</span></div>
      </div>
    </div>
  )
}

render(<Harness />, document.getElementById('app')!)
