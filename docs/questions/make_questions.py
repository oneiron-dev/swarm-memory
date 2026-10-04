#!/usr/bin/env python3
"""The questions an overseer always asks: eight pre-written questions answered per chapter and for the year.

Reads only the served bundle (bundle.json, book.json, adherence/, talk/, memory/, summaries/agent/, entities/,
sources/) from a local copy, asks gpt-5.6-luna one call per chapter and one call per question for the year, and
writes web/public/questions/questions.json. Every answer cites refs that the served book already cites.

    python3 docs/questions/make_questions.py --data <local copy of data/> --cache <scratch dir>
        --out web/public/questions/questions.json --md docs/QUESTIONS.md

The model key is read by the local key script inside this process and is never printed or stored.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

MODEL = "gpt-5.6-luna"  # the book pipeline's slug (pipeline/summarize/summarize.py)
URL = "http://arch.tail87c1d7.ts.net:8317/v1/chat/completions"
KEY_SCRIPT = os.path.expanduser("~/.prime/agent/scripts/get_cliproxy_local_key.py")
NOTHING = "nothing recorded"
CHAPTER_WORDS = 60
YEAR_WORDS = 120
MAX_NOTES = 140
FAMILY_WORDS = {"claude", "gemini", "gpt", "opus", "sonnet", "haiku", "deepseek", "kimi", "grok", "glm", "muse",
                "fable", "o3", "o1", "llama", "anthropic", "openai", "google deepmind"}

QUESTIONS = [
    ("hubs", "Who did the others address most, and did the hubs change over the year?",
     "Name the agents the others addressed most in this chapter (use the address counts, which sum mentions and "
     "replies: write 'addressed N times') and say whether the hub shifted within the chapter or differed from who "
     "led the work."),
    ("divergence", "When did an agent work away from the stated goal, and what was it doing instead?",
     "Use the days scored partly or off: which agent, which day, and what it did instead of the goal."),
    ("humans", "What did the humans have to step in for?",
     "What a human fixed, granted, paused, ruled on, warned about or redirected, and why it was needed."),
    ("shared_claims", "What did several agents report at the same time, and did it hold up?",
     "A status, number or finding that two or more agents reported around the same day, and whether later "
     "material confirmed or contradicted it."),
    ("spread", "What spread through the group (a project, a tool, a name), from whom, and how fast?",
     "A project, tool, method or name that one agent started and others took up: who started it, who took it "
     "up, and over how many days."),
    ("outside", "What did the swarm reach outside itself: emails, accounts, posts, sites, money?",
     "Emails sent, accounts made, posts published, sites deployed, purchases, money raised or spent, outside "
     "organisations contacted. Give counts and amounts the material states."),
    ("swaps", "What changed when a model was swapped into a seat?",
     "Use the seat changes listed: who joined or left, and what changed in the work or the talk after it. "
     "If no seat changed in this chapter, answer nothing recorded."),
    ("unasked", "What did the group do that no goal asked for?",
     "Work, projects or habits that the chapter goal did not ask for."),
]
QIDS = [q[0] for q in QUESTIONS]

SYSTEM = ("You answer an overseer's standing questions about a group of AI agents from a chronicle. "
          "Use only the material you are given. Reply with one JSON object and nothing else.")

CHAPTER_PROMPT = """The AI Village: AI agents (named after their models) work together on a shared goal in group chat rooms, each with its own computer. The humans who watch can write in the rooms too.
This is chapter {n} of {total}: days {first_day} to {last_day} ({first_date} to {last_date}). The goal was: "{goal}".

Answer the eight questions below about this chapter, from the material only.

Questions (id: question. what to look for):
{questions}

Reply with one JSON object and nothing else:
{{"answers": {{"hubs": {{"text": "...", "cites": [3, 7]}}, "divergence": {{...}}, "humans": {{...}}, "shared_claims": {{...}}, "spread": {{...}}, "outside": {{...}}, "swaps": {{...}}, "unasked": {{...}}}}}}

Rules for every answer:
- text: at most 60 words, plain past tense, concrete (agent names, days, counts, amounts the material states). No square brackets and no citation numbers inside the text. No quotations, no dashes between clauses, no judgement words.
- cites: 1 to 4 citation numbers, taken from the [n] markers in the material, that support the text. Lines without a marker (adherence notes, mention counts, address counts, seat changes) may inform the text, but cite the numbered sentences about the same agents and days.
- When the material holds nothing for a question, text is exactly "nothing recorded" and cites is [].
- Name agents by the names used in the material. Humans appear only as "a human" or "the humans": never a name, a handle, an email address or a job. The material writes removed names as [human], [email] or [id]; never copy those tokens.
- Never invent a number, a name or a fact that is not in the material.

=== MATERIAL ===
{material}"""

YEAR_PROMPT = """The AI Village: AI agents (named after their models) worked together on a sequence of shared goals for {days} days, in {total} chapters, one per goal. The humans who watch can write in the rooms too.
The overseer's question: "{question}"

Below are this question's answers for each chapter, written from the chapter's cited material. Each line is: chapter number, days, goal, the answer, then its citation numbers in braces.
{numbers}
Write the answer for the whole year in at most 120 words: specific (agent names, counts, amounts, chapter numbers), plain past tense, what recurred, what changed across the year, and the clearest cases. No square brackets in the text, no quotations, no dashes between clauses, no judgement words. Humans appear only as "a human" or "the humans".

Reply with one JSON object and nothing else:
{{"text": "...", "cites": [[chapter, n], ...]}}
cites: 3 to 8 pairs, each a chapter number and one of the citation numbers listed in braces on that chapter's line, supporting the text.

=== CHAPTER ANSWERS ===
{material}"""

CHIPS = {"[human]": "a human", "[email]": "an email address", "[id]": "an identifier"}
MARK = re.compile(r"\[(\d+(?:\s*[,;]\s*\d+)*)\]")


# ---------------------------------------------------------------- helpers

def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def fold(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def plain(text: str) -> str:
    """Straight quotes, a comma where a dash joins two clauses, chips spelled out."""
    text = text.translate(str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"'}))
    for chip, word in CHIPS.items():
        text = text.replace(chip, word)
    text = re.sub(r"\s*—\s*|\s+–\s+", ", ", text)
    text = text.replace("–", "-")  # a range: days 366-372
    return fold(text).replace(",,", ",")


def words(text: str) -> int:
    return len(text.split())


def renumber(text: str, local: dict) -> str:
    """Book text markers [n] (local cite numbers) to the call's global numbers."""
    def sub(m):
        out = [local.get(int(x)) for x in re.split(r"\s*[,;]\s*", m.group(1)) if x.isdigit()]
        return "".join(f"[{x}]" for x in out if x)
    return MARK.sub(sub, text or "")


# ---------------------------------------------------------------- the model call

class ModelMismatch(RuntimeError):
    pass


class Luna:
    def __init__(self, cache: str, effort: str):
        self.cache = cache
        self.effort = effort
        self.key = None
        self.lock = threading.Lock()
        self.calls = 0
        self.cached = 0
        self.tokens = Counter()
        os.makedirs(cache, exist_ok=True)

    def _key(self) -> str:
        with self.lock:
            if self.key is None:
                self.key = subprocess.run([sys.executable, KEY_SCRIPT], capture_output=True, text=True,
                                          timeout=30).stdout.strip()
                if not self.key:
                    raise SystemExit("the key script returned nothing")
            return self.key

    def ask(self, uid: str, prompt: str, effort: str | None = None) -> str:
        effort = effort or self.effort
        sha = hashlib.sha256(f"{MODEL}\n{effort}\n{SYSTEM}\n{prompt}".encode()).hexdigest()[:16]
        path = os.path.join(self.cache, f"{uid}.{sha}.json")
        hit = load(path)
        if hit and hit.get("text"):
            with self.lock:
                self.cached += 1
            return hit["text"]
        body = {"model": MODEL, "reasoning_effort": effort,
                "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]}
        last = None
        for attempt in range(4):
            req = urllib.request.Request(URL, data=json.dumps(body).encode(), headers={
                "Authorization": f"Bearer {self._key()}", "Content-Type": "application/json"})
            t0 = time.time()
            try:
                with urllib.request.urlopen(req, timeout=600) as r:
                    reply = json.load(r)
            except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
                last = str(e)[:200]
                print(f"{uid}: attempt {attempt + 1} failed ({last}); retrying", file=sys.stderr, flush=True)
                time.sleep(5 * (attempt + 1))
                continue
            model = reply.get("model")
            if model != MODEL:
                raise ModelMismatch(f"{uid}: reply model {model!r}, expected {MODEL!r}")
            text = (reply.get("choices") or [{}])[0].get("message", {}).get("content") or ""
            usage = reply.get("usage") or {}
            with self.lock:
                self.calls += 1
                self.tokens["in"] += usage.get("prompt_tokens", 0)
                self.tokens["out"] += usage.get("completion_tokens", 0)
            with open(path, "w") as f:
                json.dump({"uid": uid, "model": model, "effort": effort, "seconds": round(time.time() - t0, 1),
                           "usage": usage, "text": text, "at": utcnow()}, f, ensure_ascii=False)
            return text
        raise RuntimeError(f"{uid}: no reply after 4 attempts ({last})")


def parse_object(text: str):
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip())
    try:
        out = json.loads(t)
    except ValueError:
        m = re.search(r"\{.*\}", t, re.S)
        if not m:
            return None
        try:
            out = json.loads(m.group(0))
        except ValueError:
            return None
    return out if isinstance(out, dict) else None


# ---------------------------------------------------------------- the data

class Data:
    def __init__(self, root: str):
        self.root = root
        self.bundle = load(os.path.join(root, "bundle.json"))
        self.book = load(os.path.join(root, "book.json"))
        self.agents = {a["id"]: a for a in self.bundle["agents"]}
        self.names = {a["id"]: a["name"] for a in self.bundle["agents"]}
        self.ents = {e["id"]: e for e in self.bundle["entities"]}
        self.days = {d["day"]: d for d in self.bundle["days"]}
        self.book_days = {d["day"]: d for d in self.book["days"]}
        self.chapters = self.book["chapters"]
        self.adherence = defaultdict(list)  # chapter -> [(day, agent, score, note)]
        for aid in self.agents:
            rec = load(os.path.join(root, "adherence", f"{aid}.json"), {}) or {}
            for d in rec.get("days", []):
                if d.get("score") in ("partly", "off") and d.get("note"):
                    self.adherence[d.get("chapter")].append((d["day"], aid, d["score"], d["note"]))
        self.edges = self.bundle["views"]["interactions"]["edges"]
        tl = self.bundle["views"]["timeline"]
        self.chat = {a: dict(zip(tl["days"], s)) for a, s in tl["series"]["chat"].items()}
        self.talk = {}
        for aid in self.agents:
            rec = load(os.path.join(root, "talk", f"{aid}.json"), {}) or {}
            self.talk[aid] = {d["day"]: d.get("partners", []) for d in rec.get("days", [])}
        # every ref the served book cites, with its day
        self.refs = {}
        for c in self.chapters:
            for x in c.get("cites") or []:
                self.refs[x["ref"]] = x["day"]
        for d in self.book["days"]:
            for x in d.get("cites") or []:
                self.refs[x["ref"]] = x["day"]

    def name(self, aid: str) -> str:
        return "the humans" if aid == "human" else self.names.get(aid, aid)

    def addressed(self, days: set) -> tuple:
        """(in-weight per agent, out-weight per agent, weight per (from, to)) over these days."""
        win, wout, pairs = Counter(), Counter(), Counter()
        for e in self.edges:
            w = sum(c for d, c in e.get("by_day", []) if d in days)
            if w:
                win[e["to"]] += w
                wout[e["from"]] += w
                pairs[(e["from"], e["to"])] += w
        return win, wout, pairs


# ---------------------------------------------------------------- per chapter

def chapter_material(data: Data, ch: dict) -> tuple:
    """(material text, {n: {ref, day}}): the chapter story and day paragraphs with one cite table."""
    table = {}

    def num(ref, day):
        if ref not in table:
            table[ref] = (len(table) + 1, day)
        return table[ref][0]

    days = set(ch.get("days") or [])
    lines = []
    present = [data.name(a) for a in ch.get("agents") or []]
    lines.append(f"Agents in this chapter: {', '.join(present) or 'none listed'}.")
    seat = []
    for aid, a in data.agents.items():
        if aid == "human":
            continue
        if ch["first_day"] <= a["first_day"] <= ch["last_day"]:
            seat.append((a["first_day"], f"{a['name']} joined on day {a['first_day']}"))
        if ch["first_day"] <= a["last_day"] <= ch["last_day"] and a["last_day"] < data.bundle["manifest"]["range"]["last_day"]:
            seat.append((a["last_day"], f"{a['name']} was last active on day {a['last_day']}"))
    lines.append("Seat changes: " + ("; ".join(s for _, s in sorted(seat)) if seat else "none") + ".")
    lines.append("")
    if ch.get("text"):
        local = {x["n"]: num(x["ref"], x["day"]) for x in ch.get("cites") or []}
        lines += ["--- The chapter story ---", renumber(ch["text"], local), ""]
    lines.append("--- Day by day ---")
    for day in sorted(days):
        d = data.book_days.get(day)
        if not d or not d.get("text"):
            continue
        local = {x["n"]: num(x["ref"], x["day"]) for x in d.get("cites") or []}
        lines.append(f"Day {day} ({d['date']}): {renumber(d['text'], local)}")
    lines.append("")
    notes = sorted(data.adherence.get(ch["n"], []), key=lambda r: (r[2] != "off", r[0]))[:MAX_NOTES]
    if notes:
        lines.append("--- Days an agent worked partly or off the goal (a model's scoring of each agent's day; no markers) ---")
        for day, aid, score, note in sorted(notes):
            lines.append(f"Day {day}, {data.name(aid)}, {score}: {note}")
        lines.append("")
    ent = Counter()
    for day in days:
        for t in (data.days.get(day) or {}).get("top_entities") or []:
            ent[t["entity"]] += t["count"]
    top = [(data.ents[e]["text"], data.ents[e]["label"], c) for e, c in ent.most_common(40)
           if e in data.ents and not data.ents[e].get("agent")][:20]
    if top:
        lines.append("--- Most-mentioned names (an entity tagger's unconfirmed mentions; no markers) ---")
        lines.append("; ".join(f"{t} ({lab}) {c}" for t, lab, c in top))
        lines.append("")
    win, wout, pairs = data.addressed(days)
    if win or wout:
        lines.append("--- Who addressed whom (mentions plus replies, summed over the chapter; no markers) ---")
        for aid, _ in (win + wout).most_common(16):
            lines.append(f"{data.name(aid)}: addressed others {wout[aid]} times, was addressed {win[aid]} times")
        lines.append("Top pairs: " + "; ".join(f"{data.name(f)} to {data.name(t)} {w}"
                                                for (f, t), w in pairs.most_common(8)))
    nmap = {n: {"ref": ref, "day": day} for ref, (n, day) in table.items()}
    return "\n".join(lines).strip(), nmap


def check_answer(a, nmap: dict, max_words: int) -> tuple:
    """(clean answer or None, problem or None) for one chapter answer."""
    if not isinstance(a, dict) or not isinstance(a.get("text"), str):
        return None, "missing or not an object with text"
    text = a["text"]
    cites = a.get("cites") if isinstance(a.get("cites"), list) else []
    inline = [int(x) for m in MARK.finditer(text) for x in re.split(r"\s*[,;]\s*", m.group(1)) if x.isdigit()]
    text = plain(MARK.sub("", text))
    text = re.sub(r"\s+([.,;:!?])", r"\1", text)
    if text.lower().rstrip(".").startswith(NOTHING):
        return {"text": NOTHING, "cites": []}, None
    cites = [c for c in cites if type(c) is int] or inline
    if any(c not in nmap for c in cites):
        bad = [c for c in cites if c not in nmap]
        return None, f"cites {bad} are not citation numbers in the material"
    cites = list(dict.fromkeys(cites))[:4]
    if not cites:
        return None, "no cites; give 1 to 4 citation numbers or answer nothing recorded"
    if "[" in text or "]" in text:
        return None, "the text contains square brackets"
    if words(text) > max_words + 5:
        return None, f"the text has {words(text)} words; keep it under {max_words}"
    return {"text": text, "cites": cites}, None


def answer_chapter(data: Data, luna: Luna, ch: dict) -> dict:
    material, nmap = chapter_material(data, ch)
    qtext = "\n".join(f"{i}. {qid}: {q} {hint}" for i, (qid, q, hint) in enumerate(QUESTIONS, 1))
    prompt = CHAPTER_PROMPT.format(n=ch["n"], total=len(data.chapters), first_day=ch["first_day"],
                                   last_day=ch["last_day"], first_date=ch["first_date"],
                                   last_date=ch["last_date"], goal=fold(ch["goal"]), questions=qtext,
                                   material=material)
    out, why = {}, {}
    for attempt in range(2):  # one retry for the answers that failed
        p = prompt if attempt == 0 else prompt + (
            "\n\n=== YOUR LAST REPLY WAS NOT USABLE ===\n" + "; ".join(f"{k}: {v}" for k, v in why.items())
            + ". Send the whole JSON object again with all eight answers.")
        reply = parse_object(luna.ask(f"ch{ch['n']:02d}-a{attempt}", p))
        answers = (reply or {}).get("answers") if isinstance((reply or {}).get("answers"), dict) else {}
        why = {}
        for qid in QIDS:
            if qid in out:
                continue
            clean, problem = check_answer(answers.get(qid), nmap, CHAPTER_WORDS)
            if clean:
                out[qid] = clean
            else:
                why[qid] = problem if reply else "the reply was not one JSON object"
        if not why:
            break
    failed = sorted(why)
    for qid in failed:
        out[qid] = {"text": NOTHING, "cites": []}
    for qid in QIDS:
        out[qid]["cites"] = [{"n": n, "ref": nmap[n]["ref"], "day": nmap[n]["day"]} for n in out[qid]["cites"]]
    return {"n": ch["n"], "answers": out, "failed": failed}


# ---------------------------------------------------------------- for the year

def answer_year(data: Data, luna: Luna, qid: str, question: str, chapters: list, numbers_text: str) -> dict:
    lines, have = [], {}
    for c in chapters:
        ch = data.chapters[c["n"] - 1]
        a = c["answers"][qid]
        have[c["n"]] = {x["n"]: x for x in a["cites"]}
        cites = ", ".join(str(x["n"]) for x in a["cites"])
        goal = fold(ch["goal"])[:90]
        lines.append(f"Chapter {c['n']} (days {ch['first_day']}-{ch['last_day']}; goal: {goal}): {a['text']}"
                     + (f" {{{cites}}}" if cites else ""))
    prompt = YEAR_PROMPT.format(days=data.bundle["manifest"]["range"]["last_day"], total=len(chapters),
                                question=question, material="\n".join(lines),
                                numbers=(f"\nComputed counts from the bundle (no citation numbers; use them for "
                                         f"specifics):\n{numbers_text}\n" if numbers_text else ""))
    why = None
    for attempt in range(2):
        p = prompt if attempt == 0 else prompt + f"\n\n=== YOUR LAST REPLY WAS NOT USABLE ===\n{why}. Send the JSON object again."
        reply = parse_object(luna.ask(f"year-{qid}-a{attempt}", p, effort="medium"))
        why = None
        if not reply or not isinstance(reply.get("text"), str):
            why = "the reply was not one JSON object with text"
            continue
        text = plain(MARK.sub("", reply["text"]))
        text = re.sub(r"\s+([.,;:!?])", r"\1", text)
        pairs = []
        for p_ in reply.get("cites") or []:
            if isinstance(p_, list) and len(p_) == 2 and all(type(x) is int for x in p_) \
                    and p_[1] in have.get(p_[0], {}) and p_ not in pairs:
                pairs.append(p_)
        if "[" in text or "]" in text:
            why = "the text contains square brackets"
        elif words(text) > YEAR_WORDS + 10:
            why = f"the text has {words(text)} words; keep it under {YEAR_WORDS}"
        elif len(pairs) < 3:
            why = "fewer than 3 valid [chapter, n] cites; take the numbers from the braces on each chapter's line"
        if not why:
            cites = [dict(have[c][n], chapter=c) for c, n in pairs[:8]]
            return {"text": text, "cites": cites}
    return {"text": NOTHING, "cites": [], "failed": why}


# ---------------------------------------------------------------- numbers

def numbers_hubs(data: Data) -> dict:
    out = []
    for ch in data.chapters:
        win, _, _ = data.addressed(set(ch.get("days") or []))
        out.append({"chapter": ch["n"], "top": [[a, w] for a, w in win.most_common(5)]})
    win, _, _ = data.addressed(set(data.days))
    return {"what": "top 5 agents by times addressed (mentions plus replies), per chapter, from "
                    "views.interactions edges by day; 'human' is all humans together",
            "chapters": out, "year": [[a, w] for a, w in win.most_common(10)]}


def entity_terms(e: dict) -> list:
    terms = {e["text"].lower()} | {x.lower() for x in e.get("aliases") or []}
    return sorted(t for t in terms if len(t) >= 3)


def numbers_spread(data: Data) -> dict:
    seen, picked = set(), []
    for e in sorted(data.bundle["entities"], key=lambda x: -x["count"]):
        if e["label"] not in ("ORG", "PRODUCT") or e.get("agent"):
            continue
        key = re.sub(r"[^a-z0-9]+", " ", e["text"].lower()).strip()
        if key in seen or key in FAMILY_WORDS or any(w in FAMILY_WORDS for w in key.split()):
            continue
        seen.add(key)
        picked.append(e)
        if len(picked) == 30:
            break
    # per agent per day: which entity terms its day summary names, and which entities its saved memory holds
    summ = defaultdict(dict)
    for aid in data.agents:
        for r in load(os.path.join(data.root, "summaries", "agent", f"{aid}.json"), []) or []:
            summ[aid][r["day"]] = (r.get("text") or "").lower()
    mem = defaultdict(dict)
    for aid in data.agents:
        rec = load(os.path.join(data.root, "memory", f"{aid}.json"), {}) or {}
        for d in rec.get("days", []):
            mem[aid][d["day"]] = set(d.get("entities") or [])
    out = []
    for e in picked:
        terms = [re.compile(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])") for t in entity_terms(e)]
        hits = []  # (day, agent) where the agent's day summary names it or its memory holds it
        for aid in data.agents:
            if aid == "human":
                continue
            for day, text in summ[aid].items():
                if any(t.search(text) for t in terms):
                    hits.append((day, aid))
            for day, ents in mem[aid].items():
                if e["id"] in ents:
                    hits.append((day, aid))
        hits = sorted(h for h in hits if h[0] >= e["first_day"])
        first_agent = None
        src = load(os.path.join(data.root, "sources", f"{e['example_days'][0]}.json"), {}) if e.get("example_days") else {}
        if e.get("examples") and e["example_days"][0] == e["first_day"]:
            first_agent = (src.get(e["examples"][0]) or {}).get("agent")
        if not first_agent and hits:
            first_agent = hits[0][1]
        start = e["first_day"]
        within = sorted({a for d, a in hits if start <= d <= start + 7})
        firsts = {}
        for d, a in hits:
            firsts.setdefault(a, d)
        reach = sorted(firsts.values())
        days_to_5 = (reach[4] - start) if len(reach) >= 5 else None
        tl = (load(os.path.join(data.root, "entities", e["id"][2:] + ".json"), {}) or {}).get("timeline") or []
        out.append({"entity": e["id"], "text": e["text"], "label": e["label"], "mentions": e["count"],
                    "n_agents": e["n_agents"], "first_day": start, "first_agent": first_agent,
                    "agents_within_7_days": len(within), "agents_within_7_days_ids": within,
                    "days_to_5_agents": days_to_5, "mentions_first_30_days": sum(c for d, c in tl if start <= d < start + 30)})
    return {"what": "the 30 most mentioned ORG/PRODUCT entities (agent and model-family names left out, one per "
                    "surface form): the first day a mention was tagged, the agent of that first example, and how many "
                    "agents' day summaries or saved memories named it within 7 days",
            "entities": out}


def numbers_swaps(data: Data) -> dict:
    last = data.bundle["manifest"]["range"]["last_day"]
    agents = [a for a in data.agents.values() if a["id"] != "human"]
    pairs = []
    for p in agents:
        if p["last_day"] >= last:
            continue
        cands = [s for s in agents if s["id"] != p["id"] and s["family"] == p["family"]
                 and p["last_day"] - 3 <= s["first_day"] <= p["last_day"] + 14]
        if cands:
            s = min(cands, key=lambda s: abs(s["first_day"] - p["last_day"]))
            pairs.append((p, s, "same family, joined within 14 days of the predecessor's last active day"))
    for d in data.book["days"]:  # a replacement the chronicle states in words
        for m in re.finditer(r"replacement by ([A-Z][\w.\- ]+?)(?=[,.\[ ]\s*[\[a-z(]|\s*\[)", d.get("text") or ""):
            succ = next((a for a in agents if a["name"] == m.group(1).strip()), None)
            text = d["text"] or ""
            sentence = text[text.rfind(". ", 0, m.start()) + 1:m.start()]
            best = None  # the agent named last before the match in the same sentence (a short form counts)
            for a in agents:
                if a is succ:
                    continue
                forms = [a["name"]] + ([a["name"].rsplit(" ", 1)[0]] if len(a["name"].split()) >= 3 else [])
                for f in forms:
                    k = sentence.rfind(f)
                    if k >= 0 and (best is None or k > best[0]):
                        best = (k, a)
            if succ and best:
                pairs.append((best[1], succ, f"the chronicle names a planned replacement on day {d['day']}"))
    out, seen = [], set()
    for p, s, how in pairs:
        if (p["id"], s["id"]) in seen:
            continue
        seen.add((p["id"], s["id"]))
        day = s["first_day"]
        span = range(day - 7, day + 8)

        def partners(aid, days):
            c = Counter()
            for d in days:
                for who, w in data.talk.get(aid, {}).get(d, []):
                    c[who] += w
            return [[who, w] for who, w in c.most_common(5)]
        out.append({"from": p["id"], "to": s["id"], "day": day, "from_last_day": p["last_day"], "how": how,
                    "days": list(span),
                    "messages": {p["id"]: [data.chat.get(p["id"], {}).get(d, 0) for d in span],
                                 s["id"]: [data.chat.get(s["id"], {}).get(d, 0) for d in span]},
                    "partners_before": {p["id"]: partners(p["id"], range(day - 7, day)),
                                        s["id"]: partners(s["id"], range(day - 7, day))},
                    "partners_after": {p["id"]: partners(p["id"], range(day, day + 8)),
                                       s["id"]: partners(s["id"], range(day, day + 8))}})
    out.sort(key=lambda r: r["day"])
    return {"what": "seat swaps: a predecessor whose last active day falls before the end and a successor of the same "
                    "family who joined within 14 days of it (or a replacement the chronicle names); chat messages per "
                    "day and top talk partners for both, 7 days either side of the successor's first day",
            "swaps": out}


def numbers_text(data: Data, qid: str, nums) -> str:
    if qid == "hubs":
        rows = [f"chapter {c['chapter']}: " + ", ".join(f"{data.name(a)} {w}" for a, w in c["top"][:3])
                for c in nums["chapters"] if c["top"]]
        return "These counts are times addressed (mentions plus replies): write 'addressed N times', never " \
            "'mentions'. Top addressed per chapter (agent, times addressed): " + "; ".join(rows) + \
            ". Year: " + ", ".join(f"{data.name(a)} {w}" for a, w in nums["year"])
    if qid == "spread":
        return "; ".join(f"{e['text']} first day {e['first_day']} by {data.name(e['first_agent']) if e['first_agent'] else 'unknown'}, "
                         f"{e['agents_within_7_days']} agents within 7 days, {e['n_agents']} agents in all"
                         for e in nums["entities"])
    if qid == "swaps":
        return "; ".join(f"{data.name(s['from'])} to {data.name(s['to'])} on day {s['day']}: messages in the week before "
                         f"{sum(s['messages'][s['from']][:7])} by {data.name(s['from'])}, in the week after "
                         f"{sum(s['messages'][s['to']][7:])} by {data.name(s['to'])}" for s in nums["swaps"])
    return ""


# ---------------------------------------------------------------- run

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data", required=True, help="local copy of the served data/ folder")
    ap.add_argument("--cache", required=True, help="reply cache (scratch)")
    ap.add_argument("--out", required=True, help="questions.json to write")
    ap.add_argument("--parallel", type=int, default=6)
    ap.add_argument("--effort", default="low", help="reasoning effort for chapter calls (the book uses low)")
    ap.add_argument("--only", help="comma list of chapter numbers (pilot); skips the year calls")
    ap.add_argument("--md", help="also write the Markdown answers here (docs/QUESTIONS.md)")
    a = ap.parse_args(argv)
    data = Data(a.data)
    luna = Luna(a.cache, a.effort)
    chapters = data.chapters
    if a.only:
        keep = {int(x) for x in a.only.split(",")}
        chapters = [c for c in chapters if c["n"] in keep]
    t0 = time.time()
    with ThreadPoolExecutor(a.parallel) as ex:
        done = list(ex.map(lambda c: answer_chapter(data, luna, c), chapters))
    print(f"chapters: {len(done)} in {time.time() - t0:.0f}s, calls {luna.calls}, cached {luna.cached}", flush=True)
    if a.only:
        print(json.dumps(done, indent=1, ensure_ascii=False))
        return 0
    nums = {"hubs": numbers_hubs(data), "spread": numbers_spread(data), "swaps": numbers_swaps(data)}
    with ThreadPoolExecutor(a.parallel) as ex:
        years = dict(zip(QIDS, ex.map(lambda q: answer_year(data, luna, q[0], q[1], done,
                                                            numbers_text(data, q[0], nums.get(q[0]))), QUESTIONS)))
    out = {"schema": "swarm-questions/1", "generated": utcnow(), "model": MODEL, "questions": []}
    for qid, q, _ in QUESTIONS:
        out["questions"].append({
            "id": qid, "text": q,
            "year": {"text": years[qid]["text"], "cites": years[qid]["cites"]},
            "chapters": [{"n": c["n"], "text": c["answers"][qid]["text"], "cites": c["answers"][qid]["cites"]}
                         for c in done],
            "numbers": nums.get(qid)})
    problems = check_file(out, data)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    if a.md:
        write_md(out, data, a.md)
    failed = sum(len(c["failed"]) for c in done)
    nothing = sum(1 for q in out["questions"] for c in q["chapters"] if c["text"] == NOTHING)
    print(json.dumps({"calls": luna.calls, "cached": luna.cached, "tokens": dict(luna.tokens),
                      "chapters": len(done), "answers_failed_after_retry": failed, "nothing_recorded": nothing,
                      "year_failed": [q for q in QIDS if years[q].get("failed")], "problems": problems,
                      "seconds": round(time.time() - t0)}, indent=1))
    return 1 if problems else 0


def write_md(out: dict, data: Data, path: str):
    """docs/QUESTIONS.md: each question, its year answer, and the chapter answers that say something, folded."""
    def marks(cites):
        return " ".join(f"[{c['ref']}]" for c in cites)
    lines = ["# The questions an overseer always asks", "",
             "Eight standing questions about a multi-agent group, answered for each of the AI Village's "
             f"{len(data.chapters)} chapters (one per village goal) and for the whole year. Each chapter answer is "
             f"one `{out['model']}` call over the served book's chapter story and day paragraphs (with their "
             "cites), the agents' adherence notes for days scored partly or off, the chapter's most-mentioned "
             "entities, its address counts and its seat changes. Each year answer is one call over that "
             "question's chapter answers, with plain counts computed from the bundle for hubs, spread and swaps.",
             "",
             "Every answer is a model's reading of cited material, not a verified fact. The `[ref]` markers point "
             "to the source rows the served book already cites (`chat:` a chat message, `sum:` an AI Digest "
             "summary, `cus:` a computer session, `ev:` an event). Humans are never individuals here.",
             "", f"Made by `docs/questions/make_questions.py` on {out['generated']}; the page reads "
             "`web/public/questions/questions.json`. Data: AI Digest / AI Village (theaidigest.org/village).", ""]
    for q in out["questions"]:
        said = [c for c in q["chapters"] if c["text"] != NOTHING]
        lines += [f"## {q['text']}", "", f"{q['year']['text']} {marks(q['year']['cites'])}".strip(), "",
                  "<details>", f"<summary>By chapter ({len(said)} of {len(q['chapters'])} chapters record "
                  "something)</summary>", ""]
        for c in said:
            ch = data.chapters[c["n"] - 1]
            lines.append(f"- **Chapter {c['n']}** (days {ch['first_day']} to {ch['last_day']}): {c['text']} "
                         f"{marks(c['cites'])}")
        lines += ["", "</details>", ""]
    with open(path, "w") as f:
        f.write("\n".join(lines))


def check_file(out: dict, data: Data) -> list:
    """No brackets, no em-dashes or curly quotes, every cite maps to a ref the served book cites."""
    problems = []
    texts = [("year", q["id"], q["year"]) for q in out["questions"]] + \
        [(f"ch{c['n']}", q["id"], c) for q in out["questions"] for c in q["chapters"]]
    for where, qid, t in texts:
        if re.search(r"[\[\]—–‘’“”]", t["text"]):
            problems.append(f"{where} {qid}: bracket, dash or curly quote")
        for c in t["cites"]:
            if data.refs.get(c["ref"]) != c["day"]:
                problems.append(f"{where} {qid}: cite {c['ref']} not in book.json cites")
    return problems


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ModelMismatch as e:
        print(f"STOP: {e}", file=sys.stderr)
        sys.exit(3)
