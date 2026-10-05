# pipeline/summarize

Writes the per-agent and per-room daily summaries of `docs/schema.md` section 3. Each summary is 3 to 7 sentences by a model (the real run used `gpt-5.6-luna`), and every sentence cites slice ids.

Village days need no call. The bundler uses the archive's own daily logs for them (AI Digest's `daily` summaries in the real run, `daily_summary` events in an `archive.jsonl`).

The model client is `pipeline/common/swarm_llm.py`: `--endpoint <base URL> --model <id>` for any OpenAI-compatible server (key in `SWARM_LLM_API_KEY`), `--prime-agent <path>` for our own harness, or `--mock` for templated, cited placeholders with no model at all. The same three work in every model step of the pipeline.

## What it does

- **Units.** One `room-day:<room>:<day>` for every room with chat that day. One `agent-day:<agent>:<day>` for every non-human agent with an input row that day.
- **Inputs.**
  - A room-day gets all chat in that room that day. Every human speaker is shown as `human`.
  - An agent-day gets that agent's `chat`, `session_goal`, `session_summary`, `next_goal`, `history_query`, `outreach` and `cc_text` rows, plus the first 2,000 characters of the day's `memory` row. `thinking` and `cc_thinking` are never read.
- **Prompt.** The lines are in time order and numbered `[n] HH:MM <agent> (<kind>): <text>`. One line is clipped at 2,000 characters. The script keeps the n → ref map. The numbered lines are the material (prime-agent's stdin; the first part of the user message for an endpoint), and the task line follows them.
- **Cap.** One call takes about 40k input tokens at most. Over the cap, every `session_goal`, `session_summary`, `next_goal` and `memory` row is kept, and the other rows are sampled evenly in time. The record then carries `"sampled": true`.
- **Validate.** Every `[n]` must exist in the map; unknown markers are dropped. `[2, 3]` and `[2-3]` become `[2][3]`. A marker after a full stop moves in front of it. A reply with no valid marker is retried once, then kept with `"uncited": true`.
- **Human names.** The system prompt lists the agent names as the only person-like names allowed. After each summary, a second small call lists any name, handle or email in the text that may belong to a human. If it finds one, the summary is written again with those names forbidden. If the second draft still has them, they are replaced with "a human" in place. `audit-existing` runs the same check over records written before this step, and moves flagged records out (a local `pre-audit-*` backup is kept) so the next run writes them again.
- **Model check.** The run stops hard (exit 3) if the model a reply names is not the one asked for (`--model`, `SWARM_LLM_MODEL`; prime-agent's default `gpt-5.6-luna`). A dated snapshot of the asked model counts as the same. Every record carries the model that wrote it (`mock` under `--mock`).
- **Calls.** Concurrency starts at 8. It rises by 4, up to 12, after 40 clean calls in a row, and falls by 4 on a rate limit. Errors back off exponentially (10 s, doubling, capped at 300 s, 6 attempts). After 25 failed units in a row, the run stops.
- **Order.** Room-days run first, then agent-days. Both go from the most recent day backwards.
- **Resume.** Output is append-only, one JSON object per line. Ids already written are skipped, and failed units are retried on the next run.

## Run

Python 3.12, stdlib only (`requirements.txt` is empty on purpose). The work area is `--work` or `$SWARM_WORK`.

```bash
export SWARM_WORK=$W
python3 summarize.py estimate                              # summaries/ESTIMATE.json: units, tokens, calls, the client
python3 summarize.py run --mock                            # no model: placeholders, to see the pipeline run
export SWARM_LLM_BASE_URL=https://api.openai.com/v1 SWARM_LLM_MODEL=<model id> SWARM_LLM_API_KEY=<key>
python3 summarize.py run --scope room --limit 5            # pilot on a real model
python3 summarize.py pilot-md --n 10                       # summaries/PILOT.md, local only: spot-check the cites
python3 summarize.py run                                   # the rest; a rerun does only what is missing
python3 summarize.py audit-existing                        # human-name check over records written before the guard
```

Flags: `--scope all|room|agent`, `--limit N` (new units this run), `--since-day D` (only days ≥ D), `--concurrency 8`, `--max-concurrency 12`, `--cap 40000`, `--no-audit` (skip the human-name check), `--wait-slice` (wait until `slice/STATS.json` marks the needed kinds done), `--save-prompts DIR` (local debugging only), and the client flags `--mock`, `--endpoint`, `--model`, `--reasoning-effort`, `--prime-agent`, `--provider`, `--thinking`, `--timeout`.

The real run went through prime-agent on Arch: `summarize.py run --work /home/lexi/calc-nvme/hackathon-20261004 --wait-slice --prime-agent /home/lexi/.local/bin/prime-agent` under `systemd-run`.

Tests use synthetic fixtures, a fake prime-agent and the mock:

```bash
python3 -m unittest discover -s pipeline/summarize/tests
```

## Inputs and outputs (under the work area)

| path | what |
|---|---|
| `slice/slice-<kind>.jsonl`, `slice/STATS.json`, `meta/agents.json`, `meta/rooms.json` | input (schema section 1) |
| `summaries/room.jsonl`, `summaries/agent.jsonl` | schema section 3 records, plus `sampled` / `uncited` when true |
| `summaries/calls.jsonl` | one line per finished unit: seconds, calls, tokens in and out, line count, whether it was regenerated or scrubbed for a human name |
| `summaries/audit.jsonl` | `audit-existing` verdicts (id and flag only, never the names) |
| `summaries/failed.jsonl` | units that failed every attempt; retried on the next run |
| `summaries/PROGRESS.json` | done, failed, tokens in and out, calls per minute, concurrency, ETA; updated every 15 s |
| `summaries/ESTIMATE.json` | unit counts, input tokens total and p50/p95 per call, wall hours at 8 and 12 |

Real run data: AI Digest / AI Village. Research use only; nothing from the dataset is committed here.
