---
name: swarm-reader
description: Turn any archive of multi-agent transcripts into cited day summaries, entity and mood tags, who-talked-with-whom, memory drift, goal calls, a scrollable book and eight overseer answers, with the privacy sweeps run before anything is served. Runs on a laptop with Python, bun and any OpenAI-compatible endpoint; a mock mode runs it with no model at all.
---

# swarm-reader

Use this skill when you hold a large archive of messages written by many agents and a person needs to understand what the group did. The pipeline lives in this repo under `pipeline/`; `skills/swarm-reader/RUNBOOK.md` has every command in order. Each stage has its own README with all its flags. This file tells you the order, the contracts and the rules.

**Start here.** Run `scripts/smoke.sh`. In about a minute (a few on a cold machine) it builds a venv, writes an invented archive, runs every stage with a mock model and the reference tagger, validates the bundle, builds and serves the site, and checks that no planted identifier was served. If it ends with `SMOKE OK`, your machine can run the real thing. Then follow RUNBOOK.md section 2 with your own archive.

Where to get it: the public copy of this repo, `git clone https://github.com/oneiron-dev/swarm-memory`.

What you need: Python 3.12, bun, about 2.5 GB of disk for the venv, its download cache and the tagger model, and for real summaries an OpenAI-compatible endpoint (`SWARM_LLM_BASE_URL`, `SWARM_LLM_MODEL`, `SWARM_LLM_API_KEY`). The tagger's GLiNER model goes to the Hugging Face cache (`HF_HOME`) and its mood lexicon to `~/.cache/swarm-reader` (`SWARM_CACHE` moves it). The optional privacy-filter sweep needs about 2.8 GB more for its checkpoint. No GPU. Nothing private: no harness, no checkpoint, no lists of ours.

## 1. What it does

You give it an archive of agent messages. Each message has an agent, a time, a room and a text. Memory notes, session logs, goals and daily logs are optional and make the output richer.

It gives back:

- **Day summaries** per agent, per room and for the whole group. Every sentence cites numbered source lines.
- **Entity tags**: people, places, organisations, dates, events, relationships, emotions, money and products, as character spans.
- **Mood per turn**: valence, arousal and dominance read from each text.
- **Who talked with whom**: mentions and replies as a weighted graph per day.
- **Memory drift**: which remembered entities each agent kept, added and dropped from one day to the next.
- **Goal calls**: each agent-day scored on, partly, off or idle against the group goal, with a short note.
- **A book** a person can scroll: one chapter per goal, one cited paragraph per day, hooks, beats, talking pairs and stage directions for the pictures.
- **Eight overseer questions** answered for the whole run and for each chapter, every answer cited.

All of it lands in one static, redacted, validated bundle that a small server and a static web app read.

## 2. Inputs and the slice format

Give the archive as `archive.jsonl`, one JSON object per text unit:

```json
{"id": "m-0042", "time": "2031-03-02T09:11:00Z", "agent": "Alder-7", "room": "general", "kind": "chat",
 "text": "Birch 2.1, can you check the seed list?", "reply_to": null}
```

- `kind` is `chat`, `memory`, `session` or `event`. `subkind` refines it: a session row is a `goal`, `summary` (default), `next_goal`, `thinking` or `code`; an event row is a `goal` (a new group goal starts, the text is the goal), `agent_goal`, `daily_summary` (a written log of the day), `outreach` or `query`.
- Every human is `"agent": "human"`. A per-human id may sit in `human_id` (scrubbed from every text, never written out) and a display name in `human_name` (read only into the redaction list).
- A group-level event row (`goal`, `daily_summary`, or any event subkind the slice skips) belongs to the group: give it `"agent": "human"` or any other value, such as `"system"`. Such a row never becomes an agent.
- A time with an offset (`+09:00`) is converted to UTC. A day is a UTC date, and it starts at its first row, so a goal that starts at 09:00 starts its chapter that day.
- `model` on an agent's row gives its model id; `session` groups session text. The whole table and its mapping are in `pipeline/slice/README.md`.

`pipeline/slice/from_jsonl.py` turns it into the slice. The AI Village slicer (`slicer.py`) is the other front door, for that dataset's tables. Both write the same files with the same code; everything after the slice is the same.

The slice is one JSON object per line in `slice/slice-<kind>.jsonl`, sorted by time (schema section 1 in `docs/schema.md`):

| field | meaning |
|---|---|
| `id` | unique ref, `<src>:<source id>`; every citation points at it |
| `time`, `day`, `date` | UTC ISO time with `Z`, the run's day number (day 1 = the first date), the calendar date |
| `agent` | agent slug, or `human` |
| `speaker` | `agent` or `human` |
| `room` | room slug for chat, else null |
| `session` | session id when the text belongs to one, else null |
| `kind` | `chat`, `session_goal`, `session_summary`, `next_goal`, `history_query`, `outreach`, `thinking`, `cc_text`, `cc_thinking`, `memory`, `digest_summary` |
| `text`, `ntok` | the text and its token count |

Beside it: `meta/agents.json`, `rooms.json`, `days.json`, `village_goals.json`, `agent_goals.json`, `summaries_index.json`, `dataset.json`. The slice pools every human into `human`, drops ids, dedupes per kind on the folded text and keeps the last memory of each agent per day.

## 3. The stages

`$W` is the work area, outside the repo; every stage defaults to `$SWARM_WORK`. `$R` is this repo. One setup line installs everything: `scripts/setup.sh --tagger`.

Every model step takes the same client flags (`pipeline/common/README.md`): `--endpoint <base URL> --model <id>` or the `SWARM_LLM_*` variables for any OpenAI-compatible server, `--prime-agent <path>` or `PRIME_AGENT` for our own harness, or `--mock` (`export SWARM_LLM_MOCK=1` once for a whole dry run) for templated, cited placeholders with no model. Only an explicit choice picks a backend. Nothing is found on PATH, and with nothing set a model step stops with `no model backend configured` and the line that sets one. Every model step stops hard (exit 3) when a reply names another model than the one asked for.

| stage | command | reads | writes |
|---|---|---|---|
| slice | `python $R/pipeline/slice/from_jsonl.py archive.jsonl --name <name> --cite <credit>` | the archive | `$W/slice/`, `$W/meta/` |
| tag | `python $R/pipeline/tag/run_tagger.py --adapter gliner_vad` (section 4) | the slice | `tags/tags-<kind>.jsonl`, `tags/STATUS.json`, `tags/LABELS.json` |
| summarize | `python $R/pipeline/summarize/summarize.py run` (`estimate` first, `--limit 5` and `pilot-md` to pilot) | slice, `meta/` | `summaries/room.jsonl`, `summaries/agent.jsonl` |
| tag summaries | `run_tagger.py --adapter gliner_vad --summaries` | the summaries | `tags/tags-summaries.jsonl` |
| names | `python $R/pipeline/bundle/human_names.py --archive archive.jsonl` | `human_name` fields, `--names` file | `meta/human_names.json` |
| bundle | `python $R/pipeline/bundle/build_bundle.py` | `meta/`, the lists, slice, tags, summaries, `book/`, `questions/` | a new generation under `bundle/gen/`, then `bundle/data` |
| validate | `python $R/pipeline/bundle/validate_bundle.py` | `bundle/data/`, the lists | exit 1 on any error |
| handle sweep | `human_ids.py candidates`, `classify`, `scan`, `hits`, `write --review` | the built bundle | `human_ids/`, `meta/extra_human_ids.json` |
| book: days | `python $R/pipeline/book/book.py days --parallel 8` | the built bundle | `book/days.jsonl`; then `gaps` for empty days |
| book: chapters | `book.py chapters --parallel 8` | day paragraphs | `book/chapters.jsonl` |
| book: hooks | `book.py hooks --parallel 8` | the served chapter text | `book/hooks.jsonl` (hook, beats, figure) |
| book: pairs | `book.py pairs --parallel 8` | interaction edges, chat rows | `book/pairs.jsonl` (one sentence per talking pair) |
| book: stage | `book.py stage --parallel 8` | chapter text and beats | `book/stage.jsonl` (props and moments from the stage catalogue) |
| book: adherence | `book.py adherence --parallel 4` | agent-day summaries | `book/adherence.jsonl` |
| book: rephrase | `book.py rephrase` | every book string, the lists | `book/rewrites.jsonl` |
| bundle again | `build_bundle.py`, then `validate_bundle.py` | all of the above | `book.json`, `pairs.json`, `adherence/`, `talk/` in the bundle |
| questions | `python $R/docs/questions/make_questions.py` | the built bundle | `questions/questions.json`, then `data/questions.json` on the next build |
| web | `cd $R/web && bun install && bun run build`, then copy `web/dist/` to `$W/web-dist/` | `./data/` at run time | the static app |
| serve | `python $R/pipeline/serve/serve.py --check`, then `serve.py` | `$W/web-dist/`, `$W/bundle/data/` | HTTP on `127.0.0.1:8790`; `/api/health` says `"ok": true` |

The bundler runs more than once because the book and the questions read the built bundle. The builder writes a new generation, validates it, and swaps it in with one rename only when it passes. Every cache is append-only JSONL; a rerun does only what is missing and the last line per key wins.

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
- `mood` is optional; `mood.v` is in [-1, 1], `mood.a` and `mood.d` are in [0, 1].

`run_tagger.py --adapter <name>` drives an adapter (`pipeline/tag/adapters/`) and checks every file against its slice before it lands: same ids, same order, same count, every span text equal to its slice text, every label one of the nine, every mood value in range. `run_tagger.py --check` checks files a tagger wrote on its own.

- **`gliner_vad`, the reference.** Public and CPU-only. GLiNER small (`urchade/gliner_small-v2.1`, Apache-2.0) finds the spans; the NRC VAD Lexicon v2.1 (research and educational use; downloaded on first use, never committed) gives the mood. About 105 rows and 9,500 characters a second on a laptop CPU. It tags a name after "I am", "my name is", "call me" or "Dear" as PERSON and turns an ORG or PRODUCT span that also scores as a person into PERSON, because GLiNER calls some people products.
- **`oneironer`, ours.** A multilingual span NER model with a character boundary head and a VAD head on the same encoder. It is not public; the adapter runs it through `ONEIRONER_TAG_CMD` when you have it. The real run used it.
- **Your own.** Any span model and any mood regressor: write a class with `load(args)` and `tag(rows)` returning `{"spans", "mood"}` per row, and add it to `ADAPTERS` in `adapters/__init__.py`. Map its labels onto the nine, drop labels you cannot map, rescale its mood into the ranges above.

**Register.** Dialogue tags better in the conversation register. Group chat rows by room in time order, pack them into long windows, and prefix each row with a role only (`A: ` for a human, `B: ` for an agent, never a name). Map spans back to their row and drop any span that crosses two rows or touches a prefix. Read mood from the row alone.

## 5. The privacy procedure

Nothing served may identify a human. Agent names are model names and stay. Over-redaction is the chosen failure mode. Run every step below before the first public byte.

1. **Pool the humans at the slice.** Every human speaker is `human`. `human_id` values are scrubbed from every text and never written out.
2. **Build the lists.** They live in one directory, `--lists <dir>` on every script (default `$W/meta`), and are never served or committed. `human_names.py` fills `human_names.json` from the archive's `human_name` fields and from a `--names` file a person writes. `human_ids.py candidates`, `classify`, `scan` and `hits` find usernames and handles; `hits` refuses until classify has finished every batch, and writes `review-template.txt`. A person decides every hit and `write --review` saves the accepted ones in `extra_human_ids.json`. A missing list is created empty.
   - **What the rules cut with empty lists:** every human pooled, emails (also percent-encoded or cut), `@handles` that are not an agent or a public account, phone numbers, order and tracking ids, uuids, profile accounts in URLs, long URL tokens, names in a salutation ("Dear X Y") or after a title ("Dr. X"), and every non-agent PERSON span of the tagger.
   - **What a person must still review:** a name written in plain prose ("ask jo about it"), a bare username with no handle shape, and any person the tagger called ORG or PRODUCT. The smoke run's invented viewer introduces herself by name; the tagger's cue rule and the `human_name` field each catch it, but an archive without either needs a person to read the hits.
3. **Guard the summaries.** The summarizer lists the agent names as the only allowed names, then a second call looks for human names and forces a rewrite. Run `summarize.py audit-existing` over anything written before the guard.
4. **Let the builder cut the rule classes.** Listed names, non-agent PERSON spans, extra ids, the rule classes above. They become `[human]`, `[email]` or `[id]`. A final sweep runs the filter over every served string again.
5. **Red-team the bundle.** An agent with file tools hunts the served bundle and writes leak rows to `$W/redteam/leaks.jsonl` (`mkdir -p $W/redteam` first); fold them in with `human_ids.py redteam --leaks <jsonl> --review <file>` (an empty review file accepts every high and medium row), rebuild, and hunt again until a round finds nothing high or medium. A clean round is an empty `leaks.jsonl`. Keep the site private while a round is open. A prompt that works:

   > You are a privacy red-team. Read every JSON file under `<W>/bundle/data/` (the served bundle of a multi-agent archive). Agent names are model names and are allowed; `[human]`, `[email]` and `[id]` are cuts already made. Find every string that could identify a real human: a name, a nickname, a username or handle, an email or part of one, a phone number, a personal URL or account, an order or account number, or a detail that singles one person out. For each one write one JSON line to `<W>/redteam/leaks.jsonl`: `{"file": "data/<path>", "ref_or_path": "<JSON path like $[\"chat:x\"][\"text\"]>", "identifier": "<the exact string>", "kind": "name|handle|email|url|phone|other", "context": "<a few words around it>", "confidence": "high|medium|low"}`. Do not copy any other text. End with the number of rows by confidence.

6. **Run the privacy-filter sweep** (Apple Silicon). `pipeline/pf/pf.py` runs openai/privacy-filter over every served string: `collect`, `run`, `filter`, `classify`, `review`, `write`. `review` refuses until every candidate was classified. A person accepts or rejects each hit; `write` adds the accepted ids to `extra_human_ids.json`. Elsewhere, run the filter with its own PyTorch tooling and start at `filter`, or skip the sweep and say so.
7. **Rephrase the model's own text.** `book.py rephrase` rewords any book sentence the filter would cut, and keeps the rewording only if the filter then cuts nothing and the cites are unchanged. Run it again whenever the lists change.
8. **Validate.** `validate_bundle.py` reads the full lists with the builder's matcher over every string, key and file path, and checks every citation. The builder swaps a generation live only when it passes.
9. **Scan the snapshot.** Before the repo or a bundle copy goes public, scan every published byte against the name and id lists. Look for archive rows, quotes, names in fixtures and secrets. Fixtures must be invented and obviously fake. `scripts/smoke.sh` ends with such a scan for the identifiers it planted.

## 6. The questions battery

`docs/questions/make_questions.py` asks eight questions of the served bundle, one call per chapter and one per question for the whole run. It writes `$W/questions/questions.json` and `QUESTIONS.md` beside it; the next build serves the JSON as `data/questions.json`, where the site's questions page reads it. Nothing from your run is written into the repo.

The eight: who the others addressed most and whether the hubs moved; when an agent worked away from the goal; what the humans stepped in for; what several agents reported at once and whether it held; what spread through the group; what the swarm reached outside itself; what changed when a model was swapped into a seat; what the group did that no goal asked for.

To add a question, append one tuple to the `QUESTIONS` list: a short key, the question as a reader sees it, and the instruction the model gets. Say which bundle material it should use. Every answer must cite refs the served book already cites, or say "nothing recorded". Rerun the script; cached answers for the old questions are kept. `--effort` and `--year-effort` send a reasoning effort to an endpoint.

## 7. The output

The served bundle is `bundle/data/`. `docs/schema.md` section 4 is the contract.

| file | holds |
|---|---|
| `bundle.json` | manifest (dataset, credit line, tagger, summarizer), agents, rooms, days, goals, top entities, timeline, interactions, mood |
| `summaries/agent/`, `summaries/room/`, `summaries/village/` | cited day summaries |
| `sources/<day>.json` | the snippet behind every cited ref |
| `entities/<slug>.json` | per-entity counts per agent and per day |
| `memory/<agent>.json` | kept memory per day, added and dropped entities, overlap with the day before |
| `talk/<agent>.json` | who the agent addressed and who addressed it |
| `adherence/<agent>.json` | goal calls per agent-day |
| `book.json`, `pairs.json` | chapters, days, hooks, beats, figures, stage directions, talking pairs |
| `questions.json` | the eight answers, per chapter and for the whole run |
| `findings.json` | optional cited findings |

Caps: `bundle.json` under 3 MB, any on-demand file under 2 MB, the whole bundle under 100 MB.

## 8. Limits and what not to do

- **Do not train** or fine-tune any model on an archive whose terms forbid it, and keep it out of every training mix.
- **Do not re-identify** anyone. Never keep a human id, handle or name in any output, log you publish, test or README.
- **Keep the archive local.** Only the redacted bundle is served. The model calls are the only traffic that carries raw text: pick an endpoint whose terms you accept for that text, or a local one.
- **The mock is a placeholder.** `--mock` text says so ("Placeholder text, written without a model") and carries `"model": "mock"`; it shows the pipeline running, never a reading of the archive.
- **Tags are a model's reading,** not facts. Mood is read from words. Summaries and the book are written by a model and can be wrong, which is why every line cites.
- **Goal calls judge summaries,** not the agents' raw work. Memory drift counts entity turnover, not understanding. Replies are a heuristic: the next speaker in the room within ten minutes; `reply_to` is read but not used yet.
- **The prompts and the site speak of a village.** The summary, book and question prompts describe the archive as "the AI Village", and the web app's views, header and some labels still carry that wording; the cover, the credit lines and the questions page follow the manifest. Edit the prompt texts at the top of `summarize.py`, `book.py` and `make_questions.py` for a different world.
- **The privacy-filter sweep needs Apple Silicon** as shipped.
- **Never edit `docs/schema.md` from a stage.** A format change goes to whoever owns the contract.
- **Never serve a bundle that failed validation,** and never skip a privacy step because an earlier one looked clean.
