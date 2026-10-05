#!/usr/bin/env python3
"""Slice any multi-agent archive given as archive.jsonl into the same slice the AI Village slicer writes.

One JSON object per line, one text unit per object (the input contract, pipeline/slice/README.md):

  {"id": str, "time": ISO-8601, "agent": str, "room": str|null, "kind": "chat"|"memory"|"session"|"event",
   "text": str, "reply_to": str|null}

Humans carry "agent": "human" (pooled into one pseudo-agent); a per-human id may sit in "human_id" (used only to
scrub that id from every text, never written out) and a display name in "human_name" (read only by
pipeline/bundle/human_names.py --archive into the redaction list, never written out). Group-level event rows (goal,
daily_summary and every event that is not sliced) may carry "agent": "human" or any other value: they never make an
agent (a daily_summary naming an agent that has rows of its own stays credited to it). Optional fields:
"model" (the agent's model id, first one wins), "session" (groups session text), and "subkind":
  session: goal | summary (default) | next_goal | thinking | code
  event:   goal (a new group goal starts; text = the goal) | agent_goal (the agent's own goal) |
           daily_summary (someone's written log of the day) | outreach | query; other events are counted, not sliced

Writes <out>/slice/slice-<kind>.jsonl + STATS.json and <out>/meta/{agents,rooms,days,village_goals,agent_goals,
summaries_index,dataset}.json with the AI Village slicer's code (KindWriter, DayMap, slug, family): same files,
same schema, same dedupe, same token counts. A day is a UTC calendar date; day 1 is the date of the first row, and a
day's first_ts is the time of its first row (as the AI Village days.json has it), so a goal that starts at 09:00 on a
day starts on that day. A time with a UTC offset is converted to UTC.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "common"))
import swarm_paths  # noqa: E402
from slicer import KIND_SOURCES, DayMap, KindWriter, family, load_tokenizer, slug, time_key, write_json  # noqa: E402

KINDS = ("chat", "memory", "session", "event")
SESSION_KINDS = {"goal": "session_goal", "summary": "session_summary", "next_goal": "next_goal",
                 "thinking": "thinking", "code": "cc_text"}
EVENT_KINDS = {"daily_summary": "digest_summary", "outreach": "outreach", "query": "history_query"}
SRC = {"chat": "chat", "memory": "mem", "session_goal": "cus", "session_summary": "ev", "next_goal": "ev",
       "thinking": "ev", "cc_text": "cc", "digest_summary": "sum", "outreach": "ev", "history_query": "ev"}
DEFAULT_ROOM = "main"


PER_AGENT_EVENTS = {"agent_goal", "outreach", "query"}  # every other event row is group-level


class ArchiveError(ValueError):
    pass


def utc_key(ts):
    """time_key in UTC: an offset such as +09:00 is applied (time_key alone drops it, which the AI Village tables,
    all UTC, never needed)."""
    s = str(ts).strip().replace(" ", "T")
    try:
        t = dt.datetime.fromisoformat(s[:-1] + "+00:00" if s.endswith("Z") else s)
    except ValueError:
        return time_key(s)
    if t.tzinfo is not None:
        t = t.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return t.strftime("%Y-%m-%dT%H:%M:%S.%f")


def utc_iso(ts):
    return utc_key(ts)[:19] + "Z"


def group_level(r):
    """A goal, a daily log or an event the slice does not keep: it belongs to the group, not to its `agent`."""
    return r["kind"] == "event" and r.get("subkind") not in PER_AGENT_EVENTS


def read_rows(path):
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except ValueError as e:
                raise ArchiveError(f"line {n}: not JSON ({e})")
            if not isinstance(r, dict):
                raise ArchiveError(f"line {n}: not an object")
            yield n, r


def check_row(n, r):
    for k in ("id", "time", "agent", "kind", "text"):
        if k not in r:
            raise ArchiveError(f"line {n}: missing {k!r}")
    if r["kind"] not in KINDS:
        raise ArchiveError(f"line {n}: kind {r['kind']!r} is not one of {', '.join(KINDS)}")
    if not isinstance(r["agent"], str) or not r["agent"].strip():
        raise ArchiveError(f"line {n}: agent must be a non-empty string ('human' for any human)")
    try:
        dt.datetime.fromisoformat(str(r["time"]).strip().replace(" ", "T").replace("Z", "+00:00"))
    except ValueError:
        raise ArchiveError(f"line {n}: time {r['time']!r} is not ISO-8601")


class JsonlSlicer:
    def __init__(self, archive, out, tokenizer="Alibaba-NLP/gte-multilingual-mlm-base", name=None, cite=None,
                 goal=None, log=print):
        self.archive, self.log = archive, log
        self.slice_dir, self.meta_dir = os.path.join(out, "slice"), os.path.join(out, "meta")
        os.makedirs(self.slice_dir, exist_ok=True)
        os.makedirs(self.meta_dir, exist_ok=True)
        self.tokenizer_name = tokenizer
        self.count_tokens = load_tokenizer(tokenizer)
        self.name = name or os.path.splitext(os.path.basename(archive))[0]
        self.cite = cite or f"Data: {self.name}"
        self.default_goal = goal or "The work recorded in this archive"
        self.t0 = time.time()
        self.unknown = Counter()

    # ---------- pass 1: who, where, when, goals, human ids
    def scan(self):
        agents, rooms, dates, goals, agoals = {}, {}, {}, [], []
        human_ids = set()
        rows = 0
        for n, r in read_rows(self.archive):
            check_row(n, r)
            rows += 1
            t = utc_iso(r["time"])
            if t[:10] not in dates or t < dates[t[:10]]:
                dates[t[:10]] = t  # date -> its first row's time
            who = r["agent"].strip()
            if who == "human":
                hid = r.get("human_id")
                if isinstance(hid, str) and len(hid.strip()) >= 3:
                    human_ids.add(hid.strip())
            elif group_level(r):
                pass  # "system", "village", "human" or anything else: a group row makes no agent
            elif who not in agents or t < agents[who]["first"]:
                prev = agents.get(who, {})
                agents[who] = {"first": t, "model": prev.get("model") or r.get("model")}
            elif r.get("model") and not agents[who].get("model"):
                agents[who]["model"] = r["model"]
            if r["kind"] == "chat":
                room = r.get("room") or DEFAULT_ROOM
                if room not in rooms or t < rooms[room]:
                    rooms[room] = t
            if r["kind"] == "event" and r.get("subkind") == "goal":
                goals.append((t, str(r["id"]), r["text"]))
            if r["kind"] == "event" and r.get("subkind") == "agent_goal" and who != "human":
                agoals.append((t, str(r["id"]), who, r["text"]))
        if not rows:
            raise ArchiveError("the archive has no rows")
        self.rows = rows
        return agents, rooms, dates, goals, agoals, human_ids

    def metadata(self, agents, rooms, dates, goals, agoals):
        out, seen = [], set()
        self.agent_slug = {}
        for name, a in sorted(agents.items(), key=lambda kv: (kv[1]["first"], kv[0])):
            s = base = slug(name) or "agent"
            k = 2
            while s in seen or s == "human":
                s, k = f"{base}-{k}", k + 1
            seen.add(s)
            self.agent_slug[name] = s
            model = a.get("model") or name
            out.append({"id": s, "uuid": name, "name": name, "model": model, "participating": True,
                        "created_at": a["first"], "family": family(model)})
        write_json(os.path.join(self.meta_dir, "agents.json"), out)
        self.agents = out

        rr, seen = [], set()
        self.room_slug = {}
        for name, first in sorted(rooms.items(), key=lambda kv: (kv[1], kv[0])):
            s = base = slug(name) or "room"
            k = 2
            while s in seen:
                s, k = f"{base}-{k}", k + 1
            seen.add(s)
            self.room_slug[name] = s
            rr.append({"id": s, "uuid": name, "name": name, "deleted_at": None, "created_at": first})
        write_json(os.path.join(self.meta_dir, "rooms.json"), rr)

        first, last = dt.date.fromisoformat(min(dates)), dt.date.fromisoformat(max(dates))
        days = []
        for i in range((last - first).days + 1):
            d = (first + dt.timedelta(days=i)).isoformat()
            days.append({"day": i + 1, "date": d, "first_ts": dates.get(d), "events": 0})
        self.day_list = days
        self.days = DayMap(days)
        write_json(os.path.join(self.meta_dir, "days.json"), days)

        if not goals:
            goals = [(dates[min(dates)], "default-goal", self.default_goal)]
            self.log(f"no goal events: one group goal for the whole archive ({self.default_goal!r}); give the "
                     f"archive event rows with subkind \"goal\" to split the book into chapters")
        goals.sort()
        vg = []
        for i, (t, gid, text) in enumerate(goals):
            end = goals[i + 1][0] if i + 1 < len(goals) else None
            g = {"uuid": gid, "goal": text, "start": t, "end": end, "first_day": self.days.at(time_key(t))[0],
                 "last_day": self.days.at(time_key(end))[0] if end else None}
            vg.append(g)
        write_json(os.path.join(self.meta_dir, "village_goals.json"), vg)
        self.goals = vg

        ag = []
        for t, gid, who, text in sorted(agoals):
            short = " ".join(text.split()[:4])
            ag.append({"uuid": gid, "agent": self.agent_slug.get(who, slug(who)), "name": text[:80], "short": short,
                       "description": text, "start": t, "end": None})
        write_json(os.path.join(self.meta_dir, "agent_goals.json"), ag)
        write_json(os.path.join(self.meta_dir, "dataset.json"), {"dataset": self.name, "cite": self.cite,
                                                                  "source": "archive.jsonl (pipeline/slice/from_jsonl.py)"})

    # ---------- pass 2: the text units
    def run(self):
        t = time.time()
        agents, rooms, dates, goals, agoals, human_ids = self.scan()
        self.metadata(agents, rooms, dates, goals, agoals)
        scrub = None
        if human_ids:
            scrub = re.compile(r"(?<![\w.@-])(?:" + "|".join(re.escape(h) for h in sorted(human_ids, key=len, reverse=True))
                               + r")(?![\w-])")
        writers = {k: KindWriter(self.slice_dir, k, self.count_tokens, set()) for k in KIND_SOURCES}
        memories = {}
        index = []
        events_skipped = Counter()
        for _, r in read_rows(self.archive):
            kind = self.kind_of(r)
            if kind is None:
                if r["kind"] == "event" and r.get("subkind") not in ("goal", "agent_goal"):
                    events_skipped[r.get("subkind") or "(none)"] += 1
                continue
            text = r["text"] if isinstance(r["text"], str) else None
            if text is not None and scrub is not None:
                text, k = scrub.subn("[REDACTED]", text)
                self.unknown["human_ids_scrubbed"] += k
            who = r["agent"].strip()
            human = who == "human"
            # a log of the day written by the archive's keepers, or by anyone who is not an agent of the archive
            log_entry = kind == "digest_summary" and (human or who not in self.agent_slug)
            agent = "human" if human else self.agent_slug.get(who)
            key = utc_key(r["time"])
            day, date = self.days.at(key)
            rid = re.sub(r"\s+", "_", str(r["id"]))
            sess = r.get("session")
            rec = {"id": f"{SRC[kind]}:{rid}", "src": SRC[kind], "time": key[:19] + "Z", "day": day, "date": date,
                   "agent": None if log_entry else agent,
                   "speaker": "human" if human and not log_entry else "agent",
                   "room": self.room_slug[r.get("room") or DEFAULT_ROOM] if kind == "chat" else None,
                   "session": "cus:" + re.sub(r"\s+", "_", str(sess)) if sess else None,
                   "kind": kind, "text": text}
            if kind == "memory":
                old = memories.get((agent, day))
                if old is None or (key, rec["id"]) > old[0]:
                    memories[(agent, day)] = ((key, rec["id"]), rec)
                continue
            if kind == "digest_summary":
                index.append({"id": rec["id"], "type": "daily", "target": str(day), "summary_date": date, "day": day,
                              "generated_by": r.get("model") or ("archive" if log_entry else agent),
                              "created_at": rec["time"]})
            writers[kind].add(rec, key)
        for (k, rec) in sorted(memories.values(), key=lambda x: x[0]):
            writers["memory"].add(rec, k[0])
        kind_stats = {}
        for kind, w in writers.items():
            kind_stats[kind] = w.finalize()
            s = kind_stats[kind]
            self.log(f"  kind {kind}: rows={s['rows']} tokens={s['tokens']} dropped={s['dropped']}")
        index.sort(key=lambda x: (x["type"], x["day"], x["id"]))
        write_json(os.path.join(self.meta_dir, "summaries_index.json"), index)
        stats = {
            "schema": "swarm-slice/1", "final": True,
            "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "wall_secs": round(time.time() - self.t0, 1), "limit_per_table": None,
            "tokenizer": self.tokenizer_name, "tables_done": ["archive.jsonl"], "final_kinds": sorted(kind_stats),
            "totals": {"rows": sum(s["rows"] for s in kind_stats.values()),
                       "chars": sum(s["chars"] for s in kind_stats.values()),
                       "tokens": sum(s["tokens"] for s in kind_stats.values())},
            "kinds": kind_stats,
            "tables": {"metadata": {"agents": len(self.agents), "rooms": len(self.room_slug), "days": len(self.day_list),
                                    "village_goals": len(self.goals), "agent_goals": len(agoals)},
                       "archive.jsonl": {"rows_read": self.rows, "secs": round(time.time() - t, 1),
                                         "events_not_sliced": dict(events_skipped),
                                         "memories_kept_agent_days": len(memories)}},
            "thinking_shapes": {}, "unknown_refs": dict(self.unknown),
            "source": {"format": "archive.jsonl", "name": self.name, "human_ids": len(human_ids)},
        }
        write_json(os.path.join(self.slice_dir, "STATS.json"), stats)
        self.log(f"all done in {time.time() - self.t0:.1f}s: {stats['totals']['rows']} slice rows, "
                 f"{len(self.agents)} agents, {len(self.room_slug)} rooms, {len(self.day_list)} days, "
                 f"{len(self.goals)} goals")
        return stats

    @staticmethod
    def kind_of(r):
        k, sub = r["kind"], r.get("subkind")
        if k == "chat":
            return "chat"
        if k == "memory":
            return "memory"
        if k == "session":
            return SESSION_KINDS.get(sub or "summary", "session_summary")
        if k == "event":
            return EVENT_KINDS.get(sub)
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("archive", help="archive.jsonl")
    ap.add_argument("--out", default=swarm_paths.work_default(), help="the work area (default $SWARM_WORK); "
                                                                       "writes <out>/slice/ and <out>/meta/")
    ap.add_argument("--name", help="the dataset's name in the bundle manifest (default: the archive file name)")
    ap.add_argument("--cite", help="the data credit line the site shows (default \"Data: <name>\")")
    ap.add_argument("--goal", help="the group goal when the archive has no goal events")
    ap.add_argument("--tokenizer", default="Alibaba-NLP/gte-multilingual-mlm-base",
                    help="HF tokenizer id for ntok, or 'whitespace' (no download)")
    a = ap.parse_args(argv)
    out = swarm_paths.need_work(a.out, "--out")

    def log(msg):
        print(time.strftime("%H:%M:%S"), msg, flush=True)

    try:
        JsonlSlicer(a.archive, out, a.tokenizer, a.name, a.cite, a.goal, log).run()
    except ArchiveError as e:
        raise SystemExit(f"from_jsonl: {a.archive}: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
