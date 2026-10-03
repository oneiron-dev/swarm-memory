import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { buildIndex, load, type Bundle, type Index } from './data'
import { develop } from './field'
import { VIEWS, href, useRoute } from './route'
import { SnippetSheet } from './text'
import { Overview } from './views/overview'
import { Timeline } from './views/timeline'
import { Agents } from './views/agents'
import { Rooms } from './views/rooms'
import { Memory } from './views/memory'
import { Entities } from './views/entities'

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
    const name = VIEWS.find((v) => v.id === route.view)?.name ?? ''
    document.title = route.view === 'overview' ? 'Swarm memory: the AI Village through Oneiron' : `${name[0].toUpperCase()}${name.slice(1)} · Swarm memory`
  }, [route.view])

  useEffect(() => {
    window.scrollTo({ top: 0 })
  }, [route.view])

  return (
    <>
      <Field />
      <div class="app">
        <header class="mast">
          <a class="brand" href={href('overview')}>
            <b>Swarm memory</b> <span>Oneiron over the AI Village</span>
          </a>
          <nav class="nav" aria-label="views">
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
          <p>
            Data: AI Digest / AI Village (<a href="https://theaidigest.org/village" target="_blank" rel="noopener">theaidigest.org/village</a>). Research use only.
          </p>
          <p>Entities are unconfirmed tags from oneiroNER, not facts.</p>
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
    default: return <Overview idx={idx} />
  }
}
