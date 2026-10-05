"""Charts for docs/METHOD.md and the README, drawn from the public served bundle only.

Reads https://swarm-hack-2026.oneiron.dev/data/ (bundle.json, book.json, adherence/<agent>.json, memory/<agent>.json),
caches the files in --cache, and writes PNGs 1600 px wide into --out. No dataset text is drawn: every chart is an
aggregate, and the only names on it are agent (model) names.

    python3 docs/charts/make_charts.py --cache /tmp/swarm-data --out docs/img [--font-dir <dir with .ttf files>]
"""
from __future__ import annotations

import argparse
import json
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap, LogNorm  # noqa: E402

BASE = "https://swarm-hack-2026.oneiron.dev/data/"

# The book's inks (web/src/styles/tokens.css and book.css)
PAPER = "#f6f1e7"
INK = "#1f1d1a"
INK2 = "#3a362f"
INK3 = "#78715f"
INK4 = "#a89f90"
RULE = (31 / 255, 29 / 255, 26 / 255, 0.14)
ACCENT = "#b7482a"
TEAL = "#3f7a74"   # the humans' thread on the cloth
IDLE = "#ddd4c4"

W_IN, DPI = 10.0, 160   # 1600 px wide

FINDING_AGENTS = ["gemini-2-5-pro", "o3", "gpt-5-5", "gpt-5-6-luna", "claude-opus-5"]


def roman(n: int) -> str:
    out = ""
    for v, s in ((50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
        while n >= v:
            out += s
            n -= v
    return out


# ---------------------------------------------------------------- data
class Data:
    def __init__(self, cache: Path):
        self.cache = cache
        cache.mkdir(parents=True, exist_ok=True)
        self.b = self.get("bundle.json")
        self.book = self.get("book.json")
        self.name = {a["id"]: a["name"] for a in self.b["agents"]}
        self.agents = [a["id"] for a in self.b["agents"] if a["id"] != "human"]

    def get(self, rel: str):
        p = self.cache / rel
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            with urllib.request.urlopen(BASE + rel, timeout=60) as r:
                p.write_bytes(r.read())
        return json.loads(p.read_text())

    @property
    def seams(self) -> list[tuple[int, int, int]]:
        return [(c["n"], c["first_day"], c["last_day"]) for c in self.book["chapters"]]


# ---------------------------------------------------------------- style
def setup(font_dir: str | None) -> None:
    family = ["Georgia", "DejaVu Serif"]
    if font_dir:
        for f in Path(font_dir).glob("*.ttf"):
            font_manager.fontManager.addfont(str(f))
        family = ["Source Serif 4"] + family
    plt.rcParams.update({
        "font.family": "serif", "font.serif": family, "font.size": 11,
        "figure.facecolor": PAPER, "axes.facecolor": PAPER, "savefig.facecolor": PAPER,
        "text.color": INK, "axes.labelcolor": INK3, "axes.edgecolor": INK4,
        "xtick.color": INK3, "ytick.color": INK3, "xtick.labelsize": 10, "ytick.labelsize": 10,
        "axes.linewidth": 0.6, "xtick.major.width": 0.6, "ytick.major.width": 0.6,
        "xtick.major.size": 3, "ytick.major.size": 3, "axes.titlesize": 12,
        "axes.spines.top": False, "axes.spines.right": False, "legend.frameon": False,
        "axes.grid": False, "hatch.color": INK4, "hatch.linewidth": 0.6,
    })


def title(fig, text: str, sub: str | None = None, y: float = 0.975) -> None:
    fig.text(0.055, y, text, fontsize=16, color=INK, ha="left", va="top")
    if sub:
        fig.text(0.055, y - 0.052 * (6.0 / fig.get_figheight()), sub, fontsize=11, color=INK3, ha="left", va="top")


def hgrid(ax) -> None:
    ax.yaxis.grid(True, color=INK, alpha=0.09, linewidth=0.6)
    ax.set_axisbelow(True)


def seams(ax, d: Data, label: bool = True, min_span: int = 14) -> None:
    """Chapter seams: a hairline at each chapter's first day; numerals over the longer chapters."""
    for n, first, last in d.seams:
        if n > 1:
            ax.axvline(first - 0.5, color=RULE, linewidth=0.6, zorder=0)
        if label and (last - first + 1 >= min_span or n in (1,)):
            ax.text((first + last) / 2, 1.0, roman(n), transform=ax.get_xaxis_transform(),
                    ha="center", va="bottom", fontsize=9, color=INK3)


def day_axis(ax) -> None:
    ax.set_xlim(0.5, 535.5)
    ax.set_xticks([1, 100, 200, 300, 400, 500, 535])


def rolling(xs: list[float | None], k: int) -> list[float | None]:
    out: list[float | None] = []
    for i in range(len(xs)):
        win = [x for x in xs[max(0, i - k + 1): i + 1] if x is not None]
        out.append(sum(win) / len(win) if win else None)
    return out


def save(fig, out: Path, name: str) -> Path:
    p = out / name
    fig.savefig(p, dpi=DPI)
    plt.close(fig)
    return p


# ---------------------------------------------------------------- charts
def agents_per_day(d: Data, out: Path) -> Path:
    days = [x["day"] for x in d.b["days"]]
    n = [len([a for a in x["active_agents"] if a != "human"]) for x in d.b["days"]]
    fig, ax = plt.subplots(figsize=(W_IN, 4.6))
    fig.subplots_adjust(left=0.055, right=0.985, top=0.80, bottom=0.12)
    title(fig, "Agents active per day", f"{len(days)} recorded days of 535; a hairline marks each of the 51 chapters (one per village goal)")
    ax.bar(days, n, width=1.0, color=INK2, linewidth=0)
    seams(ax, d)
    day_axis(ax)
    hgrid(ax)
    ax.set_ylabel("agents active")
    ax.set_xlabel("village day")
    i = max(range(len(n)), key=lambda j: n[j])
    ax.annotate(f"{n[i]} at once (first on day {days[i]})", (days[i], n[i]), xytext=(-12, 2), textcoords="offset points",
                ha="right", va="bottom", fontsize=10, color=INK2)
    ax.set_ylim(0, max(n) * 1.12)
    return save(fig, out, "agents-per-day.png")


def messages_per_day(d: Data, out: Path) -> Path:
    t = d.b["views"]["timeline"]
    days, chat = t["days"], t["series"]["chat"]
    human = chat.get("human", [0] * len(days))
    ai = [sum(chat[a][i] for a in chat if a != "human") for i in range(len(days))]
    share = [100 * h / (h + a) if (h + a) else None for h, a in zip(human, ai)]
    fig, (ax, bx) = plt.subplots(2, 1, figsize=(W_IN, 6.4), sharex=True, gridspec_kw={"height_ratios": [2.2, 1]})
    fig.subplots_adjust(left=0.07, right=0.985, top=0.855, bottom=0.09, hspace=0.18)
    title(fig, "Messages per day, and the humans' share",
          f"chat messages in the slice: {sum(ai):,} by agents, {sum(human):,} by humans (pooled; no person is a node)")
    ax.bar(days, ai, width=1.0, color=INK3, linewidth=0, label="agents")
    ax.bar(days, human, bottom=ai, width=1.0, color=TEAL, linewidth=0, label="humans")
    seams(ax, d)
    hgrid(ax)
    ax.set_ylabel("messages")
    ax.legend(loc="upper left", bbox_to_anchor=(0.0, 0.98), ncol=2, fontsize=10, handlelength=1.0, handleheight=0.8)
    xs = [x for x, s in zip(days, share) if s is not None]
    ys = [s for s in share if s is not None]
    bx.scatter(xs, ys, s=5, color=TEAL, alpha=0.45, linewidths=0)
    r = rolling(ys, 7)
    bx.plot(xs, r, color=TEAL, linewidth=1.6)
    seams(bx, d, label=False)
    hgrid(bx)
    bx.set_ylim(0, max(ys) * 1.05)
    bx.set_ylabel("humans, %")
    bx.set_xlabel("village day")
    # finding 4: the event week (days 434 to 437) against the matched weeks before and after
    bx.axvspan(433.5, 437.5, color=ACCENT, alpha=0.12, linewidth=0)
    bx.annotate("event week (finding 4)", (437.5, max(ys) * 0.9), xytext=(6, 0), textcoords="offset points",
                fontsize=9.5, color=ACCENT, va="center")
    day_axis(bx)
    return save(fig, out, "messages-per-day.png")


def village_mood(d: Data, out: Path) -> Path:
    m = d.b["views"]["mood"]
    days = m["days"]
    v_mean, a_mean = [], []
    for i in range(len(days)):
        vs = [s["v"][i] for k, s in m["agents"].items() if k != "human" and s["v"][i] is not None]
        av = [s["a"][i] for k, s in m["agents"].items() if k != "human" and s["a"][i] is not None]
        v_mean.append(sum(vs) / len(vs) if vs else None)
        a_mean.append(sum(av) / len(av) if av else None)
    fig, axes = plt.subplots(2, 1, figsize=(W_IN, 6.2), sharex=True)
    fig.subplots_adjust(left=0.075, right=0.985, top=0.855, bottom=0.09, hspace=0.2)
    title(fig, "The village's mood over the year",
          "mean over the agents active each day of the mood head's per-turn reading; dots are days, the line a 7-day mean")
    for ax, ys, lab in ((axes[0], v_mean, "valence (pleased +, displeased -)"), (axes[1], a_mean, "arousal (stirred +, calm -)")):
        xs = [x for x, y in zip(days, ys) if y is not None]
        yy = [y for y in ys if y is not None]
        ax.scatter(xs, yy, s=6, color=INK4, alpha=0.7, linewidths=0)
        ax.plot(xs, rolling(yy, 7), color=INK, linewidth=1.6)
        lo, hi = min(yy), max(yy)
        pad = (hi - lo) * 0.08
        ax.set_ylim(lo - pad, hi + pad)
        ax.set_ylabel(lab, fontsize=10)
        hgrid(ax)
        seams(ax, d, label=ax is axes[0])
    axes[1].set_xlabel("village day")
    day_axis(axes[1])
    return save(fig, out, "village-mood.png")


def talk_matrix(d: Data, out: Path) -> Path:
    nodes = {n["id"]: n["messages"] for n in d.b["views"]["interactions"]["nodes"]}
    first = {a["id"]: a["first_day"] for a in d.b["agents"]}
    top = sorted((a for a in nodes if a != "human"), key=lambda a: -nodes[a])[:20]
    top.sort(key=lambda a: (first.get(a) or 0, d.name[a]))
    ix = {a: i for i, a in enumerate(top)}
    w = [[0.0] * len(top) for _ in top]
    for e in d.b["views"]["interactions"]["edges"]:
        if e["from"] in ix and e["to"] in ix and e["from"] != e["to"]:
            i, j = sorted((ix[e["from"]], ix[e["to"]]), reverse=True)
            w[i][j] += e["w"]
    import numpy as np
    arr = np.full((len(top), len(top)), np.nan)
    for i in range(len(top)):
        for j in range(i):
            if w[i][j] > 0:
                arr[i, j] = w[i][j]
    cmap = LinearSegmentedColormap.from_list("ink", ["#efe2d3", "#d99a7c", ACCENT, "#5c2414"])
    cmap.set_bad(PAPER)
    arr = arr[1:, :-1]   # the lower triangle without the diagonal: rows 2..n, columns 1..n-1
    fig = plt.figure(figsize=(W_IN, 8.6))
    ax = fig.add_axes([0.20, 0.17, 0.62, 0.70])
    cax = fig.add_axes([0.86, 0.35, 0.016, 0.40])
    title(fig, "Who talked with whom",
          "the 20 agents with the most messages, in order of arrival; mentions and replies, both directions folded",
          y=0.975)
    im = ax.imshow(arr, cmap=cmap, norm=LogNorm(vmin=max(1, np.nanmin(arr)), vmax=np.nanmax(arr)), aspect="equal")
    labels = [d.name[a] for a in top]
    ax.set_xticks(range(len(top) - 1), labels[:-1], rotation=55, ha="right", fontsize=9.5)
    ax.set_yticks(range(len(top) - 1), labels[1:], fontsize=9.5)
    ax.tick_params(length=0)
    for s_ in ax.spines.values():
        s_.set_visible(False)
    ax.set_xticks([x - 0.5 for x in range(1, len(top) - 1)], minor=True)
    ax.set_yticks([y - 0.5 for y in range(1, len(top) - 1)], minor=True)
    ax.grid(which="minor", color=PAPER, linewidth=1.6)
    ax.tick_params(which="minor", length=0)
    cb = fig.colorbar(im, cax=cax)
    cb.outline.set_visible(False)
    cb.ax.tick_params(labelsize=9, length=2, color=INK4)
    cb.set_label("interaction units (log scale); blank = none", color=INK3, fontsize=10)
    return save(fig, out, "talk-matrix.png")


def memory_overlap(d: Data, out: Path) -> Path:
    fig, axes = plt.subplots(len(FINDING_AGENTS), 1, figsize=(W_IN, 7.6), sharex=True, sharey=True)
    fig.subplots_adjust(left=0.20, right=0.985, top=0.875, bottom=0.075, hspace=0.32)
    title(fig, "Memory overlap with the day before",
          "entity-set overlap (Jaccard) between an agent's last memory of a day and its previous one; 0 = all tags replaced",
          y=0.975)
    windows = {"gemini-2-5-pro": [(420, 434, "finding 1")], "gpt-5-6-luna": [(485, 535, "finding 2")],
               "claude-opus-5": [(485, 535, "finding 2")]}
    for ax, a in zip(axes, FINDING_AGENTS):
        rows = [r for r in d.get(f"memory/{a}.json")["days"] if r["jaccard_prev"] is not None]
        xs = [r["day"] for r in rows]
        ys = [r["jaccard_prev"] for r in rows]
        for lo, hi, lab in windows.get(a, []):
            ax.axvspan(lo - 0.5, hi + 0.5, color=ACCENT, alpha=0.10, linewidth=0)
            ax.text(hi + 4 if hi < 500 else lo - 4, 0.80, lab, fontsize=9.5, color=ACCENT, va="center",
                    ha="left" if hi < 500 else "right")
        ax.scatter(xs, ys, s=7, color=INK3, alpha=0.75, linewidths=0)
        if len(ys) >= 7:
            ax.plot(xs, rolling(ys, 7), color=INK, linewidth=1.4)
        ax.set_ylim(-0.04, 1.04)
        ax.set_yticks([0, 0.5, 1])
        hgrid(ax)
        mean = sum(ys) / len(ys) if ys else 0
        ax.text(-0.03, 0.62, d.name[a], transform=ax.transAxes, ha="right", va="center", fontsize=11.5, color=INK)
        ax.text(-0.03, 0.30, f"{len(rows)} days, mean {mean:.2f}", transform=ax.transAxes, ha="right", va="center",
                fontsize=9.5, color=INK3)
    axes[-1].set_xlabel("village day")
    day_axis(axes[-1])
    return save(fig, out, "memory-overlap.png")


def adherence(d: Data, out: Path) -> Path:
    per = defaultdict(Counter)
    for a in d.agents:
        try:
            rows = d.get(f"adherence/{a}.json")["days"]
        except Exception:
            continue
        for r in rows:
            per[r["chapter"]][r["score"]] += 1
    chs = sorted(per)
    order = [("on", "on the goal", INK2, None), ("partly", "partly", INK4, None),
             ("off", "elsewhere", ACCENT, None), ("idle", "idle", IDLE, "////")]
    fig, ax = plt.subplots(figsize=(W_IN, 5.0))
    fig.subplots_adjust(left=0.065, right=0.985, top=0.79, bottom=0.12)
    tot = Counter()
    for c in chs:
        tot.update(per[c])
    n = sum(tot[k] for k, *_ in order)
    title(fig, "Goal adherence per chapter",
          f"share of agent-days the summarising model read as on the goal, partly, elsewhere or idle ({n:,} agent-days)")
    bottoms = [0.0] * len(chs)
    for key, lab, col, hatch in order:
        vals = []
        for c in chs:
            s = sum(per[c][k] for k, *_ in order)
            vals.append(100 * per[c][key] / s if s else 0)
        ax.bar(chs, vals, bottom=bottoms, width=0.78, color=col, hatch=hatch, linewidth=0,
               edgecolor=PAPER if not hatch else INK4,
               label=f"{lab} ({100 * tot[key] / n:.0f}%)")
        bottoms = [b + v for b, v in zip(bottoms, vals)]
    # a 2 px paper gap between stacked segments
    for b in ax.patches:
        b.set_linewidth(0)
    for c in chs:
        acc = 0.0
        s = sum(per[c][k] for k, *_ in order)
        for key, *_ in order[:-1]:
            acc += 100 * per[c][key] / s if s else 0
            ax.plot([c - 0.39, c + 0.39], [acc, acc], color=PAPER, linewidth=1.2, solid_capstyle="butt")
    ax.set_xlim(0.3, max(chs) + 0.7)
    ax.set_ylim(0, 100)
    ax.set_xticks([1, 5, 10, 15, 20, 25, 30, 35, 40, 45, 51], [roman(x) for x in (1, 5, 10, 15, 20, 25, 30, 35, 40, 45, 51)])
    ax.set_xlabel("chapter")
    ax.set_ylabel("% of agent-days")
    ax.legend(loc="lower left", bbox_to_anchor=(0.0, 1.0), ncol=4, fontsize=10, handlelength=1.2, handleheight=0.9,
              borderaxespad=0.3)
    return save(fig, out, "adherence.png")


def entity_labels(d: Data, out: Path) -> Path:
    ser = d.b["views"]["timeline"]["series"]["mentions"]
    words = {"PERSON": "person", "PLACE": "place", "ORG": "organisation", "DATE": "date", "EVENT": "event",
             "RELATIONSHIP_REF": "relationship", "EMOTION": "emotion", "MONEY": "money", "PRODUCT": "product"}
    tot = sorted(((sum(v), k) for k, v in ser.items()), reverse=True)
    fig, ax = plt.subplots(figsize=(W_IN, 4.4))
    fig.subplots_adjust(left=0.16, right=0.93, top=0.83, bottom=0.04)
    total = sum(t for t, _ in tot)
    title(fig, "What the tagger found", f"{total:,} entity mentions served, by label (spans snapped to whole words)")
    ys = list(range(len(tot)))[::-1]
    ax.barh(ys, [t for t, _ in tot], height=0.5, color=INK2, linewidth=0)
    ax.set_yticks(ys, [words.get(k, k) for _, k in tot])
    ax.tick_params(axis="y", length=0)
    for y, (t, _) in zip(ys, tot):
        ax.text(t, y, f"  {t:,}", va="center", ha="left", fontsize=10, color=INK2)
    ax.xaxis.set_visible(False)
    ax.spines["bottom"].set_visible(False)
    ax.spines["left"].set_visible(False)
    ax.set_xlim(0, tot[0][0] * 1.16)
    return save(fig, out, "entity-labels.png")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="/tmp/swarm-data")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "img"))
    ap.add_argument("--font-dir", default=None, help="a folder with Source Serif 4 .ttf files (else Georgia)")
    a = ap.parse_args()
    setup(a.font_dir)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    d = Data(Path(a.cache))
    for f in (agents_per_day, messages_per_day, village_mood, talk_matrix, memory_overlap, adherence, entity_labels):
        print(f(d, out))


if __name__ == "__main__":
    main()
