import type { ComponentChildren } from 'preact'
import { useEffect, useRef } from 'preact/hooks'
import { useLoad, labelName, type Cite, type SourcesFile, type Index } from './data'
import { openSnippet, useSnippet, href } from './route'

const REDACTED = /\[(human|email|id)\]/g

// Plain text with redaction tokens shown as small neutral chips.
export function Redacted({ text }: { text: string }) {
  const out: ComponentChildren[] = []
  let last = 0
  for (const m of text.matchAll(REDACTED)) {
    if (m.index! > last) out.push(text.slice(last, m.index))
    out.push(<span class="chip" title="redacted in the bundle">{m[1]}</span>)
    last = m.index! + m[0].length
  }
  if (last < text.length) out.push(text.slice(last))
  return <>{out}</>
}

// Village texts from AI Digest arrive wrapped in pseudo-XML; keep the words, drop the tags.
export function clean(text: string): string {
  return text
    .replace(/<\/?[a-z_]+>/gi, '')
    .replace(/\*\*(.+?)\*\*/g, '$1')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

const TOKEN = /\[(\d+)\]|\[(human|email|id)\]|`([^`\n]+)`/g

// The day whose sources/<day>.json holds a cite: its own day, else the text's day; null opens nothing.
export function citeDay(c: Cite, day?: number): number | null {
  const d = c.day ?? day
  return d != null && Number.isInteger(d) ? d : null
}

// A summary text: [n] markers open the cited snippet, redactions become chips.
// `day` is the text's own day (agent, room, village summaries); findings pass none and rely on each cite's day.
export function RichText({ text, cites, day, block }: { text: string; cites: Cite[]; day?: number; block?: boolean }) {
  const byN = new Map(cites.map((c) => [c.n, c]))
  const paras = clean(text).split(/\n+/)
  const render = (t: string) => {
    const out: ComponentChildren[] = []
    let last = 0
    for (const m of t.matchAll(TOKEN)) {
      if (m.index! > last) out.push(t.slice(last, m.index))
      if (m[1]) {
        const n = Number(m[1])
        const c = byN.get(n)
        const d = c ? citeDay(c, day) : null
        out.push(
          c && d != null ? (
            <button class="cite" type="button" aria-label={`open citation ${n}`} onClick={() => openSnippet({ ref: c.ref, day: d, n })}>
              {n}
            </button>
          ) : (
            <sup class="cite cite-dead">{n}</sup>
          ),
        )
      } else if (m[2]) {
        out.push(<span class="chip" title="redacted in the bundle">{m[2]}</span>)
      } else if (m[3]) {
        out.push(<code>{m[3]}</code>)
      }
      last = m.index! + m[0].length
    }
    if (last < t.length) out.push(t.slice(last))
    return out
  }
  // cites the text never marks still get a receipt at the end
  const marked = new Set([...text.matchAll(/\[(\d+)\]/g)].map((m) => Number(m[1])))
  const rest = cites.filter((c) => !marked.has(c.n))
  const tail = rest.map((c) => {
    const d = citeDay(c, day)
    return d == null ? <sup class="cite cite-dead">{c.n}</sup> : (
      <button class="cite" type="button" aria-label={`open citation ${c.n}`} onClick={() => openSnippet({ ref: c.ref, day: d, n: c.n })}>
        {c.n}
      </button>
    )
  })
  if (!block) return <>{render(paras.join(' '))}{tail}</>
  return <>{paras.map((p, i) => <p>{render(p)}{i === paras.length - 1 ? tail : null}</p>)}</>
}

// A snippet with its tagged spans highlighted.
export function Spans({ text, spans, focus }: { text: string; spans: [number, number, string][]; focus?: string }) {
  const out: ComponentChildren[] = []
  let last = 0
  const sorted = [...spans].filter((s) => s[0] < text.length).sort((a, b) => a[0] - b[0])
  for (const [s, e0, label] of sorted) {
    if (s < last) continue
    const e = Math.min(e0, text.length)
    if (s > last) out.push(<Redacted text={text.slice(last, s)} />)
    const surface = text.slice(s, e)
    const hit = focus && surface.toLowerCase() === focus.toLowerCase()
    out.push(
      <mark class={hit ? 'span hit' : 'span'} title={labelName(label)}>
        {surface}
      </mark>,
    )
    last = e
  }
  if (last < text.length) out.push(<Redacted text={text.slice(last)} />)
  return <>{out}</>
}

const KIND: Record<string, string> = {
  chat: 'chat message', session_goal: 'session goal', session_summary: 'session summary', next_goal: 'next goal',
  history_query: 'history search', outreach: 'outreach', thinking: 'thinking', cc_text: 'code agent text',
  cc_thinking: 'code agent thinking', memory: 'memory', digest_summary: 'AI Digest summary',
}
export const kindName = (k: string) => KIND[k] ?? k.replace(/_/g, ' ')

export function timeOf(iso: string) {
  return iso.replace('T', ' ').replace(/:\d\dZ$/, '').replace(/Z$/, '')
}

// One cited source row, loaded from sources/<day>.json.
export function SourceCard({ idx, refId, day, focus }: { idx: Index; refId: string; day: number; focus?: string }) {
  const { data, loading } = useLoad<SourcesFile>(`sources/${day}.json`)
  if (loading) return <p class="quiet">loading the day's sources</p>
  const s = data?.[refId]
  if (!s) return <p class="quiet">This source is not in day {day}'s snippet file.</p>
  const agent = s.agent ? idx.agent.get(s.agent) : undefined
  return (
    <figure class="source">
      <figcaption>
        {!s.agent ? (
          <span>AI Digest</span>
        ) : s.agent === 'human' ? (
          <span>a human</span>
        ) : (
          <a href={href('agents', s.agent)}>{agent?.name ?? s.agent}</a>
        )}
        <span class="quiet"> {kindName(s.kind)}{s.room ? ` in ${s.room}` : ''}</span>
        <span class="fig"> {timeOf(s.time)}</span>
      </figcaption>
      <blockquote>
        {s.spans.length ? <Spans text={s.text} spans={s.spans} focus={focus} /> : <Redacted text={clean(s.text)} />}
        {s.text.length >= 600 ? <span class="quiet"> …</span> : null}
      </blockquote>
    </figure>
  )
}

// The citation sheet: a native dialog, one open citation at a time.
export function SnippetSheet({ idx }: { idx: Index }) {
  const req = useSnippet()
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const d = ref.current
    if (!d) return
    if (req && !d.open) d.showModal()
    if (!req && d.open) d.close()
  }, [req])
  return (
    <dialog
      ref={ref}
      class="sheet"
      onClose={() => openSnippet(null)}
      onClick={(e) => { if (e.target === ref.current) openSnippet(null) }}
      aria-label="cited source"
    >
      {req ? (
        <div class="sheet-body">
          <header>
            <h2>{req.n != null ? <>Source <span class="fig">[{req.n}]</span></> : 'Source'}</h2>
            <button class="verb" type="button" onClick={() => openSnippet(null)}>close</button>
          </header>
          <SourceCard idx={idx} refId={req.ref} day={req.day} focus={req.highlight} />
          <p class="ref fig" title="dataset row id">{req.ref}</p>
        </div>
      ) : null}
    </dialog>
  )
}
