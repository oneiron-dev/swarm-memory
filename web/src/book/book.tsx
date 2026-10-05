import { memo } from 'preact/compat'
import type { ComponentChildren } from 'preact'
import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { credit, isVillage, useLoad, type Finding, type Index, type Summary } from '../data'
import { go, href, internalHref, type Route } from '../route'
import { buildHouses } from './houses'
import { pinOf } from './tapestry'
import { RichText, clean } from '../text'
import { Drawer } from './cast'
import { COVER_PIGMENT, Pigment } from './pigment'
import { Tapestry } from './tapestry'
import { Plate } from './plate'
import { Figure, ModeSwitch } from './glyphs'
import { agentCuts, meanMood, moodColour, moodCuts, moodKey, villageMood, type Cuts, type MoodKey } from './mood'
import { beatsOf, deriveChapters, fmtDate, fmtRange, hookOf, lensAgent, parseLens, weekday, type BeatView, type BookFile, type Chapter, type Lens } from './types'
import { boardText, pairsOf, topicsOf } from './scene'
import type { Houses } from './houses'

// The book is one long scroll: the cover, then a section per chapter (a hook, a figure, a few beats, the unfoldings).
// Beside it a picture that is a pure function of where the reader is: the plate (the village, drawn) and the cloth
// (the whole year) in a window that pans to the chapter under the eye. On phones the picture sits above the words.

// wide screens show the cloth beside the words; narrow ones keep the plate on top and the cloth one tap away
function useWide(min = 980): boolean {
  const [wide, setWide] = useState(() => (typeof window === 'undefined' ? true : window.innerWidth >= min))
  useEffect(() => {
    const mq = window.matchMedia(`(min-width: ${min}px)`)
    const on = () => setWide(mq.matches)
    on(); mq.addEventListener('change', on)
    return () => mq.removeEventListener('change', on)
  }, [min])
  return wide
}

type Plate = { set: 'wash' | 'sumi'; key: MoodKey }
const KEYS: MoodKey[] = ['v-hi_a-hi', 'v-hi_a-lo', 'v-mid_a-hi', 'v-mid_a-lo', 'v-lo_a-hi', 'v-lo_a-lo', 'neutral']
const plateUrl = (p: Plate) => `./plates/${p.set}/${p.key}.webp`
const tinyUrl = (p: Plate) => `./plates/${p.set}/${p.key}-tiny.webp`
const same = (a: Plate | null, b: Plate) => !!a && a.set === b.set && a.key === b.key

// The ground: two stacked plates; a change loads the next one, then crossfades.
function Ground({ plate }: { plate: Plate }) {
  const [slots, setSlots] = useState<[Plate | null, Plate | null]>([plate, null])
  const [cur, setCur] = useState(0)
  const curRef = useRef(0)
  const shown = useRef<Plate>(plate)
  useEffect(() => {
    if (same(shown.current, plate)) return
    let live = true
    const img = new Image()
    const show = () => {
      if (!live) return
      live = false
      const n = curRef.current ^ 1
      curRef.current = n
      shown.current = plate
      setSlots((s) => { const t: [Plate | null, Plate | null] = [s[0], s[1]]; t[n] = plate; return t })
      setCur(n)
    }
    img.onload = show
    img.onerror = show
    img.src = plateUrl(plate)
    if (img.complete) show()
    return () => { live = false }
  }, [plate.set, plate.key])
  useEffect(() => {
    const idle = (window as unknown as { requestIdleCallback?: (cb: () => void) => number }).requestIdleCallback
    const run = () => { for (const set of ['sumi', 'wash'] as const) for (const key of KEYS) { const i = new Image(); i.src = plateUrl({ set, key }) } }
    const id = idle ? idle(run) : window.setTimeout(run, 1500)
    return () => { if (!idle) window.clearTimeout(id) }
  }, [])
  return (
    <div class="ground" aria-hidden="true">
      {slots.map((p, i) => (
        <div class={`plate-img${cur === i ? ' on' : ''}${p?.set === 'sumi' ? ' sumi' : ''}`} style={p ? { backgroundImage: `url(${plateUrl(p)}), url(${tinyUrl(p)})` } : undefined} />
      ))}
      <div class="veil" />
      <div class="wash" />
    </div>
  )
}

function roman(n: number): string {
  const t: [number, string][] = [[50, 'L'], [40, 'XL'], [10, 'X'], [9, 'IX'], [5, 'V'], [4, 'IV'], [1, 'I']]
  let s = ''
  for (const [v, r] of t) while (n >= v) { s += r; n -= v }
  return s
}

// Where the reader's eye is: the chapter, the beat, how far into it, and the day that makes. Written by the scroll
// engine many times a second; only the picture subscribes, so the words never re-render for a day.
interface Eye { chapter: number | null; beat: number; p: number; day: number | null; t: number }
const eyeStore = { v: { chapter: null, beat: -1, p: 0, day: null, t: 0.5 } as Eye, ls: new Set<() => void>() }
function setEye(next: Eye) {
  const c = eyeStore.v
  if (c.chapter === next.chapter && c.beat === next.beat && c.day === next.day && Math.abs(c.p - next.p) < 1 / 24) return
  eyeStore.v = next
  eyeStore.ls.forEach((l) => l())
}
function useEye(): Eye {
  const [, tick] = useState(0)
  useEffect(() => { const l = () => tick((x) => x + 1); eyeStore.ls.add(l); return () => { eyeStore.ls.delete(l) } }, [])
  return eyeStore.v
}

// The day each beat is about, as an index into the chapter's days: the beat's cited day when it has one, the gaps
// spread evenly, never going backwards; the head of the chapter is its first day.
function planDays(c: Chapter, beats: BeatView[]): number[] {
  const n = c.days.length
  const plan: number[] = [0, ...beats.map((b) => (b.day == null ? NaN : Math.max(0, c.days.indexOf(b.day))))]
  for (let i = 1; i < plan.length; i++) {
    if (!Number.isNaN(plan[i])) continue
    let j = i; while (j < plan.length && Number.isNaN(plan[j])) j++
    const a = plan[i - 1], b = j < plan.length ? plan[j] : n - 1
    for (let k = i; k < j; k++) plan[k] = a + ((b - a) * (k - i + 1)) / (j - i + 1)
    i = j
  }
  for (let i = 1; i < plan.length; i++) if (plan[i] < plan[i - 1]) plan[i] = plan[i - 1]
  return plan
}

function Cover({ idx, chapters, lens }: { idx: Index; chapters: Chapter[]; lens: Lens }) {
  const m = idx.b.manifest
  const agents = idx.b.agents.filter((a) => a.id !== 'human').length
  const rooms = idx.b.rooms.length
  const where = isVillage(m) || rooms <= 1 ? 'one chat room' : `${rooms} chat rooms`
  return (
    <section class="cover">
      <h1>{agents} AI agents lived together in {where} for {m.range.last_day - m.range.first_day + 1} days.</h1>
      <p class="lede">This book is what happened, told from their own words. Every line points back to its source.</p>
      <p class="begin">
        <a href={href('book', lens, 1)} onClick={(e) => { e.preventDefault(); const el = document.getElementById('c0') ?? document.getElementById('c1'); if (el) window.scrollTo({ top: el.getBoundingClientRect().top + window.scrollY - 4, behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' }) }}>
          Begin
          <svg class="cue" viewBox="0 0 24 40" width="18" height="30" aria-hidden="true"><path d="M12 2.5 C11.6 12, 12.3 22, 12 34.5 M4.5 27.5 C7 30.5, 9.5 33.5, 12 36.5 C14.6 33.3, 17 30.4, 19.5 27.3" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" /></svg>
          <span class="small">scroll</span>
        </a>
      </p>
      <p class="credits">
        {chapters.length} chapters, one per {isVillage(m) ? 'village' : 'group'} goal. {credit(m)}, research use.
        The tags are a model's reading, not facts. No person is named. <a href={href('overview')}>The appendix</a> holds the six instruments and <a href={href('questions')}>the questions</a> an overseer always asks.
      </p>
    </section>
  )
}

// Before chapter I the book teaches its pictures, one thing per step, beside a thread that draws itself as you scroll.
// Each step sets the picture to one day of the first chapter that shows the thing.
const CLOTH_STEP = 5   // the cloth appears at this step
function prologueDays(idx: Index, chapters: Chapter[], houses: Houses): number[] {
  const first = chapters[0]
  const d1 = first?.first_day ?? idx.b.manifest.range.first_day
  const days = first?.days?.length ? first.days : [d1]
  const t = idx.b.views.timeline, chat = t.series.chat ?? {}
  const sum = (d: number, who: (a: string) => boolean) => { const k = t.days.indexOf(d); return k < 0 ? 0 : Object.entries(chat).filter(([a]) => who(a)).reduce((acc, [, row]) => acc + (row[k] ?? 0), 0) }
  const best = (who: (a: string) => boolean) => days.reduce((b, d) => (sum(d, who) > sum(b, who) ? d : b), d1)
  const busiest = best((a) => a !== 'human'), human = best((a) => a === 'human')
  const talk = days.find((d) => pairsOf(idx, houses, d).length >= 3) ?? busiest
  return [d1, busiest, human, d1, talk, d1, d1]
}

function MoodLegend({ cuts }: { cuts: Cuts }) {
  const n = 14
  return (
    <span class="legend" aria-label="the colour of a strand: violet when displeased, orange when pleased">
      <span>displeased</span>
      <svg width="140" height="10" viewBox="0 0 140 10" aria-hidden="true">
        {Array.from({ length: n }, (_, i) => <rect x={i * 10} y={0} width={10.5} height={10} fill={moodColour(cuts.vLo + ((cuts.vHi - cuts.vLo) * i) / (n - 1), (cuts.aLo + cuts.aHi) / 2, cuts)} />)}
      </svg>
      <span>pleased</span>
    </span>
  )
}

function Prologue({ idx, chapters, cuts, onBeat }: { idx: Index; chapters: Chapter[]; cuts: Cuts; onBeat: number }) {
  const first = chapters[0]
  const d1 = first?.first_day ?? idx.b.manifest.range.first_day
  const founders = (idx.day.get(d1)?.active_agents ?? []).filter((a) => a !== 'human').map((a) => idx.agent.get(a)?.name ?? a)
  const names = founders.length > 1 ? `${founders.slice(0, -1).join(', ')} and ${founders[founders.length - 1]}` : founders.join('')
  const date = idx.day.get(d1)?.date
  const steps: ComponentChildren[] = [
    <>On {date ? fmtDate(date) : `day ${d1}`}, {founders.length || 'four'} models moved into {founders.length || 'four'} houses{names ? `: ${names}` : ''}. A house is a seat: when a model leaves, the next one moves in. Click a house.</>,
    <>A lamp is lit where a resident wrote that day. Smoke rises with how much it said.</>,
    <>A figure at the gate means a human wrote in the room that day.</>,
    <>The board by the gate carries the week's goal. It changed {chapters.length} times in {idx.b.manifest.range.last_day} days.</>,
    <>The residents who talked most that day stand together on the road. The line under the picture says who, and what the room talked about most.</>,
    <>Below the village, the whole year is one cloth: a strand per house, a row per day. A strand's colour is the resident's mood that day, as a model read its words, <MoodLegend cuts={cuts} /> vivid when stirred and pale when calm. Thick means many messages; teal is the humans. A knot is a new resident, a ring a mood swing, a red tick a day its posts were elsewhere. Hover a strand; click a row to open its day.</>,
    <>The five glyphs in the corner change what the pictures show: the village; the goal, with red ink where a resident wandered from it; memory, what each kept overnight; the humans; or one agent alone. Try one, then read on.</>,
  ]
  return (
    <section class="prologue" id="c0">
      <svg class="thread" viewBox="0 0 12 1000" preserveAspectRatio="none" aria-hidden="true">
        <path pathLength={1000} d="M6 0 C 7.5 120, 4.5 240, 6 360 S 7.6 600, 5.2 720 S 6.8 940, 6 1000" />
      </svg>
      {steps.map((st, k) => <p class={`beat step${onBeat === k ? ' on' : ''}`} data-chapter={0} data-beat={k}>{st}</p>)}
    </section>
  )
}

export function Book({ idx, route }: { idx: Index; route: Route }) {
  const agentIds = useMemo(() => new Set(idx.b.agents.map((a) => a.id)), [idx])
  const lens = parseLens(route.arg, agentIds)
  const { data: book, loading: bookLoading } = useLoad<BookFile>('book.json')
  const { data: findings } = useLoad<Finding[]>('findings.json')
  const chapters = useMemo(() => (book?.chapters?.length ? book.chapters : deriveChapters(idx.b)), [book, idx])
  const paras = useMemo(() => new Map((book?.days ?? []).map((d) => [d.day, d])), [book])
  const moods = useMemo(() => villageMood(idx.b), [idx])
  const cuts = useMemo(() => moodCuts(moods.values()), [moods])          // the village's days: picks the plate
  const acuts = useMemo(() => agentCuts(idx.b), [idx])                    // every agent-day: colours threads and notes
  const houses = useMemo(() => buildHouses(idx.b), [idx])
  const agentList = useMemo(() => idx.b.agents.filter((a) => a.id !== 'human').slice().sort((a, b) => (a.first_day ?? 0) - (b.first_day ?? 0)).map((a) => ({ id: a.id, name: a.name })), [idx])
  const beatsBy = useMemo(() => new Map(chapters.map((c) => [c.n, beatsOf(c)])), [chapters])
  const plans = useMemo(() => new Map(chapters.map((c) => [c.n, planDays(c, beatsBy.get(c.n) ?? [])])), [chapters, beatsBy])
  const prologue = useMemo(() => prologueDays(idx, chapters, houses), [idx, chapters, houses])
  const prologueRef = useRef(prologue)
  prologueRef.current = prologue

  const wide = useWide()
  const chapterN = route.sub && /^\d+$/.test(route.sub) ? Number(route.sub) : null
  const chapter = chapterN != null ? chapters.find((c) => c.n === chapterN) ?? null : null
  const dayRaw = route.sub2 && /^\d+$/.test(route.sub2) ? Number(route.sub2) : null
  const day = chapter && dayRaw != null && chapter.days.includes(dayRaw) ? dayRaw : null
  // the open note is the fifth segment of the hash, so a note can be linked to
  const open = day != null && route.sub3 && (route.sub3 === 'human' || agentIds.has(route.sub3)) ? route.sub3 : null
  const agent = lensAgent(lens)
  const [sheet, setSheet] = useState(false)   // the phone's full-cloth sheet
  const sheetBox = useRef<HTMLDivElement>(null)
  useEffect(() => { document.documentElement.classList.toggle('sheet-open', sheet); return () => document.documentElement.classList.remove('sheet-open') }, [sheet])

  // the chapter and beat under the eye (state: the words care); the day and the progress live in the store (the picture cares)
  const [cur, setCur] = useState<{ chapter: number | null; beat: number }>({ chapter: chapterN, beat: -1 })
  const curRef = useRef(cur)
  const routeChapter = useRef(chapterN)
  routeChapter.current = chapterN
  const lensRef = useRef(lens)
  lensRef.current = lens
  const fromScroll = useRef(false)
  const routeDay = useRef(day)
  routeDay.current = day
  const quietUntil = useRef(0)   // after a scroll the page made itself, the engine does not write the route for a moment

  useEffect(() => {
    document.documentElement.classList.add('book-open')
    return () => document.documentElement.classList.remove('book-open')
  }, [])
  useEffect(() => {
    const c = cur.chapter != null ? chapters.find((x) => x.n === cur.chapter) : null
    document.title = c ? `${c.title} · Swarm memory` : 'Swarm memory'
  }, [cur.chapter, chapters])

  // the scroll engine: the beat nearest the 45% line, and how far past it the eye is
  useEffect(() => {
    let frame = 0
    let marks: { chapter: number; beat: number; top: number }[] = []
    const measure = () => {
      marks = [...document.querySelectorAll<HTMLElement>('[data-beat]')].map((el) => ({ chapter: Number(el.dataset.chapter), beat: Number(el.dataset.beat), top: el.getBoundingClientRect().top + window.scrollY }))
    }
    const publish = (chapterNo: number, beat: number, p: number) => {
      if (chapterNo === 0) {
        const dayNo = prologueRef.current[Math.max(0, beat)] ?? prologueRef.current[0] ?? 1
        setEye({ chapter: 0, beat, p, day: dayNo, t: 0.5 })
        if (curRef.current.chapter !== 0 || curRef.current.beat !== beat) { curRef.current = { chapter: 0, beat }; setCur(curRef.current) }
        if (routeChapter.current != null && performance.now() > quietUntil.current && routeDay.current == null) { fromScroll.current = true; go('book', lensRef.current, null, null, null, true) }
        return
      }
      const c = chapters.find((x) => x.n === chapterNo)
      if (!c) return
      const plan = plans.get(chapterNo) ?? [0]
      const n = c.days.length
      let at = 0
      if (beat >= 0) { const a = plan[beat + 1] ?? 0, b = beat + 2 < plan.length ? plan[beat + 2] : n - 1; at = a + (b - a) * p }
      const dayNo = c.days[Math.min(n - 1, Math.max(0, Math.round(at)))] ?? c.first_day
      setEye({ chapter: chapterNo, beat, p, day: dayNo, t: beat < 0 ? 0.5 : at + 0.5 - Math.round(at) })
      if (curRef.current.chapter !== chapterNo || curRef.current.beat !== beat) { curRef.current = { chapter: chapterNo, beat }; setCur(curRef.current) }
      // a day the reader chose stays in the route until they have read on past the next chapter
      const keepDay = routeDay.current != null && routeChapter.current != null && Math.abs(routeChapter.current - chapterNo) <= 1
      if (routeChapter.current !== chapterNo && performance.now() > quietUntil.current && !keepDay) { fromScroll.current = true; go('book', lensRef.current, chapterNo, null, null, true) }
    }
    const on = () => {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(() => {
        const probe = window.scrollY + window.innerHeight * 0.52
        const pro = document.getElementById('c0')
        if (pro) { const r = pro.getBoundingClientRect(); pro.style.setProperty('--thread', String(Math.min(1, Math.max(0, (probe - (r.top + window.scrollY)) / Math.max(1, r.height))))) }
        let i = -1
        for (let k = 0; k < marks.length; k++) { if (marks[k].top <= probe) i = k; else break }
        if (i < 0) {
          setEye({ chapter: null, beat: -1, p: 0, day: null, t: 0.5 })
          if (curRef.current.chapter != null) { curRef.current = { chapter: null, beat: -1 }; setCur(curRef.current) }
          return
        }
        const m = marks[i], next = marks[i + 1]
        const span = next ? Math.max(1, next.top - m.top) : window.innerHeight * 0.5
        publish(m.chapter, m.beat, Math.min(1, Math.max(0, (probe - m.top) / span)))
      })
    }
    measure(); on()
    const ro = new ResizeObserver(() => { measure(); on() })
    ro.observe(document.body)
    window.addEventListener('scroll', on, { passive: true })
    window.addEventListener('resize', on)
    return () => { ro.disconnect(); window.removeEventListener('scroll', on); window.removeEventListener('resize', on); cancelAnimationFrame(frame) }
  }, [chapters, plans])

  // a chapter in the route (a deep link, Begin, a turn, the keyboard) scrolls the words to it; the scroll engine's own route writes do not
  const scrolledTo = useRef<number | null>(null)
  useEffect(() => {
    if (chapterN == null) { scrolledTo.current = null; return }
    if (fromScroll.current) { fromScroll.current = false; scrolledTo.current = chapterN; return }
    // a lens switch keeps the reader's place; only a new chapter (or the book's arrival) moves the page
    if (scrolledTo.current === chapterN && chapters.length > 1) return
    scrolledTo.current = chapterN
    const jump = () => { const el = document.getElementById(`c${chapterN}`); if (el) window.scrollTo({ top: el.getBoundingClientRect().top + window.scrollY - 4, behavior: 'auto' }) }
    quietUntil.current = performance.now() + 1400
    jump()
    // fonts and plates settle after the first paint and move the page; land once more
    const again = window.setTimeout(() => { quietUntil.current = performance.now() + 900; jump() }, 500)
    return () => window.clearTimeout(again)
  }, [chapterN, lens, chapters.length])
  // a day opened from the picture must not move the reader: the fold is scrolled to only on a narrow screen,
  // or when the day belongs to another chapter than the one under the eye (a deep link, a cloth click far away)
  useEffect(() => {
    if (day == null) return
    if (wide && chapter && curRef.current.chapter === chapter.n) return
    const el = document.getElementById('day')
    if (el) el.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'center' })
  }, [day])

  // ← → turn the day, or the chapter when no day is open
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
      const tag = (e.target as HTMLElement | null)?.tagName
      if (tag === 'SELECT' || tag === 'INPUT' || tag === 'TEXTAREA') return
      const dir = e.key === 'ArrowRight' ? 1 : -1
      const here = chapter ?? chapters.find((c) => c.n === cur.chapter) ?? null
      if (!here) { if (dir > 0 && chapters.length) go('book', lens, chapters[0].n); return }
      if (day == null) {
        const next = chapters.find((c) => c.n === here.n + dir)
        if (next) go('book', lens, next.n)
        return
      }
      const i = here.days.indexOf(day) + dir
      if (i >= 0 && i < here.days.length) go('book', lens, here.n, here.days[i])
      else {
        const next = chapters.find((c) => c.n === here.n + dir)
        if (next && next.days.length) go('book', lens, next.n, dir > 0 ? next.days[0] : next.days[next.days.length - 1])
      }
    }
    window.addEventListener('keydown', on)
    return () => window.removeEventListener('keydown', on)
  }, [chapter, chapters, day, lens, cur.chapter])

  const eyeChapter = chapters.find((c) => c.n === cur.chapter) ?? null
  const plate: Plate = !eyeChapter
    ? { set: 'wash', key: 'v-hi_a-hi' }
    : day != null && chapter && chapter.n === eyeChapter.n
      ? { set: 'sumi', key: moodKey(moods.get(day)?.v, moods.get(day)?.a, cuts) }
      : (() => { const mm = meanMood(eyeChapter.days, moods); return { set: 'wash', key: moodKey(mm.v, mm.a, cuts) } })()

  const chapterOfDay = (d: number) => chapters.find((c) => c.days.includes(d)) ?? null
  const pick = (d: number, a: string | null) => {
    const c = chapterOfDay(d)
    if (!c) return
    quietUntil.current = performance.now() + 1200
    go('book', lens, c.n, d, a && d === day && a === open ? null : a)
  }
  const closeNote = () => { if (chapter && day != null) go('book', lens, chapter.n, day) }
  const onCover = cur.chapter == null

  return (
    <div class={`book reader${open ? ' has-note' : ''}${sheet ? ' has-sheet' : ''}${onCover ? ' on-cover' : ''}${cur.chapter === 0 ? ' in-prologue' : ''}${cur.chapter === 0 && cur.beat < CLOTH_STEP ? ' hide-cloth' : ''}`}>
      <Ground plate={plate} />
      {onCover ? <Pigment state={COVER_PIGMENT} /> : null}
      <div class="lens-switch"><ModeSwitch lens={lens} agents={agentList} onChange={(l) => go('book', l, cur.chapter ?? 1, day)} /></div>
      <Cover idx={idx} chapters={chapters} lens={lens} />
      <main class="reader-grid">
        <div class="scroll">
          <Prologue idx={idx} chapters={chapters} cuts={acuts} onBeat={cur.chapter === 0 ? cur.beat : -2} />
          {chapters.map((c) => (
            <ChapterSection key={c.n} idx={idx} count={chapters.length} c={c} lens={lens} agent={agent} beats={beatsBy.get(c.n) ?? []}
              onBeat={cur.chapter === c.n ? cur.beat : -2} day={chapter && chapter.n === c.n ? day : null}
              para={chapter && chapter.n === c.n && day != null ? paras.get(day) ?? null : null} pending={bookLoading}
              findings={findings} houses={houses} />
          ))}
          <footer class="colophon">
            <div class="painting" style={{ backgroundImage: `url(${plateUrl({ set: 'wash', key: 'v-hi_a-hi' })})` }} />
            <p class="faint">{credit(idx.b.manifest)}, research use. The day summaries and the tags were made by our own memory engine and entity tagger; they are a model's reading, not facts. No person is named.</p>
          </footer>
        </div>
        <Widget idx={idx} chapters={chapters} houses={houses} lens={lens} cuts={acuts} day={day} open={open} findings={findings} onPick={pick} wide={wide} />
      </main>
      {open && eyeChapter && day != null ? <Drawer idx={idx} agent={open} day={day} chapter={chapter ?? eyeChapter} cuts={acuts} onClose={closeNote} /> : null}
      {!wide && !onCover && !sheet && eyeChapter ? (
        <button type="button" class="cloth-card" onClick={() => setSheet(true)} aria-label="open the whole year as one cloth">
          <span class="cloth-card-label">the whole year, as one cloth</span>
          <span class="cloth-card-chapter">{roman(eyeChapter.n)} · {eyeChapter.title}</span>
        </button>
      ) : null}
      {sheet ? (
        <div class="cloth-sheet" role="dialog" aria-label="the whole year as one cloth">
          <header>
            <span>The year as one cloth. Tap a row to open that day.</span>
            <button type="button" class="close" onClick={() => setSheet(false)} aria-label="close">×</button>
          </header>
          <div class="cloth-sheet-in" ref={sheetBox}>
            <SheetCloth idx={idx} chapters={chapters} lens={lens} cuts={acuts} day={day} open={open} findings={findings} box={sheetBox}
              onPick={(d, a) => { setSheet(false); pick(d, a) }} />
          </div>
        </div>
      ) : null}
    </div>
  )
}

// The picture beside the words. The plate on top (the village; drawn once the pen lands), the cloth below in a window.
function Widget({ idx, chapters, houses, lens, cuts, day, open, findings, onPick, wide }: {
  idx: Index; chapters: Chapter[]; houses: ReturnType<typeof buildHouses>; lens: Lens; cuts: ReturnType<typeof agentCuts>; day: number | null; open: string | null
  findings: Finding[] | null; onPick: (d: number, a: string | null) => void; wide: boolean
}) {
  const eye = useEye()
  const box = useRef<HTMLDivElement>(null)
  const sceneDay = eye.day ?? chapters[0]?.first_day ?? null
  const boardOf = (dayNo: number) => { const c = chapters.find((x) => x.days.includes(dayNo)); return c ? boardText(c.goal) : null }
  const nameOf = (id: string) => (id === 'human' ? 'the humans' : idx.agent.get(id)?.name ?? id)
  // "Today: A talked with B; C with D. Most mentioned: X, Y, Z."
  const talkLine = (dayNo: number) => {
    const pairs = pairsOf(idx, houses, dayNo).map((p, i) => (i === 0 ? `${nameOf(p.agents[0])} talked with ${nameOf(p.agents[1])}` : `${nameOf(p.agents[0])} with ${nameOf(p.agents[1])}`))
    const topics = topicsOf(idx, dayNo)
    return `${pairs.length ? `. ${pairs.join('; ')}` : ''}${topics.length ? `. Most mentioned: ${topics.join(', ')}` : ''}`
  }
  const d = sceneDay != null ? idx.day.get(sceneDay) : null
  // what the chapters so far turned on: this chapter's figure and the two before it, as scraps beside the village
  const scraps = eye.chapter == null ? [] : chapters.filter((c) => c.n < eye.chapter! && c.figure).slice(-3).reverse()
  return (
    <aside class="widget" aria-label="the picture">
      <div class="plate">
        {sceneDay != null ? <Plate idx={idx} houses={houses} day={sceneDay} t={eye.t} lens={lens} cuts={cuts} findings={findings} board={boardOf(sceneDay)} stage={eye.chapter ? chapters.find((c) => c.n === eye.chapter)?.stage ?? null : null} beat={eye.chapter ? eye.beat : -1} onPick={onPick} /> : null}
      </div>
      <div class="widget-paper">
        <p class="plate-caption">{d && eye.chapter != null ? `day ${d.day}, ${weekday(d.date)} ${fmtDate(d.date)}${talkLine(sceneDay!)}` : '\u00a0'}</p>
        {scraps.length ? (
          <ul class="ledger" aria-label="figures so far">
            {scraps.map((c) => <li title={`${roman(c.n)}: ${c.figure!.value} ${c.figure!.label}`}><b>{c.figure!.value}</b><span>{c.figure!.label}</span></li>)}
          </ul>
        ) : null}
        {wide ? (
          <div class="cloth-window" ref={box}>
            <Tapestry idx={idx} chapters={chapters} lens={lens} cuts={cuts} day={day} open={open} current={eye.chapter} findings={findings}
              onCurrent={() => {}} onPick={onPick} windowed eyeDay={eye.day} box={box} />
          </div>
        ) : null}
      </div>
    </aside>
  )
}

function SheetCloth({ idx, chapters, lens, cuts, day, open, findings, box, onPick }: {
  idx: Index; chapters: Chapter[]; lens: Lens; cuts: ReturnType<typeof agentCuts>; day: number | null; open: string | null
  findings: Finding[] | null; box: { current: HTMLDivElement | null }; onPick: (d: number, a: string | null) => void
}) {
  const eye = useEye()
  return (
    <Tapestry idx={idx} chapters={chapters} lens={lens} cuts={cuts} day={day} open={open} current={eye.chapter} findings={findings}
      onCurrent={() => {}} onPick={onPick} windowed eyeDay={eye.day} box={box} />
  )
}

// One chapter of the scroll: the hook, the figure, the beats, then what unfolds.
const ChapterSection = memo(function ChapterSection({ idx, count, c, lens, agent, beats, onBeat, day, para, pending, findings, houses }: {
  idx: Index; count: number; c: Chapter; lens: Lens; agent: string | null; beats: BeatView[]; onBeat: number; day: number | null
  para: { text: string; cites: { n: number; ref: string; day?: number }[] } | null; pending: boolean; findings: Finding[] | null
  houses: ReturnType<typeof buildHouses>
}) {
  // the findings whose evidence starts in this chapter: a claim, its cited lines, its limit
  const found = (findings ?? []).map((f) => ({ f, ...pinOf(f, houses) })).filter((p) => p.day != null && c.days.includes(p.day))
  const goal = c.goal.trim().replace(/[.!]+$/, '')
  return (
    <section class="chapter" id={`c${c.n}`}>
      <header class="head" data-chapter={c.n} data-beat={-1}>
        <p class="folio">{roman(c.n)} <span class="faint">of {count}</span></p>
        <h1 class="title">{hookOf(c)}</h1>
        <p class="line">{c.hook ? `${c.title}. ` : ''}Days {c.first_day} to {c.last_day}, {fmtRange(c.first_date, c.last_date)}. The goal: <i>{goal}</i>.</p>
        {c.figure ? <div class="figure"><CountFigure value={c.figure.value} label={c.figure.label} live={onBeat >= -1} /></div> : null}
      </header>
      {beats.length ? beats.map((b, k) => (
        <p class={`beat${onBeat === k ? ' on' : ''}`} data-chapter={c.n} data-beat={k}><RichText text={b.text} cites={b.cites} /></p>
      )) : <p class="beat faint" data-chapter={c.n} data-beat={0}>{pending ? 'Opening the book…' : "This chapter's story is still being written."}</p>}
      <div class="more">
        {c.beats?.length && c.text ? (
          <details class="unfold">
            <summary>the full account</summary>
            <p class="para"><RichText text={c.text} cites={c.cites} /></p>
          </details>
        ) : null}
        {found.map((p) => (
          <details class="found" open={day != null && p.day === day}>
            <summary>Found here: {p.f.title}</summary>
            <p class="para"><RichText text={p.f.body} cites={p.f.cites} /></p>
            {internalHref(p.f.view) ? <p class="small"><a href={internalHref(p.f.view)!}>open in the appendix</a></p> : null}
          </details>
        ))}
        {day != null ? <DayFold idx={idx} day={day} agent={agent} para={para} pending={pending} lens={lens} chapter={c} /> : null}
      </div>
    </section>
  )
})

// The chapter's number rises to its value when the chapter comes under the eye (the first number in the string; the rest stays).
function CountFigure({ value, label, live }: { value: string; label: string; live: boolean }) {
  const [shown, setShown] = useState(value)
  const was = useRef(false)
  useEffect(() => {
    const m = /\d[\d,]*(?:\.\d+)?/.exec(value)
    const still = matchMedia('(prefers-reduced-motion: reduce)').matches
    if (!live || !m || still) { was.current = live; setShown(value); return }
    if (was.current) return
    was.current = true
    const target = Number(m[0].replace(/,/g, ''))
    const decimals = (m[0].split('.')[1] ?? '').length
    const grouped = m[0].includes(',')
    const fmt = (n: number) => { const f = n.toFixed(decimals); return grouped ? Number(f).toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals }) : f }
    const t0 = performance.now(), dur = 900
    let frame = 0
    const step = (now: number) => {
      const k = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - k, 3)
      setShown(value.slice(0, m.index) + fmt(target * e) + value.slice(m.index + m[0].length))
      if (k < 1) frame = requestAnimationFrame(step)
    }
    frame = requestAnimationFrame(step)
    return () => cancelAnimationFrame(frame)
  }, [live, value])
  return <Figure value={shown} label={label} />
}

function DayFold({ idx, day, agent, para, pending, lens, chapter }: {
  idx: Index; day: number; agent: string | null; para: { text: string; cites: { n: number; ref: string; day?: number }[] } | null; pending: boolean
  lens: Lens; chapter: Chapter
}) {
  const d = idx.day.get(day)
  const { data: sums } = useLoad<Summary[]>(agent ? `summaries/agent/${agent}.json` : null)
  const own = agent ? sums?.find((s) => s.day === day) ?? null : null
  const preview = idx.village.get(day)
  const name = agent ? idx.agent.get(agent)?.name ?? agent : null
  return (
    <section class="fold" id="day">
      <p class="line">Day {day}{d ? `, ${weekday(d.date)} ${fmtDate(d.date)}` : ''} <a class="faint" href={href('book', lens, chapter.n)} aria-label="close the day">close</a></p>
      {agent ? (
        own ? <p class="para"><span class="asof">{name}: </span><RichText text={own.text} cites={own.cites} day={day} /></p>
          : <p class="para faint">{sums === undefined ? 'Reading…' : `${name} left no account of this day.`}</p>
      ) : null}
      {para ? (
        <p class="para"><RichText text={para.text} cites={para.cites} day={day} /></p>
      ) : !agent ? (
        preview ? <p class="para faint"><span class="asof">from AI Digest's log: </span>{clean(preview.text).slice(0, 420)}…</p> : <p class="para faint">{pending ? 'Opening the book…' : 'No account of this day yet.'}</p>
      ) : null}
      <p class="small"><a href={href('timeline', day)}>this day in the appendix</a></p>
    </section>
  )
}
