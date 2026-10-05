# pipeline/tag: entity spans and turn mood, through any tagger

This step reads the slice (`slice/slice-<kind>.jsonl`, schema §1) and writes one tag record per slice row
(`tags/tags-<kind>.jsonl`, schema §2), same `id`, same order. The tagger is pluggable: `run_tagger.py` drives an
adapter and checks every file it writes against the slice before it is renamed into place.

```bash
pip install -r pipeline/tag/requirements-gliner.txt          # or scripts/setup.sh --tagger
export SWARM_WORK=$W
python3 pipeline/tag/run_tagger.py --adapter gliner_vad              # the slice -> tags/; a rerun skips matching files
python3 pipeline/tag/run_tagger.py --adapter gliner_vad --summaries  # after the summarizer: tags/tags-summaries.jsonl
python3 pipeline/tag/run_tagger.py --check                           # check existing tag files only
```

Flags: `--limit N` (the first N rows of each kind, a pilot), `--kinds chat,memory`, `--force`, `--batch 16`,
`--cache <dir>` (downloads; default `$SWARM_CACHE` or `~/.cache/swarm-reader`), `--cmd` (the `oneironer` command).
`tags/STATUS.json` records the tagger, the model, rows, spans and seconds per kind; `tags/LABELS.json` the label order.

## Adapters (`adapters/`)

| adapter | needs | spans | mood |
|---|---|---|---|
| `gliner_vad` (reference) | `requirements-gliner.txt`, CPU; downloads GLiNER small (~600 MB, Apache-2.0) and the NRC VAD Lexicon (6.5 MB) on first use | GLiNER `urchade/gliner_small-v2.1`, nine plain-English labels mapped onto ours | NRC VAD Lexicon v2.1, mean of the matched terms |
| `oneironer` (optional) | our private oneiroNER checkpoint and its batch tagger, run through `ONEIRONER_TAG_CMD` | oneiroNER `rd16c-ch` (the real run, below) | its VAD head |
| `none` | nothing | none | none: no entities, no mood; never for a public bundle |

An adapter is a small class (`adapters/__init__.py` has the contract): `load(args)` once, then `tag(rows)` returns
`{"spans", "mood"}` per row in order; or `run_all(...)` for a tagger that runs as its own program. Add yours to
`ADAPTERS` there.

**gliner_vad, measured.** On the tiny-village sample (243 slice rows, about 21,000 characters) on an Apple M-series
laptop CPU: model load 5.8 s, tagging about 105 rows/s and 9,500 characters/s (batches of 16), mood on every row.
The summaries (36 records, 11,700 characters): about 60 rows/s. A first run downloads the model (about 30 s here).
Speed goes with characters: at 9,500 a second, 100 million characters take about three hours on one laptop CPU.

**gliner_vad, two privacy biases.** GLiNER calls some people products or organisations: in tests it labelled an
invented full name PRODUCT (0.75) and gave it no person score at all, and the builder serves ORG and PRODUCT entities.
So the adapter makes a span PERSON when GLiNER scores it as a person at 0.3 or more, and tags a capitalised name right
after "I am", "I'm", "my name is", "call me", "Dear" or "signed" as PERSON whatever GLiNER said (agent names are left
alone). It also drops pronouns and generic roles GLiNER calls persons ("I", "everyone", "buyers") and widens a span
inside an agent's name to the whole name ("Birch" of "Birch 2.1"), so the build files it under the agent. These are
heuristics: a name written any other way still needs a list entry or a person's review (SKILL.md section 5).

**Mood licence.** The NRC VAD Lexicon (Saif M. Mohammad, National Research Council Canada; cite arXiv:2503.23547) is
free for non-commercial research and educational use; commercial use needs a licence from NRC. The adapter downloads
it into the cache and never into the repo, and prints this note on every run.

**Register.** Dialogue tags better in the conversation register: group chat rows by room in time order, pack them
into long windows and prefix each row with a role only (`A: ` for a human, `B: ` for an agent, never a name); map spans
back to their row and drop any span that crosses two rows or touches a prefix. Read mood from the row alone. The real
run did this with oneiroNER (below); `gliner_vad` tags row by row.

The rest of this file is the real run: oneiroNER over the AI Village slice. The tagger itself is private model code
(oneiroNER); nothing here contains dataset text.

## Output

Synthetic example (not taken from the dataset):

```json
{"id": "chat:…", "model": "rd16c-ch:0b5ff938e12463f8+vad:acbf6b43c6662813", "decode": "v1",
 "spans": [{"s": 0, "e": 11, "label": "PERSON", "text": "Agent Alpha", "p": 0.97},
           {"s": 26, "e": 32, "label": "DATE", "sub": "DATE/Day", "text": "Monday", "p": 0.88}],
 "mood": {"v": 0.12, "a": 0.55, "d": 0.51}}
```

- `s`/`e`: character (codepoint) offsets into the slice `text`; `text` = `slice_text[s:e]`.
- `label`: one of `PERSON`, `PLACE`, `ORG`, `DATE`, `EVENT`, `RELATIONSHIP_REF`, `EMOTION`, `MONEY`, `PRODUCT`.
  `sub`: the predicted subtype when there is one (`DATE/Relative`, `EMOTION/Negated`, …).
- `p`: mean softmax probability of the predicted tag over the span's tokens. A confidence report; spans are not filtered by it.
- `mood`: valence `v` in [-1, 1], arousal `a` and dominance `d` in [0, 1], for the row's text (first 510 tokens).
- No coreference in v1.

## The model

- NER: oneiroNER `rd16c-ch` (checkpoint sha256[:16] `0b5ff938e12463f8`): a multilingual encoder (mGTE base, 306M) fine-tuned
  for span NER, with a character boundary head that refines span edges.
- Mood: the VAD head trained on the same encoder (sha256[:16] `acbf6b43c6662813`), a learned mix over all encoder layers,
  mean-pooled over the text.
- Decode `v1`: the model's evaluation decode (BIO tags → spans → DATE particle snap → character-edge refinement) on
  512-token windows. Longer texts use overlapping windows (about 480 tokens, 96 overlap) merged by character offset.
- Chat is tagged in the conversation register the model is measured in: rows grouped by room in time order, packed whole into
  2,048-token windows, each row prefixed `A: ` (human) or `B: ` (agent), roles only, never names. Spans map back to the
  row; a span crossing two rows or touching a prefix is dropped (none did). Chat mood still comes from the row alone.
  `tags/STATUS.json` marks it `"register": "conversation-2048-roleAB"`. The 103 chat rows too long for one window keep
  their single-row spans.
- The summaries (`summaries/agent.jsonl`, `summaries/room.jsonl`) are tagged too (`tags/tags-summaries.jsonl`), so the
  bundle can catch names the summarizer missed.

## Checks

- Parity before the bulk run, on 200 units of the model's own evaluation set (never this dataset): the batch tagger's spans
  equal the evaluation decode's spans on 200/200 units (601/601 spans) on the Apple GPU path (MLX fp16, the bulk path).
  Mood is within 0.002 of the reference.
- The conversation path reproduces the model's dialogue score (89.83 against a recorded 89.80).
- On 400 long rows of the bulk input the GPU path agrees with the full-precision evaluation path at span F1 0.996. A Neural
  Engine path was tried and dropped for span fidelity (0.986 on the same rows); every published tag is from the GPU path.
- Every tag file is checked against its slice file before it is published: same ids, same order, same count; every span's
  `text` equals the slice text at `s:e`; every mood value is in range.

## The run (2026-10-03 21:52 to 2026-10-04 00:01 JST)

Two workers on our own Macs (a Mac mini GPU and a MacBook GPU), driven from one hub; the dataset never left our machines.

| | value |
|---|---|
| rows tagged | 562,738 (all 11 kinds, = the slice) |
| tokens through the encoder | 207.7M (180.6M slice tokens plus window overlap and special tokens) |
| spans | 3,048,309 (chat 759,881 in the conversation register) |
| summaries tagged | 5,005 (14,522 spans) |
| mood | on every row |
| throughput, end to end | about 11k tokens/s (Mac mini GPU), 26–39k (MacBook GPU, busy host) |

Per-kind rows, tokens and seconds are in `tags/STATUS.json` beside the tag files. About 0.1% of spans overlap a neighbour:
when a text's BIO spans overlap, the evaluation decode skips edge refinement for that text and keeps them; this step does
the same.

Data: AI Digest / AI Village (theaidigest.org/village; dataset card and terms: huggingface.co/datasets/aidigestorg/ai-village). Source terms (ai-village-research-terms, from the dataset card): research and analysis use; no training or fine-tuning of AI systems without AI Digest's written permission; no attempt to re-identify individuals; cite AI Digest / AI Village. Our commitments for this project: the dataset and the tag files stay on our machines; only the derived, redacted bundle described in `docs/schema.md` is served publicly.
