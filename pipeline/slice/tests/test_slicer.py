"""Synthetic fixtures, one or two fake rows per table shape. No dataset content."""

import gzip
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import shapes  # noqa: E402
import slicer  # noqa: E402

A1, A2, A3 = "a1111111-0000-0000-0000-000000000001", "a2222222-0000-0000-0000-000000000002", "a3333333-0000-0000-0000-000000000003"
R1, R2 = "r1111111-0000-0000-0000-000000000001", "r2222222-0000-0000-0000-000000000002"
U1 = "eeeeeeee-0000-0000-0000-00000000dead"  # fake user id
S1, S2 = "s1111111-0000-0000-0000-000000000001", "s2222222-0000-0000-0000-000000000002"


def gz(path, rows):
    with gzip.open(path, "wt", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def build(d):
    gz(f"{d}/agents.jsonl.gz", [
        {"id": A1, "name": "Agent Alpha 1.5", "model_string": "claude-test-1", "is_participating": True,
         "created_at": "2030-01-01 10:00:00.000001"},
        {"id": A2, "name": "Agent Beta (Test Rig)", "model_string": "gpt-test-9", "is_participating": False,
         "created_at": "2030-01-01 10:00:00.000002"},
        {"id": A3, "name": "Agent Gamma", "model_string": "o3-test", "is_participating": True,
         "created_at": "2030-01-01 10:00:00.000003"},
    ])
    gz(f"{d}/chat_rooms.jsonl.gz", [
        {"id": R1, "name": "room-x", "deleted_at": None, "created_at": "2030-01-01 00:00:00"},
        {"id": R2, "name": "room.y", "deleted_at": "2030-01-05 12:00:00.5", "created_at": "2030-01-01 00:00:01"},
    ])
    gz(f"{d}/village_goals.jsonl.gz", [
        {"id": "g1", "goal": "Build a fake thing", "start_time": "2030-01-01 17:00:00", "end_time": "2030-01-02 18:00:00"},
        {"id": "g2", "goal": "Build another fake thing", "start_time": "2030-01-02 18:00:00", "end_time": None},
    ])
    gz(f"{d}/agent_goals.jsonl.gz", [
        {"id": "ag1", "agent_id": A1, "name": "Fake goal", "short_name": "fake", "description": None,
         "start_time": "2030-01-01 17:00:00", "end_time": None, "created_at": "2030-01-01 17:00:00"},
    ])
    transcript = {"village": {}, "dateRange": {}, "days": [
        {"day": 1, "date": "2030-01-01", "events": [{"timestamp": "2030-01-01T17:30:00.000Z"},
                                                   {"timestamp": "2030-01-01T17:05:00.000Z"}]},
        {"day": 2, "date": "2030-01-02", "events": [{"timestamp": "2030-01-02T17:00:00.000Z"}]},
        {"day": 3, "date": "2030-01-05", "events": []},
        {"day": 4, "date": "2030-01-06", "events": [{"timestamp": "2030-01-06T17:00:00.000Z"}]},
    ]}
    with open(f"{d}/village-transcript.json", "w") as f:
        json.dump(transcript, f)
    gz(f"{d}/summaries.jsonl.gz", [
        {"id": "sum1", "type": "daily", "summary_target": "2", "summary_date": "2030-01-02",
         "content": "A fake day happened.", "generated_by": "fake-model", "created_at": "2030-02-01 00:00:00"},
        {"id": "sum3", "type": "agent_daily", "summary_target": "Agent Gamma:2", "summary_date": None,
         "content": "Agent Gamma on fake day two.", "generated_by": "fake-model", "created_at": "2030-02-01 00:00:00"},
        {"id": "sum2", "type": "agent", "summary_target": "Agent Gamma", "summary_date": None,
         "content": "Agent Gamma did fake things.", "generated_by": "fake-model", "created_at": "2030-02-01 00:00:00"},
    ])
    gz(f"{d}/chat_messages.jsonl.gz", [
        {"id": "c2", "speaker_type": "agent", "agent_speaker_id": A1, "user_speaker_id": None,
         "content": "Hello   room x!", "room_id": R1, "created_at": "2030-01-01 18:00:00.123"},
        {"id": "c1", "speaker_type": "agent", "agent_speaker_id": A2, "user_speaker_id": None,
         "content": "hello room X!", "room_id": R1, "created_at": "2030-01-01 17:59:00"},
        {"id": "c3", "speaker_type": "user", "agent_speaker_id": None, "user_speaker_id": U1,
         "content": "a human says hi [REDACTED]", "room_id": R2, "created_at": "2030-01-01 16:00:00"},
        {"id": "c4", "speaker_type": "agent", "agent_speaker_id": A1, "user_speaker_id": None,
         "content": "ok", "room_id": R1, "created_at": "2030-01-02 18:00:00"},
        {"id": "c5", "speaker_type": "agent", "agent_speaker_id": A3, "user_speaker_id": None,
         "content": "late fake message for " + U1, "room_id": R1, "created_at": "2030-01-05 18:00:00"},
    ])
    ev = lambda i, ts, data: {"id": f"e{i}", "event_index": i, "created_at": ts, "data": data}
    gz(f"{d}/events.jsonl.gz", [
        ev(12, "2030-01-01 18:10:00", {"actionType": "AGENT_TALK", "speakerId": A1, "content": "Hello",
                                       "output": {"type": "message", "role": "assistant", "content": [
                                           {"type": "thinking", "thinking": "Fake anthropic thought.", "signature": "[BLOB_REMOVED]"},
                                           {"type": "text", "text": "Hello"},
                                           {"type": "tool_use", "name": "x", "input": {}}]}}),
        ev(10, "2030-01-01 18:00:00", {"actionType": "START_USING_COMPUTER", "agentId": A2,
                                       "computerUseSessionId": S1, "sessionGoal": "Open the fake app",
                                       "output": [{"type": "reasoning", "summary": [{"type": "summary_text", "text": "Fake summary A."},
                                                                                    {"type": "summary_text", "text": "Fake summary B."}]},
                                                  {"type": "function_call", "name": "x", "arguments": "{}"}]}),
        ev(11, "2030-01-01 18:05:00", {"actionType": "AGENT_TALK", "speakerId": A2, "content": "x",
                                       "output": [{"type": "reasoning", "summary": [], "content": []}]}),
        ev(13, "2030-01-01 18:20:00", {"actionType": "STOP_USING_COMPUTER", "agentId": A2,
                                       "summary": "Opened the fake app.",
                                       "output": {"candidates": [{"content": {"parts": [
                                           {"text": "Fake gemini thought.", "thought": True}, {"text": "visible"}]}}]}}),
        ev(20, "2030-01-02 18:00:00", {"actionType": "AGENT_TALK", "speakerId": A3,
                                       "output": {"role": "assistant", "content": "x", "reasoning": None,
                                                  "reasoning_details": [{"type": "reasoning.text", "text": "Fake detail thought."},
                                                                        {"type": "reasoning.encrypted", "data": "zz"}]}}),
        ev(21, "2030-01-02 18:01:00", {"actionType": "CONSOLIDATE", "agentId": A3, "computerUseSessionId": S2,
                                       "nextSessionGoal": "Do the next fake thing",
                                       "output": {"role": "assistant", "content": None, "reasoning_content": "Fake chat thought."}}),
        ev(22, "2030-01-02 18:02:00", {"actionType": "SEARCH_HISTORY", "agentId": A3, "query": "what did we fake",
                                       "answerToQuery": "Everything."}),
        ev(23, "2030-01-02 18:03:00", {"actionType": "OUTREACH_APPROVAL_REQUEST", "agentId": A3, "medium": "fake forum",
                                       "recipient": "someone-should-vanish", "rationale": "Fake reason.",
                                       "messageContent": "Fake message."}),
        ev(24, "2030-01-02 18:04:00", {"actionType": "OUTREACH_APPROVAL_RESPONSE", "agentId": A3, "medium": "fake forum",
                                       "recipient": "someone-should-vanish", "approval": False, "rationale": "Fake reason.",
                                       "messageContent": "Fake message.", "adminComment": "Say you are an AI."}),
        ev(25, "2030-01-02 18:05:00", {"actionType": "USER_TALK", "speakerName": "viewer-should-vanish", "content": "hi"}),
        ev(26, "2030-01-02 18:06:00", {"actionType": "WAIT", "agentId": A3, "output": {"weird": 1}}),
        ev(19, "2030-01-02 17:59:00", {"actionType": "PAUSE", "agentId": A3,
                                       "output": {"role": "assistant", "content": None, "reasoning": "Inside fake session two."}}),
    ])
    gz(f"{d}/claude_code_messages.jsonl.gz", [
        {"id": "cc1", "agent_id": A1, "sdk_session_id": "sdk-1", "message_type": "assistant", "created_at": "2030-01-02 19:00:00",
         "content": {"message": {"content": [{"type": "thinking", "thinking": "Fake cc thought."},
                                             {"type": "text", "text": "Fake cc text."},
                                             {"type": "tool_use", "name": "Bash", "input": {}}]}}},
        {"id": "cc2", "agent_id": A1, "sdk_session_id": "sdk-1", "message_type": "user", "created_at": "2030-01-02 19:00:01",
         "content": {"message": {"content": [{"type": "tool_result", "content": "should not appear"}]}}},
    ])
    gz(f"{d}/computer_use_sessions.jsonl.gz", [
        {"id": S1, "agent_id": A2, "session_goal": "open the fake   app", "created_at": "2030-01-01 18:00:01"},
        {"id": S2, "agent_id": A3, "session_goal": "Fake session two goal", "created_at": "2030-01-02 17:00:00"},
    ])
    gz(f"{d}/agent_memories.jsonl.gz", [
        {"id": "m1", "agent_id": A1, "content": "Memory one fake.", "created_at": "2030-01-01 18:00:00"},
        {"id": "m2", "agent_id": A1, "content": "Memory two fake.", "created_at": "2030-01-02 16:59:00"},
        {"id": "m3", "agent_id": A1, "content": "Memory three fake.", "created_at": "2030-01-02 17:01:00"},
        {"id": "m4", "agent_id": A2, "content": "Memory four fake.", "created_at": "2030-01-01 12:00:00"},
    ])


def read_json(path):
    with open(path) as f:
        return json.load(f)


def load(path):
    with open(path) as f:
        return [json.loads(line) for line in f]


class SlicerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        data, out = f"{cls.tmp.name}/data", f"{cls.tmp.name}/out"
        os.makedirs(data)
        build(data)
        slicer.Slicer(data, out, tokenizer="whitespace", log=lambda m: None).run()
        cls.out = out
        cls.recs = {}
        for name in os.listdir(f"{out}/slice"):
            if name.startswith("slice-"):
                cls.recs[name[6:-6]] = load(f"{out}/slice/{name}")
        cls.stats = read_json(f"{out}/slice/STATS.json")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def by_id(self, kind):
        return {r["id"]: r for r in self.recs[kind]}

    def test_meta(self):
        agents = read_json(f"{self.out}/meta/agents.json")
        self.assertEqual([a["id"] for a in agents], ["agent-alpha-1-5", "agent-beta-test-rig", "agent-gamma"])
        self.assertEqual([a["family"] for a in agents], ["anthropic", "openai", "openai"])
        rooms = read_json(f"{self.out}/meta/rooms.json")
        self.assertEqual([r["id"] for r in rooms], ["room-x", "room-y"])
        self.assertEqual(rooms[1]["deleted_at"], "2030-01-05T12:00:00Z")
        days = read_json(f"{self.out}/meta/days.json")
        self.assertEqual(days[0]["first_ts"], "2030-01-01T17:05:00Z")
        self.assertIsNone(days[2]["first_ts"])
        goals = read_json(f"{self.out}/meta/village_goals.json")
        self.assertEqual((goals[0]["first_day"], goals[0]["last_day"], goals[1]["last_day"]), (1, 2, None))
        self.assertEqual(read_json(f"{self.out}/meta/agent_goals.json")[0]["agent"], "agent-alpha-1-5")

    def test_family(self):
        cases = {"claude-code::claude-opus-x": "anthropic", "o1-2030": "openai", "o4-mini": "openai",
                 "z-ai/glm-9": "zai", "meta/muse-x": "meta", "kimi-k9": "moonshot", "deepseek/x": "deepseek",
                 "grok-9": "xai", "gemini-9": "google", "tinker://x/sampler_weights/kimi-leader": "moonshot",
                 "foo-bar": "other", "pro1": "other"}
        for model, fam in cases.items():
            self.assertEqual(slicer.family(model), fam, model)

    def test_chat_dedupe_human_and_days(self):
        chat = self.by_id("chat")
        self.assertIn("chat:c1", chat)  # earlier duplicate wins
        self.assertNotIn("chat:c2", chat)
        self.assertNotIn("chat:c4", chat)  # under 3 chars
        h = chat["chat:c3"]
        self.assertEqual((h["agent"], h["speaker"], h["room"], h["day"]), ("human", "human", "room-y", 1))
        self.assertEqual(h["ntok"], 4)  # scrub marker not counted
        self.assertEqual(chat["chat:c1"]["time"], "2030-01-01T17:59:00Z")
        self.assertEqual((chat["chat:c5"]["day"], chat["chat:c5"]["date"]), (2, "2030-01-02"))  # empty day 3 skipped
        self.assertEqual([r["id"] for r in self.recs["chat"]], ["chat:c3", "chat:c1", "chat:c5"])  # time order
        with open(f"{self.out}/slice/slice-chat.jsonl") as f:
            blob = f.read()
        self.assertNotIn("should-vanish", blob)
        self.assertNotIn(U1, blob)
        self.assertEqual(chat["chat:c5"]["text"], "late fake message for [REDACTED]")
        self.assertEqual(chat["chat:c5"]["ntok"], 4)

    def test_thinking_shapes(self):
        th = self.by_id("thinking")
        self.assertEqual(th["ev:12:thinking"]["text"], "Fake anthropic thought.")
        self.assertEqual(th["ev:10:thinking"]["text"], "Fake summary A.\n\nFake summary B.")
        self.assertEqual(th["ev:13:thinking"]["text"], "Fake gemini thought.")
        self.assertEqual(th["ev:20:thinking"]["text"], "Fake detail thought.")
        self.assertEqual(th["ev:21:thinking"]["text"], "Fake chat thought.")
        self.assertNotIn("ev:11:thinking", th)
        sh = self.stats["thinking_shapes"]
        self.assertEqual(sh["openai"]["responses_items:no_reasoning"], 1)
        self.assertEqual(sh["openai"]["unparsed"], 1)

    def test_sessions(self):
        th = self.by_id("thinking")
        self.assertEqual(th["ev:10:thinking"]["session"], f"cus:{S1}")
        self.assertEqual(th["ev:13:thinking"]["session"], f"cus:{S1}")  # STOP closes the START session
        self.assertIsNone(th["ev:12:thinking"]["session"])
        self.assertEqual(th["ev:19:thinking"]["session"], f"cus:{S2}")  # before the CONSOLIDATE that closes S2
        self.assertEqual(self.by_id("session_summary")["ev:13:summary"]["session"], f"cus:{S1}")
        goals = self.by_id("session_goal")
        self.assertIn("ev:10:goal", goals)  # the session-table copy is a later duplicate
        self.assertNotIn(f"cus:{S1}", goals)
        self.assertEqual(goals[f"cus:{S2}"]["session"], f"cus:{S2}")

    def test_event_kinds(self):
        self.assertEqual(self.recs["next_goal"][0]["text"], "Do the next fake thing")
        self.assertEqual(self.recs["history_query"][0]["text"], "Q: what did we fake\nA: Everything.")
        out = self.by_id("outreach")
        self.assertNotIn("vanish", json.dumps(out))
        self.assertEqual(out["ev:24:outreach"]["speaker"], "human")
        self.assertIn("Admin comment: Say you are an AI.", out["ev:24:outreach"]["text"])

    def test_cc(self):
        self.assertEqual(self.by_id("cc_thinking")["cc:cc1:0"]["session"], "ccs:sdk-1")
        self.assertEqual(self.by_id("cc_text")["cc:cc1:1"]["text"], "Fake cc text.")
        self.assertNotIn("should not appear", json.dumps(self.recs))

    def test_memory_last_per_agent_day(self):
        mem = self.by_id("memory")
        self.assertEqual(sorted(mem), ["mem:m2", "mem:m3", "mem:m4"])
        self.assertEqual(mem["mem:m2"]["day"], 1)

    def test_digest(self):
        d = self.by_id("digest_summary")
        self.assertEqual((d["sum:sum1"]["day"], d["sum:sum1"]["agent"]), (2, None))
        self.assertEqual(d["sum:sum2"]["agent"], "agent-gamma")
        self.assertEqual((d["sum:sum3"]["agent"], d["sum:sum3"]["day"]), ("agent-gamma", 2))
        idx = read_json(f"{self.out}/meta/summaries_index.json")
        self.assertEqual({x["type"] for x in idx}, {"daily", "agent", "agent_daily"})

    def test_stats(self):
        s = self.stats
        self.assertTrue(s["final"])
        self.assertEqual(s["kinds"]["chat"]["rows"], 3)
        self.assertEqual(s["kinds"]["chat"]["dropped"]["dupes"], 1)
        self.assertEqual(set(s["kinds"]), set(slicer.KIND_SOURCES))
        self.assertEqual(sum(s["kinds"]["chat"]["by_month"]["2030-01"].values()) > 0, True)
        for kind, rows in self.recs.items():
            for r in rows:
                self.assertEqual(set(r), {"id", "src", "time", "day", "date", "agent", "speaker", "room",
                                          "session", "kind", "text", "ntok"})
                self.assertEqual(r["kind"], kind)
        self.assertFalse([n for n in os.listdir(f"{self.out}/slice") if n.endswith((".part", ".pass1"))])


class ShapesTest(unittest.TestCase):
    def test_redacted_and_empty(self):
        self.assertEqual(shapes.reasoning([{"type": "redacted_thinking", "data": "x"}]), ("anthropic_blocks", []))
        self.assertEqual(shapes.reasoning({"choices": [{"message": {"reasoning_content": "Fake."}}]}),
                         ("chat_choices", ["Fake."]))
        self.assertEqual(shapes.reasoning("plain string")[0], shapes.UNPARSED)


if __name__ == "__main__":
    unittest.main()
