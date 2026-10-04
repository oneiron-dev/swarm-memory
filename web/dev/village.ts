// The village plate harness: five synthetic days (no dataset rows), drawn at two sizes, timed; a clock runs the life.
// The page links the book's tokens, so the board letters in Source Serif 4 as in the book; ?georgia draws the fallback.
import { GATE_TURN, PROP_KINDS, drawVillage, type HouseScene, type Lab, type Moment, type MomentKind, type Prop, type VillageScene } from '../src/book/village'
import { rngOf } from '../src/book/pen'

if (new URLSearchParams(location.search).has('georgia')) document.documentElement.style.setProperty('--serif', 'Georgia, serif')

const LABS: Lab[] = ['anthropic', 'openai', 'google', 'other']
const house = (n: number, over: Partial<HouseScene> = {}): HouseScene => ({
  n, present: true, lab: LABS[(n * 7 + 3) % 4], window: null, smoke: 0, arriving: false, leaving: false, ghost: false, mark: null, away: false, idle: false, ...over,
})

// (a) day one: four houses, everyone home, the village lens, a visitor at the gate
function sceneA(): VillageScene {
  const hues = [70, 150, 25, 250]
  return {
    lens: 'village', visitor: 0.6, t: 0.5, board: 'Plant a garden and keep a log', pairs: [],
    houses: [1, 2, 3, 4].map((n, i) => house(n, {
      lab: (['anthropic', 'openai', 'google', 'anthropic'] as Lab[])[i],
      window: `oklch(0.72 0.12 ${hues[i]})`, smoke: [0.9, 0.4, 0.15, 0.65][i],
    })),
  }
}

// (b) mid-year: 22 houses, three empty, one walking in, one walking out, windows in a spread of mood hues, a pin and a swing
function sceneB(t: number): VillageScene {
  const r = rngOf('harness-b')
  return {
    lens: 'village', visitor: 0.2, t, board: null, pairs: [],
    houses: Array.from({ length: 22 }, (_, i) => {
      const n = i + 1
      const empty = n === 6 || n === 13 || n === 19
      return house(n, empty ? { present: false, lab: null } : {
        window: r() < 0.15 ? null : `oklch(0.7 0.13 ${Math.round((n * 47) % 360)})`,
        smoke: r() < 0.2 ? 0 : r(),
        arriving: n === 8, leaving: n === 15,
        mark: n === 11 ? 'pin' : n === 17 ? 'swing' : null,
      })
    }),
  }
}

// (c) forty houses under the goal lens: on / partly / elsewhere / idle; two ghosts; no humans today
function sceneC(): VillageScene {
  const r = rngOf('harness-c')
  const goal = ['oklch(0.30 0.025 60)', 'oklch(0.60 0.035 60)', 'oklch(0.55 0.17 30)', null]
  return {
    lens: 'goal', visitor: 0, t: 0.5, board: 'Write a field guide to the birds that visit the valley', pairs: [],
    houses: Array.from({ length: 40 }, (_, i) => {
      const n = i + 1
      const empty = r() < 0.12
      return house(n, empty ? { present: false, lab: null } : {
        window: goal[Math.floor(r() * 4)], smoke: r() < 0.25 ? 0 : r(), ghost: n === 9 || n === 30,
      })
    }),
  }
}

// (d) a lived day: the week's goal on the board, three talking pairs (one across the gate), two out on the road,
// one idle house with its shutter closed, one walking in, one walking out
function sceneD(count = 12): VillageScene {
  const hues = [70, 150, 25, 250, 95, 300, 40, 200, 120, 330, 10, 180]
  return {
    lens: 'village', visitor: 0.55, t: 0.5, board: 'Write a field guide to the birds that visit the valley',
    pairs: [{ a: 0, b: 2, w: 0.7 }, { a: 4, b: 6, w: 1 }, { a: 8, b: 11, w: 0.35 }],
    houses: Array.from({ length: count }, (_, i) => {
      const n = i + 1
      return house(n, {
        window: `oklch(0.74 0.12 ${hues[i % hues.length]})`, smoke: [0.9, 0.5, 0.2, 0.75, 0.4, 1, 0.3, 0.6, 0.85, 0.15, 0.55, 0.7][i % 12],
        away: n === 3 || n === 10, idle: n === 7, arriving: n === 5, leaving: n === 9,
      })
    }),
  }
}

// (e) the opening chapter: four founders at home, a holiday on the board, and three of them talking with the humans,
// who take turns at the gate (the strongest first, one exchange each)
function sceneE(): VillageScene {
  const hues = [70, 150, 25, 250]
  return {
    lens: 'village', visitor: 0.8, t: 0.5, board: "Holiday: do whatever you'd like! Next goal will begin soon",
    pairs: [{ a: 0, b: 1, w: 1 }, { a: 0, b: 3, w: 0.8 }, { a: 0, b: 2, w: 0.5 }],
    houses: [1, 2, 3, 4].map((n, i) => house(n, {
      lab: (['anthropic', 'openai', 'google', 'anthropic'] as Lab[])[i],
      window: `oklch(0.74 0.12 ${hues[i]})`, smoke: [0.8, 0.45, 0.6, 0.3][i],
    })),
  }
}

// the stage: generic labels only (no dataset words). (f) every prop, three to a plate, two rows of six
const LABELS: Record<string, string> = {
  stall: 'Bake sale', mailbox: 'Newsletter', ballot: 'Leader vote', scaffold: 'New hub', gallery: 'Museum', chessboard: 'Tournament',
  stage: 'Quiz night', press: 'Daily paper', garden: 'Park cleanup', lanterns: 'Holiday week', cloud: 'Outages', signpost: 'New plan',
}
const propsOf = (kinds: string[], labelled = true): Prop[] => kinds.map((k) => ({ kind: k as Prop['kind'], label: labelled ? LABELS[k] : null }))
function sceneF(k: number): VillageScene {
  return { ...sceneD(12), pairs: [], board: 'Every prop on the stage', props: propsOf(PROP_KINDS.slice(k * 3, k * 3 + 3)) }
}
// (g) a chapter with a stall, a mailbox and lanterns: send (beat 0), build (1), celebrate (2), lighting a second apart
function sceneG(count = 12): VillageScene {
  const moments: Moment[] = [
    { kind: 'send', houses: [2, 5, 9], label: null, start: 0 },
    { kind: 'build', houses: [4], label: null, start: 1 },
    { kind: 'celebrate', houses: [6, 7, 8], label: null, start: 2 },
  ]
  return { ...sceneD(count), pairs: [{ a: 1, b: 3, w: 0.6 }], board: 'Raise money for a charity', props: propsOf(['stall', 'mailbox', 'lanterns']), moments }
}
// (h) thirty houses under a rain cloud with a scaffold: a fail and a win
function sceneH(): VillageScene {
  const moments: Moment[] = [
    { kind: 'fail', houses: [11], label: null, start: 0 },
    { kind: 'win', houses: [20], label: null, start: 1 },
  ]
  return { ...sceneD(30), board: 'Build a website for the village', props: propsOf(['cloud', 'scaffold']), moments }
}
// (i) gather, vote and play: a ballot box comes with the vote (no ballot prop), the game takes the chessboard
function sceneI(count = 12): VillageScene {
  const moments: Moment[] = [
    { kind: 'gather', houses: [3, 6, 9, 11], label: null, start: 0 },
    { kind: 'vote', houses: [2, 5, 8], label: null, start: 0.5 },
    { kind: 'play', houses: [4, 10], label: null, start: 1 },
  ]
  return { ...sceneD(count), pairs: [{ a: 1, b: 12, w: 0.5 }], board: 'Choose a leader and play a game', props: propsOf(['chessboard', 'gallery']), moments }
}
// (j) forty houses, three props, three moments running: the frame-time case
function sceneJ(): VillageScene {
  const s = sceneC()
  const moments: Moment[] = [
    { kind: 'celebrate', houses: [5, 6, 7, 21], label: null, start: 0 },
    { kind: 'send', houses: [10, 22, 33, 38], label: null, start: 0.5 },
    { kind: 'build', houses: [16], label: null, start: 1 },
  ]
  return { ...s, lens: 'village', props: propsOf(['press', 'garden', 'stage']), moments }
}

interface Plate { name: string; cssW: number; cssH: number; scene: (t: number | null) => VillageScene; canvas: HTMLCanvasElement; time: HTMLElement; box: HTMLElement }
const plates: Plate[] = []
const add = (rowId: string, name: string, cssW: number, cssH: number, scene: (t: number | null) => VillageScene) => {
  const fig = document.createElement('figure')
  const box = document.createElement('div')
  box.className = 'plate'
  box.style.width = `${cssW}px`; box.style.height = `${cssH}px`
  const canvas = document.createElement('canvas')
  canvas.width = cssW * 2; canvas.height = cssH * 2
  canvas.style.width = `${cssW}px`; canvas.style.height = `${cssH}px`
  canvas.setAttribute('aria-hidden', 'true')
  box.append(canvas)
  const cap = document.createElement('figcaption')
  const label = document.createElement('span')
  label.textContent = `${name} · ${cssW} x ${cssH}`
  const time = document.createElement('span')
  time.className = 'time'
  cap.append(label, time)
  fig.append(box, cap)
  document.getElementById(rowId)!.append(fig)
  box.dataset.name = name
  plates.push({ name, cssW, cssH, scene, canvas, time, box })
}

const withT = (s: VillageScene, t: number | null) => (t === null ? s : { ...s, t })
add('big', '(a) day one', 440, 280, (t) => withT(sceneA(), t))
add('big', '(b) mid-year, t 0.4', 440, 280, (t) => sceneB(t ?? 0.4))
add('big', '(c) goal lens, 40', 440, 280, (t) => withT(sceneC(), t))
add('small', '(a) day one', 360, 200, (t) => withT(sceneA(), t))
add('small', '(b) mid-year, t 0.7', 360, 200, (t) => sceneB(t ?? 0.7))
add('small', '(c) goal lens, 40', 360, 200, (t) => withT(sceneC(), t))
add('life', '(d) a lived day', 440, 280, (t) => withT(sceneD(), t))
add('life', '(d) a lived day', 360, 200, (t) => withT(sceneD(), t))
add('life', '(d) the same, 30 houses', 440, 280, (t) => withT(sceneD(30), t))
add('life', '(e) day one, three at the gate', 440, 280, (t) => withT(sceneE(), t))
add('life', '(e) day one, three at the gate', 360, 200, (t) => withT(sceneE(), t))

for (let k = 0; k < 4; k++) add('props', `(f) props ${k * 3 + 1} to ${k * 3 + 3}`, 440, 280, (t) => withT(sceneF(k), t))
add('stage', '(g) send, build, celebrate', 440, 280, (t) => withT(sceneG(), t))
add('stage', '(g) the same', 360, 200, (t) => withT(sceneG(), t))
add('stage', '(h) 30 houses, fail and win', 440, 280, (t) => withT(sceneH(), t))
add('stage', '(i) gather, vote, play', 440, 280, (t) => withT(sceneI(), t))
add('stage', '(i) the same, 22 houses', 360, 200, (t) => withT(sceneI(22), t))
add('stage', '(j) 40 houses, 3 props, 3 moments', 440, 280, (t) => withT(sceneJ(), t))
// (k) the review's cases: residents shared by three moments, a send with no mailbox and a resident at the gate, a fail
// on a back-row house, a build in a short row
function sceneK(which: 'twice' | 'gate' | 'failback' | 'build4'): VillageScene {
  if (which === 'twice') return { ...sceneD(12), pairs: [], board: 'Play a game together', props: propsOf(['chessboard']), moments: [
    { kind: 'gather', houses: [3, 5, 7], label: null, start: 0 }, { kind: 'play', houses: [3, 5], label: null, start: 2 }, { kind: 'celebrate', houses: [3, 7], label: null, start: 4 }] }
  if (which === 'gate') return { ...sceneD(12), pairs: [{ a: 0, b: 3, w: 0.7 }], board: 'Write to the humans', props: [], moments: [{ kind: 'send', houses: [2, 5, 9], label: null, start: 0 }] }
  if (which === 'failback') return { ...sceneD(12), pairs: [], board: 'Keep the boards up', props: propsOf(['cloud']), moments: [{ kind: 'fail', houses: [8], label: null, start: 0 }, { kind: 'fail', houses: [4], label: null, start: 0.5 }] }
  return { ...sceneD(4), pairs: [], board: 'Build a hub', props: propsOf(['scaffold']), moments: [{ kind: 'build', houses: [2], label: null, start: 0 }] }
}
add('fixes', '(k) shared residents', 440, 280, (t) => withT(sceneK('twice'), t))
add('fixes', '(k) send, no mailbox, gate talk', 440, 280, (t) => withT(sceneK('gate'), t))
add('fixes', '(k) fail on back-row houses', 440, 280, (t) => withT(sceneK('failback'), t))
add('fixes', '(k) build, 4 houses', 440, 280, (t) => withT(sceneK('build4'), t))
add('fixes', '(k) send, no mailbox', 360, 200, (t) => withT(sceneK('gate'), t))

// (l) the moments that name nobody (houses: [], as the writing model leaves them when it cannot name anyone), at the
// book's own plate size (760 x 372): each still plays, at the residents nearest the board or the gate, or at the stage
const nobody = (ks: [MomentKind, number][]): Moment[] => ks.map(([kind, start]) => ({ kind, houses: [], label: null, start }))
function sceneL(which: 1 | 2 | 3 | 4): VillageScene {
  if (which === 1) return { ...sceneD(4), pairs: [], board: 'Raise money for a charity', props: [{ kind: 'stall', label: 'Charity fundraiser' }, { kind: 'mailbox', label: 'Fundraising outreach' }, { kind: 'scaffold', label: 'Donation page' }], moments: nobody([['send', 0], ['build', 1], ['celebrate', 2]]) }
  if (which === 2) return { ...sceneD(12), pairs: [], board: 'Elect a village leader', props: [{ kind: 'chessboard', label: 'Chess club' }, { kind: 'gallery', label: null }], moments: nobody([['gather', 0], ['vote', 0.5], ['play', 1]]) }
  if (which === 3) return { ...sceneD(22), pairs: [{ a: 4, b: 6, w: 0.7 }], board: 'Ship the museum before Friday', props: [{ kind: 'cloud', label: 'Outages' }, { kind: 'press', label: 'Daily paper' }], moments: nobody([['win', 0], ['fail', 1], ['build', 2]]) }
  return { ...sceneD(40), board: 'Throw a party for the whole valley', props: [{ kind: 'lanterns', label: 'Party week' }], moments: nobody([['celebrate', 0], ['win', 1]]) }
}
add('nobody', '(l) send, build, celebrate, nobody named, 4', 760, 372, (t) => withT(sceneL(1), t))
add('nobody', '(l) gather, vote, play, nobody named, 12', 760, 372, (t) => withT(sceneL(2), t))
add('nobody', '(l) win, fail, build at the board, 22', 760, 372, (t) => withT(sceneL(3), t))
add('nobody', '(l) celebrate everyone, win, 40', 760, 372, (t) => withT(sceneL(4), t))
add('nobody', '(g) named, at the book size', 760, 372, (t) => withT(sceneG(), t))

// ?words=1: the year's real weekly goals (50 to 58 characters) on the board at four plate sizes, in the page's face
if (new URLSearchParams(location.search).has('words')) {
  const row = document.createElement('div'); row.className = 'row'; row.id = 'words'
  document.body.append(row)
  const goals = ['Write a field guide to the birds that visit the valley', "Holiday: do whatever you'd like! Next goal will begin soon", 'Choose a charity and raise as much money as you can for it', 'Develop a turn-based RPG together while voting out Easter…']
  goals.forEach((goal, k) => {
    for (const [w, h, count] of [[440, 280, 4], [360, 200, 12], [354, 250, 22], [650, 370, 40]] as [number, number, number][]) {
      add('words', `goal ${k + 1}, ${count} houses`, w, h, (t) => ({ ...withT(sceneD(count), t), board: goal }))
    }
  })
}

// ?extra=1 adds lens and count checks below the three scenes
if (new URLSearchParams(location.search).has('extra')) {
  const row = document.createElement('div'); row.className = 'row'; row.id = 'extra'
  document.body.append(row)
  add('extra', 'humans lens', 440, 280, (t) => {
    const s = sceneB(t ?? 0.4)
    return { ...s, lens: 'humans', visitor: 0.8, houses: s.houses.map((h) => ({ ...h, window: null, ghost: h.present })) }
  })
  add('extra', 'a lived day, humans lens', 440, 280, (t) => {
    const s = withT(sceneD(), t)
    return { ...s, lens: 'humans', visitor: 0.8, houses: s.houses.map((h) => ({ ...h, window: null, ghost: h.n !== 2 })) }
  })
  add('extra', 'a lived day, no goal posted', 360, 200, (t) => ({ ...withT(sceneD(8), t), board: null }))
  add('extra', 'one agent', 440, 280, (t) => {
    const s = withT(sceneC(), t)
    return { ...s, lens: 'agent', houses: s.houses.map((h) => ({ ...h, ghost: h.n !== 21, window: h.n === 21 ? 'oklch(0.72 0.14 150)' : null, mark: h.n === 21 ? 'swing' as const : null })) }
  })
  add('extra', 'lamps, 9 houses', 440, 280, () => ({
    lens: 'village', visitor: 0.05, t: 1, board: null, pairs: [],
    houses: Array.from({ length: 9 }, (_, i) => house(i + 1, { window: i % 3 === 2 ? null : 'oklch(0.86 0.09 80)', smoke: (i % 4) / 3, arriving: i === 4, leaving: i === 7, mark: i === 1 ? 'pin' : i === 6 ? 'swing' : null })),
  }))
  add('extra', 'one house', 360, 200, () => ({ lens: 'village', visitor: 1, t: 0.5, board: 'One house, one goal', pairs: [], houses: [house(1, { window: 'oklch(0.86 0.09 80)', smoke: 1, mark: 'pin' })] }))
  add('extra', '12 houses, pins', 360, 200, (t) => ({
    lens: 'village', visitor: 0.5, t: t ?? 0.15, board: null, pairs: [],
    houses: Array.from({ length: 12 }, (_, i) => house(i + 1, { window: 'oklch(0.86 0.09 80)', smoke: 0.5, mark: i < 2 ? 'pin' : null, arriving: i === 1, leaving: i === 2 })),
  }))
}

// ?check=1 adds the review's two cases: everyone walking (no walker may close its legs at any t, the slider included),
// and a pin on every house (no front-row pin may sit on a back-row house)
if (new URLSearchParams(location.search).has('check')) {
  const row = document.createElement('div'); row.className = 'row'; row.id = 'check'
  document.body.append(row)
  const walking = (count: number) => (t: number | null): VillageScene => ({
    lens: 'village', visitor: 0, t: t ?? 0.5, board: null, pairs: [],
    houses: Array.from({ length: count }, (_, i) => house(i + 1, { window: 'oklch(0.86 0.09 80)', smoke: 0.3, arriving: i % 2 === 0, leaving: i % 2 === 1 })),
  })
  const pinned = (count: number) => (t: number | null): VillageScene => ({
    lens: 'village', visitor: 0.4, t: t ?? 0.5, board: null, pairs: [],
    houses: Array.from({ length: count }, (_, i) => house(i + 1, { window: 'oklch(0.72 0.12 40)', smoke: (i % 3) / 2, mark: 'pin' })),
  })
  add('check', 'walkers, 4', 440, 280, walking(4))
  add('check', 'walkers, 9', 440, 280, walking(9))
  add('check', 'walkers, 22', 440, 280, walking(22))
  add('check', 'pins, 10', 440, 280, pinned(10))
  add('check', 'pins, 22', 440, 280, pinned(22))
  add('check', 'pins, 40', 440, 280, pinned(40))
  add('check', 'pins, 22', 360, 200, pinned(22))
  add('check', 'pins, 40', 360, 200, pinned(40))
}

const slider = document.getElementById('t') as HTMLInputElement
const own = document.getElementById('own') as HTMLInputElement
const noclock = document.getElementById('noclock') as HTMLInputElement
const scrub = document.getElementById('clock') as HTMLInputElement
const play = document.getElementById('play') as HTMLButtonElement
const params = new URLSearchParams(location.search)
if (params.has('t')) { slider.value = params.get('t')!; own.checked = false }

// the clock: runs by requestAnimationFrame from 0 to 16 s and round again (three turns at the gate are 15.6 s); Pause
// holds it; the slider scrubs it
let clock = 0, playing = true, last = 0
if (params.has('clock')) { clock = Number(params.get('clock')); playing = false }
if (params.has('noclock')) noclock.checked = true

export interface Timing { name: string; cold: number; median: number; p95: number; flushed: number }
const timings: Timing[] = []
const frameTimes: number[] = []

function draw(p: Plate, t: number | null): number {
  const ctx = p.canvas.getContext('2d')!
  ctx.setTransform(1, 0, 0, 1, 0, 0)
  ctx.clearRect(0, 0, p.canvas.width, p.canvas.height)
  ctx.setTransform(2, 0, 0, 2, 0, 0)
  const scene = p.scene(t)
  const t0 = performance.now()
  drawVillage(ctx, scene, p.cssW, p.cssH, noclock.checked ? undefined : clock)
  return performance.now() - t0
}

function measure(p: Plate, t: number | null): Timing {
  const ctx = p.canvas.getContext('2d')!
  const scene = p.scene(t)
  const cl = noclock.checked ? undefined : clock
  const run = (k: number) => {
    ctx.setTransform(1, 0, 0, 1, 0, 0)
    ctx.clearRect(0, 0, p.canvas.width, p.canvas.height)
    ctx.setTransform(2, 0, 0, 2, 0, 0)
    const t0 = performance.now()
    drawVillage(ctx, scene, p.cssW, p.cssH, cl === undefined ? undefined : cl + k / 60)
    return performance.now() - t0
  }
  // a fresh size so the first call pays for its layout, then the steady state (the clock advancing a frame each run)
  const t0 = performance.now()
  drawVillage(ctx, scene, p.cssW + 0.001, p.cssH, cl)
  const cold = performance.now() - t0
  const xs: number[] = []
  for (let i = 0; i < 60; i++) xs.push(run(i))
  xs.sort((a, b) => a - b)
  // the same draw with the GPU flush forced by a one-pixel read, so raster time is counted too
  const f0 = performance.now()
  run(0); ctx.getImageData(0, 0, 1, 1)
  const flushed = performance.now() - f0
  return { name: `${p.name} @ ${p.cssW}x${p.cssH}`, cold, median: xs[30], p95: xs[Math.floor(xs.length * 0.95)], flushed }
}

const forty = plates.find((p) => p.name.startsWith('(c)') && p.cssW === 440)!
const staged40 = plates.find((p) => p.name.startsWith('(j)'))!
const stageTimes: number[] = []
function render(): void {
  const t = own.checked ? null : Number(slider.value)
  document.getElementById('t-out')!.textContent = Number(slider.value).toFixed(2)
  scrub.value = String(clock)
  document.getElementById('clock-out')!.textContent = `${clock.toFixed(2)} s`
  for (const p of plates) {
    const ms = draw(p, t)
    if (p === forty) {
      // the 40-house scene's draw time per frame, median over the last 60 frames
      frameTimes.push(ms)
      if (frameTimes.length > 60) frameTimes.shift()
      const xs = frameTimes.slice().sort((a, b) => a - b)
      document.getElementById('frame')!.textContent = `40 houses at 880 x 560 device px: ${xs[Math.floor(xs.length / 2)].toFixed(2)} ms median over ${xs.length} frames`
    }
    if (p === staged40) {
      stageTimes.push(ms)
      if (stageTimes.length > 60) stageTimes.shift()
      const xs = stageTimes.slice().sort((a, b) => a - b)
      document.getElementById('frame-stage')!.textContent = `40 houses, 3 props, 3 moments at 880 x 560 device px: ${xs[Math.floor(xs.length / 2)].toFixed(2)} ms median, max ${xs[xs.length - 1].toFixed(2)} ms over ${xs.length} frames`
    }
  }
}

function benchmark(): void {
  timings.length = 0
  const t = own.checked ? null : Number(slider.value)
  for (const p of plates) {
    const m = measure(p, t)
    timings.push(m)
    p.time.textContent = `${m.median.toFixed(2)} ms · p95 ${m.p95.toFixed(2)} · cold ${m.cold.toFixed(2)}`
  }
  render()
  const worst = Math.max(...timings.map((m) => m.p95))
  document.getElementById('summary')!.textContent = `worst p95 ${worst.toFixed(2)} ms`
  console.info('village draw times', timings)
}

function tick(now: number): void {
  if (playing) {
    if (last) clock = (clock + (now - last) / 1000) % 16
    last = now
    render()
  } else last = 0
  requestAnimationFrame(tick)
}
play.textContent = playing ? 'Pause' : 'Play'
play.addEventListener('click', () => { playing = !playing; play.textContent = playing ? 'Pause' : 'Play' })
scrub.addEventListener('input', () => { playing = false; play.textContent = 'Play'; clock = Number(scrub.value); render() })
slider.addEventListener('input', () => { own.checked = false; render() })
own.addEventListener('change', render)
noclock.addEventListener('change', render)
// the board's face: the canvas draws in a fallback until the italic Source Serif 4 has loaded, and the plate refits its
// words once it has, so a paused page draws again when a face arrives
const face = () => {
  const fam = getComputedStyle(document.documentElement).getPropertyValue('--serif').trim() || 'Georgia, serif'
  const ok = document.fonts.check(`italic 16px ${fam}`)
  document.getElementById('face')!.textContent = `board face: ${fam.split(',')[0]} italic${ok ? '' : ' (loading)'} · gate turn ${GATE_TURN} s`
}
document.fonts.addEventListener('loadingdone', () => { face(); render() })
void document.fonts.ready.then(() => { face(); render() })
face()
render()
requestAnimationFrame(() => benchmark())
requestAnimationFrame(tick)
;(window as unknown as { villageLab: unknown }).villageLab = {
  get timings() { return timings }, get frameTimes() { return frameTimes }, get stageTimes() { return stageTimes }, benchmark, render,
  setClock(v: number) { playing = false; clock = v; render() },
}
