# Method notes

Data attribution: **AI Digest / AI Village**, dataset revision `838b4150`. All work used the local derived bundle; no source data or pipeline constants were changed. No human identity lookup was attempted.

## Reproduce the selected numbers

Run the standalone, standard-library script (no project environment or dependencies are supplied with this bundle):

```text
# save the script below as analysis-work/reproduce-findings.py next to the bundle's data/ folder, then:
python3 analysis-work/reproduce-findings.py
```

The first command was run, with its output saved to analysis-work/selected-metrics.json. The exact Python follows; paths inside the script are resolved relative to its own location, not the caller’s working directory.

```python
from pathlib import Path
from statistics import mean, median
from collections import Counter
import json, re
R = Path(__file__).resolve().parents[1]
def load(p): return json.loads((R/'data'/p).read_text())
b = load('bundle.json')
def memories(a): return load(f'memory/{a}.json')['days']
def drift(rows):
    return [1-len(x&y)/len(x|y) for a,c in zip(rows,rows[1:])
            if (x:=set(a['entities'])) | (y:=set(c['entities']))]
out = {}
r = [x for x in memories('gemini-2-5-pro') if 420<=x['day']<=434]
nonempty = [(a,c) for a,c in zip(r,r[1:]) if a['entities'] and c['entities']]
out['repeated_opening'] = dict(snapshots=len(r), unique_openings=len({x['excerpt'] for x in r}),
    transitions=len(drift(r)), drift=mean(drift(r)), empty=sum(not x['entities'] for x in r),
    nonempty_pairs=len(nonempty), nonempty_pair_drifts=[drift([a,c])[0] for a,c in nonempty])
ms={a:memories(a) for a in ('gpt-5-6-luna','claude-opus-5')}
shared=set.intersection(*({x['day'] for x in r if 485<=x['day']<=535} for r in ms.values()))
out['matched_memory']={}
for a,r in ms.items():
    r=[x for x in r if x['day'] in shared]; sets=[set(x['entities']) for x in r]
    adjacent=[drift([p,q])[0] for p,q in zip(r,r[1:]) if q['day']-p['day']==1]
    out['matched_memory'][a]=dict(n=len(r), transitions=len(drift(r)), drift=mean(drift(r)),
        persistent=len(set.intersection(*sets)), median_tags=median(map(len,sets)),
        adjacent_n=len(adjacent), adjacent_drift=mean(adjacent))
e=b['views']['interactions']['edges']; out['o3']={}
for name,key in (('incoming','to'),('outgoing','from')):
    out['o3'][name]=dict(sum((Counter(x['kinds']) for x in e if x[key]=='o3'),Counter()))
out['o3']['ratio']=sum(out['o3']['incoming'].values())/sum(out['o3']['outgoing'].values())
t=b['views']['timeline']; out['human_windows']=[]
for start,end in [('2026-06-02','2026-06-05'),('2026-06-09','2026-06-12'),('2026-06-16','2026-06-19')]:
    days={x['day'] for x in b['days'] if start<=x['date']<=end}
    ix=[i for i,d in enumerate(t['days']) if d in days]
    c={a:sum(v[i] for i in ix) for a,v in t['series']['chat'].items()}
    h,all_=c.get('human',0),sum(c.values())
    out['human_windows'].append(dict(days=sorted(days), human=h, all_chat=all_, ai=all_-h,
        human_share=100*h/all_, human_per_100_ai=100*h/(all_-h)))
out['human_middle_to_first']=out['human_windows'][1]['human_per_100_ai']/out['human_windows'][0]['human_per_100_ai']
s=[x for p in (R/'data/summaries/agent').glob('*.json') for x in json.loads(p.read_text()) if x['key']!='human']
g=[x for x in s if x['day']==322]
out['ghost_pr']=dict(denominator=len(g), agents=[x['key'] for x in g if re.search(r'\bghost[ -]PRs?\b',x['text'],re.I)])
out['signal_garden']=[]
for lo,hi in [(461,475),(521,535)]:
    for owner in (True,False):
        rows=[x for x in s if lo<=x['day']<=hi and (x['key']=='gpt-5-5')==owner]
        hits=[x for x in rows if re.search(r'\bsignal garden\b|\bDSG\b',x['text'],re.I)]
        out['signal_garden'].append(dict(window=[lo,hi],owner=owner,hits=len(hits),
            denominator=len(rows),observed_days=len({x['day'] for x in rows})))
print(json.dumps(out,indent=2))
```

## What each number means

1. **Repeated opening, days 420–434.** Select Gemini 2.5 Pro’s retained rows in that inclusive range. There are 11 rows, one distinct `excerpt`, and 10 successive pairs. Recompute `1 - len(A & B)/len(A | B)` from entity-ID sets: every pair is 1.0 (100% drift). Three rows have empty sets. Four pairs have nonempty sets on both sides; every one still has drift 1.0. The citations verify selected repeated openings, not an unchanged full memory. This window was selected after inspecting repeated excerpts.

2. **Matched retention, days 485–535.** Intersect the observed dates of GPT-5.6 Luna and Claude Opus 5 within this range, giving 37 rows and 36 transitions per agent. Day 485 follows the objective assignment visible in the cited Opus 5 memory; it is an analyst-chosen baseline, not a village-goal boundary. Equal-weight mean drift is about 0.458 and 0.925, displayed as 45.8% and 92.5% (final bundle, after human-identifier entities were removed; an earlier build gave 45.7%). Intersect every set per agent: six persistent IDs for Luna, zero for Opus 5. Median set sizes are 42 and four. Both agents have nonempty sets throughout. Restricting to the 29 one-day transitions gives 45.3% versus 91.1%; seven transitions span gaps. The objective-language check uses the two cited Opus 5 endpoints, not every full memory.

3. **o3 interaction imbalance, full bundle.** Sum `edges[].kinds` by stored `to == 'o3'` and `from == 'o3'`. Incoming: 24,102 mention + 6,458 reply = 30,560. Outgoing: 2,491 mention + 6,470 reply = 8,961. Divide totals for 3.4103336681, displayed as 3.41:1. This includes references after o3’s last observed chat; it is not restricted to its active tenure. The cited blocker episodes illustrate context only and do not classify all edges.

4. **Human participation.** Choose dates using `days[].date`, then align those day keys with the chat arrays. June 2–5 maps to days 427–430; June 9–12 to 434–437; June 16–19 to 441–444. Each has four distinct Tuesday–Friday dates. Sum human/all-speaker chat: 56/1,573, 269/3,474, 78/1,784. Multiply by 100 for 3.56%, 7.74%, 4.37%. AI-only denominators are 1,517, 3,205, 1,706; human/AI × 100 gives 3.69, 8.39, 4.57. Divide the unrounded middle rate by the first: 2.2736405170, displayed as 2.27. These windows avoid transition Mondays and compare equal weekdays, but do not control changing rosters or parallel room goals.

5. **Repository symptom, day 322.** Count all 12 non-human agent-day summaries, then case-insensitive matches to `\bghost[ -]PRs?\b`. Four match: Claude Opus 4.6, Claude Sonnet 4.5, Gemini 2.5 Pro, Gemini 3 Pro. This is a count of summaries mentioning the symptom, not four independent verified incidents. Gemini 3 Pro’s summary discusses documentation; do not label it a fourth direct obstruction. The selected Opus and Sonnet source reports substantiate web/CLI access problems and a reported workaround, not the underlying cause or independent success verification.

6. **Signal Garden summary mentions.** Match `\bsignal garden\b|\bDSG\b`, case-insensitive, once per summary; split GPT-5.5 from all other non-human agents. Inclusive day ranges 461–475 and 521–535 are equal-width exploratory windows with 11 observed dates each. Other-agent counts are 20/253 and 0/336; GPT-5.5 counts are 11/11 in both. Denominators are available summaries, not the changing roster or raw chat volume. The alias rule can miss paraphrases. The chosen early source documents a poster collaboration; the late source explicitly prioritizes game upkeep. Neither establishes usage trends or the absence of other collaboration.

## Coverage, assumptions and limits

- The bundle contains 46 AI agents plus one pooled `human` node. There are 404 day records in the index range 1–535, not 535 observed daily records. The bundle’s final date is **2026-09-18**, not the brief’s September 20. Always use the stored citation day; self-written day numbers inside snippets sometimes disagree.
- The supplied goal table’s mapped start days disagree with its timestamp dates for 49 of 51 entries. Five dates have duplicate day records. We did not use goal `first_day`/`last_day` as causal change points. The human comparison uses explicit, unique recorded dates instead.
- Memory files hold retained daily samples, not every consolidation. Initial rows supply a baseline, never a drift observation. A both-empty union is undefined and excluded; one-empty unions yield drift 1. No missing days are filled in. Text deduplication, sparse extraction, changing set size and limited label coverage can affect apparent churn. Identical opening excerpts do not imply identical full memories, and an absent entity tag does not imply forgetting.
- The audit found 4,491 agent summaries across 391 day keys; 199 carry a `sampled` flag. Eight active agent-days lack summaries. Counts use existing summaries and do not interpret missing summaries as inactivity. Luna summaries can omit actions, emphasize memory text, or err; summary mentions are not a direct attention or collaboration measure.
- Mention edges are unconfirmed tag/name matches. Reply edges are the next different speaker in the same room within ten minutes, not verified replies. We use stored direction, not a response-rate interpretation. An edge unit is not necessarily a distinct message; mention and reply contributions can overlap. No day-by-kind split or edge-to-source mapping is supplied.
- All selected citations exist in their corresponding source-day JSON and were read. Source snippets are selected, capped at 600 characters, and are often self-reports. They contextualize aggregate computations; a few citations cannot independently reproduce a corpus-wide total. The supplied app routes and IDs were checked against the schema and bundle, but no running app or frontend source was supplied for browser testing.
- The full citation audit found no missing summary citations or entity examples. Every stored edge weight reconciles with its daily and kind totals; recomputed memory Jaccard values agree within rounding. Entity timeline totals reconcile, but 1,051 of 2,000 entities have fewer agent-assigned mentions than total mentions. We therefore did not use per-agent entity counts as a complete partition of all mentions.
- Mood means were explored but not selected: no stronger, well-contextualized mood result was needed. Entity labels/aliases were not merged or relabeled. All windows and regexes above are analyst choices; there were no significance tests, causal models, inferred human identities, or changes to input constants.

## Execution records (local, not in this repo)

Exploration and checks are retained in analysis-work. The main audit scripts are analysis-work/audit.py and analysis-work/integrity.py; independent lane analyses are analysis-work/memory.py, analysis-work/network.py and analysis-work/patterns.py. The final six findings supersede longer candidate drafts. JSON titles, allowed routes, source refs/day keys, citation markers and the judge-write-up word count were validated by analysis-work/validate.py.
