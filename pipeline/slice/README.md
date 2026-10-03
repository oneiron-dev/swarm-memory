# pipeline/slice

Turns the AI Village tables into one natural-language JSONL per kind (schema §1 in `docs/schema.md`), plus the small metadata tables. Runs on Arch, where the dataset lives. Nothing from the dataset is committed here; the tests use synthetic rows.

## Run

```bash
H=/home/lexi/calc-nvme/hackathon-20261004
uv venv -p 3.12 $H/venv-slice && uv pip install -p $H/venv-slice/bin/python -r requirements.txt
systemd-run --user --unit hk-slice --collect -p Nice=15 -p CPUQuota=600% -p MemoryMax=10G \
  --setenv=RAYON_RS_NUM_CPUS=6 --working-directory=$PWD \
  /bin/bash -c "$H/venv-slice/bin/python slicer.py --out $H > $H/logs/slice.log 2>&1"
```

Dry run: add `--limit 2000` (rows per table) and point `--out` somewhere else. Tests: `python -m unittest discover -s tests`.

## Inputs

`--data` (default `/mnt/wd16/datasets/aidigestorg/ai-village`): the gzipped tables and `village-transcript.json`. Read one table at a time, line by line. `computer_use_turns` and the images are never read.

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

Data: AI Digest / AI Village. Research use only.
