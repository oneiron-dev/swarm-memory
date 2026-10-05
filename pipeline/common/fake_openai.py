#!/usr/bin/env python3
"""A local OpenAI-compatible chat-completions server that answers with the mock (swarm_mock.py): no model, no key.

It lets you try the --endpoint path end to end before you point it at a real server:

    python3 pipeline/common/fake_openai.py --port 8901 --model fake-1 &
    export SWARM_LLM_BASE_URL=http://127.0.0.1:8901/v1 SWARM_LLM_MODEL=fake-1
    python3 pipeline/summarize/summarize.py run          # no --mock: real HTTP calls, placeholder answers

scripts/smoke.sh uses it with SMOKE_CLIENT=endpoint. POST /v1/chat/completions only.
"""
import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import swarm_mock  # noqa: E402


def make_handler(model):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            if not self.path.rstrip("/").endswith("/chat/completions"):
                return self.send_error(404)
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            msgs = body.get("messages") or []
            system = next((m.get("content") or "" for m in msgs if m.get("role") == "system"), "")
            user = next((m.get("content") or "" for m in msgs if m.get("role") == "user"), "")
            try:
                text = swarm_mock.reply(system, user, user)
                code, out = 200, {"id": "fake", "object": "chat.completion", "model": model,
                                  "choices": [{"index": 0, "finish_reason": "stop",
                                               "message": {"role": "assistant", "content": text}}],
                                  "usage": {"prompt_tokens": len(user) // 4, "completion_tokens": len(text) // 4}}
            except Exception as e:  # noqa: BLE001
                code, out = 500, {"error": {"message": f"fake server: {e}"}}
            data = json.dumps(out).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    return H


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8901)
    ap.add_argument("--model", default="fake-1", help="the model id every reply carries")
    a = ap.parse_args(argv)
    srv = ThreadingHTTPServer((a.host, a.port), make_handler(a.model))
    print(f"fake OpenAI-compatible endpoint: http://{a.host}:{srv.server_address[1]}/v1 model {a.model}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
