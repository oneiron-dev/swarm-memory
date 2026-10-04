#!/usr/bin/env python3
"""Collect human display names from the events table into meta/human_names.json (Arch only).

The list is pipeline-internal: never committed, never served. build_bundle.py uses it only to replace
those names with [human] in the text it serves. Sources: USER_TALK speakerName, USER_NAME_CHANGE
oldName/newName, OUTREACH_* recipient (the name part; email addresses are redacted by pattern anyway), plus
the reviewed names in meta/human_names_reviewed.json (names found by privacy sweeps; kept across reruns).
Prints counts only.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import re

DATASET = "/mnt/wd16/datasets/aidigestorg/ai-village"
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
NEEDLES = (b"USER_TALK", b"USER_NAME_CHANGE", b"OUTREACH_APPROVAL")


def names_from_events(path: str) -> set:
    out = set()
    with gzip.open(path, "rb") as f:
        for line in f:
            if not any(n in line for n in NEEDLES):
                continue
            data = json.loads(line).get("data") or {}
            kind = data.get("actionType")
            if kind == "USER_TALK":
                vals = [data.get("speakerName")]
            elif kind == "USER_NAME_CHANGE":
                vals = [data.get("oldName"), data.get("newName")]
            elif kind in ("OUTREACH_APPROVAL_REQUEST", "OUTREACH_APPROVAL_RESPONSE"):
                rec = data.get("recipient")
                vals = [EMAIL_RE.sub(" ", p).strip(" <>\"'()") for p in re.split(r"[,;\n]", rec or "")]
            else:
                continue
            for v in vals:
                if isinstance(v, str) and v.strip():
                    out.add(" ".join(v.split()))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", default=DATASET)
    ap.add_argument("--out", default="/home/lexi/calc-nvme/hackathon-20261004/meta/human_names.json")
    args = ap.parse_args(argv)
    names = names_from_events(os.path.join(args.dataset, "events.jsonl.gz"))
    reviewed = os.path.join(os.path.dirname(args.out), "human_names_reviewed.json")
    if os.path.exists(reviewed):
        with open(reviewed) as f:
            names |= {" ".join(n.split()) for n in json.load(f) if isinstance(n, str) and n.strip()}
    names = sorted(names)
    tmp = args.out + ".tmp"
    with open(tmp, "w") as f:
        json.dump(names, f, ensure_ascii=False)
    os.replace(tmp, args.out)
    print(f"wrote {args.out}: {len(names)} distinct names")


if __name__ == "__main__":
    main()
