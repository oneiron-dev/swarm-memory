"""Slice the AI Village tables into schema-§1 natural-language records.

Streams each gzipped table line by line. Every kind is written in two passes:
pass 1 appends raw records to `<kind>.pass1` and keeps one index entry per
distinct text (sha1 of whitespace-folded lowercase text, earliest wins);
finalize re-reads the winners in time order, counts tokens in batches and
writes `slice-<kind>.jsonl` atomically. A kind is finalized as soon as its
last source table is done, so downstream jobs can start early.
"""

import argparse
import bisect
import gzip
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict

from shapes import UNPARSED, reasoning

TABLE_ORDER = ["summaries", "chat", "events", "claude_code", "sessions", "memories"]
# Last source table of each kind: the kind is final once that table is done.
KIND_SOURCES = {
    "digest_summary": "summaries",
    "chat": "chat",
    "session_summary": "events",
    "next_goal": "events",
    "history_query": "events",
    "outreach": "events",
    "thinking": "events",
    "cc_text": "claude_code",
    "cc_thinking": "claude_code",
    "session_goal": "sessions",
    "memory": "memories",
}
SCRUB = re.compile(r"\[(?:REDACTED|BLOB_REMOVED|IMAGE_REMOVED)\]")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
FAMILY_RULES = [
    ("anthropic", re.compile(r"claude")),
    ("openai", re.compile(r"gpt|(?<![a-z0-9])o[134](?![a-z0-9])")),
    ("google", re.compile(r"gemini")),
    ("xai", re.compile(r"grok")),
    ("deepseek", re.compile(r"deepseek")),
    ("moonshot", re.compile(r"kimi")),
    ("zai", re.compile(r"glm")),
    ("meta", re.compile(r"muse")),
]


def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")


def family(model):
    m = (model or "").lower()
    for fam, rx in FAMILY_RULES:
        if rx.search(m):
            return fam
    return "other"


def time_key(ts):
    """Sortable UTC key 'YYYY-MM-DDTHH:MM:SS.ffffff' from either dataset format."""
    s = ts.strip().replace(" ", "T")
    if s.endswith("Z"):
        s = s[:-1]
    elif len(s) > 19 and s[-6] in "+-" and s[-3] == ":":
        s = s[:-6]
    base, _, frac = s.partition(".")
    return base + "." + (frac + "000000")[:6]


def iso(ts):
    return None if ts is None else time_key(ts)[:19] + "Z"


def fold_hash(text):
    return hashlib.sha1(" ".join(text.lower().split()).encode("utf-8")).digest()


def read_gz(path, limit=None):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for i, line in enumerate(f):
            if limit is not None and i >= limit:
                return
            yield json.loads(line)


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
        f.write("\n")
    os.replace(tmp, path)


class DayMap:
    """Record time -> village day: the last day whose first event is <= the time."""

    def __init__(self, days):
        self.days = days
        live = [d for d in days if d["first_ts"]]
        self.keys = [time_key(d["first_ts"]) for d in live]
        self.live = live
        self.by_date = {d["date"]: d for d in days}
        self.by_num = {d["day"]: d for d in days}

    def at(self, key):
        i = bisect.bisect_right(self.keys, key) - 1
        d = self.live[max(i, 0)]
        return d["day"], d["date"]


def load_tokenizer(name):
    if name == "whitespace":  # tests only
        return lambda texts: [len(t.split()) for t in texts]
    from tokenizers import Tokenizer
    tok = Tokenizer.from_pretrained(name)
    tok.no_truncation()
    tok.no_padding()
    return lambda texts: [len(e.ids) for e in tok.encode_batch(texts, add_special_tokens=False)]


class Sessions:
    """Computer-use session of an agent's event, rebuilt after the events pass.

    START_USING_COMPUTER names the session it opens. CONSOLIDATE (perma
    computer use, 2026-03-24 on) names the session it closes, so events between
    two CONSOLIDATEs belong to the later one. STOP_USING_COMPUTER names none; it
    closes the session the last START opened.
    """

    def __init__(self):
        self.marks = defaultdict(list)  # agent slug -> [(event_index, action, session ref)]

    def add(self, agent, event_index, action, sid):
        self.marks[agent].append((event_index, action, sid))

    def build(self):
        self.keys = {}
        for agent, marks in self.marks.items():
            marks.sort()
            self.keys[agent] = [m[0] for m in marks]

    def at(self, agent, event_index):
        keys = self.keys.get(agent)
        if not keys:
            return None
        marks = self.marks[agent]
        i = bisect.bisect_right(keys, event_index) - 1
        if i >= 0 and keys[i] == event_index:
            _, action, sid = marks[i]
            if action != "STOP_USING_COMPUTER":
                return sid
            i -= 1
            return marks[i][2] if i >= 0 and marks[i][1] == "START_USING_COMPUTER" else None
        if i >= 0 and marks[i][1] == "START_USING_COMPUTER":
            return marks[i][2]
        if i + 1 < len(marks) and marks[i + 1][1] == "CONSOLIDATE":
            return marks[i + 1][2]
        return None


def new_bucket():
    return {"rows": 0, "chars": 0, "tokens": 0}


class KindWriter:
    def __init__(self, out_dir, kind, count_tokens, user_ids, batch_chars=2_000_000, batch_rows=4096):
        self.out_dir, self.kind, self.count_tokens, self.user_ids = out_dir, kind, count_tokens, user_ids
        self.batch_chars, self.batch_rows = batch_chars, batch_rows
        self.pass1_path = os.path.join(out_dir, f".{kind}.pass1")
        self.f = open(self.pass1_path, "wb")
        self.offset = 0
        self.best = {}  # text hash -> (time key, id, offset, length)
        self.c = Counter()
        self.stats = None

    def add(self, rec, tkey):
        text = rec["text"]
        if not isinstance(text, str):
            self.c["not_text"] += 1
            return
        try:
            text.encode("utf-8")
        except UnicodeEncodeError:
            text = rec["text"] = text.encode("utf-8", "replace").decode("utf-8")
            self.c["surrogates_fixed"] += 1
        self.c["seen"] += 1
        if self.user_ids and any(m in self.user_ids for m in UUID.findall(text)):
            text = rec["text"] = UUID.sub(lambda m: "[REDACTED]" if m.group(0) in self.user_ids else m.group(0), text)
            self.c["user_ids_scrubbed"] += 1
        if len(text.strip()) < 3:
            self.c["short"] += 1
            return
        if len(SCRUB.sub("", text).strip()) < 3:
            self.c["scrub_only"] += 1
            return
        line = (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8")
        self.f.write(line)
        h = fold_hash(text)
        cand = (tkey, rec["id"], self.offset, len(line))
        old = self.best.get(h)
        if old is None:
            self.best[h] = cand
        else:
            self.c["dupes"] += 1
            if cand[:2] < old[:2]:
                self.best[h] = cand
        self.offset += len(line)

    def finalize(self, patch=None):
        self.f.close()
        winners = sorted(self.best.values())
        self.best = None
        stats = {"rows": 0, "chars": 0, "tokens": 0, "by_month": defaultdict(new_bucket),
                 "by_agent": defaultdict(new_bucket), "by_day": defaultdict(new_bucket)}
        part = os.path.join(self.out_dir, f"slice-{self.kind}.jsonl.part")
        with open(self.pass1_path, "rb") as src, open(part, "w", encoding="utf-8") as dst:
            batch, chars = [], 0
            for _, _, off, length in winners:
                src.seek(off)
                rec = json.loads(src.read(length))
                if patch:
                    patch(rec)
                batch.append(rec)
                chars += len(rec["text"])
                if len(batch) >= self.batch_rows or chars >= self.batch_chars:
                    self._flush(batch, dst, stats)
                    batch, chars = [], 0
            if batch:
                self._flush(batch, dst, stats)
        os.replace(part, os.path.join(self.out_dir, f"slice-{self.kind}.jsonl"))
        os.remove(self.pass1_path)
        for k in ("by_month", "by_agent", "by_day"):
            stats[k] = dict(sorted(stats[k].items(), key=lambda kv: (str(type(kv[0])), kv[0])))
        stats.update({"in_rows": self.c["seen"], "dropped": {k: v for k, v in self.c.items() if k != "seen"}})
        self.stats = stats
        return stats

    def _flush(self, batch, dst, stats):
        ntoks = self.count_tokens([SCRUB.sub("", r["text"]) for r in batch])
        for rec, n in zip(batch, ntoks):
            rec["ntok"] = n
            dst.write(json.dumps(rec, ensure_ascii=False) + "\n")
            nch = len(rec["text"])
            for bucket in (stats, stats["by_month"][rec["time"][:7]], stats["by_agent"][rec["agent"] or "-"],
                           stats["by_day"][rec["day"]]):
                bucket["rows"] += 1
                bucket["chars"] += nch
                bucket["tokens"] += n


class Slicer:
    def __init__(self, data, out, limit=None, tokenizer="Alibaba-NLP/gte-multilingual-mlm-base", log=print):
        self.data, self.limit, self.log = data, limit, log
        self.slice_dir = os.path.join(out, "slice")
        self.meta_dir = os.path.join(out, "meta")
        os.makedirs(self.slice_dir, exist_ok=True)
        os.makedirs(self.meta_dir, exist_ok=True)
        self.tokenizer_name = tokenizer
        self.count_tokens = load_tokenizer(tokenizer)
        self.t0 = time.time()
        self.user_ids = set()  # filled in metadata(); scrubbed from every text
        self.writers = {k: KindWriter(self.slice_dir, k, self.count_tokens, self.user_ids) for k in KIND_SOURCES}
        self.kind_stats = {}
        self.tables_done = []
        self.table_stats = {}
        self.shapes = defaultdict(Counter)
        self.unknown = Counter()
        self.sessions = Sessions()

    # ---------- helpers ----------
    def path(self, name):
        return os.path.join(self.data, name)

    def rows(self, table):
        return read_gz(self.path(f"{table}.jsonl.gz"), self.limit)

    def agent(self, uuid):
        a = self.agents_by_uuid.get(uuid)
        if a is None:
            self.unknown["agent_uuid"] += 1
            return "unknown"
        return a["id"]

    def rec(self, rid, src, created, kind, text, agent=None, speaker="agent", room=None, session=None, day=None):
        k = time_key(created)
        d, date = (day if day is not None else self.days.at(k))
        return {"id": rid, "src": src, "time": k[:19] + "Z", "day": d, "date": date, "agent": agent,
                "speaker": speaker, "room": room, "session": session, "kind": kind, "text": text}, k

    def emit(self, r):
        rec, k = r
        self.writers[rec["kind"]].add(rec, k)

    # ---------- metadata ----------
    def metadata(self):
        t = time.time()
        agents, seen = [], set()
        for r in sorted(read_gz(self.path("agents.jsonl.gz")), key=lambda r: (r["created_at"], r["id"])):
            s = base = slug(r["name"])
            n = 2
            while s in seen or s == "human":
                s, n = f"{base}-{n}", n + 1
            seen.add(s)
            agents.append({"id": s, "uuid": r["id"], "name": r["name"], "model": r["model_string"],
                           "participating": r["is_participating"], "created_at": iso(r["created_at"]),
                           "family": family(r["model_string"])})
        self.agents_by_uuid = {a["uuid"]: a for a in agents}
        self.agents_by_name = {a["name"]: a for a in agents}
        self.agents_by_slug = {a["id"]: a for a in agents}
        write_json(os.path.join(self.meta_dir, "agents.json"), agents)

        rooms, seen = [], set()
        for r in sorted(read_gz(self.path("chat_rooms.jsonl.gz")), key=lambda r: (r["created_at"], r["id"])):
            s = base = slug(r["name"])
            n = 2
            while s in seen:
                s, n = f"{base}-{n}", n + 1
            seen.add(s)
            rooms.append({"id": s, "uuid": r["id"], "name": r["name"], "deleted_at": iso(r["deleted_at"]),
                          "created_at": iso(r["created_at"])})
        self.rooms_by_uuid = {r["uuid"]: r["id"] for r in rooms}

        for r in self.rows("chat_messages"):
            if r.get("user_speaker_id"):
                self.user_ids.add(r["user_speaker_id"])
        write_json(os.path.join(self.meta_dir, "rooms.json"), rooms)

        with open(self.path("village-transcript.json"), encoding="utf-8") as f:
            tr = json.load(f)
        days = []
        for d in tr["days"]:
            ts = min((e["timestamp"] for e in d.get("events") or [] if e.get("timestamp")), key=time_key, default=None)
            days.append({"day": d["day"], "date": d["date"], "first_ts": iso(ts) if ts else None,
                         "events": len(d.get("events") or [])})
        del tr
        days.sort(key=lambda d: d["day"])
        self.days = DayMap(days)
        write_json(os.path.join(self.meta_dir, "days.json"), days)

        goals = []
        for r in sorted(read_gz(self.path("village_goals.jsonl.gz")), key=lambda r: (r["start_time"], r["id"])):
            g = {"uuid": r["id"], "goal": r["goal"], "start": iso(r["start_time"]), "end": iso(r["end_time"])}
            g["first_day"] = self.days.at(time_key(r["start_time"]))[0]
            g["last_day"] = self.days.at(time_key(r["end_time"]))[0] if r["end_time"] else None
            goals.append(g)
        write_json(os.path.join(self.meta_dir, "village_goals.json"), goals)

        agoals = []
        for r in sorted(read_gz(self.path("agent_goals.jsonl.gz")), key=lambda r: (r["created_at"], r["id"])):
            agoals.append({"uuid": r["id"], "agent": self.agent(r["agent_id"]), "name": r["name"],
                           "short": r["short_name"], "description": r["description"],
                           "start": iso(r.get("start_time")), "end": iso(r.get("end_time"))})
        write_json(os.path.join(self.meta_dir, "agent_goals.json"), agoals)
        self.table_stats["metadata"] = {"agents": len(agents), "rooms": len(rooms), "days": len(days),
                                        "village_goals": len(goals), "agent_goals": len(agoals),
                                        "secs": round(time.time() - t, 1)}

    # ---------- tables ----------
    def t_summaries(self):
        index = []
        for r in self.rows("summaries"):
            typ, target, sdate = r["type"], r.get("summary_target"), r.get("summary_date")
            day = None
            if typ == "daily":
                d = self.days.by_date.get(sdate) if sdate else None
                if d is None and target and target.isdigit():
                    d = self.days.by_num.get(int(target))
                if d is not None:
                    day = (d["day"], d["date"])
            agent = None
            if typ == "agent" and target:
                a = self.agents_by_name.get(target)
                if a is None:
                    self.unknown["summary_agent_name"] += 1
                agent = a["id"] if a else slug(target)
            elif typ == "agent_daily" and target:
                # target is "<agent name>:<day>"
                m = re.fullmatch(r"(.+)[:-](\d+)", target)
                a = m and (self.agents_by_name.get(m.group(1)) or self.agents_by_slug.get(slug(m.group(1))))
                if a and int(m.group(2)) in self.days.by_num:
                    agent = a["id"]
                    d = self.days.by_num[int(m.group(2))]
                    day = (d["day"], d["date"])
            rec = self.rec(f"sum:{r['id']}", "sum", r["created_at"], "digest_summary", r["content"], agent=agent,
                           day=day)
            index.append({"id": rec[0]["id"], "type": typ, "target": agent if typ == "agent" else target,
                          "summary_date": sdate, "day": rec[0]["day"], "generated_by": r.get("generated_by"),
                          "created_at": iso(r["created_at"])})
            self.emit(rec)
        index.sort(key=lambda x: (x["type"], x["day"], x["id"]))
        write_json(os.path.join(self.meta_dir, "summaries_index.json"), index)
        return len(index)

    def t_chat(self):
        n = 0
        for r in self.rows("chat_messages"):
            n += 1
            if r.get("speaker_type") == "agent" and r.get("agent_speaker_id"):
                agent, speaker = self.agent(r["agent_speaker_id"]), "agent"
            else:
                agent, speaker = "human", "human"
            room = self.rooms_by_uuid.get(r.get("room_id"))
            if room is None:
                self.unknown["room_uuid"] += 1
                room = "unknown"
            self.emit(self.rec(f"chat:{r['id']}", "chat", r["created_at"], "chat", r["content"], agent=agent,
                               speaker=speaker, room=room))
        return n

    def t_events(self):
        n = 0
        for r in self.rows("events"):
            n += 1
            d = r.get("data") or {}
            at = d.get("actionType")
            ei = r["event_index"]
            uuid = d.get("agentId") or (d.get("speakerId") if at == "AGENT_TALK" else None)
            if uuid is None:
                continue  # USER_TALK, USER_NAME_CHANGE: humans, covered by chat or dropped
            agent = self.agent(uuid)
            sid = d.get("computerUseSessionId")
            sess = f"cus:{sid}" if sid else None
            ev = f"ev:{ei}"
            ca = r["created_at"]
            if at in ("START_USING_COMPUTER", "CONSOLIDATE", "STOP_USING_COMPUTER"):
                self.sessions.add(agent, ei, at, sess)
            if at == "START_USING_COMPUTER" and isinstance(d.get("sessionGoal"), str):
                self.emit(self.rec(f"{ev}:goal", "ev", ca, "session_goal", d["sessionGoal"], agent=agent, session=sess))
            elif at == "STOP_USING_COMPUTER" and isinstance(d.get("summary"), str):
                self.emit(self.rec(f"{ev}:summary", "ev", ca, "session_summary", d["summary"], agent=agent))
            elif at == "CONSOLIDATE" and isinstance(d.get("nextSessionGoal"), str):
                self.emit(self.rec(f"{ev}:next_goal", "ev", ca, "next_goal", d["nextSessionGoal"], agent=agent,
                                   session=sess))
            elif at == "SEARCH_HISTORY" and (d.get("query") or d.get("answerToQuery")):
                text = f"Q: {d.get('query') or ''}\nA: {d.get('answerToQuery') or ''}"
                self.emit(self.rec(f"{ev}:query", "ev", ca, "history_query", text, agent=agent))
            elif at == "OUTREACH_APPROVAL_REQUEST":
                text = (f"Outreach request via {d.get('medium') or '?'}\nRationale: {d.get('rationale') or ''}\n"
                        f"Message: {d.get('messageContent') or ''}")
                self.emit(self.rec(f"{ev}:outreach", "ev", ca, "outreach", text, agent=agent))
            elif at == "OUTREACH_APPROVAL_RESPONSE":
                # The response echoes the request; the new text is the verdict and the admin's comment.
                verdict = "approved" if d.get("approval") is True else "rejected"
                text = f"Outreach {verdict} ({d.get('medium') or '?'})"
                if d.get("adminComment"):
                    text += f"\nAdmin comment: {d['adminComment']}"
                self.emit(self.rec(f"{ev}:outreach", "ev", ca, "outreach", text, agent=agent, speaker="human"))
            out = d.get("output")
            if out is not None:
                fam = self.agents_by_uuid.get(uuid, {}).get("family", "unknown")
                shape, texts = reasoning(out)
                self.shapes[fam][shape] += 1
                if shape != UNPARSED and not texts:
                    self.shapes[fam][shape + ":no_reasoning"] += 1
                if texts:
                    self.emit(self.rec(f"{ev}:thinking", "ev", ca, "thinking", "\n\n".join(texts), agent=agent,
                                       session=sess))
        self.sessions.build()
        return n

    def t_claude_code(self):
        n = 0
        for r in self.rows("claude_code_messages"):
            n += 1
            if r.get("message_type") != "assistant":
                continue
            blocks = ((r.get("content") or {}).get("message") or {}).get("content")
            if not isinstance(blocks, list):
                self.unknown["cc_nonlist_content"] += 1
                continue
            agent = self.agent(r["agent_id"])
            sess = f"ccs:{r['sdk_session_id']}" if r.get("sdk_session_id") else None
            for i, b in enumerate(blocks):
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text" and isinstance(b.get("text"), str):
                    self.emit(self.rec(f"cc:{r['id']}:{i}", "cc", r["created_at"], "cc_text", b["text"], agent=agent,
                                       session=sess))
                elif b.get("type") == "thinking" and isinstance(b.get("thinking"), str):
                    self.emit(self.rec(f"cc:{r['id']}:{i}", "cc", r["created_at"], "cc_thinking", b["thinking"],
                                       agent=agent, session=sess))
        return n

    def t_sessions(self):
        n = 0
        for r in self.rows("computer_use_sessions"):
            n += 1
            if isinstance(r.get("session_goal"), str):
                self.emit(self.rec(f"cus:{r['id']}", "cus", r["created_at"], "session_goal", r["session_goal"],
                                   agent=self.agent(r["agent_id"]), session=f"cus:{r['id']}"))
        return n

    def t_memories(self):
        best = {}  # (agent uuid, day) -> (time key, id, content)
        n = 0
        for r in self.rows("agent_memories"):
            n += 1
            k = time_key(r["created_at"])
            key = (r["agent_id"], self.days.at(k)[0])
            cand = (k, r["id"])
            old = best.get(key)
            if old is None or cand > old[:2]:
                best[key] = (k, r["id"], r["content"], r["created_at"])
        for (uuid, _), (_, rid, content, created) in sorted(best.items(), key=lambda kv: kv[1][:2]):
            self.emit(self.rec(f"mem:{rid}", "mem", created, "memory", content, agent=self.agent(uuid)))
        self.table_stats["memories_kept_agent_days"] = len(best)
        return n

    # ---------- driver ----------
    def patch_for(self, kind):
        if kind not in ("thinking", "session_summary"):
            return None

        def patch(rec):
            if rec["session"] is None:
                rec["session"] = self.sessions.at(rec["agent"], int(rec["id"].split(":")[1]))
        return patch

    def write_stats(self, final=False):
        stats = {
            "schema": "swarm-slice/1",
            "final": final,
            "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "wall_secs": round(time.time() - self.t0, 1),
            "limit_per_table": self.limit,
            "tokenizer": self.tokenizer_name,
            "tables_done": self.tables_done,
            "final_kinds": sorted(self.kind_stats),
            "totals": {"rows": sum(s["rows"] for s in self.kind_stats.values()),
                       "chars": sum(s["chars"] for s in self.kind_stats.values()),
                       "tokens": sum(s["tokens"] for s in self.kind_stats.values())},
            "kinds": self.kind_stats,
            "tables": self.table_stats,
            "thinking_shapes": {f: dict(c) for f, c in sorted(self.shapes.items())},
            "unknown_refs": dict(self.unknown),
        }
        write_json(os.path.join(self.slice_dir, "STATS.json"), stats)

    def run(self, tables=TABLE_ORDER):
        self.metadata()
        self.log(f"metadata done {self.table_stats['metadata']}")
        fns = {"summaries": self.t_summaries, "chat": self.t_chat, "events": self.t_events,
               "claude_code": self.t_claude_code, "sessions": self.t_sessions, "memories": self.t_memories}
        for table in TABLE_ORDER:
            t = time.time()
            n = fns[table]() if table in tables else 0
            self.tables_done.append(table)
            ts = self.table_stats.setdefault(table, {})
            ts.update({"rows_read": n, "read_secs": round(time.time() - t, 1)})
            for kind, last in KIND_SOURCES.items():
                if last == table:
                    tk = time.time()
                    self.kind_stats[kind] = self.writers[kind].finalize(self.patch_for(kind))
                    s = self.kind_stats[kind]
                    self.log(f"  kind {kind}: rows={s['rows']} tokens={s['tokens']} dropped={s['dropped']} "
                             f"({time.time() - tk:.1f}s)")
            ts["secs"] = round(time.time() - t, 1)
            self.log(f"table {table} done: {ts}")
            self.write_stats(final=False)
        self.write_stats(final=True)
        self.log(f"all done in {time.time() - self.t0:.1f}s")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", required=True, help="the AI Village table dir (gzipped JSONL + village-transcript.json)")
    ap.add_argument("--out", default=os.environ.get("SWARM_WORK"),
                    help="writes <out>/slice/ and <out>/meta/ (default $SWARM_WORK)")
    ap.add_argument("--limit", type=int, default=None, help="rows per table (dry runs)")
    ap.add_argument("--tables", default=",".join(TABLE_ORDER), help="comma list; others are skipped")
    ap.add_argument("--tokenizer", default="Alibaba-NLP/gte-multilingual-mlm-base",
                    help="HF tokenizer id, or 'whitespace' for tests")
    a = ap.parse_args(argv)
    if not a.out:
        raise SystemExit("slicer: set SWARM_WORK=<work area> or pass --out <dir>")

    def log(msg):
        print(time.strftime("%H:%M:%S"), msg, flush=True)

    Slicer(a.data, a.out, a.limit, a.tokenizer, log).run(a.tables.split(","))


if __name__ == "__main__":
    sys.exit(main())
