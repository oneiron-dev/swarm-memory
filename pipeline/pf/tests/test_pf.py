"""pf.py on synthetic fixtures: string collection, splitting, the constrained decoder and the span filter."""
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import pf  # noqa: E402


class Collect(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmp, "entities"))
        with open(os.path.join(self.tmp, "bundle.json"), "w") as f:
            json.dump({"agents": [{"id": "agent-alpha", "name": "Agent Alpha"}], "note": "Agent Alpha"}, f)
        with open(os.path.join(self.tmp, "entities", "room-x.json"), "w") as f:
            json.dump({"timeline": [{"text": "met Person Zed"}]}, f)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_values_keys_and_names(self):
        got = {}
        for text, loc in pf.bundle_strings(self.tmp):
            got.setdefault(text, []).append(loc)
        self.assertEqual(sorted(got["Agent Alpha"]), ['bundle.json#$["agents"][0]["name"]', 'bundle.json#$["note"]'])
        self.assertIn('bundle.json#$#key', got["agents"])
        self.assertEqual(got["room-x"], ["entities/room-x.json#name"])
        self.assertEqual(got["met Person Zed"], ['entities/room-x.json#$["timeline"][0]["text"]'])

    def test_shapes_and_types(self):
        loc = 'entities/room-x.json#$["timeline"][3]["text"]'
        self.assertEqual(pf.file_type(loc), "entities")
        self.assertEqual(pf.path_shape(loc), 'entities/<name>.json#$["timeline"][*]["text"]')
        self.assertEqual(pf.path_shape('sources/7.json#$["chat:abc-1"]["text"]'), 'sources/<name>.json#$[<key>]["text"]')
        self.assertEqual(pf.file_type("summaries/agent/agent-alpha.json#name"), "summaries/agent")


class Split(unittest.TestCase):
    def test_pieces_cover_text_on_sentence_ends(self):
        text = " ".join(f"Sentence number {i} is here." for i in range(400))
        pieces = pf.split_text(text, 500)
        self.assertTrue(all(len(p) <= 500 for _, p in pieces))
        self.assertTrue(all(p.rstrip().endswith(".") for _, p in pieces[:-1]))
        for off, p in pieces:
            self.assertEqual(text[off:off + len(p)], p)

    def test_long_sentence_is_cut(self):
        text = "word " * 300
        pieces = pf.split_text(text, 100)
        self.assertTrue(all(len(p) <= 100 for _, p in pieces))
        self.assertEqual("".join(p for _, p in pieces).split(), text.split())


class Decode(unittest.TestCase):
    def setUp(self):
        import numpy as np
        self.np = np
        labels = ["O"] + [f"{t}-private_person" for t in "BIES"] + [f"{t}-secret" for t in "BIES"]
        self.dec = pf.Decoder({str(i): l for i, l in enumerate(labels)})

    def lp(self, rows):
        a = self.np.full((len(rows), 9), -9.0, self.np.float32)
        for t, (k, v) in enumerate(rows):
            a[t, k] = v
        return a

    def test_valid_argmax_kept(self):
        lp = self.lp([(0, 0), (1, 0), (3, 0), (0, 0)])
        self.assertEqual(list(self.dec.decode(lp)), [0, 1, 3, 0])

    def test_invalid_path_repaired(self):
        # I without B is not allowed: the decoder must not return a bare inside label
        lp = self.lp([(0, 0), (2, -0.1), (0, 0)])
        lp[1, 4] = -0.2
        path = list(self.dec.decode(lp))
        self.assertNotIn(2, path)
        self.assertEqual(path[1], 4)

    def test_spans(self):
        lp = self.lp([(0, 0), (1, 0), (3, 0), (0, 0), (8, 0)])
        path = self.dec.decode(lp)
        offs = [(0, 3), (3, 10), (10, 15), (15, 16), (16, 30)]
        got = [(s, e, c) for s, e, c, _ in self.dec.spans(path, lp, offs)]
        self.assertEqual(got, [(3, 15, "private_person"), (16, 30, "secret")])


class Filter(unittest.TestCase):
    def setUp(self):
        import re
        self.L = {"agents": {"agent alpha", "agent-alpha", "alpha-1"}, "agent_tokens": {"agent", "alpha", "1"},
                  "keep": {"famous figure"}, "keep_re": re.compile(r"(?<![^\W_])(?:famous[\W_]{0,3}figure)(?![^\W_])", re.I),
                  "allow": {"ai-village"}, "routes": frozenset({"about"}),
                  "names": {"human_names": {"person zed"}, "extra_human_ids": set(), "rules": set()}}

    def drop(self, text, cls, string=None):
        string = string or text
        s = string.find(text)
        return pf.drop_reason({"text": text, "label": cls, "s": s, "e": s + len(text)}, string, self.L)

    def test_rules(self):
        self.assertEqual(self.drop("[human]", "private_person"), "redaction_token")
        self.assertEqual(self.drop("human]!", "private_person", "Hey [human]! Thanks"), "redaction_token")
        self.assertIsNone(self.drop("human] Zed", "private_person", "Hey [human] Zed, thanks"))
        self.assertEqual(self.drop("Agent Alpha", "private_person"), "agent_or_model")
        self.assertEqual(self.drop("Claude Opus 4.5", "private_person"), "agent_or_model")
        self.assertEqual(self.drop("Famous Figure", "private_person"), "keep_list")
        self.assertEqual(self.drop("Figure", "private_person", "wrote to Famous Figure today"), "keep_list")
        self.assertEqual(self.drop("April 2", "private_date", "met on April 2 at noon"), "date")
        self.assertEqual(self.drop("https://example.org/docs", "private_url"), "url_no_account")
        self.assertEqual(self.drop("github.com/ai-village", "private_url"), "url_no_account")
        self.assertEqual(self.drop("https://ai-village.substack.com/p/post", "private_url"), "url_no_account")
        self.assertIsNone(self.drop("https://fakewriter.substack.com/p/post", "private_url"))
        self.assertIsNone(self.drop("github.com/fakeuser42", "private_url"))
        self.assertEqual(self.drop("Springfield, Freedonia", "private_address"), "address_city_country")
        self.assertIsNone(self.drop("12 Fake Street, Springfield", "private_address"))
        self.assertIsNone(self.drop("Person Zed", "private_person"))
        self.assertEqual(self.drop("4b8c-99b6", "secret", "chat:f6449983-4b8c-99b6-6fc9723962d2"), "structural_id")
        self.assertEqual(self.drop("527", "private_address", "village-day:527"), "structural_id")
        self.assertEqual(self.drop("2026-03-23T11:17:17Z", "private_person"), "structural_id")
        self.assertIsNone(self.drop("Person Zed 1", "private_person"))
        self.assertEqual(self.drop("Claude Op", "private_person", "asked Claude Opus about it"), "agent_or_model")

    def test_list_hit(self):
        words = {"human_names": {"person", "zed"}, "extra_human_ids": set(), "rules": set()}
        self.assertEqual(pf.list_hit("person zed", self.L["names"], words), ("human_names", "exact"))
        self.assertEqual(pf.list_hit("zed", self.L["names"], words), ("human_names", "token"))
        self.assertEqual(pf.list_hit("nobody", self.L["names"], words), (None, None))


if __name__ == "__main__":
    unittest.main()


class Write(unittest.TestCase):
    def test_review_syntax(self):
        tmp = tempfile.mkdtemp()
        try:
            work, meta = os.path.join(tmp, "work"), os.path.join(tmp, "meta")
            os.makedirs(work)
            os.makedirs(meta)
            hits = {"p1": {"text": "Person Zed", "label": "private_person", "count": 2, "example": "met Person Zed"},
                    "p2": {"text": "post 1a2b by zzq-poster", "label": "private_person", "count": 1, "example": "x"},
                    "p3": {"text": "Room-X", "label": "private_person", "count": 1, "example": "in Room-X"}}
            with open(os.path.join(work, "review.json"), "w") as f:
                json.dump(hits, f)
            open(os.path.join(work, "kept.jsonl"), "w").close()
            review = os.path.join(work, "review.txt")
            with open(review, "w") as f:
                f.write("# comment\np1 accept private person\np2 accept:zzq-poster handle inside a longer span\n"
                        "p3 reject a place\n+ zzq@fake | truncated email\n")
            import argparse
            pf.cmd_write(argparse.Namespace(work=work, root=tmp, review=review, out=work))
            with open(os.path.join(meta, "pf_candidates.json")) as f:
                self.assertEqual(json.load(f), ["Person Zed", "zzq-poster", "zzq@fake"])
            with open(os.path.join(meta, "extra_human_ids.json")) as f:  # accepted ids join the list the build reads
                self.assertEqual(json.load(f), ["Person Zed", "zzq-poster", "zzq@fake"])
        finally:
            shutil.rmtree(tmp)


class Review(unittest.TestCase):
    def test_review_refuses_while_candidates_are_unclassified(self):
        tmp = tempfile.mkdtemp()
        try:
            work = os.path.join(tmp, "pf")
            os.makedirs(work)
            with open(os.path.join(work, "candidates.jsonl"), "w") as f:
                f.write(json.dumps({"i": 1, "text": "Quill Fakename", "label": "private_person", "count": 1,
                                    "aliases": [], "example": "x"}) + "\n")
            with self.assertRaises(SystemExit) as cm:
                pf.main(["review", "--root", tmp])
            self.assertIn("never classified", str(cm.exception))
            pf.main(["review", "--root", tmp, "--allow-incomplete"])
            round1 = os.path.join(work, "luna")
            os.makedirs(round1)
            with open(os.path.join(round1, "candidates.jsonl"), "w") as f:
                f.write(json.dumps({"i": 1, "text": "Quill Fakename", "label": "private_person", "count": 1,
                                    "aliases": [], "example": "x", "agent": None}) + "\n")
            with open(os.path.join(round1, "classified.jsonl"), "w") as f:
                f.write(json.dumps({"batch": 0, "items": 1, "n_items": 1, "hits": [{"i": 1, "why": "a name"}]}) + "\n")
            with self.assertRaises(SystemExit):  # the round has no done mark: unfinished
                pf.main(["review", "--root", tmp])
            open(os.path.join(round1, "done"), "w").close()
            pf.main(["review", "--root", tmp])
            with open(os.path.join(work, "review.json")) as f:
                self.assertEqual(list(json.load(f)), ["p1"])
        finally:
            shutil.rmtree(tmp)
