# pipeline/pf: a third privacy sweep with openai/privacy-filter

`pf.py` runs OpenAI's privacy-filter (a token classifier, 1.5B MoE / 50M active, Apache-2.0) over every string of the served bundle and turns its hits into a review file for the orchestrator. Two rule passes and two LLM red-teams ran before it; this pass is independent of both. The model card says it misses uncommon names and over-redacts public entities, so its hits are candidates, never verdicts. Nothing here is served or committed, and nothing changes a redaction list: accepted candidates go to `meta/pf_candidates.json` beside the lists, and the orchestrator decides what enters them.

## Runtime

The MLX port `mlx-community/openai-privacy-filter-bf16` (converted with mlx-embeddings 0.1.1) on the Mac mini (Apple M4, 16 GB). The port's own example takes a per-token argmax; `pf.py` instead decodes like the official `opf` package: log-softmax per token, then constrained BIOES Viterbi with the checkpoint's `viterbi_calibration.json`. That file ships one operating point, `default`, with every transition bias 0 (there is no high-recall preset). A valid argmax path is the constrained optimum at zero biases, so Viterbi runs only on sequences whose argmax breaks a BIOES constraint. A span's `score` is the geometric mean of its tokens' decoded-label probabilities.

Throughput on the Mac mini: 18,997 tokens/s for forward pass plus decoding (the whole bundle: 232,130 distinct strings, 12,994,245 tokens in 684 s; batches of up to 16,384 tokens, longest first; see `run.json`).

## Run

```bash
# Mac mini: venv, model, bundle copy
uv venv -p 3.12 /Users/olety/hackathon-20261004/venv-pf
uv pip install -p /Users/olety/hackathon-20261004/venv-pf mlx==0.32.3 mlx-lm==0.32.0 numpy tokenizers huggingface_hub \
  "git+https://github.com/Blaizzy/mlx-embeddings@9b28270be81211f2b8daed0041aec65ea5dc4b28"
HF_HOME=/Users/olety/hackathon-20261004/pf/hf /Users/olety/hackathon-20261004/venv-pf/bin/python -c \
  'from huggingface_hub import snapshot_download as d; d("mlx-community/openai-privacy-filter-bf16", local_dir="/Users/olety/hackathon-20261004/pf/model/bf16")'
rsync -a arch:/home/lexi/calc-nvme/hackathon-20261004/bundle/data/ /Users/olety/hackathon-20261004/pf/data/

P=/Users/olety/hackathon-20261004/pf
$PY pipeline/pf/pf.py collect --data $P/data --work $P/work
$PY pipeline/pf/pf.py run --work $P/work --model $P/model/bf16        # writes work/raw.jsonl and work/run.json

# Arch: copy work/strings.jsonl, raw.jsonl, collect.json, run.json to $W/pf/, then
W=/home/lexi/calc-nvme/hackathon-20261004
$W/venv-pf/bin/python pipeline/pf/pf.py filter                         # meta/ lists; writes pf/kept.jsonl, candidates.jsonl
$W/venv-pf/bin/python pipeline/pf/pf.py classify --prime-agent /home/lexi/.local/bin/prime-agent
$W/venv-pf/bin/python pipeline/pf/pf.py review                         # pf/review.json: Luna's human_individual hits
$W/venv-pf/bin/python pipeline/pf/pf.py write --review $W/pf/review.txt   # meta/pf_candidates.json, pf-hits.jsonl, pf-summary.md
```

`$W/venv-pf` holds `pipeline/bundle/requirements.txt` (`filter` and `classify` import the builder's lists and `human_ids.py`).

## Steps

| step | in | out |
|---|---|---|
| `collect` | `data/` (every JSON file) | `strings.jsonl`: each distinct non-blank string value, object key and file name with its locations (`<file>#$["k"][0]`, `<file>#$["k"]#key`, `<file>#name`); `collect.json` counts |
| `run` | `strings.jsonl`, the MLX checkpoint | `raw.jsonl`: `{sid, s, e, label, score, text}` per span (offsets into the string; strings over 4,000 characters are split on sentence ends); `run.json` |
| `filter` | `raw.jsonl`, `meta/` lists, `bundle.json` agents | `kept.jsonl`, `candidates.jsonl` (distinct new person, account and secret spans), `filter.json` (counts per class and file type before and after, drop reasons, rule gaps with their path shapes) |
| `classify` | `candidates.jsonl` | one round dir per run (`luna/`, `luna-2/`, ...) holding only candidates no earlier round sent, each `classified.jsonl` through `human_ids.cmd_classify` (gpt-5.6-luna, `--provider cpa-r --no-tools`, batches of 100, `responseModel` verified, incomplete replies retried) |
| `review` | Luna's hits | `review.json` (`p<n>` with one 120-character context each) |
| `write` | `review.txt`: `p<n> accept\|reject <reason>`, `p<n> accept:<id> <reason>` for an id inside a longer span, `+ <id> \| <reason>` for an id found in a class Luna did not see | `meta/pf_candidates.json` (same shape as `meta/extra_human_ids.json`), `pf-hits.jsonl`, `pf-summary.md` |

A span is dropped when its text (case-folded, trimmed of punctuation): has nothing left once redaction tokens are removed; lies in a string that is a schema identifier (source ref, summary or entity id, uuid, hash, timestamp, bare number); is an agent's name, id or model, or only model and lab words (also when grown to whole words); is on the builder's public keep lists or allowlists (`PUBLIC_HANDLES`, `PROFILE_ALLOW`, `extra_human_rules.json` `keep` and `profile_allow`); is a `private_date`; is a `private_url` without an account path (an account from `PROFILE_RE` or `SUBDOMAIN_RE` that the allowlists do not keep; else substack, linkedin or `@`); or is a `private_address` with no digit and no street word (a city or country). A kept `private_person` span that is on `human_names.json`, `extra_human_ids.json` or the rule lists is a rule gap; one on none of them is a new candidate.

Tests (synthetic fixtures, no model): `python3 -m unittest discover -s pipeline/pf/tests`.

Data: AI Digest / AI Village. Research use only; no training on it; no re-identification.
