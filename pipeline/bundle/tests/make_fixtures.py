#!/usr/bin/env python3
"""Write a small synthetic work area (meta/ slice/ tags/ summaries/) for the bundle tests.

Everything here is invented: fake agents, fake rooms, fake people, fake projects. No dataset text.
Usage: python make_fixtures.py <root> [--no-tags] [--no-summaries] [--no-findings]
"""
from __future__ import annotations

import argparse
import json
import os

AGENTS = [
    {"id": "agent-alpha", "uuid": "a1", "name": "Agent Alpha", "model": "claude-alpha-1", "participating": True,
     "created_at": "2030-01-01T09:00:00Z", "family": "anthropic"},
    {"id": "agent-beta-2-5", "uuid": "a2", "name": "Agent Beta 2.5", "model": "gpt-beta-2.5", "participating": True,
     "created_at": "2030-01-01T09:00:00Z", "family": "openai"},
    {"id": "agent-beta-2", "uuid": "a3", "name": "Agent Beta 2", "model": "gpt-beta-2", "participating": False,
     "created_at": "2030-01-01T09:00:00Z", "family": "openai"},
    {"id": "gamma-pro-agent-code", "uuid": "a4", "name": "Gamma Pro (Agent Code)", "model": "claude-code::gamma",
     "participating": True, "created_at": "2030-01-02T09:00:00Z", "family": "anthropic"},
    {"id": "o9", "uuid": "a5", "name": "o9", "model": "o9-fake", "participating": True,
     "created_at": "2030-01-02T09:00:00Z", "family": "openai"},
]
ROOMS = [{"id": "general", "uuid": "r1", "name": "general", "deleted_at": None},
         {"id": "room-x", "uuid": "r2", "name": "room-x", "deleted_at": "2030-01-04T00:00:00Z"}]
DAYS = [{"day": d, "date": f"2030-01-0{d}", "first_ts": f"2030-01-0{d}T17:00:00Z"} for d in range(1, 5)]
VILLAGE_GOALS = [{"goal": "Open a pretend bakery on the moon", "start_time": "2030-01-01T17:00:00Z",
                  "end_time": "2030-01-03T17:00:00Z"},
                 {"goal": "Write a fake almanac", "start_time": "2030-01-03T17:00:00Z", "end_time": None}]
AGENT_GOALS = [{"agent": "agent-alpha", "name": "Bake", "short_name": "bake",
                "description": "Bake fake bread for the vexa-led club. Ask Will about the oven. Will knows ovens.",
                "start_time": None, "end_time": None}]
# "vexa": a display name typed in lowercase and copied in lowercase; not an English word, so it is matched in any
# case. "Oven" and "Will": common words, redacted where oneiroNER tags them PERSON, or in untagged text when
# capitalized and not sentence-initial.
HUMAN_NAMES = ["Zed Quill", "quill_77", "Hello", "a", "Oven", "vexa", "Will", "Brenna"]
# usernames / handles of fake contributors that are no display name (meta/extra_human_ids.json)
EXTRA_IDS = ["zzq-prober-dev", "zzq-prober"]
# meta/extra_human_rules.json (its per-location rule is added in build, where the row id is known)
RULES = {"exact": ["Moss"], "ids": ["QZ555000"], "drop_entities": ["almanac"], "profile_allow": ["zzq-org"]}
# English zipf frequencies for the fixture words (stands in for the wordfreq package); absent words are 0
COMMON_WORDS = {"oven": 4.05, "zed": 3.02, "quill": 3.11, "doe": 4.5, "bakery": 3.6, "asked": 5.1, "today": 5.6,
                "ships": 4.2}
LABELS = ["THING", "PERSON", "PLACE"]

_n = 0


def uid() -> str:
    global _n
    _n += 1
    return f"00000000-0000-4000-8000-{_n:012d}"


def row(kind, src, day, hhmm, agent, text, room=None, session=None, speaker=None, part=None, **extra):
    rid = f"{src}:{uid()}" + (f":{part}" if part is not None else "")
    d = DAYS[day - 1]
    rec = {"id": rid, "src": src, "time": f"{d['date']}T{hhmm}:00Z", "day": day, "date": d["date"],
           "agent": agent, "speaker": speaker or ("human" if agent == "human" else "agent"), "room": room,
           "session": session, "kind": kind, "text": text, "ntok": len(text.split())}
    rec.update(extra)
    return rec


# (kind, src, day, hh:mm, agent, room, text, [(surface, label), ...], mood or None)
CHAT = [
    (1, "18:00", "agent-alpha", "general", "Hello Agent Beta 2.5, shall we open the Moon Bakery today?",
     [("Moon Bakery", "THING")], (0.6, 0.5, 0.5)),
    (1, "18:03", "agent-beta-2-5", "general", "Yes Agent Alpha, the moon bakery is a fine plan. Thanks Zed Quill for the idea!",
     [("moon bakery", "THING"), ("Zed Quill", "PERSON")], (0.7, 0.4, 0.5)),
    (1, "18:05", "human", "general", "go team, ask quill_77 about flour", [("flour", "THING")], None),
    (1, "18:30", "agent-beta-2", "general", "agent-alpha I can review the oven at Crater Nine. [REDACTED]",
     [("Crater Nine", "PLACE"), ("[REDACTED]", "THING")], (0.2, 0.6, 0.4)),
    (1, "18:31", "agent-alpha", "general", "Agent Beta 2.5 and Agent Beta 2 both helped. Dana Fakename sent a note.",
     [("Agent Beta 2.5", "THING"), ("Dana Fakename", "PERSON")], (0.5, 0.5, 0.5)),
    (2, "18:00", "agent-alpha", "room-x", "The Moon Bakery needs an oven at Crater Nine.",
     [("The Moon Bakery", "THING"), ("Crater Nine", "PLACE")], (0.4, 0.3, 0.6)),
    (2, "19:00", "agent-beta-2-5", "room-x", "Agent Alpha, I placed the oven order. Receipt: orders@example.invalid, oven.",
     [("oven", "THING"), ("orders@example.invalid", "THING"), ("orders", "THING"), ("oven", "THING")],
     (0.5, 0.5, 0.5)),
    (3, "18:00", "gamma-pro-agent-code", "general", "Gamma here: the almanac draft mentions the Moon  Bakery.",
     [("almanac", "THING"), ("Moon  Bakery", "THING")], (0.6, 0.6, 0.6)),
    # raw tagger noise: part-word PERSON spans ("o" of the agent o9, "auli" of a person's name)
    (4, "18:00", "agent-alpha", "general", "Thanks o9, and Pauli too.", [("o", "PERSON"), ("auli", "PERSON")], None),
    # a lowercase display name, copied in lowercase more often than not
    (3, "19:00", "o9", "room-x", "vexa asked for a recipe card.", [], None),
    (3, "19:20", "o9", "room-x", "Sent the card, thanks vexa.", [], None),
    (3, "19:40", "o9", "room-x", "vexa is back with Vexa's notes.", [], None),
    # a common-word display name, tagged PERSON in lowercase
    (4, "20:00", "agent-beta-2-5", "general", "Thanks to will for the oven tip.", [("will", "PERSON")], None),
    # a contributor's handle tagged as a thing, @handles: a person, an agent, a public account
    (4, "21:00", "o9", "room-x", "Thanks zzq-prober and @zzq-prober-dev for the cleanup, and @Agent Alpha too. "
     "Ping @aidigest_ and @fakeperson99.", [("zzq-prober-dev", "THING"), ("zzq-prober", "THING")], None),
    # identifiers that are no names: a salutation, an order id, a phone, an encoded email, a username next to "viewer",
    # an ALL-CAPS buyer, profile URLs (one on the allowlist), an exact-case name and a per-location token
    (4, "22:00", "agent-alpha", "room-x", "Hi Quorra, order QZ918273 ships today; call +1 555 201 7788 or mail "
     "qv%40example.invalid. Viewer zzqx77 and buyer QUORRA VANTE asked; see github.com/zzqhub/repo, "
     "zzqblog.substack.com and github.com/ai-village-agents/site. Moss said the moss grew; Copper too.", [], None),
]


def spans_for(text, items):
    out, after = [], {}
    for surface, label in items:
        s = text.find(surface, after.get(surface, 0))
        if s < 0:
            raise ValueError(f"{surface!r} not in {text!r}")
        out.append({"s": s, "e": s + len(surface), "label": label, "text": surface, "p": 0.9})
        after[surface] = s + len(surface)
    return out


def build(root, tags=True, summaries=True, findings=True):
    for sub in ("meta", "slice", "tags", "summaries"):
        os.makedirs(os.path.join(root, sub), exist_ok=True)

    def dump(rel, obj):
        with open(os.path.join(root, rel), "w") as f:
            json.dump(obj, f, indent=1)

    dump("meta/agents.json", AGENTS)
    dump("meta/rooms.json", ROOMS)
    dump("meta/days.json", DAYS)
    dump("meta/village_goals.json", VILLAGE_GOALS)
    dump("meta/agent_goals.json", AGENT_GOALS)
    dump("meta/human_names.json", HUMAN_NAMES)
    dump("meta/extra_human_ids.json", EXTRA_IDS)
    dump("common_words.json", COMMON_WORDS)

    slices = {}
    tag_rows = {}

    def add(rec, items=(), mood=None):
        slices.setdefault(rec["kind"], []).append(rec)
        if items or mood:
            t = {"id": rec["id"], "model": "fake0000fake0000", "decode": "v1", "spans": spans_for(rec["text"], items)}
            if mood:
                t["mood"] = {"v": mood[0], "a": mood[1], "d": mood[2]}
            tag_rows.setdefault(rec["kind"], []).append(t)
        return rec

    chat_ids = []
    for day, hhmm, agent, room, text, items, mood in CHAT:
        chat_ids.append(add(row("chat", "chat", day, hhmm, agent, text, room=room), items, mood)["id"])
    dump("meta/extra_human_rules.json", dict(RULES, at=[{"file": "sources/4.json", "token": "Copper",
                                                          "path": f'$["{chat_ids[14]}"]["text"]'}]))

    sess = "cus:" + uid()
    add(row("session_goal", "cus", 1, "19:00", "agent-alpha", "Find an oven supplier for the Moon Bakery", session=sess),
        [("Moon Bakery", "THING")])
    add(row("session_summary", "ev", 1, "20:00", "agent-alpha", "Found two oven suppliers near Crater Nine.",
            session=sess, part="summary"), [("Crater Nine", "PLACE")])
    add(row("next_goal", "ev", 2, "17:30", "agent-beta-2-5", "Confirm the oven delivery with Agent Alpha.",
            session="cus:" + uid(), part="next_goal"))
    add(row("thinking", "ev", 2, "18:10", "agent-beta-2-5", "I think the Moon Bakery needs flour first.", part="thinking"),
        [("Moon Bakery", "THING"), ("flour", "THING")], (0.3, 0.7, 0.4))
    add(row("outreach", "ev", 4, "12:00", "agent-beta-2", "Reply from a human: happy to help with the almanac.",
            speaker="human", part="response"))
    ccs = "ccs:fake-session-1"
    add(row("cc_text", "cc", 3, "18:20", "gamma-pro-agent-code", "Drafted the almanac index for Agent Beta 2.5.",
            session=ccs, part=1), [("almanac", "THING")])
    add(row("cc_thinking", "cc", 3, "18:21", "gamma-pro-agent-code", "The almanac needs a calendar.", session=ccs, part=0),
        [("almanac", "THING")])

    mem_texts = [
        (1, "Memory: Moon Bakery project. Oven at Crater Nine.", [("Moon Bakery", "THING"), ("Crater Nine", "PLACE")]),
        (2, "Memory: Moon Bakery project. Oven at Crater Nine. Need flour. Zed Quill gave advice.",
         [("Moon Bakery", "THING"), ("Crater Nine", "PLACE"), ("flour", "THING"), ("Zed Quill", "PERSON")]),
        (3, "Memory: Moon Bakery closed. Now the almanac.", [("Moon Bakery", "THING"), ("almanac", "THING")]),
    ]
    for day, text, items in mem_texts:
        add(row("memory", "mem", day, "16:00", "agent-alpha", text), items)
    add(row("memory", "mem", 2, "16:00", "agent-beta-2-5", "Memory: placed oven order."), [("oven", "THING")])
    add(row("memory", "mem", 3, "16:00", "agent-beta-2-5", "Memory: zzq-prober-dev fixed the oven."),
        [("zzq-prober-dev", "THING"), ("oven", "THING")])

    dig1 = add(row("digest_summary", "sum", 1, "23:00", None,
                   "<list_of_chronological_events>\n1. vexa opened the village.\n</list_of_chronological_events>\n\n"
                   "<narrative_summary>\nDay 1: the agents planned a moon bakery. Hello Zed Quill.\n</narrative_summary>\n",
                   speaker="agent", digest_type="daily", summary_date="2030-01-01", summary_target=None,
                   generated_by="fake-model"), [("moon bakery", "THING")])
    add(row("digest_summary", "sum", 2, "23:00", None, "Day 2: the oven was ordered at Crater Nine.", speaker="agent",
            digest_type="daily", summary_date=None, summary_target="2", generated_by="fake-model"))
    add(row("digest_summary", "sum", 3, "23:00", "agent-alpha", "Agent profile: Agent Alpha likes ovens.", speaker="agent",
            digest_type="agent", summary_date=None, summary_target="Agent Alpha", generated_by="fake-model"))

    for kind, rows in slices.items():
        with open(os.path.join(root, f"slice/slice-{kind}.jsonl"), "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
    stats = {k: {"rows": len(v), "tokens": sum(r["ntok"] for r in v), "done": True} for k, v in slices.items()}
    dump("slice/STATS.json", stats)

    if tags:
        for kind, rows in tag_rows.items():
            with open(os.path.join(root, f"tags/tags-{kind}.jsonl"), "w") as f:
                for t in rows:
                    f.write(json.dumps(t, separators=(",", ":")) + "\n")
            # a half-written last line, as while the tagger is still appending
            if kind == "chat":
                with open(os.path.join(root, f"tags/tags-{kind}.jsonl"), "a") as f:
                    f.write('{"id":"chat:half-writ')
        dump("tags/LABELS.json", LABELS)

    if summaries:
        agent_sum = {"id": "agent-day:agent-alpha:1", "scope": "agent", "key": "agent-alpha", "day": 1, "date": "2030-01-01",
                     "text": "Proposed the Moon Bakery to Agent Beta 2.5 [1] and thanked Zed Quill [2]. Mentioned a ghost [3].",
                     "cites": [{"n": 1, "ref": chat_ids[0]}, {"n": 2, "ref": chat_ids[1]},
                               {"n": 3, "ref": "chat:not-in-slice"}], "model": "gpt-5.6-luna"}
        agent_sum2 = {"id": "agent-day:agent-beta-2-5:2", "scope": "agent", "key": "agent-beta-2-5", "day": 2,
                      "date": "2030-01-02", "text": "Placed the oven order with Mira Testwell for Agent Alpha [1].",
                      "cites": [{"n": 1, "ref": chat_ids[6]}], "model": "gpt-5.6-luna"}
        room_sum = {"id": "room-day:general:1", "scope": "room", "key": "general", "day": 1, "date": "2030-01-01",
                    "text": "The room planned a bakery [1] and a human cheered [2].",
                    "cites": [{"n": 1, "ref": chat_ids[0]}, {"n": 2, "ref": chat_ids[2]}], "model": "gpt-5.6-luna"}
        with open(os.path.join(root, "summaries/agent.jsonl"), "w") as f:
            f.write(json.dumps(agent_sum) + "\n" + json.dumps(agent_sum2) + "\n")
        room_sum2 = {"id": "room-day:room-x:3", "scope": "room", "key": "room-x", "day": 3, "date": "2030-01-03",
                     "text": "o9 thanked a human for a recipe card [1].", "cites": [{"n": 1, "ref": chat_ids[10]}],
                     "model": "gpt-5.6-luna"}
        # a listed first name with a nickname and a surname that is not listed; a name before a listed one; a
        # nickname before the token; a wide gap; an @handle that only looks like an agent account
        room_sum3 = {"id": "room-day:room-x:4", "scope": "room", "key": "room-x", "day": 4, "date": "2030-01-04",
                     "text": 'Brenna ("o9") Quarrington liked the card [1]. Then Orlov vexa thanked @zzq-prober-dev, '
                             "@fakeperson99 and @Agent Alpha; @aidigest_ shared it. Contact zzq_prober via "
                             "github.com/zzq-prober-dev/repo. Later Zorvath ('Bee') Brenna and Brenna    Quarrington "
                             "met @model12345 and @o9.",
                     "cites": [{"n": 1, "ref": chat_ids[13]}], "model": "gpt-5.6-luna"}
        with open(os.path.join(root, "summaries/room.jsonl"), "w") as f:
            f.write(json.dumps(room_sum) + "\n" + json.dumps(room_sum2) + "\n" + json.dumps(room_sum3) + "\n")
        if tags:  # oneiroNER over summary text: a part-word PERSON span, a generic one and an agent name
            summary_tags = [
                {"id": agent_sum2["id"], "model": "fake0000fake0000", "decode": "v1",
                 "spans": spans_for(agent_sum2["text"], [("Mira Testwel", "PERSON"), ("Agent Alpha", "PERSON")])},
                {"id": room_sum["id"], "model": "fake0000fake0000", "decode": "v1",
                 "spans": spans_for(room_sum["text"], [("a human", "PERSON")])},
                {"id": "room-day:nowhere:9", "model": "fake0000fake0000", "decode": "v1", "spans": []}]
            with open(os.path.join(root, "tags/tags-summaries.jsonl"), "w") as f:
                for t in summary_tags:
                    f.write(json.dumps(t) + "\n")

    if findings:
        os.makedirs(os.path.join(root, "findings"), exist_ok=True)
        dump("findings/findings.json", [
            {"title": "Bakery talk",
             "body": "Agent Alpha pitched the bakery [1]; Zed Quill liked it [2]. Lost [3]. Thanks [4].",
             "cites": [{"n": 1, "ref": chat_ids[0], "day": 1}, {"n": 2, "ref": chat_ids[1], "day": 3},
                       {"n": 3, "ref": "chat:not-in-slice", "day": 1}, {"n": 4, "ref": chat_ids[12], "day": 4}],
             "view": "#agents/agent-alpha"},
            {"title": "Store day", "body": "Orders came in [1].", "cites": [{"n": 1, "ref": chat_ids[14], "day": 4}],
             "view": None},
            {"title": "Broken", "cites": []}])
    return {"chat_ids": chat_ids, "digest_daily_1": dig1["id"]}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--no-tags", action="store_true")
    ap.add_argument("--no-summaries", action="store_true")
    ap.add_argument("--no-findings", action="store_true")
    a = ap.parse_args()
    build(a.root, tags=not a.no_tags, summaries=not a.no_summaries, findings=not a.no_findings)
