#!/usr/bin/env python3
"""Tag the slice (and, after the summarizer, the summaries) with any adapter, and check every file it writes.

  python3 pipeline/tag/run_tagger.py --adapter gliner_vad                 # $SWARM_WORK/slice -> $SWARM_WORK/tags
  python3 pipeline/tag/run_tagger.py --adapter gliner_vad --summaries     # summaries/*.jsonl -> tags/tags-summaries.jsonl
  python3 pipeline/tag/run_tagger.py --check                              # check existing tag files only

One tag record per slice row, same id, same order (adapters/__init__.py has the contract). A kind whose tag file
already matches its slice (same ids, same model) is skipped, so a rerun does only what is missing. Each file is
written to a .part file, checked row by row against the slice (ids, order, count, span texts, labels, mood ranges)
and only then renamed into place. tags/STATUS.json records the tagger, the model and rows, spans and seconds per
kind; tags/LABELS.json the label order. --limit N tags only the first N rows of each kind (a pilot).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "common"))
import adapters  # noqa: E402
import swarm_paths  # noqa: E402

CHUNK = 512  # rows handed to the adapter at a time


def read_jsonl(path, limit=None):
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if limit is not None and len(out) >= limit:
                break
            if line.strip():
                out.append(json.loads(line))
    return out


def write_json(path, obj):
    with open(path + ".tmp", "w") as f:
        json.dump(obj, f, indent=1, ensure_ascii=False)
    os.replace(path + ".tmp", path)


def check_file(tag_path, rows) -> list:
    """Problems of one tag file against its rows (the first 20)."""
    if not os.path.exists(tag_path):
        return [f"{os.path.basename(tag_path)} missing"]
    recs = read_jsonl(tag_path)
    probs = []
    if len(recs) != len(rows):
        probs.append(f"{os.path.basename(tag_path)}: {len(recs)} records for {len(rows)} rows")
    for rec, row in zip(recs, rows):
        why = adapters.check_record(rec, row)
        if why:
            probs.append(why)
            if len(probs) >= 20:
                break
    return probs


def matches(tag_path, rows, model) -> bool:
    if not os.path.exists(tag_path):
        return False
    try:
        recs = read_jsonl(tag_path)
    except ValueError:
        return False
    return len(recs) == len(rows) and all(r.get("id") == x.get("id") and r.get("model") == model
                                          for r, x in zip(recs, rows))


def tag_rows(ad, rows, out_path, kind, log):
    t0 = time.time()
    part = out_path + ".part"
    spans = moods = 0
    with open(part, "w", encoding="utf-8") as f:
        for k in range(0, len(rows), CHUNK):
            chunk = rows[k:k + CHUNK]
            res = ad.tag(chunk)
            if len(res) != len(chunk):
                raise SystemExit(f"{ad.name}: {len(res)} results for {len(chunk)} rows")
            for row, r in zip(chunk, res):
                rec = {"id": row["id"], "model": ad.model, "decode": ad.decode, "spans": r.get("spans") or []}
                if r.get("mood") is not None:
                    rec["mood"] = r["mood"]
                    moods += 1
                why = adapters.check_record(rec, row)
                if why:
                    raise SystemExit(f"{ad.name}: contract broken on {kind}: {why}")
                spans += len(rec["spans"])
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if len(rows) > CHUNK:
                log(f"  {kind}: {min(k + CHUNK, len(rows))}/{len(rows)} rows, {time.time() - t0:.0f} s")
    os.replace(part, out_path)
    secs = time.time() - t0
    chars = sum(len(r.get("text") or "") for r in rows)
    return {"rows": len(rows), "spans": spans, "mood_rows": moods, "chars": chars, "seconds": round(secs, 2),
            "rows_per_s": round(len(rows) / secs, 1) if secs else None,
            "chars_per_s": round(chars / secs) if secs else None}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--adapter", default="gliner_vad", help=f"one of {', '.join(sorted(adapters.ADAPTERS))}")
    ap.add_argument("--root", default=swarm_paths.work_default(), help="the work area (default $SWARM_WORK)")
    ap.add_argument("--slice", help="slice dir (default <root>/slice)")
    ap.add_argument("--out", help="tags dir (default <root>/tags)")
    ap.add_argument("--kinds", help="comma list of slice kinds (default every slice file)")
    ap.add_argument("--limit", type=int, help="tag only the first N rows of each kind (pilot)")
    ap.add_argument("--summaries", action="store_true", help="tag summaries/agent.jsonl and room.jsonl instead")
    ap.add_argument("--check", action="store_true", help="only check existing tag files against the slice")
    ap.add_argument("--force", action="store_true", help="tag again even where a matching file exists")
    ap.add_argument("--batch", type=int, default=16, help="texts per model batch")
    ap.add_argument("--cache", help="download cache for adapter resources (default $SWARM_CACHE or ~/.cache/swarm-reader)")
    ap.add_argument("--cmd", help="oneironer: the outside tag command (default $ONEIRONER_TAG_CMD)")
    a = ap.parse_args(argv)
    root = swarm_paths.need_work(a.root) if not (a.slice and a.out) else (a.root or os.path.dirname(a.out))
    a.slice = a.slice or os.path.join(root, "slice")
    a.out = a.out or os.path.join(root, "tags")
    os.makedirs(a.out, exist_ok=True)

    def log(msg):
        print(time.strftime("%H:%M:%S"), msg, flush=True)

    if a.summaries:
        inputs = {"summaries": []}
        for name in ("room.jsonl", "agent.jsonl"):
            path = os.path.join(root, "summaries", name)
            if os.path.exists(path):
                inputs["summaries"] += [{"id": r["id"], "text": r.get("text") or "", "kind": "summary"}
                                        for r in read_jsonl(path)]
        if not inputs["summaries"]:
            raise SystemExit(f"no summaries in {root}/summaries: run the summarizer first")
        if a.limit:
            inputs["summaries"] = inputs["summaries"][:a.limit]
    else:
        if not os.path.isdir(a.slice):
            raise SystemExit(f"no slice dir {a.slice}: slice the archive first")
        kinds = sorted(f[len("slice-"):-len(".jsonl")] for f in os.listdir(a.slice)
                       if f.startswith("slice-") and f.endswith(".jsonl"))
        if a.kinds:
            want = set(a.kinds.split(","))
            kinds = [k for k in kinds if k in want]
        inputs = {k: read_jsonl(os.path.join(a.slice, f"slice-{k}.jsonl"), a.limit) for k in kinds}

    if a.check:
        bad = []
        for kind, rows in inputs.items():
            bad += check_file(os.path.join(a.out, f"tags-{kind}.jsonl"), rows)
        for b in bad[:50]:
            print("ERROR", b)
        print(f"{'FAIL' if bad else 'OK'}: {len(inputs)} tag files checked against their slice, {len(bad)} problems")
        return 1 if bad else 0

    ad = adapters.get(a.adapter)
    agents_path = os.path.join(root, "meta", "agents.json")
    a.agent_names = []
    if os.path.exists(agents_path):
        with open(agents_path) as f:
            meta_agents = json.load(f)
        a.agent_names = [n for x in meta_agents for n in (x.get("name"), x.get("id")) if n]
    t0 = time.time()
    status_path = os.path.join(a.out, "STATUS.json")
    status = {}
    if os.path.exists(status_path):
        with open(status_path) as f:
            status = json.load(f)
    if status.get("adapter") not in (None, ad.name):
        status = {}  # another tagger made the old files: start the record again
    status.update({"tagger": ad.tagger, "adapter": ad.name, "model": ad.model, "decode": ad.decode,
                   "register": "row", "limit_per_kind": a.limit})
    per = status.setdefault("kinds", {})
    if hasattr(ad, "run_all"):
        ad.load(a)
        ad.run_all(a.slice, a.out, list(inputs), a.limit)
        bad = [p for kind, rows in inputs.items() for p in check_file(os.path.join(a.out, f"tags-{kind}.jsonl"), rows)]
        if bad:
            for b in bad[:20]:
                print("ERROR", b)
            raise SystemExit(f"{ad.name}: {len(bad)} contract problems in the files it wrote")
        for kind, rows in inputs.items():
            per[kind] = {"rows": len(rows)}
    else:
        todo = {k: rows for k, rows in inputs.items()
                if a.force or not matches(os.path.join(a.out, f"tags-{k}.jsonl"), rows, ad.model)}
        skipped = sorted(set(inputs) - set(todo))
        if skipped:
            log(f"already tagged with {ad.model}: {', '.join(skipped)}")
        if any(todo.values()):
            t_load = time.time()
            ad.load(a)
            status["load_seconds"] = round(time.time() - t_load, 1)
            log(f"{ad.name} loaded in {status['load_seconds']} s")
        for kind, rows in todo.items():
            out_path = os.path.join(a.out, f"tags-{kind}.jsonl")
            if not rows:
                open(out_path, "w").close()
                per[kind] = {"rows": 0, "spans": 0, "seconds": 0}
                continue
            per[kind] = tag_rows(ad, rows, out_path, kind, log)
            log(f"tagged {kind}: {per[kind]}")
    tagged = [v for v in per.values() if v.get("seconds")]
    secs = sum(v["seconds"] for v in tagged)
    rows = sum(v.get("rows", 0) for v in tagged)
    chars = sum(v.get("chars", 0) for v in tagged)
    status.update({"rows": sum(v.get("rows", 0) for v in per.values()), "spans": sum(v.get("spans", 0) for v in per.values()),
                   "tag_seconds": round(secs, 1), "rows_per_s": round(rows / secs, 1) if secs else None,
                   "chars_per_s": round(chars / secs) if secs else None,
                   "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    write_json(status_path, status)
    write_json(os.path.join(a.out, "LABELS.json"), list(ad.labels or adapters.LABELS))
    log(f"done in {time.time() - t0:.1f} s: {status['rows']} rows, {status['spans']} spans, "
        f"{status['rows_per_s']} rows/s, {status['chars_per_s']} chars/s (tagging only) -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
