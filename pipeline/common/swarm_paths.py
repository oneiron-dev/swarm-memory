"""The work area and the redaction lists, shared by every stage.

The work area ($W) holds everything a run writes: slice/, meta/, tags/, summaries/, bundle/, book/, questions/,
human_ids/, pf/, web-dist/. Every stage takes it as --root (or --work / --out, as each stage always named it) and
defaults to the environment variable SWARM_WORK. Nothing defaults to a path on our machines any more.

The redaction lists live in one directory, by default the work area's meta/ (the real run kept them there):
  human_names.json       display names of humans (human_names.py), JSON list
  extra_human_ids.json   usernames, handles, email-like ids of individual humans (human_ids.py write/redteam, pf.py write)
  context_handles.json   handles that are also English words, cut only in handle context
  extra_human_rules.json rule file (human_ids.py redteam): {"exact", "cap", "ids", "at", "drop_entities", "keep", ...}
--lists <dir> (or SWARM_LISTS) points every stage at another directory. A missing list file is created empty by the
builder (ensure_lists) and named on stdout; a malformed one still stops the build.
"""
from __future__ import annotations

import json
import os

LIST_FILES = {"human_names.json": [], "extra_human_ids.json": [], "context_handles.json": [],
              "extra_human_rules.json": {}}


def work_default():
    return os.environ.get("SWARM_WORK") or None


def need_work(value, flag="--root"):
    """The work area, or SystemExit saying how to give one."""
    if not value:
        raise SystemExit(f"no work area: set SWARM_WORK=<dir> (export SWARM_WORK=$W) or pass {flag} <dir>")
    return os.path.abspath(os.path.expanduser(value))


def lists_dir(root, lists=None):
    """The redaction-list directory: --lists, else SWARM_LISTS, else <root>/meta."""
    return os.path.abspath(lists or os.environ.get("SWARM_LISTS") or os.path.join(root, "meta"))


def ensure_lists(directory, log=print) -> list:
    """Create the missing list files empty; returns the names created. An existing file is never touched."""
    os.makedirs(directory, exist_ok=True)
    made = []
    for name, empty in LIST_FILES.items():
        path = os.path.join(directory, name)
        if not os.path.exists(path):
            tmp = path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(empty, f)
            os.replace(tmp, path)
            made.append(name)
    if made and log:
        log(f"lists: created empty {', '.join(made)} in {directory}. With empty lists the rules still pool every "
            f"human, cut emails, @handles, phone numbers, profile accounts in URLs, long URL tokens, uuids and names "
            f"after a title or in a salutation; a name a human gives in plain text (\"I am Jo Bloggs\") stays unless "
            f"the tagger marks it PERSON or a list holds it (SKILL.md section 5)")
    return made
