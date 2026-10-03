# web: the judge-facing frontend

One static app (Vite, Preact, TypeScript, hand-rolled SVG charts) that shows what the AI Village swarm did, who talked to whom, what each agent remembered and how its memory drifted. It reads the bundle described in `docs/schema.md` (section 4) from the relative path `./data/`: `bundle.json` at start, everything else on demand with a small in-memory cache.

Views, each deep-linkable by URL hash:

| hash | view |
|---|---|
| `#overview` | what this is, headline counts, messages per day by lab, optional findings from `data/findings.json` |
| `#timeline/<day>` | daily activity (messages, sessions, thinking, mentions) with village goals as bands; a day's goal, counts, agents, top entities and village summary |
| `#agents/<agent>` | agents grouped by lab: goals, stats, top entities, mood over days, cited daily summaries |
| `#rooms/<room>` | who talks to whom (mentions and replies, by day range) and each room's daily summaries |
| `#memory/<agent>/<day>` | per-agent memory drift (1 - jaccard with the previous kept memory), added and dropped entities, excerpt |
| `#entities/<slug>` | searchable table of the top entities; detail with a per-day sparkline, per-agent bars and tagged example snippets |

Every `[n]` in a summary opens the cited snippet from `sources/<day>.json`. Redaction tokens (`[human]`, `[email]`, `[id]`) render as small neutral chips. Add `?theme=light` or `?theme=dark` to force a theme; otherwise the system setting decides.

## Commands

```bash
cd web
bun install
bun run dev          # http://localhost:5173, proxies /data to the live bundle on Arch (set DATA_ORIGIN to change)
bun run dev:sample   # same app on the synthetic bundle in sample-data/
bun run test         # route, chart and citation safety tests (bun test)
bun run build        # type-check, then build to dist/
bun run deploy       # build, then rsync dist/ to Arch web-dist/ (served at / by hk-serve)
```

The real bundle is never copied into this repo. `public/data/` is git-ignored in case you rsync a local copy there; `sample-data/` holds only the synthetic fixture bundle from `pipeline/bundle/tests/expected/data/` plus a synthetic `findings.json`.

## Design

Oneiron Safelight: the dithered field (night and day plates from oneiron-web) develops through an 8x8 Bayer screen on load, content sits in wells carved into it, Source Serif 4 for words and Server Mono for figures. Fonts are vendored under the SIL Open Font License (`public/fonts/FONTS.md`, licence texts beside the fonts). The two field plates are Oneiron art with all rights reserved, not covered by any code licence: see `public/art/ASSETS.md`. The lab colours are three hues that pass the dataviz palette validator on all pairs in both themes (Anthropic, OpenAI, Google), with other labs in neutral ink and humans in the outside hue.

Data: AI Digest / AI Village (theaidigest.org/village). Research use only. Entities are unconfirmed tags from oneiroNER, not facts.
