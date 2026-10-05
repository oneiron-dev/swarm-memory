"""Unit tests on synthetic fixtures only (Agent Alpha, Agent Beta, room-x). Run:
python3 -m unittest discover -s pipeline/summarize/tests
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import summarize as S  # noqa: E402

FAKE = os.path.join(HERE, "fake_prime_agent.py")


def jload(path):
    with open(path) as f:
        return json.load(f)


def readf(path):
    with open(path) as f:
        return f.read()


def rec(id_, kind, day, agent, text, time_, room=None, speaker="agent"):
    return {"id": id_, "src": id_.split(":")[0], "time": time_, "day": day, "date": f"2030-01-{day:02d}",
            "agent": agent, "speaker": speaker, "room": room, "session": None, "kind": kind,
            "text": text, "ntok": len(text) // 4}


def write_slice(work, rows, partial_tail=None):
    os.makedirs(os.path.join(work, "slice"), exist_ok=True)
    by_kind = {}
    for r in rows:
        by_kind.setdefault(r["kind"], []).append(r)
    for kind, rs in by_kind.items():
        with open(os.path.join(work, "slice", f"slice-{kind}.jsonl"), "w") as f:
            for r in rs:
                f.write(json.dumps(r) + "\n")
            if partial_tail and kind == "chat":
                f.write(partial_tail)


def fixture_rows():
    return [
        rec("chat:a1", "chat", 1, "agent-alpha", "I will build the puzzle page today.", "2030-01-01T10:00:00Z", "room-x"),
        rec("chat:h1", "chat", 1, "human", "Nice work, keep going.", "2030-01-01T10:05:00Z", "room-x", "human"),
        rec("chat:b1", "chat", 2, "agent-beta", "Deploy is blocked on a 404.", "2030-01-02T09:00:00Z", "room-y"),
        rec("ev:1:summary", "session_summary", 1, "agent-alpha", "Built the page; deploy failed.", "2030-01-01T11:30:00Z"),
        rec("mem:m1", "memory", 1, "agent-alpha", "M" * 5000, "2030-01-01T23:00:00Z"),
        rec("cc:c1:0", "cc_text", 2, "agent-beta", "Fixed the static path.", "2030-01-02T09:30:00Z"),
        rec("ev:9:thinking", "thinking", 3, "agent-alpha", "private reasoning", "2030-01-03T09:30:00Z"),
    ]


class Base(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.mkdtemp(prefix="summ-test-")
        self.state = os.path.join(self.work, "fake-state")

    def tearDown(self):
        shutil.rmtree(self.work, ignore_errors=True)

    def run_cli(self, *args, mode="ok"):
        os.environ["FAKE_LUNA_MODE"] = mode
        os.environ["FAKE_LUNA_STATE"] = self.state
        return S.main(["run", "--work", self.work, "--prime-agent", FAKE, "--backoff", "0.01", *args])

    def records(self, scope):
        path = os.path.join(self.work, "summaries", f"{scope}.jsonl")
        if not os.path.exists(path):
            return []
        return [json.loads(l) for l in readf(path).splitlines()]

    def calls(self):
        path = os.path.join(self.state, "calls")
        return len(readf(path).splitlines()) if os.path.exists(path) else 0


class TestIndex(Base):
    def test_units_and_kinds(self):
        write_slice(self.work, fixture_rows(), partial_tail='{"id":"chat:half","kind":"chat"')
        idx = S.SliceIndex(self.work, {"room", "agent"})
        keys = set(idx.units)
        self.assertEqual(keys, {("agent", "agent-alpha", 1), ("agent", "agent-beta", 2),
                                ("room", "room-x", 1), ("room", "room-y", 2)})
        self.assertNotIn(("agent", "human", 1), keys)  # humans never get agent-days
        self.assertEqual(idx.unit_ids("agent"), [("agent", "agent-beta", 2), ("agent", "agent-alpha", 1)])

    def test_prompt_lines(self):
        write_slice(self.work, fixture_rows())
        idx = S.SliceIndex(self.work, {"room", "agent"})
        p = S.build_prompt(idx, ("room", "room-x", 1))
        lines = p["stdin"].split("\n")
        self.assertEqual(lines[0], "[1] 10:00 agent-alpha (chat): I will build the puzzle page today.")
        self.assertTrue(lines[1].startswith("[2] 10:05 human (chat): "))
        self.assertEqual(p["nmap"], {1: "chat:a1", 2: "chat:h1"})
        a = S.build_prompt(idx, ("agent", "agent-alpha", 1))
        kinds = [l.split("(")[1].split(")")[0] for l in a["stdin"].split("\n")]
        self.assertEqual(kinds, ["chat", "session_summary", "memory"])
        mem = a["stdin"].split("\n")[2].split("): ", 1)[1]
        self.assertEqual(len(mem), S.MEMORY_CHARS)
        self.assertIn("agent-alpha", a["task"])
        self.assertFalse(a["sampled"])


class TestSampling(unittest.TestCase):
    def test_over_cap_keeps_summaries(self):
        rows = []
        for i in range(3000):
            rows.append(S.Row(f"2030-01-01T{i // 200:02d}:{i % 60:02d}:00Z", "chat", 0, i, 10, 400))
            if i % 100 == 0:
                rows.append(S.Row(f"2030-01-01T{i // 200:02d}:{i % 60:02d}:01Z", "session_summary", 0, i, 10, 900))
        chosen, sampled, est = S.plan_rows(rows, 40_000)
        self.assertTrue(sampled)
        self.assertLessEqual(est, 40_000)
        self.assertEqual(sum(r.kind == "session_summary" for r in chosen), 30)
        chats = [r for r in chosen if r.kind == "chat"]
        self.assertGreater(len(chats), 100)
        self.assertLess(chats[0].off, 50)        # spread from the start
        self.assertGreater(chats[-1].off, 2900)  # to the end

    def test_under_cap_keeps_all(self):
        rows = [S.Row("2030-01-01T00:00:00Z", "chat", 0, i, 10, 100) for i in range(10)]
        chosen, sampled, _ = S.plan_rows(rows)
        self.assertEqual(len(chosen), 10)
        self.assertFalse(sampled)


class TestValidate(unittest.TestCase):
    nmap = {1: "chat:a", 2: "chat:b", 3: "ev:c", 4: "mem:d"}

    def test_markers(self):
        text, cites = S.validate("Did A. [1]\nDid B [2, 9] and C [3-4][77].", self.nmap)
        self.assertEqual(text, "Did A [1]. Did B [2] and C [3][4].")
        self.assertEqual([c["n"] for c in cites], [1, 2, 3, 4])
        self.assertEqual(cites[0]["ref"], "chat:a")

    def test_unknown_only(self):
        text, cites = S.validate("Did A [9]. Did B [10].", self.nmap)
        self.assertEqual(text, "Did A. Did B.")
        self.assertEqual(cites, [])

    def test_non_numeric_brackets_untouched(self):
        text, cites = S.validate("It saw [REDACTED] text [2].", self.nmap)
        self.assertEqual(text, "It saw [REDACTED] text [2].")
        self.assertEqual(len(cites), 1)


class TestScrub(unittest.TestCase):
    def test_articles_and_handles(self):
        self.assertEqual(S.scrub("It ran a Pat Doe review with @pat_d [1].", ["Pat Doe", "@pat_d"]),
                         "It ran a human review with a human [1].")
        self.assertEqual(S.scrub("The Pat Doe thread grew.", ["Pat Doe"]), "A human thread grew.")


class TestRun(Base):
    def test_run_resume_order(self):
        write_slice(self.work, fixture_rows())
        self.assertEqual(self.run_cli("--limit", "1"), 0)
        self.assertEqual([r["id"] for r in self.records("room")], ["room-day:room-y:2"])  # rooms first, newest day
        self.assertEqual(self.run_cli("--concurrency", "1", "--max-concurrency", "1"), 0)
        rooms, agents = self.records("room"), self.records("agent")
        self.assertEqual(len(rooms), 2)
        self.assertEqual([r["id"] for r in agents], ["agent-day:agent-beta:2", "agent-day:agent-alpha:1"])
        for r in rooms + agents:
            self.assertEqual(r["model"], "gpt-5.6-luna")
            self.assertNotIn("[999]", r["text"])
            self.assertTrue(r["cites"])
            self.assertNotIn("uncited", r)
        a = agents[1]
        self.assertEqual(a["key"], "agent-alpha")
        self.assertEqual(a["date"], "2030-01-01")
        self.assertEqual({c["ref"] for c in a["cites"]}, {"chat:a1", "ev:1:summary", "mem:m1"})
        n = self.calls()
        self.assertEqual(n, 8)  # one summary and one audit per unit
        self.assertEqual(self.run_cli(), 0)  # resume: nothing new
        self.assertEqual(self.calls(), n)
        prog = jload(os.path.join(self.work, "summaries", "PROGRESS.json"))
        self.assertTrue(prog["final"])
        self.assertEqual(prog["skipped_existing"], 4)

    def test_since_day(self):
        write_slice(self.work, fixture_rows())
        self.run_cli("--since-day", "2")
        self.assertEqual([r["id"] for r in self.records("room")], ["room-day:room-y:2"])
        self.assertEqual([r["id"] for r in self.records("agent")], ["agent-day:agent-beta:2"])

    def test_nocite_retried_once_then_flagged(self):
        write_slice(self.work, fixture_rows())
        self.run_cli("--scope", "room", mode="nocite")
        rooms = self.records("room")
        self.assertEqual(len(rooms), 2)
        self.assertTrue(all(r.get("uncited") and r["cites"] == [] for r in rooms))
        self.assertEqual(self.calls(), 6)  # two summaries and one audit per unit

    def test_human_name_regenerated(self):
        write_slice(self.work, fixture_rows())
        self.run_cli("--scope", "room", mode="human")
        rooms = self.records("room")
        self.assertEqual(len(rooms), 2)
        self.assertTrue(all("Pat Doe" not in r["text"] for r in rooms))
        self.assertEqual(self.calls(), 8)  # draft, audit, redraft, audit per unit
        tasks = readf(os.path.join(self.state, "tasks.txt"))
        self.assertIn("Do not write any of these; write \"a human\" instead: Pat Doe.", tasks)

    def test_human_name_scrubbed_as_last_resort(self):
        write_slice(self.work, fixture_rows())
        self.run_cli("--scope", "room", mode="stubborn")
        rooms = {r["id"]: r for r in self.records("room")}
        self.assertEqual(rooms["room-day:room-x:1"]["text"],
                         "Agent Alpha thanked a human for the idea [1]. It shipped the page [2].")
        prog = jload(os.path.join(self.work, "summaries", "PROGRESS.json"))
        self.assertEqual(prog["human_name_scrubbed"], 2)

    def test_audit_existing_removes_flagged(self):
        write_slice(self.work, fixture_rows())
        self.run_cli("--scope", "room", "--no-audit", mode="stubborn")
        self.assertEqual(len(self.records("room")), 2)
        os.environ["FAKE_LUNA_MODE"] = "ok"
        self.assertEqual(S.main(["audit-existing", "--work", self.work, "--prime-agent", FAKE]), 0)
        self.assertEqual(self.records("room"), [])
        self.run_cli("--scope", "room")
        self.assertTrue(all("Pat Doe" not in r["text"] for r in self.records("room")))
        self.assertEqual(len(self.records("room")), 2)

    def test_mismatch_stops_hard(self):
        write_slice(self.work, fixture_rows())
        self.assertEqual(self.run_cli("--concurrency", "1", "--max-concurrency", "1", mode="mismatch"), 3)
        self.assertEqual(self.records("room") + self.records("agent"), [])
        prog = jload(os.path.join(self.work, "summaries", "PROGRESS.json"))
        self.assertIn("gpt-5.5", prog["stopped"])
        self.assertEqual(self.calls(), 1)

    def test_rate_limit_backoff(self):
        write_slice(self.work, fixture_rows())
        self.assertEqual(self.run_cli("--scope", "room", "--concurrency", "1", "--max-concurrency", "1",
                                      mode="flaky"), 0)
        self.assertEqual(len(self.records("room")), 2)
        prog = jload(os.path.join(self.work, "summaries", "PROGRESS.json"))
        self.assertEqual(prog["rate_limited"], 4)  # every odd call fails once, audits included

    def test_mock_writes_cited_placeholders_with_no_model(self):
        write_slice(self.work, fixture_rows())
        self.assertEqual(S.main(["run", "--work", self.work, "--mock"]), 0)
        recs = self.records("room") + self.records("agent")
        self.assertTrue(recs)
        for r in recs:
            self.assertEqual(r["model"], "mock")
            self.assertTrue(r["cites"])
            self.assertIn("Placeholder text, written without a model", r["text"])
            self.assertNotIn("Nice work", r["text"])  # the mock copies no source text

    def test_estimate_and_pilot(self):
        write_slice(self.work, fixture_rows())
        S.main(["estimate", "--work", self.work])
        est = jload(os.path.join(self.work, "summaries", "ESTIMATE.json"))
        self.assertEqual(est["scopes"]["room"]["units"], 2)
        self.assertEqual(est["scopes"]["agent"]["units"], 2)
        self.run_cli()
        S.main(["pilot-md", "--work", self.work, "--n", "4"])
        md = readf(os.path.join(self.work, "summaries", "PILOT.md"))
        self.assertIn("agent-day:agent-alpha:1", md)
        self.assertNotIn("(ref not found)", md)


class TestSliceReady(Base):
    def test_final_kinds(self):
        os.makedirs(os.path.join(self.work, "slice"))
        with open(os.path.join(self.work, "slice", "STATS.json"), "w") as f:
            json.dump({"final": False, "final_kinds": ["digest_summary", "chat"]}, f)
        self.assertEqual(S.slice_done_kinds(self.work), ({"digest_summary", "chat"}, False))
        stop = S.threading.Event()
        S.wait_for_slice(self.work, S.ROOM_KINDS, stop, 0.01)  # returns at once: chat is final


class TestAllowedNames(Base):
    def test_agent_and_room_names_never_flagged(self):
        os.makedirs(os.path.join(self.work, "meta"))
        with open(os.path.join(self.work, "meta", "agents.json"), "w") as f:
            json.dump([{"id": "agent-alpha-5", "name": "Agent Alpha 5"}], f)
        with open(os.path.join(self.work, "meta", "rooms.json"), "w") as f:
            json.dump([{"id": "room-x", "name": "room-x"}], f)
        r = S.Runner(S.argparse.Namespace(work=self.work, concurrency=1, max_concurrency=1))
        self.assertTrue(r.allowed_name("Alpha"))
        self.assertTrue(r.allowed_name("#room-x"))
        self.assertFalse(r.allowed_name("Pat Doe"))
        self.assertIn("Agent Alpha 5", r.system)


class TestGate(unittest.TestCase):
    def test_raise_and_drop(self):
        g = S.Gate(8, 12, raise_after=3)
        for _ in range(3):
            g.acquire()
            g.release(ok=True)
        self.assertEqual(g.limit, 12)
        g.acquire()
        g.release(ok=False, rate_limited=True)
        self.assertEqual(g.limit, 8)


if __name__ == "__main__":
    unittest.main()
