"""Chapters start on the day their goal starts: from_jsonl.py writes each day's first_ts as its first row's time, and
the builder gives each day the goal in effect for most of it.  Run: python -m unittest discover pipeline/bundle/tests"""
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PIPE = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(PIPE, "slice"))

import build_bundle  # noqa: E402
import from_jsonl  # noqa: E402


def read(path):
    with open(path) as f:
        return json.load(f)


def row(i, time, agent, kind, text, room=None, **kw):
    return dict({"id": f"g{i}", "time": time, "agent": agent, "room": room, "kind": kind, "text": text,
                 "reply_to": None}, **kw)


# day 1 to day 5, 2030-03-01 to 2030-03-05; 2030-03-04 has no rows; the second goal starts at 09:00 on day 3,
# after a row at 08:00 on that day
ARCHIVE = [
    row(1, "2030-03-01T09:00:00Z", "human", "event", "Bake fake bread", subkind="goal"),
    row(2, "2030-03-01T10:00:00Z", "Agent Alpha", "chat", "Agent Beta, the fake oven is warm.", "room-x"),
    row(3, "2030-03-01T14:00:00Z", "Agent Beta", "chat", "Agent Alpha, the fake dough is ready.", "room-x"),
    row(4, "2030-03-02T10:00:00Z", "Agent Alpha", "chat", "Agent Beta, the fake loaves are out.", "room-x"),
    row(5, "2030-03-02T15:00:00Z", "Agent Beta", "chat", "Agent Alpha, they cooled on the fake rack.", "room-x"),
    row(6, "2030-03-03T08:00:00Z", "Agent Alpha", "chat", "Agent Beta, one more fake loaf first.", "room-x"),
    row(7, "2030-03-03T09:00:00Z", "human", "event", "Write a fake almanac", subkind="goal"),
    row(8, "2030-03-03T11:00:00Z", "Agent Beta", "chat", "Agent Alpha, the fake almanac needs a cover.", "room-x"),
    row(9, "2030-03-03T16:00:00Z", "Agent Alpha", "chat", "Agent Beta, the fake cover is drawn.", "room-x"),
    row(10, "2030-03-05T10:00:00Z", "Agent Beta", "chat", "Agent Alpha, fake page two is done.", "room-x"),
    row(11, "2030-03-05T12:00:00Z", "Agent Alpha", "chat", "Agent Beta, fake page three next.", "room-x"),
]


class GoalDays(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        archive = os.path.join(cls.tmp, "archive.jsonl")
        with open(archive, "w") as f:
            for r in ARCHIVE:
                f.write(json.dumps(r) + "\n")
        from_jsonl.JsonlSlicer(archive, cls.tmp, "whitespace", name="Fake Bakery", log=lambda *_: None).run()
        os.makedirs(os.path.join(cls.tmp, "book"))
        open(os.path.join(cls.tmp, "book", "days.jsonl"), "w").close()  # no paragraphs yet: chapters, no text
        with open(os.path.join(cls.tmp, "common_words.json"), "w") as f:
            json.dump({}, f)
        build_bundle.main(["--root", cls.tmp, "--generated", "2030-03-06T00:00:00Z", "--quiet",
                           "--common-words", os.path.join(cls.tmp, "common_words.json")])
        data = os.path.join(cls.tmp, "bundle", "data")
        cls.bundle = read(os.path.join(data, "bundle.json"))
        cls.book = read(os.path.join(data, "book.json"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def test_first_ts_is_the_first_row(self):
        days = read(os.path.join(self.tmp, "meta", "days.json"))
        self.assertEqual([d["first_ts"] for d in days], ["2030-03-01T09:00:00Z", "2030-03-02T10:00:00Z",
                                                         "2030-03-03T08:00:00Z", None, "2030-03-05T10:00:00Z"])

    def test_each_day_has_the_goal_in_effect_for_most_of_it(self):
        self.assertEqual([d["village_goal"] for d in self.bundle["days"]],
                         ["Bake fake bread", "Bake fake bread", "Write a fake almanac", "Write a fake almanac",
                          "Write a fake almanac"])

    def test_chapter_two_starts_on_day_three(self):
        self.assertEqual([(c["goal"], c["days"]) for c in self.book["chapters"]],
                         [("Bake fake bread", [1, 2]), ("Write a fake almanac", [3, 4, 5])])
        goals = [(g["first_day"], g["last_day"]) for g in self.bundle["goals"]]
        self.assertEqual(goals, [(1, 3), (3, 5)])


class GoalOfDay(unittest.TestCase):
    GOALS = [{"goal": "one", "start": "2030-01-01T00:00:00Z", "end": "2030-01-03T18:00:00Z"},
             {"goal": "two", "start": "2030-01-03T18:00:00Z", "end": None}]

    def pick(self, *args):
        g = build_bundle.goal_of_day(self.GOALS, *args)
        return g["goal"] if g else None

    def test_most_of_the_day(self):
        self.assertEqual(self.pick("2030-01-03T17:00:00Z", "2030-01-03T22:00:00Z", "2030-01-03"), "two")
        self.assertEqual(self.pick("2030-01-03T08:00:00Z", "2030-01-03T19:00:00Z", "2030-01-03"), "one")
        self.assertEqual(self.pick("2030-01-03T17:00:00Z", "2030-01-03T19:00:00Z", "2030-01-03"), "two")  # a tie
        self.assertEqual(self.pick("2030-01-03T17:59:30Z", None, "2030-01-03"), "one")  # one row: at that row

    def test_a_row_filed_later_does_not_stretch_the_day(self):
        # a log written two days later still belongs to the day; the next day's first row ends the span
        self.assertEqual(self.pick("2030-01-03T08:00:00Z", "2030-01-06T00:00:00Z", "2030-01-03",
                                   "2030-01-03T19:00:00Z"), "one")
        self.assertEqual(self.pick("2030-01-03T08:00:00Z", "2030-01-06T00:00:00Z", "2030-01-03"), "two")

    def test_an_empty_date_takes_the_goal_on_that_date(self):
        self.assertEqual(self.pick(None, None, "2030-01-02"), "one")
        self.assertEqual(self.pick(None, None, "2030-01-05"), "two")
        self.assertIsNone(self.pick(None, None, "2029-12-30"))
        self.assertIsNone(self.pick(None, None, None))


if __name__ == "__main__":
    unittest.main()
