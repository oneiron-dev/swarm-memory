import { useEffect, useState } from 'preact/hooks'
import { load, type Cite, type Index } from '../data'
import { RichText } from '../text'
import { href } from '../route'

// The questions an overseer always asks of a swarm, answered for the year and per chapter, every answer cited.
// The file is written by the questions step (docs/questions/make_questions.py) and served as data/questions.json
// beside the bundle it was made from; it is a model's reading of cited material. A bundle without one falls back to
// the copy in the web build (web/public/questions/questions.json), so the live site answers either way.
interface QCite extends Cite { chapter?: number }
interface Question { id: string; text: string; year: { text: string; cites: QCite[] }; chapters: { n: number; text: string; cites: Cite[] }[]; numbers?: unknown }
export interface QuestionsFile { schema: string; generated?: string; model?: string; questions: Question[] }

const usable = (j: QuestionsFile | null): QuestionsFile | null => (j && Array.isArray(j.questions) ? j : null)
// The bundle's own answers when it has a usable file, else the web build's copy, else null.
export function questionsFrom(bundle: () => Promise<QuestionsFile | null>,
  build: () => Promise<QuestionsFile | null>): Promise<QuestionsFile | null> {
  return bundle().then((j) => usable(j) ?? build().then(usable))
}
let questions: Promise<QuestionsFile | null> | null = null
function loadQuestions(): Promise<QuestionsFile | null> {
  questions ??= questionsFrom(() => load<QuestionsFile>('questions.json'),
    () => fetch('./questions/questions.json').then((r) => (r.ok ? r.json() : null)).catch(() => null))
  return questions
}

export function Questions({ idx }: { idx: Index }) {
  const [file, setFile] = useState<QuestionsFile | null | undefined>(undefined)
  useEffect(() => {
    let live = true
    loadQuestions().then((j) => { if (live) setFile(j) })
    return () => { live = false }
  }, [])
  const nChapters = file?.questions[0]?.chapters.length ?? 0
  const chapterTitle = (n: number) => `chapter ${n}`
  return (
    <div class="stack">
      <section class="hero">
        <h1 class="place">The questions an overseer always asks</h1>
        <div class="read lede">
          <p>Eight questions you would put to any group of agents, answered for the whole run and for each of its {nChapters} chapters, every answer pointing at its source. The answers are a model's reading of the cited summaries, not facts.</p>
        </div>
      </section>
      {file === undefined ? <div class="well loading">Reading the answers</div> : null}
      {file === null ? <div class="well"><p class="read">The answers are not in this build yet.</p></div> : null}
      {file ? file.questions.map((q, i) => (
        <section class="well" id={q.id}>
          <div class="head"><h2>{i + 1}. {q.text}</h2></div>
          <p class="read"><RichText text={q.year.text} cites={q.year.cites} /></p>
          {q.chapters.filter((c) => c.text && !/^nothing recorded/i.test(c.text)).length ? (
            <details class="mt-s">
              <summary class="quiet">week by week</summary>
              <ul class="read">
                {q.chapters.filter((c) => c.text && !/^nothing recorded/i.test(c.text)).map((c) => (
                  <li><a href={href('book', 'village', c.n)}>{chapterTitle(c.n)}</a>: <RichText text={c.text} cites={c.cites} /></li>
                ))}
              </ul>
            </details>
          ) : null}
        </section>
      )) : null}
      {file ? <p class="quiet">Written by {file.model ?? 'the summarising model'} from the served bundle{file.generated ? ` on ${file.generated.slice(0, 10)}` : ''}; {idx.b.manifest.counts.summaries ?? ''} summaries behind it.</p> : null}
    </div>
  )
}
