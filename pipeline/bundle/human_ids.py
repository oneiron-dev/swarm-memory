#!/usr/bin/env python3
"""Find usernames, handles and email-like ids of individual humans for <lists>/extra_human_ids.json.

human_names.py lists display names; a person can also appear by a username or handle that is no display name
(a GitHub handle, an X account). This tool finds such ids so the build can redact them like rare names.
Everything it writes stays in the work area: never committed, never served. Prints counts only.
The work area is --root (default $SWARM_WORK); the lists are in --lists (default $SWARM_LISTS or <root>/meta).

  candidates  entity surfaces of a built bundle (text, label, count, aliases, one example of <= 200 chars)
  classify    a model (--endpoint, --prime-agent or --mock; pipeline/common/swarm_llm.py) marks each surface
              human_individual or other; a reply whose n_items is not the batch size, or that is malformed, is
              retried and never saved as done
  scan        handle-shaped tokens (@word, word-dev, word123, word_word) near human words in served text
  hits        numbered list of classifier hits (h<n>) and scan candidates (s<n>) for review, and a review template
              beside it (review-template.txt); refuses while classify has batches left (--allow-incomplete overrides)
  write       <lists>/extra_human_ids.json from the review file (one "<h|s><n> accept|reject <reason>" per line;
              every h<n> needs a verdict; an empty file is right when hits found nothing)
  redteam     fold a privacy red-team's leak rows ({file, ref_or_path, identifier, kind, context, confidence}, high
              and medium) into meta/extra_human_ids.json and meta/extra_human_rules.json; the review file names the
              exceptions, one "<exact|cap|at|drop|reject> <identifier> | <reason>" per line (default: any case)
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "common"))
import swarm_llm  # noqa: E402
import swarm_paths  # noqa: E402
from build_bundle import (PROFILE_RE, PROFILE_ROUTES, STOP_WORDS, SUBDOMAIN_RE, as_list, fold,  # noqa: E402
                          tokens_of)

MODEL = swarm_llm.DEFAULT_MODEL  # the real run's model; each saved batch records the model that answered
BATCH = 100
SNIPPET = 200
HUMAN_WORDS = re.compile(r"\b(?:humans?|volunteers?|contributors?|viewers?|authors?|followers?|users?|teams?|groups?|"
                         r"helpers?|maintainers?|creators?|owners?|members?|commenters?|donors?|fans?|people|person)\b",
                         re.I)
# handle shapes: @word; word-dev / word-bot style suffixes; letters followed by digits; snake_case
HANDLE_SHAPES = re.compile(r"(?<![\w@.+-])@[A-Za-z0-9_][A-Za-z0-9_.-]*[A-Za-z0-9_]"
                           r"|\b[a-z][a-z0-9]{2,}-(?:dev|bot|io|hq|app|codes?|labs?|ai)\b"
                           r"|\b[A-Za-z][A-Za-z]{2,}[0-9]{2,}\b"
                           r"|\b[a-z][a-z0-9]+_[a-z0-9_]+\b")
NEAR = 80  # characters either side

SYSTEM = "You label named-entity surfaces for a privacy filter. Reply with one JSON object and nothing else."
TASK = """Each stdin line is one entity found by a tagger in the logs of the AI Village, where AI agents work
together and humans sometimes chat with them. Fields: i (item number), text, label (the tagger's guess, often
wrong), count, aliases, example (one line where it occurs; [human] marks an already removed name).

Label an item human_individual when its text names or identifies one specific real human: a personal name (full,
first or last name), a username, handle, account or repository owner name of an individual, or an email-like id.
Public figures count. A handle counts even when the tagger labelled it ORG or PRODUCT.
Everything else is other: the AI agents listed below and any spelling of them, AI models and products, companies,
organisations, teams, communities, websites, places, events, dates, generic roles ("a human", "the viewer",
"volunteers"), fictional characters and named AI personas.

AI agents (never human_individual): {agents}

Reply with exactly {{"n_items": <number of lines read>, "human_individual": [{{"i": <n>, "why": "<at most 8 words>"}}, ...]}}
listing only the human_individual items (an empty list when there are none)."""


def read_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def snippet(text: str, needle: str, n: int = SNIPPET) -> str:
    """At most n characters of `text` around the first case-insensitive occurrence of `needle`."""
    text = " ".join((text or "").split())
    i = text.lower().find(needle.lower()) if needle else -1
    if i < 0 or len(text) <= n:
        return text[:n]
    s = max(0, min(i - n // 3, len(text) - n))
    return text[s:s + n]


def cmd_candidates(a):
    b = read_json(os.path.join(a.data, "bundle.json"))
    sources = {}
    out = []
    for i, e in enumerate(b["entities"], 1):
        ex = ""
        for ref, d in zip(e["examples"], e["example_days"]):
            if d not in sources:
                sources[d] = read_json(os.path.join(a.data, "sources", f"{d}.json"), {})
            row = sources[d].get(ref)
            if row:
                ex = snippet(row["text"], e["text"])
                break
        out.append({"i": i, "text": e["text"], "label": e["label"], "count": e["count"],
                    "aliases": e["aliases"][:3], "example": ex, "agent": e.get("agent")})
    os.makedirs(a.work, exist_ok=True)
    with open(os.path.join(a.work, "candidates.jsonl"), "w") as f:
        for r in out:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"candidates: {len(out)} entities ({sum(1 for r in out if r['agent'])} agent names, not sent)")


def call_luna(stdin_text: str, task: str, a) -> dict:
    """One classify call through the configured client: {"reply": the JSON object or None, "model", "in", "out"}.
    A model other than the one asked for stops the run."""
    try:
        res = a.llm.call(stdin_text, task, SYSTEM, json_mode=True, purpose="classify", cwd=getattr(a, "cwd", None))
    except swarm_llm.ModelMismatch as e:
        raise SystemExit(f"{e}: stopping")
    except swarm_llm.CallError as e:
        raise RuntimeError(str(e)[:300])
    m = re.search(r"\{.*\}", res["text"], re.S)
    return {"reply": json.loads(m.group(0)) if m else None, "model": res["model"], "in": res["in"], "out": res["out"]}


def cmd_classify(a):
    with open(os.path.join(a.work, "candidates.jsonl")) as f:
        rows = [json.loads(l) for l in f]
    if getattr(a, "llm", None) is None:
        a.llm = swarm_llm.from_args(a)
    agents = as_list(read_json(os.path.join(a.root, "meta", "agents.json")), "agents")
    task = TASK.format(agents=", ".join(f"{r.get('name')} ({r.get('id')})" for r in agents))
    send = [r for r in rows if not r["agent"]]
    batches = [send[k:k + BATCH] for k in range(0, len(send), BATCH)]
    a.cwd = os.path.join(a.work, ".cwd")
    os.makedirs(a.cwd, exist_ok=True)
    done_path = os.path.join(a.work, "classified.jsonl")
    done, redo = {}, set()
    if os.path.exists(done_path):
        with open(done_path) as f:
            saved = [json.loads(l) for l in f]
        for r in saved:
            if complete(r, batches):
                done[r["batch"]] = r
                redo.discard(r["batch"])
            elif r["batch"] not in done:
                redo.add(r["batch"])
    if redo:
        print(f"classify: {len(redo)} saved batches are incomplete or malformed: running them again", flush=True)

    def run(k):
        """One batch through Luna; a reply that does not account for every item is retried, never kept."""
        items = batches[k]
        stdin = "\n".join(json.dumps({x: r[x] for x in ("i", "text", "label", "count", "aliases", "example")},
                                     ensure_ascii=False) for r in items)
        for attempt in range(a.attempts):
            t0 = time.time()
            try:
                res = call_luna(stdin, task, a)
            except (RuntimeError, ValueError) as e:
                print(f"batch {k}: attempt {attempt + 1} failed ({e.__class__.__name__})", flush=True)
                continue
            reply = res["reply"]
            r = {"batch": k, "items": len(items), "n_items": reply.get("n_items") if isinstance(reply, dict) else None,
                 "hits": reply.get("human_individual") if isinstance(reply, dict) else None,
                 "model": res["model"], "in": res["in"], "out": res["out"], "seconds": round(time.time() - t0, 1)}
            if complete(r, batches):
                return r
            print(f"batch {k}: attempt {attempt + 1} incomplete or malformed (n_items {r['n_items']!r} of "
                  f"{len(items)}): retrying", flush=True)
        print(f"batch {k}: no complete reply in {a.attempts} attempts: not saved", flush=True)
        return None

    todo = [k for k in range(len(batches)) if k not in done]
    failed = 0
    with concurrent.futures.ThreadPoolExecutor(a.parallel) as pool, open(done_path, "a") as f:
        for r in pool.map(run, todo):
            if r is None:
                failed += 1
                continue
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            f.flush()
            done[r["batch"]] = r
            print(f"batch {r['batch']}: {r['items']} items, {len(r['hits'])} hits, {r['in']}+{r['out']} tok, "
                  f"{r['seconds']} s", flush=True)
    tot = list(done.values())
    print(f"classify: {len(tot)}/{len(batches)} batches complete, {sum(r['items'] for r in tot)} items, "
          f"{sum(len(r['hits']) for r in tot)} human_individual, tokens {sum(r['in'] for r in tot)} in "
          f"{sum(r['out'] for r in tot)} out, {sum(r['seconds'] for r in tot):.0f} s summed")
    if failed or len(tot) < len(batches):
        raise SystemExit(f"classify: {len(batches) - len(tot)} batches incomplete: run classify again")


def complete(r: dict, batches: list) -> bool:
    """A saved or fresh batch result that accounts for every item of its batch: n_items equals the batch size and
    every hit is an object naming an item of that batch."""
    k = r.get("batch")
    if not isinstance(k, int) or not 0 <= k < len(batches) or r.get("items") != len(batches[k]):
        return False
    ids = {x["i"] for x in batches[k]}
    hits = r.get("hits")
    return (r.get("n_items") == len(batches[k]) and isinstance(hits, list)
            and all(isinstance(h, dict) and h.get("i") in ids for h in hits))


def served_strings(data: str):
    for root, _, files in os.walk(data):
        for fn in files:
            if not fn.endswith(".json"):
                continue
            rel = os.path.relpath(os.path.join(root, fn), data)
            stack = [read_json(os.path.join(root, fn))]
            while stack:
                x = stack.pop()
                if isinstance(x, str):
                    yield rel, x
                elif isinstance(x, dict):
                    stack.extend(x.values())
                elif isinstance(x, list):
                    stack.extend(x)


def cmd_scan(a):
    agents = as_list(read_json(os.path.join(a.root, "meta", "agents.json")), "agents")
    agent_toks = {tokens_of(f) for r in agents for f in (r.get("name") or "", r.get("id") or "")}
    found = collections.defaultdict(lambda: {"n": 0, "near_human": 0, "files": set(), "ctx": []})
    for rel, s in served_strings(a.data):
        for m in HANDLE_SHAPES.finditer(s):
            tok = m.group(0)
            toks = tokens_of(tok)
            if toks in agent_toks or all(t in STOP_WORDS or t.isdigit() for t in toks):
                continue
            f = found[tok.lower()]
            f["n"] += 1
            f["files"].add(rel.split("/")[0])
            ctx = s[max(0, m.start() - NEAR):m.end() + NEAR]
            if HUMAN_WORDS.search(ctx) or tok.startswith("@"):
                f["near_human"] += 1
                if len(f["ctx"]) < 2:
                    f["ctx"].append(" ".join(ctx.split()))
    keep = sorted(((k, v) for k, v in found.items() if v["near_human"]), key=lambda kv: -kv[1]["n"])
    write_json(os.path.join(a.work, "scan.json"),
               [{"text": k, "n": v["n"], "near_human": v["near_human"], "files": sorted(v["files"]), "ctx": v["ctx"]}
                for k, v in keep])
    print(f"scan: {len(found)} handle-shaped tokens, {len(keep)} near a human word or @-prefixed")


def classify_left(work: str) -> tuple:
    """(batches classify must still finish, batches in all) for the candidates in `work`: what `hits` needs before
    "no hits" can mean "nothing found"."""
    path = os.path.join(work, "candidates.jsonl")
    if not os.path.exists(path):
        return None, 0
    with open(path) as f:
        rows = [json.loads(l) for l in f if l.strip()]
    send = [r for r in rows if not r.get("agent")]
    batches = [send[k:k + BATCH] for k in range(0, len(send), BATCH)]
    done = set()
    saved = os.path.join(work, "classified.jsonl")
    if os.path.exists(saved):
        with open(saved) as f:
            for l in f:
                if l.strip():
                    r = json.loads(l)
                    if complete(r, batches):
                        done.add(r["batch"])
    return len(batches) - len(done), len(batches)


def cmd_hits(a):
    left, total = classify_left(a.work)
    if left is None:
        raise SystemExit(f"hits: no {a.work}/candidates.jsonl: run `candidates` first")
    if left and not a.allow_incomplete:
        raise SystemExit(f"hits: classify finished {total - left} of {total} batches; run `classify` until it "
                         f"reports every batch complete (or pass --allow-incomplete to list the partial hits anyway). "
                         f"Without it, \"0 hits\" would mean \"not checked\", not \"clean\".")
    cands = {json.loads(l)["i"]: json.loads(l) for l in open(os.path.join(a.work, "candidates.jsonl"))}
    # the union of every classify run given (same candidates), and of every saved reply, an incomplete one too
    whys = collections.defaultdict(list)
    for work in [a.work] + (a.merge or []):
        if not os.path.exists(os.path.join(work, "classified.jsonl")):
            continue
        for l in open(os.path.join(work, "classified.jsonl")):
            for h in json.loads(l)["hits"]:
                whys[h["i"]].append(h.get("why"))
    hits = []
    for i, why in whys.items():
        c = cands[i]
        hits.append({"text": c["text"], "label": c["label"], "count": c["count"], "aliases": c["aliases"],
                     "why": why, "runs": len(why), "example": c["example"]})
    hits.sort(key=lambda h: -h["count"])
    out = {f"h{k}": h for k, h in enumerate(hits, 1)}
    for k, s in enumerate(read_json(os.path.join(a.work, "scan.json"), []), 1):
        out[f"s{k}"] = s
    write_json(os.path.join(a.work, "hits.json"), out)
    with open(os.path.join(a.work, "review-template.txt"), "w") as f:
        f.write("# One line per hit: <key> accept <reason> | <key> reject <reason>. Every h<n> needs a verdict;\n"
                "# s<n> (scan candidates) are optional. An id you found yourself: + accept <id> | <reason>.\n"
                "# Copy this file to review.txt, decide each line, then run `write --review <file>`.\n")
        for k, h in out.items():
            f.write(f"# {k}: {h.get('text')!r} ({h.get('label', 'scan')}, {h.get('count', h.get('n'))}x) "
                    f"{(h.get('example') or ' | '.join(h.get('ctx') or []))[:160]!r}\n")
            if k.startswith("h"):
                f.write(f"{k} reject <reason>\n")
    print(f"hits: {len(hits)} classifier hits, {len(out) - len(hits)} scan candidates -> {a.work}/hits.json "
          f"(review template: {a.work}/review-template.txt)")


def cmd_write(a):
    hits = read_json(os.path.join(a.work, "hits.json"))
    if hits is None:
        raise SystemExit(f"write: no {a.work}/hits.json: run `hits` first")
    if not a.review or not os.path.exists(a.review):
        raise SystemExit(f"write: no review file {a.review!r}. Start from {a.work}/review-template.txt: one line per "
                         f"hit, \"h1 accept <reason>\" or \"h1 reject <reason>\"; when hits found nothing, an empty "
                         f"file is the review (touch it)")
    accept, reject, extra = [], [], []
    for line in open(a.review):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, verdict, *why = line.split(None, 2)
        if key.startswith("+"):  # an id the review found itself, given in the line: "+ accept <id> | <reason>"
            ident, _, reason = (why[0] if why else "").partition("|")
            extra.append(ident.strip())
            accept.append(key)
            continue
        if key not in hits or verdict not in ("accept", "reject") or not why:
            raise SystemExit(f"bad review line: {key} {verdict}")
        (accept if verdict == "accept" else reject).append(key)
        if verdict == "accept":
            extra.append(hits[key]["text"].lstrip("@"))
    missing = [k for k in hits if k.startswith("h") and k not in accept and k not in reject]
    if missing:
        raise SystemExit(f"{len(missing)} classifier hits have no verdict")
    ids = sorted({" ".join(x.split()) for x in extra if x.strip()}, key=str.lower)
    write_json(os.path.join(a.lists, "extra_human_ids.json"), ids)
    print(f"write: {len(ids)} ids ({len(accept)} accepted, {len(reject)} rejected) -> {a.lists}/extra_human_ids.json")


SLUG_HOSTS = ("telegra.ph", "prnewswire.com", "medium.com", "wordpress.com/20")  # the post slug carries the name


def url_id(ident: str) -> str:
    """The part of a URL leak that names the person: a profile account, a personal subdomain, a post slug on a
    publishing host (cut before any [token]), or a personal domain's first label (the whole host when that label
    is a common word)."""
    m = PROFILE_RE.search(ident) or SUBDOMAIN_RE.search(ident)
    if m:
        return m.group(1)
    host = re.sub(r"^(?:https?://)?(?:www\.)?", "", ident).split("/")[0]
    if any(h in ident for h in SLUG_HOSTS):
        slug = [p for p in re.split(r"[/?#]", ident.split("://")[-1])[1:] if p][-1:]
        if slug:
            return re.sub(r"\.html?$", "", slug[0].split("[")[0]).strip("-_")
    label = host.split(".")[0]
    from wordfreq import zipf_frequency
    return host if len(label) < 4 or zipf_frequency(label, "en") >= 3.0 else label


def leak_path(r: dict, data: str):
    """A leak row's served file and its location as '$["k"][n]...': the row gives '$[...]' or an RFC 6901 pointer
    ('/k/3/text @chars 10:15'); pointer segments are typed against the served file."""
    rel = r["file"].split("/data/", 1)[-1]
    path = r["ref_or_path"].split(" @chars")[0]
    if path.startswith("$"):
        return rel, path
    doc = read_json(os.path.join(data, rel))
    out = []
    for seg in path.lstrip("/").split("/") if path.strip("/") else []:
        seg = seg.replace("~1", "/").replace("~0", "~")
        key = int(seg) if isinstance(doc, list) and seg.isdigit() else seg
        out.append(key)
        try:
            doc = doc[key]
        except (KeyError, IndexError, TypeError):
            doc = None
    return rel, "$" + "".join(f"[{json.dumps(k, ensure_ascii=False)}]" for k in out)


def cmd_redteam(a):
    if not a.leaks:
        raise SystemExit("redteam needs --leaks <the red-team's leak rows, JSONL> (SKILL.md section 5.5)")
    if not os.path.isfile(a.leaks):
        raise SystemExit(f"no leaks file at {a.leaks}: the red-team writes one JSON row per leak it finds there "
                         f"(SKILL.md section 5.5). A clean round is an empty file: mkdir -p "
                         f"{os.path.dirname(a.leaks) or '.'} && : > {a.leaks}")
    for path, what in ((a.review, "--review"), (a.keep, "--keep")):
        if path and not os.path.isfile(path):
            raise SystemExit(f"no {what} file at {path}: create it (an empty review file accepts every high and "
                             f"medium row) or leave {what} out")
    rows = [json.loads(l) for l in open(a.leaks) if l.strip()]
    rows = [r for r in rows if r.get("confidence") in ("high", "medium")]
    keep = [k.strip() for k in open(a.keep)] if a.keep else []  # public figures: never redacted
    keep = [k for k in keep if k and not k.startswith("#")]
    keep_low = {k.lower() for k in keep}
    verdicts, reasons = {}, {}
    for line in open(a.review) if a.review else []:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        verdict, _, rest = line.partition(" ")
        ident, _, why = rest.partition("|")
        if verdict not in ("exact", "cap", "at", "drop", "reject") or not ident.strip() or not why.strip():
            raise SystemExit(f"bad review line ({verdict})")
        verdicts[ident.strip()] = verdict
    extra = set(read_json(os.path.join(a.lists, "extra_human_ids.json"), []))
    rules = read_json(os.path.join(a.lists, "extra_human_rules.json"), {}) or {}
    exact, ids, drop = set(rules.get("exact", [])), set(rules.get("ids", [])), set(rules.get("drop_entities", []))
    cap = set(rules.get("cap", []))
    at = {(r["file"], r["path"], r["token"]) for r in rules.get("at", [])}
    added = collections.Counter()
    for r in rows:
        ident, kind = r["identifier"].strip(), r["kind"]
        verdict = verdicts.get(ident)
        bare = ident[2:] if ident.startswith("e:") else ident.lstrip("@")
        if ident.lower() in keep_low or bare.lower() in keep_low:
            added["kept_public_figure"] += 1
        elif verdict == "reject":
            added["rejected"] += 1
        elif verdict in ("at", "drop"):
            rel, path = leak_path(r, a.data)
            if rel.startswith(("sources/", "summaries/", "memory/")) and path.endswith(('["text"]', '["excerpt"]')):
                at.add((rel, path, bare))
                added["at"] += 1
            if verdict == "drop" or rel == "bundle.json" or rel.startswith("entities/") or '["entities"]' in r["ref_or_path"]:
                drop.add(fold(bare.replace("-", " ") if ident.startswith("e:") else bare))
                added["drop"] += 1
        elif verdict == "exact":
            exact.add(bare)
            added["exact"] += 1
        elif verdict == "cap":
            cap.add(bare)
            added["cap"] += 1
        elif kind == "email":
            added["email_rule"] += 1  # the percent-encoded email rule covers it
        elif kind == "url":
            extra.add(url_id(ident))
            added["url"] += 1
        elif kind in ("phone", "other") and not re.search(r"[A-Za-z]{3,}", ident):
            ids.add(ident)
            added["ids"] += 1
        else:
            extra.add(bare)
            added["any_case"] += 1
    extra |= set(a.add)
    extra = sorted({" ".join(x.split()) for x in extra if x.strip()}, key=str.lower)
    # profile accounts on the served site that the red-team did not flag (orgs, agents, projects) stay
    flagged = " ".join(json.loads(l)["identifier"].lower() for l in open(a.leaks) if l.strip())
    low_extra = {x.lower() for x in extra}
    allow = set(rules.get("profile_allow", []))
    for _, s in served_strings(a.data):
        if "/" not in s and "." not in s:
            continue
        for m in list(PROFILE_RE.finditer(s)) + list(SUBDOMAIN_RE.finditer(s)):
            seg = m.group(1).lower()
            if seg not in flagged and seg not in low_extra and seg not in PROFILE_ROUTES and seg != "human":
                allow.add(seg)
    write_json(os.path.join(a.lists, "extra_human_ids.json"), extra)
    write_json(os.path.join(a.lists, "extra_human_rules.json"),
               {"exact": sorted(exact), "cap": sorted(cap), "ids": sorted(ids),
                "keep": sorted(set(rules.get("keep", [])) | set(keep), key=str.lower), "drop_entities": sorted(drop), "profile_allow": sorted(allow),
                "at": [{"file": f, "path": p, "token": t} for f, p, t in sorted(at)]})
    print(f"redteam: {len(rows)} high/medium rows; {dict(added)}; extra ids {len(extra)}, exact {len(exact)}, "
          f"cap {len(cap)}, ids {len(ids)}, at {len(at)}, drop {len(drop)}, profile allow {len(allow)}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=("candidates", "classify", "scan", "hits", "write", "redteam"))
    ap.add_argument("--root", default=swarm_paths.work_default(), help="the work area (default $SWARM_WORK)")
    ap.add_argument("--lists", help="redaction-list dir (default $SWARM_LISTS or <root>/meta)")
    ap.add_argument("--data", help="built bundle data dir (default <root>/bundle/data)")
    ap.add_argument("--work", help="work dir (default <root>/human_ids)")
    ap.add_argument("--review", help="review file for `write` and `redteam`")
    ap.add_argument("--leaks", help="`redteam`: the red-team's leak rows (JSONL)")
    ap.add_argument("--add", action="append", default=[], help="`redteam`: one more reviewed id, any case (repeatable)")
    ap.add_argument("--keep", help="`redteam`: file of public figures to keep visible, one per line")
    ap.add_argument("--merge", action="append", help="`hits`: also the classify results in this work dir (repeatable)")
    ap.add_argument("--allow-incomplete", action="store_true", help="`hits`: list hits while classify has batches left")
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--attempts", type=int, default=4, help="`classify`: tries per batch before it is left undone")
    swarm_llm.add_args(ap, timeout=600)
    a = ap.parse_args(argv)
    a.root = swarm_paths.need_work(a.root)
    a.lists = swarm_paths.lists_dir(a.root, a.lists)
    a.data = a.data or os.path.join(a.root, "bundle", "data")
    a.work = a.work or os.path.join(a.root, "human_ids")
    a.review = a.review or (os.path.join(a.work, "review.txt") if a.step == "write" else None)
    if a.step in ("write", "redteam"):
        os.makedirs(a.lists, exist_ok=True)
    os.makedirs(a.work, exist_ok=True)
    {"candidates": cmd_candidates, "classify": cmd_classify, "scan": cmd_scan, "hits": cmd_hits,
     "write": cmd_write, "redteam": cmd_redteam}[a.step](a)


if __name__ == "__main__":
    main()
