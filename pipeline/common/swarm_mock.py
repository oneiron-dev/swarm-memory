"""The mock model: templated, cited replies built from a prompt's own material, without any model.

It exists so the whole pipeline runs end to end with no key (scripts/smoke.sh): every reply passes the same
acceptance rules a real model's reply must pass (word bounds, citation numbers from the material, JSON shapes), so
the bundle, the book and the questions page fill with text that says plainly it is a placeholder.

The mock never copies source text. It writes counts, times, agent names and line numbers only, so it cannot carry a
human's name into the bundle. The steps tell it what to write with `purpose`; without one it guesses from the prompt.
"""
from __future__ import annotations

import collections
import json
import re

MARK_RE = re.compile(r"\[(\d+)\]")
LINE_RE = re.compile(r"^\[(\d+)\] (\S+) (.+?) \((#?[\w-]+)\): ", re.M)
STOP = frozenset("a an and the of to for in on at by with from is are was were be this that it its their our".split())


def reply(system: str, stdin_text: str, task: str, purpose: str | None = None) -> str:
    if not (stdin_text or "").strip():  # the whole prompt came as the task (make_questions.py)
        stdin_text = task or ""
    purpose = purpose or guess(system, stdin_text, task)
    fn = {"summary": summary, "audit": audit, "day": day, "gap": gap, "chapter": chapter, "hooks": hooks,
          "pair": pair, "stage": stage, "adherence": adherence, "rephrase": rephrase, "classify": classify,
          "questions-chapter": questions_chapter, "questions-year": questions_year}.get(purpose)
    if fn is None:
        raise ValueError(f"the mock has no template for purpose {purpose!r}")
    return fn(system, stdin_text, task)


def guess(system, stdin_text, task) -> str:
    s = system or ""
    if s.startswith("You check a short text"):
        return "audit"
    if s.startswith("You write short factual summaries"):
        return "summary"
    if "privacy filter" in (task or ""):
        return "rephrase"
    if "JSON array" in (task or ""):
        return "adherence"
    if "named-entity surfaces" in s:
        return "classify"
    if "=== BEATS (number: text) ===" in stdin_text:
        return "stage"
    if "=== PARAGRAPH ===" in stdin_text:
        return "hooks"
    if "=== CHAT LINES ===" in stdin_text:
        return "pair"
    if "No summary of this day exists" in stdin_text:
        return "gap"
    if "day-by-day paragraphs of this chapter" in stdin_text:
        return "chapter"
    if "=== CHAPTER ANSWERS ===" in stdin_text:
        return "questions-year"
    if "Answer the eight questions" in stdin_text:
        return "questions-chapter"
    return "day"


# ---------------------------------------------------------------- helpers

def words(text: str) -> int:
    return len(MARK_RE.sub("", text).split())


def numbers_in(text: str) -> list:
    """The [n] numbers of a text, each once, in order of first use."""
    return list(dict.fromkeys(int(n) for n in MARK_RE.findall(text or "")))


def after(text: str, head: str) -> str:
    i = text.find(head)
    return text[i + len(head):] if i >= 0 else text


def mark(ns: list, k: int, width: int = 1) -> str:
    """Markers for sentence k: `width` numbers taken round-robin from ns ('' when ns is empty)."""
    if not ns:
        return ""
    return "".join(f"[{ns[(k * width + j) % len(ns)]}]" for j in range(min(width, len(ns))))


def sentence(body: str, ns: list, k: int) -> str:
    m = mark(ns, k)
    return f"{body} {m}." if m else f"{body}."


def fit(sentences: list, pads: list, lo: int, hi: int, ns: list, start: int = 0) -> str:
    """Sentences (already marked) plus pad sentences until the text has lo..hi words; drops from the end when over."""
    out = list(sentences)
    k = start
    i = 0
    while words(" ".join(out)) < lo:
        out.append(sentence(pads[i % len(pads)], ns, k))
        i += 1
        k += 1
    while words(" ".join(out)) > hi and len(out) > 1:
        out.pop(len(out) - 1 if len(out) > 2 else 0)
    return " ".join(out)


def json_lines(text: str) -> list:
    """The JSON-object lines of a text (the item lines a step sends; prose around them is skipped)."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                x = json.loads(line)
            except ValueError:
                continue
            if isinstance(x, dict):
                out.append(x)
    return out


def plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def parse_lines(stdin_text: str) -> list:
    """(n, hh:mm, speaker, kind) of the numbered source lines '[n] HH:MM speaker (kind): text'."""
    return [(int(m.group(1)), m.group(2), m.group(3), m.group(4).lstrip("#")) for m in LINE_RE.finditer(stdin_text)]


# ---------------------------------------------------------------- the summarizer

KIND_SENTENCES = {
    "chat": "{s} wrote {n} in the group chat between {t0} and {t1} UTC",
    "session_goal": "{s} set {n} for its computer work",
    "session_summary": "{s} wrote {n} after its computer work",
    "next_goal": "{s} set {n} for the sessions that followed",
    "history_query": "{s} searched its own history {n}",
    "outreach": "{s} handled {n}",
    "cc_text": "{s} wrote {n} while working in a coding tool",
    "memory": "{s} saved its notes at the end of the day",
}
KIND_NOUNS = {"chat": ("message", None), "session_goal": ("session goal", None),
              "session_summary": ("session summary", "session summaries"), "next_goal": ("next goal", None),
              "history_query": ("time", None), "outreach": ("outreach request", None), "cc_text": ("note", None),
              "memory": ("memory", "memories")}


def summary(system, stdin_text, task):
    lines = parse_lines(stdin_text)
    m = re.search(r"for the agent (.+?), from the lines", task or "")
    room = re.search(r"in the chat room #(\S+?), from", task or "")
    subject = m.group(1) if m else "The room"
    by_kind = collections.defaultdict(list)
    for n, hhmm, who, kind in lines:
        by_kind[kind].append((n, hhmm, who))
    out = []
    if room:
        chat = by_kind.get("chat", [])
        speakers = collections.Counter(who for _, _, who in chat)
        if chat:
            out.append(f"{plural(len(chat), 'message')} were posted in #{room.group(1)} between {chat[0][1]} and "
                       f"{chat[-1][1]} UTC [{chat[0][0]}][{chat[-1][0]}].")
        for who, k in speakers.most_common(3):
            first = next(n for n, _, w in chat if w == who)
            name = "A human" if who == "human" else who
            out.append(f"{name} wrote {plural(k, 'of them', 'of them')} [{first}].".replace("1 of them", "one of them"))
    else:
        for kind, rows in by_kind.items():
            tpl = KIND_SENTENCES.get(kind)
            if not tpl:
                continue
            one, many = KIND_NOUNS[kind]
            body = tpl.format(s=subject, n=plural(len(rows), one, many), t0=rows[0][1], t1=rows[-1][1])
            cites = f"[{rows[0][0]}]" + (f"[{rows[-1][0]}]" if len(rows) > 1 else "")
            out.append(f"{body} {cites}.")
    first = lines[0][0] if lines else None
    tail = "Placeholder text, written without a model, from the numbered source lines"  # no person-like phrase:
    # the tagger would read one as a person and the builder would cut it to [human]
    out.append(f"{tail} [{first}]." if first else f"{tail}.")
    while len(out) < 3:
        out.insert(0, f"{subject} appears in {plural(len(lines), 'source line')} for this day"
                      + (f" [{first}]." if first else "."))
    return " ".join(out[:7])


def audit(system, stdin_text, task):
    return json.dumps({"names": []})


# ---------------------------------------------------------------- the book

def day(system, stdin_text, task):
    d = re.search(r"This is day (\d+) \(([^)]*)\)", stdin_text)
    counts = re.search(r"Counts: (\d+) chat messages, (\d+) computer sessions, (\d+) memory saves\. "
                       r"Active agents: (.*?)\.\n", stdin_text)
    material = after(stdin_text, "=== MATERIAL ===")
    ns = numbers_in(material)
    agents = [a.strip() for a in counts.group(4).split(",")] if counts and counts.group(4) != "none" else []
    out = []
    num = d.group(1) if d else "this"
    if counts:
        out.append(sentence(f"On day {num} the village logged {plural(int(counts.group(1)), 'chat message')}, "
                            f"{plural(int(counts.group(2)), 'computer session')} and "
                            f"{plural(int(counts.group(3)), 'memory save')}", ns, 0))
    if agents:
        shown = agents[:5]
        rest = f" and {len(agents) - 5} more" if len(agents) > 5 else ""
        out.append(sentence(f"The active agents were {', '.join(shown)}{rest}", ns, 1))
    for k, line in enumerate(re.findall(r"^([^:\n]{2,40}): .*\[\d+\]", material, re.M)[:4], 2):
        if line.startswith(("Room #", "Counts", "Active")):
            continue
        out.append(sentence(f"{line} has a summary of its own day in the record", ns, k))
    pads = ["Each sentence here points to a numbered line of the day's material, so each source line can be opened",
            "A model would write this paragraph from the agent and room summaries; the mock only counts them",
            "The goal of the week stayed the same through the day",
            "Nothing in this placeholder is a reading of the record"]
    return fit(out, pads, 85, 125, ns, start=6)


def gap(system, stdin_text, task):
    d = re.search(r"This is day (\d+) \(([^)]*)\)", stdin_text)
    lines = parse_lines(after(stdin_text, "=== MATERIAL ==="))
    ns = [n for n, *_ in lines]
    kinds = collections.Counter(k for *_, k in lines)
    out = [sentence(f"Day {d.group(1) if d else 'this'} has no summary, only {plural(len(lines), 'log line')}", ns, 0)]
    for k, (kind, c) in enumerate(kinds.most_common(3), 1):
        out.append(sentence(f"Of those, {c} came from {kind.replace('_', ' ')} rows", ns, k))
    pads = ["The mock counts the lines and cites them without reading them",
            "A model would tell the day from these lines"]
    return fit(out, pads, 45, 85, ns, start=4)


def chapter(system, stdin_text, task):
    c = re.search(r"chapter (\d+) of (\d+)\. It covers days (\d+) to (\d+)", stdin_text)
    material = after(stdin_text, "=== MATERIAL ===")
    ns = numbers_in(material)
    days = re.findall(r"^Day (\d+) \(", material, re.M)
    first, last = (c.group(3), c.group(4)) if c else ("?", "?")
    title = f"Days {first} to {last}" if first != last else f"Day {first} on its own"
    out = [sentence(f"This chapter covers days {first} to {last}, under one goal of the village", ns, 0)]
    step = max(1, len(days) // 6)
    for k, dd in enumerate(days[::step][:6], 1):
        out.append(sentence(f"Day {dd} has its own paragraph in the record", ns, k))
    pads = ["The mock joins the day paragraphs into one placeholder story and keeps their citation numbers",
            "A model would tell how the week started, what was tried, what changed and how it ended",
            "Every number in brackets opens a source line to check",
            "Nothing in this placeholder is a reading of the record"]
    body = fit(out, pads, 125, 175, ns, start=8)
    return f"TITLE: {title}\n\n{body}"


def hooks(system, stdin_text, task):
    days = re.findall(r"^\[(\d+)\] day (\d+)$", after(stdin_text, "=== DAY OF EACH CITATION NUMBER ==="), re.M)
    ns = [int(n) for n, _ in days][:4]
    beats = [{"text": f"The record cites this part of the week here [{n}].", "cites": [n]} for n in ns]
    while beats and len(beats) < 3:
        n = ns[len(beats) % len(ns)]
        beats.append({"text": f"The week went on, and the record cites it again [{n}].", "cites": [n]})
    return json.dumps({"hook": "A placeholder week, told only from its own numbered source lines",
                       "beats": beats, "figure": None})


def pair(system, stdin_text, task):
    names = re.search(r'Name the two as "(.+?)" and "(.+?)"', stdin_text)
    a, b = (names.group(1), names.group(2)) if names else ("one agent", "another")
    lines = parse_lines(after(stdin_text, "=== CHAT LINES ==="))
    text = f"{a} and {b} exchanged {len(lines)} chat messages about the shared work that day."
    if len(text.split()) > 16:
        text = f"{a} and {b} exchanged chat messages that day."
    while len(text.split()) < 8:
        text = text[:-1] + " in the room."
    cites = [n for n, *_ in lines][:1] + ([lines[-1][0]] if len(lines) > 1 else [])
    return json.dumps({"text": text, "cites": cites or [1]})


def stage(system, stdin_text, task):
    beats = re.findall(r"^(\d+): ", after(stdin_text, "=== BEATS (number: text) ==="), re.M)
    moments = [{"beat": 0, "kind": "gather", "agents": [], "label": None}] if beats else []
    return json.dumps({"props": [{"kind": "scaffold", "label": None}], "moments": moments})


def adherence(system, stdin_text, task):
    out = []
    for x in json_lines(stdin_text):
        goal = {w for w in re.findall(r"[a-z]{3,}", (x.get("goal") or "").lower()) if w not in STOP}
        summ = (x.get("summary") or "").lower()
        hits = sum(1 for w in goal if w in summ)
        score = "idle" if len(summ.split()) < 6 else "on" if hits >= 2 else "partly" if hits == 1 else "off"
        out.append({"i": x["i"], "score": score, "note": "Mock score from word overlap with the goal, no model."})
    return json.dumps(out)


def rephrase(system, stdin_text, task):
    out = []
    for x in json_lines(stdin_text):
        t = x["text"]
        for w in x.get("remove") or []:
            t = re.sub(rf"(?<!\w){re.escape(w)}(?!\w)", "someone", t)
        out.append(t)
    return json.dumps(out)


def classify(system, stdin_text, task):
    items = json_lines(stdin_text)
    hits = [{"i": x["i"], "why": "mock: tagged as a person"} for x in items
            if str(x.get("label", "")).upper() in ("PERSON", "PRIVATE_PERSON")]
    return json.dumps({"n_items": len(items), "human_individual": hits})


# ---------------------------------------------------------------- the questions

def questions_chapter(system, stdin_text, task):
    qids = re.findall(r"^\d+\. (\w+): ", stdin_text, re.M)
    ns = numbers_in(after(stdin_text, "=== MATERIAL ==="))
    answers = {}
    for k, q in enumerate(qids):
        if not ns:
            answers[q] = {"text": "nothing recorded", "cites": []}
            continue
        pick = [ns[(k * 2 + j) % len(ns)] for j in range(min(2, len(ns)))]
        answers[q] = {"text": f"Placeholder answer from the mock: the chapter material holds {len(ns)} cited "
                              f"sentences, and a model would read them for this question.",
                      "cites": list(dict.fromkeys(pick))}
    return json.dumps({"answers": answers})


def questions_year(system, stdin_text, task):
    pairs = []
    for ch, nums in re.findall(r"^Chapter (\d+) \(.*\{([\d, ]+)\}$", after(stdin_text, "=== CHAPTER ANSWERS ==="),
                               re.M):
        for n in nums.split(","):
            if n.strip().isdigit():
                pairs.append([int(ch), int(n)])
    return json.dumps({"text": f"Placeholder answer from the mock: {plural(len(pairs), 'cited chapter answer')} "
                               f"carry this question, and a model would compare them across the chapters.",
                       "cites": pairs[:8]})
