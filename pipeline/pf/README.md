# pipeline/pf: a third privacy sweep with openai/privacy-filter

`pf.py` runs OpenAI's privacy-filter (a token classifier, 1.5B MoE / 50M active, Apache-2.0) over every string of the served bundle and turns its hits into a review file for the orchestrator. Two rule passes and two LLM red-teams ran before it; this pass is independent of both. The model card says it misses uncommon names and over-redacts public entities, so its hits are candidates, never verdicts. Nothing here is served or committed. A person accepts or rejects every hit; `write` puts the accepted ids in `<lists>/pf_candidates.json` and adds them to `<lists>/extra_human_ids.json`, which the next build reads.

**Apple Silicon only.** `run` needs MLX, which runs only on M-series Macs. On Linux or Windows, run openai/privacy-filter with its own PyTorch tooling, write `<work>/raw.jsonl` in the shape below, and go on from `filter`; or skip this sweep and say so in the bundle's notes. Every other step is plain Python.

## Runtime

The MLX port `mlx-community/openai-privacy-filter-bf16` (converted with mlx-embeddings 0.1.1) on the Mac mini (Apple M4, 16 GB). The port's own example takes a per-token argmax; `pf.py` instead decodes like the official `opf` package: log-softmax per token, then constrained BIOES Viterbi with the checkpoint's `viterbi_calibration.json`. That file ships one operating point, `default`, with every transition bias 0 (there is no high-recall preset). A valid argmax path is the constrained optimum at zero biases, so Viterbi runs only on sequences whose argmax breaks a BIOES constraint. A span's `score` is the geometric mean of its tokens' decoded-label probabilities.

Throughput on the Mac mini: 18,997 tokens/s for forward pass plus decoding (the whole bundle: 232,130 distinct strings, 12,994,245 tokens in 684 s; batches of up to 16,384 tokens, longest first; see `run.json`).

## Run

```bash
export SWARM_WORK=$W
scripts/setup.sh --pf                                     # MLX, mlx-embeddings (pinned commit), numpy
huggingface-cli download mlx-community/openai-privacy-filter-bf16 --local-dir $W/pf-model   # 2.8 GB, public
python3 pipeline/pf/pf.py collect                         # $W/bundle/data -> $W/pf/strings.jsonl
python3 pipeline/pf/pf.py run --model $W/pf-model         # raw.jsonl, run.json
python3 pipeline/pf/pf.py filter                          # kept.jsonl, candidates.jsonl (reads the lists)
python3 pipeline/pf/pf.py classify --mock                 # or --endpoint/--prime-agent; --llm-model names the model
python3 pipeline/pf/pf.py review                          # review.json + review-template.txt; refuses until classify is done
python3 pipeline/pf/pf.py write --review $W/pf/review.txt # pf_candidates.json, merged into extra_human_ids.json
```

Flags: `--root` (default `$SWARM_WORK`), `--data` (default `<root>/bundle/data`), `--work` (default `<root>/pf`), `--lists` (default `$SWARM_LISTS` or `<root>/meta`), `--model` (the MLX checkpoint dir for `run`), `--limit N` (`run`: first N strings), `--allow-incomplete` (`review`), and the client flags `--mock`, `--endpoint`, `--llm-model` (the classify model; `--model` is the checkpoint here), `--prime-agent`, `--timeout`.

In the real run, `collect` and `run` ran on the Mac mini (a bundle copy) and the other steps on Arch through prime-agent.

## Steps

| step | in | out |
|---|---|---|
| `collect` | `data/` (every JSON file) | `strings.jsonl`: each distinct non-blank string value, object key and file name with its locations (`<file>#$["k"][0]`, `<file>#$["k"]#key`, `<file>#name`); `collect.json` counts |
| `run` | `strings.jsonl`, the MLX checkpoint | `raw.jsonl`: `{sid, s, e, label, score, text}` per span (offsets into the string; strings over 4,000 characters are split on sentence ends); `run.json` |
| `filter` | `raw.jsonl`, `meta/` lists, `bundle.json` agents | `kept.jsonl`, `candidates.jsonl` (distinct new person, account and secret spans), `filter.json` (counts per class and file type before and after, drop reasons, rule gaps with their path shapes) |
| `classify` | `candidates.jsonl` | one round dir per run (`luna/`, `luna-2/`, ...) holding only candidates no earlier round sent, each `classified.jsonl` through `human_ids.cmd_classify` (the configured model, batches of 100, the reply's model verified, incomplete replies retried); a finished round gets a `done` mark |
| `review` | the classifier's hits | `review.json` (`p<n>` with one 120-character context each) and `review-template.txt`; refuses while a round has no `done` mark or a candidate was never sent (else those would drop out silently) |
| `write` | `review.txt`: `p<n> accept\|reject <reason>`, `p<n> accept:<id> <reason>` for an id inside a longer span, `+ <id> \| <reason>` for an id found in a class the classifier did not see | `<lists>/pf_candidates.json` (same shape as `extra_human_ids.json`), the accepted ids added to `<lists>/extra_human_ids.json`, `pf-hits.jsonl`, `pf-summary.md` |

A span is dropped when its text (case-folded, trimmed of punctuation): has nothing left once redaction tokens are removed; lies in a string that is a schema identifier (source ref, summary or entity id, uuid, hash, timestamp, bare number); is an agent's name, id or model, or only model and lab words (also when grown to whole words); is on the builder's public keep lists or allowlists (`PUBLIC_HANDLES`, `PROFILE_ALLOW`, `extra_human_rules.json` `keep` and `profile_allow`); is a `private_date`; is a `private_url` without an account path (an account from `PROFILE_RE` or `SUBDOMAIN_RE` that the allowlists do not keep; else substack, linkedin or `@`); or is a `private_address` with no digit and no street word (a city or country). A kept `private_person` span that is on `human_names.json`, `extra_human_ids.json` or the rule lists is a rule gap; one on none of them is a new candidate.

Tests (synthetic fixtures, no model): `python3 -m unittest discover -s pipeline/pf/tests`.

Real run data: AI Digest / AI Village. Research use only; no training on it; no re-identification.
