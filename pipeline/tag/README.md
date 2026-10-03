# pipeline/tag — entity spans and turn mood with oneiroNER

This step reads the slice (`slice/slice-<kind>.jsonl`, schema §1) and writes one tag record per slice row
(`tags/tags-<kind>.jsonl`, schema §2), same `id`, same order. The tagger itself is private model code (oneiroNER);
this folder documents the contract and how the run was made. Nothing here contains dataset text.

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
