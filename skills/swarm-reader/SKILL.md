---
name: swarm-reader
description: Turn any archive of multi-agent transcripts into cited day summaries, entity and mood tags, who-talked-with-whom, memory drift, goal calls, a scrollable book and eight overseer answers, with the privacy sweeps run before anything is served.
---

# swarm-reader

Use this skill when you hold a large archive of messages written by many agents and a person needs to understand what the group did. The pipeline lives in this repo under `pipeline/`. Each stage has its own README with the exact flags; this file tells you the order, the contracts and the rules.

## 1. What it does

You give it an archive of agent messages. Each message has an agent, a time, a room and a text. Memory notes and session logs are optional and make the output richer.

It gives back:

- **Day summaries** per agent, per room and for the whole village. Every sentence cites numbered source lines.
- **Entity tags**: people, places, organisations, dates, events, relationships, emotions, money and products, as character spans.
- **Mood per turn**: valence, arousal and dominance read from each text.
- **Who talked with whom**: mentions and replies as a weighted graph per day.
- **Memory drift**: which remembered entities each agent kept, added and dropped from one day to the next.
- **Goal calls**: each agent-day scored on, partly, off or idle against the group goal, with a short note.
- **A book** a person can scroll: one chapter per goal, one cited paragraph per day, hooks, beats, talking pairs and stage directions for the pictures.
- **Eight overseer questions** answered for the whole run and for each chapter, every answer cited.

All of it lands in one static, redacted, validated bundle that a small server and a static web app read.

## 2. Inputs and the slice format

The slicer is the only stage that knows your source format. Everything after it reads the slice. To run on a new archive, write a slicer for your tables that emits the slice records below; the rest of the pipeline runs unchanged.

One JSON object per line in `slice/slice-<kind>.jsonl`, sorted by time (schema section 1 in `docs/schema.md`):

| field | meaning |
|---|---|
| `id` | unique ref, `<kind>:<source id>`; every citation points at it |
| `time`, `day`, `date` | UTC ISO time with `Z`, the run's day number, the calendar date |
| `agent` | agent slug, or `human` |
| `speaker` | `agent` or `human` |
| `room` | room name for chat, else null |
| `session` | session id when the text belongs to one, else null |
| `kind` | `chat`, `session_goal`, `session_summary`, `next_goal`, `history_query`, `outreach`, `thinking`, `cc_text`, `cc_thinking`, `memory`, `digest_summary` |
| `text`, `ntok` | the text and its token count under the tagger's tokenizer |

The slicer also writes `meta/agents.json`, `meta/rooms.json`, `meta/days.json`, `meta/village_goals.json` and `meta/agent_goals.json`. Pool every human speaker into `human` here and drop user ids, viewer names and recipients. Dedupe per kind on the folded text. Never read screenshots or raw tool output.

## 3. The stages

`$W` is the work area, outside the repo. `$R` is this repo. Every LLM step stops hard if the returned model id is not the one it asked for.

| stage | command | reads | writes |
|---|---|---|---|
| slice | `python $R/pipeline/slice/slicer.py --data <archive> --out $W` | the raw archive | `$W/slice/`, `$W/meta/`, `slice/STATS.json` |
| tag | your tagger (section 4) | `slice/slice-<kind>.jsonl`, the summaries | `tags/tags-<kind>.jsonl`, `tags/tags-summaries.jsonl`, `tags/STATUS.json` |
| summarize | `python $R/pipeline/summarize/summarize.py run --work $W` (`estimate` first, `--limit 5` to pilot, `audit-existing` after) | slice, `meta/` | `summaries/room.jsonl`, `summaries/agent.jsonl` |
| bundle | `python $R/pipeline/bundle/build_bundle.py` | `meta/`, slice, tags, summaries, `book/` | a new generation under `bundle/gen/`, then `bundle/data` |
| validate | `python $R/pipeline/bundle/validate_bundle.py` | `bundle/data/`, the name and id lists | exit 1 on any error |
| book: days | `python $R/pipeline/book/book.py days --parallel 8` | the built bundle | `book/days.jsonl`; then `gaps` for empty days |
| book: chapters | `book.py chapters --parallel 8` | day paragraphs | `book/chapters.jsonl` |
| book: hooks | `book.py hooks --parallel 8` | the served chapter text | `book/hooks.jsonl` (hook, beats, figure) |
| book: pairs | `book.py pairs --parallel 8` | interaction edges, chat rows | `book/pairs.jsonl` (one sentence per talking pair) |
| book: stage | `book.py stage --parallel 8` | chapter text and beats | `book/stage.jsonl` (props and moments, enums in the stage catalogue) |
| book: adherence | `book.py adherence --parallel 4` | agent-day summaries | `book/adherence.jsonl` |
| book: rephrase | `book.py rephrase` | every book string, the redaction lists | `book/rewrites.jsonl` |
| bundle again | `build_bundle.py`, then `validate_bundle.py` | all of the above | `book.json`, `pairs.json`, `adherence/`, `talk/` in the bundle |
| swap | done by the builder | a validated generation | `bundle/data` repointed with one rename; the old generation is kept for roll-back |
| serve | `python $R/pipeline/serve/serve.py` | `web-dist/`, `bundle/data/` | HTTP on `127.0.0.1:8790`, `/api/health` |
| web | `cd $R/web && bun install && bun run build` | `./data/` at run time | `web/dist/`, copied to `web-dist/` |

The bundler runs twice because the book steps read the built bundle. Every cache is append-only JSONL; a rerun does only what is missing and the last line per key wins.

## 4. The tagger is pluggable

The pipeline expects one tag record per slice row, same `id`, same order, in `tags/tags-<kind>.jsonl` (schema section 2, `pipeline/tag/README.md`):

```json
{"id": "chat:…", "model": "<model id>", "decode": "v1",
 "spans": [{"s": 0, "e": 11, "label": "PERSON", "text": "Agent Alpha", "p": 0.97}],
 "mood": {"v": 0.12, "a": 0.55, "d": 0.51}}
```

- `s` and `e` are codepoint offsets into the slice `text`, and `text` must equal `slice_text[s:e]`.
- `label` is one of `PERSON`, `PLACE`, `ORG`, `DATE`, `EVENT`, `RELATIONSHIP_REF`, `EMOTION`, `MONEY`, `PRODUCT`. `sub` is optional.
- `p` is a confidence report; spans are not filtered by it.
- `mood.v` is in [-1, 1]; `mood.a` and `mood.d` are in [0, 1].

**Our option.** oneiroNER: a multilingual span NER model with a character boundary head, and a VAD head on the same encoder. It is our own model and is not in this repo.

**Another option.** Any span model works: a token-classification model such as GLiNER or spaCy for the spans, and any sentiment or VAD regressor for mood. Map its labels onto the nine above, drop labels you cannot map, and rescale its mood scores into the ranges above.

**Register.** Dialogue tags better in the conversation register. Group chat rows by room in time order, pack them into long windows, and prefix each row with a role only (`A: ` for a human, `B: ` for an agent, never a name). Map spans back to their row and drop any span that crosses two rows or touches a prefix. Read mood from the row alone.

**Before you publish tags,** check each file against its slice: same ids, same order, same count, every span text equal to its slice text, every mood value in range.

## 5. The privacy procedure

Nothing served may identify a human. Agent names are model names and stay. Over-redaction is the chosen failure mode. Run every step below before the first public byte.

1. **Pool the humans at the slice.** Every human speaker is `human`. Drop user ids, viewer names and outreach recipients.
2. **Build the lists.** Run `human_names.py` for the dataset's own human name list. Run `human_ids.py candidates`, `classify`, `scan`, `hits` and `write --review` for usernames and handles. A person reviews every classifier hit. Fill `meta/context_handles.json` and `meta/extra_human_rules.json` (`{}` or `[]` when empty). These lists are never served or committed.
3. **Guard the summaries.** The summarizer lists the agent names as the only allowed names, then a second call looks for human names and forces a rewrite. Run `summarize.py audit-existing` over anything written before the guard.
4. **Let the builder cut the rule classes.** Listed names, non-agent PERSON spans, extra ids, @handles that are not an agent or a public organisation, capitalised names next to a `[human]` chip, names after a title or in a salutation, emails (also partial, percent-encoded or cut), uuids, phone numbers, order and tracking ids, chess game ids, profile accounts in URLs and long URL tokens. They become `[human]`, `[email]` or `[id]`. A final sweep runs the filter over every served string again.
5. **Red-team the bundle.** An LLM agent with tools hunts the served bundle for identifiers and writes leak rows. Fold them in with `human_ids.py redteam --leaks <jsonl> --review <file>`, rebuild, and hunt again until every high and medium row is cut. Take the public link down while a round is open.
6. **Run the privacy-filter sweep.** `pipeline/pf/pf.py` runs openai/privacy-filter over every served string: `collect`, `run`, `filter`, `classify`, `review`, `write`. Its hits are candidates; a person accepts or rejects each, and accepted ones join the lists.
7. **Rephrase the model's own text.** `book.py rephrase` rewords any book sentence the filter would cut, and keeps the rewording only if the filter then cuts nothing and the cites are unchanged.
8. **Validate.** `validate_bundle.py` reads the full lists with the builder's matcher over every string, key and file path, and checks every citation. The builder swaps a generation live only when it passes.
9. **Scan the snapshot.** Before the repo or a bundle copy goes public, scan every committed and published byte against the name and id lists. Look for dataset rows, quotes, names in fixtures and secrets. Fixtures must be synthetic and obviously fake.

## 6. The questions battery

`docs/questions/make_questions.py` asks eight questions of the served bundle, one call per chapter and one per question for the whole run. It writes `web/public/questions/questions.json` and `docs/QUESTIONS.md`.

The eight: who the others addressed most and whether the hubs moved; when an agent worked away from the goal; what the humans stepped in for; what several agents reported at once and whether it held; what spread through the group; what the swarm reached outside itself; what changed when a model was swapped into a seat; what the group did that no goal asked for.

To add a question, append one tuple to the `QUESTIONS` list: a short key, the question as a reader sees it, and the instruction the model gets. Say which bundle material it should use. Every answer must cite refs the served book already cites, or say "nothing recorded". Rerun the script; cached answers for the old questions are kept.

## 7. The output

The served bundle is `bundle/data/`. `docs/schema.md` section 4 is the contract.

| file | holds |
|---|---|
| `bundle.json` | manifest, agents, rooms, days, goals, top entities, timeline, interactions, mood |
| `summaries/agent/`, `summaries/room/`, `summaries/village/` | cited day summaries |
| `sources/<day>.json` | the snippet behind every cited ref |
| `entities/<slug>.json` | per-entity counts per agent and per day |
| `memory/<agent>.json` | kept memory per day, added and dropped entities, overlap with the day before |
| `talk/<agent>.json` | who the agent addressed and who addressed it |
| `adherence/<agent>.json` | goal calls per agent-day |
| `book.json`, `pairs.json` | chapters, days, hooks, beats, figures, stage directions, talking pairs |
| `findings.json` | optional cited findings |

Caps: `bundle.json` under 3 MB, any on-demand file under 2 MB, the whole bundle under 100 MB.

## 8. Limits and what not to do

- **Do not train** or fine-tune any model on the archive, and keep it out of every training mix.
- **Do not re-identify** anyone. Never keep a human id, handle or name in any output, log you publish, test or README.
- **Keep the archive local.** Only the redacted bundle is served. The LLM calls the summary and book steps make are the only traffic that carries raw text.
- **Tags are a model's reading,** not facts. Mood is read from words. Summaries and the book are written by a model and can be wrong, which is why every line cites.
- **Goal calls judge summaries,** not the agents' raw work. Memory drift counts entity turnover, not understanding. Replies are a heuristic: the next speaker in the room within ten minutes.
- **Never edit `docs/schema.md` from a stage.** A format change goes to whoever owns the contract.
- **Never serve a bundle that failed validation,** and never skip a privacy step because an earlier one looked clean.
