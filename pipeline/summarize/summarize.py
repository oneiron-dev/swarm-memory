#!/usr/bin/env python3
"""Per-agent and per-room daily summaries with citations (docs/schema.md section 3).

Reads the slice (section 1), builds one numbered prompt per unit, asks the model (any OpenAI-compatible
endpoint, our prime-agent harness, or --mock for templated text without a model; pipeline/common/swarm_llm.py),
validates the [n] markers and appends one record per line to summaries/{room,agent}.jsonl. Resume-safe: ids
already written are skipped.

Subcommands: estimate, run, audit-existing, pilot-md. See README.md.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import signal
import statistics
import sys
import threading
import time
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "common"))
import swarm_llm  # noqa: E402
import swarm_paths  # noqa: E402
from swarm_llm import CallError, ModelMismatch  # noqa: E402,F401  (book.py imports them from here)

MODEL = swarm_llm.DEFAULT_MODEL  # the real run's model; records carry the model each call actually used
AGENT_KINDS = ("chat", "session_goal", "session_summary", "next_goal",
               "history_query", "outreach", "cc_text", "memory")
ROOM_KINDS = ("chat",)
KEEP_KINDS = frozenset({"session_goal", "session_summary", "next_goal", "memory"})
INPUT_CAP = 40_000        # LLM input tokens per call, approximate
OVERHEAD_TOKENS = 1_100   # system prompt + harness digest + task line
LINE_CHARS = 2_000        # one source line is clipped here
MEMORY_CHARS = 2_000      # the brief: first 2,000 characters of the day's memory
CHARS_PER_TOKEN = 3.8     # estimate only; ESTIMATE.json reports the calibration

SYSTEM_PROMPT = """You write short factual summaries of one day in AI Village, a long-running project where AI agents (named after their models) share group chat rooms, use their own computers, and pursue goals together.

You get numbered source lines, one per line: [n] HH:MM speaker (kind): text. Times are UTC. Kinds:
- chat: a message in a group chat room
- session_goal: the goal the agent set when it started a computer session
- session_summary: the agent's own summary after a computer session
- next_goal: the goal it set for its next session
- history_query: a question it asked its memory search, with the answer
- outreach: a request to contact people outside the village, or the reply to it
- cc_text: text the agent wrote while working in a coding tool
- memory: an excerpt of the agent's saved notes at the end of the day

Write 3 to 7 plain sentences about what the subject did, decided, coordinated on, and got stuck on that day.
Rules:
1. Every sentence ends with one or more markers such as [4] or [4][9], placed before the final period, that point to the lines that support it. Cite the one to four lines that support the sentence most directly.
2. Use only what the lines say. No speculation and no outside knowledge.
3. Never name or describe a human. The speaker "human" stands for any human. The lines often contain names, nicknames, usernames, social media handles or email addresses of people (readers, authors, followers, staff, collaborators): never copy them. Write "a human" or "humans" instead, with no gender, job, nationality or other detail.
4. The only person-like names you may write are these AI agent names: {agents}. Organizations, products, websites and projects are fine.
5. Write one plain paragraph: no lists, no headings, no quotations.
Return only the summary text."""

AUDIT_PROMPT = """You check a short text for references to human people. The text is about AI agents, which are named after their models. These agent names are allowed: {agents}. Also allowed: companies, organizations, products, websites, projects, places, and the words "human" and "humans".

List every name, nickname, username, social media handle or email address in the text that refers, or may refer, to a human person. Copy each one exactly as it appears in the text. Return only JSON: {{"names": ["..."]}}, or {{"names": []}} when there are none."""


def system_prompts(agent_names: dict) -> tuple[str, str]:
    agents = ", ".join(sorted(set(agent_names.values()))) or "(any AI model name)"
    return SYSTEM_PROMPT.format(agents=agents), AUDIT_PROMPT.format(agents=agents)


def parse_names(text: str) -> list[str]:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON object")
    names = json.loads(m.group(0)).get("names")
    if not isinstance(names, list):
        raise ValueError("names is not a list")
    return [n.strip() for n in names if isinstance(n, str) and len(n.strip()) >= 2]


def scrub(text: str, names: list[str]) -> str:
    """Last resort after a regenerated summary still names a human: replace in place."""
    for name in sorted(set(names), key=len, reverse=True):
        bare = re.escape(name.lstrip("@"))
        text = re.sub(r"\b([Aa]n?|[Tt]he)\s+@?" + bare + r"(?!\w)",
                      lambda m: "A human" if m.group(1)[0].isupper() else "a human", text)
        text = re.sub(r"@?" + bare, "a human", text)
    return text


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fold(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def est_tokens_chars(chars: int) -> int:
    return int(chars / CHARS_PER_TOKEN) + 1


# ---------------------------------------------------------------- slice index

class Row:
    """A pointer to one slice line; the text is read back with pread on demand."""
    __slots__ = ("time", "kind", "fid", "off", "length", "chars")

    def __init__(self, time_, kind, fid, off, length, chars):
        self.time, self.kind, self.fid, self.off, self.length, self.chars = time_, kind, fid, off, length, chars

    def line_chars(self) -> int:
        cap = MEMORY_CHARS if self.kind == "memory" else LINE_CHARS
        return min(self.chars, cap) + 48  # "[n] HH:MM agent (kind): "


class SliceIndex:
    def __init__(self, work: str, scopes):
        self.work = work
        self.slice_dir = os.path.join(work, "slice")
        self.files: list[str] = []
        self.fds: list[int] = []
        self.units: dict[tuple, list[Row]] = defaultdict(list)
        self.dates: dict[int, str] = {}
        self.rows_read = defaultdict(int)
        self.bad_lines = 0
        kinds = set()
        if "room" in scopes:
            kinds.update(ROOM_KINDS)
        if "agent" in scopes:
            kinds.update(AGENT_KINDS)
        for kind in sorted(kinds):
            path = os.path.join(self.slice_dir, f"slice-{kind}.jsonl")
            if os.path.exists(path):
                self._scan(path, kind, scopes)
        for rows in self.units.values():
            rows.sort(key=lambda r: (r.time, r.fid, r.off))
        self.agent_names = self.names(work, "agents.json")
        self.room_names = self.names(work, "rooms.json")

    @staticmethod
    def names(work, fname):
        path = os.path.join(work, "meta", fname)
        try:
            with open(path) as f:
                rows = json.load(f)
        except (OSError, ValueError):
            return {}
        if isinstance(rows, dict):
            rows = rows.get("agents") or rows.get("rooms") or list(rows.values())
        return {r["id"]: r.get("name") or r["id"] for r in rows if isinstance(r, dict) and "id" in r}

    def _scan(self, path, kind, scopes):
        fid = len(self.files)
        self.files.append(path)
        self.fds.append(os.open(path, os.O_RDONLY))
        off = 0
        with open(path, "rb") as f:
            for raw in f:
                n = len(raw)
                if not raw.endswith(b"\n"):  # a line still being written
                    break
                try:
                    rec = json.loads(raw)
                except ValueError:
                    self.bad_lines += 1
                    off += n
                    continue
                k = rec.get("kind") or kind
                day = rec.get("day")
                if day is None or k != kind:
                    off += n
                    continue
                self.dates.setdefault(day, rec.get("date"))
                row = Row(rec.get("time") or "", k, fid, off, n, len(rec.get("text") or ""))
                self.rows_read[k] += 1
                agent = rec.get("agent")
                if "agent" in scopes and rec.get("speaker") == "agent" and agent and agent != "human":
                    self.units[("agent", agent, day)].append(row)
                if "room" in scopes and k == "chat" and rec.get("room"):
                    self.units[("room", rec["room"], day)].append(row)
                off += n

    def read(self, row: Row) -> dict:
        return json.loads(os.pread(self.fds[row.fid], row.length, row.off))

    def unit_ids(self, scope, since_day=None):
        keys = [k for k in self.units if k[0] == scope and (since_day is None or k[2] >= since_day)]
        keys.sort(key=lambda k: (-k[2], k[1]))  # most recent day first
        return keys


def unit_id(key) -> str:
    scope, name, day = key
    return f"{scope}-day:{name}:{day}"


# ---------------------------------------------------------------- planning

def even_pick(idx: list[int], k: int) -> list[int]:
    if k <= 0:
        return []
    if k >= len(idx):
        return list(idx)
    if k == 1:
        return [idx[len(idx) // 2]]
    step = (len(idx) - 1) / (k - 1)
    return sorted({idx[round(j * step)] for j in range(k)})


def plan_rows(rows: list[Row], cap_tokens: int = INPUT_CAP):
    """Pick the rows for one call. Over the cap, keep every goal/summary/memory row
    and sample the rest evenly in time. Returns (rows, sampled, est_input_tokens)."""
    est = [est_tokens_chars(r.line_chars()) for r in rows]
    budget = cap_tokens - OVERHEAD_TOKENS
    total = sum(est)
    if total <= budget:
        return rows, False, total + OVERHEAD_TOKENS
    keep = [i for i, r in enumerate(rows) if r.kind in KEEP_KINDS]
    rest = [i for i, r in enumerate(rows) if r.kind not in KEEP_KINDS]
    left = budget - sum(est[i] for i in keep)
    lo, hi, best = 0, len(rest), []
    while lo <= hi:
        mid = (lo + hi) // 2
        pick = even_pick(rest, mid)
        if sum(est[i] for i in pick) <= left:
            best, lo = pick, mid + 1
        else:
            hi = mid - 1
    chosen = sorted(keep + best)
    return [rows[i] for i in chosen], True, sum(est[i] for i in chosen) + OVERHEAD_TOKENS


def format_line(n: int, rec: dict) -> str:
    kind = rec.get("kind", "")
    who = "human" if rec.get("speaker") != "agent" else (rec.get("agent") or "agent")
    hhmm = (rec.get("time") or "")[11:16] or "--:--"
    cap = MEMORY_CHARS if kind == "memory" else LINE_CHARS
    return f"[{n}] {hhmm} {who} ({kind}): {clip(fold(rec.get('text', '')), cap)}"


def build_prompt(idx: SliceIndex, key, cap_tokens: int = INPUT_CAP):
    scope, name, day = key
    rows, sampled, est_in = plan_rows(idx.units[key], cap_tokens)
    lines, nmap = [], {}
    for n, row in enumerate(rows, 1):
        rec = idx.read(row)
        nmap[n] = rec["id"]
        lines.append(format_line(n, rec))
    date = idx.dates.get(day)
    if scope == "agent":
        label = idx.agent_names.get(name, name)
        task = (f"Summarize day {day} ({date}) for the agent {label}, from the lines above. "
                f"Write about {label} in the third person.")
    else:
        label = idx.room_names.get(name, name)
        task = (f"Summarize day {day} ({date}) in the chat room #{label}, from the lines above: "
                f"what the agents there did, decided, coordinated on, and got stuck on.")
    if sampled:
        task += " The lines are an even sample of a longer day."
    return {"key": key, "id": unit_id(key), "stdin": "\n".join(lines), "task": task,
            "nmap": nmap, "sampled": sampled, "est_in": est_in, "date": date,
            "n_lines": len(lines), "n_rows": len(idx.units[key])}


# ---------------------------------------------------------------- citations

MARKER = re.compile(r"\[(\d+(?:\s*[,;–-]\s*\d+)*)\]")


def _expand(body: str) -> list[int]:
    out = []
    for part in re.split(r"\s*[,;]\s*", body):
        m = re.fullmatch(r"(\d+)\s*[–-]\s*(\d+)", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a <= b and b - a <= 20:
                out.extend(range(a, b + 1))
        elif part.isdigit():
            out.append(int(part))
    return out


def validate(text: str, nmap: dict):
    """Drop unknown markers, split lists and ranges into [a][b], move a marker that
    trails a full stop in front of it. Returns (text, cites)."""
    used = []

    def repl(m):
        good = [n for n in _expand(m.group(1)) if n in nmap]
        used.extend(good)
        return "".join(f"[{n}]" for n in good)

    out = MARKER.sub(repl, fold(text))
    out = re.sub(r"([.!?])\s*((?:\[\d+\])+)", r" \2\1", out)
    out = re.sub(r"\s+([.,;:!?])", r"\1", out)
    out = re.sub(r"(\[\d+\])(?=[A-Za-z])", r"\1 ", out)
    out = fold(out)
    cites = [{"n": n, "ref": nmap[n]} for n in sorted(set(used))]
    return out, cites


# ---------------------------------------------------------------- the model call

class UnitFailed(RuntimeError):
    def __init__(self, msg, attempts):
        super().__init__(msg)
        self.attempts = attempts


def call_luna(stdin_text: str, task: str, cfg, system: str, purpose: str | None = None, json_mode: bool = False) -> dict:
    """One call through the configured client (cfg.llm): {"text", "model", "in", "out"}. The name stays from the
    real run; book.py calls it too."""
    if purpose is None:
        purpose = "audit" if system.startswith("You check a short text") else "summary"
    return cfg.llm.call(stdin_text, task, system, json_mode=json_mode, purpose=purpose,
                        cwd=getattr(cfg, "call_cwd", None))


# ---------------------------------------------------------------- the run

class Gate:
    """Concurrency limit that starts at `start`, rises in steps of 4 to `maxc` after
    a clean streak, and falls by 4 on a rate limit."""

    def __init__(self, start, maxc, raise_after=40):
        self.limit, self.start, self.maxc, self.raise_after = start, start, maxc, raise_after
        self.active, self.streak = 0, 0
        self.cv = threading.Condition()

    def acquire(self):
        with self.cv:
            while self.active >= self.limit:
                self.cv.wait()
            self.active += 1

    def release(self, ok=True, rate_limited=False, error=False):
        with self.cv:
            self.active -= 1
            if rate_limited:
                self.limit, self.streak = max(2, self.limit - 4), 0
            elif error:
                self.streak = 0
            elif ok:
                self.streak += 1
                if self.streak >= self.raise_after and self.limit < self.maxc:
                    self.limit, self.streak = min(self.maxc, self.limit + 4), 0
            self.cv.notify_all()


class Runner:
    def __init__(self, cfg):
        self.cfg = cfg
        self.out_dir = os.path.join(cfg.work, "summaries")
        os.makedirs(self.out_dir, exist_ok=True)
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.stop_reason = None
        self.gate = Gate(cfg.concurrency, max(cfg.concurrency, cfg.max_concurrency))
        self.stats = defaultdict(int)
        self.recent = deque()  # completion times, for calls per minute
        self.inflight = set()
        self.started = time.time()
        self.consecutive_fail = 0
        self.planned = 0
        self.phase = None
        agents = SliceIndex.names(cfg.work, "agents.json")
        rooms = SliceIndex.names(cfg.work, "rooms.json")
        self.system, self.audit_system = system_prompts(agents)
        self.allowed = {x.lower() for x in [*agents, *agents.values(), *rooms, *rooms.values(), "human"]}

    def path(self, name):
        return os.path.join(self.out_dir, name)

    def done_ids(self):
        ids = set()
        for name in ("room.jsonl", "agent.jsonl"):
            try:
                with open(self.path(name)) as f:
                    for line in f:
                        try:
                            ids.add(json.loads(line)["id"])
                        except (ValueError, KeyError):
                            pass
            except OSError:
                pass
        return ids

    def append(self, name, obj):
        with self.lock:
            with open(self.path(name), "a") as f:
                f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    def progress(self, final=False):
        with self.lock:
            now = time.time()
            while self.recent and now - self.recent[0] > 600:
                self.recent.popleft()
            window = min(600.0, max(60.0, now - self.started))
            cpm = len(self.recent) / (window / 60)
            remaining = max(0, self.planned - self.stats["done"] - self.stats["failed"])
            eta = None
            if cpm > 0 and remaining:
                eta = datetime.fromtimestamp(now + remaining / cpm * 60, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            doc = {"updated": utcnow(), "phase": self.phase, "final": final,
                   "started": datetime.fromtimestamp(self.started, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "planned": self.planned, "remaining": remaining, "eta": eta,
                   "calls_per_min": round(cpm, 2), "concurrency": self.gate.limit,
                   "inflight": sorted(self.inflight)[:16], "stopped": self.stop_reason,
                   **dict(self.stats)}
        tmp = self.path("PROGRESS.json.tmp")
        with open(tmp, "w") as f:
            json.dump(doc, f, indent=1)
        os.replace(tmp, self.path("PROGRESS.json"))

    def _monitor(self):
        while not self.stop.wait(15):
            self.progress()

    def _call(self, uid, stdin_text, task, system):
        """One model call with backoff. Returns the result, None when the run is
        stopping, or raises UnitFailed after the last attempt."""
        attempts = 0
        while not self.stop.is_set():
            attempts += 1
            self.gate.acquire()
            with self.lock:
                self.inflight.add(uid)
            ok = rate = err = False
            try:
                res = call_luna(stdin_text, task, self.cfg, system,
                                json_mode=system is self.audit_system)
                if system is self.audit_system:
                    res["names"] = parse_names(res["text"])
                ok = True
            except ModelMismatch as e:
                self.stop_reason = str(e)
                self.stop.set()
                err = True
                print(f"STOP: {e} on {uid}", file=sys.stderr, flush=True)
                return None
            except (CallError, ValueError) as e:
                rate, err = getattr(e, "rate_limited", False), True
                last_err = f"{type(e).__name__}: {e}"
                with self.lock:
                    self.stats["rate_limited" if rate else "call_errors"] += 1
                if attempts >= self.cfg.max_attempts:
                    raise UnitFailed(last_err, attempts)
            finally:
                with self.lock:
                    self.inflight.discard(uid)
                self.gate.release(ok=ok, rate_limited=rate, error=err)
            if ok:
                with self.lock:
                    self.stats["tokens_in"] += res["in"]
                    self.stats["tokens_out"] += res["out"]
                    self.stats["calls"] += 1
                    self.recent.append(time.time())
                return res
            with self.lock:
                self.stats["retries"] += 1
            delay = min(300.0, self.cfg.backoff * 2 ** (attempts - 1)) * (0.75 + random.random() / 2)
            print(f"retry {uid} in {delay:.0f}s: {last_err[:200]}", file=sys.stderr, flush=True)
            self.stop.wait(delay)
        return None

    def audit(self, uid, text):
        """Names of humans that Luna finds in a summary; None when stopping."""
        res = self._call(uid, text, "Check the text above.", self.audit_system)
        if res is None:
            return None
        return [n for n in res["names"]
                if (n in text or n.lstrip("@") in text) and not self.allowed_name(n)], res

    def allowed_name(self, name):
        """True for an agent or room name, or a piece of one ("Luna" in "GPT-5.6 Luna")."""
        n = name.lstrip("@#").lower()
        return any(n in a for a in self.allowed)

    def one(self, idx: SliceIndex, key):
        if self.stop.is_set():
            return
        uid = unit_id(key)
        try:
            prompt = build_prompt(idx, key, self.cfg.cap)
        except Exception as e:  # a row vanished or a bad line; record and move on
            self._fail(uid, f"prompt: {e!r}", 0)
            return
        if self.cfg.save_prompts:
            os.makedirs(self.cfg.save_prompts, exist_ok=True)
            with open(os.path.join(self.cfg.save_prompts, uid.replace(":", "_") + ".txt"), "w") as f:
                f.write(prompt["task"] + "\n\n" + prompt["stdin"] + "\n")
        t0, task, calls, tin, tout, in_first = time.time(), prompt["task"], 0, 0, 0, None
        nocite_retry = regenerated = scrubbed = False
        try:
            while True:
                res = self._call(uid, prompt["stdin"], task, self.system)
                if res is None:
                    return
                calls, tin, tout = calls + 1, tin + res["in"], tout + res["out"]
                in_first = res["in"] if in_first is None else in_first
                text, cites = validate(res["text"], prompt["nmap"])
                if not cites and not nocite_retry:
                    nocite_retry = True
                    with self.lock:
                        self.stats["nocite_retries"] += 1
                    continue
                if self.cfg.no_audit:
                    break
                found = self.audit(uid, text)
                if found is None:
                    return
                names, ares = found
                calls, tin, tout = calls + 1, tin + ares["in"], tout + ares["out"]
                if names and not regenerated:
                    regenerated = True
                    with self.lock:
                        self.stats["human_name_regenerated"] += 1
                    task = (prompt["task"] + " Your last draft named humans. Do not write any of these; "
                            "write \"a human\" instead: " + "; ".join(names) + ".")
                    continue
                if names:
                    text, scrubbed = scrub(text, names), True
                    with self.lock:
                        self.stats["human_name_scrubbed"] += 1
                break
        except UnitFailed as e:
            self._fail(uid, e.args[0], e.attempts)
            return
        self._write(prompt, text, cites, calls, time.time() - t0, tin, tout, in_first, regenerated, scrubbed)

    def _write(self, prompt, text, cites, calls, secs, tin, tout, in_first, regenerated=False, scrubbed=False):
        scope, name, day = prompt["key"]
        rec = {"id": prompt["id"], "scope": scope, "key": name, "day": day, "date": prompt["date"],
               "text": text, "cites": cites, "model": self.cfg.llm.model}
        if prompt["sampled"]:
            rec["sampled"] = True
        if not cites:
            rec["uncited"] = True
        self.append(f"{scope}.jsonl", rec)
        self.append("calls.jsonl", {"id": prompt["id"], "at": utcnow(), "secs": round(secs, 1),
                                    "calls": calls, "in": tin, "out": tout, "in_first": in_first,
                                    "est_in": prompt["est_in"],
                                    "lines": prompt["n_lines"], "rows": prompt["n_rows"],
                                    "sampled": prompt["sampled"], "cites": len(cites),
                                    "regenerated": regenerated, "scrubbed": scrubbed})
        with self.lock:
            self.stats["done"] += 1
            self.stats["uncited"] += 0 if cites else 1
            self.stats["sampled"] += 1 if prompt["sampled"] else 0
            self.consecutive_fail = 0

    def _fail(self, uid, err, attempts):
        self.append("failed.jsonl", {"id": uid, "at": utcnow(), "attempts": attempts, "err": err[:600]})
        with self.lock:
            self.stats["failed"] += 1
            self.consecutive_fail += 1
            if self.consecutive_fail >= self.cfg.max_consecutive_fail and not self.stop.is_set():
                self.stop_reason = f"{self.consecutive_fail} units failed in a row; last: {err[:200]}"
                self.stop.set()

    def run_phase(self, scope):
        self.phase = scope
        idx = SliceIndex(self.cfg.work, {scope})
        done = self.done_ids()
        keys = [k for k in idx.unit_ids(scope, self.cfg.since_day) if unit_id(k) not in done]
        if self.cfg.limit is not None:
            keys = keys[: max(0, self.cfg.limit - self.stats["done"] - self.stats["failed"])]
        with self.lock:
            self.planned += len(keys)
            self.stats[f"{scope}_units_in_slice"] = len(idx.unit_ids(scope))
            self.stats["skipped_existing"] += sum(1 for k in idx.unit_ids(scope, self.cfg.since_day)
                                                  if unit_id(k) in done)
        print(f"{utcnow()} phase {scope}: {len(keys)} units to run", file=sys.stderr, flush=True)
        with ThreadPoolExecutor(max_workers=self.gate.maxc) as pool:
            for _ in pool.map(lambda k: self.one(idx, k), keys):
                pass

    def audit_existing(self):
        """Check records written before the audit step existed. Flagged records are
        moved out of the output (a local backup is kept) so the next run rewrites them."""
        self.phase = "audit"
        mon = threading.Thread(target=self._monitor, daemon=True)
        mon.start()

        def check(rec):
            try:
                found = self.audit(rec["id"], rec["text"])
            except UnitFailed:
                return rec["id"], True
            return rec["id"], None if found is None else bool(found[0])

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        try:
            for name in ("room.jsonl", "agent.jsonl"):
                try:
                    with open(self.path(name)) as f:
                        recs = [json.loads(l) for l in f if l.strip()]
                except OSError:
                    continue
                with self.lock:
                    self.planned += len(recs)
                with ThreadPoolExecutor(max_workers=self.gate.maxc) as pool:
                    verdict = dict(pool.map(check, recs))
                if self.stop.is_set() or None in verdict.values():
                    return 3
                flagged = {i for i, v in verdict.items() if v}
                for i in verdict:
                    self.append("audit.jsonl", {"id": i, "at": utcnow(), "flagged": i in flagged})
                os.replace(self.path(name), self.path(f"pre-audit-{stamp}-{name}"))
                with open(self.path(name + ".tmp"), "w") as f:
                    for r in recs:
                        if r["id"] not in flagged:
                            f.write(json.dumps(r, ensure_ascii=False) + "\n")
                os.replace(self.path(name + ".tmp"), self.path(name))
                with self.lock:
                    self.stats["done"] += len(recs)
                    self.stats[f"audit_flagged_{name.split('.')[0]}"] = len(flagged)
                print(f"{utcnow()} audit {name}: {len(recs)} checked, {len(flagged)} flagged and removed",
                      file=sys.stderr, flush=True)
        finally:
            self.stop.set()
            self.progress(final=True)
        return 0

    def run(self):
        mon = threading.Thread(target=self._monitor, daemon=True)
        mon.start()
        try:
            for scope in self.cfg.scopes:
                if self.stop.is_set():
                    break
                if self.cfg.limit is not None and self.stats["done"] + self.stats["failed"] >= self.cfg.limit:
                    break
                if self.cfg.wait_slice:
                    wait_for_slice(self.cfg.work, ROOM_KINDS if scope == "room" else AGENT_KINDS,
                                   self.stop, self.cfg.poll)
                self.run_phase(scope)
        finally:
            self.stop.set()
            self.progress(final=True)
        return 3 if self.stop_reason else 0


# ---------------------------------------------------------------- slice readiness

def slice_done_kinds(work: str) -> tuple[set, bool]:
    try:
        with open(os.path.join(work, "slice", "STATS.json")) as f:
            st = json.load(f)
    except (OSError, ValueError):
        return set(), False
    complete = bool(st.get("final") or st.get("complete"))
    done = set(st.get("final_kinds") or st.get("done_kinds") or [])
    return done, complete


def wait_for_slice(work, kinds, stop, poll):
    while not stop.is_set():
        done, complete = slice_done_kinds(work)
        if complete or set(kinds) <= done:
            return
        missing = sorted(set(kinds) - done)
        print(f"{utcnow()} waiting for slice kinds: {missing}", file=sys.stderr, flush=True)
        stop.wait(poll)


# ---------------------------------------------------------------- estimate

def pct(xs, q):
    if not xs:
        return 0
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))]


def estimate(cfg):
    idx = SliceIndex(cfg.work, set(cfg.scopes))
    out = {"generated": utcnow(), "cap_tokens": cfg.cap, "chars_per_token": CHARS_PER_TOKEN,
           "slice_rows_read": dict(idx.rows_read), "slice_done_kinds": sorted(slice_done_kinds(cfg.work)[0]),
           "slice_complete": slice_done_kinds(cfg.work)[1], "scopes": {}}
    calib, lat = calibration(cfg.work)
    latency = lat or cfg.latency
    all_est = []
    for scope in cfg.scopes:
        keys = idx.unit_ids(scope, cfg.since_day)
        ests, sampled, raw = [], 0, []
        for k in keys:
            rows = idx.units[k]
            _, s, e = plan_rows(rows, cfg.cap)
            ests.append(int(e * calib))
            raw.append(sum(est_tokens_chars(r.line_chars()) for r in rows) + OVERHEAD_TOKENS)
            sampled += s
        all_est += ests
        out["scopes"][scope] = {
            "units": len(keys), "sampled_units": sampled,
            "days": len({k[2] for k in keys}), "keys": len({k[1] for k in keys}),
            "input_tokens_total": sum(ests), "input_tokens_p50": pct(ests, 0.5),
            "input_tokens_p95": pct(ests, 0.95), "input_tokens_max": max(ests, default=0),
            "uncapped_input_tokens_total": int(sum(raw) * calib)}
    units = sum(s["units"] for s in out["scopes"].values())
    out["units_total"] = units
    out["input_tokens_total"] = sum(all_est)
    out["output_tokens_expected"] = units * 260
    out["latency_s_per_call"] = round(latency, 1)
    out["latency_source"] = "calls.jsonl median" if lat else "assumed"
    out["calibration_actual_over_est"] = round(calib, 3)
    out["wall_hours"] = {str(c): round(units * latency / c / 3600, 2) for c in (8, 12)}
    client = swarm_llm.from_args(cfg, required=False)
    out["model_client"] = client.describe() if client else (
        "no model backend configured (export SWARM_LLM_MOCK=1 for a dry run, or SWARM_LLM_BASE_URL, SWARM_LLM_MODEL "
        "and SWARM_LLM_API_KEY for a real model)")
    out["calls_expected"] = f"{units} summary calls plus {units} name-check calls (fewer with --no-audit)"
    path = os.path.join(cfg.work, "summaries", "ESTIMATE.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(out, f, indent=1)
    print(json.dumps(out, indent=1))
    return 0


def calibration(work):
    """Actual input tokens over estimated, and median seconds per call, from calls.jsonl."""
    ratios, secs = [], []
    try:
        with open(os.path.join(work, "summaries", "calls.jsonl")) as f:
            for line in f:
                c = json.loads(line)
                first = c.get("in_first") or (c.get("in") if c.get("attempts") == 1 else None)
                if first and c.get("est_in"):
                    ratios.append(first / c["est_in"])
                    secs.append(c["secs"])
    except (OSError, ValueError):
        pass
    if not ratios:
        return 1.0, None
    return statistics.median(ratios), statistics.median(secs)


# ---------------------------------------------------------------- pilot report

def pilot_md(cfg):
    recs = []
    for name in ("room.jsonl", "agent.jsonl"):
        try:
            with open(os.path.join(cfg.work, "summaries", name)) as f:
                recs += [json.loads(l) for l in f]
        except OSError:
            pass
    if cfg.ids:
        want = set(cfg.ids.split(","))
        recs = [r for r in recs if r["id"] in want]
    else:
        recs = recs[: cfg.n] if cfg.first else recs[-cfg.n:]
    refs = {c["ref"] for r in recs for c in r["cites"]}
    texts = {}
    for kind in sorted(set(AGENT_KINDS)):
        if not refs - set(texts):
            break
        path = os.path.join(cfg.work, "slice", f"slice-{kind}.jsonl")
        if not os.path.exists(path):
            continue
        with open(path, "rb") as f:
            for raw in f:
                try:
                    rec = json.loads(raw)
                except ValueError:
                    continue
                if rec.get("id") in refs:
                    texts[rec["id"]] = rec
    lines = ["# Summarizer pilot (local only, never commit)", "",
             f"Generated {utcnow()}. Spot-check: open 2 cited refs per record.", ""]
    for r in recs:
        flags = " ".join(f for f in ("sampled", "uncited") if r.get(f))
        lines += [f"## {r['id']} ({r['date']}) {flags}", "", r["text"], ""]
        for c in r["cites"]:
            src = texts.get(c["ref"])
            body = format_line(c["n"], src) if src else f"[{c['n']}] (ref not found)"
            lines.append(f"- `{c['ref']}`: {clip(body, 500)}")
        lines.append("")
    path = os.path.join(cfg.work, "summaries", "PILOT.md")
    with open(path, "w") as f:
        f.write("\n".join(lines))
    print(path)
    return 0


# ---------------------------------------------------------------- CLI

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("estimate", "run", "pilot-md", "audit-existing"))
    ap.add_argument("--work", default=swarm_paths.work_default(), help="the work area (default $SWARM_WORK)")
    ap.add_argument("--scope", default="all", choices=("all", "room", "agent"))
    ap.add_argument("--since-day", type=int, default=None, help="only units with day >= this")
    ap.add_argument("--limit", type=int, default=None, help="at most this many new units")
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--max-concurrency", type=int, default=12)
    ap.add_argument("--cap", type=int, default=INPUT_CAP, help="approximate input tokens per call")
    ap.add_argument("--max-attempts", type=int, default=6)
    ap.add_argument("--backoff", type=float, default=10.0, help="first retry delay, seconds; doubles")
    ap.add_argument("--max-consecutive-fail", type=int, default=25)
    ap.add_argument("--no-audit", action="store_true", help="skip the human-name check call")
    ap.add_argument("--wait-slice", action="store_true", help="wait for STATS.json to mark the kinds done")
    ap.add_argument("--poll", type=int, default=120)
    ap.add_argument("--save-prompts", default=None, help="dir to dump prompts (local debugging only)")
    ap.add_argument("--latency", type=float, default=25.0, help="assumed seconds per call for estimate")
    ap.add_argument("--n", type=int, default=10, help="pilot-md: records to show")
    ap.add_argument("--first", action="store_true", help="pilot-md: first n records instead of last n")
    ap.add_argument("--ids", default=None, help="pilot-md: comma-separated ids")
    swarm_llm.add_args(ap, timeout=300)
    cfg = ap.parse_args(argv)
    cfg.work = swarm_paths.need_work(cfg.work, "--work")
    cfg.scopes = ["room", "agent"] if cfg.scope == "all" else [cfg.scope]
    cfg.call_cwd = os.path.join(cfg.work, "summaries", ".cwd")
    os.makedirs(cfg.call_cwd, exist_ok=True)
    if cfg.cmd == "estimate":
        return estimate(cfg)
    if cfg.cmd == "pilot-md":
        return pilot_md(cfg)
    cfg.llm = swarm_llm.from_args(cfg)
    print(f"{utcnow()} model client: {cfg.llm.describe()}", file=sys.stderr, flush=True)
    runner = Runner(cfg)
    signal.signal(signal.SIGTERM, lambda *_: runner.stop.set())  # finish in-flight calls, then exit
    return runner.audit_existing() if cfg.cmd == "audit-existing" else runner.run()


if __name__ == "__main__":
    sys.exit(main())
