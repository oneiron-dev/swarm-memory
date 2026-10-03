#!/usr/bin/env python3
"""Stand-in for `prime-agent -p --mode json --json-event-profile factory-completed`.

FAKE_LUNA_MODE: ok | nocite | mismatch | flaky | human | stubborn. FAKE_LUNA_STATE: a dir that
counts calls. An audit call (system prompt "You check a short text") reports "Pat Doe" when the
text contains it; mode human names Pat Doe until told not to, stubborn names Pat Doe always.
Prints one agent_end event shaped like the real one.
"""
import json
import os
import re
import sys

mode = os.environ.get("FAKE_LUNA_MODE", "ok")
state = os.environ.get("FAKE_LUNA_STATE")
stdin = sys.stdin.read()
task = sys.argv[-1]
system = sys.argv[sys.argv.index("--system-prompt") + 1]
audit = system.startswith("You check a short text")
calls = 0
if state:
    os.makedirs(state, exist_ok=True)
    path = os.path.join(state, "calls")
    with open(path, "a") as f:  # one line per call; O_APPEND keeps parallel calls apart
        f.write("1\n")
    with open(path) as f:
        calls = sum(1 for _ in f)
    with open(os.path.join(state, "tasks.txt"), "a") as f:
        f.write(task + "\n")

n = len(re.findall(r"^\[\d+\]", stdin, re.M))
model = "gpt-5.6-luna"
msg = {"role": "assistant", "api": "openai-responses", "provider": "cpa-r", "model": model,
       "responseModel": model, "responseModelSource": "provider-response",
       "usage": {"input": len(stdin) // 4 + 400, "output": 60, "cacheRead": 0, "cacheWrite": 0},
       "stopReason": "stop"}
if mode == "flaky" and calls % 2 == 1:
    msg.update(stopReason="error", errorMessage="429 Too Many Requests", content=[])
else:
    if audit:
        text = json.dumps({"names": ["Pat Doe"] if "Pat Doe" in stdin else []})
    elif mode == "nocite":
        text = "Agent Alpha worked on the puzzle page.\nIt asked for help."
    elif mode == "stubborn" or (mode == "human" and "named humans" not in task):
        text = "Agent Alpha thanked Pat Doe for the idea [1]. It shipped the page [2]."
    else:
        text = f"Agent Alpha planned the page. [1]\nIt finished the work [{n}, 999] and asked for help [2-{min(n, 3)}]."
    if mode == "mismatch":
        msg["responseModel"] = "gpt-5.5"
    msg["content"] = [{"type": "thinking", "thinking": "x"}, {"type": "text", "text": text}]
print(json.dumps({"type": "session", "id": "fake"}))
print(json.dumps({"type": "agent_end", "messages": [{"role": "user", "content": []}, msg]}))
