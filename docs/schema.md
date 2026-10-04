# Swarm memory bundle: schema v1

Contract between the pipeline (Arch) and the frontend (static Vite app).
Pipeline-internal records (sections 1 to 3) stay on Arch. Only the bundle (section 4) is served.

Source: AI Village dataset by AI Digest (huggingface.co/datasets/aidigestorg/ai-village, revision 838b4150).
Research use only. No training on it. No re-identification. Every page cites "AI Digest / AI Village".

## 0. Conventions

- Times are UTC ISO strings with a `Z` (`2026-07-10T21:09:27Z`).
- `day` is the AI Village day number (day 1 = 2025-04-02), taken from `village-transcript.json` (`days[].day/date`). `date` is that day's calendar date.
- Agent ids are slugs of the display name (`claude-opus-4-5`, `gpt-5-6-luna`). Agent names are model names and are public.
- Humans are never individuals. Every human speaker becomes the single pseudo-agent `human`. No user ids, no viewer names, anywhere in the bundle. Served text carries redaction tokens: `[human]` (a human's name), `[email]`, `[id]`; render them as small neutral chips.
- Source ids (`ref`) point back to dataset rows: `<src>:<row id>[:<part>]`.

| `src` | table | example `ref` |
|---|---|---|
| `chat` | chat_messages | `chat:0000447e-ad6e-…` |
| `ev` | events (by `event_index`) | `ev:401584:summary` |
| `cc` | claude_code_messages | `cc:00003660-…:2` (content block 2) |
| `cus` | computer_use_sessions | `cus:<uuid>` |
| `mem` | agent_memories | `mem:0000287e-…` |
| `sum` | summaries (AI Digest's own) | `sum:<uuid>` |

## 1. Slice record (Arch only): `slice/slice-*.jsonl`

One natural-language unit per line.

```json
{"id":"chat:0000447e-…","src":"chat","time":"2026-07-10T21:09:27Z","day":467,"date":"2026-07-10",
 "agent":"gpt-5-5","speaker":"agent","room":"general","session":null,
 "kind":"chat","text":"If any agent wants a 60-second Friday puzzle break…","ntok":71}
```

| field | meaning |
|---|---|
| `id` | the `ref` above; unique |
| `agent` | agent slug, or `human` |
| `speaker` | `agent` or `human` |
| `room` | room name for chat (`general`, `rest`, …), else null |
| `session` | `cus:<uuid>` or `ccs:<sdk_session_id>` when the text belongs to a session, else null |
| `kind` | one of the kinds below |
| `ntok` | tokens under the tagger's tokenizer |

Kinds and their source fields:

| `kind` | from | text field |
|---|---|---|
| `chat` | chat_messages | `content` |
| `session_goal` | computer_use_sessions; events START_USING_COMPUTER | `session_goal` / `data.sessionGoal` |
| `session_summary` | events STOP_USING_COMPUTER | `data.summary` |
| `next_goal` | events CONSOLIDATE | `data.nextSessionGoal` |
| `history_query` | events SEARCH_HISTORY | `data.query` + `data.answerToQuery` |
| `outreach` | events OUTREACH_APPROVAL_* | request/response text |
| `thinking` | events AGENT_TALK raw `output` (reasoning summaries, thinking blocks, `thought: true` parts) | reasoning text only |
| `cc_text` | claude_code_messages, assistant text blocks | block text |
| `cc_thinking` | claude_code_messages, assistant thinking blocks | block text |
| `memory` | agent_memories, sampled: the last memory of each agent per day | `content` |
| `digest_summary` | summaries (`daily`, `goal`, `agent`) | `content` |

Rules: drop scrub markers (`[REDACTED]`, `[BLOB_REMOVED]`, `[IMAGE_REMOVED]`) from token counts but keep them in text; skip tool results, tool calls, screenshots and computer_use_turns in v1; dedupe on a hash of whitespace-folded lowercase text within each kind (keep the earliest); skip texts under 3 characters. Report rows and tokens per kind in `slice/STATS.json`.

## 2. Tag record (Arch only): `tags/tags-*.jsonl`

One line per slice record, same `id`. Produced by oneiroNER (model of record) on the Mac mini.

```json
{"id":"chat:0000447e-…","model":"<checkpoint sha16>","decode":"v1",
 "spans":[{"s":56,"e":69,"label":"PRODUCT","text":"Signal Garden","p":0.97}],
 "mood":{"v":0.42,"a":0.55,"d":0.61},
 "coref":[[[12,14],[56,69]]]}
```

`s`/`e` are character offsets into the slice `text`. `mood` and `coref` are present only when that head ran (v1: mood yes, coref no). `model` names the checkpoints, e.g. `rd16c-ch:0b5ff938e12463f8+vad:acbf6b43c6662813`.

Labels (v1, oneiroNER rd16c-ch), in this order: `PERSON`, `PLACE`, `ORG`, `DATE`, `EVENT`, `RELATIONSHIP_REF`, `EMOTION`, `MONEY`, `PRODUCT`. A span may carry `sub` when the model predicted a subtype: `DATE/{Day,Week,Month,Season,Year,Decade,Relative,Range}`, `EVENT/{Life,General}`, `RELATIONSHIP_REF/{Family,Romantic,Friend,Professional,Acquaintance}`, `PRODUCT/Medicine`, `EMOTION/Negated`. Mood: `v` in [-1, 1] (valence), `a` and `d` in [0, 1] (arousal, dominance).

## 3. Summary record (Arch only, copied into the bundle): `summaries/*.jsonl`

Written by gpt-5.6-luna. Every claim cites slice ids.

```json
{"id":"agent-day:gpt-5-5:467","scope":"agent","key":"gpt-5-5","day":467,"date":"2026-07-10",
 "text":"Ran a playtest call for Signal Garden [1] and fixed the static path [2].",
 "cites":[{"n":1,"ref":"chat:0000447e-…"},{"n":2,"ref":"ev:401584:summary"}],
 "model":"gpt-5.6-luna"}
```

`scope` is `agent` (per agent per day), `room` (per room per day) or `village` (per day; AI Digest's own `daily` summary when it exists, cited as `sum:`). Markers `[n]` in `text` map to `cites[].n`. Summaries name no human; humans are "a human".

## 4. The bundle (served): `data/` beside the app

The app loads everything from the relative path `./data/`. One start file, `data/bundle.json`, holds every small table and view. Per-agent and per-day files load on demand.

```
data/
  bundle.json                      loaded at start (under 3 MB)
  summaries/agent/<agent>.json     on demand: one summary per active day
  summaries/room/<room>.json       on demand
  summaries/village/<day>.json     on demand: one full AI Digest daily text
  entities/<slug>.json             on demand: one entity's agents and timeline (slug = id without "e:")
  memory/<agent>.json              on demand: memory drift for one agent
  sources/<day>.json               on demand: cited snippets for one day
  book.json                        loaded when the book opens (under 1 MB)
  adherence/<agent>.json           on demand
  talk/<agent>.json                on demand
```

`bundle.json` top level:

```json
{"manifest":{…},"agents":[…],"rooms":[…],"days":[…],"goals":[…],"entities":[…],
 "summaries":{"village":[…]},
 "views":{"timeline":{…},"interactions":{…},"mood":{…}}}
```

Each key below is one part of `bundle.json`, except where a heading names an on-demand file.

### manifest
```json
{"schema":"swarm-bundle/1.3","generated":"2026-10-04T12:00:00Z","dataset":"aidigestorg/ai-village@838b4150",
 "cite":"Data: AI Digest / AI Village (theaidigest.org/village)",
 "range":{"first_day":1,"last_day":535,"first_date":"2025-04-02","last_date":"2026-09-20"},
 "counts":{"agents":46,"rooms":16,"slice_rows":0,"slice_tokens":0,"mentions":0,"entities":0,"summaries":0},
 "tagger":{"name":"oneiroNER","checkpoint":"rd16c-ch:0b5ff938e12463f8+vad:acbf6b43c6662813","labels":["PERSON","PLACE","ORG","DATE","EVENT","RELATIONSHIP_REF","EMOTION","MONEY","PRODUCT"],"heads":["ner","mood"]},
 "summarizer":"gpt-5.6-luna"}
```

### agents
```json
[{"id":"gpt-5-5","name":"GPT-5.5","model":"gpt-5.5","family":"openai","participating":true,
  "first_day":390,"last_day":535,
  "goals":[{"name":"…","short":"…","description":"…","start":"…","end":null}],
  "stats":{"messages":0,"tokens":0,"days_active":0,"sessions":0,"memories":0},
  "top_entities":[{"entity":"e:…","count":0}]}]
```
`family` is one of `anthropic`, `openai`, `google`, `xai`, `deepseek`, `moonshot`, `zai`, `meta`, `other`. The pseudo-agent `human` appears here with `family: "human"`.

### rooms
`[{"id":"general","name":"general","deleted":null,"first_day":0,"last_day":0,"messages":0}]`

### days
```json
[{"day":467,"date":"2026-07-10","village_goal":"…","active_agents":["gpt-5-5"],
  "counts":{"chat":0,"sessions":0,"thinking":0,"memories":0,"mentions":0},
  "top_entities":[{"entity":"e:…","count":0}],"summary":"village-day:467"}]
```

### goals
`[{"goal":"…","start":"…","end":"…","first_day":0,"last_day":0}]` (village goals in order).

### entities: provisional entities from oneiroNER mentions
In `bundle.json` (all top 2,000, light records):
```json
[{"id":"e:signal-garden","text":"Signal Garden","label":"PRODUCT","aliases":["signal garden"],
  "count":0,"n_agents":0,"first_day":0,"last_day":0,
  "examples":["chat:0000447e-…"],"example_days":[467]}]
```
On demand `entities/<slug>.json` (slug = `id` without `e:`):
```json
{"id":"e:signal-garden","agents":{"gpt-5-5":0},"timeline":[[467,3],[468,1]]}
```
An entity groups mentions with the same label and the same folded surface text. These are unconfirmed mentions, never facts. A mention of an agent's display name or slug becomes an agent entity: one per label and agent, with `"agent": "<agent id>"` on the light record (the tagger may call the same agent PERSON, ORG or PRODUCT); group by `agent` to show one row per agent. Spans are snapped to whole words at build time (`manifest.tagger.snap = "word-v1"`); raw tags stay unchanged on Arch. `example_days[i]` is the day of `examples[i]`, so the app knows which `sources/<day>.json` to load.

### summaries
`summaries.village` in `bundle.json` holds previews (about 300 characters, `"truncated": true`); the full AI Digest text for a day is in the on-demand `summaries/village/<day>.json` (one record). Plus the on-demand files `summaries/agent/<agent>.json` and `summaries/room/<room>.json`. Each is an array of summary records (section 3), one per active day.

### optional `findings.json`
`data/findings.json` (may be absent): `[{"title":"…","body":"… [1]","cites":[{"n":1,"ref":"chat:…","day":467}],"view":"#agents/gpt-5-5"}]`: a few cited findings for the overview. Each cite carries the `day` of its ref; the ref is in `sources/<day>.json`.

### on demand `sources/<day>.json`: the cited snippets for one day
```json
{"chat:0000447e-…":{"time":"…","agent":"gpt-5-5","room":"general","kind":"chat",
  "text":"first 600 characters…","spans":[[56,69,"PRODUCT"]]}}
```
Only rows that a summary or entity cites. Text is cut at 600 characters. Never the full dataset.

### views.timeline
`{"days":[467,468],"series":{"chat":{"gpt-5-5":[3,4]},"mentions":{"PRODUCT":[10,12]}}}`; each series is aligned to `days`.

### views.interactions: who talks to whom
```json
{"nodes":[{"id":"gpt-5-5","messages":0}],
 "edges":[{"from":"gpt-5-5","to":"claude-opus-4-5","w":0,"kinds":{"mention":0,"reply":0},"by_day":[[467,2]]}]}
```
`mention` = the speaker's text names another agent (an oneiroNER span or a name match). `reply` = the next message in the same room within 10 minutes from a different agent.

### on demand `memory/<agent>.json`: what the agent remembers, and drift
```json
{"agent":"gpt-5-5","days":[{"day":467,"ref":"mem:0000287e-…","chars":0,"entities":["e:…"],
  "added":["e:…"],"dropped":["e:…"],"jaccard_prev":0.81,"excerpt":"first 400 characters…"}]}
```
`jaccard_prev` = entity-set overlap with the previous kept memory. Drift = 1 − `jaccard_prev`.

### the book (schema v1.3)
```
data/book.json                 loaded when the book opens (under 1 MB)
data/adherence/<agent>.json    on demand
data/talk/<agent>.json         on demand
```
`book.json`:
```json
{"schema":"swarm-book/1.1","generated":"…","model":"gpt-5.6-luna",
 "chapters":[{"n":1,"goal":"…","title":"…","first_day":1,"last_day":9,"first_date":"2025-04-02","last_date":"2025-04-10",
   "days":[1,2,3],"agents":["claude-3-7-sonnet"],
   "stats":{"days":9,"chat":0,"sessions":0,"memories":0,"human_chat":0,"agents":0},
   "text":"… [1] … [2]","cites":[{"n":1,"ref":"chat:…","day":3}],
   "hook":"…","beats":[{"text":"… [1][2].","cites":[1,2]}],"figure":{"value":"$2,731","label":"…","cites":[2]}}],
 "days":[{"day":1,"date":"2025-04-02","chapter":1,"text":"… [1]","cites":[{"n":1,"ref":"chat:…","day":1}]}]}
```
`adherence/<agent>.json`: `{"agent":"gpt-5-5","days":[{"day":467,"chapter":40,"score":"on","note":"…"}]}` (score may be null).
`talk/<agent>.json`: `{"agent":"gpt-5-5","days":[{"day":467,"partners":[["o3",12],["human",3]]}]}`.
Every `text` and `note` passes the same redaction and validator rules as summaries (names list, extra ids, handles, URLs, the `[human]` rules); entity ids are not used here. The validator must also check that every cite's `ref` exists in `sources/<day>.json` and that every `[n]` in a text has a cite.

Chapter scroll fields (swarm-book/1.1, all three optional: absent or null means the page falls back to the title, the paragraph's sentences and no figure). Written by `gpt-5.6-luna` from the chapter paragraph (pipeline/book `hooks`) and checked by the validator beside the served `text` and `cites`:
- `hook`: one line of 6 to 14 words, no square brackets (no `[n]`, no chip), not a question; every number in it is a number `text` states.
- `beats`: 3 to 6 objects `{"text","cites"}` that retell `text` in order. Each `text` has at most 24 words before its markers and ends with them (`… [3][4].`); every `[n]` is a number of the chapter's `cites`; `cites` lists exactly the markers of `text`, each once, in order of first use; no other square brackets. The day a beat is about is the `day` of its cites.
- `figure`: `{"value","label","cites"}` or null. `value` is at most 8 characters, contains a digit, and every number in it is a number `text` states in digits ("$2,731", "32", "11 days", "7 of 9"); `label` is 1 to 7 words with no closing full stop; `cites` is non-empty and every number is in the chapter's `cites`.
These strings pass the same redaction and validator rules as `text` (names list, extra ids, handles, URLs, untagged common-word names). A field that a redaction would break is served as null.

One chapter per village goal: a day joins the chapter whose `goal` equals its `village_goal`; a day without one joins the chapter of the day before it. Every day of `days[]` is in `book.json` `days` and in exactly one chapter. `title` and `text` are null when no text is written yet. `score` is `on` (most of the day's work served the goal), `partly`, `off` (it worked on something else) or `idle` (stuck, looping, errors). `partners` are the top 5 by interaction weight that day, both directions summed.

### views.mood (only when the mood head ran)
`{"days":[467],"agents":{"gpt-5-5":{"v":[0.4],"a":[0.5],"d":[0.6]}}}`: daily means of turn mood.

## 5. Sizes

`bundle.json` under 3 MB. Any on-demand file under 2 MB. All of `data/` under 100 MB. Snippets stay at 600 characters.
