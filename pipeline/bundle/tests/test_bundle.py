"""Build a bundle from the synthetic fixtures and validate it.  Run: python -m unittest discover pipeline/bundle/tests"""
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import build_bundle  # noqa: E402
import make_fixtures  # noqa: E402
import validate_bundle  # noqa: E402

GENERATED = "2030-01-05T00:00:00Z"


def run(root, *extra):
    """build_bundle.main on a fixture root, with the fixture word table instead of wordfreq."""
    return build_bundle.main(["--root", root, "--generated", GENERATED, "--quiet",
                              "--common-words", os.path.join(root, "common_words.json"), *extra])


def build(root, **kw):
    ids = make_fixtures.build(root, **kw)
    run(root)
    data = os.path.join(root, "bundle", "data")
    with open(os.path.join(data, "bundle.json")) as f:
        return ids, data, json.load(f)


def load(data, rel):
    with open(os.path.join(data, rel)) as f:
        return json.load(f)


class FullBundle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.ids, cls.data, cls.b = build(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def test_validates(self):
        v = validate_bundle.validate(self.data, os.path.join(self.tmp, "meta"), common_words=os.path.join(self.tmp, "common_words.json"))
        self.assertEqual(v.errors, [])

    def test_entities_group_on_folded_text(self):
        ent = {e["id"]: e for e in self.b["entities"]}
        mb = ent["e:moon-bakery"]
        self.assertEqual(mb["count"], 10)  # Moon Bakery / moon bakery / The Moon Bakery / Moon  Bakery
        self.assertEqual(mb["text"], "Moon Bakery")
        self.assertNotIn("timeline", mb)  # light record; detail is on demand
        detail = load(self.data, "entities/moon-bakery.json")
        self.assertEqual([d for d, _ in detail["timeline"]], [1, 2, 3])
        self.assertEqual(sum(c for _, c in detail["timeline"]), mb["count"])
        self.assertEqual(mb["n_agents"], len(detail["agents"]))
        self.assertLessEqual(len(mb["examples"]), 5)
        self.assertEqual(len(mb["examples"]), len(mb["example_days"]))

    def test_no_person_or_scrub_entities(self):
        texts = {e["text"].lower() for e in self.b["entities"]}
        self.assertNotIn("zed quill", texts)
        self.assertNotIn("dana fakename", texts)
        self.assertFalse(any("redacted" in t for t in texts))
        # the only PERSON entity is an agent name
        self.assertEqual({e.get("agent") for e in self.b["entities"] if e["label"] == "PERSON"}, {"o9"})

    def test_human_names_redacted_everywhere(self):
        for root, _, files in os.walk(self.data):
            for fn in files:
                with open(os.path.join(root, fn)) as f:
                    raw = f.read()
                for name in ("Zed Quill", "quill_77", "Dana Fakename"):
                    self.assertNotIn(name, raw, f"{fn} leaks {name}")
        src = load(self.data, "sources/1.json")
        text = src[self.ids["chat_ids"][2]]["text"]
        self.assertEqual(text, "go team, ask [human] about flour")
        s, e, label = src[self.ids["chat_ids"][2]]["spans"][0]
        self.assertEqual(text[s:e], "flour")

    def test_extra_ids_handles_and_neighbours(self):
        room = {r["id"]: r for r in load(self.data, "summaries/room/room-x.json")}
        self.assertEqual(room["room-day:room-x:4"]["text"],
                         "[human] liked the card [1]. Then [human] thanked [human], [human] and @Agent Alpha; @aidigest_ "
                         "shared it. Contact [human] via github.com/[human]/repo. Later [human] and [human] met [human] "
                         "and @o9.")
        row = load(self.data, "sources/4.json")[self.ids["chat_ids"][13]]
        self.assertEqual(row["text"], "Thanks [human] and [human] for the cleanup, and @Agent Alpha too. "
                                      "Ping @aidigest_ and [human].")
        self.assertEqual(row["spans"], [])  # the handle spans went with the handle
        for root, _, files in os.walk(self.data):
            for fn in files:
                with open(os.path.join(root, fn)) as f:
                    raw = f.read().lower()
                for leak in ("zzq", "fakeperson", "quarrington", "orlov", "zorvath", "model12345", "'bee'"):
                    self.assertNotIn(leak, raw, f"{fn} leaks {leak}")
        # the handle was a top entity: dropped, so no page and no memory entity
        mem = load(self.data, "memory/agent-beta-2-5.json")["days"]
        self.assertEqual((mem[1]["entities"], mem[1]["excerpt"]), (["e:oven"], "Memory: [human] fixed the oven."))
        with open(os.path.join(self.tmp, "bundle", "BUILD.json")) as f:
            counts = json.load(f)["counts"]
        self.assertEqual(counts["extra_human_ids"], 2)
        self.assertEqual(counts["entities_dropped_human_name"], 3)  # zzq-prober-dev, zzq-prober, almanac (drop list)
        red = counts["redaction"]
        self.assertGreaterEqual((red["name:extra"], red["name:at"], red["name:neighbour"]), (6, 3, 4))

    def test_common_word_names(self):
        # tagged rows: a common-word name goes only where oneiroNER tagged it PERSON, in any case
        row = load(self.data, "sources/2.json")[self.ids["chat_ids"][6]]
        self.assertIn("the oven order", row["text"])
        self.assertEqual(load(self.data, "sources/4.json")[self.ids["chat_ids"][12]]["text"],
                         "Thanks to [human] for the oven tip.")
        # untagged text (goals): capitalized and not sentence-initial
        alpha = next(a for a in self.b["agents"] if a["id"] == "agent-alpha")
        self.assertEqual(alpha["goals"][0]["description"],
                         "Bake fake bread for the [human]-led club. Ask [human] about the oven. Will knows ovens.")
        with open(os.path.join(self.tmp, "bundle", "redaction_names.json")) as f:
            entries = {tuple(e[0]): e[3] for e in json.load(f)["entries"]}
        self.assertEqual((entries[("oven",)], entries[("will",)], entries[("zed", "quill")]),
                         ("word-cap", "word-cap", "multi"))

    def test_short_agent_span_snapped_kept_and_linked(self):
        o9 = next(e for e in self.b["entities"] if e.get("agent") == "o9")
        self.assertEqual((o9["text"], o9["label"], o9["count"]), ("o9", "PERSON", 1))
        edges = {(e["from"], e["to"]): e for e in self.b["views"]["interactions"]["edges"]}
        self.assertEqual(edges[("agent-alpha", "o9")]["kinds"]["mention"], 1)
        row = load(self.data, f"sources/{o9['example_days'][0]}.json")[o9["examples"][0]]
        self.assertEqual(row["text"], "Thanks o9, and [human] too.")  # "auli" snapped to "Pauli", redacted
        self.assertEqual([row["text"][s:e] for s, e, _ in row["spans"]], ["o9"])

    def test_emails_redacted(self):
        row = load(self.data, "sources/2.json")[self.ids["chat_ids"][6]]
        self.assertIn("[email]", row["text"])
        self.assertNotIn("@", row["text"])

    def test_spans_point_at_their_text(self):
        for fn in os.listdir(os.path.join(self.data, "sources")):
            for ref, row in load(self.data, f"sources/{fn}").items():
                for s, e, label in row["spans"]:
                    self.assertTrue(row["text"][s:e].strip(), (ref, s, e))

    def test_interactions(self):
        edges = {(e["from"], e["to"]): e for e in self.b["views"]["interactions"]["edges"]}
        # 'Agent Beta 2' is not matched inside 'Agent Beta 2.5'
        self.assertEqual(edges[("agent-alpha", "agent-beta-2")]["kinds"], {"mention": 1, "reply": 1})
        # a name match and a span on the same message count once
        self.assertEqual(edges[("agent-alpha", "agent-beta-2-5")]["kinds"]["mention"], 2)
        self.assertEqual(edges[("agent-beta-2-5", "agent-alpha")]["kinds"], {"mention": 3, "reply": 1})
        # slug mention and humans as one node
        self.assertIn(("agent-beta-2", "agent-alpha"), edges)
        self.assertEqual(edges[("human", "agent-beta-2-5")]["kinds"], {"mention": 0, "reply": 1})
        # 60 minutes apart is not a reply
        self.assertEqual(edges[("agent-beta-2-5", "agent-alpha")]["by_day"], [[1, 2], [2, 2]])

    def test_memory_drift(self):
        mem = load(self.data, "memory/agent-alpha.json")
        days = mem["days"]
        self.assertEqual([d["day"] for d in days], [1, 2, 3])
        self.assertIsNone(days[0]["jaccard_prev"])
        self.assertAlmostEqual(days[1]["jaccard_prev"], 2 / 3, places=3)
        self.assertEqual(days[1]["added"], ["e:flour"])
        self.assertEqual(sorted(days[2]["dropped"]), ["e:crater-nine", "e:flour"])
        self.assertEqual(days[2]["jaccard_prev"], 0.3333)  # e:almanac is on the rule file's drop list

    def test_summaries(self):
        village = self.b["summaries"]["village"]
        self.assertEqual([v["day"] for v in village], [1, 2])  # daily rows only; agent digest skipped
        self.assertEqual(village[0]["cites"], [{"n": 1, "ref": self.ids["digest_daily_1"]}])
        self.assertEqual(self.b["days"][0]["summary"], "village-day:1")
        self.assertEqual(load(self.data, "summaries/village/1.json")["id"], "village-day:1")
        recs = load(self.data, "summaries/agent/agent-alpha.json")
        self.assertEqual([c["n"] for c in recs[0]["cites"]], [1, 2])  # unknown ref dropped, with its marker
        self.assertEqual(recs[0]["text"], "Proposed the Moon Bakery to Agent Beta 2.5 [1] and thanked [human] [2]. "
                                          "Mentioned a ghost.")

    def test_village_text_redacted_and_unwrapped(self):
        full = load(self.data, "summaries/village/1.json")["text"]
        self.assertEqual(full, "1. [human] opened the village.\n\nDay 1: the agents planned a moon bakery. Hello [human].")
        preview = self.b["summaries"]["village"][0]["text"]
        self.assertNotIn("<", preview)
        self.assertNotIn("vexa", preview.lower())
        row = load(self.data, "sources/1.json")[self.ids["digest_daily_1"]]
        self.assertTrue(row["text"].startswith("1. [human] opened"))
        self.assertEqual([row["text"][s:e] for s, e, _ in row["spans"]], ["moon bakery"])

    def test_lowercase_display_name_redacted(self):
        row = load(self.data, "sources/3.json")[self.ids["chat_ids"][10]]
        self.assertEqual(row["text"], "Sent the card, thanks [human].")
        with open(os.path.join(self.tmp, "bundle", "BUILD.json")) as f:
            build = json.load(f)
        self.assertGreaterEqual(build["counts"]["redaction"]["name:word"], 2)
        self.assertEqual(build["counts"]["redaction"].get("sweep", 0), 0)  # every path redacts on its own
        self.assertNotIn("tags: file with no slice kind (copied unsnapped)", build["warnings"])

    def test_person_spans_in_summaries(self):
        rec = load(self.data, "summaries/agent/agent-beta-2-5.json")[0]
        self.assertEqual(rec["text"], "Placed the oven order with [human] for Agent Alpha [1].")
        room = {r["id"]: r for r in load(self.data, "summaries/room/general.json")}
        self.assertEqual(room["room-day:general:1"]["text"], "The room planned a bakery [1] and a human cheered [2].")
        with open(os.path.join(self.tmp, "bundle", "BUILD.json")) as f:
            build = json.load(f)
        self.assertEqual(build["counts"]["redaction"]["person_span_in_summaries"], 1)
        self.assertEqual(build["counts"]["summary_tags"]["records"], 2)
        self.assertEqual(build["counts"]["summary_tags"]["id_not_in_summaries"], 1)

    def test_findings(self):
        findings = load(self.data, "findings.json")
        self.assertEqual(len(findings), 2)  # the malformed one is skipped
        f = findings[0]
        self.assertEqual(f["body"], "Agent Alpha pitched the bakery [1]; [human] liked it [2]. Lost. Thanks [4].")
        self.assertEqual(f["cites"], [{"n": 1, "ref": self.ids["chat_ids"][0], "day": 1},
                                      {"n": 2, "ref": self.ids["chat_ids"][1], "day": 1},
                                      {"n": 4, "ref": self.ids["chat_ids"][12], "day": 4}])  # day fixed, unknown dropped
        self.assertIn(self.ids["chat_ids"][1], load(self.data, "sources/1.json"))
        self.assertEqual(f["view"], "#agents/agent-alpha")

    def test_human_rows_belong_to_human(self):
        agents = {a["id"]: a for a in self.b["agents"]}
        self.assertEqual(agents["agent-beta-2"]["last_day"], 1)  # the human outreach reply on day 4 is not its
        self.assertNotIn("agent-beta-2", self.b["days"][3]["active_agents"])
        self.assertEqual(agents["human"]["last_day"], 4)
        reply = "Reply from a human: happy to help with the almanac."
        self.assertEqual(agents["agent-beta-2"]["stats"]["tokens"],
                         sum(len(t.split()) for t in [make_fixtures.CHAT[3][4]]))
        self.assertGreaterEqual(agents["human"]["stats"]["tokens"], len(reply.split()))

    def test_entity_ids_never_from_redacted_text(self):
        ids = " ".join(e["id"] for e in self.b["entities"])
        self.assertNotIn("example", ids)
        self.assertNotIn("orders", ids)
        with open(os.path.join(self.tmp, "bundle", "BUILD.json")) as f:
            counts = json.load(f)["counts"]
        self.assertEqual(counts["snap"]["dropped_email_or_uuid"], 2)  # the whole address and its mailbox part

    def test_published_as_generation_symlink(self):
        link = os.path.join(self.tmp, "bundle", "data")
        self.assertTrue(os.path.islink(link))
        self.assertTrue(os.readlink(link).startswith("gen/"))

    def test_manifest_and_views(self):
        m = self.b["manifest"]
        self.assertEqual(m["tagger"]["labels"], make_fixtures.LABELS)
        self.assertEqual(m["tagger"]["heads"], ["ner", "mood"])
        self.assertEqual(m["counts"]["agents"], 5)
        self.assertEqual(m["tagger"]["snap"], "word-v1")
        self.assertEqual(self.b["views"]["timeline"]["days"], [1, 2, 3, 4])
        self.assertEqual(self.b["views"]["timeline"]["series"]["chat"]["agent-alpha"], [2, 1, 0, 2])
        mood = self.b["views"]["mood"]
        self.assertEqual(mood["agents"]["agent-alpha"]["v"][0], 0.55)
        self.assertEqual(self.b["days"][2]["village_goal"], "Write a fake almanac")


class Budget(unittest.TestCase):
    def test_entities_trimmed_to_fit(self):
        tmp = tempfile.mkdtemp()
        try:
            make_fixtures.build(tmp)
            full = run(tmp)
            budget = full["bytes_bundle_json"] - 700
            rep = run(tmp, "--budget", str(budget))
            self.assertLessEqual(rep["bytes_bundle_json"], budget)
            self.assertLess(rep["counts"]["entities_fit_to_budget"], full["counts"]["entities_fit_to_budget"])
            v = validate_bundle.validate(os.path.join(tmp, "bundle", "data"), os.path.join(tmp, "meta"),
                                         common_words=os.path.join(tmp, "common_words.json"))
            self.assertEqual(v.errors, [])
        finally:
            shutil.rmtree(tmp)



class Redaction(unittest.TestCase):
    def test_cut_never_leaves_partial_word(self):
        n = build_bundle.Names([], ["Pat Quinnley"])
        text = "Report from Quinnleyson about ovens, written for Agent Alpha"
        out, _ = build_bundle.redact(text, [], n, set(), limit=19)
        self.assertEqual(out, "Report from")
        out, _ = build_bundle.redact("doc 12345678-1234-4234-8234-123456789abc and a@b.example", [], n, set())
        self.assertEqual(out, "doc [id] and [email]")


    def test_cut_never_splits_an_agent_handle(self):
        n = build_bundle.Names([{"id": "claude-opus-9-1", "name": "Claude Opus 9.1"}], [])
        head = "word " * 117 + "ok,"  # 588 characters
        for tail in (" @Claude Opus 9.1 said more", "  @Claude Opus 9.1", " ab @Claude Opus 9.1 said"):
            out, _ = build_bundle.redact(head + tail, [], n, set(), limit=600)
            self.assertEqual(out, (head + tail)[:(head + tail).index(" @")].rstrip(), tail)
        out, _ = build_bundle.redact(head + " @Claude Opus 9.1", [], n, set(), limit=605)  # whole: kept
        self.assertTrue(out.endswith("@Claude Opus 9.1"))

    def test_partial_word_person_spans(self):
        n = build_bundle.Names([], ["Paul"])
        text = "the Pauli gates and Mr Smithson"
        # a one-letter PERSON span is noise: skipped, and "Pauli" is not the listed "Paul"
        out, _ = build_bundle.redact(text, [(8, 9, "PERSON", "i")], n, {"PERSON"})
        self.assertEqual(out, text)
        # a part-word PERSON span is widened to the whole word
        out, _ = build_bundle.redact(text, [(27, 31, "PERSON", "hson")], n, {"PERSON"})
        self.assertEqual(out, "the Pauli gates and Mr [human]")

    def test_extra_ids(self):
        n = build_bundle.Names([], [], extra_ids=["zzq-prober-dev", "zzq-prober", " "])
        out, _ = build_bundle.redact("see https://github.com/zzq-prober-dev/x, /home/zzq-prober/, ZZQ-PROBER, "
                                     "zzq_prober, zzq prober and zzq-probers", [], n, set())
        self.assertEqual(out, "see https://github.com/[human]/x, /home/[human]/, [human], [human], [human] and zzq-probers")
        self.assertTrue(n.has_human("thanks zzq-prober-dev"))
        self.assertEqual(n.summary()["extra_ids"], 2)

    def test_at_handles(self):
        n = build_bundle.Names([{"id": "o9", "name": "o9"}, {"id": "agent-alpha", "name": "Agent Alpha"},
                                {"id": "gpt-9-5", "name": "GPT-9.5"}], [])
        # kept: exactly an agent's display name or slug, in any case, and the public accounts
        keep = ("@o9 @O9 @Agent Alpha @agent alpha @agent-alpha @Agent_Alpha @GPT-9.5 @gpt-9-5 @gpt_9.5 @aidigest_ "
                "@AIDigest @github.com @claude @Opus @GEMINI @o3 @luna @muse")
        self.assertEqual(build_bundle.redact(keep, [], n, set())[0], keep)
        # redacted: lookalikes (agent words, generic words and digits), file-like and code-like handles outside code
        leak = ("@the12345 @official77 @model12345 @ai12345 @12345 @agentalpha_9 @AgentAlpha @alpha @o9fan @o9-fan "
                "@gpt-9 @gptx @aidigest_fan @zzq-person.py @zzq-person.json @media.fan @media @everyone @zzqfan42's "
                "@zzq.fan, mail a@b.example")
        out, _ = build_bundle.redact(leak, [], n, set())
        self.assertEqual(out, " ".join(["[human]"] * 18) + " [human]'s [human], mail [email]")
        self.assertEqual(n.hits["name:at"], 20)
        # inside code only the listed code at-words stay, and only as the whole handle
        code = ("css `@media print` and ```\n@keyframes x\n@zzqfan\n``` then `@zzqfan`, `@app.route('/x')`, "
                "`@app.fan`, `@media.fan`, `unclosed @Override\nand @media here")
        out, _ = build_bundle.redact(code, [], n, set())
        self.assertEqual(out, "css `@media print` and ```\n@keyframes x\n[human]\n``` then `[human]`, `@app.route('/x')`, "
                              "`[human]`, `[human]`, `unclosed @Override\nand [human] here")
        out, _ = build_bundle.redact("```\nimport x\n@dataclass\nclass A: pass  # @zzqdev", [], n, set())  # cut fence
        self.assertEqual(out, "```\nimport x\n@dataclass\nclass A: pass  # [human]")
        self.assertTrue(n.has_human("@zzqfan42"))
        self.assertTrue(n.has_human("@model123"))
        self.assertFalse(n.has_human("@aidigest_"))

    def test_names_next_to_redaction_token(self):
        n = build_bundle.Names([{"id": "o9", "name": "o9"}], ["Brenna"], zipf=lambda w: 0.0)
        out, _ = build_bundle.redact('Brenna ("o9") Quarrington met Orlov Brenna. Viewer Brenna said hi. '
                                     "Yesterday Brenna left. Brenna I think so. Thanks Brenna O9.", [], n, set())
        self.assertEqual(out, "[human] met [human]. Viewer [human] said hi. Yesterday [human] left. [human] I think so. "
                              "Thanks [human] O9.")
        self.assertEqual(n.hits["name:neighbour"], 2)
        # a [human] token already in the text (an upstream scrub) gets the same treatment
        out, _ = build_bundle.redact("[human] (\u201cKit\u201d) Zarnowski, then Agent [human] Quell.", [], n, set())
        self.assertEqual(out, "[human], then Agent [human].")
        out, _ = build_bundle.redact("a reply to (@zzqflwr/Kalliope) in /Users/Brenna/x", [], n, set())
        self.assertEqual(out, "a reply to ([human]) in /Users/[human]/x")
        # without a nickname, common English words, all-caps words and words with a digit are no name words
        freq = {"moon": 4.7, "mission": 4.9, "smith": 4.9}
        n = build_bundle.Names([], ["Brenna"], zipf=lambda w: freq.get(w, 0.0))
        out, _ = build_bundle.redact('Brenna Moon Mission, Brenna Smith, Brenna ("Bee") Moon, Brenna URLs, Brenna S1, '
                                     "Brenna SENT", [], n, set())
        self.assertEqual(out, "[human] Moon Mission, [human], [human], [human] URLs, [human] S1, [human]")  # SENT: caps
        # a common word that is part of a listed name is a name word
        n = build_bundle.Names([], ["Brenna", "Zara Zyx"], zipf=lambda w: {"zara": 4.3, "moon": 4.7}.get(w, 0.0))
        out, _ = build_bundle.redact("Met Zara Brenna and Moon Brenna.", [], n, set(), tagged=True)
        self.assertEqual(out, "Met [human] and Moon [human].")

    def test_nicknames_and_spacing(self):
        freq = {"moon": 4.7, "mission": 4.9, "celebration": 4.2, "report": 5.2}
        n = build_bundle.Names([{"id": "o9", "name": "o9"}], [], zipf=lambda w: freq.get(w, 0.0))
        caught = ["[human] 'Bee' Quarrington left.", '[human] "Bee" Quarrington left.', "[human] (Bee) Quarrington left.",
                  "[human] \u201cBee\u201d Quarrington left.", "[human] \u2018Bee\u2019 Quarrington left.",
                  '[human]("Bee") Quarrington left.', "[human]    Quarrington left.", "[human]\t\tQuarrington left.",
                  "[human]\nQuarrington left.", "[human] \n ('Bee')\tQuarrington left.",
                  'Quarrington ("Bee") [human] left.', "Quarrington 'Bee' [human] left.",
                  "Quarrington \u2018Bee\u2019\t[human] left.", "Quarrington \u201cBee\u201d\n[human] left."]
        for text in caught:
            self.assertEqual(build_bundle.redact(text, [], n, set())[0], "[human] left.", text)
        for text in ("Met Quarrington    [human] today.", "Met Quarrington\n[human] today.", "Met Zed Quarrington [human] today."):
            self.assertEqual(build_bundle.redact(text, [], n, set())[0], "Met [human] today.", text)
        self.assertEqual(n.hits["name:neighbour"], len(caught) + 3)
        kept = {
            # after a nickname at most two words go, and only the first without the name-word tests: titles stay
            '[human] ("Bee") Moon Mission Celebration ended.': "[human] Mission Celebration ended.",
            '[human] ("Bee") Zorn Quell Moon ended.': "[human] ended.",  # then "Moon" is right after the token
            "Zorn Quell Moon ('Bee') [human] said": "Zorn [human] said",
            "Done. Later Quell ('Bee') [human] said": "Done. Later [human] said",
            # stop words, agent names and lowercase words end the run at once
            '[human] ("Bee") The Moon Mission': None, "[human] ('Bee') O9 said": None, "[human] 'Bee' said hi": None,
            # apostrophes are no quotes; a blank line ends the run; without a nickname common words stay
            "[human]'s friend's Car broke.": None, "Ask [human]'s 'Moon' Team": None,
            "[human]\n\nQuarrington left.": None, "[human] Moon Mission": None, "Thanks [human].\nReport: done": None,
            # without a nickname a word that starts the text or a sentence stays
            "Yesterday [human] left.": None, "Done. Quarrington [human] left.": None,
        }
        for text, want in kept.items():
            self.assertEqual(build_bundle.redact(text, [], n, set())[0], want or text, text)
        # the Title Case word right after the token on its line goes however common, unless a heading follows
        adjacent = {"read [human] Mission's blog": "read [human] blog", "met [human] Moon.": "met [human].",
                    "a note from [human] Report Draft today": None, "a note from [human] Report today": "a note from [human] today",
                    "[human] Celebration ended": "[human] ended", "[human] The Moon": None, "[human] O9 said": None,
                    "[human]\nCelebration ended": None, "[human] URLs and [human] S1": None}
        adjacent.update({"[human] ZORVATH wrote": "[human] wrote", "[human] van Zorvath wrote": "[human] wrote",
                         "[human] API docs": None, "[human] NEW YORK office": None, "[human] de la Zorvath.": "[human]."})
        for text, want in adjacent.items():
            self.assertEqual(build_bundle.redact(text, [], n, set())[0], want or text, text)

    def test_identifier_rules(self):
        freq = {"asked": 5.1, "said": 5.5, "helped": 4.6, "moss": 3.6, "the": 7.0, "tea": 4.5, "grew": 4.3, "count": 5.0,
                "commit": 4.2, "awesome": 4.6, "mental": 4.6, "health": 5.2}
        rules = {"exact": ["Moss"], "cap": ["Fern"], "ids": ["QZ555000"], "drop_entities": ["Lantern Club"],
                 "profile_allow": ["zzq-org"], "at": []}
        n = build_bundle.Names([{"id": "o9", "name": "o9"}, {"id": "agent-alpha", "name": "Agent Alpha"}], [],
                               zipf=lambda w: freq.get(w, 0.0), rules=rules)
        caught = {
            "mail zq%40example.invalid now": "mail [email] now",
            "from%3Azq.v%2540example.invalid": "from%3A[email]",
            "call +1 555 201 7788, (555) 201-7788, 555.201.7788 or 5552017788.": "call [id], [id], [id] or [id].",
            "ring +44 20 7946 0958": "ring [id]",
            "order #QZ918273 shipped; tracking 1Z999AA10123456784; user id: 48151623": (
                "order #[id] shipped; tracking [id]; user id: [id]"),
            "ref QZ555000 and qz555000": "ref [id] and [id]",
            "see github.com/zzqhub/repo, https://x.com/zzqbird, linkedin.com/in/zzq-lin, zzqblog.substack.com":
                "see github.com/[human]/repo, https://x.com/[human], linkedin.com/in/[human], [human].substack.com",
            "a reader zzqx77 and the viewer zzq_fan77 asked": "a reader [human] and the viewer [human] asked",
            "donor (zzqtest42) helped": "donor ([human]) helped",
            'his query "zzqmail77 status" and from:zzqmail to:me': 'his query "[human] status" and from:[human] to:me',
            "Hi Quorra,\nDear Zed Vance\nThanks Brio for the tea": "Hi [human],\nDear [human] [human]\nThanks [human] for the tea",
            "Ship to: QUORRA VANTE, 1 Main St": "Ship to: [human], 1 Main St",
            "Customer: QUORRA VANTE paid": "Customer: [human] paid",
            "Gmail search from:zzqmail to:me": "Gmail search from:[human] to:me",
            "in:inbox from:zzqmail": "in:inbox from:[human]",
            "Moss said the moss grew": "[human] said the moss grew",
            "an exchange with Fern; Fern's notes. Fern starts this; fern grows": (
                "an exchange with [human]; [human]'s notes. Fern starts this; fern grows"),
            "wrote to Dr. Zorvath and Professor Ada Quell": "wrote to Dr. [human] and Professor [human] [human]",
            "Dear Dr. Brio Vance,": "Dear Dr. [human] [human],",
        }
        for text, want in caught.items():
            self.assertEqual(build_bundle.redact(text, [], n, set())[0], want, text)
        kept = ["github.com/ai-village-agents/site github.com/zzq-org/x github.com/settings zzq-org.github.io",
                "Thanks Agent Alpha, Hi everyone, Dear Team, Hello World. Thanks for the help",
                "the viewer count hit 1500 in v2.1 at 10pm (commit df39a7b) on 2026-10-04",
                "Release 2026-10-04 at 12:30, $1,234,567, +3.5% and 3 orders today",
                "SHIPPED TO THE USA via USPS: customer ORDER CONFIRMED",
                "the reader asked about awesome-mental-health and the moss",
                "a viewer saw kobolds, a shadowban, WAVE12, SUMMER10 and utm_source; order 2026-10-04T12-30-00Z",
                'the query "gpt5 status" ran at 1727999999',
                "pipeline 2834567890 passed; comment 3712345678; see #2712345678 and `3612345678`",
                "Customers used HTTP GET to load the page. The buyer: API JSON CSV",
                "Convert from:JSON to:YAML, then searched the inbox with `from:zzqmail`",
                "mailto:support from:me in:inbox", "mail [email] and [id] for [human]"]
        for text in kept:
            self.assertEqual(build_bundle.redact(text, [], n, set())[0], text, text)
        # public figures on the keep list stay, whatever rule would take them
        n2 = build_bundle.Names([], ["Quill", "Zed Quill"], zipf=lambda w: freq.get(w, 0.0),
                                rules=dict(rules, keep=["Zed Quill", "QUILL", "quill.example"]))
        for text in ("Dear Dr. Quill, we read quill.example", "Hi Zed Quill,", "[human] QUILL replied", "met Zed Quill"):
            self.assertEqual(build_bundle.redact(text, [(text.find("Zed"), text.find("Zed") + 9, "PERSON", "Zed Quill")]
                                                 if "Zed" in text else [], n2, {"PERSON"})[0], text, text)
        self.assertEqual(build_bundle.redact("Quillson met Dr. Brio", [], n2, set())[0], "Quillson met Dr. [human]")
        # a keep entry never shields an explicit private id, nor a match that reaches beyond it
        n3 = build_bundle.Names([], ["Quill"], zipf=lambda w: freq.get(w, 0.0), extra_ids=["zzq-quill"],
                                rules=dict(rules, exact=["Quorra Quill"], keep=["Quill"]))
        text = "Thanks @zzq-quill for the patch. Contact Quorra Quill. Dr. Quill and Quill stay."
        span = (text.index("Quorra"), text.index("Quorra") + 12, "PERSON", "Quorra Quill")
        self.assertEqual(build_bundle.redact(text, [span], n3, {"PERSON"})[0],
                         "Thanks [human] for the patch. Contact [human]. Dr. Quill and Quill stay.")
        # entities: a drop-list key, a rule hit (also in title case) or none
        self.assertTrue(n.has_human("lantern club"))
        self.assertTrue(n.has_human("moss"))  # "Moss" in title case
        self.assertTrue(n.has_human("555-201-7788"))
        self.assertFalse(n.has_human("moon bakery"))

    def test_names_before_an_email(self):
        freq = {"zara": 4.3, "support": 5.0, "mail": 5.0, "row": 4.5}
        n = build_bundle.Names([], ["Zara Zyx"], zipf=lambda w: freq.get(w, 0.0), extra_ids=["zzq-prober-dev"])
        out, _ = build_bundle.redact("Row 2: Zara Quillfeather (zzqproberdev@...) signed up; Mail Ada Zorn <ada@x.example>; "
                                     "Support a@b.example", [], n, set(), tagged=True)
        self.assertEqual(out, "Row 2: [human] ([email]...) signed up; Mail [human] <[email]>; Support [email]")
        self.assertEqual((n.hits["email"], n.hits["email_partial"]), (2, 1))

    def test_recheck_after_cuts(self):
        n = build_bundle.Names([], ["Paul"])
        out, _ = build_bundle.redact("Paul12345678-1234-4234-8234-123456789abc ok", [], n, set())
        self.assertEqual(out, "[human][id] ok")


class SwapGuard(unittest.TestCase):
    def test_failing_build_is_not_swapped_in(self):
        tmp = tempfile.mkdtemp()
        try:
            make_fixtures.build(tmp)
            run(tmp)
            good = load(os.path.join(tmp, "bundle", "data"), "bundle.json")
            rep = run(tmp, "--generated", "not-a-time")  # fails validation
            self.assertFalse(rep["swapped_in"])
            self.assertEqual(load(os.path.join(tmp, "bundle", "data"), "bundle.json"), good)
            self.assertTrue(os.path.isdir(os.path.join(tmp, "bundle", "failed")))
        finally:
            shutil.rmtree(tmp)


class Snap(unittest.TestCase):
    def snap(self, text, spans):
        counts = __import__("collections").Counter()
        out = build_bundle.snap_spans(text, [{"s": s, "e": e, "label": l, "text": text[s:e]} for s, e, l in spans], counts)
        return [(sp["text"], sp["label"]) for sp in out], counts

    def test_widen_merge_drop(self):
        text = "Visit New York via ChatGPT, $5 -- ok"
        out, c = self.snap(text, [(7, 9, "PLACE"), (10, 13, "PLACE"), (19, 23, "PRODUCT"), (23, 26, "PRODUCT"),
                                  (31, 33, "ORG"), (28, 30, "MONEY")])
        self.assertEqual(out, [("New", "PLACE"), ("York", "PLACE"), ("ChatGPT", "PRODUCT"), ("$5", "MONEY")])
        self.assertEqual((c["snapped"], c["merged"], c["dropped"]), (4, 1, 1))

    def test_whitespace_trim_and_overlap_merge(self):
        out, c = self.snap("Hi  Dana Fake  there", [(3, 8, "PERSON"), (6, 14, "PERSON")])
        self.assertEqual(out, [("Dana Fake", "PERSON")])


class PartialInputs(unittest.TestCase):
    def check(self, **kw):
        tmp = tempfile.mkdtemp()
        try:
            ids, data, b = build(tmp, **kw)
            v = validate_bundle.validate(data, os.path.join(tmp, "meta"), common_words=os.path.join(tmp, "common_words.json"))
            self.assertEqual(v.errors, [])
            return b
        finally:
            shutil.rmtree(tmp)

    def test_no_tags(self):
        b = self.check(tags=False)
        self.assertEqual(b["entities"], [])
        self.assertEqual(b["views"]["mood"], {"days": [], "agents": {}})
        self.assertEqual(b["manifest"]["tagger"]["heads"], [])
        self.assertTrue(b["views"]["interactions"]["edges"])  # name and reply edges need no tags

    def test_no_tags_no_summaries(self):
        b = self.check(tags=False, summaries=False)
        self.assertEqual(b["manifest"]["counts"]["summaries"], 2)  # village digests only

    def test_meta_only(self):
        tmp = tempfile.mkdtemp()
        try:
            make_fixtures.build(tmp, tags=False, summaries=False)
            shutil.rmtree(os.path.join(tmp, "slice"))
            run(tmp)
            v = validate_bundle.validate(os.path.join(tmp, "bundle", "data"), os.path.join(tmp, "meta"),
                                         common_words=os.path.join(tmp, "common_words.json"))
            self.assertEqual(v.errors, [])
        finally:
            shutil.rmtree(tmp)


class Publish(unittest.TestCase):
    def test_generations_and_failed_build(self):
        tmp = tempfile.mkdtemp()
        try:
            make_fixtures.build(tmp)
            link = os.path.join(tmp, "bundle", "data")
            os.makedirs(link)  # an old plain data/ dir is kept as a generation on the first run
            with open(os.path.join(link, "old.txt"), "w") as f:
                f.write("x")
            seen = []
            for _ in range(3):
                rep = run(tmp)
                self.assertTrue(rep["swapped_in"])
                seen.append(os.readlink(link))
            gens = sorted(os.listdir(os.path.join(tmp, "bundle", "gen")))
            self.assertEqual(gens, sorted(os.path.basename(g) for g in seen[1:]))  # current + previous only
            rep = run(tmp, "--generated", "not-a-time")  # fails validation: the link does not move
            self.assertFalse(rep["swapped_in"])
            self.assertEqual(os.readlink(link), seen[-1])
            self.assertEqual(load(link, "bundle.json")["manifest"]["generated"], GENERATED)
        finally:
            shutil.rmtree(tmp)

    def test_missing_name_list_stops_the_build(self):
        tmp = tempfile.mkdtemp()
        try:
            make_fixtures.build(tmp)
            os.remove(os.path.join(tmp, "meta", "human_names.json"))
            with self.assertRaises(SystemExit):
                run(tmp)
            with open(os.path.join(tmp, "meta", "human_names.json"), "w") as f:
                f.write("{not json")
            with self.assertRaises(SystemExit):
                run(tmp)
            self.assertFalse(os.path.exists(os.path.join(tmp, "bundle", "data")))
            make_fixtures.build(tmp)
            os.remove(os.path.join(tmp, "meta", "extra_human_ids.json"))  # the extra id list is required too
            with self.assertRaises(SystemExit):
                run(tmp)
            self.assertFalse(os.path.exists(os.path.join(tmp, "bundle", "data")))
            make_fixtures.build(tmp)
            with open(os.path.join(tmp, "meta", "extra_human_rules.json"), "w") as f:
                json.dump({"at": [{"file": "sources/1.json"}]}, f)  # and a well-formed rule file
            with self.assertRaises(SystemExit):
                run(tmp)
            self.assertFalse(os.path.exists(os.path.join(tmp, "bundle", "data")))
        finally:
            shutil.rmtree(tmp)


class Validator(unittest.TestCase):
    def test_full_list_and_structure(self):
        tmp = tempfile.mkdtemp()
        try:
            ids, data, b = build(tmp)
            words = os.path.join(tmp, "common_words.json")
            meta = os.path.join(tmp, "meta")
            ok = validate_bundle.validate(data, meta, common_words=words)
            self.assertEqual(ok.errors, [])
            src = load(data, "sources/3.json")
            row = src[ids["chat_ids"][10]]
            row["text"] = "Sent the card, thanks VEXA."
            src["chat:extra"] = {"time": None, "agent": "nobody-agent", "room": "no-room", "kind": "chat",
                                 "text": ["not", "a", "string"], "spans": []}
            src["chat:extra2"] = {"time": None, "agent": "nobody-agent", "room": "no-room", "kind": "chat",
                                  "text": "fine", "spans": []}
            with open(os.path.join(data, "sources", "3.json"), "w") as f:
                json.dump(src, f)
            recs = load(data, "summaries/agent/agent-alpha.json")
            recs[0]["text"] += " Again [7]."
            with open(os.path.join(data, "summaries", "agent", "agent-alpha.json"), "w") as f:
                json.dump(recs, f)
            os.remove(os.path.join(data, "summaries", "room", "room-x.json"))
            b["entities"][0]["id"] = "e:vexa-fan-club"
            b["goals"][0]["goal"] = "Open a bakery. Ask Will first."
            with open(os.path.join(data, "bundle.json"), "w") as f:
                json.dump(b, f)
            v = validate_bundle.validate(data, meta, common_words=words)
            joined = "\n".join(v.errors)
            self.assertIn("listed human name", joined)  # "VEXA" in any case
            self.assertIn("text not a string", joined)
            self.assertIn("unknown agent 'nobody-agent'", joined)
            self.assertIn("unknown room 'no-room'", joined)
            self.assertIn("markers [7] without a cite", joined)
            self.assertIn("manifest.counts.summaries", joined)
            self.assertIn("entity id e:vexa-fan-club contains a listed human name", joined)
            self.assertIn("goals: 1 untagged strings still contain a capitalized common-word human name", joined)
            with open(os.path.join(data, "findings.json"), "w") as f:
                json.dump([42], f)  # malformed: reported, never a crash
            v = validate_bundle.validate(data, meta, common_words=words)
            self.assertIn("findings[0]: not an object", v.errors)
            b["goals"][0]["goal"] = "Open a bakery; Will helps. Contact: Will at the desk."
            with open(os.path.join(data, "bundle.json"), "w") as f:
                json.dump(b, f)
            v = validate_bundle.validate(data, meta, common_words=words)
            self.assertIn("goals: 1 untagged strings still contain a capitalized common-word human name", v.errors)
            os.remove(os.path.join(meta, "human_names.json"))
            v = validate_bundle.validate(data, meta, common_words=words)
            self.assertTrue(any("missing or unreadable" in e for e in v.errors))
        finally:
            shutil.rmtree(tmp)

    def test_extra_ids_handles_and_neighbours(self):
        tmp = tempfile.mkdtemp()
        try:
            ids, data, b = build(tmp)
            words = os.path.join(tmp, "common_words.json")
            meta = os.path.join(tmp, "meta")
            src = load(data, "sources/4.json")
            src[ids["chat_ids"][13]]["text"] = '[human] ("Bo") Testsurname helped @realperson7.'
            with open(os.path.join(data, "sources", "4.json"), "w") as f:
                json.dump(src, f)
            b["entities"][0]["aliases"].append("ZZQ-Prober")
            b["entities"][1]["id"] = "e:zzq-prober-dev"
            with open(os.path.join(data, "bundle.json"), "w") as f:
                json.dump(b, f)
            row = dict(src[ids["chat_ids"][13]], spans=[])
            src["chat:x1"] = dict(row, text="[human] 'Bee' Quarrington and @the12345 and @model12345 came.")
            src["chat:x2"] = dict(row, text='Quarrington ("Bee") [human] said hi to [human]    Zorvath.')
            src["chat:x3"] = dict(row, text="@zzq-person.py or @media.fan or @12345 wrote")
            with open(os.path.join(data, "sources", "4.json"), "w") as f:
                json.dump(src, f)
            with open(os.path.join(data, "entities", "zzq-prober-dev.json"), "w") as f:
                json.dump({}, f)  # a page nothing lists still serves its name
            src["chat:x4"] = dict(row, text="Hi Quorra, call 555-201-7788 or see github.com/zzqhub; Moss too.")
            src[ids["chat_ids"][14]]["text"] += " Copper."  # the per-location token back at its location
            with open(os.path.join(data, "sources", "4.json"), "w") as f:
                json.dump(src, f)
            findings = load(data, "findings.json")
            findings[0]["zzq-prober"] = 1  # an object key
            with open(os.path.join(data, "findings.json"), "w") as f:
                json.dump(findings, f)
            joined = "\n".join(validate_bundle.validate(data, meta, common_words=words).errors)
            self.assertIn("sources/4.json: 5 strings still contain a listed human name (at 3, exact 1, neighbour 3, "
                          "phone 1, profile 1, salutation 1)", joined)
            self.assertIn("a per-location rule token is still there", joined)
            self.assertIn("bundle.json: 2 strings still contain a listed human name (extra 2)", joined)  # alias and id
            self.assertIn("entity id e:zzq-prober-dev contains a listed human name", joined)
            self.assertIn("path entities/zzq-prober-dev.json contains a listed human name", joined)
            self.assertIn("findings.json: 1 strings still contain a listed human name (extra 1)", joined)
            os.remove(os.path.join(meta, "extra_human_ids.json"))
            v = validate_bundle.validate(data, meta, common_words=words)
            self.assertTrue(any("extra_human_ids.json missing or unreadable" in e for e in v.errors))
        finally:
            shutil.rmtree(tmp)

    def test_keep_never_shields_a_private_id(self):
        tmp = tempfile.mkdtemp()
        try:
            make_fixtures.build(tmp)
            meta = os.path.join(tmp, "meta")
            rules = load(meta, "extra_human_rules.json")
            rules["keep"] = ["Quill"]
            with open(os.path.join(meta, "extra_human_rules.json"), "w") as f:
                json.dump(rules, f)
            with open(os.path.join(meta, "extra_human_ids.json"), "w") as f:
                json.dump(make_fixtures.EXTRA_IDS + ["zzq-quill"], f)
            findings = load(tmp, "findings/findings.json")
            findings[0]["body"] += " Thanks @zzq-quill and Dr. Quill."
            with open(os.path.join(tmp, "findings", "findings.json"), "w") as f:
                json.dump(findings, f)
            rep = run(tmp)
            self.assertTrue(rep["swapped_in"])
            data = os.path.join(tmp, "bundle", "data")
            self.assertTrue(load(data, "findings.json")[0]["body"].endswith(" Thanks [human] and Dr. Quill."))
            served = load(data, "findings.json")
            served[0]["body"] += " Thanks @zzq-quill again."  # the private id back in: the validator rejects it
            with open(os.path.join(data, "findings.json"), "w") as f:
                json.dump(served, f)
            v = validate_bundle.validate(data, meta, common_words=os.path.join(tmp, "common_words.json"))
            self.assertIn("findings.json: 1 strings still contain a listed human name (at 1, extra 1)", v.errors)
        finally:
            shutil.rmtree(tmp)

    def test_catches_leaks_and_missing_sources(self):
        tmp = tempfile.mkdtemp()
        try:
            ids, data, b = build(tmp)
            b["agents"][0]["user_speaker_id"] = "x"
            b["summaries"]["village"][0]["cites"][0]["ref"] = "sum:missing"
            b["rooms"][0]["note"] = "12345678-1234-4234-8234-123456789abc"
            b["goals"][0]["goal"] = "met Zed Quill"
            findings = load(data, "findings.json")
            findings[0]["cites"][0]["day"] = 4
            with open(os.path.join(data, "findings.json"), "w") as f:
                json.dump(findings, f)
            with open(os.path.join(data, "summaries", "village", "2.json")) as f:
                v2 = json.load(f)
            v2["text"] = "<narrative_summary>\n" + v2["text"]
            with open(os.path.join(data, "summaries", "village", "2.json"), "w") as f:
                json.dump(v2, f)
            with open(os.path.join(data, "bundle.json"), "w") as f:
                json.dump(b, f)
            with open(os.path.join(data, "sources", "99.json"), "w") as f:
                json.dump({"chat:x": {"time": None, "agent": "human", "room": None, "kind": "chat",
                                      "text": "x", "spans": []}, "pad": "y" * 2_000_000}, f)
            v = validate_bundle.validate(data, os.path.join(tmp, "meta"), common_words=os.path.join(tmp, "common_words.json"))
            joined = "\n".join(v.errors)
            self.assertIn("on-demand limit", joined)
            self.assertIn("user_speaker_id", joined)
            self.assertIn("sum:missing", joined)
            self.assertIn("bare uuid", joined)
            self.assertIn("human name", joined)
            self.assertIn("findings[0]: cited ref", joined)
            self.assertIn("wrapper markup left", joined)
        finally:
            shutil.rmtree(tmp)


class Names(unittest.TestCase):
    def test_agent_matcher(self):
        n = build_bundle.Names([{"id": "model-4", "name": "Model 4"}, {"id": "model-4-1", "name": "Model 4.1"},
                                {"id": "o9", "name": "o9"}], [])
        self.assertEqual(n.agents_in("Model 4.1 and model-4 met"), {"model-4-1", "model-4"})
        self.assertEqual(n.agents_in("Model 4.2 is not here"), set())
        self.assertEqual(n.agents_in("ask O9."), {"o9"})
        self.assertEqual(n.agent_of_surface("the Model 4"), "model-4")

    def test_human_names_filter(self):
        zipf = {"doe": 4.5}.get
        n = build_bundle.Names([{"id": "agent-alpha", "name": "Agent Alpha"}], ["Hello", "Alpha", "ab", "Pat Doe", "pd_9"],
                               zipf=lambda w: zipf(w, 0.0))
        self.assertEqual(n.human_count, 4)  # "Pat Doe", "pd_9", and "pat" and "doe" as one-word parts
        # every word of "Pat Doe" is a common English word: matched only when capitalized
        out, _ = build_bundle.redact("Hello Pat Doe, pd_9 and PD_9 and pat doe", [], n, set())
        self.assertEqual(out, "Hello [human], [human] and [human] and pat doe")

    def test_rare_names_any_case(self):
        n = build_bundle.Names([], ["vexa", "Rook Ashby"], zipf=lambda w: 0.0)
        out, _ = build_bundle.redact("vexa, VEXA, Vexa and rook ashby met ashby", [], n, set())
        self.assertEqual(out, "[human], [human], [human] and [human] met [human]")
        self.assertEqual((n.hits["name:word"], n.hits["name:multi"]), (4, 1))
        # ordinary word boundaries for human names: compounds and host names
        out, _ = build_bundle.redact("A vexa-led effort; vexa.example.invalid has details", [], n, set())
        self.assertEqual(out, "A [human]-led effort; [human].example.invalid has details")
        n = build_bundle.Names([], ["bebb"], zipf=lambda w: 0.0)  # a name that is also a uuid hex group
        out, _ = build_bundle.redact("ref 754e033d-d976-44f4-bebb-ef7c89091101 by bebb", [], n, set())
        self.assertEqual((out, n.hits["name:word"], n.hits["id"]), ("ref [id] by [human]", 1, 1))

    def test_common_name_rule(self):
        n = build_bundle.Names([], ["Will"])
        # untagged: capitalized and not sentence-initial
        out, _ = build_bundle.redact("We will plan. Will welcomed us. Then Will left.\n- Will again, with Will.",
                                     [], n, set())
        self.assertEqual(out, "We will plan. Will welcomed us. Then [human] left.\n- Will again, with [human].")
        # tagged: only occurrences oneiroNER tagged PERSON, in any case
        text = "Thanks to will. Then Will left."
        out, _ = build_bundle.redact(text, [(10, 14, "PERSON", "will")], n, {"PERSON"}, tagged=True)
        self.assertEqual(out, "Thanks to [human]. Then Will left.")
        # ':' and ';' start a clause, not a sentence
        out, _ = build_bundle.redact("We asked; Will helped. Contact: Will at the desk.", [], n, set())
        self.assertEqual(out, "We asked; [human] helped. Contact: [human] at the desk.")

    def test_person_span_over_email_kept_for_redaction(self):
        text = "Contact Qelvara <qlv@example.invalid> or the qlvbox desk."
        s, e = text.index("Qelvara"), text.index(">") + 1
        m = text.index("qlv@")
        counts = __import__("collections").Counter()
        spans = build_bundle.snap_spans(text, [{"s": s, "e": e, "label": "PERSON", "text": text[s:e]},
                                               {"s": m, "e": m + 3, "label": "ORG", "text": "qlv"}], counts)
        self.assertEqual([sp["label"] for sp in spans], ["PERSON"])  # the mailbox ORG span goes, PERSON stays
        self.assertEqual(counts["dropped_email_or_uuid"], 1)
        n = build_bundle.Names([], [])
        out, _ = build_bundle.redact(text, [(sp["s"], sp["e"], sp["label"], sp["text"]) for sp in spans], n,
                                     {"PERSON"}, tagged=True)
        self.assertEqual(out, "Contact [human] or the qlvbox desk.")

    def test_short_and_stop_names_skipped(self):
        n = build_bundle.Names([{"id": "gemini-9", "name": "Gemini 9"}], ["B", "hello", "Gemini", "user1", "Bo Li"])
        self.assertEqual([e[0] for e in n.entries], [["bo", "li"]])
        self.assertEqual(dict(n.skipped), {"short": 1, "stop_or_agent_words": 3})

    def test_markup_stripped_with_spans(self):
        text = "<blurb>\nA <top_moments>quiet</top_moments> day at Crater Nine.\n</blurb>\n\n\n<takeaways>\nok\n</takeaways>"
        s = text.index("Crater Nine")
        hits = __import__("collections").Counter()
        out, spans = build_bundle.strip_markup(text, [(s, s + 11, "PLACE", "Crater Nine")], hits)
        self.assertEqual(out, "A quiet day at Crater Nine.\n\nok")
        self.assertEqual([out[a:b] for a, b, _, _ in spans], ["Crater Nine"])
        self.assertEqual(hits["markup_tag"], 6)  # 4 own-line tags, 2 inline
        out, _ = build_bundle.strip_markup("Done.\n\n<blurb>All good.</blurb>\n## <takeaways>\nx", [], hits)
        self.assertEqual(out, "Done.\n\nAll good.\nx")


if __name__ == "__main__":
    unittest.main()
