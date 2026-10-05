# swarm-reader runbook

`R` is this repo, `W` the work area (outside the repo). Every command below runs as written once `SWARM_WORK` is set. SKILL.md says what each step does and why.

## 1. See it work (about a minute warm, a few minutes cold)

Needs Python 3.12, bun, and the network for the first run (the tagger model, about 600 MB).

```bash
$R/scripts/smoke.sh
```

It builds a venv (`$R/.venv`), writes an invented archive, runs every step below with `SWARM_LLM_MOCK=1` (no model, no key) and the `gliner_vad` tagger, validates the bundle three times, builds the site, checks `/api/health`, and scans the served bundle for the identifiers it planted. It prints timings and ends with `SMOKE OK`. Measured here: 61 s with every cache empty, 20 s warm. Options: `SMOKE_WORK=<dir>`, `SMOKE_ADAPTER=none` (no download), `SMOKE_LIMIT=50`, `SMOKE_CLIENT=endpoint` (model steps over HTTP to a local fake server), `SMOKE_PF_MODEL=<dir>` (adds the privacy-filter sweep on Apple Silicon). Then look at the site: `python3 $R/pipeline/serve/serve.py --work <the work area it printed>`.

## 2. Your archive

Clone the public copy first: `git clone https://github.com/oneiron-dev/swarm-memory`. Then pick the model backend once. Every model step below reads it from the environment, and with none set a model step stops with `no model backend configured`.

```bash
export R=<this repo> W=<work area> SWARM_WORK=<work area>
$R/scripts/setup.sh --tagger && source $R/.venv/bin/activate                    # 0 setup: one venv, every stage
export SWARM_LLM_MOCK=1                                                         # a dry run: no model, placeholder text
# or a real model, on any OpenAI-compatible server (unset SWARM_LLM_MOCK first; it wins over the others):
# export SWARM_LLM_BASE_URL=https://api.openai.com/v1 SWARM_LLM_MODEL=<model id> SWARM_LLM_API_KEY=<key>

python $R/pipeline/slice/from_jsonl.py archive.jsonl --name "<dataset>" --cite "Data: <who made it>"   # 1 slice
python $R/pipeline/tag/run_tagger.py --adapter gliner_vad                       # 2 tag the slice (--limit 200 to pilot)
python $R/pipeline/summarize/summarize.py estimate                              # 3 units, tokens, calls, the client
python $R/pipeline/summarize/summarize.py run --limit 5 && python $R/pipeline/summarize/summarize.py pilot-md
python $R/pipeline/summarize/summarize.py run                                   # 4 cited summaries, name guard on
python $R/pipeline/tag/run_tagger.py --adapter gliner_vad --summaries           # 5 tag the summaries
python $R/pipeline/bundle/human_names.py --archive archive.jsonl                # 6 the human name list (+ --names <file>)
python $R/pipeline/bundle/build_bundle.py && python $R/pipeline/bundle/validate_bundle.py   # 7 first bundle
```

`archive.jsonl` is one object per text unit: `{"id", "time", "agent", "room", "kind": "chat|memory|session|event", "text", "reply_to"}`, humans as `"agent": "human"`; goals are `event` rows with `"subkind": "goal"`. A group-level event row (`goal`, `daily_summary`, or a skipped event) takes `"agent": "human"` or any other value and never becomes an agent. The full contract is in `pipeline/slice/README.md`. For the AI Village tables use `python $R/pipeline/slice/slicer.py --data <tables>` instead of step 1.

## 3. The privacy procedure (before any public byte)

```bash
python $R/pipeline/bundle/human_ids.py candidates                               # 8 handle sweep
python $R/pipeline/bundle/human_ids.py classify                                 #   until it says every batch is complete
python $R/pipeline/bundle/human_ids.py scan && python $R/pipeline/bundle/human_ids.py hits
cp $W/human_ids/review-template.txt $W/human_ids/review.txt                     #   a person decides every h<n> line
python $R/pipeline/bundle/human_ids.py write --review $W/human_ids/review.txt   # 9 -> extra_human_ids.json
for s in days gaps chapters hooks pairs stage; do python $R/pipeline/book/book.py $s --parallel 8; done   # 10 the book
python $R/pipeline/book/book.py adherence --parallel 4 && python $R/pipeline/book/book.py rephrase
python $R/pipeline/bundle/build_bundle.py && python $R/pipeline/bundle/validate_bundle.py   # 11 full bundle, swapped only if valid
mkdir -p $W/redteam && touch $W/redteam/leaks.jsonl $W/redteam/review.txt   # 12 per red-team round: the red-team writes leaks.jsonl
python $R/pipeline/bundle/human_ids.py redteam --leaks $W/redteam/leaks.jsonl --review $W/redteam/review.txt   #    a clean round is an empty leaks.jsonl
python $R/pipeline/pf/pf.py collect && python $R/pipeline/pf/pf.py run --model <mlx checkpoint dir>   # 13 privacy filter (Apple Silicon)
python $R/pipeline/pf/pf.py filter && python $R/pipeline/pf/pf.py classify && python $R/pipeline/pf/pf.py review
python $R/pipeline/pf/pf.py write --review $W/pf/review.txt                     #   accepted ids join extra_human_ids.json
python $R/pipeline/book/book.py rephrase                                        #   the lists changed: reword again
python $R/pipeline/bundle/build_bundle.py && python $R/pipeline/bundle/validate_bundle.py   # 14 rebuild with the new lists
```

The red-team prompt and the leak-row format are in SKILL.md section 5. A red-team round ends clean when `leaks.jsonl` stays empty. The privacy-filter checkpoint is public (about 2.8 GB): `huggingface-cli download mlx-community/openai-privacy-filter-bf16 --local-dir $W/pf-model`.

## 4. The questions, the site

```bash
python $R/docs/questions/make_questions.py                                      # 15 -> $W/questions/questions.json
python $R/pipeline/bundle/build_bundle.py && python $R/pipeline/bundle/validate_bundle.py   #    served as data/questions.json
(cd $R/web && bun install && bun run build) && rm -rf $W/web-dist && cp -R $R/web/dist $W/web-dist   # 16 web
python $R/pipeline/serve/serve.py --check                                       # 17 health ok, / and the bundle answer
python $R/pipeline/serve/serve.py                                               #    http://127.0.0.1:8790/
```

Before anything goes public: scan every published byte against the name and id lists, and look for archive rows and secrets (SKILL.md section 5.9).
