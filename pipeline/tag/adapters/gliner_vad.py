"""gliner_vad: the reference tagger that needs nothing private. CPU is enough.

Spans: GLiNER (urchade/gliner_small-v2.1, Apache-2.0, about 600 MB with its DeBERTa-v3 encoder, downloaded from
Hugging Face on first use), asked for nine plain-English labels and mapped onto ours:
  person -> PERSON, location -> PLACE, organization -> ORG, date -> DATE, event -> EVENT,
  relationship term -> RELATIONSHIP_REF, emotion -> EMOTION, amount of money -> MONEY, product -> PRODUCT
Two privacy biases, because over-redaction is the chosen failure mode: a span GLiNER calls ORG or PRODUCT but also
scores as a person (class probability >= 0.3) becomes PERSON, and a capitalised name right after a self-introduction
or a salutation ("I am Jo Bloggs", "my name is", "call me", "Dear") is tagged PERSON even when GLiNER missed it or
called it a product (it does: in tests it labelled an invented full name PRODUCT). Agent names are left alone.

Mood: the NRC Valence, Arousal and Dominance Lexicon v2.1 (Saif M. Mohammad, National Research Council Canada), a
word list of 54,801 English terms scored -1..1. It is downloaded on first use into the cache directory, never into
this repo: its terms allow non-commercial research and educational use, ask you to cite it, and need a licence from
NRC for commercial use. A text's mood is the mean of its matched terms (two-word terms first; terms near zero on all
three scales skipped), v kept in [-1, 1], a and d rescaled to [0, 1]. A text with no matched term gets no mood.

Install: pip install -r pipeline/tag/requirements-gliner.txt (on Linux without a GPU add
--extra-index-url https://download.pytorch.org/whl/cpu to get the small CPU build of torch).
"""
from __future__ import annotations

import os
import re
import sys
import urllib.request
import zipfile

GLINER_MODEL = "urchade/gliner_small-v2.1"
GL_LABELS = {"person": "PERSON", "location": "PLACE", "organization": "ORG", "date": "DATE", "event": "EVENT",
             "relationship term": "RELATIONSHIP_REF", "emotion": "EMOTION", "amount of money": "MONEY",
             "product": "PRODUCT"}
THRESHOLD = 0.4
PERSON_BIAS = 0.3
WINDOW_WORDS = 250
NRC_URL = "https://saifmohammad.com/WebDocs/Lexicons/NRC-VAD-Lexicon-v2.1.zip"
NRC_FILE = "NRC-VAD-Lexicon-v2.1/NRC-VAD-Lexicon-v2.1.txt"
NRC_NOTE = ("NRC VAD Lexicon v2.1, created by Saif M. Mohammad at the National Research Council Canada "
            "(saifmohammad.com/WebPages/nrc-vad.html; cite arXiv:2503.23547). Free for non-commercial research and "
            "educational use; commercial use needs a licence from NRC. Kept in {path}, never committed.")
CUE_RE = re.compile(r"(?:\b(?:I am|I'm|I’m|my name is|My name is|call me|Call me|Dear|dear|signed|Signed),?\s+)"
                    r"((?:[A-Z][a-z]+(?:['’-][A-Za-z]+)?)(?:\s+[A-Z][a-z]+(?:['’-][A-Za-z]+)?){0,2})")
NOT_NAMES = frozenset("""monday tuesday wednesday thursday friday saturday sunday january february march april may june
july august september october november december sorry here back done happy glad afraid sure not just also still
everyone all team friends""".split())
WORD_RE = re.compile(r"[a-z][a-z'’-]*")
# GLiNER calls pronouns and generic roles "person"; they are no one's name
NOT_PERSONS = frozenset("""i me my mine myself you your yours we us our ours he him his she her hers they them their
theirs it its everyone everybody someone somebody anyone anybody nobody people person team buyers buyer sellers
seller walkers walker viewers viewer humans human users user friends friend folks guys all others other""".split())


def cache_dir(args=None) -> str:
    d = getattr(args, "cache", None) or os.environ.get("SWARM_CACHE") or os.path.join(os.path.expanduser("~"), ".cache",
                                                                                       "swarm-reader")
    os.makedirs(d, exist_ok=True)
    return d


def load_nrc(directory: str, log=print) -> dict:
    """{term: (v, a, d)} from the NRC VAD Lexicon v2.1, downloaded on first use."""
    path = os.path.join(directory, "nrc-vad-v2.1.txt")
    if not os.path.exists(path):
        zpath = os.path.join(directory, "NRC-VAD-Lexicon-v2.1.zip")
        log(f"gliner_vad: downloading the NRC VAD Lexicon from {NRC_URL}")
        req = urllib.request.Request(NRC_URL, headers={"User-Agent": "Mozilla/5.0 (swarm-reader; research use)"})
        ctx = None
        try:  # certifi comes with the tagger's requirements; some Python builds ship no CA bundle of their own
            import certifi
            import ssl
            ctx = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            pass
        try:
            with urllib.request.urlopen(req, timeout=120, context=ctx) as r, open(zpath, "wb") as out:
                out.write(r.read())
        except OSError as e:
            raise SystemExit(f"gliner_vad: could not download the NRC VAD Lexicon ({e}). Download {NRC_URL} by hand "
                             f"and put {NRC_FILE} at {path}")
        with zipfile.ZipFile(zpath) as z, open(path + ".tmp", "wb") as out:
            out.write(z.read(NRC_FILE))
        os.replace(path + ".tmp", path)
        os.remove(zpath)
    log("gliner_vad: " + NRC_NOTE.format(path=path))
    lex = {}
    with open(path, encoding="utf-8") as f:
        next(f)
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) == 4:
                v, a, d = (float(x) for x in parts[1:])
                if max(abs(v), abs(a), abs(d)) >= 0.1:
                    lex[parts[0].lower()] = (v, a, d)
    return lex


def mood_of(text: str, lex: dict):
    words = WORD_RE.findall(text.lower().replace("’", "'"))
    hits, i = [], 0
    while i < len(words):
        two = f"{words[i]} {words[i + 1]}" if i + 1 < len(words) else None
        if two and two in lex:
            hits.append(lex[two])
            i += 2
            continue
        w = words[i].strip("'-")
        if w in lex:
            hits.append(lex[w])
        i += 1
    if not hits:
        return None
    n = len(hits)
    v, a, d = (sum(h[k] for h in hits) / n for k in range(3))
    return {"v": round(max(-1.0, min(1.0, v)), 3), "a": round((a + 1) / 2, 3), "d": round((d + 1) / 2, 3)}


def windows(text: str, n: int = WINDOW_WORDS):
    """(offset, piece) runs of at most n whitespace words, cut between words."""
    spans = [m.span() for m in re.finditer(r"\S+", text)]
    for k in range(0, len(spans), n):
        part = spans[k:k + n]
        s, e = part[0][0], part[-1][1]
        yield s, text[s:e]


class GlinerVad:
    name = "gliner_vad"
    tagger = "GLiNER + NRC-VAD"
    model = f"gliner:{GLINER_MODEL}+vad:nrc-vad-v2.1"
    decode = "gliner-flat-t0.4"
    labels = ("PERSON", "PLACE", "ORG", "DATE", "EVENT", "RELATIONSHIP_REF", "EMOTION", "MONEY", "PRODUCT")

    def load(self, args):
        try:
            from gliner import GLiNER
        except ImportError:
            raise SystemExit("gliner_vad: GLiNER is not installed: pip install -r pipeline/tag/requirements-gliner.txt")
        import warnings
        warnings.filterwarnings("ignore")
        self.batch = getattr(args, "batch", 16) or 16
        self.lex = load_nrc(cache_dir(args))
        print(f"gliner_vad: loading {GLINER_MODEL} (downloaded from Hugging Face on first use)", flush=True)
        self.gl = GLiNER.from_pretrained(GLINER_MODEL)
        names = [n for n in getattr(args, "agent_names", []) if n]
        self.agent_words = {w.lower() for n in names for w in re.split(r"[\s._-]+", n) if w}
        alts = sorted({re.escape(n) for n in names if len(n) >= 2}, key=len, reverse=True)
        self.agent_re = re.compile(r"(?<![\w-])(?:" + "|".join(alts) + r")(?![\w-])", re.I) if alts else None

    def tag(self, rows):
        pieces = []  # (row index, offset, text)
        for i, r in enumerate(rows):
            text = r.get("text") or ""
            if text.strip():
                pieces += [(i, off, p) for off, p in windows(text)]
        results = [{"spans": [], "mood": mood_of(r.get("text") or "", self.lex)} for r in rows]
        if pieces:
            out = self.gl.inference([p for _, _, p in pieces], list(GL_LABELS), threshold=THRESHOLD,
                                    multi_label=False, batch_size=self.batch, return_class_probs=True)
            for (i, off, piece), ents in zip(pieces, out):
                text = rows[i]["text"]
                for e in ents:
                    s, en = off + e["start"], off + e["end"]
                    if text[s:en] != e["text"]:
                        continue
                    label = GL_LABELS.get(e["label"])
                    probs = e.get("class_probs") or {}
                    if label in ("ORG", "PRODUCT") and probs.get("person", 0) >= PERSON_BIAS:
                        label = "PERSON"
                    if label:
                        results[i]["spans"].append({"s": s, "e": en, "label": label, "text": text[s:en],
                                                    "p": round(float(e["score"]), 3)})
        for i, r in enumerate(rows):
            text = r.get("text") or ""
            spans = [sp for sp in results[i]["spans"]
                     if not (sp["label"] == "PERSON" and sp["text"].lower().strip(".,'’") in NOT_PERSONS)]
            spans = self.snap_agents(text, spans)
            self.cue_names(text, spans)
            results[i]["spans"] = sorted(spans, key=lambda sp: (sp["s"], sp["e"]))
        return results

    def snap_agents(self, text: str, spans: list) -> list:
        """A span inside or across an agent's name ("Birch" of "Birch 2.1") becomes the whole name, so the build
        files it under the agent instead of taking it for a person to redact."""
        if self.agent_re is None:
            return spans
        names = [m.span() for m in self.agent_re.finditer(text)]
        if not names:
            return spans
        out, seen = [], set()
        for sp in spans:
            hit = next(((s, e) for s, e in names if sp["s"] < e and s < sp["e"]), None)
            if hit:
                if hit in seen:
                    continue
                seen.add(hit)
                sp = dict(sp, s=hit[0], e=hit[1], text=text[hit[0]:hit[1]])
            out.append(sp)
        return out

    def cue_names(self, text: str, spans: list):
        """A name after "I am", "my name is", "call me", "Dear" or "signed" is a person, whatever GLiNER said."""
        for m in CUE_RE.finditer(text):
            s, e = m.span(1)
            words = m.group(1).split()
            if words[0].lower() in NOT_NAMES or any(w.lower().strip("'’-") in self.agent_words for w in words):
                continue
            spans[:] = [sp for sp in spans if sp["e"] <= s or sp["s"] >= e]
            spans.append({"s": s, "e": e, "label": "PERSON", "text": text[s:e], "p": 0.5, "sub": "cue"})


if __name__ == "__main__":
    print(__doc__, file=sys.stderr)
