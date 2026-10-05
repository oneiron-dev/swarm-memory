"""One model client for every model step of the pipeline.

Three backends. Only an explicit choice picks one; nothing is found on PATH and nothing is guessed:
  --mock                 no model: templated, cited texts built from the prompt's own numbered lines (swarm_mock.py)
  --endpoint URL         any OpenAI-compatible chat-completions server (OpenAI, OpenRouter, vLLM, llama.cpp, Ollama, ...)
  --prime-agent PATH     our own harness (what the real run used)
With no flag the environment decides, in this order: SWARM_LLM_MOCK=1 (mock), SWARM_LLM_BASE_URL (endpoint, with
SWARM_LLM_MODEL and SWARM_LLM_API_KEY or OPENAI_API_KEY), PRIME_AGENT (the harness at that path). With none of them a
model step stops with "no model backend configured" and the line that sets one. SWARM_LLM_REASONING_EFFORT is sent
to an endpoint. The key is read from the environment inside this process; it is never printed, logged or written to
a file.

Every call checks the model id the server says it used against the model it asked for and raises ModelMismatch on
a difference (a dated snapshot of the asked model, "gpt-4o-mini-2024-07-18" for "gpt-4o-mini", counts as the same).
A step that gets ModelMismatch stops; it never keeps text from a model it did not ask for.

call() makes one attempt and raises CallError (rate_limited=True for 429, 5xx and overload replies); the steps keep
their own retry loops with backoff, as before.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.request

DEFAULT_MODEL = "gpt-5.6-luna"  # the model of the real run, through prime-agent
MOCK_MODEL = "mock"
RATE_RE = re.compile(r"\b429\b|rate.?limit|too many requests|quota|overloaded|\b5\d\d\b|unavailable", re.I)


class CallError(RuntimeError):
    def __init__(self, msg, rate_limited=False):
        super().__init__(msg)
        self.rate_limited = rate_limited


class ModelMismatch(RuntimeError):
    pass


def model_matches(asked: str, got) -> bool:
    """The reply's model is the asked one: equal, a dated or tagged snapshot of it, or equal without a provider
    prefix ("openai/gpt-4o-mini" asked, "gpt-4o-mini" answered)."""
    if not isinstance(got, str) or not got:
        return False
    if got == asked:
        return True
    for a in {asked, asked.rsplit("/", 1)[-1]}:
        g = got.rsplit("/", 1)[-1] if "/" not in a else got
        if g == a or any(g.startswith(a + sep) for sep in ("-", ":", "@")):
            return True
    return False


def add_args(ap, timeout: int = 300, model_flag: str = "--model"):
    """The model client flags, shared by every step that calls a model (pf.py names the model flag --llm-model, since
    its --model is the privacy-filter checkpoint)."""
    g = ap.add_argument_group("model client (one of --mock, --endpoint, --prime-agent; see pipeline/common/README.md)")
    g.add_argument("--mock", action="store_true", default=None,
                   help="no model: write templated, cited texts from the material (env SWARM_LLM_MOCK=1)")
    g.add_argument("--endpoint", help="OpenAI-compatible base URL, e.g. https://api.openai.com/v1 "
                                      "(env SWARM_LLM_BASE_URL; key from SWARM_LLM_API_KEY)")
    g.add_argument(model_flag, dest="llm_model", help="model id to ask for and to check replies against (env "
                                                      f"SWARM_LLM_MODEL; prime-agent default {DEFAULT_MODEL})")
    g.add_argument("--reasoning-effort", help="sent as reasoning_effort to an endpoint (env SWARM_LLM_REASONING_EFFORT)")
    g.add_argument("--prime-agent", help="path to our prime-agent harness (env PRIME_AGENT; never taken from PATH)")
    g.add_argument("--provider", default="cpa-r", help="prime-agent provider")
    g.add_argument("--thinking", default="low", help="prime-agent thinking level")
    g.add_argument("--timeout", type=int, default=timeout, help="seconds per call")
    return g


class Client:
    def __init__(self, backend: str, model: str, *, base_url=None, prime_agent=None, provider="cpa-r",
                 thinking="low", timeout=300, reasoning_effort=None):
        self.backend, self.model, self.base_url = backend, model, base_url
        self.prime_agent, self.provider, self.thinking = prime_agent, provider, thinking
        self.timeout, self.reasoning_effort = timeout, reasoning_effort
        self.json_mode_ok = os.environ.get("SWARM_LLM_JSON_MODE", "1") != "0"

    def describe(self) -> str:
        if self.backend == "mock":
            return "mock (no model)"
        if self.backend == "endpoint":
            return f"endpoint {self.base_url} model {self.model}"
        return f"prime-agent {self.prime_agent} model {self.model}"

    # ------------------------------------------------------------------ the call
    def call(self, stdin_text: str, task: str, system: str, *, json_mode=False, purpose=None, cwd=None,
             effort=None) -> dict:
        """One model call: {"text", "model", "in", "out"}. `stdin_text` is the material, `task` the instruction after
        it (prime-agent reads them as stdin and prompt; an endpoint gets them as one user message, material first).
        `json_mode` asks an endpoint for a JSON object (only where the reply is an object); `purpose` tells the mock
        which kind of text to write; `effort` overrides the reasoning effort sent to an endpoint for this call."""
        if self.backend == "mock":
            import swarm_mock
            text = swarm_mock.reply(system, stdin_text, task, purpose)
            return {"text": text, "model": MOCK_MODEL, "in": 0, "out": 0}
        if self.backend == "endpoint":
            return self._endpoint(stdin_text, task, system, json_mode, effort or self.reasoning_effort)
        return self._prime(stdin_text, task, system, cwd)

    def _endpoint(self, stdin_text, task, system, json_mode, effort=None) -> dict:
        user = f"{stdin_text}\n\n{task}" if stdin_text else task
        body = {"model": self.model, "messages": [{"role": "system", "content": system},
                                                  {"role": "user", "content": user}]}
        if effort:
            body["reasoning_effort"] = effort
        use_json = json_mode and self.json_mode_ok
        if use_json:
            body["response_format"] = {"type": "json_object"}
        key = os.environ.get("SWARM_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        url = self.base_url.rstrip("/") + "/chat/completions"
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                reply = json.load(r)
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:400]
            except OSError:
                pass
            if use_json and e.code == 400 and "response_format" in detail:
                self.json_mode_ok = False  # this server has no JSON mode: ask in words only from now on
                return self._endpoint(stdin_text, task, system, json_mode, effort)
            raise CallError(f"HTTP {e.code}: {detail}", e.code == 429 or e.code >= 500 or bool(RATE_RE.search(detail)))
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise CallError(f"{e.__class__.__name__}: {str(e)[:200]}", True)
        except ValueError as e:
            raise CallError(f"reply is not JSON: {e}")
        if isinstance(reply, dict) and reply.get("error"):
            err = str(reply["error"])[:400]
            raise CallError(f"error reply: {err}", bool(RATE_RE.search(err)))
        got = reply.get("model") if isinstance(reply, dict) else None
        if not model_matches(self.model, got):
            raise ModelMismatch(f"reply model {got!r}, expected {self.model!r}")
        try:
            msg = reply["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            raise CallError("reply has no choices[0].message")
        text = msg.get("content") or ""
        if isinstance(text, list):  # some servers send content parts
            text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
        text = text.strip()
        if not text:
            raise CallError(f"empty text (finish_reason {reply['choices'][0].get('finish_reason')!r})")
        usage = reply.get("usage") or {}
        return {"text": text, "model": got, "in": usage.get("prompt_tokens", 0) or 0,
                "out": usage.get("completion_tokens", 0) or 0}

    def _prime(self, stdin_text, task, system, cwd) -> dict:
        cmd = [self.prime_agent, "-p", "--mode", "json", "--json-event-profile", "factory-completed",
               "--provider", self.provider, "--model", self.model, "--thinking", self.thinking,
               "--no-tools", "--no-session", "-ns", "-np", "-nc",
               "--system-prompt", system, task]
        try:
            p = subprocess.run(cmd, input=stdin_text, capture_output=True, text=True, timeout=self.timeout, cwd=cwd)
        except subprocess.TimeoutExpired:
            raise CallError(f"timeout after {self.timeout}s")
        except OSError as e:
            raise CallError(f"cannot run prime-agent {self.prime_agent!r}: {e}")
        msg = None
        for line in p.stdout.splitlines():
            if '"agent_end"' not in line[:40]:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            for m in ev.get("messages", []):
                if m.get("role") == "assistant":
                    msg = m
        if msg is None:
            err = (p.stderr or p.stdout)[-600:]
            raise CallError(f"exit {p.returncode}, no assistant message: {err}", bool(RATE_RE.search(err)))
        if msg.get("stopReason") == "error" or msg.get("errorMessage"):
            err = str(msg.get("errorMessage") or "")[:600]
            raise CallError(f"stopReason error: {err}", bool(RATE_RE.search(err)))
        rm = msg.get("responseModel")
        if rm != self.model:
            raise ModelMismatch(f"responseModel={rm!r}, expected {self.model!r}")
        text = "\n".join(c.get("text", "") for c in msg.get("content", []) if c.get("type") == "text").strip()
        if not text:
            raise CallError("empty text")
        usage = msg.get("usage") or {}
        return {"text": text, "model": rm, "in": usage.get("input", 0) + usage.get("cacheRead", 0),
                "out": usage.get("output", 0)}


def from_args(a, required=True):
    """The Client the flags and the environment ask for; SystemExit with what to do when none is configured."""
    env = os.environ
    model = (a.llm_model if hasattr(a, "llm_model") else getattr(a, "model", None)) or env.get("SWARM_LLM_MODEL")
    common = {"provider": getattr(a, "provider", "cpa-r"), "thinking": getattr(a, "thinking", "low"),
              "timeout": getattr(a, "timeout", 300),
              "reasoning_effort": getattr(a, "reasoning_effort", None) or env.get("SWARM_LLM_REASONING_EFFORT")}
    if getattr(a, "mock", None):
        return Client("mock", MOCK_MODEL, **common)
    if getattr(a, "endpoint", None):
        return _endpoint_client(a.endpoint, model, common)
    if getattr(a, "prime_agent", None):
        return Client("prime", model or DEFAULT_MODEL, prime_agent=a.prime_agent, **common)
    if env.get("SWARM_LLM_MOCK", "") not in ("", "0"):
        return Client("mock", MOCK_MODEL, **common)
    if env.get("SWARM_LLM_BASE_URL"):
        return _endpoint_client(env["SWARM_LLM_BASE_URL"], model, common)
    if env.get("PRIME_AGENT"):  # our harness only when asked for by path; a prime-agent on PATH is never picked
        return Client("prime", model or DEFAULT_MODEL, prime_agent=env["PRIME_AGENT"], **common)
    if not required:
        return None
    raise SystemExit(NO_BACKEND)


NO_BACKEND = ("no model backend configured. Set one and rerun, either for a dry run with no model:\n"
              "  export SWARM_LLM_MOCK=1\n"
              "or for any OpenAI-compatible server:\n"
              "  export SWARM_LLM_BASE_URL=https://api.openai.com/v1 SWARM_LLM_MODEL=<model id> SWARM_LLM_API_KEY=<key>")


def _endpoint_client(url, model, common):
    if not model:
        raise SystemExit("--endpoint needs a model id: pass --model <id> or set SWARM_LLM_MODEL")
    if not re.match(r"^https?://", url):
        raise SystemExit(f"--endpoint must be an http(s) base URL such as https://api.openai.com/v1 (got {url!r})")
    return Client("endpoint", model, base_url=url, **common)
