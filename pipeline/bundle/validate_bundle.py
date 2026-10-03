#!/usr/bin/env python3
"""Validate a built bundle against docs/schema.md section 4.

Checks shapes, cross references (entity ids, summary ids, every cited ref of a summary or finding
present in its day's sources file, every [n] marker bound to a cite, agent and room ids, summary count),
village texts free of wrapper markup, privacy (no user_speaker_id, no bare uuid outside a ref, no human name
from the FULL meta/human_names.json under the build's matching rules, no id from meta/extra_human_ids.json, no
@handle that is not exactly an agent name or slug or a public account, no capitalized name word next to [human];
every JSON string value and object key, every entity id and every served file path, entity pages that nothing lists
included) and sizes. Common-word names ("Will") are checked where the build had no tags (goals, findings, and the rows and
summaries it lists as untagged in redaction_names.json); in tagged text the build's PERSON spans decide. Prints one line per problem; exit code 1 when any ERROR.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_bundle import (EXTRA_IDS, FAMILIES, MARKUP_INLINE_RE, MARKUP_LINE_RE, RULES, BuildError, Names,  # noqa: E402
                          as_list, at_rule_hits, json_path, load_extra_ids, load_human_names, load_rules, load_zipf)

MAX_BUNDLE = 3_000_000
MAX_FILE = 2_000_000  # any on-demand file
MAX_TOTAL = 100_000_000
ENTITY_KEYS = ("id", "text", "label", "aliases", "count", "n_agents", "first_day", "last_day", "examples", "example_days")
UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
REF_PREFIX_RE = re.compile(r"(?:chat|ev|cc|cus|ccs|mem|sum):$")
ISO_Z_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
SUMMARY_KEYS = ("id", "scope", "key", "day", "date", "text", "cites", "model")


class V:
    def __init__(self, data_dir, zipf=None, untagged=None, human_names=None, extra_ids=None, rules=None):
        self.dir = data_dir
        self.zipf = zipf
        self.untagged = untagged
        self.human_names = human_names
        self.extra_ids = extra_ids
        self.rules = rules
        self.n_summaries = 0
        self.errors = []
        self.warnings = []
        self._sources = {}

    def err(self, msg):
        self.errors.append(msg)

    def warn(self, msg):
        self.warnings.append(msg)

    def need(self, cond, msg):
        if not cond:
            self.err(msg)
        return cond

    def load(self, rel):
        path = os.path.join(self.dir, rel)
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return None
        except ValueError as e:
            self.err(f"{rel}: not valid JSON ({e})")
            return None

    def sources(self, day):
        if day not in self._sources:
            self._sources[day] = self.load(f"sources/{day}.json")
        return self._sources[day]

    def keys(self, obj, keys, where):
        if not self.need(isinstance(obj, dict), f"{where}: not an object"):
            return False
        missing = [k for k in keys if k not in obj]
        return self.need(not missing, f"{where}: missing {missing}")

    def int_or_none(self, v, where):
        self.need(v is None or (isinstance(v, int) and not isinstance(v, bool)), f"{where}: not int or null ({v!r})")

    def cited_in_sources(self, rec, where):
        for c in rec.get("cites") or []:
            if not self.keys(c, ("n", "ref"), f"{where} cite"):
                continue
            src = self.sources(rec.get("day"))
            self.need(src is not None and c["ref"] in src, f"{where}: cited ref {c['ref']} not in sources/{rec.get('day')}.json")

    def summary(self, rec, where, scope=None, key=None):
        if not self.keys(rec, SUMMARY_KEYS, where):
            return
        if scope:
            self.need(rec["scope"] == scope, f"{where}: scope {rec['scope']!r} != {scope!r}")
        if key is not None:
            self.need(rec["key"] == key, f"{where}: key {rec['key']!r} != file {key!r}")
        self.need(isinstance(rec["cites"], list), f"{where}: cites not a list")
        self.n_summaries += 1
        markers = {int(m) for m in re.findall(r"\[(\d+)\]", rec.get("text") or "")}
        ns = {c.get("n") for c in rec["cites"] if isinstance(c, dict)}
        if scope != "village":  # AI Digest's own texts carry no [n] citations of ours
            self.need(not markers - ns, f"{where}: markers {sorted(markers - ns)} without a cite")
        if scope == "village":
            text = rec.get("text") or ""
            self.need(not MARKUP_LINE_RE.search(text) and not MARKUP_INLINE_RE.search(text), f"{where}: wrapper markup left")
        self.cited_in_sources(rec, where)

    def run(self, meta_dir=None):
        b = self.load("bundle.json")
        if not self.need(b is not None, "bundle.json missing"):
            return
        if not self.keys(b, ("manifest", "agents", "rooms", "days", "goals", "entities", "summaries", "views"), "bundle"):
            return
        m = b["manifest"]
        if self.keys(m, ("schema", "generated", "dataset", "cite", "range", "counts", "tagger", "summarizer"), "manifest"):
            self.need(m["schema"] == "swarm-bundle/1", f"manifest.schema {m['schema']!r}")
            self.need(isinstance(m["generated"], str) and ISO_Z_RE.match(m["generated"]), "manifest.generated not ISO Z")
            self.need("AI Digest / AI Village" in (m["cite"] or ""), "manifest.cite lacks 'AI Digest / AI Village'")
            self.keys(m["range"], ("first_day", "last_day", "first_date", "last_date"), "manifest.range")
            if self.keys(m["counts"], ("agents", "rooms", "slice_rows", "slice_tokens", "mentions", "entities", "summaries"), "manifest.counts"):
                for k, v in m["counts"].items():
                    self.need(isinstance(v, int), f"manifest.counts.{k} not int")
            if self.keys(m["tagger"], ("name", "checkpoint", "labels", "heads"), "manifest.tagger"):
                self.need(isinstance(m["tagger"]["labels"], list), "manifest.tagger.labels not a list")
                self.need(set(m["tagger"]["heads"]) <= {"ner", "mood", "coref"}, "manifest.tagger.heads unknown head")
                self.need(m["tagger"].get("snap") in (None, "word-v1"), "manifest.tagger.snap unknown")
        labels = set(m.get("tagger", {}).get("labels") or [])

        agent_ids = set()
        for i, a in enumerate(b["agents"]):
            w = f"agents[{i}]"
            if not self.keys(a, ("id", "name", "model", "family", "participating", "first_day", "last_day", "goals", "stats", "top_entities"), w):
                continue
            self.need(a["id"] not in agent_ids, f"{w}: duplicate id {a['id']}")
            agent_ids.add(a["id"])
            self.need(a["family"] in FAMILIES + ("human",), f"{w}: family {a['family']!r}")
            self.need(isinstance(a["participating"], bool), f"{w}: participating not bool")
            self.int_or_none(a["first_day"], f"{w}.first_day")
            self.int_or_none(a["last_day"], f"{w}.last_day")
            for j, g in enumerate(a["goals"]):
                self.keys(g, ("name", "short", "description", "start", "end"), f"{w}.goals[{j}]")
            if self.keys(a["stats"], ("messages", "tokens", "days_active", "sessions", "memories"), f"{w}.stats"):
                for k, v in a["stats"].items():
                    self.need(isinstance(v, int), f"{w}.stats.{k} not int")
        self.need("human" in agent_ids, "agents: pseudo-agent 'human' missing")
        self.need(m.get("counts", {}).get("agents") == len(agent_ids - {"human"}), "manifest.counts.agents != agents")

        room_ids = set()
        for i, r in enumerate(b["rooms"]):
            if self.keys(r, ("id", "name", "deleted", "first_day", "last_day", "messages"), f"rooms[{i}]"):
                room_ids.add(r["id"])

        ent_ids = set()
        for i, e in enumerate(b["entities"]):
            w = f"entities[{i}]"
            if not self.keys(e, ENTITY_KEYS, w):
                continue
            slug = e["id"][2:]
            ok_id = e["id"].startswith("e:") and re.fullmatch(r"[a-z0-9][a-z0-9-]*", slug) and e["id"] not in ent_ids
            if not self.need(ok_id, f"{w}: bad or duplicate id {e['id']}"):
                continue
            ent_ids.add(e["id"])
            self.need("timeline" not in e and "agents" not in e, f"{w}: timeline/agents belong in entities/{slug}.json")
            if labels:
                self.need(e["label"] in labels, f"{w}: label {e['label']!r} not in manifest labels")
            self.need(len(e["examples"]) <= 5, f"{w}: more than 5 examples")
            self.need(e.get("agent") is None or e["agent"] in agent_ids, f"{w}: unknown agent {e.get('agent')}")
            detail = self.load(f"entities/{slug}.json")
            if self.keys(detail, ("id", "agents", "timeline"), f"entities/{slug}.json"):
                self.need(detail["id"] == e["id"], f"entities/{slug}.json: id mismatch")
                self.need(set(detail["agents"]) <= agent_ids, f"entities/{slug}.json: unknown agent")
                self.need(len(detail["agents"]) == e["n_agents"], f"{w}: n_agents != agents in detail")
                tl = detail["timeline"]
                self.need(all(isinstance(p, list) and len(p) == 2 for p in tl) and tl == sorted(tl),
                          f"entities/{slug}.json: timeline not sorted pairs")
                self.need(sum(p[1] for p in tl) == e["count"], f"{w}: timeline sum != count")
                if tl:
                    self.need((tl[0][0], tl[-1][0]) == (e["first_day"], e["last_day"]), f"{w}: first/last day != timeline")
            days = e["example_days"]
            if self.need(len(days) == len(e["examples"]), f"{w}: example_days length"):
                for ref, d in zip(e["examples"], days):
                    src = self.sources(d)
                    self.need(src is not None and ref in src, f"{w}: example {ref} not in sources/{d}.json")
        self.need(m.get("counts", {}).get("entities") == len(ent_ids), "manifest.counts.entities != entities")
        for a in b["agents"]:
            for t in a.get("top_entities") or []:
                self.need(t.get("entity") in ent_ids, f"agent {a.get('id')}: top entity {t.get('entity')} unknown")

        village = b["summaries"].get("village") if isinstance(b["summaries"], dict) else None
        village_ids = set()
        if self.need(isinstance(village, list), "summaries.village not a list"):
            for i, rec in enumerate(village):
                self.summary(rec, f"summaries.village[{i}]", scope="village")
                village_ids.add(rec.get("id"))
            self.n_summaries = 0  # previews; the full records below are the ones counted

        findings = self.load("findings.json")  # optional
        if findings is not None and self.need(isinstance(findings, list), "findings.json: not a list"):
            for i, f in enumerate(findings):
                w = f"findings[{i}]"
                if not self.keys(f, ("title", "body", "cites", "view"), w):
                    continue
                self.need(isinstance(f["title"], str) and isinstance(f["body"], str), f"{w}: title/body not strings")
                self.need(f["view"] is None or isinstance(f["view"], str), f"{w}: view not a string")
                if not self.need(isinstance(f["cites"], list), f"{w}: cites not a list"):
                    continue
                for c in f["cites"]:
                    if not self.keys(c, ("n", "ref", "day"), f"{w} cite"):
                        continue
                    src = self.sources(c["day"])
                    self.need(src is not None and c["ref"] in src, f"{w}: cited ref {c['ref']} not in sources/{c['day']}.json")
                markers = {int(m) for m in re.findall(r"\[(\d+)\]", f["body"] if isinstance(f["body"], str) else "")}
                ns = {c.get("n") for c in f["cites"] if isinstance(c, dict)}
                self.need(not markers - ns, f"{w}: markers {sorted(markers - ns)} without a cite")

        day_nums = []
        for i, d in enumerate(b["days"]):
            w = f"days[{i}]"
            if not self.keys(d, ("day", "date", "village_goal", "active_agents", "counts", "top_entities", "summary"), w):
                continue
            day_nums.append(d["day"])
            self.keys(d["counts"], ("chat", "sessions", "thinking", "memories", "mentions"), f"{w}.counts")
            self.need(set(d["active_agents"]) <= agent_ids, f"{w}: unknown active agent")
            self.need(d["summary"] is None or d["summary"] in village_ids, f"{w}: summary {d['summary']} unknown")
            for t in d["top_entities"]:
                self.need(t.get("entity") in ent_ids, f"{w}: top entity {t.get('entity')} unknown")
        self.need(day_nums == sorted(set(day_nums)), "days not sorted unique")

        for i, g in enumerate(b["goals"]):
            self.keys(g, ("goal", "start", "end", "first_day", "last_day"), f"goals[{i}]")

        views = b["views"]
        if self.keys(views, ("timeline", "interactions", "mood"), "views"):
            tl = views["timeline"]
            if self.keys(tl, ("days", "series"), "views.timeline"):
                n = len(tl["days"])
                for s, per in tl["series"].items():
                    for k, arr in per.items():
                        self.need(len(arr) == n, f"views.timeline.series.{s}.{k}: length {len(arr)} != {n}")
            it = views["interactions"]
            if self.keys(it, ("nodes", "edges"), "views.interactions"):
                nodes = {nd.get("id") for nd in it["nodes"]}
                self.need(nodes <= agent_ids, "views.interactions: unknown node")
                for j, e in enumerate(it["edges"]):
                    w = f"views.interactions.edges[{j}]"
                    if not self.keys(e, ("from", "to", "w", "kinds", "by_day"), w):
                        continue
                    self.need(e["from"] in nodes and e["to"] in nodes, f"{w}: endpoint not a node")
                    self.need(e["from"] != e["to"], f"{w}: self edge")
                    self.need(e["w"] == e["kinds"].get("mention", 0) + e["kinds"].get("reply", 0), f"{w}: w != mention + reply")
                    self.need(sum(p[1] for p in e["by_day"]) == e["w"], f"{w}: by_day sum != w")
            mood = views["mood"]
            if self.keys(mood, ("days", "agents"), "views.mood"):
                n = len(mood["days"])
                for a, vad in mood["agents"].items():
                    for k, lo in (("v", -1), ("a", 0), ("d", 0)):
                        arr = vad.get(k, [])
                        self.need(len(arr) == n, f"views.mood.agents.{a}.{k}: length != days")
                        self.need(all(x is None or lo <= x <= 1 for x in arr), f"views.mood.agents.{a}.{k}: out of range")

        # on-demand files
        # full AI Digest daily texts: summaries/village/<day>.json (one record each) or one summaries/village.json
        full = self.load("summaries/village.json")
        if full is None:
            full = []
            d = os.path.join(self.dir, "summaries", "village")
            for fn in sorted(os.listdir(d)) if os.path.isdir(d) else []:
                rec = self.load(f"summaries/village/{fn}")
                if self.need(isinstance(rec, dict) and str(rec.get("day")) == fn[:-5], f"summaries/village/{fn}: day mismatch"):
                    full.append(rec)
        if self.need(isinstance(full, list), "summaries/village.json not a list"):
            for i, rec in enumerate(full):
                self.summary(rec, f"village full text {rec.get('day')}", scope="village")
            self.need({r.get("id") for r in full} == village_ids, "full village texts != bundle village previews")
        for scope, sub in (("agent", "summaries/agent"), ("room", "summaries/room")):
            d = os.path.join(self.dir, sub)
            for fn in sorted(os.listdir(d)) if os.path.isdir(d) else []:
                key = fn[:-5]
                recs = self.load(f"{sub}/{fn}")
                if self.need(isinstance(recs, list), f"{sub}/{fn}: not a list"):
                    for i, rec in enumerate(recs):
                        self.summary(rec, f"{sub}/{fn}[{i}]", scope=scope, key=key)
                if scope == "agent":
                    self.need(key in agent_ids, f"{sub}/{fn}: unknown agent")
                else:
                    self.need(key in room_ids, f"{sub}/{fn}: unknown room")
        self.need(self.n_summaries == m.get("counts", {}).get("summaries"),
                  f"summary records ({self.n_summaries}) != manifest.counts.summaries ({m.get('counts', {}).get('summaries')})")
        d = os.path.join(self.dir, "memory")
        for fn in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            doc = self.load(f"memory/{fn}")
            w = f"memory/{fn}"
            if not self.keys(doc, ("agent", "days"), w):
                continue
            self.need(doc["agent"] == fn[:-5] and doc["agent"] in agent_ids, f"{w}: agent mismatch")
            for i, row in enumerate(doc["days"]):
                if not self.keys(row, ("day", "ref", "chars", "entities", "added", "dropped", "jaccard_prev", "excerpt"), f"{w}[{i}]"):
                    continue
                for k in ("entities", "added", "dropped"):
                    self.need(set(row[k]) <= ent_ids, f"{w}[{i}].{k}: unknown entity")
                j = row["jaccard_prev"]
                self.need(j is None or 0 <= j <= 1, f"{w}[{i}]: jaccard_prev out of range")
                self.need(len(row["excerpt"]) <= 400, f"{w}[{i}]: excerpt over 400 chars")
        d = os.path.join(self.dir, "sources")
        for fn in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            doc = self.sources(int(fn[:-5])) if fn[:-5].isdigit() else self.load(f"sources/{fn}")
            w = f"sources/{fn}"
            self.need(fn[:-5].isdigit(), f"{w}: name is not a day number")
            if not self.need(isinstance(doc, dict), f"{w}: not an object"):
                continue
            for ref, row in doc.items():
                if not self.keys(row, ("time", "agent", "room", "kind", "text", "spans"), f"{w} {ref}"):
                    continue
                if not self.need(isinstance(row["text"], str) and isinstance(row["spans"], list),
                                 f"{w} {ref}: text not a string or spans not a list"):
                    continue
                self.need(row["agent"] is None or row["agent"] in agent_ids, f"{w} {ref}: unknown agent {row['agent']!r}")
                self.need(row["room"] is None or row["room"] in room_ids, f"{w} {ref}: unknown room {row['room']!r}")
                self.need(row["kind"] is None or isinstance(row["kind"], str), f"{w} {ref}: kind not a string")
                self.need(len(row["text"]) <= 600, f"{w} {ref}: text over 600 chars")
                for s in row["spans"]:
                    self.need(isinstance(s, list) and len(s) == 3 and 0 <= s[0] < s[1] <= len(row["text"]),
                              f"{w} {ref}: span {s} outside text")

        self.privacy_and_sizes(meta_dir, agent_ids, ent_ids)

    def full_names(self, meta_dir, agent_ids):
        """The FULL authoritative list under the build's rules (nothing pruned)."""
        if not meta_dir and not self.human_names:
            self.err("no meta/ dir: the human-name check cannot run")
            return None
        try:
            hn = load_human_names(self.human_names or os.path.join(meta_dir, "human_names.json"))
            extra = load_extra_ids(self.extra_ids or os.path.join(meta_dir or "", EXTRA_IDS))
            self.rule_data = load_rules(self.rules or os.path.join(meta_dir or "", RULES))
        except BuildError as e:
            self.err(str(e))
            return None
        agents = [{"id": a, "name": a} for a in agent_ids]
        if meta_dir:
            agents += [{"id": a.get("id"), "name": a.get("name")}
                       for a in as_list(_read(os.path.join(meta_dir, "agents.json")), "agents")
                       if isinstance(a, dict) and a.get("id")]
        names = Names(agents, hn, zipf=self.zipf or load_zipf(None), extra_ids=extra, rules=self.rule_data)
        self.names_source = (f"meta/human_names.json ({names.human_count} names) + {EXTRA_IDS} ({len(names.extra_ids)} "
                             f"ids) + {RULES} ({sum(len(v) for v in self.rule_data.values())} rules)")
        return names

    def untagged_texts(self):
        """(where, text) of every served string the build redacted without tags: there common-word names count."""
        untagged = self.untagged
        if untagged is None:  # standalone run: the build's list sits beside the served data path
            saved = _read(os.path.join(os.path.dirname(os.path.abspath(self.dir)), "redaction_names.json"))
            untagged = (saved or {}).get("untagged", []) if isinstance(saved, dict) else []
        untagged = set(untagged)
        b = _obj(self.load("bundle.json"))
        for g in _objs(b.get("goals")):
            yield "goals", g.get("goal")
        for d in _objs(b.get("days")):
            yield f"days[{d.get('day')}].village_goal", d.get("village_goal")
        for a in _objs(b.get("agents")):
            for g in _objs(a.get("goals")):
                for k in ("name", "short", "description"):
                    yield f"agents[{a.get('id')}].goals", g.get(k)
        for f in _objs(self.load("findings.json")):  # malformed findings are reported by the shape check
            yield "findings.json", f.get("title")
            yield "findings.json", f.get("body")

        def cites_untagged(rec):
            return any(f"row:{c.get('ref')}" in untagged for c in _objs(rec.get("cites")))
        for v in _objs(_obj(b.get("summaries")).get("village")):
            if cites_untagged(v):
                yield f"village {v.get('day')}", v.get("text")
        for root, _, files in os.walk(self.dir):
            for fn in files:
                rel = os.path.relpath(os.path.join(root, fn), self.dir)
                if rel.startswith("sources/"):
                    for ref, row in _obj(self.load(rel)).items():
                        if f"row:{ref}" in untagged and isinstance(row, dict):
                            yield f"{rel} {ref}", row.get("text")
                elif rel.startswith("memory/"):
                    for row in _objs(_obj(self.load(rel)).get("days")):
                        if f"row:{row.get('ref')}" in untagged:
                            yield rel, row.get("excerpt")
                elif rel.startswith(("summaries/agent/", "summaries/room/")):
                    for rec in _objs(self.load(rel)):
                        if f"summary:{rec.get('id')}" in untagged:
                            yield rel, rec.get("text")
                elif rel.startswith("summaries/village/"):
                    rec = _obj(self.load(rel))
                    if cites_untagged(rec):
                        yield rel, rec.get("text")

    def privacy_and_sizes(self, meta_dir, agent_ids, ent_ids=()):
        total = 0
        names = self.full_names(meta_dir, agent_ids)
        if names is not None:
            for eid in ent_ids:  # served ids never carry a listed name or extra id
                if _ident_has_human(names, eid[2:]):
                    self.err(f"entity id {eid} contains a listed human name")
            for r in self.rule_data["at"]:  # per-location tokens: gone from their location
                text = _at(self.load(r["file"]) if os.path.exists(os.path.join(self.dir, r["file"])) else None,
                           json_path(r["path"]))
                if isinstance(text, str) and at_rule_hits(text, r["token"]):
                    self.err(f"{r['file']} {r['path'][:60]}: a per-location rule token is still there")
            leaks = collections.Counter(where.split(" ")[0] for where, text in self.untagged_texts()
                                        if isinstance(text, str) and names.common_matches(UUID_RE.sub(" ", text)))
            for where, n in sorted(leaks.items()):
                self.err(f"{where}: {n} untagged strings still contain a capitalized common-word human name")
        for root, _, files in os.walk(self.dir):
            for fn in files:
                path = os.path.join(root, fn)
                rel = os.path.relpath(path, self.dir)
                size = os.path.getsize(path)
                total += size
                if rel != "bundle.json":
                    self.need(size < MAX_FILE, f"{rel} is {size} bytes (on-demand limit {MAX_FILE})")
                with open(path, encoding="utf-8", errors="replace") as f:
                    raw = f.read()
                # every served path, listed or not: its file and dir names are served too
                if names is not None and any(_ident_has_human(names, part)
                                             for part in os.path.splitext(rel)[0].split(os.sep)):
                    self.err(f"path {rel} contains a listed human name")
                if "user_speaker_id" in raw:
                    self.err(f"{rel}: contains user_speaker_id")
                for mt in UUID_RE.finditer(raw):
                    if not REF_PREFIX_RE.search(raw[max(0, mt.start() - 5):mt.start()]):
                        self.err(f"{rel}: bare uuid {mt.group()[:8]}… outside a ref")
                        break
                if names is not None:
                    try:
                        doc = json.loads(raw)
                    except ValueError:
                        continue
                    # refs and other uuids are not prose: a hex group such as "bebb" is no name there
                    rules = collections.Counter()
                    for s in _strings(doc):
                        found = names.human_found(UUID_RE.sub(" ", s))
                        rules.update({r for _, _, r in found})
                        rules["strings"] += bool(found)
                    if rules["strings"]:
                        what = ", ".join(f"{r} {n}" for r, n in sorted(rules.items()) if r != "strings")
                        self.err(f"{rel}: {rules['strings']} strings still contain a listed human name ({what})")
        b = os.path.join(self.dir, "bundle.json")
        if os.path.exists(b):
            size = os.path.getsize(b)
            self.need(size < MAX_BUNDLE, f"bundle.json is {size} bytes (limit {MAX_BUNDLE})")
        self.need(total < MAX_TOTAL, f"data/ is {total} bytes (limit {MAX_TOTAL})")
        self.total = total


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return None


def _obj(x) -> dict:
    return x if isinstance(x, dict) else {}


def _objs(x) -> list:
    """The dict items of a list (anything else is the shape check's to report)."""
    return [i for i in x if isinstance(i, dict)] if isinstance(x, list) else []


def _at(doc, keys):
    for k in keys:
        try:
            doc = doc[k]
        except (KeyError, IndexError, TypeError):
            return None
    return doc


def _ident_has_human(names, ident: str) -> bool:
    """An id or file-name part (slug words joined by "-", usually lowercase) that carries a listed name, an extra
    id or an @handle; title case is tried too."""
    return bool(names.human_intervals(ident.replace("-", " ")) or names.has_human(ident))


def _strings(obj):
    """Every string value and every object key."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v)


def validate(data_dir, meta_dir=None, zipf=None, untagged=None, human_names=None, common_words=None, extra_ids=None,
             rules=None):
    """`zipf` (or `common_words`, a JSON {word: zipf}) replaces the wordfreq package; `untagged` are the rows
    and summaries the build redacted without tags (default: <data>/../redaction_names.json); `extra_ids` is
    the extra id list (default meta/extra_human_ids.json)."""
    v = V(data_dir, zipf=zipf or (load_zipf(common_words) if common_words else None), untagged=untagged,
          human_names=human_names, extra_ids=extra_ids, rules=rules)
    v.run(meta_dir)
    return v


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("data", nargs="?", default="/home/lexi/calc-nvme/hackathon-20261004/bundle/data")
    ap.add_argument("--meta", help="meta/ dir with human_names.json and agents.json (default <data>/../../meta)")
    ap.add_argument("--common-words", help="JSON {word: zipf} instead of the wordfreq package (tests)")
    args = ap.parse_args(argv)
    meta = args.meta
    if meta is None:
        guess = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(args.data))), "meta")
        meta = guess if os.path.isdir(guess) else None
    v = validate(args.data, meta, common_words=args.common_words)
    for w in v.warnings[:50]:
        print("WARN ", w)
    if len(v.warnings) > 50:
        print(f"WARN  … {len(v.warnings) - 50} more warnings")
    for e in v.errors[:200]:
        print("ERROR", e)
    if len(v.errors) > 200:
        print(f"ERROR … {len(v.errors) - 200} more errors")
    size = os.path.getsize(os.path.join(args.data, "bundle.json")) if os.path.exists(os.path.join(args.data, "bundle.json")) else 0
    print(f"{'FAIL' if v.errors else 'OK'}: {len(v.errors)} errors, {len(v.warnings)} warnings; "
          f"bundle.json {size / 1e6:.2f} MB, data/ {getattr(v, 'total', 0) / 1e6:.2f} MB; human-name check: "
          f"{getattr(v, 'names_source', 'off')}")
    return 1 if v.errors else 0


if __name__ == "__main__":
    sys.exit(main())
