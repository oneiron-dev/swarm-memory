# pipeline/slice

Turns an archive of multi-agent transcripts into one natural-language JSONL per kind (schema §1 in `docs/schema.md`), plus the small metadata tables. Every later stage reads only this slice. Two front doors write it:

- `from_jsonl.py`: any archive given as `archive.jsonl` (the input contract below). Start here with your own data.
- `slicer.py`: the AI Village tables (the real run). Kept as one adapter; it needs the dataset's eleven gzipped tables.

Both write the same files with the same schema through the same code (`KindWriter`, `DayMap`, `slug`, `family` in `slicer.py`). Nothing from any dataset is committed here; the tests use invented rows.

## Any archive: archive.jsonl

One JSON object per line, one text unit per object:

```json
{"id": "m-0042", "time": "2031-03-02T09:11:00Z", "agent": "Alder-7", "room": "general", "kind": "chat",
 "text": "Birch 2.1, can you check the seed list?", "reply_to": "m-0041"}
```

| field | required | meaning |
|---|---|---|
| `id` | yes | unique within the archive; becomes the citation ref `<src>:<id>` (whitespace turned into `_`) |
| `time` | yes | ISO-8601; an offset (`+09:00`) is converted to UTC, a time without one is read as UTC |
| `agent` | yes | the agent's display name; `"human"` for any human (all humans are pooled into one pseudo-agent). On a group-level event row (`goal`, `daily_summary`, or any event that is skipped) write `"human"` or any other value, such as `"system"`: such a row never makes an agent |
| `room` | for chat | the chat room; a chat row without one goes to `main` |
| `kind` | yes | `chat`, `memory`, `session` or `event` |
| `text` | yes | the text unit |
| `reply_to` | no | read but not used: the bundle's replies are the next speaker in the same room within ten minutes |
| `subkind` | no | `session`: `goal`, `summary` (default), `next_goal`, `thinking`, `code`. `event`: `goal` (a new group goal starts; the text is the goal), `agent_goal`, `daily_summary` (a written log of the day), `outreach`, `query`; any other event is counted and skipped |
| `session` | no | groups session text; becomes `cus:<session>` |
| `model` | no | the agent's model id (first one per agent wins); sets the agent's lab family |
| `human_id` | no | on human rows: a per-human id. Every occurrence of it in any text becomes `[REDACTED]`; it is never written out |
| `human_name` | no | on human rows: the name the human goes by. Never written to the slice; `pipeline/bundle/human_names.py --archive` reads it into the redaction list |

### How it maps onto the slice

| archive row | slice kind | ref |
|---|---|---|
| `chat` | `chat` | `chat:<id>` |
| `memory` | `memory`: the last memory of each agent per day, as for AI Village | `mem:<id>` |
| `session` + `goal` / `summary` / `next_goal` / `thinking` / `code` | `session_goal` / `session_summary` / `next_goal` / `thinking` / `cc_text` | `cus:` / `ev:` / `ev:` / `ev:` / `cc:` |
| `event` + `daily_summary` | `digest_summary` (village day summaries; `meta/summaries_index.json` type `daily`); credited to its `agent` only when that agent has rows of its own, else to no one | `sum:<id>` |
| `event` + `outreach` / `query` | `outreach` / `history_query` | `ev:<id>` |
| `event` + `goal` | `meta/village_goals.json` (start = its time, end = the next goal's) | |
| `event` + `agent_goal` | `meta/agent_goals.json` | |

- **Days.** A day is a UTC calendar date; day 1 is the date of the first row, and every date up to the last row is a day. A day's `first_ts` is the time of its first row of any kind, event rows included, as in the AI Village `days.json`; a date with no rows gets `first_ts: null`. `meta/days.json` has the same shape as the AI Village one. A goal that starts at 09:00 on a day starts its chapter on that day: the bundle gives each day the goal in effect for most of it (`goal_of_day` in `pipeline/bundle/build_bundle.py`).
- **Agents and rooms.** `meta/agents.json` lists every non-human `agent` of a chat, memory, session, `agent_goal`, `outreach` or `query` row, in order of first appearance, slugged like the AI Village names (`Birch 2.1` → `birch-2-1`); the `agent` of a group-level event row is never listed. `meta/rooms.json` lists every chat room.
- **Goals.** With no `goal` events, one group goal covers the whole archive (`--goal` sets its text), so the book has one chapter.
- **Dataset.** `meta/dataset.json` holds `--name` and `--cite`; the bundle's manifest and the site's credit line use them.
- **Rules.** The same as below: dedupe per kind on the folded text, texts under 3 characters skipped, `ntok` from the same tokenizer (`--tokenizer whitespace` skips the download).

```bash
export SWARM_WORK=$W
python3 pipeline/slice/from_jsonl.py archive.jsonl --name "My archive" --cite "Data: My lab, 2031"
```

`samples/tiny-village/make_tiny_village.py` writes an invented archive in this shape (4 agents, 6 days, 2 rooms, 3 goals, 253 rows).

## The AI Village tables: slicer.py

Runs where the dataset lives (Arch in the real run).

```bash
python3 slicer.py --data /path/to/ai-village --out $W          # --out defaults to $SWARM_WORK
```

Dry run: add `--limit 2000` (rows per table) and point `--out` somewhere else. Tests: `python -m unittest discover -s tests`.

## Inputs

`--data`: the gzipped tables and `village-transcript.json` (`agents`, `chat_rooms`, `chat_messages`, `events`, `summaries`, `claude_code_messages`, `computer_use_sessions`, `agent_memories`, `village_goals`, `agent_goals` as `.jsonl.gz`). Read one table at a time, line by line. `computer_use_turns` and the images are never read.

## Outputs

`<out>/meta/`
- `agents.json`: `{id, uuid, name, model, participating, created_at, family}`; `id` is the slug of the name.
- `rooms.json`: `{id, uuid, name, deleted_at, created_at}`.
- `days.json`: `{day, date, first_ts, events}` from the transcript. A record belongs to the last day whose `first_ts` is at or before its time (empty days have `first_ts: null` and get no records).
- `village_goals.json` (with `first_day`/`last_day`), `agent_goals.json` (agent slug), `summaries_index.json` (type, target, day of each `sum:` record; no text).

`<out>/slice/`
- `slice-<kind>.jsonl` for `chat`, `session_goal`, `session_summary`, `next_goal`, `history_query`, `outreach`, `thinking`, `cc_text`, `cc_thinking`, `memory`, `digest_summary`. Records are sorted by time. A file appears (atomic rename) only when its kind is complete.
- `STATS.json`: rows, chars and tokens per kind, per year-month, per agent and per day; dropped counts (dupes, short, scrub-only); reasoning shape counts per model family. Rewritten after each table; `final: true` at the end, `final_kinds` lists the files that are done.

## Rules applied

- Times are UTC ISO with `Z`, cut to seconds. Every human speaker is `human`; user ids, viewer names and outreach recipients are dropped. A chat user id that shows up inside any text becomes `[REDACTED]`.
- Dedupe per kind on sha1 of whitespace-folded lowercase text, keeping the earliest record. Texts under 3 characters, or under 3 once scrub markers are removed, are skipped.
- `ntok` is the XLM-R tokenizer of `Alibaba-NLP/gte-multilingual-mlm-base`, no special tokens, no truncation, counted with scrub markers removed.
- `thinking` is reasoning only, from any event's raw `data.output`: Anthropic `thinking` blocks (message object or bare block list), OpenAI Responses `reasoning` summaries, Gemini parts with `thought: true`, chat-completion `reasoning_content` / `reasoning` / `reasoning_details`. Visible replies and tool calls are skipped.
- `session` on event records: `START_USING_COMPUTER` names the session it opens, `CONSOLIDATE` names the session it closes, and `STOP_USING_COMPUTER` closes the one the last START opened. Events in between inherit that session. `next_goal` carries the session it was written in (the one being consolidated).
- `outreach`: a request is the agent's medium, rationale and message; a response is the verdict plus the admin comment, with `speaker: human`.
- `memory`: the last memory of each agent per village day.
- `digest_summary`: `daily` rows take their day from `summary_date` (or the day number in `summary_target`); `agent` rows carry the agent slug; `agent_daily` rows (`<agent name>:<day>`) carry both.

Real run data: AI Digest / AI Village. Research use only.
