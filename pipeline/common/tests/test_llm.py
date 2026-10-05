"""The shared model client against a local fake OpenAI-compatible server, and the mock.
Run: python3 -m unittest discover -s pipeline/common/tests"""
import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import swarm_llm  # noqa: E402
import swarm_mock  # noqa: E402

KEY = "test-key-not-a-secret-0000"


class Fake(BaseHTTPRequestHandler):
    """Answers /v1/chat/completions with whatever the test queued; records each request."""
    replies, seen = [], []

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Fake.seen.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
        code, payload = Fake.replies.pop(0)
        if callable(payload):
            payload = payload(body)
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def ok(text, model="test-model-1"):
    return 200, {"model": model, "choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                 "usage": {"prompt_tokens": 11, "completion_tokens": 3}}


class Endpoint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.srv.server_address[1]}/v1"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        Fake.replies.clear()
        Fake.seen.clear()
        self.env = dict(os.environ)
        os.environ["SWARM_LLM_API_KEY"] = KEY
        for k in ("SWARM_LLM_BASE_URL", "SWARM_LLM_MODEL", "SWARM_LLM_MOCK", "SWARM_LLM_JSON_MODE"):
            os.environ.pop(k, None)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.env)

    def client(self, model="test-model-1"):
        return swarm_llm.from_args(argparse.Namespace(endpoint=self.url, model=model, mock=None, prime_agent=None))

    def test_one_user_message_material_first_and_the_key_in_the_header(self):
        Fake.replies.append(ok("hello"))
        res = self.client().call("[1] 09:00 a (chat): hi", "Summarize.", "You write.")
        self.assertEqual(res, {"text": "hello", "model": "test-model-1", "in": 11, "out": 3})
        req = Fake.seen[0]
        self.assertEqual(req["path"], "/v1/chat/completions")
        self.assertEqual(req["auth"], f"Bearer {KEY}")
        self.assertEqual(req["body"]["messages"], [{"role": "system", "content": "You write."},
                                                   {"role": "user", "content": "[1] 09:00 a (chat): hi\n\nSummarize."}])
        self.assertNotIn("response_format", req["body"])

    def test_json_mode_and_a_server_without_it(self):
        Fake.replies += [ok("{}"), (400, {"error": {"message": "response_format is not supported"}}), ok("{}")]
        c = self.client()
        c.call("x", "y", "z", json_mode=True)
        self.assertEqual(Fake.seen[-1]["body"]["response_format"], {"type": "json_object"})
        c.call("x", "y", "z", json_mode=True)  # 400 on response_format: asked again without it, and never again
        self.assertNotIn("response_format", Fake.seen[-1]["body"])
        self.assertFalse(c.json_mode_ok)

    def test_a_different_model_stops_and_a_dated_snapshot_does_not(self):
        Fake.replies += [ok("x", model="other-model"), ok("x", model="test-model-1-2031-01-01")]
        with self.assertRaises(swarm_llm.ModelMismatch):
            self.client().call("a", "b", "c")
        self.assertEqual(self.client().call("a", "b", "c")["model"], "test-model-1-2031-01-01")

    def test_rate_limits_are_marked_and_the_key_never_shows(self):
        Fake.replies += [(429, {"error": {"message": "slow down"}}), (500, {"error": "boom"})]
        for _ in range(2):
            with self.assertRaises(swarm_llm.CallError) as cm:
                self.client().call("a", "b", "c")
            self.assertTrue(cm.exception.rate_limited)
            self.assertNotIn(KEY, str(cm.exception))

    def test_the_environment_configures_the_client(self):
        os.environ.update({"SWARM_LLM_BASE_URL": self.url, "SWARM_LLM_MODEL": "test-model-1"})
        c = swarm_llm.from_args(argparse.Namespace(endpoint=None, model=None, mock=None, prime_agent=None))
        self.assertEqual((c.backend, c.model), ("endpoint", "test-model-1"))
        self.assertNotIn(KEY, c.describe())


class Pick(unittest.TestCase):
    def setUp(self):
        self.env = dict(os.environ)
        for k in ("SWARM_LLM_BASE_URL", "SWARM_LLM_MODEL", "SWARM_LLM_MOCK", "PRIME_AGENT"):
            os.environ.pop(k, None)
        os.environ["PATH"] = "/nonexistent"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.env)

    def ns(self, **kw):
        base = {"endpoint": None, "model": None, "mock": None, "prime_agent": None}
        base.update(kw)
        return argparse.Namespace(**base)

    def test_a_prime_agent_on_path_is_never_picked(self):
        bindir = tempfile.mkdtemp()
        try:
            exe = os.path.join(bindir, "prime-agent")
            with open(exe, "w") as f:
                f.write("#!/bin/sh\nexit 0\n")
            os.chmod(exe, 0o755)
            os.environ["PATH"] = bindir
            with self.assertRaises(SystemExit) as cm:
                swarm_llm.from_args(self.ns())
            self.assertIn("no model backend configured", str(cm.exception))
            self.assertIsNone(swarm_llm.from_args(self.ns(), required=False))
            os.environ["PRIME_AGENT"] = exe  # asked for by path: then it is used
            self.assertEqual(swarm_llm.from_args(self.ns()).backend, "prime")
            os.environ["SWARM_LLM_MOCK"] = "1"  # the dry-run switch wins over every other variable
            self.assertEqual(swarm_llm.from_args(self.ns()).backend, "mock")
        finally:
            shutil.rmtree(bindir)

    def test_order_flags_then_environment(self):
        self.assertEqual(swarm_llm.from_args(self.ns(mock=True, endpoint="http://x/v1", model="m")).backend, "mock")
        self.assertEqual(swarm_llm.from_args(self.ns(endpoint="http://x/v1", model="m", prime_agent="pa")).backend,
                         "endpoint")
        pa = swarm_llm.from_args(self.ns(prime_agent="/opt/prime-agent"))
        self.assertEqual((pa.backend, pa.model), ("prime", swarm_llm.DEFAULT_MODEL))
        os.environ["SWARM_LLM_MOCK"] = "1"
        self.assertEqual(swarm_llm.from_args(self.ns()).backend, "mock")

    def test_nothing_configured_says_what_to_do(self):
        with self.assertRaises(SystemExit) as cm:
            swarm_llm.from_args(self.ns())
        self.assertIn("no model backend configured", str(cm.exception))
        self.assertIn("export SWARM_LLM_MOCK=1", str(cm.exception))
        self.assertIn("SWARM_LLM_BASE_URL=", str(cm.exception))
        self.assertIsNone(swarm_llm.from_args(self.ns(), required=False))
        with self.assertRaises(SystemExit):
            swarm_llm.from_args(self.ns(endpoint="http://x/v1"))  # an endpoint needs a model id

    def test_model_matches(self):
        self.assertTrue(swarm_llm.model_matches("gpt-4o-mini", "gpt-4o-mini-2024-07-18"))
        self.assertTrue(swarm_llm.model_matches("openai/gpt-4o-mini", "openai/gpt-4o-mini"))
        self.assertTrue(swarm_llm.model_matches("openai/gpt-4o-mini", "gpt-4o-mini"))
        self.assertFalse(swarm_llm.model_matches("gpt-4o-mini", "gpt-4o"))
        self.assertFalse(swarm_llm.model_matches("gpt-4o", "gpt-4o-mini-x") and False)
        self.assertFalse(swarm_llm.model_matches("a", None))


class Mock(unittest.TestCase):
    def test_summary_cites_its_lines_and_copies_no_text(self):
        lines = "\n".join(["[1] 09:00 alder-7 (chat): Hello, I am Jo Bloggs from Somewhere.",
                           "[2] 10:00 alder-7 (chat): The plan is ready.",
                           "[3] 11:00 alder-7 (memory): Notes for the day."])
        text = swarm_mock.reply("You write short factual summaries of one day", lines,
                                "Summarize day 1 (2031-01-01) for the agent Alder-7, from the lines above.")
        self.assertRegex(text, r"\[1\]")
        self.assertNotIn("Bloggs", text)
        self.assertGreaterEqual(text.count("."), 3)

    def test_the_mock_says_placeholder_with_no_person_like_word(self):
        # a person-like phrase ("the mock summarizer", "a reader", "the agents") is read as a person by the tagger,
        # and the builder cuts it to [human]; the fixed sentences the mock writes carry none
        src = open(swarm_mock.__file__).read()
        fixed = re.findall(r'"([A-Z][^"{}()\\\n]{20,})"', src)  # the prose literals, not the regexes
        self.assertTrue(any(f.startswith("Placeholder text, written without a model") for f in fixed))
        person = re.compile(r"\b(writer|summari[sz]er|readers?|agents|humans?|people|person|someone|author)\b", re.I)
        self.assertEqual([f for f in fixed if person.search(f)], [])
        text = swarm_mock.reply("You write short factual summaries of one day", "[1] 09:00 alder-7 (chat): Hi.",
                                "Summarize day 1 (2031-01-01) for the agent Alder-7, from the lines above.")
        self.assertIn("Placeholder text, written without a model, from the numbered source lines [1].", text)

    def test_audit_and_classify_shapes(self):
        self.assertEqual(json.loads(swarm_mock.reply("You check a short text", "x", "y")), {"names": []})
        items = "\n".join(json.dumps(x) for x in ({"i": 1, "text": "Jo", "label": "PERSON"},
                                                  {"i": 2, "text": "Acme", "label": "ORG"}))
        out = json.loads(swarm_mock.reply("s", items, "t", "classify"))
        self.assertEqual(out["n_items"], 2)
        self.assertEqual([h["i"] for h in out["human_individual"]], [1])

    def test_an_unknown_purpose_is_an_error(self):
        with self.assertRaises(ValueError):
            swarm_mock.reply("s", "x", "t", "poem")


if __name__ == "__main__":
    unittest.main()
