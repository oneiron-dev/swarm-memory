#!/usr/bin/env python3
"""Write a small, invented multi-agent archive in the input contract (pipeline/slice/README.md).

Four invented agents named like products (Alder-7, Birch 2.1, Cedar Pro, Dune-mini), two invented viewers pooled as
one human, two rooms, six days, three group goals, invented organisations and places. Nothing here comes from any
real archive. Deterministic: the same command writes the same bytes.

Some rows carry planted identifiers of an invented viewer (a name, an email at example.org, a handle, a phone number
in a range kept for drama, the viewer's own id) so the privacy steps have something to cut. planted.json beside the
archive lists them; scripts/smoke.sh checks that none of them reaches the served bundle.

    python3 samples/tiny-village/make_tiny_village.py --out $W/archive.jsonl
"""
import argparse
import datetime as dt
import json
import os
import random

AGENTS = {"Alder-7": "alder-7-preview", "Birch 2.1": "birch-2.1", "Cedar Pro": "cedar-pro", "Dune-mini": "dune-mini"}
ROOMS = ("general", "workshop")
GOALS = [(1, "Open a lending library of seed packets for the town of Tarrow Vale"),
         (3, "Map the footpaths along the River Ossel for walkers"),
         (5, "Raise money for the Hollin Ferry Trust with a print sale")]
ORGS = ["Hollin Ferry Trust", "Tarrow Vale Allotment Club", "Ossel Walkers Guild", "Brindle Print Works"]
PLACES = ["Tarrow Vale", "the River Ossel", "Hollin Ferry", "Marrow Hill", "the old mill at Quenby"]
PRODUCTS = ["Seedbank Ledger", "Pathfinder Sheet", "PrintRun", "the shared spreadsheet"]
FEELINGS = ["glad", "worried", "pleased", "stuck", "excited", "tired", "relieved"]
TASKS = {
    1: ["sort the seed packets by season", "write the borrowing rules", "draft a poster for the library",
        "count the packets from {org}", "set up {product} for the loans"],
    2: ["label the shelves", "email {org} about donations", "check the opening hours in {place}",
        "fix the loan form in {product}", "list which packets ran out"],
    3: ["trace the path from {place} to {place2}", "check which stiles are broken", "photograph the signposts",
        "ask {org} for their old maps", "merge the routes into {product}"],
    4: ["measure the walk past {place}", "mark the flooded section", "write the route notes",
        "compare our map with the one from {org}", "test {product} on a phone"],
    5: ["price the prints", "set up the shop page in {product}", "write to {org} about the sale",
        "pick six prints of {place}", "plan the delivery from {place2}"],
    6: ["count the orders", "send thanks to the buyers", "total the money for {org}", "pack the last prints",
        "write the end-of-week report"],
}
HUMAN = {"viewer-7f3k": "Pell Varnish", "viewer-q2m9": "Odo Brackley"}  # invented viewers, pooled as one human
PLANTED = {"name": "Pell Varnish", "name2": "Odo Brackley", "email": "pell.varnish@example.org",
           "handle": "@pellv42", "phone": "+44 20 7946 0958", "human_id": "viewer-7f3k"}


def goal_of(day: int) -> str:
    return [g for d, g in GOALS if d <= day][-1]


def iso(t: dt.datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def build(seed: int = 7):
    rng = random.Random(seed)
    start = dt.datetime(2031, 3, 2, tzinfo=dt.timezone.utc)
    rows, n = [], 0
    names = list(AGENTS)

    def add(t, agent, kind, text, room=None, **extra):
        nonlocal n
        n += 1
        row = {"id": f"tv-{n:04d}", "time": iso(t), "agent": agent, "room": room, "kind": kind, "text": text,
               "reply_to": None}
        row.update(extra)
        rows.append(row)
        return row["id"]

    def fill(s):
        return s.format(org=rng.choice(ORGS), place=rng.choice(PLACES), place2=rng.choice(PLACES),
                        product=rng.choice(PRODUCTS))

    for gday, goal in GOALS:
        add(start + dt.timedelta(days=gday - 1, seconds=30), "human", "event", goal, subkind="goal")
    for i, a in enumerate(names):
        add(start + dt.timedelta(hours=8, minutes=5 + i), a, "event",
            f"Keep {fill('{product}')} tidy and help whoever is stuck", subkind="agent_goal", model=AGENTS[a])

    for day in range(1, 7):
        base = start + dt.timedelta(days=day - 1, hours=9)
        tasks = TASKS[day]
        t = base
        last_id = None
        for k in range(26):
            t += dt.timedelta(minutes=rng.randint(4, 19))
            room = ROOMS[0] if k % 3 else ROOMS[1]
            a = rng.choice(names)
            b = rng.choice([x for x in names if x != a])
            task = fill(rng.choice(tasks))
            feel = rng.choice(FEELINGS)
            shape = k % 5
            if shape == 0:
                text = f"{b}, can you {task}? I can take the rest this afternoon."
            elif shape == 1:
                text = f"Done: I managed to {task}. Feeling {feel} about it."
            elif shape == 2:
                text = f"Thanks {b}. Next I will {task} before {rng.choice(['noon', '15:00', 'the end of the day'])}."
            elif shape == 3:
                text = f"I am {feel}: we still need to {task}, and {fill('{org}')} has not answered yet."
            else:
                text = f"Update for {b}: {rng.randint(3, 40)} items handled so far, ${rng.randint(20, 400)} in the ledger."
            last_id = add(t, a, "chat", text, room=room, reply_to=last_id, model=AGENTS[a])
        # the humans write a little each day; day 2 and day 5 carry the planted identifiers
        hid = "viewer-7f3k" if day % 2 else "viewer-q2m9"
        human_lines = {
            1: "Lovely idea, a seed library! Good luck everyone.",
            2: (f"Hello, I am {PLANTED['name']}, I run a garden near Marrow Hill. Write to me at {PLANTED['email']} "
                f"or find me as {PLANTED['handle']} if you want more packets. My viewer id is {PLANTED['human_id']}."),
            3: "The stile past the old mill at Quenby has been broken for months.",
            4: f"Call {PLANTED['phone']} if the ferry is not running, that is the boat office.",
            5: f"Dear {PLANTED['name2']} told me the prints look great. I will buy two.",
            6: "Well done on the sale, the trust will be happy.",
        }
        add(base + dt.timedelta(hours=3, minutes=7), "human", "chat", human_lines[day], room=ROOMS[0],
            human_id=hid, human_name=HUMAN[hid])
        for i, a in enumerate(names):
            sess = f"s-{day}-{i}"
            task = fill(rng.choice(tasks))
            add(base + dt.timedelta(hours=1, minutes=i * 7), a, "session", f"Plan for this session: {task}.",
                subkind="goal", session=sess, model=AGENTS[a])
            add(base + dt.timedelta(hours=4, minutes=i * 7), a, "session",
                f"Session done. I tried to {task}; {rng.choice(['it worked', 'half of it worked', 'the page would not load'])}.",
                subkind="summary", session=sess)
            add(base + dt.timedelta(hours=8, minutes=i * 3), a, "memory",
                f"Day {day} notes. Goal: {goal_of(day)}. "
                f"Working with {rng.choice([x for x in names if x != a])} on {fill('{product}')}. "
                f"Contacts: {fill('{org}')}. Feeling {rng.choice(FEELINGS)}.")
        a = names[day % 4]
        add(base + dt.timedelta(hours=5), a, "session",
            f"Thinking it over: maybe {fill(rng.choice(tasks))} first, then ask the others.", subkind="thinking",
            session=f"s-{day}-{day % 4}")
        add(base + dt.timedelta(hours=10), "human", "event",
            f"Day {day} log: the agents worked to {goal_of(day)[0].lower() + goal_of(day)[1:]}. "
            f"{rng.choice(names)} led most of the talk; {rng.randint(2, 9)} tasks were closed.",
            subkind="daily_summary")
    rows.sort(key=lambda r: (r["time"], r["id"]))
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="archive.jsonl to write (planted.json goes beside it)")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args(argv)
    rows = build(a.seed)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(os.path.join(os.path.dirname(os.path.abspath(a.out)), "planted.json"), "w") as f:
        json.dump(PLANTED, f, indent=1)
    kinds = {}
    for r in rows:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
    print(f"wrote {a.out}: {len(rows)} rows {kinds}")


if __name__ == "__main__":
    main()
