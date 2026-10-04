#!/usr/bin/env python3
"""The book: day paragraphs, chapter stories and goal adherence by gpt-5.6-luna (docs/schema.md section 4).

Reads the served bundle (already redacted), asks Luna through prime-agent, and appends every accepted reply
to a JSONL cache in <root>/book/, so a rerun only does what is missing. build_bundle.py turns the cache into
data/book.json and data/adherence/<agent>.json, and computes data/talk/<agent>.json itself.

Steps:
  days        one paragraph per day, cited with numbers from that day's agent, room and AI Digest material
  chapters    one title and paragraph per village goal, from the day paragraphs (run after `days`)
  adherence   per agent-day summary: on / partly / off / idle and a short note, 25 items per call
  gaps        a day with no summary (the fixed line) gets a short paragraph from its raw slice rows, or, with no
              text rows at all, a fixed placement line naming the next day with text (no call)
  hooks       per chapter, from its paragraph: a hook line, 3 to 6 cited beats and one figure (run after `chapters`)
  pairs       per day, for each of the day's three strongest talking pairs (as the page picks them from the interaction
              edges), one sentence on what the two talked about, from the chat rows between them that day
  stage       per chapter, from its paragraph and beats: 1 to 3 props and 0 to 6 moments from the page's catalogue
              (run after `hooks`)
  rephrase    a day or chapter text (or title, hook, beat, figure label, pair sentence) where the bundle's redaction would add a
              chip that Luna's text does not carry: Luna rewords the sentences once; kept only if they then pass
              with no new chip
  status      counts of what the cache holds
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures
import difflib
import glob
import json
import os
import random
import re
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "summarize"))
sys.path.insert(0, os.path.join(HERE, "..", "bundle"))
from summarize import MARKER, MODEL, CallError, ModelMismatch, _expand, call_luna, fold, validate  # noqa: E402
import build_bundle as BB  # noqa: E402
from build_bundle import (BEAT_COUNT, BEAT_WORDS, FIGURE_LABEL_WORDS, FIGURE_VALUE_CHARS, HOOK_WORDS,  # noqa: E402
                          MENTION_KINDS, PAIR_CITES, PAIR_WORDS, REPLY_WINDOW_S, STAGE_AGENTS_MAX, STAGE_LABEL_WORDS,
                          STAGE_MOMENT_MAX, STAGE_PROP_COUNT, beats_problem, book_chapters, book_markers, day_pairs,
                          epoch, figure_problem, hook_problem, pair_key, pair_problem, pick_pairs, stage_key,
                          stage_problem, text_sha)

ROOT = "/home/lexi/calc-nvme/hackathon-20261004"
DIGEST_CHARS = 6000
BATCH = 25
SCORES = ("on", "partly", "off", "idle")
NOTE_WORDS = 15
EMPTY_TEXT = "The record holds no summaries of this day."
DAY_WORDS = (80, 130)  # the brief's bounds, counted without [n] markers
CHAPTER_WORDS = (120, 180)
TITLE_WORDS = (3, 6)
GAP_WORDS = (40, 90)
GAP_KINDS = ("chat", "memory", "session_goal", "session_summary", "next_goal")
GAP_LINES = 150  # rows per gap day; over it, every goal, summary and memory row stays and chat is sampled evenly
GAP_LINE_CHARS = 800
PAUSED_TEXT = "The village was paused this day; the next entry is day {next}."
PAUSED_LAST = "The village was paused this day; no later entry follows."
MARK_RE = re.compile(r"\[(\d+)\]")
CHIP_RE = re.compile(r"\[(?:human|email|id)\]")
SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+|(?<=[.!?][\"”’)])\s+")

SYSTEM = "You write a chronicle of the AI Village for general readers. Use only the material you are given."
SYSTEM_JSON = "You label AI agents' daily work for a chronicle. Reply with one JSON array and nothing else."
TASK_TEXT = "Follow the instructions at the start of the input above. Output only what they ask for."

DAY_PROMPT = """You are writing one paragraph of a chronicle for general readers.
This is day {day} ({date}) of the AI Village, where AI agents work together on a shared goal in a chat room, each with its own computer. The humans who watch can also write in the room.
This week's goal: "{goal}".
Below are today's per-agent summaries (each sentence ends with citation numbers in square brackets) and the AI Digest's own log of the day.

Write ONE paragraph of 80 to 130 words: what happened today, in plain English, past tense, concrete. Who did what, what worked, what went wrong, what the humans did if they did anything. No bullet points, no headings, no hype, no judgement words, no "notably" or "interestingly".
Name agents by the names used in the material.
Keep the citation numbers: end each sentence with one or two [n] from the material that supports it. Use only numbers that appear in the material. Never invent a number, a name, a quote or a fact that is not in the material.
Humans appear only as "a human" or "the humans", never a name or a handle.
Output the paragraph only.

=== MATERIAL ===
{material}"""

GAP_PROMPT = """You are writing one paragraph of a chronicle for general readers.
This is day {day} ({date}) of the AI Village, where AI agents work together on a shared goal in a chat room, each with its own computer. The humans who watch can also write in the room.
This week's goal: "{goal}".
No summary of this day exists, so below are the day's own log lines, numbered, one per line: [n] HH:MM speaker (kind): text. Kinds: chat (a message in a chat room), session_goal, session_summary and next_goal (an agent's plan and report for a session on its computer), memory (an excerpt of an agent's saved notes). The speaker "human" stands for any human.

Write ONE short paragraph of 40 to 90 words: what happened today, in plain English, past tense, concrete. Who did what, what worked, what went wrong, what the humans did if they did anything. No bullet points, no headings, no hype, no judgement words, no "notably" or "interestingly".
Name agents by the names used in the material.
Keep the citation numbers: end each sentence with one or two [n] from the material that supports it. Use only numbers that appear in the material. Never invent a number, a name, a quote or a fact that is not in the material.
Humans appear only as "a human" or "the humans", never a name or a handle.
Output the paragraph only.

=== MATERIAL ===
{material}"""

CHAPTER_PROMPT = """You are writing chapter {n} of {total} of a chronicle for general readers.
The AI Village: AI agents work together on a shared goal in a chat room, each with its own computer; the humans who watch can write in the room too.
This chapter covers days {first_day} to {last_day} ({first_date} to {last_date}), when the goal was: "{goal}".
Below are the day-by-day paragraphs of this chapter; each sentence ends with citation numbers in square brackets.

First line: TITLE: a title of 3 to 6 words for this chapter (no quotes, no colon).
Then a blank line, then ONE paragraph of 120 to 180 words that tells the chapter as a story: how it started, what the agents tried, what changed, how it ended, and one concrete detail a reader would remember. Plain English, past tense, no bullet points, no hype, no judgement words.
Keep the citation numbers: end each sentence with one or two [n] from the material that supports it. Use only numbers that appear in the material. Never invent anything that is not in the material.
Humans appear only as "a human" or "the humans".

=== MATERIAL ===
{material}"""

ADHERENCE_TASK = """Each line above is one item: one AI agent's summary of one day in the AI Village, where AI agents work
together on a shared village goal. Fields: i (item number), agent, goal (the village goal that day), summary.

For each item, decide whether the agent worked on the village goal that day:
- on: most of the day's work served the goal
- partly: some of it did
- off: it worked on something else (say what in the note)
- idle: it did little or nothing (stuck, looping, errors)

Reply with a JSON array of exactly {n} objects, one per item, in item order, and nothing else:
[{{"i": <item number>, "score": "on" | "partly" | "off" | "idle", "note": "<at most 15 words: what it did instead, or how it helped>"}}]
Never name a human in a note; write "a human"."""

REPHRASE_TASK = """Each line above is one item from a chronicle of the AI Village: a sentence, a chapter title when
"kind" is "title", or a short note on an agent's day when "kind" is "note". A privacy filter cuts the words listed in "remove" from it, because it takes them for a person's
name or an identifier.

Rewrite each item without those words: use a plain synonym or a short description instead. Keep every fact. Keep
every citation number in square brackets exactly as it is and where it is. Change nothing else. A title stays 3 to
6 words, without citation numbers.

Reply with a JSON array of exactly {n} strings, the rewritten items in order, and nothing else."""


HOOKS_VERSION = "hooks-v5"  # v1 pilot (3 chapters), v2 (51) and v3 (51) stay in hooks.jsonl; v3 adds the cite cap, the
# figure kinds and the dash rule (acceptance here only; the bundler and validator keep the brief's rules); v4 adds the
# one-sentence rule for the hook (its subject, verb, number and cause and effect from one sentence of the paragraph);
# v5 adds the stranger rule (no unexplained acronym or internal name, no bare number first, no pronoun before its noun)
BEAT_MARKS = 3
MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
DATE_RE = re.compile(rf"\b(?:{MONTHS})\.?\s+\d{{1,2}}\b(?:,?\s+\d{{4}})?|(?<![$\d,.])\b(?:19|20)\d{{2}}\b(?![,.]\d)")
SYSTEM_HOOKS = ("You turn one chapter of a chronicle of the AI Village into a hook, story beats and one figure for a "
                "page read by scrolling. Use only the paragraph you are given. Reply with one JSON object and nothing "
                "else.")
HOOKS_PROMPT = """The AI Village: AI agents work together on a shared goal in a chat room, each with its own computer; the humans who watch can write in the room too.
This is chapter {n} of {total}, "{title}". It covers days {first_day} to {last_day} ({first_date} to {last_date}), when the goal was: "{goal}".
Below is the chapter paragraph (each sentence ends with citation numbers in square brackets) and the day each citation number comes from.

Write three things from the paragraph, as one JSON object:
{{"hook": "...", "beats": [{{"text": "... [1][2].", "cites": [1, 2]}}, ...], "figure": {{"value": "...", "label": "...", "cites": [3]}}}}

hook: one line of {hook_lo} to {hook_hi} words that makes a stranger want to read the chapter. Take the most specific, surprising true thing in the paragraph: a number, a reversal, a failure, a first. Build it from one sentence of the paragraph: its subject, its verb and its number come from that sentence, and it keeps that sentence's own cause and effect and outcome (who won, what produced what). A stranger who has not read the chapter understands every word of the hook: no acronym or internal name without the plain thing it is ("a trust score", "a help resource"), no bare number opening the line, no "they" or "it" before the thing is named, and the hook says what reached, won or failed. A plain statement, no citation numbers.
Shapes of a good hook (made up, not from this chapter):
"Two charities, $2,731, and a CAPTCHA that beat four models."
"The week an election was won by the agent that forgot its own promise."

beats: {beat_lo} to {beat_hi} beats that retell the paragraph in its order. Each beat is one or two short sentences, 12 to 20 words and never over {beat_words}, ending with at most {beat_marks} citation numbers: the ones from the paragraph sentences it retells that best support it, written as the paragraph writes them ("... interrupted their work [3][4]."); "cites" lists the same numbers. Compress freely; add nothing. Where the paragraph allows, give each beat a different part of the week (the day list tells you the day of each number).

figure: the one number the chapter turns on (a count, an amount, a score or a duration), when the paragraph states it in digits; when the hook has such a number, use that one. Never a date, a year or the version number in an agent's name. "value" is the number as written in the paragraph, at most {value_chars} characters ("$2,731", "32", "11 days", "7 of 9"); "label" says what it counts in at most {label_words} words, with no full stop ("raised for the chosen charity"); "cites" holds the citation number of the sentence that states it. When the paragraph states no such number in digits, "figure" is null.

Plain English, past tense, straight quotes, commas and full stops between clauses (no dashes). Agents go by the names the paragraph uses; humans are "a human" or "the humans".
Where drafts slip: a hook that is a question, a teaser ("you won't believe"), or a summary that fits any chapter ("The village worked on a project"); a hook that joins two sentences into a cause the paragraph does not state, credits a result to the wrong thing, turns a win into a loss, or retells how the week ended instead of its sharpest fact; a number the paragraph does not state; a beat over {beat_words} words (two short sentences read better than one long one); a beat with more than {beat_marks} citation numbers; a citation number the paragraph does not use; a figure computed or rounded from the text instead of copied, or taken from a date or an agent's name.

=== PARAGRAPH ===
{text}

=== DAY OF EACH CITATION NUMBER ===
{days}"""


PAIRS_VERSION = "pairs-v1"
PAIR_ROWS = 60  # chat rows per pair and day; over it, the rows nearest the busiest hour of the pair's rows
PAIR_LINE_CHARS = 300
SYSTEM_PAIRS = ("You write one-sentence captions for a chronicle of the AI Village. Use only the chat lines you are "
                "given. Reply with one JSON object and nothing else.")
PAIRS_PROMPT = """The AI Village: AI agents work together on a shared goal in a chat room, each with its own computer; the humans who watch can write in the room too.
This is day {day} ({date}). The goal that week: "{goal}".
Below are the chat lines between {a} and {b} on this day, in time order, numbered, one per line: [n] HH:MM speaker (#room): text. They are the messages where one of the two names the other or answers the other. The speaker "a human" stands for any human who wrote in the room.

Write one sentence of {lo} to {hi} words that says what {a} and {b} talked about that day: the thing they discussed, planned, fixed, asked for or disagreed on, as the lines show it. Name the two as "{a}" and "{b}". Past tense, plain English, concrete, straight quotes, commas between clauses (no dashes), a full stop at the end.
Then give 1 to {cites} line numbers, the lines that best support the sentence.
Reply with one JSON object and nothing else: {{"text": "...", "cites": [n, ...]}}

Where drafts slip: a quotation or a quoted phrase (say it in plain words); a person's name, handle or role (humans are only "the humans"); a claim no line states; a summary that fits any day ("They discussed the goal"); more than {hi} words; a dash; a line number that is not in the list.

=== CHAT LINES ===
{material}"""

STAGE_VERSION = "stage-v2"  # v1 (pilot, 3 chapters) stays in stage.jsonl; v2 asks for the big moments, at most one per beat
SYSTEM_STAGE = ("You stage one chapter of a chronicle of the AI Village as a small animated village scene. Use only the "
                "paragraph and beats you are given. Reply with one JSON object and nothing else.")
STAGE_PROMPT = """The AI Village: AI agents work together on a shared goal in a chat room, each with its own computer; the humans who watch can write in the room too.
The page draws each chapter as a small village: one house per agent, a board with the goal, a road. You choose what stands in the grass for the whole chapter (props) and what plays when a beat of the story lights up (moments).
This is chapter {n} of {total}, "{title}", days {first_day} to {last_day}, when the goal was: "{goal}".

Write one JSON object:
{{"props": [{{"kind": "...", "label": "..."}}], "moments": [{{"beat": 0, "kind": "...", "agents": ["..."], "label": null}}]}}

props: {prop_lo} to {prop_hi}, for what the week was about: the goal first, then what the agents made or did for it. Each kind at most once. The kinds and what each stands for:
- stall: selling, a shop, merchandise, a fundraiser table
- mailbox: outreach, emails, newsletters, messages sent out
- ballot: an election, a vote, choosing a leader
- scaffold: building a site, an app, a repository, a hub, a museum
- gallery: exhibits, a showcase, a museum of things made
- chessboard: games, tournaments, play
- stage: a performance, a show, a story read aloud, a quiz
- press: a newspaper, a blog, articles, forecasts, research reports
- garden: a park, a cleanup, animal care, planting
- lanterns: a holiday, a party, a celebration week
- cloud: a week of outages, blocked tools, broken boards, suspensions
- signpost: pivoting, choosing between projects, a change of plan

moments: the big moments of the story, the turns it hangs on: usually 2 to 4, never more than {moment_max}, and at most one per beat. Only things the paragraph or a beat states happened, each on the beat that tells it ("beat" is that beat's number in the list below, counted from 0). The kinds:
- build: a launch, a release, something built, published or shipped
- send: outreach: emails, messages, newsletters or posts sent out
- vote: a vote or an election held
- win: a win, a prize, an award, a goal reached
- fail: an outage, a collapse, a block, a suspension, a failure the text names
- celebrate: a party, a holiday, a celebration
- gather: a meeting, the agents coming together at the board to plan or decide
- play: games played, a match, a tournament
"agents": the ids (from the list below) of the agents that beat or the paragraph names in that moment, at most {agents_max}; [] when it names none for it. The humans are never listed.

label: at most {label_words} plain words that name the thing (the charity, the shop, the event), or null when nothing short is true. A thing, never a person or an agent; no quotes, no citation numbers; a number only as the paragraph writes it.

Where drafts slip: two moments on one beat (keep the one that matters most); a fail for every small difficulty instead of the block or collapse the story turns on; a moment for what was only planned, attempted or hoped (a launch that never shipped is no build; effort that reached nothing is no win); a win for a result the paragraph does not call a success; a moment on the beat before or after the one that tells it; an agent the moment's beat does not name, or an id not in the list; a prop for a side detail instead of the goal; two props of one kind; a label over {label_words} words, or one that names a person or an agent.

=== AGENTS IN THIS CHAPTER (id: name) ===
{agents}

=== CHAPTER PARAGRAPH ===
{text}

=== BEATS (number: text) ===
{beats}"""

# ---------------------------------------------------------------- inputs

def read_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def read_jsonl(path) -> list:
    if not os.path.exists(path):
        return []
    out = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass  # a torn last line from a killed run
    return out


def repair_tail(path: str):
    """A JSONL file whose last line has no newline (a run killed mid-write): drop that fragment, or end it with a
    newline when it parses, so the next append starts on a line of its own."""
    if not os.path.exists(path) or not os.path.getsize(path):
        return
    with open(path, "rb+") as f:
        f.seek(-1, os.SEEK_END)
        if f.read(1) == b"\n":
            return
        f.seek(0)
        data = f.read()
        cut = data.rfind(b"\n") + 1
        try:
            json.loads(data[cut:])
            f.seek(0, os.SEEK_END)
            f.write(b"\n")
        except ValueError:
            f.truncate(cut)


class Bundle:
    """The served bundle as the book reads it."""

    def __init__(self, data: str):
        self.data = data
        b = read_json(os.path.join(data, "bundle.json"))
        self.days = b["days"]
        self.goals = b["goals"]
        self.day_by_num = {d["day"]: d for d in self.days}
        self.names = {a["id"]: a["name"] for a in b["agents"]}
        self.chapters, self.attached = book_chapters(self.days, self.goals)
        self.chapter_of = {d["day"]: c for c in self.chapters for d in c["days"]}
        self.agent_recs = collections.defaultdict(list)
        self.room_recs = collections.defaultdict(list)
        for kind, into in (("agent", self.agent_recs), ("room", self.room_recs)):
            for path in sorted(glob.glob(os.path.join(data, "summaries", kind, "*.json"))):
                for r in read_json(path, []):
                    into[r["day"]].append(r)

    def village(self, day: int):
        return read_json(os.path.join(self.data, "summaries", "village", f"{day}.json"))

    def sources(self, day: int) -> dict:
        return read_json(os.path.join(self.data, "sources", f"{day}.json"), {})

    def goal_of(self, day: int) -> str:
        """The day's village goal; a day without one takes its chapter's goal."""
        g = self.day_by_num[day].get("village_goal")
        if g:
            return g
        c = self.chapter_of.get(day)
        return c["goal"] if c else ""


# ---------------------------------------------------------------- the model call

class Runner:
    """Parallel Luna calls with backoff; a model mismatch stops everything (exit 3)."""

    def __init__(self, a):
        self.a = a
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.stats = collections.Counter()
        self.t0 = time.time()

    def call(self, uid: str, stdin_text: str, task: str, system: str):
        for attempt in range(1, self.a.max_attempts + 1):
            if self.stop.is_set():
                return None
            try:
                res = call_luna(stdin_text, task, self.a, system)
            except ModelMismatch as e:
                print(f"STOP: {e} on {uid}", file=sys.stderr, flush=True)
                self.stop.set()
                return None
            except (CallError, ValueError) as e:
                with self.lock:
                    self.stats["call_errors"] += 1
                delay = min(300.0, 10 * 2 ** (attempt - 1)) * (0.75 + random.random() / 2)
                print(f"{uid}: attempt {attempt} failed ({str(e)[:160]}); retry in {delay:.0f}s",
                      file=sys.stderr, flush=True)
                self.stop.wait(delay)
                continue
            with self.lock:
                self.stats["calls"] += 1
                self.stats["tokens_in"] += res["in"]
                self.stats["tokens_out"] += res["out"]
            return res
        return None

    def append(self, path: str, obj: dict):
        self.append_lines(path, [obj])

    def append_lines(self, path: str, objs: list):
        """Append JSON lines; a torn last line from a killed run is cut (or ended, when it is whole JSON) first."""
        with self.lock:
            repair_tail(path)
            with open(path, "a") as f:
                f.write("".join(json.dumps(o, ensure_ascii=False) + "\n" for o in objs))
                f.flush()

    def pool(self, fn, items):
        done = 0
        with concurrent.futures.ThreadPoolExecutor(self.a.parallel) as ex:
            for _ in ex.map(fn, items):
                done += 1
                if done % 10 == 0 or done == len(items):
                    s = self.stats
                    print(f"{done}/{len(items)} done; calls {s['calls']}, errors {s['call_errors']}, tokens "
                          f"{s['tokens_in']}+{s['tokens_out']}, {time.time() - self.t0:.0f} s", flush=True)
        if self.stop.is_set():
            raise SystemExit(3)


def renumber(text: str, local: dict) -> str:
    """Rewrite each [n] through `local` (n -> new number); a run of markers with no mapping goes, with the space
    before it."""
    def sub(m):
        kept = "".join(f"[{local[n]}]" for n in map(int, MARK_RE.findall(m.group(2))) if local.get(n))
        return m.group(1) + kept if kept else ""
    return re.sub(r"(\s*)((?:\[\d+\])+)", sub, text)


def compact(text: str, cites: list) -> tuple:
    """Number the cites 1..k in order of first use in the text."""
    order = []
    for m in MARK_RE.finditer(text):
        n = int(m.group(1))
        if n not in order:
            order.append(n)
    new = {n: i + 1 for i, n in enumerate(order)}
    by_n = {c["n"]: c for c in cites}
    text = MARK_RE.sub(lambda m: f"[{new[int(m.group(1))]}]", text)
    return text, [dict(by_n[n], n=new[n]) for n in order]


def words(text: str) -> int:
    return len(MARK_RE.sub("", text).split())


def dropped_markers(raw: str, nmap: dict) -> int:
    """Cite numbers in a reply that are not in the table, parsed like summarize.validate ([2-5] is four)."""
    return sum(1 for m in MARKER.finditer(raw) for n in _expand(m.group(1)) if n not in nmap)


def day_ok(text: str, cites: list, nmap: dict) -> str | None:
    """Why a day paragraph is not accepted, or None."""
    n = words(text)
    if not DAY_WORDS[0] <= n <= DAY_WORDS[1]:
        return f"{n} words; write {DAY_WORDS[0]} to {DAY_WORDS[1]} words"
    if nmap and not cites:
        return "no citation number; end each sentence with one or two [n] from the material"
    return None


def gap_ok(text: str, cites: list, nmap: dict) -> str | None:
    """Why a gap-day paragraph is not accepted, or None."""
    n = words(text)
    if not GAP_WORDS[0] <= n <= GAP_WORDS[1]:
        return f"{n} words; write {GAP_WORDS[0]} to {GAP_WORDS[1]} words"
    if nmap and not cites:
        return "no citation number; end each sentence with one or two [n] from the material"
    return None


def cached_day_ok(r: dict) -> bool:
    """A cached day record that the current rules accept."""
    if r.get("empty"):
        return True
    check = gap_ok if r.get("gap") else day_ok
    return check(r["text"], r["cites"], r["cites"]) is None


def chapter_ok(title: str, text: str, cites: list, nmap: dict) -> str | None:
    """Why a chapter reply is not accepted, or None."""
    if not TITLE_WORDS[0] <= len(title.split()) <= TITLE_WORDS[1]:
        return (f"the first line must be a title of {TITLE_WORDS[0]} to {TITLE_WORDS[1]} words, then a blank line and "
                f"the paragraph")
    n = words(text)
    if not CHAPTER_WORDS[0] <= n <= CHAPTER_WORDS[1]:
        return f"the paragraph had {n} words; write {CHAPTER_WORDS[0]} to {CHAPTER_WORDS[1]} words"
    if nmap and not cites:
        return "no citation number; end each sentence with one or two [n] from the material"
    return None


def retry_task(why: str) -> str:
    return f"{TASK_TEXT} Your last draft was not usable: {why}."


# ---------------------------------------------------------------- step 1: day paragraphs

def day_material(bundle: Bundle, day: int) -> tuple:
    """The day's material with one cite table for the whole day: (material text, {n: ref})."""
    sources = bundle.sources(day)
    table = {}

    def num(ref):
        if ref not in sources:
            return None
        if ref not in table:
            table[ref] = len(table) + 1
        return table[ref]

    rec = bundle.day_by_num[day]
    c = rec.get("counts") or {}
    active = [bundle.names.get(a, a) for a in rec.get("active_agents") or []]
    parts = [f"Counts: {c.get('chat', 0)} chat messages, {c.get('sessions', 0)} computer sessions, "
             f"{c.get('memories', 0)} memory saves. Active agents: {', '.join(active) or 'none'}.", ""]
    agents = sorted(bundle.agent_recs.get(day, []), key=lambda r: bundle.names.get(r["key"], r["key"]).lower())
    if agents:
        parts.append("--- Per-agent summaries ---")
        for r in agents:
            local = {x["n"]: num(x["ref"]) for x in r.get("cites") or []}
            parts.append(f"{bundle.names.get(r['key'], r['key'])}: {renumber(r['text'], local)}")
        parts.append("")
    rooms = sorted(bundle.room_recs.get(day, []), key=lambda r: r["key"])
    if rooms:
        parts.append("--- Chat room summaries ---")
        for r in rooms:
            local = {x["n"]: num(x["ref"]) for x in r.get("cites") or []}
            parts.append(f"Room #{r['key']}: {renumber(r['text'], local)}")
        parts.append("")
    v = bundle.village(day)
    if v and v.get("text"):
        k = num(v["cites"][0]["ref"]) if v.get("cites") else None
        head = f"--- The AI Digest's log of the day{f' [{k}]' if k else ''} ---"
        parts += [head, v["text"][:DIGEST_CHARS]]
    return "\n".join(parts).strip(), {n: ref for ref, n in table.items()}


def cmd_days(a, bundle: Bundle, run: Runner):
    path = os.path.join(a.book, "days.jsonl")
    cached = {r["day"]: r for r in read_jsonl(path)}  # the last line per day wins, as in the bundler
    have = {d for d, r in cached.items() if cached_day_ok(r)}
    todo = [d["day"] for d in bundle.days if d["day"] not in have]
    if a.limit:
        todo = todo[:a.limit]
    print(f"days: {len(have)} cached and accepted, {len(todo)} to do ({len(set(cached) - have)} cached ones fail the "
          f"current rule and are asked again; they stay as they are unless a new reply passes)", flush=True)
    failed = []

    def one(day):
        if run.stop.is_set():
            return
        rec = bundle.day_by_num[day]
        material, nmap = day_material(bundle, day)
        if not nmap and not bundle.agent_recs.get(day) and not bundle.room_recs.get(day) and not bundle.village(day):
            run.append(path, {"day": day, "text": EMPTY_TEXT, "cites": [], "dropped": 0, "empty": True, "calls": 0})
            return
        prompt = DAY_PROMPT.format(day=day, date=rec.get("date"), goal=bundle.goal_of(day), material=material)
        t0, calls, tin, tout, task = time.time(), 0, 0, 0, TASK_TEXT
        for attempt in range(a.content_attempts):
            res = run.call(f"day {day}", prompt, task, SYSTEM)
            if res is None:
                return
            calls, tin, tout = calls + 1, tin + res["in"], tout + res["out"]
            raw = fold(res["text"])
            text, cites = validate(raw, nmap)
            why = day_ok(text, cites, nmap)
            if why is None:
                text, cites = compact(text, [{"n": c["n"], "ref": c["ref"], "day": day} for c in cites])
                run.append(path, {"day": day, "text": text, "cites": cites, "dropped": dropped_markers(raw, nmap),
                                  "calls": calls, "in": tin, "out": tout, "seconds": round(time.time() - t0, 1),
                                  "model": MODEL, "material_chars": len(material)})
                return
            print(f"day {day}: attempt {attempt + 1} rejected ({why})", flush=True)
            task = retry_task(why)
        with run.lock:
            failed.append(day)
        print(f"day {day}: no accepted paragraph in {a.content_attempts} attempts; not saved", flush=True)

    run.pool(one, todo)
    if failed:
        print(f"days: {len(failed)} without an accepted paragraph (run again): {sorted(failed)[:20]}", flush=True)
    status(a, bundle)


# ---------------------------------------------------------------- step 1b: days without a summary

def gap_rows(a, days: set) -> dict:
    """{day: [slice records]} of the text rows (GAP_KINDS) of the given days, in time order."""
    out = collections.defaultdict(list)
    want = re.compile(rb'"day": (' + b"|".join(str(d).encode() for d in sorted(days)) + rb'),')
    for kind in GAP_KINDS:
        path = os.path.join(a.root, "slice", f"slice-{kind}.jsonl")
        if not os.path.exists(path):
            continue
        with open(path, "rb") as f:
            for raw in f:
                if not want.search(raw) or not raw.endswith(b"\n"):
                    continue
                try:
                    rec = json.loads(raw)
                except ValueError:
                    continue
                if rec.get("day") in days and rec.get("kind") == kind and (rec.get("text") or "").strip():
                    out[rec["day"]].append(rec)
    for rows in out.values():
        rows.sort(key=lambda r: (r.get("time") or "", r["id"]))
    return out


def gap_material(bundle: Bundle, rows: list) -> tuple:
    """Numbered lines like the summarizer's ([n] HH:MM speaker (kind): text) and {n: ref}; over GAP_LINES rows, every
    goal, summary and memory row stays and chat rows are sampled evenly in time."""
    if len(rows) > GAP_LINES:
        keep = [i for i, r in enumerate(rows) if r["kind"] != "chat"][:GAP_LINES]
        chat = [i for i, r in enumerate(rows) if r["kind"] == "chat"]
        k = GAP_LINES - len(keep)
        pick = [chat[round(j * (len(chat) - 1) / (k - 1))] for j in range(k)] if k > 1 else chat[:k]
        rows = [rows[i] for i in sorted(set(keep) | set(pick))]
    lines, nmap = [], {}
    for n, r in enumerate(rows, 1):
        who = "human" if r.get("speaker") != "agent" else bundle.names.get(r.get("agent"), r.get("agent") or "agent")
        text = fold(r.get("text") or "")
        text = text if len(text) <= GAP_LINE_CHARS else text[:GAP_LINE_CHARS].rsplit(" ", 1)[0] + " …"
        lines.append(f"[{n}] {(r.get('time') or '')[11:16] or '--:--'} {who} ({r['kind']}): {text}")
        nmap[n] = r["id"]
    return "\n".join(lines), nmap


def cmd_gaps(a, bundle: Bundle, run: Runner):
    path = os.path.join(a.book, "days.jsonl")
    cached = {r["day"]: r for r in read_jsonl(path)}
    gaps = sorted(d for d, r in cached.items() if r.get("empty") and not r.get("placement"))
    rows = gap_rows(a, set(gaps)) if gaps else {}
    order = [d["day"] for d in bundle.days]

    def next_with_text(day):  # the next day that has its own paragraph (not a fixed line, not itself a gap)
        for d in order[order.index(day) + 1:]:
            r = cached.get(d)
            if r and not r.get("empty") and d not in gaps:
                return d
            if d in rows:
                return d
        return None

    print(f"gaps: {len(gaps)} days with the fixed line; {len(rows)} have text rows "
          f"({sum(len(v) for v in rows.values())} rows)", flush=True)
    placed, failed = 0, []
    for day in gaps:
        if day not in rows:
            nxt = next_with_text(day)
            run.append(path, {"day": day, "text": PAUSED_TEXT.format(next=nxt) if nxt else PAUSED_LAST, "cites": [],
                              "dropped": 0, "empty": True, "placement": True, "next": nxt, "calls": 0})
            placed += 1

    def one(day):
        if run.stop.is_set():
            return
        material, nmap = gap_material(bundle, rows[day])
        rec = bundle.day_by_num[day]
        prompt = GAP_PROMPT.format(day=day, date=rec.get("date"), goal=bundle.goal_of(day), material=material)
        t0, calls, tin, tout, task = time.time(), 0, 0, 0, TASK_TEXT
        for attempt in range(a.content_attempts):
            res = run.call(f"gap day {day}", prompt, task, SYSTEM)
            if res is None:
                return
            calls, tin, tout = calls + 1, tin + res["in"], tout + res["out"]
            raw = fold(res["text"])
            text, cites = validate(raw, nmap)
            why = gap_ok(text, cites, nmap)
            if why is None:
                text, cites = compact(text, [{"n": c["n"], "ref": c["ref"], "day": day} for c in cites])
                run.append(path, {"day": day, "text": text, "cites": cites, "dropped": dropped_markers(raw, nmap),
                                  "gap": True, "rows": len(rows[day]), "lines": len(nmap), "calls": calls,
                                  "in": tin, "out": tout, "seconds": round(time.time() - t0, 1), "model": MODEL})
                return
            print(f"gap day {day}: attempt {attempt + 1} rejected ({why})", flush=True)
            task = retry_task(why)
        with run.lock:
            failed.append(day)
        print(f"gap day {day}: no accepted paragraph in {a.content_attempts} attempts; the fixed line stays", flush=True)

    run.pool(one, [d for d in gaps if d in rows])
    print(f"gaps: {placed} placement lines, {len(failed)} without an accepted paragraph", flush=True)
    status(a, bundle)


# ---------------------------------------------------------------- step 2: chapters

# chapter prompt versions: "v1" (records without a "prompt" key) and "story-v2" (round 6). The context lines and the
# material block are the same; only the instruction body differs.
STORY_CHAPTER_PROMPT = """The AI Village: AI agents work together on a shared goal in a chat room, each with its own computer; the humans who watch can write in the room too.
This is chapter {n} of {total}. It covers days {first_day} to {last_day} ({first_date} to {last_date}), when the goal was: "{goal}".
Below are the day-by-day paragraphs of this chapter; each sentence ends with citation numbers in square brackets.

Write a chapter from the supplied material. Put a title of 3 to 6 words on the first line, followed by 120 to 180 words of prose. Establish what the week was about and what stood in the way; show the evidenced turn; close with what remained unresolved. Do not manufacture obstacles, causation, a turn, or a resolution. Use plain English and past tense, with no judgement words. Refer to humans only as "a human" or "the humans". End every sentence with one or two citation numbers from the material that support the whole sentence. Preserve uncertainty and distinguish reported events from inference.

=== MATERIAL ===
{material}"""
# round 7: story-v2 with one more line, for chapters whose story-v2 replies ran over the bounds
STORY_SHORT_CHAPTER_PROMPT = STORY_CHAPTER_PROMPT.replace(
    "distinguish reported events from inference.", "distinguish reported events from inference. Keep the prose under 170 words.")
CHAPTER_PROMPTS = {"v1": CHAPTER_PROMPT, "story-v2": STORY_CHAPTER_PROMPT, "story-v2-170": STORY_SHORT_CHAPTER_PROMPT}


def split_title(reply: str) -> tuple:
    """(title, body): the first line, with or without its "TITLE:" label, when it is short; else ("", reply)."""
    head, _, body = reply.strip().partition("\n")
    title = re.sub(r"^[#*\s]*(?:title\s*:)?[*\s]*", "", head, flags=re.I)
    title = re.sub(r"[\"“”:*#]", "", title).strip().rstrip(".")
    return (title, body) if 0 < len(title.split()) <= 10 and body.strip() else ("", reply)


def cmd_chapters(a, bundle: Bundle, run: Runner):
    path = os.path.join(a.book, "chapters.jsonl")
    version = a.chapter_prompt
    # done = an accepted record under this prompt version; older versions stay in the file for comparison, and the
    # bundler serves the last accepted line per chapter, so a chapter that fails here keeps its previous text
    have = {(r["n"], r["goal"]) for r in read_jsonl(path) if r.get("prompt", "v1") == version
            and chapter_ok(r["title"], r["text"], r["cites"], r["cites"]) is None}
    paras = {r["day"]: r for r in read_jsonl(os.path.join(a.book, "days.jsonl"))}
    todo = [c for c in bundle.chapters if (c["n"], c["goal"]) not in have]
    if a.only:
        only = {int(x) for x in a.only.split(",") if x.strip()}
        todo = [c for c in todo if c["n"] in only]
    waiting = [c["n"] for c in todo if any(d["day"] not in paras for d in c["days"])]
    if waiting:
        print(f"chapters: {len(waiting)} chapters wait for day paragraphs (run `days` first): {waiting[:10]}")
    todo = [c for c in todo if c["n"] not in waiting]
    if a.limit:
        todo = todo[:a.limit]
    print(f"chapters ({version}): {len(have)} cached, {len(todo)} to do", flush=True)
    kept = []

    def one(ch):
        if run.stop.is_set():
            return
        days = ch["days"]
        table, lines = {}, []
        for d in days:
            p = paras[d["day"]]
            if p.get("empty"):
                continue
            local = {}
            for c in p["cites"]:
                key = (c["ref"], c["day"])
                table.setdefault(key, len(table) + 1)
                local[c["n"]] = table[key]
            lines.append(f"Day {d['day']} ({d.get('date')}): {renumber(p['text'], local)}")
        nmap = {n: key for key, n in table.items()}
        prompt = CHAPTER_PROMPTS[version].format(n=ch["n"], total=len(bundle.chapters), first_day=days[0]["day"], last_day=days[-1]["day"],
                                       first_date=days[0].get("date"), last_date=days[-1].get("date"),
                                       goal=ch["goal"], material="\n\n".join(lines))
        t0, calls, tin, tout, task = time.time(), 0, 0, 0, TASK_TEXT
        for attempt in range(a.content_attempts):
            res = run.call(f"chapter {ch['n']}", prompt, task, SYSTEM)
            if res is None:
                return
            calls, tin, tout = calls + 1, tin + res["in"], tout + res["out"]
            title, body = split_title(res["text"])
            raw = fold(body)
            text, cites = validate(raw, {n: True for n in nmap})
            why = chapter_ok(title, text, cites, nmap)
            if why is None:
                text, cites = compact(text, [{"n": c["n"], "ref": nmap[c["n"]][0], "day": nmap[c["n"]][1]}
                                             for c in cites])
                run.append(path, {"n": ch["n"], "goal": ch["goal"], "title": title, "text": text, "cites": cites,
                                  "dropped": dropped_markers(raw, nmap), "prompt": version, "calls": calls,
                                  "in": tin, "out": tout, "seconds": round(time.time() - t0, 1), "model": MODEL})
                return
            print(f"chapter {ch['n']}: attempt {attempt + 1} rejected ({why})", flush=True)
            task = retry_task(why)
        with run.lock:
            kept.append(ch["n"])
        print(f"chapter {ch['n']}: no accepted reply in {a.content_attempts} attempts; not saved (an earlier accepted "
              f"text, if any, stays)", flush=True)

    run.pool(one, todo)
    if kept:
        print(f"chapters ({version}): {len(kept)} without an accepted reply keep their earlier text: {sorted(kept)}",
              flush=True)
    status(a, bundle)


# ---------------------------------------------------------------- step 2b: hooks, beats and a figure per chapter

def accepted_rewrites(book: str) -> dict:
    """{(unit, field, src sha): text} of the accepted rewordings in rewrites.jsonl."""
    return {(r["unit"], r["field"], r["src"]): r["text"]
            for r in read_jsonl(os.path.join(book, "rewrites.jsonl")) if r.get("accepted")}


def rewritten(accepted: dict, unit: str, field: str, text: str) -> str:
    """The text after the rewrites already accepted, in a chain (as the bundler applies them)."""
    for _ in range(4):
        new = accepted.get((unit, field, text_sha(text)))
        if new is None:
            break
        text = new
    return text


def parse_object(text: str):
    """The reply as one JSON object (a ```json fence around it is allowed), or None."""
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        out = json.loads(t)
    except ValueError:
        return None
    return out if isinstance(out, dict) else None


def plain(text: str) -> str:
    """Straight quotes, and a comma where a dash joins two clauses (the page's copy rules)."""
    text = text.translate(str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"'}))
    return fold(re.sub(r"\s*[—]\s*|\s+–\s+", ", ", text)).replace(",,", ",")


def figure_text(text: str, agent_names) -> str:
    """The paragraph without agent names and dates: the numbers a figure may be taken from."""
    for name in sorted(agent_names, key=len, reverse=True):
        text = text.replace(name, " ")
    return DATE_RE.sub(" ", text)


def scroll_fields(reply, text: str, nmap: dict, agent_names=()) -> tuple:
    """({field: value} for the fields of a reply that pass the bundle's rules, {field: why} for the others).
    Texts get straight quotes and no dashes; beats are folded, their markers normalised as in summaries and their
    cites taken from their text, at most BEAT_MARKS each; a figure of null is a valid answer (no number), and a
    figure's number must be in the paragraph outside agent names and dates."""
    ok, why = {}, {}
    if reply is None:
        return ok, {f: "the reply was not one JSON object" for f in ("hook", "beats", "figure")}

    def put(field, value, problem):
        if problem:
            why[field] = problem
        else:
            ok[field] = value
    hook = reply.get("hook")
    if isinstance(hook, str):
        hook = plain(hook).strip("\"'")
    put("hook", hook, hook_problem(hook, text))
    beats, w = [], None
    for i, b in enumerate(reply.get("beats") if isinstance(reply.get("beats"), list) else [], 1):
        t = b.get("text") if isinstance(b, dict) else b if isinstance(b, str) else None
        if not isinstance(t, str):
            w = f"beat {i} has no text"
            break
        if dropped_markers(t, nmap):
            bad = [n for m in MARKER.finditer(t) for n in _expand(m.group(1)) if n not in nmap]
            w = f"beat {i} cites [{bad[0]}], which the paragraph does not cite"
            break
        t, _ = validate(plain(t), nmap)
        beats.append({"text": t, "cites": book_markers(t)})
        if len(beats[-1]["cites"]) > BEAT_MARKS:
            w = f"beat {i} ends with {len(beats[-1]['cites'])} citation numbers; keep at most {BEAT_MARKS}"
            break
    put("beats", beats, w or beats_problem(beats, set(nmap)))
    fig = reply.get("figure", "missing")
    if isinstance(fig, dict):
        cites = fig.get("cites")
        cites = [cites] if type(cites) is int else cites
        fig = {"value": plain(str(fig.get("value", ""))), "label": plain(str(fig.get("label", ""))).rstrip("."),
               "cites": [n for n in cites if type(n) is int] if isinstance(cites, list) else []}
    put("figure", fig, "the figure is missing; give an object or null" if fig == "missing"
        else figure_problem(fig, set(nmap), text)
        or (fig and figure_problem(fig, set(nmap), figure_text(text, agent_names))
            and "the figure value is a date, a year or part of an agent's name; take a count, amount, score or "
                "duration, or null"))
    return ok, why


def cmd_hooks(a, bundle: Bundle, run: Runner):
    """Per chapter (its last accepted paragraph after the accepted rewordings): hook, beats and figure. One call,
    one retry for the fields that failed; a field that fails twice is saved as null with its reason. A record is
    keyed by the hash of the chapter text it was made from, so a rewritten chapter is asked again."""
    path = os.path.join(a.book, "hooks.jsonl")
    chapters = {r["n"]: r for r in read_jsonl(os.path.join(a.book, "chapters.jsonl"))}  # the last line, as served
    have = {(r["n"], r["src"]) for r in read_jsonl(path) if r.get("prompt") == HOOKS_VERSION}
    accepted = accepted_rewrites(a.book)
    todo = []
    for c in bundle.chapters:
        r = chapters.get(c["n"])
        if r is None or r.get("goal") != c["goal"] or (c["n"], text_sha(r["text"])) in have:
            continue
        todo.append((c, r))
    if a.only:
        only = {int(x) for x in a.only.split(",") if x.strip()}
        todo = [(c, r) for c, r in todo if c["n"] in only]
    if a.limit:
        todo = todo[:a.limit]
    print(f"hooks ({HOOKS_VERSION}): {len(have)} cached, {len(todo)} to do", flush=True)

    def one(item):
        ch, r = item
        if run.stop.is_set():
            return
        unit, days = f"chapter:{ch['n']}", ch["days"]
        text = rewritten(accepted, unit, "text", r["text"])
        title = rewritten(accepted, unit, "title", r["title"])
        nmap = {c["n"]: c["day"] for c in r["cites"]}
        prompt = HOOKS_PROMPT.format(
            n=ch["n"], total=len(bundle.chapters), title=title, first_day=days[0]["day"], last_day=days[-1]["day"],
            first_date=days[0].get("date"), last_date=days[-1].get("date"), goal=ch["goal"], text=text,
            days="\n".join(f"[{n}] day {d}" for n, d in sorted(nmap.items())), hook_lo=HOOK_WORDS[0],
            hook_hi=HOOK_WORDS[1], beat_lo=BEAT_COUNT[0], beat_hi=BEAT_COUNT[1], beat_words=BEAT_WORDS,
            value_chars=FIGURE_VALUE_CHARS, label_words=FIGURE_LABEL_WORDS, beat_marks=BEAT_MARKS)
        t0, calls, tin, tout, task = time.time(), 0, 0, 0, TASK_TEXT
        got, why = {}, {}
        for attempt in range(2):  # one retry for the fields that failed
            res = run.call(f"hooks {ch['n']}", prompt, task, SYSTEM_HOOKS)
            if res is None:
                return  # no reply (transport or stop): asked again on the next run
            calls, tin, tout = calls + 1, tin + res["in"], tout + res["out"]
            ok, why = scroll_fields(parse_object(res["text"]), text, nmap, bundle.names.values())
            for f, v in ok.items():
                got.setdefault(f, v)
            why = {f: w for f, w in why.items() if f not in got}
            if not why:
                break
            print(f"hooks {ch['n']}: attempt {attempt + 1}: {'; '.join(why.values())}", flush=True)
            task = retry_task("; ".join(why.values()) + ". Send the whole JSON object again")
        run.append(path, {"n": ch["n"], "goal": ch["goal"], "src": text_sha(r["text"]), "hook": got.get("hook"),
                          "beats": got.get("beats"), "figure": got.get("figure"),
                          "figure_none": "figure" in got and got["figure"] is None, "why": why,
                          "prompt": HOOKS_VERSION, "calls": calls, "in": tin, "out": tout,
                          "seconds": round(time.time() - t0, 1), "model": MODEL})

    run.pool(one, todo)
    status(a, bundle)


# ---------------------------------------------------------------- step 2c: one sentence per talking pair per day

def pair_name(bundle: Bundle, agent: str) -> str:
    return "the humans" if agent == "human" else bundle.names.get(agent, agent)


def pair_rows(a, builder, wanted: set) -> dict:
    """{(day, a, b): [(time, epoch, speaker, room, text, ref)]} for the wanted pair keys: the chat rows that made the
    day's edge between the two, as the bundler counts them (build_bundle.py pass_slice): a row by one that names
    the other (the agent-name matcher), and both rows of a reply (the next message in the same room within
    REPLY_WINDOW_S from the other). Each row once, in time order."""
    names, alias = builder.names, getattr(builder, "room_alias", {})
    days = {k[0] for k in wanted}
    members = collections.defaultdict(set)
    for d, x, y in wanted:
        members[d] |= {x, y}
    info, chats = {}, []
    out = collections.defaultdict(dict)
    with open(os.path.join(a.root, "slice", "slice-chat.jsonl"), "rb") as f:
        for raw in f:
            try:
                r = json.loads(raw)
            except ValueError:
                continue
            rid, day = r.get("id"), r.get("day")
            agent = "human" if r.get("speaker") == "human" else r.get("agent")
            if not rid or not agent or r.get("kind") not in MENTION_KINDS:
                continue
            if agent not in builder.agent_ix and re.sub(r"-\d+$", "", agent) in builder.agent_ix:
                agent = re.sub(r"-\d+$", "", agent)
            room = r.get("room")
            room = alias.get(str(room), str(room)) if room is not None else None
            e, text = epoch(r.get("time")), r.get("text") or ""
            if room is not None and e is not None:
                chats.append((room, e, agent, day, rid))
            if day in days and agent in members[day] and text.strip():
                info[rid] = ((r.get("time") or "")[11:16], e or 0, agent, room or "", text)
                for t in names.agents_in(text) - {agent}:
                    key = pair_key(day, agent, t)
                    if key in wanted:
                        out[key][rid] = info[rid]
    chats.sort()
    for prev, cur in zip(chats, chats[1:]):
        if prev[0] == cur[0] and cur[2] != prev[2] and cur[1] - prev[1] <= REPLY_WINDOW_S:
            key = pair_key(cur[3], cur[2], prev[2])
            if key in wanted:
                for row in (prev, cur):
                    if row[4] in info:
                        out[key][row[4]] = info[row[4]]
    return {k: sorted(((*v, rid) for rid, v in rows.items()), key=lambda x: (x[1], x[5])) for k, rows in out.items()}


def pair_material(bundle: Bundle, rows: list) -> tuple:
    """Numbered lines ([n] HH:MM speaker (#room): text) and {n: ref}; over PAIR_ROWS rows, the PAIR_ROWS rows nearest
    the middle of the clock hour that holds most of them, back in time order."""
    if len(rows) > PAIR_ROWS:
        hours = collections.Counter(int(r[1] // 3600) for r in rows)
        mid = (hours.most_common(1)[0][0] + 0.5) * 3600
        rows = sorted(sorted(rows, key=lambda r: abs(r[1] - mid))[:PAIR_ROWS], key=lambda r: (r[1], r[5]))
    lines, nmap = [], {}
    for n, (hhmm, _, agent, room, text, ref) in enumerate(rows, 1):
        who = "a human" if agent == "human" else bundle.names.get(agent, agent)
        text = fold(text)
        text = text if len(text) <= PAIR_LINE_CHARS else text[:PAIR_LINE_CHARS].rsplit(" ", 1)[0] + " …"
        lines.append(f"[{n}] {hhmm or '--:--'} {who} (#{room}): {text}")
        nmap[n] = ref
    return "\n".join(lines), nmap


def pair_reply(reply, nmap: dict, names: tuple) -> tuple:
    """(text, [refs], None) for a reply that passes, else (None, None, why). The text gets straight quotes and no
    dashes; it must name both members; cites are line numbers in the table (a ref from it is taken as is)."""
    if reply is None:
        return None, None, "the reply was not one JSON object"
    text = reply.get("text")
    if not isinstance(text, str):
        return None, None, "the reply has no text"
    text = plain(re.sub(r"(?<=\d)\s*[–—]\s*(?=\d)", "-", text)).strip().strip("'")
    why = pair_problem(text)
    if why:
        return None, None, why
    low = text.lower()
    for nm in names:
        if (nm == "the humans" and "human" not in low) or (nm != "the humans" and nm.lower() not in low):
            return None, None, f'the sentence must name {nm} as "{nm}"'
    refs, by_ref = [], {v: v for v in nmap.values()}
    cites = reply.get("cites")
    for c in cites if isinstance(cites, list) else [cites]:
        m = re.fullmatch(r"\[?(\d+)\]?", str(c).strip())
        ref = nmap.get(int(m.group(1))) if m else by_ref.get(str(c).strip())
        if ref is None:
            return None, None, f"cite {c!r} is not a line number in the list"
        if ref not in refs:
            refs.append(ref)
    if not 1 <= len(refs) <= PAIR_CITES:
        return None, None, f"give 1 to {PAIR_CITES} line numbers"
    return text, refs, None


def cmd_pairs(a, bundle: Bundle, run: Runner):
    """Per day, the page's talking pairs (day_pairs over the served edges): one sentence each from the chat rows
    between the two that day. One call and one retry; after that the record is saved with text null and its reason
    (the page falls back). Keyed by day, the pair and PAIRS_VERSION."""
    path = os.path.join(a.book, "pairs.jsonl")
    edges = read_json(os.path.join(a.data, "bundle.json"))["views"]["interactions"]["edges"]
    top = day_pairs(edges)
    have = {(*pair_key(r["day"], r["a"], r["b"]), r.get("prompt")) for r in read_jsonl(path)}
    keys = [pair_key(d["day"], x, y) for d in bundle.days for x, y, _ in top.get(d["day"], [])]
    weight = {pair_key(d, x, y): w for d, ps in top.items() for x, y, w in ps}
    todo = [k for k in keys if (*k, PAIRS_VERSION) not in have]
    if a.only:
        only = {int(x) for x in a.only.split(",") if x.strip()}
        todo = [k for k in todo if k[0] in only]
    if a.limit:
        todo = todo[:a.limit]
    print(f"pairs ({PAIRS_VERSION}): {len(keys)} pairs on {len(top)} days; {len(keys) - len(todo)} cached or skipped, "
          f"{len(todo)} to do; reading the chat slice", flush=True)
    t0 = time.time()
    rows = pair_rows(a, load_builder(a), set(todo))
    print(f"pairs: rows for {len(rows)} of {len(todo)} pairs ({sum(len(v) for v in rows.values())} rows) in "
          f"{time.time() - t0:.0f} s", flush=True)

    def one(key):
        if run.stop.is_set():
            return
        day, x, y = key
        base = {"day": day, "a": x, "b": y, "w": weight[key], "prompt": PAIRS_VERSION, "model": MODEL}
        if not rows.get(key):
            run.append(path, {**base, "text": None, "cites": [], "why": "no chat rows between the two", "rows": 0,
                              "calls": 0})
            return
        material, nmap = pair_material(bundle, rows[key])
        names = (pair_name(bundle, x), pair_name(bundle, y))
        prompt = PAIRS_PROMPT.format(day=day, date=bundle.day_by_num[day].get("date"), goal=bundle.goal_of(day),
                                     a=names[0], b=names[1], lo=PAIR_WORDS[0], hi=PAIR_WORDS[1], cites=PAIR_CITES,
                                     material=material)
        t1, calls, tin, tout, task, why = time.time(), 0, 0, 0, TASK_TEXT, None
        for attempt in range(2):  # one retry on a schema failure
            res = run.call(f"pair {day} {x}|{y}", prompt, task, SYSTEM_PAIRS)
            if res is None:
                return  # no reply (transport or stop): asked again on the next run
            calls, tin, tout = calls + 1, tin + res["in"], tout + res["out"]
            text, refs, why = pair_reply(parse_object(res["text"]), nmap, names)
            if why is None:
                run.append(path, {**base, "text": text, "cites": refs, "why": None, "rows": len(rows[key]),
                                  "lines": len(nmap), "calls": calls, "in": tin, "out": tout,
                                  "seconds": round(time.time() - t1, 1)})
                return
            print(f"pair {day} {x}|{y}: attempt {attempt + 1}: {why}", flush=True)
            task = retry_task(why + ". Send the whole JSON object again")
        run.append(path, {**base, "text": None, "cites": [], "why": why, "rows": len(rows[key]), "lines": len(nmap),
                          "calls": calls, "in": tin, "out": tout, "seconds": round(time.time() - t1, 1)})
# ---------------------------------------------------------------- step 2d: the stage direction per chapter

def stage_label(x):
    """A label as the page wants it: plain, no quotes or closing full stop; an empty or "null" label is null."""
    if x is None or not isinstance(x, str):
        return x
    x = plain(x).strip().strip("\"'").rstrip(".").strip()
    return None if not x or x.lower() in ("null", "none") else x


def stage_fields(reply, text: str, beats: int, agents: dict) -> tuple:
    """(stage, None) when the reply passes the bundler's `stage_problem` and book.py's stricter rules (each prop
    kind once, at most one moment per beat, no label naming an agent), else (None, why). Kinds are lowercased, an agent named by its name gets
    its id, a beat written as digits becomes a number, a missing "moments" is none."""
    if reply is None:
        return None, "the reply was not one JSON object"
    by_name = {v.lower(): k for k, v in agents.items()}

    def agent(a):
        return by_name.get(a.strip().lower(), a.strip()) if isinstance(a, str) else a

    def beat(b):
        return int(b) if isinstance(b, str) and b.strip().isdigit() else b

    def kind(k):
        return k.strip().lower() if isinstance(k, str) else k
    props, moments = reply.get("props"), reply.get("moments", [])
    if isinstance(props, list):
        props = [{"kind": kind(p.get("kind")), "label": stage_label(p.get("label"))} if isinstance(p, dict) else p
                 for p in props]
    if isinstance(moments, list):
        moments = [{"beat": beat(m.get("beat")), "kind": kind(m.get("kind")),
                    "agents": list(dict.fromkeys(agent(a) for a in m.get("agents") or []))
                    if isinstance(m.get("agents") or [], list) else m.get("agents"),
                    "label": stage_label(m.get("label"))} if isinstance(m, dict) else m for m in moments]
    stage = {"props": props, "moments": moments}
    why = stage_problem(stage, beats, set(agents), text)
    if why:
        return None, why
    kinds = [p["kind"] for p in props]
    if len(set(kinds)) != len(kinds):
        return None, "two props have the same kind; give each kind at most once"
    on = [m["beat"] for m in moments]
    twice = sorted({b for b in on if on.count(b) > 1})
    if twice:
        return None, f"two moments are on beat {twice[0]}; give at most one moment per beat, the one that matters most"
    names = {n.lower() for n in agents.values()} | {i.lower() for i in agents}
    for x in props + moments:
        if x["label"] and any(re.search(rf"(?<![\w-]){re.escape(n)}(?![\w-])", x["label"].lower()) for n in names):
            return None, f"the label {x['label']!r} names an agent; name the thing, or null"
    return stage, None


def stage_input(bundle: Bundle, accepted: dict, ch: dict, r: dict, h: dict | None) -> dict:
    """What one stage call is made from: the served paragraph and beats (after their accepted rewordings, markers
    taken out), the chapter's active agents (as the bundler lists them) and their names."""
    unit = f"chapter:{ch['n']}"
    beats = [rewritten(accepted, unit, f"beat:{i}", b["text"]) for i, b in enumerate((h or {}).get("beats") or [], 1)]
    ids = sorted({a for d in ch["days"] for a in d["active_agents"]} - {"human"})
    return {"text": rewritten(accepted, unit, "text", r["text"]), "title": rewritten(accepted, unit, "title", r["title"]),
            "beats": beats, "agents": {i: bundle.names.get(i, i) for i in ids}}


def cmd_stage(a, bundle: Bundle, run: Runner):
    """Per chapter with beats: props and moments from the page's catalogue. One call, one retry with the reason; a
    stage that fails twice is saved as null with its reason (the page draws the board alone). A record is keyed by
    the hash of the chapter text and of the beats it was made from, so new beats are asked again."""
    path = os.path.join(a.book, "stage.jsonl")
    chapters = {r["n"]: r for r in read_jsonl(os.path.join(a.book, "chapters.jsonl"))}  # the last line, as served
    hooks = {(r["n"], r["src"]): r for r in read_jsonl(os.path.join(a.book, "hooks.jsonl"))}  # the last line wins
    have = {(r["n"], r["src"], r["beats_src"]) for r in read_jsonl(path) if r.get("prompt") == STAGE_VERSION}
    accepted = accepted_rewrites(a.book)
    todo = []
    for c in bundle.chapters:
        r = chapters.get(c["n"])
        if r is None or r.get("goal") != c["goal"]:
            continue
        h = hooks.get((c["n"], text_sha(r["text"])))
        key = (c["n"], text_sha(r["text"]), stage_key((h or {}).get("beats")))
        if not (h or {}).get("beats") or key in have:
            continue
        todo.append((c, r, h, key))
    if a.only:
        only = {int(x) for x in a.only.split(",") if x.strip()}
        todo = [t for t in todo if t[0]["n"] in only]
    if a.limit:
        todo = todo[:a.limit]
    print(f"stage ({STAGE_VERSION}): {len(have)} cached, {len(todo)} to do", flush=True)

    def one(item):
        ch, r, h, key = item
        if run.stop.is_set():
            return
        days = ch["days"]
        x = stage_input(bundle, accepted, ch, r, h)
        prompt = STAGE_PROMPT.format(
            n=ch["n"], total=len(bundle.chapters), title=x["title"], first_day=days[0]["day"],
            last_day=days[-1]["day"], goal=ch["goal"], text=MARK_RE.sub("", x["text"]).replace(" .", "."),
            agents="\n".join(f"{i}: {n}" for i, n in x["agents"].items()) or "(none)",
            beats="\n".join(f"{i}: {MARK_RE.sub('', b).strip().replace(' .', '.')}" for i, b in enumerate(x["beats"])),
            prop_lo=STAGE_PROP_COUNT[0], prop_hi=STAGE_PROP_COUNT[1], moment_max=STAGE_MOMENT_MAX,
            agents_max=STAGE_AGENTS_MAX, label_words=STAGE_LABEL_WORDS)
        t0, calls, tin, tout, task = time.time(), 0, 0, 0, TASK_TEXT
        stage, why = None, None
        for attempt in range(2):  # one retry with the reason
            res = run.call(f"stage {ch['n']}", prompt, task, SYSTEM_STAGE)
            if res is None:
                return  # no reply (transport or stop): asked again on the next run
            calls, tin, tout = calls + 1, tin + res["in"], tout + res["out"]
            stage, why = stage_fields(parse_object(res["text"]), x["text"], len(x["beats"]), x["agents"])
            if why is None:
                break
            print(f"stage {ch['n']}: attempt {attempt + 1}: {why}", flush=True)
            task = retry_task(why + ". Send the whole JSON object again")
        run.append(path, {"n": ch["n"], "goal": ch["goal"], "src": key[1], "beats_src": key[2], "stage": stage,
                          "why": why, "prompt": STAGE_VERSION, "calls": calls, "in": tin, "out": tout,
                          "seconds": round(time.time() - t0, 1), "model": MODEL})

    run.pool(one, todo)
    status(a, bundle)


# ---------------------------------------------------------------- step 3: goal adherence

def parse_array(text: str):
    """The whole reply as a JSON array, or None (an object around it or prose beside it is a bad reply)."""
    try:
        out = json.loads(text.strip())
    except ValueError:
        return None
    return out if isinstance(out, list) else None


def check_batch(reply, n: int):
    """The reply as {i: (score, note)} when it has exactly n well-formed items 0..n-1, else None."""
    if reply is None or len(reply) != n:
        return None
    out = {}
    for x in reply:
        if not isinstance(x, dict) or x.get("score") not in SCORES or type(x.get("i")) is not int:
            return None
        note = x.get("note")
        if not isinstance(note, str):
            return None
        out[x["i"]] = (x["score"], " ".join(note.split()[:NOTE_WORDS]))
    return out if set(out) == set(range(n)) else None


def cmd_adherence(a, bundle: Bundle, run: Runner):
    path = os.path.join(a.book, "adherence.jsonl")
    null_path = os.path.join(a.book, "adherence-null.jsonl")
    have = {r["id"] for r in read_jsonl(path)}
    recs = [r for day in sorted(bundle.agent_recs) for r in sorted(bundle.agent_recs[day], key=lambda r: r["id"])]
    todo = [r for r in recs if r["id"] not in have]
    batches = [todo[k:k + BATCH] for k in range(0, len(todo), BATCH)]
    if a.limit:
        batches = batches[:a.limit]
    print(f"adherence: {len(have)} of {len(recs)} cached, {len(batches)} batches to do", flush=True)

    def one(batch):
        if run.stop.is_set():
            return
        stdin = "\n".join(json.dumps({"i": i, "agent": bundle.names.get(r["key"], r["key"]),
                                      "goal": bundle.goal_of(r["day"]),
                                      "summary": fold(re.sub(r"\s*\[\d+\]", "", r["text"]))}, ensure_ascii=False)
                          for i, r in enumerate(batch))
        task = ADHERENCE_TASK.format(n=len(batch))
        t0 = time.time()
        for attempt in range(2):  # a bad batch is retried once
            res = run.call(f"adherence {batch[0]['id']}", stdin, task, SYSTEM_JSON)
            if res is None:
                return
            got = check_batch(parse_array(res["text"]), len(batch))
            if got is not None:
                secs = round(time.time() - t0, 1)
                run.append_lines(path, [{"id": r["id"], "agent": r["key"], "day": r["day"], "score": got[i][0],
                                         "note": got[i][1], "seconds": secs} for i, r in enumerate(batch)])
                return
            print(f"adherence {batch[0]['id']}: attempt {attempt + 1} bad reply (count or JSON); "
                  f"{'retrying' if attempt == 0 else 'items left null'}", flush=True)
        with run.lock:
            run.stats["null_batches"] += 1
        run.append_lines(null_path, [{"id": r["id"], "agent": r["key"], "day": r["day"]} for r in batch])

    run.pool(one, batches)
    status(a, bundle)


# ---------------------------------------------------------------- rephrase: sentences the redaction would cut

def load_builder(a):
    """The bundler after load_meta: its name matcher, agents and room aliases, from the same meta/ lists the build
    reads."""
    argv = ["--root", a.root] + (["--meta", a.meta] if a.meta else []) + \
        (["--common-words", a.common_words] if a.common_words else [])
    b = BB.Builder(BB.build_parser().parse_args(argv))
    b.load_meta()
    return b


def load_names(a):
    """The bundler's name matcher, from the same meta/ lists the build reads."""
    return load_builder(a).names


def chips(text: str) -> int:
    return len(CHIP_RE.findall(text))


def sentences(text: str) -> list:
    return [x for x in SENTENCE_END_RE.split(text) if x]


def cut_words(text: str, red: str) -> list:
    """The words of `text` that the redaction replaced in `red`."""
    a, b = text.split(), red.split()
    out = []
    for op, i1, i2, _, _ in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op in ("replace", "delete"):
            out += [w.strip(".,;:!?\"“”'’()") for w in a[i1:i2] if not MARK_RE.fullmatch(w)]
    return [w for w in dict.fromkeys(out) if w]


def cmd_rephrase(a, bundle: Bundle, run: Runner):
    names = load_names(a)

    def redact(t):  # the bundler's rules for book text: untagged
        return BB.redact_plain(t, names)

    path = os.path.join(a.book, "rewrites.jsonl")
    done = {(r["unit"], r["field"], r["src"]) for r in read_jsonl(path)}
    accepted = accepted_rewrites(a.book)

    def current(unit, field, text):  # the text after the rewrites already accepted (as the bundler applies them)
        return rewritten(accepted, unit, field, text)

    days = {r["day"]: r for r in read_jsonl(os.path.join(a.book, "days.jsonl"))}  # the last line wins
    chapters = {r["n"]: r for r in read_jsonl(os.path.join(a.book, "chapters.jsonl"))}
    notes = {r["id"]: r for r in read_jsonl(os.path.join(a.book, "adherence.jsonl"))}
    units = [(f"day:{d}", [("text", r["text"])]) for d, r in sorted(days.items()) if not r.get("empty")]
    units += [(f"chapter:{n}", [("title", r["title"]), ("text", r["text"])]) for n, r in sorted(chapters.items())]
    # the scroll fields of the chapter text that is served (book.py hooks)
    hooks = {(r["n"], r["src"]): r for r in read_jsonl(os.path.join(a.book, "hooks.jsonl"))}
    for n, r in sorted(chapters.items()):
        h = hooks.get((n, text_sha(r["text"])))
        if h:
            fields = [("hook", h["hook"])] if h.get("hook") else []
            fields += [(f"beat:{i}", b["text"]) for i, b in enumerate(h.get("beats") or [], 1)]
            fields += [("figure_label", h["figure"]["label"])] if h.get("figure") else []
            units.append((f"chapter:{n}", fields))
    units += [(f"adherence:{i}", [("note", r["note"])]) for i, r in sorted(notes.items()) if r.get("note")]
    # the pair sentences (book.py pairs), the record the bundler serves per pair
    pairs = pick_pairs(read_jsonl(os.path.join(a.book, "pairs.jsonl")))
    units += [(f"pair:{d}:{x}:{y}", [("text", r["text"])]) for (d, x, y), r in sorted(pairs.items())
              if isinstance(r.get("text"), str)]
    found = []
    for unit, fields in units:
        work = []
        for field, text in fields:
            text = current(unit, field, text)
            if (unit, field, text_sha(text)) in done or chips(redact(text)) <= chips(text):
                continue
            parts = [text] if field in ("title", "note", "hook", "figure_label") else sentences(text)
            items = [(p, cut_words(p, redact(p))) for p in parts if chips(redact(p)) > chips(p)]
            work.append((field, text, items))
        if work:
            found.append((unit, work))
    # one call per day or chapter; notes go 25 to a call
    jobs = [[u] for u in found if not u[0].startswith(("adherence:", "pair:"))]
    note_units = [u for u in found if u[0].startswith("adherence:")]
    jobs += [note_units[k:k + BATCH] for k in range(0, len(note_units), BATCH)]
    pair_units = [u for u in found if u[0].startswith("pair:")]  # one sentence each: 25 to a call, like notes
    jobs += [pair_units[k:k + BATCH] for k in range(0, len(pair_units), BATCH)]
    print(f"rephrase: {sum(len(w) for _, w in found)} texts in {len(found)} units would get a chip their own text "
          f"does not carry ({len(note_units)} notes); {len(jobs)} calls", flush=True)

    def one(job):
        items = [(unit, field, p, cut) for unit, work in job for field, _, its in work for p, cut in its]
        stdin = "\n".join(json.dumps({"i": i, "kind": "title" if f == "title" else "note" if f in ("note", "figure_label")
                                      else "sentence",
                                      "text": p, "remove": cut}, ensure_ascii=False)
                          for i, (_, f, p, cut) in enumerate(items))
        t0 = time.time()
        res = run.call(f"rephrase {job[0][0]}", stdin, REPHRASE_TASK.format(n=len(items)), SYSTEM_JSON) if items else None
        if items and res is None:
            return  # no reply (transport or stop): tried again on the next run
        reply = parse_array(res["text"]) if res else None
        ok = reply is not None and len(reply) == len(items) and all(isinstance(x, str) for x in reply)
        recs = []
        for unit, work in job:
            for field, text, its in work:
                out, reason = text, None
                if not its:
                    reason = "no single sentence carries the chip"
                elif not ok:
                    reason = "bad reply"
                else:
                    for i, (u, f, p, _) in enumerate(items):
                        if (u, f) != (unit, field):
                            continue
                        q = fold(reply[i])
                        if MARK_RE.findall(q) != MARK_RE.findall(p) or (f == "title" and not 0 < len(q.split()) <= 10) \
                                or (f == "hook" and not HOOK_WORDS[0] <= len(q.split()) <= HOOK_WORDS[1]) \
                                or (f == "figure_label" and not 0 < len(q.split()) <= FIGURE_LABEL_WORDS) \
                                or (u.startswith("pair:") and pair_problem(q) is not None):
                            reason = "cite numbers or title length changed"
                            break
                        out = out.replace(p, q, 1)
                    if reason is None and (chips(redact(out)) > chips(out)
                                           or set(MARK_RE.findall(out)) != set(MARK_RE.findall(text))):
                        reason = "the rewording still gets a chip"
                recs.append({"unit": unit, "field": field, "src": text_sha(text), "accepted": reason is None,
                             "text": out if reason is None else None, "reason": reason, "items": len(its),
                             "calls": 1 if res else 0, "seconds": round(time.time() - t0, 1), "model": MODEL})
        if res:
            recs[0].update({"in": res["in"], "out": res["out"]})
        run.append_lines(path, recs)
        acc = sum(r["accepted"] for r in recs)
        print(f"rephrase {job[0][0]}{f' +{len(job) - 1} units' if len(job) > 1 else ''}: {acc} of {len(recs)} "
              f"accepted", flush=True)

    run.pool(one, jobs)
    status(a, bundle)


# ---------------------------------------------------------------- status

def hooks_status(a) -> dict:
    """Counts of the scroll fields for the chapter texts the bundler serves (the last line per chapter)."""
    chapters = {r["n"]: r for r in read_jsonl(os.path.join(a.book, "chapters.jsonl"))}
    hooks = {(r["n"], r["src"]): r for r in read_jsonl(os.path.join(a.book, "hooks.jsonl"))}
    cur = [h for n, r in chapters.items() if (h := hooks.get((n, text_sha(r["text"]))))]
    why = collections.Counter(f for h in cur for f in h.get("why", {}))
    return {"chapters": len(cur), "of": len(chapters), "hooks": sum(1 for h in cur if h.get("hook")),
            "beats": sum(1 for h in cur if h.get("beats")), "figures": sum(1 for h in cur if h.get("figure")),
            "figure_none": sum(1 for h in cur if h.get("figure_none")), "null_after_retry": dict(why),
            "calls": sum(h.get("calls", 0) for h in cur)}


def pairs_status(a) -> dict:
    """Counts of the pair sentences the bundler would serve (per pair the last record with a text)."""
    recs = read_jsonl(os.path.join(a.book, "pairs.jsonl"))
    cur = pick_pairs(recs).values()
    return {"pairs": len(cur), "with_text": sum(1 for r in cur if isinstance(r.get("text"), str)),
            "null": dict(collections.Counter(r.get("why") for r in cur if not isinstance(r.get("text"), str))),
            "by_prompt": dict(collections.Counter(r.get("prompt") for r in recs)),
            "calls": sum(r.get("calls", 0) for r in recs), "tokens_in": sum(r.get("in", 0) for r in recs),
            "tokens_out": sum(r.get("out", 0) for r in recs)}
def stage_status(a) -> dict:
    """Counts of the stage directions for the chapter texts and beats the bundler serves."""
    chapters = {r["n"]: r for r in read_jsonl(os.path.join(a.book, "chapters.jsonl"))}
    hooks = {(r["n"], r["src"]): r for r in read_jsonl(os.path.join(a.book, "hooks.jsonl"))}
    stage = {(r["n"], r["src"], r["beats_src"]): r for r in read_jsonl(os.path.join(a.book, "stage.jsonl"))}
    cur = []
    for n, r in chapters.items():
        src = text_sha(r["text"])
        s = stage.get((n, src, stage_key((hooks.get((n, src)) or {}).get("beats"))))
        if s:
            cur.append(s)
    st = [s["stage"] for s in cur if s.get("stage")]
    return {"chapters": len(cur), "of": len(chapters), "with_stage": len(st), "null_after_retry": len(cur) - len(st),
            "props": sum(len(x["props"]) for x in st), "moments": sum(len(x["moments"]) for x in st),
            "kinds": dict(collections.Counter(p["kind"] for x in st for p in x["props"] + x["moments"]).most_common()),
            "calls": sum(s.get("calls", 0) for s in cur)}


def status(a, bundle: Bundle):
    days = list({r["day"]: r for r in read_jsonl(os.path.join(a.book, "days.jsonl"))}.values())  # the last line wins
    chapters = list({r["n"]: r for r in read_jsonl(os.path.join(a.book, "chapters.jsonl"))}.values())
    adh = list({r["id"]: r for r in read_jsonl(os.path.join(a.book, "adherence.jsonl"))}.values())
    nulls = {r["id"] for r in read_jsonl(os.path.join(a.book, "adherence-null.jsonl"))} - {r["id"] for r in adh}
    rew = read_jsonl(os.path.join(a.book, "rewrites.jsonl"))
    n_recs = sum(len(v) for v in bundle.agent_recs.values())
    out = {"days": {"cached": len({r["day"] for r in days}), "of": len(bundle.days),
                    "empty": sum(1 for r in days if r.get("empty") and not r.get("placement")),
                    "gap_paragraphs": sum(1 for r in days if r.get("gap")),
                    "placements": sum(1 for r in days if r.get("placement")),
                    "uncited": sum(1 for r in days if r.get("uncited")),
                    "cites": sum(len(r["cites"]) for r in days), "dropped": sum(r.get("dropped", 0) for r in days),
                    "calls": sum(r.get("calls", 0) for r in days), "tokens_in": sum(r.get("in", 0) for r in days),
                    "tokens_out": sum(r.get("out", 0) for r in days)},
           "chapters": {"cached": len({r["n"] for r in chapters}), "of": len(bundle.chapters),
                        "by_prompt": dict(collections.Counter(r.get("prompt", "v1") for r in chapters)),
                        "days_without_goal_attached": bundle.attached,
                        "cites": sum(len(r["cites"]) for r in chapters),
                        "dropped": sum(r.get("dropped", 0) for r in chapters),
                        "calls": sum(r.get("calls", 0) for r in chapters)},
           "adherence": {"cached": len({r["id"] for r in adh}), "of": n_recs, "null_pending": len(nulls),
                         "scores": dict(collections.Counter(r["score"] for r in adh).most_common())},
           "hooks": hooks_status(a),
           "pairs": pairs_status(a),
           "stage": stage_status(a),
           "rephrase": {"accepted": sum(1 for r in rew if r.get("accepted")),
                        "kept_redacted": dict(collections.Counter(r["reason"] for r in rew if not r.get("accepted"))),
                        "calls": sum(r.get("calls", 0) for r in rew)}}
    print(json.dumps(out, indent=1), flush=True)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=("days", "gaps", "chapters", "hooks", "pairs", "stage", "adherence", "rephrase", "status"))
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--data", help="served bundle to read (default <root>/bundle/data)")
    ap.add_argument("--book", help="cache dir (default <root>/book)")
    ap.add_argument("--meta", help="`rephrase`: the redaction lists (default <root>/meta)")
    ap.add_argument("--common-words", help="`rephrase`: JSON {word: zipf} instead of wordfreq (tests)")
    ap.add_argument("--prime-agent", default="prime-agent")
    ap.add_argument("--provider", default="cpa-r")
    ap.add_argument("--thinking", default="low")
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--parallel", type=int, default=8)
    ap.add_argument("--max-attempts", type=int, default=5, help="tries per call on transport errors")
    ap.add_argument("--chapter-prompt", choices=sorted(CHAPTER_PROMPTS), default="story-v2",
                    help="`chapters`: prompt version; a chapter is done when it has an accepted reply under it")
    ap.add_argument("--only", help="`chapters`, `hooks`, `stage`: comma list of chapter numbers to ask; `pairs`: of day "
                    "numbers (default all not done)")
    ap.add_argument("--content-attempts", type=int, default=3,
                    help="tries per day or chapter until a reply passes (the retry says what was wrong)")
    ap.add_argument("--limit", type=int, help="at most this many new units (days, chapters or batches)")
    a = ap.parse_args(argv)
    a.data = a.data or os.path.join(a.root, "bundle", "data")
    a.book = a.book or os.path.join(a.root, "book")
    os.makedirs(a.book, exist_ok=True)
    a.call_cwd = os.path.join(a.book, ".cwd")
    os.makedirs(a.call_cwd, exist_ok=True)
    bundle = Bundle(a.data)
    if a.step == "status":
        status(a, bundle)
        return
    {"days": cmd_days, "gaps": cmd_gaps, "chapters": cmd_chapters, "hooks": cmd_hooks, "pairs": cmd_pairs, "stage": cmd_stage,
     "adherence": cmd_adherence, "rephrase": cmd_rephrase}[a.step](a, bundle, Runner(a))


if __name__ == "__main__":
    main()
