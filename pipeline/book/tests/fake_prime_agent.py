#!/usr/bin/env python3
"""Stand-in for `prime-agent -p --mode json` in the book tests. FAKE_BOOK_MODE: ok | short | bad | wrapped | prose | boolidx | range | mismatch | stubborn | hookbad |
hookworse | stagebad | stageworse.
FAKE_BOOK_STATE: a dir that counts calls. Rephrase calls replace each word to remove with "stone" (`stubborn`:
unchanged). Adherence calls (items on stdin) get a JSON array: `short` drops one
item on the first call only, `bad` replies with broken JSON, `wrapped` with an object around the array, `prose`
with text around it, `boolidx` with boolean indices. Day and chapter calls in `short` get a reply too short and
uncited; `range` adds a [2-5] range. Day and chapter calls get a cited paragraph
with one invented marker [99]. Prints one agent_end event shaped like the real one."""
import json
import os
import sys

mode = os.environ.get("FAKE_BOOK_MODE", "ok")
state = os.environ.get("FAKE_BOOK_STATE")
stdin = sys.stdin.read()
task = sys.argv[-1]
calls = 0
if state:
    os.makedirs(state, exist_ok=True)
    with open(os.path.join(state, "calls"), "a") as f:
        f.write("1\n")
    with open(os.path.join(state, "calls")) as f:
        calls = sum(1 for _ in f)
    with open(os.path.join(state, "prompts.txt"), "a") as f:
        f.write(stdin[:2000] + ("\n...\n" + stdin[-1500:] if len(stdin) > 2000 else "") + "\n=====\n")

if "privacy filter" in task:  # rephrase: each removed word becomes "stone" (`stubborn` keeps the text as is)
    items = [json.loads(line) for line in stdin.splitlines() if line.strip()]
    out = []
    for x in items:
        t = x["text"]
        for w in x["remove"]:
            t = t if mode == "stubborn" else t.replace(w, "stone")
        out.append(t)
    text = json.dumps(out)
elif "JSON array" in task:
    items = [json.loads(line) for line in stdin.splitlines() if line.strip()]
    out = [{"i": x["i"], "score": ("on", "partly", "off", "idle")[x["i"] % 4], "note": "Worked on the fake goal."}
           for x in items]
    if mode == "short" and calls == 1:
        out = out[:-1]
    if mode == "boolidx":
        out = [dict(o, i=bool(o["i"])) for o in out]
    text = json.dumps(out)
    if mode == "bad":
        text = "[{broken"
    elif mode == "wrapped":
        text = json.dumps({"results": out})
    elif mode == "prose":
        text = "Here are the results: " + text + " Hope this helps."
elif "=== BEATS (number: text) ===" in stdin:  # stage: `stagebad` is wrong on a first ask and right on the retry,
    good = {"props": [{"kind": "Stall", "label": "\"moon bakery\""}, {"kind": "garden", "label": ""}],  # `stageworse`
            "moments": [{"beat": "2", "kind": "fail", "agents": ["Agent Alpha"], "label": "the oven."},  # always
                        {"beat": 0, "kind": "build", "agents": [], "label": None}]}
    bad = {"props": [{"kind": "rocket", "label": None}], "moments": [{"beat": 9, "kind": "win", "agents": [], "label": None}]}
    if mode == "stageworse" or (mode == "stagebad" and "not usable" not in task):
        text = json.dumps(bad)
    else:
        text = "```json\n" + json.dumps(good) + "\n```"
elif "=== PARAGRAPH ===" in stdin:  # hooks: `hookbad` is wrong on a first ask and right on the retry,
    good = {"hook": "Sixteen loaves of fake bread, and an oven that broke at the end",  # `hookworse` always
            "beats": [{"text": "Agent Alpha baked fake bread for the room [1].", "cites": [1]},
                      {"text": "The baking went on all week. [1]", "cites": [1]},
                      {"text": "The oven broke on the last day [1].", "cites": [1]}],
            "figure": None}
    bad = dict(good, hook="Why did the pretend oven break so soon?", figure={"value": "42", "label": "loaves", "cites": [1]},
               beats=good["beats"][:2] + [{"text": "The oven broke [7].", "cites": [7]}])
    if mode == "hookworse":
        text = json.dumps(dict(bad, beats=good["beats"]))
    elif mode == "hookbad" and "not usable" not in task:
        text = json.dumps(bad)
    else:
        text = "```json\n" + json.dumps(good) + "\n```"
elif "No summary of this day exists" in stdin:
    text = " ".join(["Agent Alpha tidied the pretend kitchen today [1]."] * 6) + " A human waved [2][99]."
elif "day-by-day paragraphs of this chapter" in stdin:
    if mode == "short":
        text = "TITLE: The Pretend Bakery Week\n\n" + " ".join(["Agent Alpha baked fake bread [1]."] * 8)
    else:
        text = ("TITLE: The Pretend Bakery Week\n\n" + " ".join(["Agent Alpha baked fake bread for the room [1]."] * 16)
                + " The oven broke on the last day [2][99].")
elif mode == "short":
    text = " ".join(["Agent Alpha planned the moon bakery with the others."] * 3)
else:
    text = (" ".join(["Agent Alpha planned the moon bakery with the others today [1]."] * 10)
            + " A human cheered [2][99]." + (" The oven came [2-5]." if mode == "range" else ""))
model = "gpt-5.5" if mode == "mismatch" else "gpt-5.6-luna"
msg = {"role": "assistant", "model": model, "responseModel": model, "stopReason": "stop",
       "usage": {"input": len(stdin) // 4, "output": 60, "cacheRead": 0},
       "content": [{"type": "text", "text": text}]}
print(json.dumps({"type": "agent_end", "messages": [{"role": "user", "content": []}, msg]}))
