"""run_tagger.py and the adapter contract, without downloading a model.
Run: python3 -m unittest discover -s pipeline/tag/tests"""
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

import adapters  # noqa: E402
import run_tagger  # noqa: E402
from adapters import gliner_vad  # noqa: E402

ROWS = [{"id": "chat:1", "text": "Agent Alpha baked bread.", "kind": "chat"},
        {"id": "chat:2", "text": "Hello, I am Quill Fakename and I love it.", "kind": "chat"}]


class Runner(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmp, "slice"))
        with open(os.path.join(self.tmp, "slice", "slice-chat.jsonl"), "w") as f:
            for r in ROWS:
                f.write(json.dumps(r) + "\n")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_none_adapter_writes_one_record_per_row_and_status(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_tagger.main(["--adapter", "none", "--root", self.tmp]), 0)
        recs = [json.loads(l) for l in open(os.path.join(self.tmp, "tags", "tags-chat.jsonl"))]
        self.assertEqual([r["id"] for r in recs], ["chat:1", "chat:2"])
        st = json.load(open(os.path.join(self.tmp, "tags", "STATUS.json")))
        self.assertEqual((st["tagger"], st["rows"]), ("none", 2))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_tagger.main(["--check", "--root", self.tmp]), 0)

    def test_check_finds_a_broken_file(self):
        os.makedirs(os.path.join(self.tmp, "tags"))
        with open(os.path.join(self.tmp, "tags", "tags-chat.jsonl"), "w") as f:
            f.write(json.dumps({"id": "chat:1", "spans": [{"s": 0, "e": 5, "label": "PERSON", "text": "Agent"}]}) + "\n")
            f.write(json.dumps({"id": "chat:2", "spans": [{"s": 0, "e": 5, "label": "PERSON", "text": "nope"}]}) + "\n")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_tagger.main(["--check", "--root", self.tmp]), 1)

    def test_contract(self):
        ok = {"id": "chat:1", "spans": [{"s": 0, "e": 11, "label": "PERSON", "text": "Agent Alpha"}],
              "mood": {"v": -0.5, "a": 0.2, "d": 0.9}}
        self.assertIsNone(adapters.check_record(ok, ROWS[0]))
        self.assertIn("label", adapters.check_record(dict(ok, spans=[dict(ok["spans"][0], label="ANIMAL")]), ROWS[0]))
        self.assertIn("mood", adapters.check_record(dict(ok, mood={"v": 0, "a": 2, "d": 0}), ROWS[0]))
        self.assertIn("id", adapters.check_record(ok, ROWS[1]))


class GlinerVadParts(unittest.TestCase):
    """The parts of the reference adapter that need no model: mood, windows, the person biases."""

    def adapter(self):
        ad = gliner_vad.GlinerVad()
        names = ["Agent Alpha", "agent-alpha", "Birch 2.1"]
        ad.agent_words = {w.lower() for n in names for w in n.replace("-", " ").split()}
        ad.agent_re = gliner_vad.re.compile(r"(?<![\w-])(?:Agent Alpha|agent-alpha|Birch 2\.1)(?![\w-])", gliner_vad.re.I)
        return ad

    def test_mood_from_a_lexicon(self):
        lex = {"love": (0.9, 0.6, 0.5), "worried sick": (-0.8, 0.7, -0.4), "bread": (0.3, -0.2, 0.0)}
        m = gliner_vad.mood_of("I love bread", lex)
        self.assertEqual(m, {"v": 0.6, "a": 0.6, "d": 0.625})
        self.assertEqual(gliner_vad.mood_of("worried sick", lex)["v"], -0.8)
        self.assertIsNone(gliner_vad.mood_of("nothing known here", lex))

    def test_windows_keep_offsets(self):
        text = " ".join(f"w{i}" for i in range(600))
        parts = list(gliner_vad.windows(text, 250))
        self.assertEqual(len(parts), 3)
        for off, piece in parts:
            self.assertEqual(text[off:off + len(piece)], piece)

    def test_a_self_introduced_name_is_a_person_and_agents_are_whole(self):
        ad = self.adapter()
        text = ROWS[1]["text"]
        spans = [{"s": 12, "e": 26, "label": "PRODUCT", "text": "Quill Fakename", "p": 0.7}]
        ad.cue_names(text, spans)
        self.assertEqual([(s["text"], s["label"]) for s in spans], [("Quill Fakename", "PERSON")])
        spans = []
        ad.cue_names("I am Agent Alpha, here to help.", spans)
        self.assertEqual(spans, [])  # an agent introducing itself is no person
        text = "Birch 2.1, can you check?"
        snapped = ad.snap_agents(text, [{"s": 0, "e": 5, "label": "PERSON", "text": "Birch", "p": 0.7}])
        self.assertEqual(snapped[0]["text"], "Birch 2.1")


if __name__ == "__main__":
    unittest.main()
