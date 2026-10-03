#!/usr/bin/env python3
"""Build the served swarm bundle (docs/schema.md section 4).

Reads meta/, slice/, tags/ and summaries/ under --root and writes --out (default <root>/bundle/data).
Every input except meta/ may be missing or partial: the matching bundle parts are then empty but valid.
The new bundle is written beside the old one and swapped in at the end, so a running server never
sees a half-written bundle. Build statistics go to <out>/../BUILD.json (not served).

Passes:
  1. slice: counts, agent and room stats, chat order for replies, name mentions, memory rows
  A. tags:  entity key counts, label series, mood, span mentions
  B. tags:  per-entity detail for the top keys, example candidates, memory entity sets
  C. tags:  spans of the rows the bundle quotes (only those lines are parsed)
  2. slice: text of the rows the bundle quotes (only those lines are parsed)
"""
from __future__ import annotations

import argparse
import bisect
import collections
import datetime as dt
import glob
import hashlib
import json
import os
import re
import shutil
import sys
import time

try:
    import orjson

    def loads(b):
        return orjson.loads(b)

    def dumps(obj) -> bytes:
        return orjson.dumps(obj)
except ImportError:  # stdlib fallback, same output
    def loads(b):
        return json.loads(b)

    def dumps(obj) -> bytes:
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode()


SCHEMA = "swarm-bundle/1"
DATASET = "aidigestorg/ai-village@838b4150"
CITE = "Data: AI Digest / AI Village (theaidigest.org/village)"
SUMMARIZER = "gpt-5.6-luna"
DEFAULT_ROOT = "/home/lexi/calc-nvme/hackathon-20261004"

TOP_ENTITIES = 2000
EXAMPLES = 5
SOURCE_CHARS = 600
EXCERPT_CHARS = 400
REPLY_WINDOW_S = 600
AGENT_TOP = 10
DAY_TOP = 5
REDACT_TOKEN = "[human]"
EMAIL_TOKEN = "[email]"
ID_TOKEN = "[id]"
VILLAGE_PREVIEW = 300
BUNDLE_BUDGET = 2_900_000  # bundle.json must stay under 3 MB
FILE_CAP = 2_000_000  # any on-demand file
DATA_CAP = 100_000_000  # all of data/
DEFAULT_CHECKPOINT = "rd16c-ch:0b5ff938e12463f8+vad:acbf6b43c6662813"
# oneiroNER rd16c-ch label set, in order (docs/schema.md section 2)
DEFAULT_LABELS = ["PERSON", "PLACE", "ORG", "DATE", "EVENT", "RELATIONSHIP_REF", "EMOTION", "MONEY", "PRODUCT"]

# Text a speaker shows to others: name mentions and turn mood come from these kinds only.
MENTION_KINDS = frozenset({"chat", "session_goal", "session_summary", "next_goal", "cc_text"})
THINK_KINDS = frozenset({"thinking", "cc_thinking"})
# Kinds that read well as an entity example; others are a fallback.
EXAMPLE_KINDS = frozenset({"chat", "session_goal", "session_summary", "next_goal", "history_query",
                           "outreach", "cc_text", "digest_summary"})

# Human names. A name with a word that is not a common English word matches in any case; a name made only of
# common words matches only capitalized. Common: wordfreq zipf (log10 uses per billion words) at or above
# COMMON_ZIPF, or in STOP_WORDS / NAME_WORDS.
COMMON_ZIPF = 3.5
NOT_PERSON = (5, 0.3)  # a common one-word name under 30% PERSON of >= 5 oneiroNER spans is not a person here
# A PERSON span made only of these words (or agent-name words and digits) is not a name: "a human", "Opus 4.5".
GENERIC_PERSON = frozenset("""
a an the one human humans user users person people someone somebody viewer viewers visitor visitors guest guests
""".split())
SUMMARY_TAGS = "tags-summaries.jsonl"  # oneiroNER tags over summary text (ids of summaries/*.jsonl)
EXTRA_IDS = "extra_human_ids.json"  # meta/: usernames, handles, email-like ids of individual humans (human_ids.py)
# @handles: an @-mention is a human identifier unless it is exactly an agent's display name or slug (any case, any of
# " -._" between its words: "@Agent Alpha", "@agent_alpha") or one of these public organisation accounts and domains
# (AI Digest / AI Village, the AI labs and charities the agents worked with). Nothing else is exempt: partial names and
# lookalikes ("@alpha", "@AgentAlpha", "@aidigest_fan", "@model123") are redacted.
PUBLIC_HANDLES = frozenset("""
aidigest aidigest_ theaidigest theaidigest.org aivillage aivillagenews agentvillage agentvillage.org
anthropicai openai googledeepmind googleai aiatmeta
fightingmalaria helenkellerintl malarianomore malariaconsortium givewell givingwhatwecan evidenceaction thegivingblock
github.com gmail.com
""".split())
# Agent family words: "@claude", "@opus", "@gemini" address agents, never a person (any case, the whole handle only)
AGENT_FAMILY_HANDLES = frozenset("""
claude opus sonnet haiku fable gpt gemini grok kimi deepseek glm o1 o3 o4 luna sol terra astra muse
""".split())
# Well-known code at-words (CSS at-rules, JSON-LD keys, decorators and annotations, JSDoc tags, npm scopes). Exempt
# only as the whole handle (any case) and only inside code: a `backtick` span (to the end of its line when unclosed)
# or a ``` fenced block (to the end of the text when unclosed). Anywhere else they are redacted like any handle.
CODE_AT_WORDS = frozenset("""
media import keyframes font-face supports charset layer container apply tailwind
context type id graph vocab
dataclass property staticmethod classmethod abstractmethod cached_property override wraps functools.wraps
app.route app.get app.post app.put app.delete router.get router.post pytest.fixture
component injectable input output test autowired getmapping postmapping
param returns return throws example deprecated
types babel angular vue mui testing-library playwright anthropic-ai tanstack radix-ui vercel nestjs sveltejs
modelcontextprotocol supabase octokit aws-sdk
state binding published observable observedobject environment mainactor objc escaping appstorage
""".split())
CODE_RE = re.compile(r"```.*?(?:```|\Z)|`[^`\n]*(?:`|$)", re.S | re.M)
AT_HANDLE_RE = re.compile(r"(?<![\w@.+-])@([A-Za-z0-9_](?:[A-Za-z0-9_.-]*[A-Za-z0-9_])?)")

# Identifier rules beyond names. Each match is cut with the token of its rule (RULE_TOKEN; [human] otherwise).
RULES = "extra_human_rules.json"
PRIVATE_RULES = frozenset({"extra", "exact", "extra_id", "cap"})  # explicit private ids: no public exemption applies  # meta/: exact-case names, ids, per-location tokens, dropped entities, profile allowlist
RULE_TOKEN = {"email_pct": EMAIL_TOKEN, "phone": ID_TOKEN, "order": ID_TOKEN, "extra_id": ID_TOKEN}
PCT_EMAIL_RE = re.compile(r"(?:(?<=%[0-9A-Fa-f]{2})|(?<![\w.+%-]))[\w.+-]+%(?:25)?40[\w-]+(?:\.[\w-]+)+")  # a%40b.org
# phone numbers: international (+44 20 7946 0958), (555) 201-7788, 555-201-7788 / 555.201.7788 / 555 201 7788, and
# ten digits in a row; a NANP (US/Canada) area code and exchange start with 2-9 (so a unix time such as 1727999999 is
# no phone); never inside a longer number, a version, a path or a time
PHONE_RE = re.compile(r"(?<![\w+$€£.,/:-])(?:\+\d{1,3}[ .-]?(?:\(\d{1,4}\)[ .-]?)?\d{1,4}(?:[ .-]\d{2,4}){1,4}"
                      r"|(?:1[ .-])?\([2-9]\d{2}\)[ .-]?[2-9]\d{2}[ .-]\d{4}|(?:1[ .-])?[2-9]\d{2}([ .-])[2-9]\d{2}\1\d{4}"
                      r"|[2-9]\d{2}[2-9]\d{6})(?!\w|[.,/:-]\d)")
# ten bare digits right after one of these are a CI, comment or message number, not a phone
TECH_NUMBER_RE = re.compile(r"(?i)(?:[#`=]|\b(?:pipelines?|comments?|notes?|jobs?|builds?|runs?|issues?|pr|mr|"
                            r"commits?|merge request|threads?|messages?|status|tweets?|posts?|latest|ids?)\b[^\n\d]{0,6})$")
# order, tracking, customer and user ids: a token with 5+ digits, at least 40% of it digits and no date in it, within
# 40 characters after one of these words
ORDER_CUE_RE = re.compile(r"(?i)\b(?:orders?|tracking|shipments?|customers?|user[ _-]?ids?|userid|account[ _-]?ids?|"
                          r"transactions?|txn|receipts?|confirmation)\b")
ORDER_ID_RE = re.compile(r"(?<![\w-])[A-Za-z0-9][A-Za-z0-9-]{3,}[A-Za-z0-9](?![\w-])")
DATE_LIKE_RE = re.compile(r"\d{4}-\d{2}-\d{2}|\d{2}-\d{2}-\d{4}")
# usernames without an @ next to these words (within 50 characters after, 30 before, same line)
HUMAN_CUE_RE = re.compile(r"(?i)\b(?:readers?|viewers?|customers?|contributors?|authors?|buyers?|donors?|volunteers?|"
                          r"recipients?|followers?|subscribers?|commenters?|correspondents?|players?)\b")
HUMAN_CUES = frozenset("""reader readers viewer viewers customer customers contributor contributors author authors buyer
buyers donor donors volunteer volunteers recipient recipients follower followers subscriber subscribers commenter
commenters correspondent correspondents player players""".split())
# a whole word of prose: never a piece of a URL, path, host, email, or of a [token]
WORD_TOKEN_RE = re.compile(r"(?<![\w@./:%\[-])[A-Za-z0-9][A-Za-z0-9_.'-]*[A-Za-z0-9](?![\w@/%\]-]|\.\w)")
USERNAME_RE = re.compile(r"[A-Za-z][A-Za-z_]{2,}\d{2,}")
NOT_USERNAME_RE = re.compile(r"(?i)v?\d+(?:\.\d+)+|\d+(?:st|nd|rd|th|am|pm|h|hr|hrs|m|min|s|ms|k|m|b|x|px|gb|mb|kb|"
                             r"tb|fps|p|d|w|y|yo)|[0-9a-f]{6,40}|\d+")
# a quoted search query ("query: "...""), where usernames and rare capitalized words are names too
QUERY_RE = re.compile(r"(?i)\b(?:query|queries|search(?:ed|es|ing)?|look(?:ed|ing)?\s+up|lookup)\b[^\n\"“]{0,20}?"
                      r"[\"“]([^\"”\n]{1,200})[\"”]")
# search operators naming a person: from:x, to:x, cc:x, bcc:x, author:x, user:x, sender:x
QUERY_OP_RE = re.compile(r"(?i)(?<![\w.])(?:from|to|cc|bcc|author|user|owner|sender|recipient):(\"[^\"\n]{1,60}\"|"
                         r"[^\s\"'()\[\],;]+)")
# a person search: mail or search words before the operator on its line, or a mail-search operator on the line
MAIL_SEARCH_RE = re.compile(r"(?i)\b(?:search(?:ed|es|ing)?|query|queries|inbox|gmail|mailbox|e-?mails?|mail|messages?|"
                            r"threads?|filter(?:ed|ing)?|sender|look(?:ed)?\s+up)\b")
MAIL_OP_RE = re.compile(r"(?i)(?<![\w.])(?:in|is|subject|label|has|newer_than|older_than|after|before|filename):\S")
QUERY_OP_KEEP = frozenset("me anyone everyone all inbox sent spam trash drafts unread read starred important * any "
                          "none noreply no-reply".split())
# salutations: "Hi X,", "Dear X Y", "Thanks X for"
SALUTE_RE = re.compile(r"(?:^|(?<=[\s>\"'(*_]))(?:Hi|Hello|Hey|Dear|Thanks|Thank you|Thx|Greetings|Cheers|Congrats|"
                       r"Congratulations|Good (?:morning|afternoon|evening))[ \t]*,?[ \t]+([A-Z][\w'’-]*)"
                       r"(?:[ \t]+([A-Z][\w'’-]*))?(?=[ \t]*(?:[,!.:;)\n—–-]|$)|[ \t]+[a-z])", re.M)
SALUTE_KEEP = frozenset("""
there world folks friends friend colleagues colleague customer customers sir madam team teams all everyone everybody
guys y'all again back so much ladies gentlemen judges reviewers organizers organisers humans human viewers readers
chat village agents agent bro dude boss crew gang squad fam diary community creatives helpdesk support admins mods
""".split())
# ALL-CAPS buyer names next to shipping or customer words (within 60 characters after, same line)
CAPS_CUE_RE = re.compile(r"(?i)\b(?:ship(?:ped)?[ -]to|deliver(?:y|ed)?[ -]to|bill(?:ing)?[ -]to|sold[ -]to|"
                         r"shipping (?:address|name|label)|recipient|addressee|buyer|customer(?: name)?)\b[ \t]*[:\-–—]?")
CAPS_NAME_RE = re.compile(r"(?<![\w-])[A-Z][A-Z'’-]+(?:[ \t]+[A-Z][A-Z'’-]*){1,2}(?![\w-])")
# short ALL-CAPS tech, protocol and other acronyms: never part of a name
CAPS_TECH = frozenset("""
http https get post put patch delete head options api apis json yaml xml html css url urls uri csv tsv pdf png jpg jpeg
gif svg mp3 mp4 sql ssh ssl tls dns ip tcp udp cli gui ui ux ai ml llm gpu cpu ram ssd hdd os ios mac pc sdk ide ci cd
pr qa eta faq ceo cto cfo coo vp hr pm am utc gmt pst est jst nyc sf id ids sms mms pin sku upc isbn vin vat gst cod
rss ftp smtp imap pop vpn lan wan usb hdmi rest crud jwt oauth sso mfa otp npm pip git aws gcp ssr seo cms crm erp
kpi roi b2b b2c saas paas iaas nft dao eth btc gpt rag nlp ocr tts stt asr
""".split())
CAPS_KEEP = frozenset("""
us usa uk eu llc inc ltd co corp po box apt ste st ave rd blvd dr ln ct hwy usps ups fedex dhl tracking order orders
shipped shipping delivered delivery confirmed pending paid unpaid fulfilled unfulfilled cancelled canceled refunded
new total subtotal tax discount code price item items qty sku size color colour black white red blue green
api url pdf usd eur gbp jpy tbd asap fyi ok done note important urgent fyi status name address email phone
customer customers buyer recipient ship bill sold to from the and of for in on at by with via
""".split())
# profile URLs: the host stays, the account segment becomes [human] unless allowlisted or a site route
PROFILE_RE = re.compile(r"(?i)(?<![\w.-])(?:https?://)?(?:www\.)?(?:(?:github|gitlab)\.com|(?:x|twitter)\.com|"
                        r"linkedin\.com/in|bsky\.app/profile|reddit\.com/(?:u|user)|dev\.to|substack\.com/@|medium\.com/@|"
                        r"calendly\.com|cal\.com|savvycal\.com|tidycal\.com|zcal\.co|youtube\.com/@|instagram\.com|"
                        r"tiktok\.com/@|threads\.net/@)/([A-Za-z0-9_](?:[A-Za-z0-9_.-]*[A-Za-z0-9_])?)")
SUBDOMAIN_RE = re.compile(r"(?i)(?<![\w.-])(?:https?://)?([A-Za-z0-9][A-Za-z0-9-]*)\.(?:github\.io|substack\.com|"
                          r"bsky\.social)\b")
PROFILE_ROUTES = frozenset("""
about features pricing login logout signup join settings orgs organizations topics explore marketplace sponsors
notifications search trending collections issues pulls new apps enterprise security site home i intent share hashtag
messages compose events groups company school jobs feed pub docs blog help support contact terms privacy policies
legal api graphql raw gist codespaces copilot discussions users dashboard profile account accounts en www open p
watch shorts reel reels explore stories direct oauth auth sessions password readme
""".split())
PROFILE_ALLOW = frozenset("ai-village-agents ai-village aidigest_ aidigest theaidigest".split())  # + meta profile_allow
# Words next to [human] that are no part of a name: roles and titles (the neighbour rule keeps them).
NEIGHBOUR_KEEP = frozenset("""
volunteer volunteers contributor contributors helper helpers author authors follower followers donor donors member
members commenter commenters moderator moderators organizer organiser staff judge reporter journalist researcher
professor doctor mx colleague partner ceo founder cofounder director manager editor host intern engineer developer
creator owner maintainer player players phd
""".split())
# Next to [human] without a nickname a capitalized common English word (zipf at or above NEIGHBOUR_ZIPF) is taken
# for a heading or a title word ("[human] Moon Mission"), except these frequent family names.
NEIGHBOUR_ZIPF = 4.0
SURNAMES = frozenset("""
smith johnson williams brown jones miller davis wilson taylor moore white clark lewis walker hall young king wright
scott green baker adams nelson hill campbell mitchell roberts carter phillips evans turner parker collins edwards
stewart morris murphy cook rogers morgan cooper peterson reed bailey bell kelly howard ward cox wood gray james
bennett brooks ross sanders price myers foster russell griffin hayes graham
wang li zhang liu chen yang huang wu zhou xu sun zhu hu guo lin kim lee park choi jung kang cho nguyen tran
pham singh kumar shah khan ali ahmed garcia martinez rodriguez hernandez lopez gonzalez perez sanchez ramirez torres
""".split())
NICK_WORDS = 2  # at most this many capitalized words go with a nickname: "[human] ("Bee") Zorn Quill" (not a title)
GAP = r"(?:[ \t]+\n?[ \t]*|\n[ \t]*)"  # a whitespace run: spaces and tabs, at most one newline
# a nickname: parenthesised, or in straight or curly, single or double quotes; a straight single quote opens only after
# whitespace and closes before a non-word character, so "[human]'s friend's" is no nickname
NICK = (r"\([^()\n]{1,40}\)|\"[^\"\n]{1,40}\"|“[^”\n]{1,40}”|‘[^’\n]{1,40}’(?!\w)|(?<=\s)'[^'\n]{1,40}'(?!\w)")
NICK_RE = re.compile(rf"{GAP}?(?:{NICK})")  # after [human]
PREV_NICK_RE = re.compile(rf"(?:^|(?<=\s))(?:{NICK}){GAP}?$")  # before [human]: "Surname ("Bee") [human]"
NAME_WORD_RE = re.compile(r"[^\W\d_][\w'’-]*")
PREV_WORD_RE = re.compile(rf"([^\W\d_][\w'’-]*)(?:{GAP}|/)$")
PREV_EMAIL_WORD_RE = re.compile(rf"([^\W\d_][\w'’-]*)(?:{GAP}[(<]?|{GAP}?[(<])$")  # "Ada Zorn ([email])"
NEXT_GAP_RE = re.compile(rf"{GAP}|/")  # "[human] Surname", "[human]/Nickname"
TITLE_NEXT_RE = re.compile(r"[ \t]+[A-Z](?:[a-z]|[A-Z]+\b)")  # another Title Case or ALL-CAPS word follows on the line
# a surname particle and the capitalized word after it, right after [human]: "[human] van Zorn"
PARTICLE_NAME_RE = re.compile(r"(?:van|von|de|der|den|da|di|du|del|della|le|la|bin|ibn|al|el)(?:[ \t]+(?:der|den|de|la))?"
                              r"[ \t]+[A-Z][\w'’-]*")
# an academic or medical title and the name after it: "Dr. Zorn", "Professor Ada Zorn"
TITLE_NAME_RE = re.compile(r"\b(?:Dr|Prof|Professor|Doctor)\.?[ \t]+([A-Z][\w'’-]*)(?:[ \t]+([A-Z][\w'’-]*))?")
# Keys whose values are ids, refs or fixed vocabulary: the final name sweep leaves them alone.
SWEEP_SKIP = frozenset({"id", "ref", "examples", "entity", "agent", "key", "room", "from", "to", "kind", "label",
                        "family", "date", "time", "generated", "start", "end", "schema", "dataset", "checkpoint",
                        "snap", "summary", "scope"})

SCRUB_RE = re.compile(r"[\W_]*(?:\[(?:REDACTED|BLOB_REMOVED|IMAGE_REMOVED)\][\W_]*)+")
# AI Digest daily texts wrap their sections in tags: a line holding only a tag (or "## <tag>", or an xml
# declaration), an inline snake_case tag, or an inline <blurb>/<takeaways>.
MARKUP_LINE_RE = re.compile(r"^[ \t]*(?:#+[ \t]*)?<[?/]?[A-Za-z]\w*(?:[ \t][^<>\n]*)?>[ \t]*(?:\n|$)", re.M)
MARKUP_INLINE_RE = re.compile(r"</?(?:[A-Za-z]+(?:_[A-Za-z]+)+|blurb|takeaways)>")
PERSON_LABEL_RE = re.compile(r"^(?:PER|PERSON|PERSONS|PEOPLE|HUMAN|INDIVIDUAL)(?:[_\-:].*)?$", re.I)
TOKEN_RE = re.compile(r"\w+")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
URL_RE = re.compile(r"https?://\S+|www\.\S+")
UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
PARTIAL_EMAIL_RE = re.compile(r"[\w.+-]*\w@(?=\.{2,}|…)")  # a mailbox whose domain was cut: "ada@..."
GAP_CHARS = frozenset(" \t-.()'")

FAMILIES = ("anthropic", "openai", "google", "xai", "deepseek", "moonshot", "zai", "meta", "other")

# Generic words, handles, platforms and model families: a display name made only of these (and agent-name words
# and digits) is no name and never used for redaction.
STOP_WORDS = frozenset("""
a about above after again all also am an and any are as at be because been before being below between
both but by can could did do does doing down during each few for from further had has have having he her
here hers him his how i if in into is it its just me more most my no nor not now of off on once only or
other our out over own same she should so some such than that the their them then there these they this
those through to too under until up very was we were what when where which while who whom why will with
would you your yes ok okay hi hey hello thanks thank please sorry good great nice cool new old big little
day night today love happy sad user users guest anon anonymous viewer viewers human humans admin test
tester testing bot bots ai agent agents village chat room general rest help helper mod moderator null none
unknown someone somebody anyone everyone nobody friend friends team the_village name real fan fans watcher
claude gpt gemini grok opus sonnet haiku llama deepseek kimi glm muse mini pro flash ultra nano turbo max
code chatgpt openai anthropic google meta xai digest aidigest theaidigest
substack youtube twitter reddit discord github gitlab gmail facebook instagram tiktok linkedin bluesky mastodon
telegram whatsapp slack notion figma canva wikipedia amazon apple microsoft netflix spotify twitch patreon
kickstarter gofundme medium wordpress tumblr pinterest snapchat zoom paypal stripe ebay etsy shopify
docs sheets drive calendar python javascript linux windows android iphone chrome firefox
will may mark bill grace hope joy faith art rose jack frank pat sue gene dawn summer autumn winter spring
january february march april june july august september october november december monday tuesday
wednesday thursday friday saturday sunday sunny rich chase page wade lane dean drew earl guy ray rob sky
victor carry price young north south east west green brown black white gray grey king rex major august
bot1 me1 user1 test1 dear sir madam mr mrs ms dr prof
""".split())
# Common words that are also given names: never skipped, matched only when capitalized (and dropped when agents
# mostly write them in lowercase, as "will" and "may").
NAME_WORDS = frozenset("""
will may mark bill grace hope joy faith art rose jack frank pat sue gene dawn sunny rich chase page wade lane dean
drew earl guy ray rob sky victor carry price young king rex major
""".split())
STOP_WORDS = STOP_WORDS - NAME_WORDS


# ---------------------------------------------------------------- helpers

def fold(text: str) -> str:
    """Entity grouping key: lowercase, whitespace folded, leading 'the ' dropped."""
    t = " ".join(text.lower().split())
    if t.startswith("the "):
        t = t[4:]
    return t


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def tokens_of(text: str) -> tuple:
    return tuple(TOKEN_RE.findall(text.lower()))


def iso(value) -> str | None:
    """Any dataset or ISO time -> 'YYYY-MM-DDTHH:MM:SSZ' (UTC)."""
    if value in (None, ""):
        return None
    s = str(value).strip().replace(" ", "T")
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        t = dt.datetime.fromisoformat(s)
    except ValueError:
        return str(value)
    if t.tzinfo is not None:
        t = t.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def epoch(value) -> float | None:
    if value in (None, ""):
        return None
    s = str(value).strip().replace(" ", "T")
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        t = dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t.timestamp()


def family_of(model: str | None) -> str:
    m = (model or "").lower()
    if "claude" in m:
        return "anthropic"
    if re.search(r"(^|[^a-z])(gpt|o1|o3|o4)", m):
        return "openai"
    if "gemini" in m:
        return "google"
    if "grok" in m:
        return "xai"
    if "deepseek" in m:
        return "deepseek"
    if "kimi" in m or "moonshot" in m:
        return "moonshot"
    if "glm" in m:
        return "zai"
    if "muse" in m or "llama" in m:
        return "meta"
    return "other"


def read_json(path: str, default=None):
    try:
        with open(path, "rb") as f:
            return loads(f.read())
    except FileNotFoundError:
        return default


def as_list(obj, key: str | None = None) -> list:
    """Meta files may be a list, an object keyed by id, or {key: [...]}."""
    if obj is None:
        return []
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        if key and isinstance(obj.get(key), list):
            return obj[key]
        out = []
        for k, v in obj.items():
            if isinstance(v, dict):
                v = dict(v)
                v.setdefault("id", k)
                out.append(v)
        return out
    return []


def pick_spread(items: list, k: int) -> list:
    """k items evenly spaced over a sorted list (first and last included)."""
    if len(items) <= k:
        return list(items)
    if k == 1:
        return [items[0]]
    return [items[round(i * (len(items) - 1) / (k - 1))] for i in range(k)]


class BuildError(Exception):
    pass


def load_human_names(path: str) -> list:
    """The authoritative redaction list. A missing, unreadable or malformed file stops the build: served text
    must never go out without it."""
    try:
        with open(path, "rb") as f:
            raw = loads(f.read())
        names = raw.get("names") if isinstance(raw, dict) else raw
    except (OSError, ValueError) as e:
        raise BuildError(f"human-name list {path} missing or unreadable ({e.__class__.__name__}): refusing to build")
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise BuildError(f"human-name list {path} is not a list of strings: refusing to build")
    return names


def load_extra_ids(path: str) -> list:
    """Usernames, handles and email-like ids of individual humans (meta/extra_human_ids.json, written by
    human_ids.py after review). Required like the name list: an empty list is written when there are none."""
    return load_human_names(path)


def load_rules(path: str) -> dict:
    """meta/extra_human_rules.json (Arch only, written by human_ids.py redteam): {"exact": names matched only in
    their exact case, "cap": common-word names matched where capitalized and not starting a sentence, in every
    text (tagged or not), "ids": numbers and codes cut as [id], "at": [{"file", "path", "token"}] tokens cut only at
    one served location, "drop_entities": folded entity keys never served, "profile_allow": accounts (org, agent,
    project) that the profile-URL rule keeps, "keep": public figures (full names, surnames, personal domains) that
    no rule redacts}. Required like the name list."""
    try:
        with open(path, "rb") as f:
            raw = loads(f.read())
    except (OSError, ValueError) as e:
        raise BuildError(f"rule file {path} missing or unreadable ({e.__class__.__name__}): refusing to build")
    if not isinstance(raw, dict):
        raise BuildError(f"rule file {path} is not an object: refusing to build")
    out = {}
    for k in ("exact", "cap", "ids", "drop_entities", "profile_allow", "keep"):
        v = raw.get(k, [])
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            raise BuildError(f"rule file {path}: {k} is not a list of strings: refusing to build")
        out[k] = [x for x in v if x.strip()]
    at = raw.get("at", [])
    if not isinstance(at, list) or not all(isinstance(r, dict) and all(isinstance(r.get(k), str) and r.get(k)
                                                                         for k in ("file", "path", "token"))
                                           for r in at):
        raise BuildError(f"rule file {path}: at is not a list of {{file, path, token}}: refusing to build")
    out["at"] = at
    return out


def json_path(path: str) -> list:
    """'$["ev:1:x"]["text"]' or '$["days"][3]["excerpt"]' -> ["ev:1:x", "text"] / ["days", 3, "excerpt"]."""
    return [json.loads(k) for k in re.findall(r'\[("(?:[^"\\]|\\.)*"|\d+)\]', path)]


def at_rule_hits(text: str, token: str) -> list:
    """(s, e) of `token` in `text`: any case, letter-or-digit boundaries."""
    return [(m.start(), m.end()) for m in re.finditer(r"(?<![^\W_])" + re.escape(token) + r"(?![^\W_])", text, re.I)]


def load_zipf(path: str | None = None):
    """word -> English zipf frequency: from `path` (a JSON {word: zipf}, for tests) or the wordfreq package."""
    if path:
        table = {str(k).lower(): float(v) for k, v in (read_json(path) or {}).items()}
        return lambda w: table.get(w, 0.0)
    from wordfreq import zipf_frequency
    memo = {}

    def zipf(w):
        if w not in memo:
            memo[w] = zipf_frequency(w, "en")
        return memo[w]
    return zipf


class Stats:
    def __init__(self):
        self.t0 = time.time()
        self.timings = {}
        self.warnings = collections.Counter()
        self.counts = {}

    def lap(self, name: str):
        now = time.time()
        self.timings[name] = round(now - self.t0 - sum(self.timings.values()), 2)


def iter_jsonl(paths, stats: Stats, label: str):
    """Yield parsed records; a bad or half-written line (file still growing) is counted and skipped."""
    for p in paths:
        with open(p, "rb") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield loads(line)
                except ValueError:
                    stats.warnings[f"{label}: bad json line"] += 1


def iter_jsonl_ids(paths, wanted: set, stats: Stats, label: str):
    """Yield records whose id is in `wanted`, parsing only those lines when the id leads the line."""
    for p in paths:
        with open(p, "rb") as f:
            for line in f:
                k = 7 if line.startswith(b'{"id":"') else 8 if line.startswith(b'{"id": "') else 0
                if k:
                    j = line.find(b'"', k)
                    if j > 0 and line[k:j].decode("utf-8", "replace") not in wanted:
                        continue
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = loads(line)
                except ValueError:
                    stats.warnings[f"{label}: bad json line"] += 1
                    continue
                if rec.get("id") in wanted:
                    yield rec


# ---------------------------------------------------------------- name matching

class TokenMatcher:
    """Find word-token sequences (agent names, human names) in text, longest match first.

    'GPT-5.5', 'gpt 5.5' and the slug 'gpt-5-5' are the same token sequence. Tokens in a match may be
    separated by 1 to 3 of ' -.()' only. With `version_bounds` (agent names) a match never ends just before
    '.<alnum>' or '-<alnum>' nor starts just after one (so 'Model 4' does not match inside 'Model 4.1');
    without it (human names) ordinary word boundaries apply ('vexa-led', 'vexa.example' match).
    """

    def __init__(self, version_bounds: bool = True):
        self.version_bounds = version_bounds
        self.first: dict[str, list] = collections.defaultdict(list)

    def add(self, toks: tuple, value, exact: str | None = None, any_case: bool = True):
        if toks:
            self.first[toks[0]].append((toks, value, exact, any_case))
            self.first[toks[0]].sort(key=lambda e: -len(e[0]))

    def finditer(self, text: str):
        toks = [(m.start(), m.end(), m.group().lower()) for m in TOKEN_RE.finditer(text)]
        n = len(toks)
        i = 0
        while i < n:
            cands = self.first.get(toks[i][2])
            hit = None
            if cands:
                for ntoks, value, exact, any_case in cands:
                    k = len(ntoks)
                    if i + k > n:
                        continue
                    ok = True
                    for j in range(1, k):
                        if toks[i + j][2] != ntoks[j]:
                            ok = False
                            break
                        gap = text[toks[i + j - 1][1]:toks[i + j][0]]
                        if not (1 <= len(gap) <= 3) or any(c not in GAP_CHARS for c in gap):
                            ok = False
                            break
                    if not ok:
                        continue
                    s, e = toks[i][0], toks[i + k - 1][1]
                    if self.version_bounds and e + 1 < len(text) and text[e] in ".-" and text[e + 1].isalnum():
                        continue
                    if self.version_bounds and s >= 2 and text[s - 1] in ".-" and text[s - 2].isalnum():
                        continue
                    if not any_case and not text[s].isupper() and text[s:e] != exact:
                        continue
                    hit = (s, e, value, k)
                    break
            if hit:
                yield hit[0], hit[1], hit[2]
                i += hit[3]
            else:
                i += 1


class Names:
    """Agent names (for mention edges) and human names (for redaction of served text).

    Every name on the list is used, except names under 3 letters and names made only of STOP_WORDS, agent-name
    words and digits. Each entry is [tokens, exact, any_case, rule]:
      handle     a digit or "_" in the name: any case
      word       one word that is not a common English word: any case (a display name typed in lowercase
                 is copied in lowercase)
      multi      several words, one of them not common: any case
      multi-cap  several common words: capitalized or in the exact listed form
      word-cap   one common word ("Will", "Time"), never dropped: in tagged text an occurrence is redacted
                 when oneiroNER tagged it PERSON (the PERSON-span rule); in untagged text (goals, findings,
                 rows without tags) when it is capitalized and not sentence-initial
    The first and last word of a two- or three-word Title Case name ("Zed Quill") are added as one-word names.
    The first four rules match everywhere (`human_matches`); word-cap matches come from `common_matches`.
    Three more rules for identifiers that are no display name, everywhere:
      extra      ids from meta/extra_human_ids.json (usernames, handles): any case, letter-or-digit boundaries,
                 so also inside "@x", "x-dev", "x_y", URLs and paths; their parts may be joined by 0-3 separators
      at         an @handle that is not exactly an agent's display name or slug (any case and separators), a public
                 account (PUBLIC_HANDLES) or, inside code, a code at-word (CODE_AT_WORDS)
      neighbour  capitalized words right after or before [human] ("[human] ("x") Surname", "Firstname [human]",
                 "Surname 'x' [human]"), unless agent names, stop words or roles; with a nickname at most NICK_WORDS
                 words; except the word next to a nickname, also not sentence-initial words (before the token),
                 all-caps words, words with a digit and common English words (bar SURNAMES); the run becomes one
                 [human]
    """

    def __init__(self, agents: list, human_names: list, zipf=None, extra_ids=(), rules=None):
        """`zipf`: word -> English zipf frequency (see load_zipf); without it every word that is not in
        STOP_WORDS or NAME_WORDS counts as rare. `extra_ids`: see the extra rule. `rules`: see load_rules."""
        self.agents = TokenMatcher()
        self.agent_by_tokens: dict[tuple, str] = {}
        agent_tokens = set()
        self.agent_handles = set()  # the letter-digit runs of each display name and slug: the agent forms an @ may carry
        for a in agents:
            if a["id"] == "human":
                continue
            for form in (a.get("name") or "", a["id"]):
                toks = tokens_of(form)
                if toks and toks not in self.agent_by_tokens:
                    self.agent_by_tokens[toks] = a["id"]
                    self.agents.add(toks, a["id"])
                agent_tokens.update(toks)
                if form.strip():
                    self.agent_handles.add(handle_parts(form))
        self.agent_tokens = agent_tokens
        self.zipf = zipf or (lambda w: 0.0)
        self.hits = collections.Counter()  # matches per rule, reset by the caller per assemble
        self.skipped = collections.Counter()
        self.entries = self._entries(human_names)
        # the words of listed names shaped like "Firstname Lastname" (2-3 Title Case words): the neighbour rule takes
        # them for name words even when they are common English words ("Zara")
        self.name_tokens = {t.lower() for n in human_names or [] if isinstance(n, str) and 2 <= len(n.split()) <= 3
                            and all(w[:1].isupper() and w[1:].islower() and w.isalpha() for w in n.split())
                            for t in n.split() if t.lower() not in STOP_WORDS and t.lower() not in agent_tokens}
        self.extra_ids = sorted({" ".join(x.split()) for x in extra_ids or () if isinstance(x, str) and x.strip()})
        parts = [[p for p in re.split(r"[\W_]+", x) if p] for x in self.extra_ids]
        alts = sorted((r"[\W_]{0,3}".join(map(re.escape, p)) for p in parts if p), key=len, reverse=True)
        self.extra_re = re.compile(r"(?<![^\W_])(?:" + "|".join(alts) + r")(?![^\W_])", re.I) if alts else None
        rules = rules or {}
        self.rules = rules
        exact = sorted({x.strip() for x in rules.get("exact", [])}, key=len, reverse=True)
        self.exact_re = (re.compile(r"(?<![^\W_])(?:" + "|".join(map(re.escape, exact)) + r")(?![^\W_])")
                         if exact else None)
        cap = sorted({x.strip() for x in rules.get("cap", [])}, key=len, reverse=True)
        self.cap_re = (re.compile(r"(?<![^\W_])(?:" + "|".join(map(re.escape, cap)) + r")(?![^\W_])")
                       if cap else None)
        parts = [[p for p in re.split(r"[\W_]+", x) if p] for x in rules.get("ids", [])]
        alts = sorted((r"[\W_]{0,3}".join(map(re.escape, p)) for p in parts if p), key=len, reverse=True)
        self.ids_re = re.compile(r"(?<![^\W_])(?:" + "|".join(alts) + r")(?![^\W_])", re.I) if alts else None
        self.drop_entities = {fold(x) for x in rules.get("drop_entities", [])}
        parts = [[p for p in re.split(r"[\W_]+", x) if p] for x in rules.get("keep", [])]
        alts = sorted((r"[\W_]{0,3}".join(map(re.escape, p)) for p in parts if p), key=len, reverse=True)
        self.keep_re = re.compile(r"(?<![^\W_])(?:" + "|".join(alts) + r")(?![^\W_])", re.I) if alts else None
        self.profile_allow = PROFILE_ALLOW | {x.lower() for x in rules.get("profile_allow", [])}
        self._build()

    def common(self, word: str) -> bool:
        return word in STOP_WORDS or word in NAME_WORDS or self.zipf(word) >= COMMON_ZIPF

    def _entries(self, human_names: list) -> list:
        out, seen = [], set()
        ignorable = STOP_WORDS | self.agent_tokens
        for raw in human_names or []:
            if not isinstance(raw, str):
                continue
            name = " ".join(raw.split())
            toks = tokens_of(name)
            if not toks or toks in seen:
                continue
            seen.add(toks)
            if len("".join(toks)) < 3:
                self.skipped["short"] += 1
                continue
            if toks in self.agent_by_tokens or all(t in ignorable or t.isdigit() for t in toks):
                self.skipped["stop_or_agent_words"] += 1
                continue
            out.append(self._entry(toks, name))
            if 2 <= len(toks) <= 3 and all(w[:1].isupper() and w.isalpha() for w in name.split()):
                for part in (toks[0], toks[-1]):  # people are often addressed by one part of their name
                    if (part,) not in seen and len(part) >= 3 and part not in ignorable:
                        seen.add((part,))
                        out.append(self._entry((part,), None))
        return out

    def _entry(self, toks: tuple, name: str | None) -> list:
        if name and any(c.isdigit() or c == "_" for c in name):
            return [list(toks), name, True, "handle"]
        rare = any(len(t) >= 3 and not t.isdigit() and t not in self.agent_tokens and not self.common(t)
                   for t in toks)
        if len(toks) == 1:
            return [list(toks), None, rare, "word" if rare else "word-cap"]
        return [list(toks), name, rare, "multi" if rare else "multi-cap"]

    def _build(self):
        self.humans = TokenMatcher(version_bounds=False)  # every name except word-cap
        self.commons = TokenMatcher(version_bounds=False)  # word-cap names, capitalized occurrences only
        for toks, exact, any_case, rule in self.entries:
            (self.commons if rule == "word-cap" else self.humans).add(tuple(toks), rule, exact=exact,
                                                                     any_case=any_case)
        self.human_count = len(self.entries)

    def summary(self) -> dict:
        counts = {f"skipped_{k}": v for k, v in self.skipped.items()}
        counts["final"] = self.human_count
        counts.update({f"final_{r}": n for r, n in collections.Counter(e[3] for e in self.entries).items()})
        counts["extra_ids"] = len(self.extra_ids)
        counts.update({f"rules_{k}": len(v) for k, v in self.rules.items()})
        return counts

    def not_a_name(self, surface: str) -> bool:
        """A PERSON span made only of generic words, agent-name words and digits ("a human", "Opus 4.5")."""
        toks = tokens_of(surface)
        return bool(toks) and all(t in GENERIC_PERSON or t in self.agent_tokens or t.isdigit() for t in toks)

    def agent_of_surface(self, surface: str) -> str | None:
        memo = self.__dict__.setdefault("_surface_memo", {})
        if surface not in memo:
            toks = tokens_of(surface)
            if toks and toks[0] == "the":
                toks = toks[1:]
            if len(memo) > 500_000:
                memo.clear()
            memo[surface] = self.agent_by_tokens.get(toks)
        return memo[surface]

    def agents_in(self, text: str) -> set:
        return {v for _, _, v in self.agents.finditer(text)}

    def human_matches(self, text: str) -> list:
        """(s, e, rule) of listed human names in `text` (word-cap names excepted), never inside an agent name
        (never redacted); then extra ids and @handles."""
        found = self._outside_agents(text, list(self.humans.finditer(text)) if self.human_count else [])
        if self.extra_re is not None:
            found += [(m.start(), m.end(), "extra") for m in self.extra_re.finditer(text)]
        return self.outside_keep(text, found + self.at_matches(text) + self.rule_matches(text))

    def keep_spans(self, text: str) -> list:
        """(s, e) of public figures the rule file keeps visible ("keep": well-known people the agents wrote to or
        cited, their surnames and personal domains)."""
        return [(m.start(), m.end()) for m in self.keep_re.finditer(text)] if self.keep_re is not None else []

    def outside_keep(self, text: str, found: list, keep: list | None = None) -> list:
        """Drop matches that lie entirely inside a kept public figure. A match that reaches beyond one ("@zzq-quill"
        around a kept "Quill") stays, and so does any match of an explicit private id (PRIVATE_RULES): a listed id
        always wins over a public exemption."""
        if self.keep_re is None or not found:
            return found
        keep = self.keep_spans(text) if keep is None else keep
        return [f for f in found if f[2] in PRIVATE_RULES or not any(ks <= f[0] and f[1] <= ke for ks, ke in keep)]

    def kept(self, keep: list, s: int, e: int) -> bool:
        return any(ks <= s and e <= ke for ks, ke in keep)

    def rule_matches(self, text: str) -> list:
        """(s, e, rule) of identifiers that are no name: exact-case names and ids from the rule file, percent-encoded
        emails, phone numbers, order/tracking/customer/user ids, profile-URL accounts, search-operator operands,
        usernames next to human words or in a quoted search query, salutation names, ALL-CAPS buyer names."""
        out = []
        if self.exact_re is not None:
            out += self._outside_agents(text, [(m.start(), m.end(), "exact") for m in self.exact_re.finditer(text)])
        if self.cap_re is not None:
            out += self._outside_agents(text, [(m.start(), m.end(), "cap") for m in self.cap_re.finditer(text)
                                               if not sentence_initial(text, m.start())])
        if self.ids_re is not None:
            out += [(m.start(), m.end(), "extra_id") for m in self.ids_re.finditer(text)]
        if "%" in text:
            out += [(m.start(), m.end(), "email_pct") for m in PCT_EMAIL_RE.finditer(text)]
        out += [(m.start(), m.end(), "phone") for m in PHONE_RE.finditer(text)
                if 8 <= sum(c.isdigit() for c in m.group()) <= 15
                and not (m.group().isdigit() and TECH_NUMBER_RE.search(text, max(0, m.start() - 24), m.start()))]
        for c in ORDER_CUE_RE.finditer(text):
            win_end = text.find("\n", c.end(), c.end() + 40)
            for m in ORDER_ID_RE.finditer(text, c.end(), win_end if win_end >= 0 else c.end() + 40):
                digits = sum(ch.isdigit() for ch in m.group())
                if (digits >= 5 and digits * 5 >= 2 * sum(ch.isalnum() for ch in m.group())
                        and not DATE_LIKE_RE.search(m.group())):
                    out.append((m.start(), m.end(), "order"))
        if "/" in text or "." in text:
            for m in PROFILE_RE.finditer(text):
                if m.group(1).lower() not in self.profile_allow and m.group(1).lower() not in PROFILE_ROUTES:
                    out.append((m.start(1), m.end(1), "profile"))
            for m in SUBDOMAIN_RE.finditer(text):
                if m.group(1).lower() not in self.profile_allow and m.group(1).lower() not in PROFILE_ROUTES:
                    out.append((m.start(1), m.end(1), "profile"))
        if ":" in text:
            code = None
            for m in QUERY_OP_RE.finditer(text):
                v = m.group(1).strip('"')
                if v.lower() in QUERY_OP_KEEP or v.isdigit() or self._agent_or_public(v):
                    continue
                line_start = text.rfind("\n", 0, m.start()) + 1
                line_end = text.find("\n", m.end())
                line = text[line_start:line_end if line_end >= 0 else len(text)]
                if not (MAIL_SEARCH_RE.search(text, max(line_start, m.start() - 80), m.start())
                        or MAIL_OP_RE.search(line)):
                    continue  # "Convert from:JSON to:YAML" is no person search
                if code is None:
                    code = [(c.start(), c.end()) for c in CODE_RE.finditer(text)]
                if not any(cs < m.start() < ce for cs, ce in code):
                    out.append((m.start(1), m.end(1), "query"))
        for c in HUMAN_CUE_RE.finditer(text):
            a = max(text.rfind("\n", 0, c.start()) + 1, c.start() - 30)
            b = text.find("\n", c.end(), c.end() + 50)
            for m in WORD_TOKEN_RE.finditer(text, a, b if b >= 0 else c.end() + 50):
                if not (c.start() <= m.start() < c.end()) and self._username(m.group()):
                    out.append((m.start(), m.end(), "cue_user"))
        for q in QUERY_RE.finditer(text):
            for m in WORD_TOKEN_RE.finditer(text, q.start(1), q.end(1)):
                if self._username(m.group()):
                    out.append((m.start(), m.end(), "query"))
        for m in TITLE_NAME_RE.finditer(text):
            for g in (1, 2):
                if m.group(g) is None or not self._salute_name(m.group(g)):
                    break
                out.append((m.start(g), m.end(g), "title_name"))
        for m in SALUTE_RE.finditer(text):
            for g in (1, 2):
                if m.group(g) is None or not self._salute_name(m.group(g)):
                    break
                out.append((m.start(g), m.end(g), "salutation"))
        for c in CAPS_CUE_RE.finditer(text):
            b = text.find("\n", c.end(), c.end() + 60)
            for m in CAPS_NAME_RE.finditer(text, c.end(), b if b >= 0 else c.end() + 60):
                words = re.findall(r"[A-Z][A-Z'’-]*", m.group())
                if (all(2 <= len(w) <= 15 for w in words) and sum(len(w) >= 3 for w in words) >= len(words) - 1
                        and not any(w.lower() in CAPS_KEEP or w.lower() in CAPS_TECH or w.lower() in STOP_WORDS
                                    or w.lower() in self.agent_tokens for w in words)):
                    out.append((m.start(), m.end(), "caps_name"))
        return out

    def _agent_or_public(self, word: str) -> bool:
        w = word.lower().lstrip("@")
        return (w in PUBLIC_HANDLES or w in self.profile_allow or handle_parts(w) in self.agent_handles
                or all(t in self.agent_tokens or t.isdigit() for t in handle_parts(w)))

    def _username(self, tok: str) -> bool:
        """An account name written without an @: three or more letters (an "_" allowed) ending in two or more digits,
        not all capitals ("zzq42", "zzq_fan77"; not "WAVE12" or "SUMMER10"). Snake-case and rare lowercase words
        are too often code or jargon here: the reviewed ids on meta/extra_human_ids.json cover those. Versions,
        times, hex hashes, agent names and public accounts are no usernames."""
        t = tok.strip(".'-")
        if (not USERNAME_RE.fullmatch(t) or t.isupper() or NOT_USERNAME_RE.fullmatch(t) or self._agent_or_public(t)
                or DATE_LIKE_RE.search(t)):
            return False
        return t.lower() not in STOP_WORDS

    def _salute_name(self, word: str) -> bool:
        toks = tokens_of(word)
        return bool(toks) and not any(t in STOP_WORDS or t in GENERIC_PERSON or t in NEIGHBOUR_KEEP or t in SALUTE_KEEP
                                      or t in self.agent_tokens or t.isdigit() for t in toks)

    def at_matches(self, text: str) -> list:
        """(s, e, "at") of @handles that are not exactly an agent's display name or slug, an agent family word
        (AGENT_FAMILY_HANDLES), a public account (PUBLIC_HANDLES) or, inside code, a well-known code at-word
        (CODE_AT_WORDS)."""
        if "@" not in text:
            return []
        out, agent_starts, code = [], None, None
        for m in AT_HANDLE_RE.finditer(text):
            h = m.group(1).lower()
            if h in PUBLIC_HANDLES or h in AGENT_FAMILY_HANDLES or handle_parts(h) in self.agent_handles:
                continue
            if h in CODE_AT_WORDS:
                if code is None:
                    code = [(c.start(), c.end()) for c in CODE_RE.finditer(text)]
                if any(cs < m.start() < ce for cs, ce in code):
                    continue
            if agent_starts is None:  # "@Claude Opus 4.1": an agent's whole display name right after the @
                agent_starts = {s: e for s, e, _ in self.agents.finditer(text)}
            ae = agent_starts.get(m.start(1))
            if ae is not None and ae >= m.end(1) and handle_parts(text[m.start(1):ae]) in self.agent_handles:
                continue
            out.append((m.start(), m.end(), "at"))
        return out

    def neighbour_matches(self, text: str) -> list:
        """(s, e, "neighbour"): a [human] token with the capitalized name words right after it or right before it,
        joined by a whitespace run (spaces, tabs, at most one newline) or a "/"; one parenthesised or quoted
        nickname may stand between, on either side ("[human] 'Bee' Zorn", "Zorn ("Bee") [human]"): then the word
        next to the nickname needs only to be capitalized and no agent name, stop word or role, and at most
        NICK_WORDS words go. So does the Title Case word right after the token on the same line, however common
        ("[human] Zorn's", "[human] Zorn."), unless another Title Case word follows it (a heading). The interval runs over the token, so the cut leaves one [human]. Also the name words
        right before an [email] token ("Ada Zorn ([email])"); there the interval ends at the last word and the token
        stays. Every other word must pass _name_word in full, and before the token must not start a sentence."""
        if REDACT_TOKEN not in text and EMAIL_TOKEN not in text:
            return []
        out, agent_starts = [], {s for s, _, _ in self.agents.finditer(text)}
        keep = self.keep_spans(text)  # a kept public figure is no name word: the run stops before it
        for m in re.finditer(re.escape(REDACT_TOKEN) + "|" + re.escape(EMAIL_TOKEN), text):
            s, e = m.start(), m.end()
            email = m.group() == EMAIL_TOKEN
            end = None
            if not email:
                nick = NICK_RE.match(text, e)
                q, strong, taken = (nick.end() if nick else e), bool(nick), 0
                while not (strong and taken == NICK_WORDS):
                    ws = NEXT_GAP_RE.match(text, q)
                    w = NAME_WORD_RE.match(text, ws.end()) if ws else None
                    # the word right after the token on its line: any Title Case or ALL-CAPS word but an agent name,
                    # stop word or acronym, unless another such word follows (a heading: "[human] Moon Mission";
                    # "[human] Zorn's Blog" is a name); the cut makes the next word adjacent, so a run would go word by
                    # word otherwise. A surname particle comes along: "[human] van Zorn".
                    if w is not None and w.group()[0].islower() and not taken and not strong and "\n" not in ws.group():
                        pw = PARTICLE_NAME_RE.match(text, ws.end())
                        if (pw and not TITLE_NEXT_RE.match(text, pw.end()) and pw.start() not in agent_starts
                                and not self.kept(keep, pw.start(), pw.end())):
                            end = q = pw.end()
                            taken += 1
                            continue
                    word = w.group() if w else ""
                    caps = word.isupper() and word.isalpha() and len(word) >= 2 and word.lower() not in CAPS_KEEP
                    adjacent = (w is not None and not taken and "\n" not in ws.group() and not any(c.isdigit() for c in word)
                                and (caps or not (len(word) > 1 and word[1].isupper()))
                                and (word.endswith(("'s", "’s")) or not TITLE_NEXT_RE.match(text, w.end())))
                    if not w or self.kept(keep, w.start(), w.end()) or not self._name_word(w.group(), w.start(), agent_starts,
                                                    strong=(strong and not taken) or adjacent):
                        break
                    end = q = w.end()
                    taken += 1
            start = None
            prev = PREV_EMAIL_WORD_RE if email else PREV_WORD_RE
            nick = None if email else PREV_NICK_RE.search(text, max(0, s - 60), s)
            q, strong, taken = (nick.start() if nick else s), bool(nick), 0
            while not (strong and taken == NICK_WORDS):
                w = prev.search(text, max(0, q - 60), q)
                first = strong and not taken  # the word next to a nickname
                if (not w or self.kept(keep, w.start(1), w.end(1))
                        or not self._name_word(w.group(1), w.start(1), agent_starts, strong=first)
                        or (not first and sentence_initial(text, w.start(1)))):
                    break
                start = q = w.start(1)
                taken += 1
                prev = PREV_WORD_RE
                if email and end is None:
                    end = w.end(1)
            if email and start is not None:
                out.append((start, end, "neighbour"))
            elif end is not None or start is not None:
                out.append((start if start is not None else m.start(), end if end is not None else e, "neighbour"))
        return out

    def _name_word(self, word: str, pos: int, agent_starts: set, strong: bool = False) -> bool:
        """A capitalized word that may be part of a person's name. `strong`: a nickname stands between it and the
        token ("[human] ("x") Surname", "Surname ("x") [human]"), so only agent names, stop words and roles are no
        name words; otherwise all-caps words, words with a digit or a second capital ("URLs") and common English
        words are not either, unless a word of a listed name or on NAME_WORDS / SURNAMES."""
        if not word[0].isupper() or pos in agent_starts:
            return False
        toks = tokens_of(word)
        if all(len(t) < 2 or t in STOP_WORDS or t in GENERIC_PERSON or t in NEIGHBOUR_KEEP or t in self.agent_tokens
               or t.isdigit() for t in toks):
            return False
        if strong:
            return True
        if (len(word) > 1 and word[1].isupper()) or any(c.isdigit() for c in word):
            return False
        return any(t in NAME_WORDS or t in SURNAMES or t in self.name_tokens or self.zipf(t) < NEIGHBOUR_ZIPF
                   for t in toks if len(t) >= 2)

    def common_matches(self, text: str) -> list:
        """(s, e, rule) of capitalized, not sentence-initial word-cap names: the untagged-text rule."""
        if not self.human_count:
            return []
        found = [m for m in self.commons.finditer(text) if not sentence_initial(text, m[0])]
        return self.outside_keep(text, self._outside_agents(text, found))

    def _outside_agents(self, text: str, found: list) -> list:
        if found:
            agents = [(s, e) for s, e, _ in self.agents.finditer(text)]
            if agents:
                found = [(s, e, r) for s, e, r in found if not any(s < ae and e > as_ for as_, ae in agents)]
        return found

    def human_found(self, text: str, tagged: bool = True) -> list:
        """(s, e, rule) of everything that must not survive in `text`: word-cap names count only in untagged
        text; names next to [human] count everywhere."""
        found = self.human_matches(text)
        if not tagged:
            found += self.common_matches(text)
        return found + self.outside_keep(text, self.neighbour_matches(text))

    def human_intervals(self, text: str, tagged: bool = True) -> list:
        return [(s, e) for s, e, _ in self.human_found(text, tagged)]

    def has_human(self, text: str) -> bool:
        """An entity surface that names a human: a listed name, an extra id, an @handle, any identifier rule (also in
        title case) or a key on the rule file's drop list (it never gets an id)."""
        if (self.extra_re is not None and self.extra_re.search(text)) or self.at_matches(text):
            return True
        if fold(text) in self.drop_entities or self.rule_matches(text) or self.rule_matches(text.title()):
            return True
        if not self.human_count:
            return False
        for _ in self.humans.finditer(text):
            return True
        # entity keys are lowercase: also try title case
        for _ in self.humans.finditer(text.title()):
            return True
        return False


def handle_parts(text: str) -> tuple:
    """The letter-digit runs of a name or handle, lowercase: "Agent Alpha 2.5" and "agent_alpha-2-5" are equal."""
    return tuple(re.findall(r"[a-z0-9]+", text.lower()))


def sentence_initial(text: str, s: int) -> bool:
    """True when position `s` starts the text, a line or a sentence (quotes, brackets and bullets skipped)."""
    i = s - 1
    while i >= 0 and (text[i] in " \t\"'([{*-#>\u201c\u2018\u2022"):
        i -= 1
    return i < 0 or text[i] in ".!?\n"  # ':' and ';' start a clause, not a sentence


def _word(text: str, s: int, e: int) -> tuple:
    """Widen [s, e) to whole words (the tagger can mark part of a word)."""
    while s > 0 and text[s - 1].isalnum():
        s -= 1
    while e < len(text) and text[e].isalnum():
        e += 1
    return s, e


def _apply(text: str, cuts: list, spans: list) -> tuple:
    """Replace each (s, e, token) cut; drop spans that overlap a cut and shift the rest."""
    cuts.sort()
    merged = []
    for s, e, token in cuts:
        if merged and s < merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e, token])
    pieces, starts, deltas, pos, delta = [], [], [], 0, 0
    for s, e, token in merged:
        pieces.append(text[pos:s])
        pieces.append(token)
        delta += len(token) - (e - s)
        starts.append(e)
        deltas.append(delta)
        pos = e
    pieces.append(text[pos:])

    def shift(p):
        i = bisect.bisect_right(starts, p) - 1
        return p + (deltas[i] if i >= 0 else 0)

    kept = [(shift(s), shift(e), label, surface) for s, e, label, surface in spans
            if not any(s < me and e > ms for ms, me, _ in merged)]
    return "".join(pieces), kept


def redact(text: str, spans: list, names: Names, person_labels: set, limit: int | None = None,
           tagged: bool = False):
    """Replace human names with [human], emails with [email], uuids with [id]; then cut to `limit` chars
    (None: no cut) at a word boundary, so a cut never leaves a partial word that reads as a name.

    `spans` are (s, e, label, surface) tag spans on `text`. Person spans that are not an agent name
    are redacted too, widened to whole words; spans with under 2 letters are noise and skipped. `tagged`:
    oneiroNER ran on this text, so common-word names (word-cap) go only where it tagged them PERSON;
    otherwise their capitalized, not sentence-initial occurrences go.
    Extra ids and @handles go like names; capitalized name words next to a [human] token join it (neighbour rule);
    the identifier rules (Names.rule_matches) cut with the token of their rule ([id], [email] or [human]).
    The result is checked again until no listed name is left (a cut can join text into a new name).
    Returns (text, [[s, e, label], ...]) with offsets remapped into the new text.
    """
    window = text if limit is None else text[: limit * 2]
    spans = [sp for sp in spans if sp[1] <= len(window)]
    hits = names.hits
    cuts = [(m.start(), m.end(), EMAIL_TOKEN) for m in EMAIL_RE.finditer(window)]
    hits["email"] += len(cuts)
    partial = [(m.start(), m.end(), EMAIL_TOKEN) for m in PARTIAL_EMAIL_RE.finditer(window)] if "@" in window else []
    hits["email_partial"] += len(partial)
    cuts += partial
    ids = [(m.start(), m.end(), ID_TOKEN) for m in UUID_RE.finditer(window)]
    hits["id"] += len(ids)
    cuts += ids
    blocked = list(cuts)  # a name inside an email address or uuid goes with it
    for s, e, rule in names.human_matches(window) + ([] if tagged else names.common_matches(window)):
        if not any(s < be and e > bs for bs, be, _ in blocked):
            cuts.append((s, e, RULE_TOKEN.get(rule, REDACT_TOKEN)))
            hits[f"name:{rule}"] += 1
    for s, e, label, surface in spans:
        if label not in person_labels or names.agent_of_surface(surface) or not names.outside_keep(window, [(s, e, "")]):
            continue  # agent names are never redacted
        if names.not_a_name(surface):
            hits["person_span_generic_kept"] += 1
            continue
        if sum(c.isalpha() for c in surface) >= 2:
            cuts.append((*_word(window, s, e), REDACT_TOKEN))
            hits["person_span"] += 1
    out, spans = _apply(window, cuts, spans)
    for _ in range(6):  # names next to [human] show only after the first cuts
        again = names.human_found(out, tagged)
        if not again:
            break
        for _, _, rule in again:
            hits["name:neighbour" if rule == "neighbour" else "recheck"] += 1
        out, spans = _apply(out, [(s, e, RULE_TOKEN.get(rule, REDACT_TOKEN)) for s, e, rule in again], spans)
    out = fit(out, limit, names)
    return out, [[s, e, label] for s, e, label, _ in spans if e <= len(out)]


def fit(out: str, limit: int | None, names: Names) -> str:
    """Cut `out` to `limit` characters at a word boundary (None: no cut)."""
    if limit is None or len(out) <= limit:
        return out
    cut = out[:limit]
    if out[limit].isalnum() or out[limit] in "_@.-[]":
        j = max(cut.rfind(" "), cut.rfind("\n"), cut.rfind("\t"))
        cut = cut[:j] if j > 0 else ""
    if "@" in cut[-60:]:  # never split "@Agent Alpha 2.5": its first word alone reads as a handle
        for s, e, _ in names.agents.finditer(out[max(0, len(cut) - 60):len(cut) + 60]):
            s, e = s + max(0, len(cut) - 60), e + max(0, len(cut) - 60)
            if s < len(cut) < e and out[s - 1:s] == "@":
                cut = cut[:s - 1].rstrip()
                break
    return cut


def redact_plain(text: str | None, names: Names, tagged: bool = False) -> str | None:
    if not text:
        return text
    return redact(text, [], names, set(), tagged=tagged)[0]


def strip_unbound(text: str, bound: set, hits: collections.Counter) -> str:
    """Remove [n] citation markers with no cite (their ref is not in the slice), with the space before them."""
    def sub(m):
        if int(m.group(2)) in bound:
            return m.group(0)
        hits["unbound_marker"] += 1
        return ""
    return re.sub(r"([ \t]?)\[(\d+)\]", sub, text) if text else text


def strip_markup(text: str, spans: list, hits: collections.Counter) -> tuple:
    """Drop wrapper tags (a line holding only a tag; inline snake_case tags such as
    <list_of_chronological_events>) and the blank lines they leave. Spans shift with the text."""
    cuts = [(m.start(), m.end(), "") for m in MARKUP_LINE_RE.finditer(text)]
    cuts += [(m.start(), m.end(), "") for m in MARKUP_INLINE_RE.finditer(text)
             if not any(s <= m.start() < e for s, e, _ in cuts)]
    if not cuts:
        return text, spans
    hits["markup_tag"] += len(cuts)
    out, spans = _apply(text, cuts, spans)
    ws = [(m.start(), m.end(), "" if m.start() == 0 or m.end() == len(out) else "\n\n")
          for m in re.finditer(r"^\s+|\s+$|\n[ \t]*\n(?:[ \t]*\n)+", out)]
    return _apply(out, ws, spans) if ws else (out, spans)


# ---------------------------------------------------------------- span snapping

SNAP_VERSION = "word-v1"


def snap_spans(text: str, spans: list, counts: collections.Counter) -> list:
    """word-v1: a span whose start or end falls inside an alphanumeric run is widened to the word edges
    (surrounding whitespace trimmed); same-label spans that then touch or overlap are merged; spans with
    no letter or digit are dropped, and so are non-PERSON spans that overlap an email address or uuid in the
    text (even a part of one: an entity id would expose what [email] / [id] hides). PERSON spans stay: they
    are no entities, and text redaction needs them ("Contact Qelvara <q@x.example>"). `text` is re-read."""
    n = len(text)
    out = []
    blocked = [(m.start(), m.end()) for m in EMAIL_RE.finditer(text)] if "@" in text else []
    if "-" in text:
        blocked += [(m.start(), m.end()) for m in UUID_RE.finditer(text)]
    for sp in spans:
        if not isinstance(sp, dict):
            continue
        counts["spans_in"] += 1
        s0, e0 = sp.get("s"), sp.get("e")
        if not isinstance(s0, int) or not isinstance(e0, int) or not n:
            counts["unsnapped"] += 1
            out.append(sp)
            continue
        s = max(0, min(s0, n))
        e = max(s, min(e0, n))
        while s < e and text[s].isspace():
            s += 1
        while e > s and text[e - 1].isspace():
            e -= 1
        if 0 < s < n and text[s - 1].isalnum() and text[s].isalnum():
            while s > 0 and text[s - 1].isalnum():
                s -= 1
        if 0 < e < n and text[e - 1].isalnum() and text[e].isalnum():
            while e < n and text[e].isalnum():
                e += 1
        if not any(c.isalnum() for c in text[s:e]):
            counts["dropped"] += 1
            continue
        if blocked and not PERSON_LABEL_RE.match(str(sp.get("label") or "")) and any(
                s < be and e > bs for bs, be in blocked):
            counts["dropped_email_or_uuid"] += 1
            continue
        if (s, e) != (s0, e0):
            counts["snapped"] += 1
        out.append(dict(sp, s=s, e=e, text=text[s:e]))
    out.sort(key=lambda sp: (sp.get("s", 0), sp.get("e", 0)))
    merged, last = [], {}
    for sp in out:
        prev = last.get(sp.get("label"))
        if prev is not None and isinstance(sp.get("s"), int) and "s" in prev and sp["s"] <= prev["e"]:
            prev["e"] = max(prev["e"], sp["e"])
            prev["text"] = text[prev["s"]:prev["e"]]
            if isinstance(sp.get("p"), (int, float)) and isinstance(prev.get("p"), (int, float)):
                prev["p"] = max(prev["p"], sp["p"])
            counts["merged"] += 1
            continue
        merged.append(sp)
        last[sp.get("label")] = sp
    return merged


# ---------------------------------------------------------------- the build

class Builder:
    def __init__(self, args):
        self.args = args
        self.root = args.root
        self.meta_dir = args.meta or os.path.join(self.root, "meta")
        self.slice_dir = args.slice or os.path.join(self.root, "slice")
        self.tags_dir = args.tags or os.path.join(self.root, "tags")
        self.sum_dir = args.summaries or os.path.join(self.root, "summaries")
        self.out = args.out or os.path.join(self.root, "bundle", "data")
        self.stats = Stats()

    # ---- meta

    def load_meta(self):
        m = self.meta_dir
        self.agents = []
        self.agent_ix = {}
        for a in as_list(read_json(os.path.join(m, "agents.json")), "agents"):
            name = a.get("name") or a.get("id")
            aid = a.get("id") if a.get("id") and not re.fullmatch(r"[0-9a-f-]{36}", str(a["id"])) else slug(name)
            model = a.get("model") or a.get("model_string")
            fam = a.get("family") if a.get("family") in FAMILIES else family_of(model)
            self._add_agent({"id": aid, "name": name, "model": model, "family": fam,
                             "participating": bool(a.get("participating", a.get("is_participating", True))),
                             "created_at": iso(a.get("created_at"))})
        self._add_agent({"id": "human", "name": "human", "model": None, "family": "human",
                         "participating": True, "created_at": None})

        self.rooms = []
        self.room_ix = {}
        for r in as_list(read_json(os.path.join(m, "rooms.json")), "rooms"):
            name = r.get("name") or r.get("id")
            rid = slug(name) or str(r.get("id"))
            if rid in self.room_ix:
                continue
            self.room_ix[rid] = len(self.rooms)
            self.rooms.append({"id": rid, "name": name, "deleted": iso(r.get("deleted_at") or r.get("deleted")),
                               "uuid": r.get("uuid") or r.get("id")})
        self.room_alias = {}
        for r in self.rooms:
            for k in (r["id"], r["name"], r.get("uuid")):
                if k:
                    self.room_alias[str(k)] = r["id"]

        self.village_goals = []
        for g in as_list(read_json(os.path.join(m, "village_goals.json")), "village_goals"):
            text = g.get("goal") or g.get("text")
            if text:
                self.village_goals.append({"goal": text, "start": iso(g.get("start_time") or g.get("start")),
                                           "end": iso(g.get("end_time") or g.get("end")),
                                           "first_day": g.get("first_day"), "last_day": g.get("last_day")})
        self.village_goals.sort(key=lambda g: g["start"] or "")

        self.agent_goals = collections.defaultdict(list)
        for g in as_list(read_json(os.path.join(m, "agent_goals.json")), "agent_goals"):
            agent = g.get("agent") or g.get("agent_id")
            if agent:
                self.agent_goals[agent].append({
                    "name": g.get("name"), "short": g.get("short_name") or g.get("short"),
                    "description": g.get("description"),
                    "start": iso(g.get("start_time") or g.get("start")), "end": iso(g.get("end_time") or g.get("end"))})

        self.days = []
        for d in as_list(read_json(os.path.join(m, "days.json")), "days"):
            if d.get("day") is None:
                continue
            self.days.append({"day": int(d["day"]), "date": d.get("date"), "first_ts": iso(d.get("first_ts"))})
        self.days.sort(key=lambda d: d["day"])
        self._index_days()

        self.summaries_index = {r["id"]: r for r in as_list(read_json(os.path.join(m, "summaries_index.json")))
                                if isinstance(r, dict) and r.get("id")}

        names_path = self.args.human_names or os.path.join(m, "human_names.json")
        human_names = load_human_names(names_path)
        self.extra_ids_path = self.args.extra_human_ids or os.path.join(m, EXTRA_IDS)
        extra_ids = load_extra_ids(self.extra_ids_path)
        self.rules_path = self.args.rules or os.path.join(m, RULES)
        self.rules = load_rules(self.rules_path)
        self.names = Names(self.agents, human_names, zipf=load_zipf(self.args.common_words), extra_ids=extra_ids,
                           rules=self.rules)
        self.stats.counts["human_names_loaded"] = len(human_names)
        self.stats.counts["human_names_used"] = self.names.human_count
        self.stats.counts["extra_human_ids"] = len(self.names.extra_ids)

    def _add_agent(self, a: dict):
        if a["id"] in self.agent_ix:
            return
        self.agent_ix[a["id"]] = len(self.agents)
        self.agents.append(a)

    def _index_days(self):
        self.day_by_num = {d["day"]: d for d in self.days}
        self.day_by_date = {d["date"]: d["day"] for d in self.days if d.get("date")}
        pairs = sorted((epoch(d["first_ts"]), d["day"]) for d in self.days if epoch(d["first_ts"]) is not None)
        self.day_starts = [p[0] for p in pairs]
        self.day_nums = [p[1] for p in pairs]

    def day_of_time(self, t) -> int | None:
        e = epoch(t)
        if e is None or not self.day_starts:
            return None
        i = bisect.bisect_right(self.day_starts, e) - 1
        return self.day_nums[max(i, 0)]

    def day_of_date(self, date: str | None) -> int | None:
        if not date:
            return None
        if date in self.day_by_date:
            return self.day_by_date[date]
        dates = sorted(self.day_by_date)
        i = bisect.bisect_right(dates, date) - 1
        return self.day_by_date[dates[i]] if i >= 0 else None

    # ---- summaries (small; loaded before the slice so their refs are known)

    def load_summaries(self):
        recs = {}
        for rec in iter_jsonl(sorted(glob.glob(os.path.join(self.sum_dir, "*.jsonl"))), self.stats, "summaries"):
            if rec.get("id") and rec.get("scope") in ("agent", "room", "village") and rec.get("day") is not None:
                recs[rec["id"]] = rec
        self.summary_recs = recs
        self.cited = set()
        for rec in recs.values():
            for c in rec.get("cites") or []:
                if isinstance(c, dict) and c.get("ref"):
                    self.cited.add(c["ref"])
        self.load_summary_tags()
        self.load_findings()

    def load_summary_tags(self):
        """oneiroNER spans over summary text (tags/tags-summaries.jsonl), word-snapped; optional."""
        self.summary_spans = {}
        path = os.path.join(self.tags_dir, SUMMARY_TAGS)
        if not os.path.exists(path):
            return
        counts = self.summary_snap = collections.Counter()
        for rec in iter_jsonl([path], self.stats, "tags-summaries"):
            src = self.summary_recs.get(rec.get("id"))
            if src is None:
                counts["id_not_in_summaries"] += 1
                continue
            text = src.get("text") or ""
            spans = []
            for sp in rec.get("spans") or []:
                if not isinstance(sp, dict) or not isinstance(sp.get("s"), int) or not isinstance(sp.get("e"), int):
                    continue
                surface = sp.get("text")
                if surface is not None and text[sp["s"]:sp["e"]] != surface:
                    # text changed since tagging: use every word-bounded copy of the surface instead
                    counts["stale_spans"] += 1
                    spans += [dict(sp, s=m.start(), e=m.end())
                              for m in re.finditer(r"(?<!\w)" + re.escape(surface) + r"(?!\w)", text)] if surface.strip() else []
                    continue
                spans.append(sp)
            spans = snap_spans(text, spans, counts)
            self.summary_spans[rec["id"]] = [(sp["s"], sp["e"], sp.get("label"), sp.get("text") or "")
                                             for sp in spans if sp.get("label") and isinstance(sp.get("s"), int)]
            counts["records"] += 1
        self.stats.counts["summary_tags"] = dict(counts)

    def load_findings(self):
        """findings/findings.json (schema 4, optional): keep valid findings and cites; their refs get sources."""
        self.findings = None
        path = self.args.findings or os.path.join(self.root, "findings", "findings.json")
        if not os.path.exists(path):
            return
        try:
            raw = read_json(path)
        except ValueError:
            self.stats.warnings["findings: not valid JSON (skipped)"] += 1
            return
        if not isinstance(raw, list):
            self.stats.warnings["findings: not a list (skipped)"] += 1
            return
        self.findings = []
        for f in raw:
            if not (isinstance(f, dict) and isinstance(f.get("title"), str) and isinstance(f.get("body"), str)
                    and isinstance(f.get("cites"), list) and isinstance(f.get("view", ""), (str, type(None)))):
                self.stats.warnings["findings: bad finding (skipped)"] += 1
                continue
            cites = []
            for c in f["cites"]:
                if (isinstance(c, dict) and isinstance(c.get("n"), int) and isinstance(c.get("ref"), str)
                        and isinstance(c.get("day"), int) and not isinstance(c.get("day"), bool)):
                    cites.append({"n": c["n"], "ref": c["ref"], "day": c["day"]})
                    self.cited.add(c["ref"])
                else:
                    self.stats.warnings["findings: bad cite (dropped)"] += 1
            self.findings.append({"title": f["title"], "body": f["body"], "cites": cites, "view": f.get("view")})

    # ---- pass 1: slice

    def slice_paths(self):
        return sorted(glob.glob(os.path.join(self.slice_dir, "slice-*.jsonl")))

    def raw_tag_paths(self):
        return sorted(p for p in glob.glob(os.path.join(self.tags_dir, "tags-*.jsonl"))
                      if os.path.basename(p) != SUMMARY_TAGS)

    def tag_paths(self):
        """Passes A-C read the word-snapped copy written during pass 1 (raw tags on Arch stay untouched)."""
        return sorted(glob.glob(os.path.join(self.snap_dir, "tags-*.jsonl")))

    def _slice_rows(self):
        """Yield slice records file by file; snap that kind's tag spans against the row text on the way."""
        self.snap_dir = os.path.join(os.path.dirname(os.path.abspath(self.out)), ".tags-snapped")
        shutil.rmtree(self.snap_dir, ignore_errors=True)
        os.makedirs(self.snap_dir)
        counts = self.snap_counts = collections.Counter()
        slice_files = self.slice_paths()
        kinds = sorted((os.path.basename(p)[6:-6] for p in slice_files), key=len, reverse=True)
        by_kind = collections.defaultdict(list)
        for p in self.raw_tag_paths():
            name = os.path.basename(p)[5:-6]
            kind = next((k for k in kinds if name == k or name.startswith(k + "-") or name.startswith(k + ".")), None)
            by_kind[kind].append(p)
        for path in slice_files:
            kind = os.path.basename(path)[6:-6]
            pending = {}
            for tp in by_kind.pop(kind, []):
                with open(tp, "rb") as f:
                    for line in f:
                        k = 7 if line.startswith(b'{"id":"') else 8 if line.startswith(b'{"id": "') else 0
                        j = line.find(b'"', k) if k else -1
                        if j > 0:
                            pending[line[k:j].decode("utf-8", "replace")] = line
                        elif line.strip():
                            try:
                                pending[loads(line).get("id")] = line
                            except ValueError:
                                self.stats.warnings["tags: bad json line"] += 1
            out = open(os.path.join(self.snap_dir, f"tags-{kind}.jsonl"), "wb") if pending else None
            for r in iter_jsonl([path], self.stats, "slice"):
                line = pending.pop(r.get("id"), None) if pending else None
                if line is not None:
                    try:
                        rec = loads(line)
                        rec["spans"] = snap_spans(r.get("text") or "", rec.get("spans") or [], counts)
                        out.write(dumps(rec) + b"\n")
                    except ValueError:
                        self.stats.warnings["tags: bad json line"] += 1
                yield r
            if out:
                for line in pending.values():  # ids not in the slice: kept raw, counted in pass A
                    out.write(line if line.endswith(b"\n") else line + b"\n")
                out.close()
        for kind, paths in by_kind.items():  # tag files with no slice kind: copied raw
            for tp in paths:
                shutil.copy(tp, os.path.join(self.snap_dir, "tags-unsnapped-" + os.path.basename(tp)[5:]))
                self.stats.warnings["tags: file with no slice kind (copied unsnapped)"] += 1

    def pass_slice(self):
        intern = sys.intern
        self.index = {}
        self.kind_rows = collections.Counter()
        self.kind_tokens = collections.Counter()
        self.agent_msgs = collections.Counter()
        self.agent_tokens = collections.Counter()
        self.agent_days = collections.defaultdict(set)
        self.agent_sessions = collections.defaultdict(set)
        self.agent_mems = collections.Counter()
        self.day_kind = collections.defaultdict(collections.Counter)
        self.day_sessions = collections.defaultdict(set)
        self.day_active = collections.defaultdict(set)
        self.series = {k: collections.defaultdict(collections.Counter) for k in ("chat", "sessions", "thinking")}
        self.agent_day_sessions = collections.defaultdict(set)
        self.room_msgs = collections.Counter()
        self.room_days = collections.defaultdict(set)
        self.chats = []
        self.mems = {}
        self.digests = {}
        self.name_mentions = {}
        self.edges = collections.defaultdict(lambda: {"mention": 0, "reply": 0, "by_day": collections.Counter()})

        for r in self._slice_rows():
            rid = r.get("id")
            if not rid:
                continue
            kind = intern(r.get("kind") or "unknown")
            # a human-authored row is the human node's, whatever its agent field says (the slicer keeps the
            # requesting agent's slug on human outreach replies)
            agent = "human" if r.get("speaker") == "human" else r.get("agent")
            if agent and agent not in self.agent_ix and re.sub(r"-\d+$", "", agent) in self.agent_ix:
                self.stats.warnings["slice: agent slug mapped by dropping a numeric suffix"] += 1
                agent = re.sub(r"-\d+$", "", agent)
            if agent and agent not in self.agent_ix:
                self._add_agent({"id": agent, "name": agent, "model": None, "family": "other",
                                 "participating": False, "created_at": None})
                self.stats.warnings["slice: agent not in meta"] += 1
            day = r.get("day")
            if day is None:
                day = self.day_of_time(r.get("time"))
            if day is None:
                self.stats.warnings["slice: no day"] += 1
                continue
            if day not in self.day_by_num:
                self.day_by_num[day] = {"day": day, "date": r.get("date"), "first_ts": None}
            room = r.get("room")
            if room is not None:
                room = intern(self.room_alias.get(str(room), str(room)))
            agent = intern(agent) if agent else None
            ntok = r.get("ntok") or 0
            text = r.get("text") or ""
            session = r.get("session")
            self.index[rid] = (day, agent, kind, room)
            self.kind_rows[kind] += 1
            self.kind_tokens[kind] += ntok
            self.day_kind[day][kind] += 1
            if session:
                self.day_sessions[day].add(session)
            if agent:
                self.agent_tokens[agent] += ntok
                self.agent_days[agent].add(day)
                if agent != "human" and kind != "digest_summary":
                    self.day_active[day].add(agent)
                if session:
                    self.agent_sessions[agent].add(session)
                    self.agent_day_sessions[(agent, day)].add(session)
                if kind in THINK_KINDS:
                    self.series["thinking"][agent][day] += 1
            if kind == "chat":
                if agent:
                    self.agent_msgs[agent] += 1
                    self.series["chat"][agent][day] += 1
                if room:
                    self.room_msgs[room] += 1
                    self.room_days[room].add(day)
                    e = epoch(r.get("time"))
                    if e is not None and agent:
                        self.chats.append((room, e, agent, day))
            elif kind == "memory" and agent:
                prev = self.mems.get((agent, day))
                t = r.get("time") or ""
                if prev is None or t >= prev[1]:
                    self.mems[(agent, day)] = (rid, t, len(text), text[: EXCERPT_CHARS * 2])
            elif kind == "digest_summary":
                self.digests[rid] = {k: v for k, v in r.items() if k != "ntok"}
            if agent and kind in MENTION_KINDS and text:
                targets = self.names.agents_in(text)
                targets.discard(agent)
                if targets:
                    self.name_mentions[rid] = targets
                    for t in targets:
                        ed = self.edges[(agent, t)]
                        ed["mention"] += 1
                        ed["by_day"][day] += 1

        for (agent, day), sess in self.agent_day_sessions.items():
            self.series["sessions"][agent][day] = len(sess)
        for (agent, day) in self.mems:
            self.agent_mems[agent] += 1
        # reply edges: next message in the same room within 10 minutes from a different speaker
        self.chats.sort()
        for prev, cur in zip(self.chats, self.chats[1:]):
            if prev[0] == cur[0] and cur[2] != prev[2] and cur[1] - prev[1] <= REPLY_WINDOW_S:
                ed = self.edges[(cur[2], prev[2])]
                ed["reply"] += 1
                ed["by_day"][cur[3]] += 1
        self.stats.counts["slice_rows"] = sum(self.kind_rows.values())
        self.stats.counts["snap"] = {"version": SNAP_VERSION, **self.snap_counts}
        self.stats.counts["chat_rows_with_time"] = len(self.chats)
        self.chats = None

    # ---- pass A: tags -> key counts

    def _labels_from_file(self):
        for p in (self.args.labels, os.path.join(self.tags_dir, "LABELS.json"),
                  os.path.join(self.tags_dir, "labels.json")):
            if p and os.path.exists(p):
                v = read_json(p)
                labels = v.get("labels") if isinstance(v, dict) else v
                if isinstance(labels, list) and all(isinstance(x, str) for x in labels):
                    return labels
        return None

    def _valid_spans(self, rec):
        for sp in rec.get("spans") or []:
            if not isinstance(sp, dict):
                continue
            label, surface = sp.get("label"), sp.get("text")
            if not label or not surface or SCRUB_RE.fullmatch(surface):
                continue
            folded = fold(surface)
            if folded:
                yield sp, label, surface, folded

    def _entity_key(self, label: str, folded: str, target: str | None):
        """Agent names come first: an agent-name span is always kept and keyed to that agent's display name
        (so 'o3', 'O3' and the slug group together). Other persons and one-character spans are no entities."""
        if target:
            return (label, fold(self.agents[self.agent_ix[target]]["name"] or target))
        if label in self.person_labels:
            return None
        if label != "MONEY" and sum(c.isalnum() for c in folded) < 2:
            return None
        return (label, folded)

    def pass_tags_a(self):
        self.key_counts = collections.Counter()
        self.key_agent = {}
        self.label_counts = collections.Counter()
        self.label_day = collections.defaultdict(collections.Counter)
        self.day_mentions = collections.Counter()
        self.mood = collections.defaultdict(lambda: [0.0, 0.0, 0.0, 0])
        self.models = collections.Counter()
        self.tagged = 0
        self.coref_seen = False
        file_labels = self._labels_from_file()
        self.person_labels = {l for l in (file_labels or DEFAULT_LABELS) if PERSON_LABEL_RE.match(l)}
        if self.args.person_labels:
            self.person_labels = set(self.args.person_labels.split(","))
        for rec in iter_jsonl(self.tag_paths(), self.stats, "tags"):
            meta = self.index.get(rec.get("id"))
            if meta is None:
                self.stats.warnings["tags: id not in slice"] += 1
                continue
            day, agent, kind, room = meta
            self.tagged += 1
            if rec.get("model"):
                self.models[rec["model"]] += 1
            if rec.get("coref"):
                self.coref_seen = True
            mood = rec.get("mood")
            if isinstance(mood, dict) and agent and kind in MENTION_KINDS:
                try:
                    acc = self.mood[(agent, day)]
                    acc[0] += float(mood["v"])
                    acc[1] += float(mood["a"])
                    acc[2] += float(mood["d"])
                    acc[3] += 1
                except (KeyError, TypeError, ValueError):
                    self.stats.warnings["tags: bad mood"] += 1
            span_targets = set()
            for sp, label, surface, folded in self._valid_spans(rec):
                self.label_counts[label] += 1
                self.label_day[label][day] += 1
                self.day_mentions[day] += 1
                target = self.names.agent_of_surface(surface)
                if target and agent and kind in MENTION_KINDS and target != agent:
                    span_targets.add(target)
                if PERSON_LABEL_RE.match(label) and not self.args.person_labels:
                    self.person_labels.add(label)
                key = self._entity_key(label, folded, target)
                if key is not None:
                    self.key_counts[key] += 1
                    if target:
                        self.key_agent[key] = target
            if span_targets:
                done = self.name_mentions.setdefault(rec["id"], set())
                for t in span_targets - done:
                    ed = self.edges[(agent, t)]
                    ed["mention"] += 1
                    ed["by_day"][day] += 1
                    done.add(t)
        self.stats.counts["human_names"] = self.names.summary()
        self.labels = list(file_labels or DEFAULT_LABELS)
        self.labels += [l for l, _ in self.label_counts.most_common() if l not in self.labels]
        self.stats.counts["tagged_rows"] = self.tagged
        self.stats.counts["entity_keys"] = len(self.key_counts)

    def choose_entities(self):
        self.top = {}
        self.entities = []
        dropped_names = dropped_ids = 0
        for (label, folded), count in self.key_counts.most_common():
            if len(self.entities) >= self.args.top_entities:
                break
            agent = self.key_agent.get((label, folded))
            if not agent and self.names.has_human(folded):
                dropped_names += 1
                continue
            if EMAIL_RE.search(folded) or UUID_RE.search(folded):  # its id would carry what [email]/[id] hides
                dropped_ids += 1
                continue
            self.top[(label, folded)] = len(self.entities)
            self.entities.append({"label": label, "folded": folded, "count": count, "agent": agent,
                                  "agents": collections.Counter(), "days": collections.Counter(),
                                  "surface": collections.Counter(), "cand": {}})
        self.stats.counts["entities_dropped_human_name"] = dropped_names
        self.stats.counts["entities_dropped_email_or_uuid"] = dropped_ids
        self.key_counts = None

    # ---- pass B: tags -> entity detail

    def pass_tags_b(self):
        self.day_top = collections.defaultdict(collections.Counter)
        self.agent_top = collections.defaultdict(collections.Counter)
        mem_ids = {v[0]: k for k, v in self.mems.items()}
        self.mem_ents = collections.defaultdict(set)
        if not self.top:
            return
        for rec in iter_jsonl(self.tag_paths(), self.stats, "tags-b"):
            rid = rec.get("id")
            meta = self.index.get(rid)
            if meta is None:
                continue
            day, agent, kind, room = meta
            primary = kind in EXAMPLE_KINDS
            for sp, label, surface, folded in self._valid_spans(rec):
                key = self._entity_key(label, folded, self.names.agent_of_surface(surface))
                ix = self.top.get(key) if key else None
                if ix is None:
                    continue
                ent = self.entities[ix]
                if agent:
                    ent["agents"][agent] += 1
                    self.agent_top[agent][ix] += 1
                ent["days"][day] += 1
                ent["surface"][" ".join(surface.split())] += 1
                self.day_top[day][ix] += 1
                c = ent["cand"].get(day)
                if c is None or (primary and not c[1]):
                    ent["cand"][day] = (rid, primary)
                if rid in mem_ids:
                    self.mem_ents[rid].add(ix)

    def assign_entity_ids(self):
        taken = set()
        self.examples_needed = {}
        for ix, ent in enumerate(self.entities):
            surface = ent["surface"].most_common(1)[0][0] if ent["surface"] else ent["folded"]
            if ent["agent"]:
                surface = self.agents[self.agent_ix[ent["agent"]]]["name"] or ent["agent"]
            base = slug(ent["folded"])[:60].strip("-") or "u" + hashlib.sha1(ent["folded"].encode()).hexdigest()[:10]
            if redact_plain(ent["folded"], self.names, tagged=True) != ent["folded"]:  # no id from redacted text
                base = "x" + hashlib.sha1(ent["folded"].encode()).hexdigest()[:12]
            eid = "e:" + base
            if eid in taken:
                eid = f"e:{base}--{slug(ent['label'])}"
            n = 2
            while eid in taken:
                eid = f"e:{base}--{slug(ent['label'])}-{n}"
                n += 1
            taken.add(eid)
            ent["id"] = eid
            ent["text"] = surface
            aliases = [s for s, _ in ent["surface"].most_common(6) if s != surface][:4]
            if ent["folded"] != surface and ent["folded"] not in aliases:
                aliases.insert(0, ent["folded"])
            ent["aliases"] = aliases
            prim = sorted(d for d, c in ent["cand"].items() if c[1])
            sec = sorted(d for d, c in ent["cand"].items() if not c[1])
            days = pick_spread(prim, EXAMPLES)
            if len(days) < EXAMPLES:
                days += pick_spread(sec, EXAMPLES - len(days))
            days.sort()
            ent["examples"] = [(d, ent["cand"][d][0]) for d in days]
            ent["cand"] = None
            for d, rid in ent["examples"]:
                self.examples_needed.setdefault(rid, set()).add(d)

    # ---- pass C + pass 2: spans and text of the quoted rows

    def load_quoted(self):
        mem_rids = {v[0] for v in self.mems.values()}
        digest_rids = set(self.digests)
        wanted_text = set(self.examples_needed) | self.cited
        wanted_spans = wanted_text | mem_rids | digest_rids
        self.spans_by_id = {}
        if self.tagged:
            for rec in iter_jsonl_ids(self.tag_paths(), wanted_spans, self.stats, "tags-c"):
                self.spans_by_id[rec["id"]] = [(sp["s"], sp["e"], label, surface)
                                               for sp, label, surface, _ in self._valid_spans(rec)
                                               if isinstance(sp.get("s"), int) and isinstance(sp.get("e"), int)]
        self.quoted = dict(self.digests)
        wanted_text -= set(self.digests)
        if wanted_text:
            for rec in iter_jsonl_ids(self.slice_paths(), wanted_text, self.stats, "slice-2"):
                self.quoted[rec["id"]] = rec

    def is_tagged(self, rid: str) -> bool:
        """oneiroNER ran on this slice row (its tag record was read in pass C); else the row is listed untagged."""
        if rid in self.spans_by_id:
            return True
        self.untagged.add(f"row:{rid}")
        return False

    def row_text(self, rid: str, rec: dict) -> tuple:
        """A quoted row's text and tag spans; AI Digest texts lose their wrapper markup."""
        text, spans = rec.get("text") or "", self.spans_by_id.get(rid, [])
        if rec.get("kind") == "digest_summary":
            text, spans = strip_markup(text, spans, self.names.hits)
        return text, spans

    def source_entry(self, rid: str) -> dict | None:
        rec = self.quoted.get(rid)
        if rec is None:
            return None
        tagged = self.is_tagged(rid)
        text, spans = redact(*self.row_text(rid, rec), self.names, self.person_labels, SOURCE_CHARS, tagged=tagged)
        meta = self.index.get(rid, (None, rec.get("agent"), rec.get("kind"), rec.get("room")))
        return {"time": iso(rec.get("time")), "agent": meta[1], "room": meta[3], "kind": meta[2],
                "text": text, "spans": spans}

    # ---- assemble

    def build(self):
        st = self.stats
        self.load_meta()
        self.load_summaries()
        st.lap("meta+summaries")
        self.pass_slice()
        st.lap("pass1_slice")
        self.pass_tags_a()
        st.lap("passA_tags")
        self.choose_entities()
        self.pass_tags_b()
        st.lap("passB_tags")
        self.assign_entity_ids()
        self.load_quoted()
        st.lap("passC_quoted")
        self.entity_limit = len(self.entities)
        files = self.assemble()
        for _ in range(4):  # trim the entity list until bundle.json fits its budget
            size = len(dumps(files["bundle.json"]))
            if size <= self.args.budget or not self.entity_limit:
                break
            sizes = [len(dumps(e)) + 1 for e in files["bundle.json"]["entities"]]
            room, n = self.args.budget - (size - sum(sizes)), 0
            while n < len(sizes) and room - sizes[n] >= 0:
                room -= sizes[n]
                n += 1
            self.entity_limit = min(n, int(self.entity_limit * 0.97))
            files = self.assemble()
        st.counts["entities_fit_to_budget"] = self.entity_limit
        self.apply_at_rules(files)
        self.sweep(files)
        st.counts["redaction"] = dict(sorted(self.names.hits.items()))
        st.lap("assemble")
        sizes = self.write(files)
        st.lap("write")
        for rel, n in sizes.items():
            if rel != "bundle.json" and n >= FILE_CAP:
                st.warnings[f"on-demand file over {FILE_CAP} bytes: {rel}"] = n
        if sum(sizes.values()) >= DATA_CAP:
            st.warnings[f"data/ over {DATA_CAP} bytes"] = sum(sizes.values())
        return sizes

    def assemble(self) -> dict:
        names = self.names
        names.hits = collections.Counter()  # redaction counts of this (possibly final) assembly
        self.untagged = set()  # "row:<ref>" / "summary:<id>" redacted by the untagged rule (for the validator)
        all_days = sorted(self.day_by_num)
        limit = self.entity_limit
        eid = [e["id"] for e in self.entities[:limit]]
        sources = collections.defaultdict(dict)

        def top(counter, k):
            return [{"entity": eid[i], "count": c} for i, c in counter.most_common() if i < limit][:k]

        def add_source(day, rid):
            if rid in sources[day]:
                return True
            entry = self.source_entry(rid)
            if entry is None:
                return False
            sources[day][rid] = entry
            return True

        # summaries: agent / room from the summarizer, village from AI Digest's own daily summaries
        per_agent = collections.defaultdict(list)
        per_room = collections.defaultdict(list)
        dropped_cites = 0
        for rec in sorted(self.summary_recs.values(), key=lambda r: (r["day"], r["id"])):
            if rec["scope"] == "village":
                continue
            out = dict(rec)
            # listed names, and oneiroNER PERSON spans that are not agents (tags/tags-summaries.jsonl)
            before = names.hits["person_span"]
            tagged = rec["id"] in self.summary_spans
            if not tagged:
                self.untagged.add(f"summary:{rec['id']}")
            out["text"] = redact(rec.get("text") or "", self.summary_spans.get(rec["id"], []), names,
                                 self.person_labels, tagged=tagged)[0]
            names.hits["person_span_in_summaries"] += names.hits["person_span"] - before
            cites = []
            for c in rec.get("cites") or []:
                if isinstance(c, dict) and c.get("ref") and add_source(rec["day"], c["ref"]):
                    cites.append({"n": c.get("n"), "ref": c["ref"]})
                else:
                    dropped_cites += 1
            out["cites"] = cites
            out["text"] = strip_unbound(out["text"], {c["n"] for c in cites}, names.hits)
            (per_agent if rec["scope"] == "agent" else per_room)[rec.get("key")].append(out)
        self.stats.counts["summary_cites_dropped_unknown_ref"] = dropped_cites

        village_by_day = {}
        for rid, r in self.digests.items():
            ix = self.summaries_index.get(rid, {})
            r = dict(ix, **r) if ix else r
            dtype = r.get("digest_type") or r.get("summary_type") or ix.get("type")
            if dtype != "daily":
                continue
            target = r.get("summary_target", ix.get("target"))
            day = ix.get("day")
            if day is None and isinstance(target, (int, str)) and str(target).isdigit():
                day = int(target)
            if day is None:
                day = self.day_of_date(r.get("summary_date")) or r.get("day")
            if day is None or day not in self.day_by_num:
                continue
            prev = village_by_day.get(day)
            created = r.get("created_at") or r.get("time") or ""
            if prev is None or created >= (prev.get("created_at") or prev.get("time") or ""):
                village_by_day[day] = r
        village = []
        for day in sorted(village_by_day):
            r = village_by_day[day]
            if not add_source(day, r["id"]):
                continue
            text, _ = redact(*self.row_text(r["id"], r), names, self.person_labels, tagged=self.is_tagged(r["id"]))
            village.append({"id": f"village-day:{day}", "scope": "village", "key": "village", "day": day,
                            "date": self.day_by_num[day].get("date"), "text": text,
                            "cites": [{"n": 1, "ref": r["id"]}],
                            "model": r.get("generated_by") or "AI Digest"})
        village_ids = {v["day"]: v["id"] for v in village}

        # findings: each cite's day is its ref's day; a cite whose ref is unknown is dropped
        findings = None
        if self.findings is not None:
            findings, fixed, dropped = [], 0, 0
            for f in self.findings:
                cites = []
                for c in f["cites"]:
                    day = self.index.get(c["ref"], (c["day"],))[0]
                    fixed += day != c["day"]
                    if add_source(day, c["ref"]):
                        cites.append({"n": c["n"], "ref": c["ref"], "day": day})
                    else:
                        dropped += 1
                body = strip_unbound(redact_plain(f["body"], names), {c["n"] for c in cites}, names.hits)
                findings.append({"title": redact_plain(f["title"], names), "body": body, "cites": cites,
                                 "view": f["view"]})
            self.stats.counts["findings"] = {"findings": len(findings), "cites": sum(len(f["cites"]) for f in findings),
                                             "cites_day_fixed": fixed, "cites_dropped_unknown_ref": dropped}

        # entities
        # bundle.json holds light records; per-agent counts and the daily timeline load on demand
        entities, entity_files = [], {}
        for ent in self.entities[:limit]:
            ex, ex_days = [], []
            for d, rid in ent["examples"]:
                if add_source(d, rid):
                    ex.append(rid)
                    ex_days.append(d)
            entities.append({
                "id": ent["id"], "text": redact_plain(ent["text"], names, tagged=True), "label": ent["label"],
                "aliases": [redact_plain(a, names, tagged=True) for a in ent["aliases"]], "count": ent["count"],
                "n_agents": len(ent["agents"]),
                "first_day": min(ent["days"]) if ent["days"] else None,
                "last_day": max(ent["days"]) if ent["days"] else None,
                "examples": ex, "example_days": ex_days})
            if ent["agent"]:
                entities[-1]["agent"] = ent["agent"]
            entity_files[f"entities/{ent['id'][2:]}.json"] = {
                "id": ent["id"], "agents": dict(ent["agents"].most_common()),
                "timeline": [[d, ent["days"][d]] for d in sorted(ent["days"])]}

        # memory drift
        memory_files = {}
        by_agent = collections.defaultdict(list)
        for (agent, day), m in self.mems.items():
            by_agent[agent].append((day, m))
        for agent, rows in by_agent.items():
            rows.sort()
            out_days, prev = [], None
            for day, (rid, t, chars, head) in rows:
                cur = {i for i in self.mem_ents.get(rid, ()) if i < limit}
                excerpt, _ = redact(head, self.spans_by_id.get(rid, []), names, self.person_labels, EXCERPT_CHARS,
                                    tagged=self.is_tagged(rid))
                union = (cur | prev) if prev is not None else set()
                out_days.append({
                    "day": day, "ref": rid, "chars": chars,
                    "entities": [eid[i] for i in sorted(cur)],
                    "added": [eid[i] for i in sorted(cur - prev)] if prev is not None else [eid[i] for i in sorted(cur)],
                    "dropped": [eid[i] for i in sorted(prev - cur)] if prev is not None else [],
                    "jaccard_prev": round(len(cur & prev) / len(union), 4) if prev is not None and union else None,
                    "excerpt": excerpt})
                prev = cur
            memory_files[agent] = {"agent": agent, "days": out_days}

        # agents
        agents = []
        for a in self.agents:
            aid = a["id"]
            days = self.agent_days.get(aid)
            created = self.day_of_time(a.get("created_at"))
            agents.append({
                "id": aid, "name": a["name"], "model": UUID_RE.sub(ID_TOKEN, a["model"]) if a["model"] else None,
                "family": a["family"],
                "participating": a["participating"],
                "first_day": min(days) if days else created, "last_day": max(days) if days else created,
                "goals": [dict(g, name=redact_plain(g.get("name"), names), short=redact_plain(g.get("short"), names),
                               description=redact_plain(g.get("description"), names))
                          for g in self.agent_goals.get(aid, [])],
                "stats": {"messages": self.agent_msgs[aid], "tokens": self.agent_tokens[aid],
                          "days_active": len(days or ()), "sessions": len(self.agent_sessions.get(aid, ())),
                          "memories": self.agent_mems[aid]},
                "top_entities": top(self.agent_top.get(aid, collections.Counter()), AGENT_TOP)})

        rooms = []
        seen_rooms = set()
        for r in self.rooms + [{"id": rid, "name": rid, "deleted": None} for rid in self.room_msgs
                               if rid not in self.room_ix]:
            if r["id"] in seen_rooms:
                continue
            seen_rooms.add(r["id"])
            rd = self.room_days.get(r["id"])
            rooms.append({"id": r["id"], "name": r["name"], "deleted": r["deleted"],
                          "first_day": min(rd) if rd else None, "last_day": max(rd) if rd else None,
                          "messages": self.room_msgs[r["id"]]})

        def goal_at(day_rec):
            t = day_rec.get("first_ts")
            cur = None
            for g in self.village_goals:
                if g["start"] and t and g["start"] <= t:
                    cur = g
            if cur and cur["end"] and t and cur["end"] <= t:
                return None
            return redact_plain(cur["goal"], names) if cur else None

        days = []
        for d in all_days:
            rec = self.day_by_num[d]
            kc = self.day_kind.get(d, collections.Counter())
            days.append({
                "day": d, "date": rec.get("date"), "village_goal": goal_at(rec),
                "active_agents": sorted(self.day_active.get(d, ())),
                "counts": {"chat": kc["chat"], "sessions": len(self.day_sessions.get(d, ())),
                           "thinking": kc["thinking"] + kc["cc_thinking"], "memories": kc["memory"],
                           "mentions": self.day_mentions[d]},
                "top_entities": top(self.day_top.get(d, collections.Counter()), DAY_TOP),
                "summary": village_ids.get(d)})

        last_day = all_days[-1] if all_days else None
        goals = []
        for g in self.village_goals:
            goals.append({"goal": redact_plain(g["goal"], names), "start": g["start"], "end": g["end"],
                          "first_day": g.get("first_day") or self.day_of_time(g["start"]),
                          "last_day": g.get("last_day") or (self.day_of_time(g["end"]) if g["end"] else last_day)})

        # views
        def aligned(counter):
            return [counter.get(d, 0) for d in all_days]

        series = {}
        for name, per in self.series.items():
            series[name] = {a: aligned(c) for a, c in sorted(per.items()) if sum(c.values())}
        series["mentions"] = {l: aligned(self.label_day[l]) for l in self.labels if l in self.label_day}

        nodes = sorted({a for pair in self.edges for a in pair} | {a for a, n in self.agent_msgs.items() if n},
                       key=lambda a: (-self.agent_msgs[a], a))
        edges = []
        for (src, dst), ed in self.edges.items():
            w = ed["mention"] + ed["reply"]
            if w:
                edges.append({"from": src, "to": dst, "w": w,
                              "kinds": {"mention": ed["mention"], "reply": ed["reply"]},
                              "by_day": [[d, ed["by_day"][d]] for d in sorted(ed["by_day"])]})
        edges.sort(key=lambda e: (-e["w"], e["from"], e["to"]))

        mood_days = sorted({d for (_, d) in self.mood})
        mood_agents = collections.defaultdict(lambda: {"v": [], "a": [], "d": []})
        for agent in sorted({a for (a, _) in self.mood}):
            ma = mood_agents[agent]
            for d in mood_days:
                acc = self.mood.get((agent, d))
                for i, k in enumerate("vad"):
                    ma[k].append(round(acc[i] / acc[3], 3) if acc and acc[3] else None)

        heads = []
        if self.tagged:
            heads.append("ner")
            if self.mood:
                heads.append("mood")
            if self.coref_seen:
                heads.append("coref")
        first = self.day_by_num[all_days[0]] if all_days else {}
        last = self.day_by_num[last_day] if all_days else {}
        n_summaries = len(village) + sum(len(v) for v in per_agent.values()) + sum(len(v) for v in per_room.values())
        manifest = {
            "schema": SCHEMA, "generated": self.args.generated or iso(dt.datetime.now(dt.timezone.utc).isoformat()),
            "dataset": DATASET, "cite": CITE,
            "range": {"first_day": all_days[0] if all_days else None, "last_day": last_day,
                      "first_date": first.get("date"), "last_date": last.get("date")},
            "counts": {"agents": sum(1 for a in agents if a["id"] != "human"), "rooms": len(rooms),
                       "slice_rows": sum(self.kind_rows.values()), "slice_tokens": sum(self.kind_tokens.values()),
                       "mentions": sum(self.day_mentions.values()), "entities": len(entities),
                       "summaries": n_summaries, "tagged_rows": self.tagged,
                       "interaction_edges": len(edges)},
            "tagger": {"name": "oneiroNER",
                       "checkpoint": (self.models.most_common(1)[0][0] if self.models else DEFAULT_CHECKPOINT)
                       if self.tagged else None,
                       "labels": self.labels, "heads": heads, "snap": SNAP_VERSION},
            "summarizer": SUMMARIZER}

        previews = []
        for v in village:
            p = dict(v)
            if len(v["text"]) > VILLAGE_PREVIEW:
                cut = v["text"][:VILLAGE_PREVIEW]
                p["text"] = (cut[:cut.rfind(" ")] if " " in cut else cut).rstrip() + " …"
                p["truncated"] = True
            previews.append(p)
        bundle = {"manifest": manifest, "agents": agents, "rooms": rooms, "days": days, "goals": goals,
                  "entities": entities, "summaries": {"village": previews},
                  "views": {"timeline": {"days": all_days, "series": series},
                            "interactions": {"nodes": [{"id": a, "messages": self.agent_msgs[a]} for a in nodes],
                                             "edges": edges},
                            "mood": {"days": mood_days, "agents": dict(mood_agents)}}}

        files = {"bundle.json": bundle}
        if findings is not None:
            files["findings.json"] = findings
        if self.args.village_layout == "per-day":
            for v in village:  # one full AI Digest daily text per file
                files[f"summaries/village/{v['day']}.json"] = v
        else:
            files["summaries/village.json"] = village
        files.update(entity_files)
        for key, recs in per_agent.items():
            files[f"summaries/agent/{key}.json"] = recs
        for key, recs in per_room.items():
            files[f"summaries/room/{key}.json"] = recs
        for agent, doc in memory_files.items():
            files[f"memory/{agent}.json"] = doc
        for day, doc in sources.items():
            files[f"sources/{day}.json"] = doc
        self.stats.counts.update({"entities": len(entities), "village_summaries": len(village),
                                  "agent_summaries": sum(len(v) for v in per_agent.values()),
                                  "room_summaries": sum(len(v) for v in per_room.values()),
                                  "memory_agents": len(memory_files), "source_days": len(sources),
                                  "source_rows": sum(len(v) for v in sources.values()),
                                  "edges": len(edges), "kind_rows": dict(self.kind_rows),
                                  "kind_tokens": dict(self.kind_tokens)})
        return files

    def apply_at_rules(self, files: dict):
        """Per-location tokens from the rule file (a common word that names a person in one row only): cut as
        [human] at that served file and JSON path; a sources row keeps its spans in step. A path that no longer
        resolves (the row is not quoted any more) is skipped and counted."""
        hits = self.names.hits
        for r in self.rules["at"]:
            parent, key = files.get(r["file"]), None
            keys = json_path(r["path"])
            try:
                for k in keys[:-1]:
                    parent = parent[k]
                key = keys[-1]
                text = parent[key]
            except (KeyError, IndexError, TypeError):
                hits["at_rule_unresolved"] += 1
                continue
            if not isinstance(text, str):
                hits["at_rule_unresolved"] += 1
                continue
            cuts = [(s, e, REDACT_TOKEN) for s, e in at_rule_hits(text, r["token"])]
            if not cuts:
                continue
            hits["name:at_rule"] += len(cuts)
            if key == "text" and isinstance(parent.get("spans"), list):  # a sources row
                out, spans = _apply(text, cuts, [(a, b, l, text[a:b]) for a, b, l in parent["spans"]])
                out = fit(out, SOURCE_CHARS, self.names)
                parent["text"], parent["spans"] = out, [[a, b, l] for a, b, l, _ in spans if b <= len(out)]
            else:
                parent[key] = fit(_apply(text, cuts, [])[0], EXCERPT_CHARS if key == "excerpt" else None, self.names)

    def sweep(self, obj, key=None):
        """Safety net over the assembled files: every served string passes the name filter once more (ids,
        refs and other keys in SWEEP_SKIP excepted). Hits count as `sweep` and point at a missed path."""
        names = self.names
        if isinstance(obj, dict):
            if isinstance(obj.get("text"), str) and isinstance(obj.get("spans"), list):  # a sources row
                if names.human_intervals(obj["text"]):
                    names.hits["sweep"] += 1
                    t = obj["text"]
                    obj["text"], obj["spans"] = redact(t, [(s, e, l, t[s:e]) for s, e, l in obj["spans"]], names, set(),
                                                       SOURCE_CHARS, tagged=True)
            for k, v in obj.items():
                if k in SWEEP_SKIP or (k == "text" and "spans" in obj):
                    continue
                if isinstance(v, str):
                    if names.human_intervals(v):
                        names.hits["sweep"] += 1
                        obj[k] = fit(redact_plain(v, names), EXCERPT_CHARS if k == "excerpt" else None, names)
                elif isinstance(v, (dict, list)):
                    self.sweep(v, k)
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                if isinstance(v, str):
                    if key not in SWEEP_SKIP and names.human_intervals(v):
                        names.hits["sweep"] += 1
                        obj[i] = redact_plain(v, names)
                elif isinstance(v, (dict, list)):
                    self.sweep(v, key)

    def write(self, files: dict) -> dict:
        """Write an immutable generation dir, validate it, then point the served path (a symlink) at it with
        one rename. The previous generation is kept; older ones are removed. A failed build goes to failed/."""
        out = os.path.abspath(self.out)
        parent = os.path.dirname(out)
        gens = os.path.join(parent, "gen")
        os.makedirs(gens, exist_ok=True)
        tmp = os.path.join(gens, f".tmp-{os.getpid()}")
        shutil.rmtree(tmp, ignore_errors=True)
        sizes = {}
        for rel, obj in files.items():
            if not re.fullmatch(r"[A-Za-z0-9._/-]+", rel) or ".." in rel.split("/"):
                raise ValueError(f"unsafe bundle path {rel!r}")
            path = os.path.join(tmp, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            data = dumps(obj)
            with open(path, "wb") as f:
                f.write(data)
            sizes[rel] = len(data)
        with open(os.path.join(parent, "redaction_names.json"), "w") as f:  # pipeline-internal, never served
            json.dump({"entries": self.names.entries, "untagged": sorted(self.untagged)}, f, ensure_ascii=False)
        self.validation_errors = []
        if self.args.validate:  # a bundle that fails validation never replaces the served one
            import validate_bundle
            v = validate_bundle.validate(tmp, self.args.meta or self.meta_dir, zipf=self.names.zipf,
                                         untagged=self.untagged, human_names=self.args.human_names,
                                         extra_ids=self.extra_ids_path, rules=self.rules_path)
            self.validation_errors = v.errors
            if v.errors:
                failed = os.path.join(parent, "failed")
                shutil.rmtree(failed, ignore_errors=True)
                os.rename(tmp, failed)
                return sizes
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S")
        name, n = f"{stamp}-{os.getpid()}", 1
        while os.path.lexists(os.path.join(gens, name)):
            n += 1
            name = f"{stamp}-{os.getpid()}-{n}"
        os.rename(tmp, os.path.join(gens, name))
        previous = os.path.basename(os.readlink(out)) if os.path.islink(out) else None
        if os.path.isdir(out) and not os.path.islink(out):  # first run on a plain data/ dir: keep it as a generation
            previous = f"{stamp}-legacy"
            os.rename(out, os.path.join(gens, previous))
        link = os.path.join(parent, f".{os.path.basename(out)}.link-{os.getpid()}")
        if os.path.lexists(link):
            os.unlink(link)
        os.symlink(os.path.join("gen", name), link)
        os.replace(link, out)  # atomic: the served path always resolves to a complete generation
        for g in os.listdir(gens):
            if g not in (name, previous) and not g.startswith(".tmp-"):
                shutil.rmtree(os.path.join(gens, g), ignore_errors=True)
        self.generation = name
        return sizes


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=DEFAULT_ROOT, help="work area holding meta/ slice/ tags/ summaries/")
    ap.add_argument("--meta")
    ap.add_argument("--slice")
    ap.add_argument("--tags")
    ap.add_argument("--summaries")
    ap.add_argument("--out", help="bundle data dir (default <root>/bundle/data)")
    ap.add_argument("--labels", help="JSON list of tagger labels, in order (default tags/LABELS.json or seen labels)")
    ap.add_argument("--person-labels", help="comma list of labels treated as person names (default: auto)")
    ap.add_argument("--human-names", help="JSON list of human display names to redact (default meta/human_names.json)")
    ap.add_argument("--extra-human-ids", help="JSON list of human usernames/handles to redact (default meta/extra_human_ids.json)")
    ap.add_argument("--rules", help="identifier rule file (default meta/extra_human_rules.json)")
    ap.add_argument("--common-words", help="JSON {word: zipf} instead of the wordfreq package (tests)")
    ap.add_argument("--findings", help="findings JSON (default <root>/findings/findings.json; optional)")
    ap.add_argument("--top-entities", type=int, default=TOP_ENTITIES)
    ap.add_argument("--budget", type=int, default=BUNDLE_BUDGET, help="max bytes of bundle.json; entities are trimmed to fit")
    ap.add_argument("--village-layout", choices=("per-day", "single"), default="per-day",
                    help="full AI Digest daily texts: summaries/village/<day>.json (default) or one summaries/village.json")
    ap.add_argument("--generated", help="fixed manifest.generated (for reproducible fixtures)")
    ap.add_argument("--build-json", help="where to write build stats (default <out>/../BUILD.json)")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--no-validate", dest="validate", action="store_false",
                    help="swap the new bundle in without running validate_bundle.py on it first")
    args = ap.parse_args(argv)

    b = Builder(args)
    try:
        sizes = b.build()
    except BuildError as e:
        raise SystemExit(f"build_bundle: {e}")
    st = b.stats
    total = sum(sizes.values())
    report = {"generated": args.generated or iso(dt.datetime.now(dt.timezone.utc).isoformat()),
              "seconds": round(time.time() - st.t0, 2), "timings": st.timings, "counts": st.counts,
              "warnings": dict(st.warnings), "bytes_total": total, "bytes_bundle_json": sizes.get("bundle.json"),
              "files": len(sizes),
              "largest": sorted(([k, v] for k, v in sizes.items()), key=lambda kv: -kv[1])[:12],
              "validation_errors": b.validation_errors[:50], "swapped_in": not b.validation_errors}
    path = args.build_json or os.path.join(os.path.dirname(os.path.abspath(b.out)), "BUILD.json")
    with open(path, "w") as f:
        json.dump(report, f, indent=1)
    if args.quiet:
        return report
    print(json.dumps({k: report[k] for k in ("seconds", "timings", "bytes_total", "bytes_bundle_json", "files",
                                             "warnings")}, indent=1))
    if b.validation_errors:
        for e in b.validation_errors[:20]:
            print("ERROR", e)
        print(f"NOT SWAPPED: {len(b.validation_errors)} validation errors; the served bundle is unchanged; "
              f"the rejected build is in {os.path.join(os.path.dirname(os.path.abspath(b.out)), 'failed')}")
        return report
    print(f"wrote {b.out} ({len(sizes)} files, {total / 1e6:.2f} MB; bundle.json {sizes['bundle.json'] / 1e6:.2f} MB; validated)")
    return report


if __name__ == "__main__":
    sys.exit(0 if main()["swapped_in"] else 1)
