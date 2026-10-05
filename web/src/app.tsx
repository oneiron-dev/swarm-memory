import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { buildIndex, credit, isVillage, load, type Bundle, type Index } from './data'
import { develop } from './field'
import { VIEWS, href, useRoute } from './route'
import { SnippetSheet } from './text'
import { Overview } from './views/overview'
import { Timeline } from './views/timeline'
import { Agents } from './views/agents'
import { Rooms } from './views/rooms'
import { Memory } from './views/memory'
import { Entities } from './views/entities'
import { Questions } from './views/questions'
import { Book } from './book/book'
import './styles/book.css'

function Field() {
  const ref = useRef<HTMLCanvasElement>(null)
  const [developing, setDeveloping] = useState(true)
  useEffect(() => {
    if (ref.current) develop(ref.current, () => setDeveloping(false))
  }, [])
  return (
    <>
      <div class="field" aria-hidden="true" />
      {developing ? <canvas ref={ref} class="develop" aria-hidden="true" /> : null}
    </>
  )
}

export function App() {
  const [bundle, setBundle] = useState<Bundle | null | undefined>(undefined)
  useEffect(() => {
    load<Bundle>('bundle.json').then(setBundle)
  }, [])
  const idx = useMemo(() => (bundle ? buildIndex(bundle) : null), [bundle])
  const route = useRoute()

  useEffect(() => {
    if (route.view === 'book') return
    const name = VIEWS.find((v) => v.id === route.view)?.name ?? ''
    document.title = route.view === 'overview' ? 'Swarm memory: the AI Village, read' : `${name[0].toUpperCase()}${name.slice(1)} · Swarm memory`
  }, [route.view])

  useEffect(() => {
    window.scrollTo({ top: 0 })
  }, [route.view])
  // the appendix wears the book's paper
  useEffect(() => {
    document.documentElement.classList.toggle('appendix', route.view !== 'book')
    return () => document.documentElement.classList.remove('appendix')
  }, [route.view])

  // the book is the home; the six views below are its appendix
  if (route.view === 'book') {
    return (
      <>
        {bundle === undefined ? (
          <div class="book"><main class="pages"><p class="faint" style="padding-top: 30vh">Opening the book</p></main></div>
        ) : !idx ? (
          <div class="book"><main class="pages"><p class="faint" style="padding-top: 30vh">The bundle did not load. The app reads ./data/bundle.json beside it.</p></main></div>
        ) : (
          <Book idx={idx} route={route} />
        )}
        {idx ? <SnippetSheet idx={idx} /> : null}
      </>
    )
  }

  return (
    <>
      <Field />
      <div class="app">
        <header class="mast">
          <a class="brand" href={href('overview')}>
            <b>Swarm memory</b> <span>the AI Village, read</span>
          </a>
          <nav class="nav" aria-label="views">
            <a href={href('book')}><span class="mk" aria-hidden="true">◈</span>the book</a>
            {VIEWS.map((v) => (
              <a href={href(v.id)} aria-current={route.view === v.id ? 'page' : undefined}>
                <span class="mk" aria-hidden="true">{route.view === v.id ? '◆' : '◇'}</span>
                {v.name}
              </a>
            ))}
          </nav>
        </header>
        <main>
          {bundle === undefined ? (
            <div class="well loading">Loading the bundle</div>
          ) : !idx ? (
            <div class="well">
              <h1 class="place">The bundle did not load</h1>
              <p class="read lede">The app reads ./data/bundle.json beside it. Reload, or check that the data folder is served.</p>
            </div>
          ) : (
            <ViewFor idx={idx} />
          )}
        </main>
        <footer class="foot">
          {!idx || isVillage(idx.b.manifest) ? (
            <p>
              Data: AI Digest / AI Village (<a href="https://theaidigest.org/village" target="_blank" rel="noopener">theaidigest.org/village</a>). Research use only.
            </p>
          ) : (
            <p>{credit(idx.b.manifest)}. Research use only.</p>
          )}
          <p>Entities are unconfirmed tags from our own entity tagger, not facts.</p>
        </footer>
      </div>
      {idx ? <SnippetSheet idx={idx} /> : null}
    </>
  )
}

function ViewFor({ idx }: { idx: Index }) {
  const route = useRoute()
  switch (route.view) {
    case 'timeline': return <Timeline idx={idx} route={route} />
    case 'agents': return <Agents idx={idx} route={route} />
    case 'rooms': return <Rooms idx={idx} route={route} />
    case 'memory': return <Memory idx={idx} route={route} />
    case 'entities': return <Entities idx={idx} route={route} />
    case 'questions': return <Questions idx={idx} />
    default: return <Overview idx={idx} />
  }
}
