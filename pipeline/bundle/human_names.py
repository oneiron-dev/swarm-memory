#!/usr/bin/env python3
"""Collect human display names into <lists>/human_names.json (default $SWARM_WORK/meta/human_names.json).

The list is pipeline-internal: never committed, never served. build_bundle.py uses it only to replace
those names with [human] in the text it serves. Sources, any that you give:
  --archive archive.jsonl   the "human_name" field of the generic input (pipeline/slice/README.md)
  --dataset <dir>           the AI Village tables: USER_TALK speakerName, USER_NAME_CHANGE oldName/newName,
                            OUTREACH_* recipient (the name part; email addresses are redacted by pattern anyway)
  --names <file>            a file you write, one name per line (names a person found by reading the archive)
plus the reviewed names in <lists>/human_names_reviewed.json (names found by privacy sweeps; kept across reruns).
With no source the list is written empty: the build then pools and cuts humans by rule only (SKILL.md section 5).
Prints counts only.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "common"))
import swarm_paths  # noqa: E402

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


def names_from_archive(path: str) -> set:
    """The human_name fields of an archive.jsonl (rows with agent "human")."""
    out = set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            if '"human_name"' not in line:
                continue
            v = json.loads(line).get("human_name")
            if isinstance(v, str) and v.strip():
                out.add(" ".join(v.split()))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=swarm_paths.work_default(), help="the work area (default $SWARM_WORK)")
    ap.add_argument("--lists", help="redaction-list dir (default $SWARM_LISTS or <root>/meta)")
    ap.add_argument("--archive", help="generic archive.jsonl: its human_name fields")
    ap.add_argument("--dataset", help="AI Village table dir: names from events.jsonl.gz")
    ap.add_argument("--names", help="a text file, one human name per line")
    ap.add_argument("--out", help="the list to write (default <lists>/human_names.json)")
    args = ap.parse_args(argv)
    if not args.out:
        args.out = os.path.join(swarm_paths.lists_dir(swarm_paths.need_work(args.root), args.lists), "human_names.json")
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    names, sources = set(), []
    if args.archive:
        got = names_from_archive(args.archive)
        names |= got
        sources.append(f"archive {len(got)}")
    if args.dataset:
        got = names_from_events(os.path.join(args.dataset, "events.jsonl.gz"))
        names |= got
        sources.append(f"dataset {len(got)}")
    if args.names:
        with open(args.names, encoding="utf-8") as f:
            got = {" ".join(n.split()) for n in f if n.strip() and not n.lstrip().startswith("#")}
        names |= got
        sources.append(f"file {len(got)}")
    reviewed = os.path.join(os.path.dirname(args.out), "human_names_reviewed.json")
    if os.path.exists(reviewed):
        with open(reviewed) as f:
            got = {" ".join(n.split()) for n in json.load(f) if isinstance(n, str) and n.strip()}
        names |= got
        sources.append(f"reviewed {len(got)}")
    names = sorted(names)
    tmp = args.out + ".tmp"
    with open(tmp, "w") as f:
        json.dump(names, f, ensure_ascii=False)
    os.replace(tmp, args.out)
    print(f"wrote {args.out}: {len(names)} distinct names ({', '.join(sources) or 'no source given: an empty list'})")


if __name__ == "__main__":
    main()
