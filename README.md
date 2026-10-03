# Swarm memory: the AI Village through Oneiron

**Live demo:** https://arch.tail87c1d7.ts.net
**Code:** https://github.com/oneiron-dev/swarm-memory
**Results write-up:** [docs/FINDINGS.md](docs/FINDINGS.md) (method: [docs/findings-method.md](docs/findings-method.md))
**Demo video (2 min):** [swarm-memory-demo.mp4](https://github.com/oneiron-dev/swarm-memory/releases/download/demo-video-20261004/swarm-memory-demo.mp4) (also at https://arch.tail87c1d7.ts.net/media/swarm-memory-demo.mp4)

Data: AI Digest / AI Village (theaidigest.org/village; huggingface.co/datasets/aidigestorg/ai-village, revision 838b4150). Research use only.

## What it is

To oversee a swarm you need to know what each agent knew, said and remembered, and when. The AI Village logs hold all of that, but as 562,738 text units across village days 1 to 535 (404 days with recorded activity). Nobody can read them.

We ran the logs through **Oneiron**, our memory engine for agents, and **oneiroNER**, our small on-device tagger. The result is a fast static site where a judge can see, in two minutes:

- **Timeline:** each village day with activity, its goal, who worked, what was named, and AI Digest's own daily summary.
- **Agents:** each of the 46 agents with its goals, the entities it talks about, its mood over time, and a cited summary for (nearly) every day it was active.
- **Who talks to whom:** a network of mentions and replies between agents and humans, filtered by day range or village goal.
- **Memory drift:** the last memory each agent kept on each day, which tagged entities it added and dropped, and which it always kept. Drift is entity-tag turnover, not proof of forgetting.
- **Entities:** the top 2,000 people, places, orgs, products, events, dates and emotions the agents named, with timelines and example snippets.
- **Findings:** six results we found with the tool (see docs/FINDINGS.md), each linked to the view that shows it.

Every generated summary must cite numbered source lines, and the build drops any citation it cannot resolve. Click a citation and the source snippet opens with the tagged words highlighted.

## How it works

1. **Slice** (154 s on one box): chat, session goals and summaries, memory consolidations, reasoning text, Claude Code messages, search-history queries and AI Digest's summaries become 562,738 records and 180.6M tokens. Computer-use screenshots and tool output are out of scope.
2. **Tag:** oneiroNER (checkpoint rd16c-ch with its mood head) tags every record on two Apple-silicon machines with MLX: 9 entity labels plus valence, arousal and dominance per turn. Chat is tagged as whole conversations (2,048-token windows with role prefixes), which is how the model reads dialogue best. Everything else is tagged per document. The bulk run took 1 hour 46 minutes (about 2 hours with the chat re-tag in conversation windows) and produced about 3 million spans; every span offset was checked against its source text. A blind LLM check ([docs/register-check.md](docs/register-check.md)) on 80 chat messages where the two ways of reading differ rated the conversation windows more precise (68% vs 65%) with 1.7 times as many correct spans.
3. **Summarize:** gpt-5.6-luna writes 5,005 daily summaries, one per agent per day and per room per day. Each sentence must cite numbered source lines; citations are checked against the source map, and a second pass removes human names.
4. **Bundle:** a builder joins slice, tags and summaries into a 69 MB static bundle: entities, timelines, the interaction network, memory drift (entity-set overlap between consecutive daily memories) and the cited source snippets. A validator checks every citation, size cap and redaction before a new bundle goes live.
5. **Serve:** a small read-only server on our own machine, behind a public HTTPS link.

## Data handling

- The dataset stays on our machines. Only the derived bundle is public.
- No training or fine-tuning on the data. Nothing from it enters any model's training mix.
- Humans are never individuals: every human speaker is one `human` node. Human names, emails and ids are redacted as `[human]`, `[email]` and `[id]` in every served string, using the dataset's own display names plus oneiroNER's PERSON tags. Agent names are model names and stay.

## Limits

- Entities are unconfirmed tags, not facts. The tagger can mislabel; we snap spans to whole words and show the raw mention counts.
- Summaries are written by an LLM. They cite their sources, and you can check every claim, but they can still be wrong.
- We used the checkpoint whose mood head is validated (rd16c-ch), not our newer NER trunk.
- Redaction is heuristic. Common-word names are redacted where the tagger marks them as a person.

## Reproduce

`pipeline/slice`, `pipeline/summarize`, `pipeline/tag`, `pipeline/bundle`, `pipeline/serve` and `web/` each have a README with the exact commands; `docs/schema.md` is the data contract; `docs/video/record.py` re-records the demo. The tagger (oneiroNER) is our own model and is not part of this repo; `docs/schema.md` gives its input and output format.

## Team

Oleksii Kyrylchuk (Oneiron).
