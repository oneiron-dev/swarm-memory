#!/usr/bin/env python3
"""A third privacy sweep over the served bundle with openai/privacy-filter (Apache-2.0).

The model is a token classifier (classes private_person, private_email, private_phone, private_url,
private_address, account_number, private_date, secret). It misses uncommon names and over-redacts public entities,
so its hits are candidates for review, never verdicts. Everything this tool writes stays in the work area (--root,
default $SWARM_WORK): nothing here is served or committed. Prints counts only.

  collect   every string value, object key and file name of a built bundle, deduped, each with its locations
            (`<file>#$["k"][0]` for a value, `<file>#$["k"]#key` for a key of the object at that path,
            `<file>#name` for a file name)
  run       the model over those strings (Apple Silicon only: MLX): strings over 4,000 characters are split on sentence
            ends; constrained Viterbi decoding at the model's default operating point (all transition biases 0)
  filter    drop agent and model names, redaction tokens, the builder's public keep lists and allowlists, dates,
            URLs without an account path and city/country-only addresses; check person spans against the
            redaction lists (--lists, default $SWARM_LISTS or <root>/meta)
  classify  distinct new person, account and secret spans through the model exactly as human_ids.py classify
            does (--endpoint, --prime-agent or --mock; batches of 100; the reply's model verified)
  review    numbered list of the classifier's human_individual hits (p<n>) with one context each, for a manual
            verdict; refuses while a classify round is unfinished or candidates were never sent (--allow-incomplete)
  write     <lists>/pf_candidates.json from the review file (one "p<n> accept|reject <reason>" per line), the
            accepted ids merged into <lists>/extra_human_ids.json, the kept hits as JSONL and the summary as Markdown
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys
import time

BUNDLE_CODE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bundle")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "common"))
import swarm_llm  # noqa: E402
import swarm_paths  # noqa: E402
MAX_CHARS = 4000
CONTEXT = 120  # characters of context per span (half either side)
TOKEN_BUDGET = 16384  # tokens per forward batch
SCORE_BUDGET = 16_000_000  # batch * length^2: bounds the attention score memory
PAD_ID = 199999

PRODUCT_WORDS = frozenset("""
claude gpt chatgpt gemini grok opus sonnet haiku o1 o3 o4 deepseek kimi glm qwen llama mistral codex luna sol
openai anthropic google deepmind xai moonshot zai meta gemma phi copilot bard
""".split())
MODEL_SUFFIXES = frozenset("pro flash mini nano turbo preview thinking high low max fast lite exp instruct chat r1 v3".split())
REDACTION_TOKENS = re.compile(r"\[(?:human|email|id|REDACTED|BLOB_REMOVED|IMAGE_REMOVED)\]", re.I)
SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")
PUNCT = " \t\r\n\"'`.,;:!?()[]{}<>*_~|/\\-–—‘’“”«»…"
STREET_WORDS = re.compile(r"(?i)\b(?:street|st|avenue|ave|road|rd|boulevard|blvd|lane|ln|drive|dr|way|court|ct|place|"
                          r"pl|suite|ste|apt|apartment|unit|floor|fl|po box|p\.o\.|square|sq|highway|hwy|terrace|"
                          r"crescent|close|parkway|pkwy|building|bldg|chome|ku|cho)\b")
USER_PATH = re.compile(r"(?i)substack|linkedin|@")
# a whole served string that is a schema identifier: a source ref, a summary or entity id, a uuid or hash, a timestamp
STRUCT_ID = re.compile(r"(?i)^(?:(?:chat|ev|cc|cus|ccs|mem|sum):\S+|(?:village|agent|room)-day:[\w.:-]+|e:[\w.-]+|"
                       r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{16,64}|"
                       r"\d{4}-\d{2}-\d{2}(?:[T ][\d:.]+Z?)?|\d+)$")
TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?Z?)?$")
SHAPE_KEY = re.compile(r"^[a-z][a-z0-9_]*$")


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


def read_jsonl(path):
    with open(path) as f:
        return [json.loads(l) for l in f if l.strip()]


def jpath(path) -> str:
    return "$" + "".join(f"[{json.dumps(k, ensure_ascii=False)}]" for k in path)


def norm(text: str) -> str:
    """Comparison key: case-folded, whitespace folded, trimmed of punctuation."""
    return " ".join(text.casefold().split()).strip(PUNCT)


def file_type(loc: str) -> str:
    """bundle.json, findings.json, entities, memory, sources, summaries/agent, summaries/room, summaries/village."""
    rel = loc.split("#", 1)[0]
    parts = rel.split("/")
    if len(parts) == 1:
        return parts[0]
    return "/".join(parts[:2]) if parts[0] == "summaries" and len(parts) > 2 else parts[0]


def path_shape(loc: str) -> str:
    """A location with its variable parts masked: file stems, list indices and data-valued keys."""
    rel, _, rest = loc.partition("#")
    parts = rel.split("/")
    if len(parts) > 1:
        parts[-1] = "<name>.json"
    shape = "/".join(parts)
    if rest == "name":
        return shape + "#name"
    tail = "#key" if rest.endswith("#key") else ""
    path = rest[:-4] if tail else rest
    keys = re.findall(r'\[(\d+|"(?:[^"\\]|\\.)*")\]', path)
    out = []
    for k in keys:
        if k.isdigit():
            out.append("[*]")
        else:
            s = json.loads(k)
            out.append(f'["{s}"]' if SHAPE_KEY.match(s) else "[<key>]")
    return shape + "#$" + "".join(out) + tail


# ---------------------------------------------------------------- collect

def bundle_strings(data: str):
    """(text, location) for every string value, object key and file name of the JSON files under `data`."""
    for root, dirs, files in os.walk(data):
        dirs.sort()
        for fn in sorted(files):
            if not fn.endswith(".json"):
                continue
            rel = os.path.relpath(os.path.join(root, fn), data)
            yield fn[:-5], f"{rel}#name"
            stack = [((), read_json(os.path.join(root, fn)))]
            while stack:
                path, x = stack.pop()
                if isinstance(x, str):
                    yield x, f"{rel}#{jpath(path)}"
                elif isinstance(x, dict):
                    for k in reversed(list(x)):
                        stack.append((path + (k,), x[k]))
                    for k in x:
                        yield k, f"{rel}#{jpath(path)}#key"
                elif isinstance(x, list):
                    for i in range(len(x) - 1, -1, -1):
                        stack.append((path + (i,), x[i]))


def cmd_collect(a):
    seen = {}
    total = 0
    by_kind = collections.Counter()
    for text, loc in bundle_strings(a.data):
        total += 1
        by_kind["name" if loc.endswith("#name") else "key" if loc.endswith("#key") else "value"] += 1
        if not text.strip():
            continue
        seen.setdefault(text, []).append(loc)
    os.makedirs(a.work, exist_ok=True)
    chars = 0
    with open(os.path.join(a.work, "strings.jsonl"), "w") as f:
        for sid, (text, locs) in enumerate(seen.items()):
            chars += len(text)
            f.write(json.dumps({"sid": sid, "text": text, "locs": locs}, ensure_ascii=False) + "\n")
    stats = {"strings": total, "by_kind": dict(by_kind), "distinct": len(seen), "chars": chars}
    write_json(os.path.join(a.work, "collect.json"), stats)
    print(f"collect: {total} strings ({by_kind['value']} values, {by_kind['key']} keys, {by_kind['name']} file names), "
          f"{len(seen)} distinct non-blank, {chars} characters")


# ---------------------------------------------------------------- run

def split_text(text: str, n: int = MAX_CHARS) -> list:
    """(offset, piece) pieces of at most n characters, cut on sentence ends (or, inside one over-long sentence, on
    the last whitespace; or hard at n)."""
    if len(text) <= n:
        return [(0, text)]
    cuts = []
    for m in SENTENCE_END.finditer(text):
        cuts.append(m.end())
    cuts.append(len(text))
    out, start, last = [], 0, 0
    for c in cuts:
        if c - start <= n:
            last = c
            continue
        if last > start:
            out.append((start, text[start:last]))
            start = last
        while c - start > n:
            ws = text.rfind(" ", start + 1, start + n)
            end = ws if ws > start else start + n
            out.append((start, text[start:end]))
            start = end
        last = c
    if start < len(text):
        out.append((start, text[start:]))
    return [(o, p) for o, p in out if p.strip()]


class Decoder:
    """Constrained BIOES Viterbi over per-token log-probabilities, the official opf decoder at the default operating
    point (every transition bias 0): B/S/O may start, E/S/O may end, B/I continue only into I/E of the same class."""

    def __init__(self, id2label: dict):
        import numpy as np
        self.np = np
        labels = [id2label[str(i)] if str(i) in id2label else id2label[i] for i in range(len(id2label))]
        self.labels = labels
        n = len(labels)
        tag = [None if l == "O" else l[0] for l in labels]
        cls = [None if l == "O" else l[2:] for l in labels]
        self.tag, self.cls = tag, cls
        neg = -1e9
        self.start = np.full(n, neg, np.float32)
        self.end = np.full(n, neg, np.float32)
        self.trans = np.full((n, n), neg, np.float32)
        for i in range(n):
            if tag[i] in (None, "B", "S"):
                self.start[i] = 0.0
            if tag[i] in (None, "E", "S"):
                self.end[i] = 0.0
            for j in range(n):
                if tag[i] in (None, "E", "S"):
                    ok = tag[j] in (None, "B", "S")
                else:
                    ok = tag[j] in ("I", "E") and cls[j] == cls[i]
                if ok:
                    self.trans[i, j] = 0.0
        self.valid = self.trans > neg / 2

    def decode(self, lp):
        """Label ids for one [T, n] log-probability array. A valid argmax path is the constrained optimum, so the
        Viterbi pass runs only when the argmax path breaks a constraint."""
        np = self.np
        path = lp.argmax(1)
        T = len(path)
        if T == 0:
            return path
        if (self.start[path[0]] == 0 and self.end[path[-1]] == 0
                and (T == 1 or self.valid[path[:-1], path[1:]].all())):
            return path
        score = lp[0] + self.start
        bp = np.empty((T - 1, lp.shape[1]), np.int32)
        for t in range(1, T):
            cand = score[:, None] + self.trans
            bp[t - 1] = cand.argmax(0)
            score = cand.max(0) + lp[t]
        score = score + self.end
        out = np.empty(T, np.int64)
        out[-1] = score.argmax()
        for t in range(T - 2, -1, -1):
            out[t] = bp[t, out[t + 1]]
        return out

    def spans(self, path, lp, offsets):
        """(char start, char end, class, score) per decoded span; score = exp(mean log-prob of the decoded labels)."""
        np = self.np
        out, i, T = [], 0, len(path)
        while i < T:
            t = self.tag[path[i]]
            if t in ("B", "S"):
                j = i
                if t == "B":
                    while j + 1 < T and self.tag[path[j + 1]] in ("I", "E") and self.cls[path[j + 1]] == self.cls[path[i]]:
                        j += 1
                        if self.tag[path[j]] == "E":
                            break
                lps = lp[np.arange(i, j + 1), path[i:j + 1]]
                out.append((offsets[i][0], offsets[j][1], self.cls[path[i]], float(np.exp(lps.mean()))))
                i = j + 1
            else:
                i += 1
        return out


def batches_of(units: list, lengths: list):
    """Index batches by descending length under the token and score budgets."""
    order = sorted(range(len(units)), key=lambda k: -lengths[k])
    batch, top = [], 0
    for k in order:
        L = max(top, lengths[k])
        if batch and ((len(batch) + 1) * L > TOKEN_BUDGET or (len(batch) + 1) * L * L > SCORE_BUDGET):
            yield batch
            batch, L = [], lengths[k]
        batch.append(k)
        top = L
    if batch:
        yield batch


def cmd_run(a):
    try:
        import mlx.core as mx  # noqa: F401
    except ImportError:
        raise SystemExit("run: needs MLX, which runs on Apple Silicon only (pip install -r pipeline/pf/requirements.txt "
                         "on a Mac with an M-series chip). Elsewhere, run openai/privacy-filter with its own PyTorch "
                         "tooling and write <work>/raw.jsonl in the same shape, or skip this sweep and say so.")
    if not a.model or not os.path.isdir(a.model):
        raise SystemExit("run: --model must be a local checkout of mlx-community/openai-privacy-filter-bf16 "
                         "(huggingface-cli download mlx-community/openai-privacy-filter-bf16 --local-dir <dir>)")
    import mlx.core as mx
    import numpy as np
    from mlx_embeddings.utils import load
    from tokenizers import Tokenizer

    t0 = time.time()
    model, _ = load(a.model)
    tok = Tokenizer.from_file(os.path.join(a.model, "tokenizer.json"))
    cal = read_json(os.path.join(a.model, "viterbi_calibration.json"), {})
    biases = (cal.get("operating_points", {}).get("default", {}).get("biases", {}))
    if any(v != 0 for v in biases.values()):
        raise SystemExit("default operating point has non-zero transition biases: this decoder assumes zero")
    dec = Decoder(model.config.id2label)
    rows = read_jsonl(os.path.join(a.work, "strings.jsonl"))
    if a.limit:
        rows = rows[:a.limit]
    units = [(r["sid"], off, piece) for r in rows for off, piece in split_text(r["text"])]
    encs = tok.encode_batch([u[2] for u in units], add_special_tokens=False)
    lengths = [max(1, len(e.ids)) for e in encs]
    ntok = sum(len(e.ids) for e in encs)
    print(f"run: {len(rows)} strings, {len(units)} units, {ntok} tokens; load+tokenize {time.time() - t0:.1f} s",
          flush=True)

    def forward(batch):
        L = max(lengths[k] for k in batch)
        ids = np.full((len(batch), L), PAD_ID, np.int32)
        am = np.zeros((len(batch), L), np.int32)
        for r, k in enumerate(batch):
            n = len(encs[k].ids)
            ids[r, :n] = encs[k].ids
            am[r, :n] = 1
        logits = model(mx.array(ids), attention_mask=mx.array(am)).logits.astype(mx.float32)
        lp = logits - mx.logsumexp(logits, axis=-1, keepdims=True)
        mx.async_eval(lp)
        return lp

    t1 = time.time()
    done_tok, n_spans, viterbi_runs = 0, 0, 0
    plan = list(batches_of(units, lengths))
    with open(os.path.join(a.work, "raw.jsonl"), "w") as f:
        pending = forward(plan[0]) if plan else None
        for b, batch in enumerate(plan):
            lp_all = np.array(pending)
            pending = forward(plan[b + 1]) if b + 1 < len(plan) else None
            for r, k in enumerate(batch):
                e = encs[k]
                n = len(e.ids)
                if n == 0:
                    continue
                lp = lp_all[r, :n]
                path = dec.decode(lp)
                sid, off, piece = units[k]
                for s, en, cls, score in dec.spans(path, lp, e.offsets):
                    while s < en and piece[s].isspace():
                        s += 1
                    while en > s and piece[en - 1].isspace():
                        en -= 1
                    if en <= s:
                        continue
                    f.write(json.dumps({"sid": sid, "s": off + s, "e": off + en, "label": cls,
                                        "score": round(score, 4), "text": piece[s:en]}, ensure_ascii=False) + "\n")
                    n_spans += 1
                done_tok += n
            if b % 50 == 0 or b + 1 == len(plan):
                dt = time.time() - t1
                print(f"batch {b + 1}/{len(plan)}: {done_tok} tokens, {n_spans} spans, {done_tok / max(dt, 1e-9):.0f} "
                      f"tok/s", flush=True)
    dt = time.time() - t1
    stats = {"runtime": "mlx", "model": os.path.basename(os.path.normpath(a.model)), "strings": len(rows),
             "units": len(units), "tokens": ntok, "batches": len(plan), "spans": n_spans,
             "seconds": round(dt, 1), "tokens_per_second": round(ntok / max(dt, 1e-9)),
             "seconds_total": round(time.time() - t0, 1), "operating_point": "default (all transition biases 0)"}
    write_json(os.path.join(a.work, "run.json"), stats)
    print(f"run: {ntok} tokens in {dt:.0f} s = {stats['tokens_per_second']} tok/s, {n_spans} spans")


# ---------------------------------------------------------------- filter

def load_lists(root: str, data: str, lists: str | None = None):
    sys.path.insert(0, BUNDLE_CODE)
    from build_bundle import PROFILE_ALLOW, PROFILE_ROUTES, PUBLIC_HANDLES, load_extra_ids, load_human_names, load_rules
    meta = lists or os.path.join(root, "meta")
    rules = load_rules(os.path.join(meta, "extra_human_rules.json"))
    bundle = read_json(os.path.join(data, "bundle.json"))
    agents = [r for r in bundle["agents"] if r.get("id") != "human"]
    parts = [[p for p in re.split(r"[\W_]+", x) if p] for x in rules.get("keep", [])]
    alts = sorted((r"[\W_]{0,3}".join(map(re.escape, p)) for p in parts if p), key=len, reverse=True)
    return {
        "agents": {norm(str(r.get(k) or "")) for r in agents for k in ("name", "id", "model")} - {""},
        "agent_tokens": {t for r in agents for k in ("name", "id", "model")
                         for t in re.split(r"[\W_]+", str(r.get(k) or "").casefold()) if t},
        "keep": {norm(x) for x in rules.get("keep", [])} | {norm(x) for x in PUBLIC_HANDLES},
        "keep_re": re.compile(r"(?<![^\W_])(?:" + "|".join(alts) + r")(?![^\W_])", re.I) if alts else None,
        "allow": {x.lower() for x in PROFILE_ALLOW} | {x.lower() for x in rules.get("profile_allow", [])}
                 | {x.lower() for x in PUBLIC_HANDLES},
        "routes": PROFILE_ROUTES,
        "names": {"human_names": {norm(x) for x in load_human_names(os.path.join(meta, "human_names.json"))},
                  "extra_human_ids": {norm(x) for x in load_extra_ids(os.path.join(meta, "extra_human_ids.json"))},
                  "rules": {norm(x) for k in ("exact", "cap", "ids") for x in rules.get(k, [])}},
    }


def is_model_word(span: str, L: dict) -> bool:
    n = norm(span)
    if n in L["agents"]:
        return True
    toks = [t for t in re.split(r"[\W_]+", n) if t]
    return bool(toks) and any(t in PRODUCT_WORDS or (t in L["agent_tokens"] and not t.isdigit()) for t in toks) and all(
        t in PRODUCT_WORDS or t in L["agent_tokens"] or t in MODEL_SUFFIXES or t.isdigit() for t in toks)


def word_at(string: str, s: int, e: int) -> str:
    """The span grown to whitespace on both sides ("Kimi K" inside "Kimi K3" -> "Kimi K3")."""
    while s > 0 and not string[s - 1].isspace():
        s -= 1
    while e < len(string) and not string[e].isspace():
        e += 1
    return string[s:e]


def url_has_user(span: str, L: dict) -> bool:
    sys.path.insert(0, BUNDLE_CODE)
    from build_bundle import PROFILE_RE, SUBDOMAIN_RE
    accounts = [m.group(1).lower() for rx in (PROFILE_RE, SUBDOMAIN_RE) for m in rx.finditer(span)]
    if accounts:  # an account the builder's allowlists keep (an agent's blog, an org) is no user path
        return any(x not in L["allow"] and x not in L["routes"] and x not in ("human", "id", "email") for x in accounts)
    return bool(USER_PATH.search(REDACTION_TOKENS.sub("", span)))


def drop_reason(sp: dict, string: str, L: dict) -> str | None:
    """Why a model span is not a candidate, or None to keep it."""
    text, cls = sp["text"], sp["label"]
    inside = [(m.start(), m.end()) for m in REDACTION_TOKENS.finditer(string) if m.start() < sp["e"] and sp["s"] < m.end()]
    rest = "".join(ch for k, ch in enumerate(text, sp["s"]) if not any(s <= k < e for s, e in inside))
    if not re.search(r"[^\W_]", REDACTION_TOKENS.sub("", rest)):
        return "redaction_token"
    if STRUCT_ID.match(string.strip()) or TIMESTAMP.match(text.strip()):
        return "structural_id"
    word = word_at(string, sp["s"], sp["e"])
    if is_model_word(text, L) or is_model_word(word, L):
        return "agent_or_model"
    n = norm(text)
    if n in L["keep"] or n.lstrip("@") in L["allow"]:
        return "keep_list"
    if L["keep_re"] is not None and any(m.start() <= sp["s"] and sp["e"] <= m.end()
                                         for m in L["keep_re"].finditer(string)):
        return "keep_list"
    if cls == "private_date":
        return "date"
    if cls == "private_url" and not url_has_user(text, L):
        return "url_no_account"
    if cls == "private_address" and not re.search(r"\d", text) and not STREET_WORDS.search(text):
        return "address_city_country"
    return None


def context_of(string: str, s: int, e: int, n: int = CONTEXT) -> str:
    half = max(0, (n - (e - s)) // 2)
    return " ".join(string[max(0, s - half):e + half].split())


def list_hit(n: str, names: dict, words: dict) -> tuple:
    """(list name, 'exact'|'token') when a span is on a redaction list or is one word of a listed name."""
    for k, v in names.items():
        if n in v:
            return k, "exact"
    if " " not in n:
        for k, v in words.items():
            if n in v:
                return k, "token"
    return None, None


def cmd_filter(a):
    L = load_lists(a.root, a.data, a.lists)
    strings = {r["sid"]: r for r in read_jsonl(os.path.join(a.work, "strings.jsonl"))}
    raw = read_jsonl(os.path.join(a.work, "raw.jsonl"))
    before_cls, after_cls = collections.Counter(), collections.Counter()
    before_ft, after_ft = collections.Counter(), collections.Counter()
    distinct_before, distinct_after = collections.defaultdict(set), collections.defaultdict(set)
    reasons = collections.Counter()
    kept = []
    for sp in raw:
        st = strings[sp["sid"]]
        fts = collections.Counter(file_type(l) for l in st["locs"])
        before_cls[sp["label"]] += 1
        distinct_before[sp["label"]].add(norm(sp["text"]))
        for ft, c in fts.items():
            before_ft[ft] += c
        why = drop_reason(sp, st["text"], L)
        if why:
            reasons[why] += 1
            continue
        after_cls[sp["label"]] += 1
        distinct_after[sp["label"]].add(norm(sp["text"]))
        for ft, c in fts.items():
            after_ft[ft] += c
        kept.append({"sid": sp["sid"], "loc": st["locs"][0], "n_locs": len(st["locs"]), "locs": st["locs"][:20],
                     "file_types": sorted(fts), "class": sp["label"], "score": sp["score"], "span": sp["text"],
                     "context": context_of(st["text"], sp["s"], sp["e"])})
    # person spans against the redaction lists: a list entry still served = a rule gap; anything else (one word of a
    # listed name included) is a new candidate
    gaps = collections.defaultdict(lambda: {"spans": set(), "shapes": collections.Counter()})
    words = {k: {w for x in v if " " in x for w in x.split()} for k, v in L["names"].items()}
    new = {}
    for h in kept:
        n = norm(h["span"])
        if h["class"] == "private_person":
            lst, how = list_hit(n, L["names"], words)
            h["list"] = f"{lst}:{how}" if lst else None
            if how == "exact":
                g = gaps[lst]
                g["spans"].add(n)
                for loc in h["locs"]:
                    g["shapes"][path_shape(loc)] += 1
                continue
        if h["class"] in ("private_person", "account_number", "secret"):
            c = new.setdefault(n, {"text": h["span"], "label": h["class"], "count": 0, "aliases": [],
                                   "example": h["context"], "file_types": set(), "list": h.get("list")})
            c["count"] += h["n_locs"]
            c["file_types"].update(h["file_types"])
            if h["span"] != c["text"] and h["span"] not in c["aliases"] and len(c["aliases"]) < 3:
                c["aliases"].append(h["span"])
    with open(os.path.join(a.work, "kept.jsonl"), "w") as f:
        for h in kept:
            f.write(json.dumps(h, ensure_ascii=False) + "\n")
    cands = [dict(v, i=i, file_types=sorted(v["file_types"])) for i, v in enumerate(new.values(), 1)]
    with open(os.path.join(a.work, "candidates.jsonl"), "w") as f:
        for c in cands:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    stats = {"spans_before": sum(before_cls.values()), "spans_after": len(kept),
             "by_class_before": dict(before_cls), "by_class_after": dict(after_cls),
             "distinct_before": {k: len(v) for k, v in distinct_before.items()},
             "distinct_after": {k: len(v) for k, v in distinct_after.items()},
             "by_file_type_before": dict(before_ft), "by_file_type_after": dict(after_ft),
             "drop_reasons": dict(reasons),
             "rule_gaps": {k: {"distinct": len(v["spans"]), "shapes": dict(v["shapes"].most_common(12))}
                           for k, v in gaps.items()},
             "token_of_listed_name": len({norm(h["span"]) for h in kept if (h.get("list") or "").endswith(":token")}),
             "new_candidates": len(cands),
             "new_by_class": dict(collections.Counter(c["label"] for c in cands))}
    write_json(os.path.join(a.work, "filter.json"), stats)
    print(f"filter: {stats['spans_before']} spans -> {len(kept)} kept; drops {dict(reasons)}; rule gaps "
          f"{ {k: v['distinct'] for k, v in stats['rule_gaps'].items()} }; {len(cands)} new candidates "
          f"{stats['new_by_class']}")


# ---------------------------------------------------------------- classify (Luna, as human_ids.py classify)

def rounds(work: str) -> list:
    """Classify round dirs in order: luna, luna-2, luna-3, ..."""
    found = [d for d in os.listdir(work) if re.fullmatch(r"luna(?:-\d+)?", d)]
    return [os.path.join(work, d) for d in sorted(found, key=lambda d: int(d[5:] or 1))]


def cmd_classify(a):
    """One round of Luna over the candidates no earlier round sent (a later filter can add some); each round is a
    work dir in the layout human_ids.cmd_classify expects, so an interrupted round resumes where it stopped."""
    sys.path.insert(0, BUNDLE_CODE)
    import human_ids
    rows = read_jsonl(os.path.join(a.work, "candidates.jsonl"))
    prev = rounds(a.work)
    sent = set()
    for d in prev:
        sent |= {norm(r["text"]) for r in read_jsonl(os.path.join(d, "candidates.jsonl"))}
    last = prev[-1] if prev else None
    if last and not os.path.exists(os.path.join(last, "done")):
        work_ids = last  # resume an unfinished round as it was written
    else:
        rows = [r for r in rows if norm(r["text"]) not in sent]
        if not rows:
            print(f"classify: every candidate was sent in {len(prev)} earlier rounds")
            return
        work_ids = os.path.join(a.work, "luna" if not prev else f"luna-{len(prev) + 1}")
        os.makedirs(work_ids, exist_ok=True)
        with open(os.path.join(work_ids, "candidates.jsonl"), "w") as f:
            for i, r in enumerate(rows, 1):
                f.write(json.dumps({"i": i, "text": r["text"], "label": r["label"], "count": r["count"],
                                    "aliases": r["aliases"], "example": r["example"][:CONTEXT], "agent": None},
                                   ensure_ascii=False) + "\n")
    ns = argparse.Namespace(work=work_ids, root=a.root, llm=swarm_llm.from_args(a), parallel=a.parallel,
                            attempts=a.attempts)
    human_ids.cmd_classify(ns)
    open(os.path.join(work_ids, "done"), "w").close()


# ---------------------------------------------------------------- review and write

def unclassified(work: str) -> tuple:
    """(rounds without a done mark, current candidates no round sent): what review needs to be empty first."""
    current = {norm(r["text"]) for r in read_jsonl(os.path.join(work, "candidates.jsonl"))}
    sent, open_rounds = set(), []
    for d in rounds(work):
        sent |= {norm(r["text"]) for r in read_jsonl(os.path.join(d, "candidates.jsonl"))}
        if not os.path.exists(os.path.join(d, "done")):
            open_rounds.append(os.path.basename(d))
    return open_rounds, sorted(current - sent)


def cmd_review(a):
    """The classifier's human_individual hits of every round that are still candidates after the latest filter."""
    if not os.path.exists(os.path.join(a.work, "candidates.jsonl")):
        raise SystemExit(f"review: no {a.work}/candidates.jsonl: run `filter` first")
    open_rounds, never = unclassified(a.work)
    if (open_rounds or never) and not a.allow_incomplete:
        raise SystemExit(f"review: {len(never)} candidates were never classified and {len(open_rounds)} classify "
                         f"rounds are unfinished ({', '.join(open_rounds) or 'none'}); run `classify` first (or pass "
                         f"--allow-incomplete). Without it the review would drop them silently.")
    current = {norm(r["text"]): r for r in read_jsonl(os.path.join(a.work, "candidates.jsonl"))}
    whys = collections.defaultdict(list)
    for d in rounds(a.work):
        sent = {r["i"]: r for r in read_jsonl(os.path.join(d, "candidates.jsonl"))}
        if not os.path.exists(os.path.join(d, "classified.jsonl")):
            continue
        for l in open(os.path.join(d, "classified.jsonl")):
            for h in json.loads(l)["hits"]:
                whys[norm(sent[h["i"]]["text"])].append(h.get("why"))
    hits = sorted((dict(current[n], why=w) for n, w in whys.items() if n in current), key=lambda h: -h["count"])
    out = {f"p{k}": h for k, h in enumerate(hits, 1)}
    write_json(os.path.join(a.work, "review.json"), out)
    with open(os.path.join(a.work, "review-template.txt"), "w") as f:
        f.write("# One line per hit: p<n> accept <reason> | p<n> reject <reason> (accept:<id> names the id inside a "
                "longer span).\n# An id in a class the classifier did not see: + <id> | <reason>. Every p<n> needs a "
                "verdict; an empty file is right when there are no hits.\n")
        for k, h in out.items():
            f.write(f"# {k}: {h['text']!r} ({h['label']}, {h['count']}x) {h['example'][:160]!r}\n{k} reject <reason>\n")
    print(f"review: {len(out)} human_individual hits of {len(current)} candidates -> {a.work}/review.json "
          f"(template: {a.work}/review-template.txt)")


def cmd_write(a):
    hits = read_json(os.path.join(a.work, "review.json"))
    if hits is None:
        raise SystemExit(f"write: no {a.work}/review.json: run `review` first")
    if not a.review or not os.path.exists(a.review):
        raise SystemExit(f"write: no review file {a.review!r}; start from {a.work}/review-template.txt (an empty file "
                         f"when review found no hits)")
    lists = getattr(a, "lists", None) or os.path.join(a.root, "meta")
    verdicts = {}
    extra = []
    for line in open(a.review):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("+"):  # an id the review found in a class Luna did not see: "+ <id> | <reason>"
            ident, _, reason = line[1:].partition("|")
            extra.append((ident.strip(), reason.strip()))
            continue
        key, verdict, *why = line.split(None, 2)
        verdict, _, ident = verdict.partition(":")  # "accept:<id>" names the identifier inside a longer span
        if key not in hits or verdict not in ("accept", "reject") or not why:
            raise SystemExit(f"bad review line: {key} {verdict}")
        verdicts[key] = (verdict, why[0], ident or hits[key]["text"])
    missing = [k for k in hits if k not in verdicts]
    if missing:
        raise SystemExit(f"{len(missing)} hits have no verdict")
    accepted = sorted({" ".join(ident.split()).lstrip("@") for v, _, ident in verdicts.values() if v == "accept"}
                      | {" ".join(ident.split()) for ident, _ in extra if ident}, key=str.lower)
    write_json(os.path.join(lists, "pf_candidates.json"), accepted)
    extra_path = os.path.join(lists, "extra_human_ids.json")
    extra = read_json(extra_path, []) or []
    merged = sorted({" ".join(x.split()) for x in list(extra) + accepted if isinstance(x, str) and x.strip()},
                    key=str.lower)
    write_json(extra_path, merged)
    kept = read_jsonl(os.path.join(a.work, "kept.jsonl"))
    with open(os.path.join(a.out, "pf-hits.jsonl"), "w") as f:
        for h in kept:
            f.write(json.dumps({k: h.get(k) for k in ("loc", "n_locs", "class", "score", "span", "context", "list")},
                               ensure_ascii=False) + "\n")
    st = {"collect": read_json(os.path.join(a.work, "collect.json"), {}), "run": read_json(os.path.join(a.work, "run.json"), {}),
          "filter": read_json(os.path.join(a.work, "filter.json"), {})}
    luna = [r for d in rounds(a.work) for r in read_jsonl(os.path.join(d, "classified.jsonl"))]
    with open(os.path.join(a.out, "pf-summary.md"), "w") as f:
        f.write(summary_md(st, luna, hits, verdicts, extra, len(accepted)))
    print(f"write: {len(accepted)} accepted ids -> {lists}/pf_candidates.json, merged into extra_human_ids.json "
          f"({len(merged) - len(set(extra))} new); {len(kept)} kept spans and the summary -> {a.out}")


def table(counter_before: dict, counter_after: dict, head: str) -> str:
    keys = sorted(set(counter_before) | set(counter_after), key=lambda k: -counter_before.get(k, 0))
    rows = [f"| {head} | before | after |", "|---|---:|---:|"]
    rows += [f"| {k} | {counter_before.get(k, 0)} | {counter_after.get(k, 0)} |" for k in keys]
    rows.append(f"| total | {sum(counter_before.values())} | {sum(counter_after.values())} |")
    return "\n".join(rows)


def cell(s: str) -> str:
    return " ".join(str(s).split()).replace("|", "\\|")


def summary_md(st, luna, hits, verdicts, extra, n_accepted) -> str:
    c, r, fl = st["collect"], st["run"], st["filter"]
    out = ["# Privacy-filter sweep (openai/privacy-filter, third pass)", "",
           "Pipeline-internal review file: never served, never committed. Hits are candidates, not verdicts.", "",
           "## Run", "",
           f"- Strings: {c.get('strings')} ({c.get('by_kind')}), {c.get('distinct')} distinct non-blank, "
           f"{c.get('chars')} characters.",
           f"- Model: {r.get('model')} on MLX (Mac mini M4), operating point {r.get('operating_point')}. "
           f"The checkpoint ships only the default operating point; there is no high-recall preset.",
           f"- {r.get('units')} units, {r.get('tokens')} tokens, {r.get('batches')} batches, {r.get('seconds')} s "
           f"= {r.get('tokens_per_second')} tok/s; {r.get('spans')} raw spans.", "",
           "## Spans per class (before and after the filter)", "",
           table(fl.get("by_class_before", {}), fl.get("by_class_after", {}), "class"), "",
           "Distinct span texts per class: " + ", ".join(
               f"{k} {fl.get('distinct_before', {}).get(k, 0)} -> {fl.get('distinct_after', {}).get(k, 0)}"
               for k in sorted(fl.get("distinct_before", {}))) + ".", "",
           "## Span occurrences per file type (a span counts once per served location)", "",
           table(fl.get("by_file_type_before", {}), fl.get("by_file_type_after", {}), "file type"), "",
           "## Drop reasons", "",
           "| reason | spans |", "|---|---:|"]
    out += [f"| {k} | {v} |" for k, v in sorted(fl.get("drop_reasons", {}).items(), key=lambda kv: -kv[1])]
    out += ["", "## Rule gaps (person spans that are a redaction-list entry, still served)", "",
            f"{fl.get('token_of_listed_name', 0)} more distinct person spans are one word of a listed multi-word "
            "name; they went to Luna as new candidates.", ""]
    for k, g in sorted(fl.get("rule_gaps", {}).items()):
        out.append(f"- **{k}**: {g['distinct']} distinct spans. Path shapes: "
                   + "; ".join(f"`{s}` ({n})" for s, n in g["shapes"].items()))
    if not fl.get("rule_gaps"):
        out.append("- none")
    tot_in = sum(x.get("in", 0) for x in luna)
    tot_out = sum(x.get("out", 0) for x in luna)
    out += ["", "## Luna classification", "",
            f"- {fl.get('new_candidates')} new candidates ({fl.get('new_by_class')}) in {len(luna)} batches; "
            f"{len(hits)} labelled human_individual; tokens {tot_in} in, {tot_out} out; model "
            f"{sorted({x.get('model') for x in luna})}.",
            f"- Review: {n_accepted} accepted (written to `pf_candidates.json` and merged into `extra_human_ids.json`), "
            f"{sum(1 for v, _, _ in verdicts.values() if v == 'reject')} rejected.", "",
            "## Accepted candidates", "", "| # | identifier | class | count | reason | context |", "|---|---|---|---:|---|---|"]
    for k, h in hits.items():
        v, why, ident = verdicts[k]
        if v == "accept":
            out.append(f"| {k} | {cell(ident)} | {h['label']} | {h['count']} | {cell(why)} | {cell(h['example'])} |")
    for ident, why in extra:
        out.append(f"| + | {cell(ident)} | (other class) | | {cell(why)} | |")
    out += ["", "## Rejected", "", "| # | span | reason |", "|---|---|---|"]
    for k, h in hits.items():
        v, why, _ = verdicts[k]
        if v == "reject":
            out.append(f"| {k} | {cell(h['text'])} | {cell(why)} |")
    return "\n".join(out) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=("collect", "run", "filter", "classify", "review", "write"))
    ap.add_argument("--root", default=swarm_paths.work_default(), help="the work area (default $SWARM_WORK)")
    ap.add_argument("--lists", help="redaction-list dir (default $SWARM_LISTS or <root>/meta)")
    ap.add_argument("--data", help="built bundle data dir (default <root>/bundle/data)")
    ap.add_argument("--work", help="work dir (default <root>/pf)")
    ap.add_argument("--model", help="`run`: local MLX checkpoint dir (mlx-community/openai-privacy-filter-bf16)")
    ap.add_argument("--limit", type=int, default=0, help="`run`: only the first N strings (smoke test)")
    ap.add_argument("--review", help="`write`: review file")
    ap.add_argument("--out", help="`write`: where pf-hits.jsonl and pf-summary.md go (default <work>)")
    ap.add_argument("--allow-incomplete", action="store_true", help="`review`: list hits while classify is unfinished")
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--attempts", type=int, default=4)
    swarm_llm.add_args(ap, timeout=600, model_flag="--llm-model")
    a = ap.parse_args(argv)
    a.root = swarm_paths.need_work(a.root)
    a.lists = swarm_paths.lists_dir(a.root, a.lists)
    a.data = a.data or os.path.join(a.root, "bundle", "data")
    a.work = a.work or os.path.join(a.root, "pf")
    a.out = a.out or a.work
    os.makedirs(a.work, exist_ok=True)
    {"collect": cmd_collect, "run": cmd_run, "filter": cmd_filter, "classify": cmd_classify, "review": cmd_review,
     "write": cmd_write}[a.step](a)


if __name__ == "__main__":
    main()
