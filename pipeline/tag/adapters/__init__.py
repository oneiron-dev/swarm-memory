"""The tagger seam: any span tagger plugs in here as an adapter.

The contract (pipeline/tag/README.md, docs/schema.md section 2) is one tag record per slice row, same id, same order:

  {"id": "chat:...", "model": "<model id>", "decode": "<decode id>",
   "spans": [{"s": 0, "e": 11, "label": "PERSON", "text": "Agent Alpha", "p": 0.97}],
   "mood": {"v": 0.12, "a": 0.55, "d": 0.51}}

s and e are codepoint offsets into the slice text and text == slice_text[s:e]; label is one of LABELS; mood is
optional (v in [-1, 1], a and d in [0, 1]).

An adapter is a class with:
  name, tagger (a display name for the manifest), model (written into every record), decode, labels
  load(args)                      load the model once (args: the run_tagger namespace)
  tag(rows) -> [result]           rows are slice records ({"id", "text", "kind", "speaker", ...}); each result is
                                  {"spans": [...], "mood": {...} or None}, in the same order
or, for a tagger that runs as its own program, run_all(slice_dir, out_dir, kinds, limit) that writes the tag files
itself; run_tagger.py then checks them against the contract either way.
"""
from __future__ import annotations

import importlib

LABELS = ("PERSON", "PLACE", "ORG", "DATE", "EVENT", "RELATIONSHIP_REF", "EMOTION", "MONEY", "PRODUCT")
ADAPTERS = {"gliner_vad": "adapters.gliner_vad:GlinerVad", "oneironer": "adapters.oneironer:OneiroNER",
            "none": "adapters.none:NoTags"}


def get(name: str):
    if name not in ADAPTERS:
        raise SystemExit(f"unknown adapter {name!r}; known: {', '.join(sorted(ADAPTERS))}")
    mod, cls = ADAPTERS[name].split(":")
    return getattr(importlib.import_module(mod), cls)()


def check_record(rec: dict, row: dict) -> str | None:
    """Why a tag record breaks the contract for its slice row, or None."""
    if rec.get("id") != row.get("id"):
        return f"id {rec.get('id')!r} != slice id {row.get('id')!r}"
    text = row.get("text") or ""
    for sp in rec.get("spans") or []:
        if not (isinstance(sp, dict) and type(sp.get("s")) is int and type(sp.get("e")) is int
                and 0 <= sp["s"] < sp["e"] <= len(text)):
            return f"{row['id']}: bad span offsets {sp!r}"
        if sp.get("label") not in LABELS:
            return f"{row['id']}: label {sp.get('label')!r} is not one of the nine"
        if sp.get("text") != text[sp["s"]:sp["e"]]:
            return f"{row['id']}: span text {sp.get('text')!r} != slice text {text[sp['s']:sp['e']]!r}"
    mood = rec.get("mood")
    if mood is not None:
        if not isinstance(mood, dict) or not all(isinstance(mood.get(k), (int, float)) for k in "vad"):
            return f"{row['id']}: mood is not {{v, a, d}}"
        if not (-1 <= mood["v"] <= 1 and 0 <= mood["a"] <= 1 and 0 <= mood["d"] <= 1):
            return f"{row['id']}: mood out of range {mood!r}"
    return None
