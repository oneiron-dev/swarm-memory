import { fmt, short, useLoad, type Finding, type Index } from '../data'
import { Legend } from '../chart'
import { go, href, internalHref } from '../route'
import { RichText } from '../text'
import { ActivityChart } from './timeline'

export function Overview({ idx }: { idx: Index }) {
  const m = idx.b.manifest
  const c = m.counts
  const span = m.range.last_day - m.range.first_day + 1
  const agents = idx.b.agents.filter((a) => a.id !== 'human').length
  const findings = useLoad<Finding[]>('findings.json')

  return (
    <div class="stack">
      <section class="hero">
        <h1 class="place">What {agents} AI agents did, said and remembered in {span} days of the AI Village</h1>
        <div class="read lede">
          <p>
            The AI Village is a long-running experiment by AI Digest: frontier models share a chat, their own computers and a common goal, and every turn is logged.
            We read those logs with <b>Oneiron</b>, our memory engine for agents.
          </p>
          <p>
            <b>oneiroNER</b>, our on-device tagger, read {short(c.tagged_rows ?? c.slice_rows)} turns, found {fmt(c.mentions)} mentions that group into the top {fmt(c.entities)} entities, and scored the mood of every turn.
            {' '}{m.summarizer} wrote {fmt(c.summaries)} summaries, one per agent, room and day, and every claim in them cites its source.
          </p>
        </div>
      </section>

      <section class="well">
        <div class="head">
          <h2>The village in numbers</h2>
          <a class="verb" href={href('timeline')}>open the timeline</a>
        </div>
        <div class="kv" style={{ marginBottom: '16px' }}>
          <div>agents <span class="fig">{fmt(agents)}</span></div>
          <div>rooms <span class="fig">{fmt(c.rooms)}</span></div>
          <div>days <span class="fig">{fmt(span)}</span></div>
          <div>text units <span class="fig">{short(c.slice_rows)}</span></div>
          <div>tokens <span class="fig">{short(c.slice_tokens)}</span></div>
          <div>mentions <span class="fig">{short(c.mentions)}</span></div>
          <div>entities <span class="fig">{fmt(c.entities)}</span></div>
          <div>summaries <span class="fig">{fmt(c.summaries)}</span></div>
        </div>
        <div style={{ marginBottom: '8px' }}><Legend /></div>
        <ActivityChart idx={idx} metric="chat" onPick={(d) => go('timeline', d)} height={150} compact />
        <p class="quiet mt-s" style={{ fontSize: '14px' }}>
          Messages per day, by lab, from day {m.range.first_day} ({m.range.first_date}) to day {m.range.last_day} ({m.range.last_date}). The strip marks the {idx.b.goals.length} village goals. Pick a day to open it.
        </p>
      </section>

      {findings.data && findings.data.length > 0 && (
        <section class="well">
          <h2>What we found</h2>
          <div class="findings mt-s">
            {findings.data.map((f) => {
              const link = internalHref(f.view)
              return (
                <article class="summary">
                  <h3>{link ? <a href={link}>{f.title}</a> : f.title}</h3>
                  <p class="txt mt-s"><RichText text={f.body} cites={f.cites ?? []} /></p>
                </article>
              )
            })}
          </div>
        </section>
      )}

      <section class="well">
        <h2>Where to look</h2>
        <ul class="rows mt-s">
          <li><a class="row" href={href('timeline')}><span class="grow">Timeline: every day, its goal, who worked, and what happened</span></a></li>
          <li><a class="row" href={href('agents')}><span class="grow">Agents: goals, top entities, mood over time and cited daily summaries</span></a></li>
          <li><a class="row" href={href('rooms')}><span class="grow">Who talks to whom: mentions and replies between agents, by day range</span></a></li>
          <li><a class="row" href={href('memory')}><span class="grow">Memory drift: what each agent kept in memory and what it dropped</span></a></li>
          <li><a class="row" href={href('entities')}><span class="grow">Entities: the {fmt(c.entities)} things the swarm talked about most</span></a></li>
        </ul>
      </section>

      <section class="well">
        <h2>How it was built</h2>
        <ul class="read mt-s method">
          <li>The AI Village logs were cut into {short(c.slice_rows)} text units: chat, session goals and summaries, thinking, memories and AI Digest's own daily summaries.</li>
          <li>oneiroNER ({m.tagger.checkpoint.split(':')[0]}) tagged {m.tagger.labels.length} entity types and a valence, arousal and dominance score for each unit, on a Mac mini.</li>
          <li>Mentions of the same name and type became one entity; mentions of an agent's name link to that agent.</li>
          <li>Humans in the logs are one pseudo-agent, and their names, emails and ids are redacted.</li>
        </ul>
      </section>
    </div>
  )
}
