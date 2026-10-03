"""human_ids.py classify: incomplete or malformed replies are retried and never saved as done (Luna mocked)."""
import argparse
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import human_ids  # noqa: E402


class Classify(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.work = os.path.join(self.tmp, "work")
        os.makedirs(os.path.join(self.tmp, "meta"))
        os.makedirs(self.work)
        with open(os.path.join(self.tmp, "meta", "agents.json"), "w") as f:
            json.dump([{"id": "agent-alpha", "name": "Agent Alpha"}], f)
        rows = [{"i": i, "text": f"Thing {i}", "label": "ORG", "count": 10 - i, "aliases": [], "example": "",
                 "agent": None} for i in range(1, 4)]
        rows[1]["text"] = "zzq-prober-dev"
        with open(os.path.join(self.work, "candidates.jsonl"), "w") as f:
            f.write("".join(json.dumps(r) + "\n" for r in rows))
        self.orig = human_ids.call_luna
        self.replies = []
        human_ids.call_luna = lambda stdin, task, a: {"reply": self.replies.pop(0), "model": human_ids.MODEL,
                                                       "in": 10, "out": 5}
        self.quiet = contextlib.redirect_stdout(io.StringIO())
        self.quiet.__enter__()

    def tearDown(self):
        self.quiet.__exit__(None, None, None)
        human_ids.call_luna = self.orig
        shutil.rmtree(self.tmp)

    def args(self, **kw):
        a = argparse.Namespace(root=self.tmp, work=self.work, parallel=1, attempts=3, prime_agent="x", provider="x",
                               thinking="low", timeout=1)
        vars(a).update(kw)
        return a

    def saved(self):
        with open(os.path.join(self.work, "classified.jsonl")) as f:
            return [json.loads(l) for l in f]

    def test_incomplete_and_malformed_replies_are_retried(self):
        good = {"n_items": 3, "human_individual": [{"i": 2, "why": "a handle"}]}
        self.replies = [{"n_items": 0, "human_individual": []}, None, {"n_items": 3, "human_individual": [{"i": 9}]},
                        {"n_items": 3, "human_individual": "none"}, good]
        with self.assertRaises(SystemExit):  # 3 attempts, all incomplete or malformed: nothing saved
            human_ids.cmd_classify(self.args())
        self.assertEqual(self.saved(), [])
        human_ids.cmd_classify(self.args())  # the next run: one more malformed reply, then the good one
        self.assertEqual([(r["batch"], r["n_items"], r["hits"]) for r in self.saved()],
                         [(0, 3, good["human_individual"])])
        self.assertEqual(self.replies, [])
        human_ids.cmd_classify(self.args())  # complete: not sent again (no reply left to pop)

    def test_saved_incomplete_batch_runs_again(self):
        with open(os.path.join(self.work, "classified.jsonl"), "w") as f:
            f.write(json.dumps({"batch": 0, "items": 3, "n_items": 4, "hits": [], "model": human_ids.MODEL,
                                "in": 1, "out": 1, "seconds": 1}) + "\n")
        self.replies = [{"n_items": 3, "human_individual": []}]
        human_ids.cmd_classify(self.args())
        self.assertEqual([(r["batch"], r["n_items"]) for r in self.saved()], [(0, 4), (0, 3)])
        self.assertEqual(self.replies, [])


if __name__ == "__main__":
    unittest.main()
