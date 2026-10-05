"""from_jsonl.py on invented archives: the same slice files and schema as the AI Village slicer.
Run: python3 -m unittest discover -s pipeline/slice/tests"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import from_jsonl  # noqa: E402
from slicer import KIND_SOURCES  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
SLICE_KEYS = ["id", "src", "time", "day", "date", "agent", "speaker", "room", "session", "kind", "text", "ntok"]


def row(i, time, agent, kind, text, room=None, **kw):
    return dict({"id": f"r{i}", "time": time, "agent": agent, "room": room, "kind": kind, "text": text,
                 "reply_to": None}, **kw)


ROWS = [
    row(1, "2030-02-01T08:00:00Z", "human", "event", "Bake fake bread", subkind="goal"),
    row(2, "2030-02-01T09:00:00Z", "Agent Alpha", "chat", "Hello Agent Beta, shall we bake?", "general", model="alpha-1"),
    row(3, "2030-02-01T09:01:00Z", "Agent Beta", "chat", "Yes Agent Alpha, the oven is warm.", "general"),
    row(4, "2030-02-01T09:02:00Z", "human", "chat", "Nice! My id is fan-zz9, ping me.", "general",
        human_id="fan-zz9", human_name="Quill Fakename"),
    row(5, "2030-02-01T09:03:00Z", "Agent Alpha", "chat", "Hello Agent Beta, shall we bake?", "side"),  # a dupe
    row(6, "2030-02-01T10:00:00Z", "Agent Alpha", "memory", "Notes one."),
    row(7, "2030-02-01T18:00:00Z", "Agent Alpha", "memory", "Notes two, the last of the day."),
    row(8, "2030-02-01T11:00:00Z", "Agent Beta", "session", "Plan: warm the oven.", subkind="goal", session="s 1"),
    row(9, "2030-02-01T12:00:00Z", "Agent Beta", "session", "Warmed the oven."),
    row(10, "2030-02-02T09:00:00Z", "human", "event", "Write a fake almanac", subkind="goal"),
    row(11, "2030-02-02T20:00:00Z", "human", "event", "Day two: the almanac began.", subkind="daily_summary"),
    row(12, "2030-02-02T10:00:00Z", "Agent Beta", "event", "Help with baking", subkind="agent_goal"),
    row(13, "2030-02-02T10:30:00Z", "Agent Beta", "event", "something else", subkind="mystery"),
]


class FromJsonl(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.archive = os.path.join(self.tmp, "archive.jsonl")
        with open(self.archive, "w") as f:
            for r in ROWS:
                f.write(json.dumps(r) + "\n")
        from_jsonl.JsonlSlicer(self.archive, self.tmp, "whitespace", name="Fake Bakery", log=lambda *_: None).run()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def read(self, rel):
        path = os.path.join(self.tmp, rel)
        if rel.endswith(".jsonl"):
            return [json.loads(l) for l in open(path)]
        return json.load(open(path))

    def test_every_slice_file_with_the_slicer_schema(self):
        for kind in KIND_SOURCES:
            self.assertTrue(os.path.exists(os.path.join(self.tmp, "slice", f"slice-{kind}.jsonl")), kind)
        chat = self.read("slice/slice-chat.jsonl")
        self.assertEqual([list(r) for r in chat], [SLICE_KEYS] * len(chat))
        self.assertEqual([r["id"] for r in chat], ["chat:r2", "chat:r3", "chat:r4"])  # r5 is a dupe of r2
        self.assertEqual(chat[0]["agent"], "agent-alpha")
        self.assertEqual(chat[0]["room"], "general")
        stats = self.read("slice/STATS.json")
        self.assertTrue(stats["final"])
        self.assertEqual(stats["kinds"]["chat"]["dropped"], {"dupes": 1})

    def test_humans_are_pooled_and_their_ids_never_written(self):
        human = self.read("slice/slice-chat.jsonl")[2]
        self.assertEqual((human["agent"], human["speaker"]), ("human", "human"))
        self.assertIn("[REDACTED]", human["text"])
        for root, _, files in os.walk(self.tmp):
            for fn in files:
                if fn == "archive.jsonl":
                    continue
                blob = open(os.path.join(root, fn)).read()
                self.assertNotIn("fan-zz9", blob, fn)
                self.assertNotIn("Quill Fakename", blob, fn)

    def test_kinds_memory_days_goals_and_meta(self):
        mem = self.read("slice/slice-memory.jsonl")
        self.assertEqual([r["text"] for r in mem], ["Notes two, the last of the day."])  # the last per agent-day
        self.assertEqual([r["kind"] for r in self.read("slice/slice-session_goal.jsonl")], ["session_goal"])
        self.assertEqual(self.read("slice/slice-session_goal.jsonl")[0]["session"], "cus:s_1")
        self.assertEqual([r["kind"] for r in self.read("slice/slice-session_summary.jsonl")], ["session_summary"])
        digest = self.read("slice/slice-digest_summary.jsonl")
        self.assertEqual((digest[0]["id"], digest[0]["agent"], digest[0]["day"]), ("sum:r11", None, 2))
        self.assertEqual(self.read("meta/summaries_index.json")[0]["type"], "daily")
        days = self.read("meta/days.json")
        self.assertEqual([(d["day"], d["date"]) for d in days], [(1, "2030-02-01"), (2, "2030-02-02")])
        goals = self.read("meta/village_goals.json")
        self.assertEqual([(g["goal"], g["first_day"], g["last_day"]) for g in goals],
                         [("Bake fake bread", 1, 2), ("Write a fake almanac", 2, None)])
        self.assertEqual([a["id"] for a in self.read("meta/agents.json")], ["agent-alpha", "agent-beta"])
        self.assertEqual(self.read("meta/agents.json")[0]["model"], "alpha-1")
        self.assertEqual([r["id"] for r in self.read("meta/rooms.json")], ["general", "side"])
        self.assertEqual(self.read("meta/agent_goals.json")[0]["agent"], "agent-beta")
        self.assertEqual(self.read("meta/dataset.json")["cite"], "Data: Fake Bakery")
        self.assertEqual(self.read("slice/STATS.json")["tables"]["archive.jsonl"]["events_not_sliced"], {"mystery": 1})

    def test_a_bad_archive_says_which_line(self):
        bad = os.path.join(self.tmp, "bad.jsonl")
        for lines, word in (([{"id": "x", "time": "2030-01-01T00:00:00Z", "agent": "a", "kind": "poem", "text": "t"}],
                             "kind"), ([{"id": "x", "agent": "a", "kind": "chat", "text": "t"}], "time")):
            with open(bad, "w") as f:
                f.write("\n".join(json.dumps(x) for x in lines) + "\n")
            with self.assertRaises(from_jsonl.ArchiveError) as cm:
                from_jsonl.JsonlSlicer(bad, os.path.join(self.tmp, "o"), "whitespace", log=lambda *_: None).run()
            self.assertIn("line 1", str(cm.exception))
            self.assertIn(word, str(cm.exception))


class GroupRowsAndOffsets(unittest.TestCase):
    def test_group_rows_make_no_agent_and_offsets_become_utc(self):
        tmp = tempfile.mkdtemp()
        try:
            archive = os.path.join(tmp, "archive.jsonl")
            rows = [
                row(1, "2030-02-01T08:00:00Z", "system", "event", "Bake fake bread", subkind="goal"),
                row(2, "2030-02-01T09:00:00Z", "Agent Alpha", "chat", "Agent Beta, the fake oven?", "general"),
                row(3, "2030-02-01T20:00:00Z", "village-log", "event", "Day one: fake bread.", subkind="daily_summary"),
                row(4, "2030-02-01T21:00:00Z", "Agent Alpha", "event", "Day one, by me.", subkind="daily_summary"),
                row(5, "2030-02-01T21:30:00Z", "keeper", "event", "a note nobody slices", subkind="mystery"),
                # 08:30 in UTC+9 is 23:30 UTC the day before: day 1, not day 2
                row(6, "2030-02-02T08:30:00+09:00", "Agent Beta", "chat", "Agent Alpha, late fake bread.", "general"),
                row(7, "2030-02-02T10:00:00Z", "Agent Beta", "chat", "Agent Alpha, morning.", "general"),
            ]
            with open(archive, "w") as f:
                f.write("\n".join(json.dumps(r) for r in rows) + "\n")
            from_jsonl.JsonlSlicer(archive, tmp, "whitespace", log=lambda *_: None).run()
            with open(os.path.join(tmp, "meta", "agents.json")) as f:
                self.assertEqual([a["id"] for a in json.load(f)], ["agent-alpha", "agent-beta"])
            with open(os.path.join(tmp, "slice", "slice-digest_summary.jsonl")) as f:
                digest = [json.loads(l) for l in f]
            self.assertEqual([(r["id"], r["agent"], r["speaker"]) for r in digest],
                             [("sum:r3", None, "agent"), ("sum:r4", "agent-alpha", "agent")])
            with open(os.path.join(tmp, "slice", "slice-chat.jsonl")) as f:
                chat = {r["id"]: r for r in map(json.loads, f)}
            self.assertEqual((chat["chat:r6"]["time"], chat["chat:r6"]["day"]), ("2030-02-01T23:30:00Z", 1))
            self.assertEqual(chat["chat:r7"]["day"], 2)
        finally:
            shutil.rmtree(tmp)


class TinyVillage(unittest.TestCase):
    def test_the_sample_is_deterministic_and_slices(self):
        tmp = tempfile.mkdtemp()
        try:
            make = os.path.join(REPO, "samples", "tiny-village", "make_tiny_village.py")
            sums = []
            for k in (1, 2):
                out = os.path.join(tmp, f"a{k}", "archive.jsonl")
                subprocess.run([sys.executable, make, "--out", out], check=True, capture_output=True)
                sums.append(hashlib.sha256(open(out, "rb").read()).hexdigest())
            self.assertEqual(sums[0], sums[1])
            stats = from_jsonl.JsonlSlicer(os.path.join(tmp, "a1", "archive.jsonl"), os.path.join(tmp, "w"),
                                           "whitespace", log=lambda *_: None).run()
            self.assertGreater(stats["totals"]["rows"], 200)
            self.assertEqual(stats["tables"]["metadata"]["agents"], 4)
            self.assertEqual(stats["tables"]["metadata"]["days"], 6)
            self.assertEqual(stats["tables"]["metadata"]["village_goals"], 3)
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
