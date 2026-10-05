"""book.py on a tiny synthetic served bundle with a fake prime-agent.  Run: python3 -m unittest discover -s pipeline/book/tests"""
import argparse
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import book as B  # noqa: E402

FAKE = os.path.join(HERE, "fake_prime_agent.py")
GOALS = ["Open a pretend bakery on the moon", "Write a fake almanac"]


def make_bundle(data):
    def dump(rel, obj):
        path = os.path.join(data, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(obj, f)

    counts = {"chat": 3, "sessions": 1, "thinking": 0, "memories": 1, "mentions": 0}
    days = [{"day": d, "date": f"2030-01-0{d}", "village_goal": g, "active_agents": ["agent-alpha"], "counts": counts}
            for d, g in ((1, GOALS[0]), (2, None), (3, GOALS[1]), (4, GOALS[1]))]
    dump("bundle.json", {"days": days, "goals": [{"goal": g} for g in GOALS],
                         "agents": [{"id": "agent-alpha", "name": "Agent Alpha"}, {"id": "human", "name": "human"}]})
    dump("summaries/agent/agent-alpha.json", [
        {"id": f"agent-day:agent-alpha:{d}", "scope": "agent", "key": "agent-alpha", "day": d, "text": t,
         "cites": c} for d, t, c in (
            (1, "Baked fake bread [1]. Chased a ghost [2].", [{"n": 1, "ref": "chat:a1"}, {"n": 2, "ref": "chat:gone"}]),
            (2, "Fixed the pretend oven [1].", [{"n": 1, "ref": "chat:b1"}]),
            (3, "Drafted the almanac [1].", [{"n": 1, "ref": "chat:c1"}]))])
    dump("summaries/village/1.json", {"id": "village-day:1", "day": 1, "text": "The agents baked.",
                                      "cites": [{"n": 1, "ref": "sum:d1"}]})
    row = {"time": "2030-01-01T17:00:00Z", "agent": "agent-alpha", "room": "room-x", "kind": "chat", "text": "x",
           "spans": []}
    dump("sources/1.json", {"chat:a1": row, "sum:d1": row})
    dump("sources/2.json", {"chat:b1": row})
    dump("sources/3.json", {"chat:c1": row})


def read(path):
    with open(path) as f:
        return [json.loads(l) for l in f if l.strip()]


class Book(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.data = os.path.join(self.tmp, "data")
        self.book = os.path.join(self.tmp, "book")
        make_bundle(self.data)

    def tearDown(self):
        shutil.rmtree(self.tmp)
        os.environ.pop("FAKE_BOOK_MODE", None)

    def run_step(self, step, mode="ok"):
        os.environ["FAKE_BOOK_MODE"] = mode
        os.environ["FAKE_BOOK_STATE"] = os.path.join(self.tmp, f"state-{step}-{mode}")
        B.main([step, "--data", self.data, "--book", self.book, "--prime-agent", FAKE, "--parallel", "2"])

    def calls(self, step, mode="ok"):
        with open(os.path.join(self.tmp, f"state-{step}-{mode}", "calls")) as f:
            return sum(1 for _ in f)

    def test_chapters_attach_days_without_goal(self):
        bundle = B.Bundle(self.data)
        self.assertEqual([[d["day"] for d in c["days"]] for c in bundle.chapters], [[1, 2], [3, 4]])
        self.assertEqual(bundle.attached, 1)
        self.assertEqual(bundle.goal_of(2), GOALS[0])

    def test_day_material_numbers_only_refs_in_sources(self):
        material, nmap = B.day_material(B.Bundle(self.data), 1)
        self.assertEqual(nmap, {1: "chat:a1", 2: "sum:d1"})
        self.assertIn("Agent Alpha: Baked fake bread [1]. Chased a ghost.", material)
        self.assertIn("log of the day [2]", material)

    def test_days_then_chapters(self):
        self.run_step("days")
        days = {r["day"]: r for r in read(os.path.join(self.book, "days.jsonl"))}
        self.assertEqual(sorted(days), [1, 2, 3, 4])
        self.assertTrue(days[4]["empty"])  # no material at all: no call
        self.assertEqual(self.calls("days"), 3)
        d1 = days[1]
        self.assertEqual(d1["cites"], [{"n": 1, "ref": "chat:a1", "day": 1}, {"n": 2, "ref": "sum:d1", "day": 1}])
        self.assertNotIn("[99]", d1["text"])
        self.assertEqual(d1["dropped"], 1)
        self.assertEqual(days[2]["cites"], [{"n": 1, "ref": "chat:b1", "day": 2}])  # [2] unknown on day 2
        self.assertEqual(days[2]["dropped"], 2)
        self.run_step("days")  # resume: nothing left to do
        self.assertEqual(len(read(os.path.join(self.book, "days.jsonl"))), 4)
        self.run_step("chapters")
        ch = {r["n"]: r for r in read(os.path.join(self.book, "chapters.jsonl"))}
        self.assertEqual(ch[1]["title"], "The Pretend Bakery Week")
        self.assertEqual(ch[1]["goal"], GOALS[0])
        self.assertLessEqual({c["ref"] for c in ch[1]["cites"]}, {"chat:a1", "sum:d1", "chat:b1"})
        for c in ch[1]["cites"]:
            self.assertIn(c["day"], (1, 2))
        self.assertNotIn("[99]", ch[1]["text"])

    def test_hooks_beats_and_figure(self):
        self.run_step("days")
        self.run_step("chapters")
        self.run_step("hooks")
        hooks = {r["n"]: r for r in read(os.path.join(self.book, "hooks.jsonl"))}
        ch = {r["n"]: r for r in read(os.path.join(self.book, "chapters.jsonl"))}
        self.assertEqual(sorted(hooks), [1, 2])
        h = hooks[1]
        self.assertEqual(h["src"], B.text_sha(ch[1]["text"]))
        self.assertEqual(h["hook"], "Sixteen loaves of fake bread, and an oven that broke at the end")
        self.assertEqual(h["beats"][1], {"text": "The baking went on all week [1].", "cites": [1]})  # marker moved
        self.assertIsNone(h["figure"])
        self.assertTrue(h["figure_none"])
        self.assertEqual(h["why"], {})
        self.run_step("hooks")  # cached: nothing asked
        self.assertEqual(self.calls("hooks"), 2)
        with open(os.path.join(self.book, "chapters.jsonl"), "a") as f:  # a new chapter text is asked again
            f.write(json.dumps(dict(ch[1], text=ch[1]["text"].replace("fake bread", "pretend bread"))) + "\n")
        self.run_step("hooks")
        self.assertEqual(self.calls("hooks"), 3)
        self.assertEqual(B.hooks_status(argparse.Namespace(book=self.book))["hooks"], 2)

    def test_hooks_retry_once_then_null_the_field(self):
        self.run_step("days")
        self.run_step("chapters")
        self.run_step("hooks", "hookbad")  # wrong first, right on the retry
        self.assertEqual(self.calls("hooks", "hookbad"), 4)
        self.assertTrue(all(r["hook"] and r["why"] == {} for r in read(os.path.join(self.book, "hooks.jsonl"))))
        os.remove(os.path.join(self.book, "hooks.jsonl"))
        self.run_step("hooks", "hookworse")  # the hook and the figure fail twice: null, with the reason
        self.assertEqual(self.calls("hooks", "hookworse"), 4)
        r = read(os.path.join(self.book, "hooks.jsonl"))[0]
        self.assertIsNone(r["hook"])
        self.assertIsNone(r["figure"])
        self.assertFalse(r["figure_none"])
        self.assertEqual(len(r["beats"]), 3)
        self.assertEqual(sorted(r["why"]), ["figure", "hook"])
        self.assertIn("question", r["why"]["hook"])

    def test_scroll_fields_rules(self):
        text = "Agent Alpha 2 baked 12 loaves on January 3 [1]. The oven broke in 2031 [2]."
        nmap = {1: 1, 2: 2}
        beat = {"text": "Agent Alpha 2 baked loaves all week [1].", "cites": [1]}
        ok, why = B.scroll_fields({"hook": "Twelve loaves baked—then the oven broke for good",
                                   "beats": [beat] * 3, "figure": {"value": "12", "label": "loaves baked.", "cites": 1}},
                                  text, nmap, ["Agent Alpha 2"])
        self.assertEqual(why, {})
        self.assertEqual(ok["hook"], "Twelve loaves baked, then the oven broke for good")
        self.assertEqual(ok["figure"], {"value": "12", "label": "loaves baked", "cites": [1]})
        for value in ("2", "3", "2031"):  # an agent's name, a date, a year
            _, why = B.scroll_fields({"hook": None, "beats": [], "figure": {"value": value, "label": "x", "cites": [1]}},
                                     text, nmap, ["Agent Alpha 2"])
            self.assertIn("date, a year or part of an agent's name", why["figure"])
        _, why = B.scroll_fields({"beats": [{"text": "It broke [1][2][1][2].", "cites": []}] * 3, "figure": None},
                                 text, {1: 1, 2: 2})
        self.assertNotIn("beats", why)  # [1][2] twice is two numbers
        _, why = B.scroll_fields({"beats": [{"text": "It broke [1][2][3][4].", "cites": []}] * 3, "figure": None},
                                 text, {1: 1, 2: 2, 3: 3, 4: 4})
        self.assertIn("keep at most 3", why["beats"])

    def test_stage_props_and_moments(self):
        self.run_step("days")
        self.run_step("chapters")
        self.run_step("hooks")
        self.run_step("stage")
        stage = {r["n"]: r for r in read(os.path.join(self.book, "stage.jsonl"))}
        hooks = {r["n"]: r for r in read(os.path.join(self.book, "hooks.jsonl"))}
        self.assertEqual(sorted(stage), [1, 2])
        r = stage[1]
        self.assertEqual((r["src"], r["beats_src"]), (hooks[1]["src"], B.stage_key(hooks[1]["beats"])))
        self.assertEqual(r["stage"], {  # kinds lowercased, quotes and a closing full stop gone, a name made an id
            "props": [{"kind": "stall", "label": "moon bakery"}, {"kind": "garden", "label": None}],
            "moments": [{"beat": 2, "kind": "fail", "agents": ["agent-alpha"], "label": "the oven"},
                        {"beat": 0, "kind": "build", "agents": [], "label": None}]})
        self.assertIsNone(r["why"])
        self.assertEqual(r["prompt"], B.STAGE_VERSION)
        with open(os.path.join(self.tmp, "state-stage-ok", "prompts.txt")) as f:
            prompt = f.read()
        self.assertIn("agent-alpha: Agent Alpha", prompt)
        self.assertIn("0: Agent Alpha baked fake bread for the room.", prompt)  # beats from 0, markers out
        self.assertIn("2: The oven broke on the last day.", prompt)
        self.run_step("stage")  # cached: nothing asked
        self.assertEqual(self.calls("stage"), 2)
        with open(os.path.join(self.book, "hooks.jsonl"), "a") as f:  # new beats for the same text: asked again
            f.write(json.dumps(dict(hooks[1], beats=hooks[1]["beats"][::-1])) + "\n")
        self.run_step("stage")
        self.assertEqual(self.calls("stage"), 3)
        st = B.stage_status(argparse.Namespace(book=self.book))
        self.assertEqual((st["with_stage"], st["moments"], st["kinds"]["fail"]), (2, 4, 2))

    def test_stage_retry_once_then_null(self):
        self.run_step("days")
        self.run_step("chapters")
        self.run_step("hooks")
        self.run_step("stage", "stagebad")  # wrong first, right on the retry
        self.assertEqual(self.calls("stage", "stagebad"), 4)
        self.assertTrue(all(r["stage"] and r["why"] is None for r in read(os.path.join(self.book, "stage.jsonl"))))
        os.remove(os.path.join(self.book, "stage.jsonl"))
        self.run_step("stage", "stageworse")  # wrong twice: null, with the reason
        self.assertEqual(self.calls("stage", "stageworse"), 4)
        r = read(os.path.join(self.book, "stage.jsonl"))[0]
        self.assertIsNone(r["stage"])
        self.assertIn("kind 'rocket' is not one of", r["why"])

    def test_stage_fields_rules(self):
        text = "Agent Alpha opened 2 fake stalls [1]. The oven broke [2]."
        agents = {"agent-alpha": "Agent Alpha", "o9": "O9"}
        prop = {"kind": "stall", "label": "fake stalls"}
        moment = {"beat": 1, "kind": "build", "agents": ["o9"], "label": "2 stalls"}
        ok, why = B.stage_fields({"props": [prop], "moments": [moment]}, text, 3, agents)
        self.assertIsNone(why)
        self.assertEqual(ok["moments"][0]["label"], "2 stalls")
        ok, why = B.stage_fields({"props": [prop]}, text, 3, agents)  # no moments is a valid answer
        self.assertEqual((ok["moments"], why), ([], None))
        for reply, msg in (
                ({"props": []}, "give 1 to 3 props"),
                ({"props": [prop] * 4}, "give 1 to 3 props"),
                ({"props": [prop, prop]}, "same kind"),
                ({"props": [dict(prop, label="Agent Alpha stalls")]}, "names an agent"),
                ({"props": [dict(prop, label="the agent-alpha stall")]}, "names an agent"),
                ({"props": [dict(prop, label="four words are many")]}, "has 4 words"),
                ({"props": [dict(prop, label="7 stalls")]}, "number the paragraph does not state (7)"),
                ({"props": [dict(prop, label="a [human] stall")]}, "square brackets"),
                ({"props": [prop], "moments": [dict(moment, beat=3)]}, "beat 3 is not a beat number from 0 to 2"),
                ({"props": [prop], "moments": [dict(moment, beat=True)]}, "is not a beat number"),
                ({"props": [prop], "moments": [dict(moment, agents=["human"])]}, "agent 'human' is not an agent"),
                ({"props": [prop], "moments": [dict(moment, agents=["o9", "agent-alpha", "x", "y", "z"])]}, "at most 4"),
                ({"props": [prop], "moments": [dict(moment, kind="dance")]}, "kind 'dance' is not one of"),
                ({"props": [prop], "moments": [moment] * 7}, "at most 6 moments"),
                ({"props": [prop], "moments": [moment, dict(moment, kind="fail")]}, "two moments are on beat 1")):
            ok, why = B.stage_fields(reply, text, 3, agents)
            self.assertIsNone(ok)
            self.assertIn(msg, why)
        self.assertIn("not one JSON object", B.stage_fields(None, text, 3, agents)[1])

    def test_adherence_retries_a_short_batch(self):
        self.run_step("adherence", "short")
        rows = read(os.path.join(self.book, "adherence.jsonl"))
        self.assertEqual(self.calls("adherence", "short"), 2)
        self.assertEqual([r["score"] for r in rows], ["on", "partly", "off"])
        self.assertEqual(rows[0]["id"], "agent-day:agent-alpha:1")

    def test_adherence_never_saves_a_bad_batch(self):
        self.run_step("adherence", "bad")
        self.assertFalse(os.path.exists(os.path.join(self.book, "adherence.jsonl")))
        self.assertEqual(len(read(os.path.join(self.book, "adherence-null.jsonl"))), 3)
        self.assertEqual(self.calls("adherence", "bad"), 2)  # retried once
        self.run_step("adherence")  # a rerun does what is missing
        self.assertEqual(len(read(os.path.join(self.book, "adherence.jsonl"))), 3)

    def test_model_mismatch_stops(self):
        with self.assertRaises(SystemExit) as cm:
            self.run_step("days", "mismatch")
        self.assertEqual(cm.exception.code, 3)
        self.assertFalse(os.path.exists(os.path.join(self.book, "days.jsonl"))
                         and any(not r.get("empty") for r in read(os.path.join(self.book, "days.jsonl"))))

    def test_rephrase_rewords_a_sentence_the_redaction_would_cut(self):
        meta = os.path.join(self.tmp, "meta")
        os.makedirs(meta)
        for name, obj in (("agents.json", [{"id": "agent-alpha", "name": "Agent Alpha", "model": "alpha-1"}]),
                          ("human_names.json", ["Zed Quill", "Pebble"]), ("extra_human_ids.json", []),
                          ("extra_human_rules.json", {}), ("context_handles.json", []), ("common_words.json", {})):
            with open(os.path.join(meta, name), "w") as f:
                json.dump(obj, f)
        os.makedirs(self.book)
        raw = "Agent Alpha baked a Pebble cake for the room [1]. A human cheered [2]."
        with open(os.path.join(self.book, "days.jsonl"), "w") as f:
            f.write(json.dumps({"day": 1, "text": raw, "cites": []}) + "\n")
            f.write(json.dumps({"day": 3, "text": "Agent Alpha drafted the almanac [1].", "cites": []}) + "\n")
        extra = ["--meta", meta, "--common-words", os.path.join(meta, "common_words.json")]

        def rephrase(mode):
            os.environ["FAKE_BOOK_MODE"] = mode
            os.environ["FAKE_BOOK_STATE"] = os.path.join(self.tmp, f"state-rephrase-{mode}")
            B.main(["rephrase", "--data", self.data, "--book", self.book, "--prime-agent", FAKE, *extra])
            return read(os.path.join(self.book, "rewrites.jsonl"))

        rows = rephrase("stubborn")  # the reply still carries the word: the redacted version stays
        self.assertEqual([(r["unit"], r["accepted"], r["text"]) for r in rows], [("day:1", False, None)])
        self.assertEqual(rephrase("stubborn"), rows)  # cached: a rerun asks nothing
        os.remove(os.path.join(self.book, "rewrites.jsonl"))
        rows = rephrase("ok")
        self.assertEqual(len(rows), 1)  # day 3 gets no chip: never sent
        r = rows[0]
        self.assertTrue(r["accepted"])
        self.assertEqual(r["text"], "Agent Alpha baked a stone cake for the room [1]. A human cheered [2].")
        self.assertEqual(r["src"], B.text_sha(raw))
        self.assertEqual(self.calls("rephrase", "ok"), 1)
        self.assertEqual(rephrase("ok"), rows)  # the reworded text gets no chip: nothing more to do

    def pairs_fixture(self):
        """A served edge between Agent Alpha and the humans on day 1, the two chat rows that made it, and the meta/
        the builder's name matcher reads."""
        with open(os.path.join(self.data, "bundle.json")) as f:
            b = json.load(f)
        b["views"] = {"interactions": {"edges": [{"from": "human", "to": "agent-alpha", "w": 2,
                                                  "kinds": {"mention": 1, "reply": 1}, "by_day": [[1, 2]]}]}}
        with open(os.path.join(self.data, "bundle.json"), "w") as f:
            json.dump(b, f)
        os.makedirs(os.path.join(self.tmp, "slice"))
        with open(os.path.join(self.tmp, "slice", "slice-chat.jsonl"), "w") as f:
            for i, (who, text) in enumerate((("agent-alpha", "The pretend oven is warm."),
                                             ("human", "Agent Alpha, is the oven ready?"))):
                f.write(json.dumps({"id": f"chat:p-{i}", "time": f"2030-01-01T17:0{i}:00Z", "day": 1, "agent": who,
                                    "speaker": "human" if who == "human" else "agent", "room": "room-x",
                                    "kind": "chat", "text": text}) + "\n")
        meta = os.path.join(self.tmp, "meta")
        os.makedirs(meta)
        for name, obj in (("agents.json", [{"id": "agent-alpha", "name": "Agent Alpha", "model": "alpha-1"}]),
                          ("rooms.json", [{"id": "room-x", "name": "room-x"}]), ("common_words.json", {})):
            with open(os.path.join(meta, name), "w") as f:
                json.dump(obj, f)
        return ["--root", self.tmp, "--common-words", os.path.join(meta, "common_words.json")]

    def test_pairs_writes_one_sentence_per_talking_pair(self):
        """The pairs step asks once per pair and saves the sentence (its dispatch went missing once and the step
        printed "18 to do" and wrote nothing)."""
        extra = self.pairs_fixture()
        os.environ["FAKE_BOOK_MODE"] = "ok"
        os.environ["FAKE_BOOK_STATE"] = os.path.join(self.tmp, "state-pairs")
        B.main(["pairs", "--data", self.data, "--book", self.book, "--prime-agent", FAKE, *extra])
        rows = read(os.path.join(self.book, "pairs.jsonl"))
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["day"], rows[0]["a"], rows[0]["b"]), (1, "agent-alpha", "human"))
        self.assertEqual(rows[0]["text"], "Agent Alpha and the humans talked about the pretend oven that day.")
        self.assertEqual(rows[0]["cites"], ["chat:p-0"])
        with open(os.path.join(self.tmp, "state-pairs", "calls")) as f:
            self.assertEqual(sum(1 for _ in f), 1)
        B.main(["pairs", "--data", self.data, "--book", self.book, "--prime-agent", FAKE, *extra])
        self.assertEqual(len(read(os.path.join(self.book, "pairs.jsonl"))), 1)  # cached: nothing asked again

    def test_the_mock_writes_a_whole_book_that_passes_every_rule(self):
        """--mock: no model, no key; every step's reply passes the same acceptance rules a model's must."""
        extra = self.pairs_fixture()
        for step in ("days", "gaps", "chapters", "hooks", "pairs", "stage", "adherence", "rephrase"):
            B.main([step, "--mock", "--data", self.data, "--book", self.book, "--parallel", "2", *extra])
        days = {r["day"]: r for r in read(os.path.join(self.book, "days.jsonl"))}
        self.assertTrue(all(B.day_ok(r["text"], r["cites"], r["cites"]) is None for r in days.values()
                            if not r.get("empty")))
        self.assertEqual({r["model"] for r in days.values() if not r.get("empty")}, {"mock"})
        chapters = read(os.path.join(self.book, "chapters.jsonl"))
        self.assertEqual(len(chapters), 2)
        hooks = read(os.path.join(self.book, "hooks.jsonl"))
        self.assertTrue(all(h["hook"] and h["beats"] and not h["why"] for h in hooks))
        stage = read(os.path.join(self.book, "stage.jsonl"))
        self.assertTrue(stage and all(r["stage"] for r in stage))
        pairs = read(os.path.join(self.book, "pairs.jsonl"))
        self.assertTrue(pairs and all(isinstance(r["text"], str) for r in pairs))
        adh = read(os.path.join(self.book, "adherence.jsonl"))
        self.assertEqual(len(adh), 3)

    def test_exhausted_retries_stay_missing(self):
        self.run_step("days", "short")  # too short and uncited, three times: never saved
        rows = read(os.path.join(self.book, "days.jsonl"))
        self.assertEqual([r["day"] for r in rows], [4])  # only the day without material
        self.assertEqual(self.calls("days", "short"), 9)
        self.run_step("chapters", "short")  # its days are missing: it waits
        self.assertFalse(os.path.exists(os.path.join(self.book, "chapters.jsonl")))
        self.run_step("days")
        self.assertEqual(sorted(r["day"] for r in read(os.path.join(self.book, "days.jsonl"))), [1, 2, 3, 4])
        self.run_step("chapters", "short")
        self.assertFalse(os.path.exists(os.path.join(self.book, "chapters.jsonl")))
        self.assertEqual(self.calls("chapters", "short"), 6)

    def test_a_cached_paragraph_that_fails_the_rule_is_asked_again(self):
        os.makedirs(self.book)
        with open(os.path.join(self.book, "days.jsonl"), "w") as f:
            f.write(json.dumps({"day": 1, "text": "Too short [1].", "cites": [{"n": 1, "ref": "chat:a1", "day": 1}]}) + "\n")
        self.run_step("days")
        days = {}
        for r in read(os.path.join(self.book, "days.jsonl")):
            days[r["day"]] = r  # the last line wins
        self.assertGreaterEqual(B.words(days[1]["text"]), 80)

    def test_a_torn_tail_is_cut_before_appending(self):
        os.makedirs(self.book)
        for name, torn in (("days.jsonl", '{"day": 2'), ("adherence.jsonl", '{"id": "agent-day:agent-alpha:1", "sc')):
            with open(os.path.join(self.book, name), "w") as f:
                f.write(torn)
        self.run_step("days")
        self.assertEqual(sorted(r["day"] for r in read(os.path.join(self.book, "days.jsonl"))), [1, 2, 3, 4])
        self.run_step("adherence")
        self.assertEqual(len(read(os.path.join(self.book, "adherence.jsonl"))), 3)
        with open(os.path.join(self.book, "days.jsonl")) as f:
            self.assertTrue(f.read().endswith("\n"))

    def test_adherence_wants_the_whole_reply_to_be_the_array(self):
        for mode in ("wrapped", "prose", "boolidx"):
            shutil.rmtree(self.book, ignore_errors=True)
            self.run_step("adherence", mode)
            self.assertFalse(os.path.exists(os.path.join(self.book, "adherence.jsonl")), mode)
            self.assertEqual(len(read(os.path.join(self.book, "adherence-null.jsonl"))), 3, mode)
            self.assertEqual(self.calls("adherence", mode), 2, mode)

    def test_dropped_counts_expand_ranges(self):
        self.run_step("days", "range")
        days = {r["day"]: r for r in read(os.path.join(self.book, "days.jsonl"))}
        self.assertEqual(days[1]["dropped"], 4)  # [99], and 3, 4, 5 of [2-5]
        self.assertEqual(days[2]["dropped"], 6)  # [2], [99], and 2 to 5
        self.assertNotIn("[3]", days[2]["text"])

    def test_gap_days_from_raw_rows_or_a_placement_line(self):
        with open(os.path.join(self.data, "bundle.json")) as f:
            b = json.load(f)
        b["days"].append(dict(b["days"][-1], day=5, date="2030-01-05"))
        with open(os.path.join(self.data, "bundle.json"), "w") as f:
            json.dump(b, f)
        os.makedirs(os.path.join(self.tmp, "slice"))
        with open(os.path.join(self.tmp, "slice", "slice-chat.jsonl"), "w") as f:
            for i, (who, text) in enumerate((("agent-alpha", "Tidied the pretend kitchen."), ("human", "Nice work!"))):
                f.write(json.dumps({"id": f"chat:gap-{i}", "time": f"2030-01-05T17:0{i}:00Z", "day": 5, "agent": who,
                                    "speaker": "human" if who == "human" else "agent", "room": "room-x",
                                    "kind": "chat", "text": text}) + "\n")
        self.run_step("days")
        os.environ["FAKE_BOOK_STATE"] = os.path.join(self.tmp, "state-gaps")
        for _ in range(2):  # the second run has nothing left to do
            B.main(["gaps", "--root", self.tmp, "--data", self.data, "--book", self.book, "--prime-agent", FAKE])
        days = {}
        for r in read(os.path.join(self.book, "days.jsonl")):
            days[r["day"]] = r
        self.assertEqual(days[4]["text"], "The village was paused this day; the next entry is day 5.")
        self.assertTrue(days[4]["placement"])
        d5 = days[5]
        self.assertTrue(d5["gap"])
        self.assertTrue(40 <= B.words(d5["text"]) <= 90)
        self.assertEqual({c["ref"] for c in d5["cites"]}, {"chat:gap-0", "chat:gap-1"})
        self.assertEqual(d5["dropped"], 1)
        with open(os.path.join(self.tmp, "state-gaps", "calls")) as f:
            self.assertEqual(sum(1 for _ in f), 1)
        self.run_step("days")  # the gap paragraph is accepted as it is: no new call
        self.assertEqual(self.calls("days"), 3)

    def test_chapters_under_a_new_prompt_version(self):
        self.run_step("days")
        B.main(["chapters", "--chapter-prompt", "v1", "--data", self.data, "--book", self.book, "--prime-agent", FAKE])
        self.run_step("chapters")  # default: story-v2
        rows = read(os.path.join(self.book, "chapters.jsonl"))
        self.assertEqual(sorted((r["n"], r.get("prompt")) for r in rows[-2:]), [(1, "story-v2"), (2, "story-v2")])
        self.assertEqual(len(rows), 4)  # the v1 replies stay for comparison
        with open(os.path.join(self.tmp, "state-chapters-ok", "prompts.txt")) as f:
            self.assertIn("Do not manufacture obstacles", f.read())
        self.run_step("chapters")  # done under story-v2: nothing to ask
        self.assertEqual(len(read(os.path.join(self.book, "chapters.jsonl"))), 4)

    def test_a_chapter_that_fails_under_the_new_prompt_keeps_its_text(self):
        self.run_step("days")
        B.main(["chapters", "--chapter-prompt", "v1", "--data", self.data, "--book", self.book, "--prime-agent", FAKE])
        os.environ["FAKE_BOOK_MODE"] = "short"
        os.environ["FAKE_BOOK_STATE"] = os.path.join(self.tmp, "state-story-short")
        B.main(["chapters", "--content-attempts", "2", "--data", self.data, "--book", self.book, "--prime-agent", FAKE])
        rows = read(os.path.join(self.book, "chapters.jsonl"))
        self.assertEqual([r.get("prompt") for r in rows], ["v1", "v1"])  # the last accepted line per chapter is served
        with open(os.path.join(self.tmp, "state-story-short", "calls")) as f:
            self.assertEqual(sum(1 for _ in f), 4)  # two chapters, two tries each

    def test_a_named_chapter_under_the_170_word_prompt(self):
        self.run_step("days")
        B.main(["chapters", "--chapter-prompt", "story-v2-170", "--only", "2", "--content-attempts", "2",
                "--data", self.data, "--book", self.book, "--prime-agent", FAKE])
        rows = read(os.path.join(self.book, "chapters.jsonl"))
        self.assertEqual([(r["n"], r["prompt"]) for r in rows], [(2, "story-v2-170")])
        with open(os.path.join(self.tmp, "state-days-ok", "prompts.txt")) as f:
            self.assertIn("Keep the prose under 170 words.", f.read())

    def test_pair_reply_rules(self):
        nmap = {1: "chat:p1", 2: "chat:p2", 3: "chat:p3", 4: "chat:p4"}
        names = ("Agent Alpha", "the humans")
        ok = {"text": "Agent Alpha and the humans fixed the pretend oven — then baked 7–3 loaves.", "cites": [2, "[1]"]}
        text, refs, why = B.pair_reply(ok, nmap, names)
        self.assertIsNone(why)
        self.assertEqual(text, "Agent Alpha and the humans fixed the pretend oven, then baked 7-3 loaves.")
        self.assertEqual(refs, ["chat:p2", "chat:p1"])
        self.assertEqual(B.pair_reply(dict(ok, cites=["chat:p3"]), nmap, names)[1], ["chat:p3"])  # a ref as given
        for bad, part in ((dict(ok, text="Agent Alpha fixed the pretend oven for the bake sale."), "the humans"),
                          (dict(ok, cites=[9]), "not a line number"), (dict(ok, cites=[1, 2, 3, 4]), "1 to 3"),
                          (dict(ok, cites=[]), "1 to 3"), (dict(ok, text="Agent Alpha and the humans met."), "words"),
                          (None, "JSON object")):
            self.assertIn(part, B.pair_reply(bad, nmap, names)[2])

    def test_pair_material_caps_rows_near_the_busiest_hour(self):
        bundle = B.Bundle(self.data)
        rows = [(f"{h:02d}:{m:02d}", h * 3600 + m * 60, "agent-alpha" if m % 2 else "human", "room-x", "hello there",
                 f"chat:r{h}-{m}") for h in (9, 10) for m in range(0, 60, 1 if h == 10 else 6)]
        material, nmap = B.pair_material(bundle, rows)
        self.assertEqual(len(nmap), B.PAIR_ROWS)
        self.assertTrue(all(ref.startswith("chat:r10-") for ref in nmap.values()))  # the busy hour's rows
        self.assertIn("[1] 10:00 a human (#room-x): hello there", material)
        self.assertEqual(list(nmap.values()), sorted(nmap.values(), key=lambda r: int(r.split("-")[1])))

    def test_check_batch(self):
        good = [{"i": 0, "score": "on", "note": "a " * 30}, {"i": 1, "score": "idle", "note": ""}]
        self.assertIsNone(B.check_batch([{"i": False, "score": "on", "note": ""}, {"i": True, "score": "on", "note": ""}], 2))
        self.assertIsNone(B.parse_array('{"results": []}'))
        self.assertIsNone(B.parse_array('Here: [] done'))
        self.assertEqual(B.parse_array(' [] '), [])
        out = B.check_batch(good, 2)
        self.assertEqual(len(out[0][1].split()), B.NOTE_WORDS)
        self.assertIsNone(B.check_batch(good, 3))
        self.assertIsNone(B.check_batch([{"i": 0, "score": "maybe", "note": ""}], 1))
        self.assertIsNone(B.check_batch([{"i": 1, "score": "on", "note": ""}], 1))

    def test_split_title(self):
        self.assertEqual(B.split_title("TITLE: The Oven Week\n\nBody [1]."), ("The Oven Week", "\nBody [1]."))
        self.assertEqual(B.split_title("**The Oven Week**\n\nBody [1].")[0], "The Oven Week")
        self.assertEqual(B.split_title("A long first line that is clearly the paragraph and not a title at all [1].")[0], "")

    def test_renumber_and_compact(self):
        self.assertEqual(B.renumber("A [1]. B [2][3].", {1: 5, 3: 6}), "A [5]. B [6].")
        text, cites = B.compact("A [7]. B [3][7].", [{"n": 3, "ref": "x"}, {"n": 7, "ref": "y"}])
        self.assertEqual(text, "A [1]. B [2][1].")
        self.assertEqual(cites, [{"n": 1, "ref": "y"}, {"n": 2, "ref": "x"}])


if __name__ == "__main__":
    unittest.main()
