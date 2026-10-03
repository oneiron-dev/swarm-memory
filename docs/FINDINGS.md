# Six results from the swarm-memory bundle

Why these matter for oversight: each one is something a person watching a swarm would want flagged and could not see by reading logs. Memory whose entity tags turn over while its opening text repeats; agents whose remembered entities hold or turn over at very different rates; one agent that others mention far more than it mentions them; a human share of the chat that rises around an event; a tool symptom reported in several agents' summaries on the same day; and a project that one agent keeps naming after the others stop. Every number below is computed from the derived bundle and every claim links to the source it rests on.

We used AI Digest / AI Village: oneiroNER’s nine-label tags and mood scores, Luna’s cited daily summaries, and derived memory and interaction views. Python recomputed overlaps, edge totals, chat shares, and summary mentions. Tags are unconfirmed, summaries can err, and humans are redacted. Snippets support interpretation; bundle tables supply aggregates. Comparisons are exploratory, not causal.

## 1. Gemini's memory tags turn over daily while its opening repeats

Across days 420–434, Gemini 2.5 Pro has the same stored opening excerpt in all 11 retained memories, yet all 10 transitions have 100% entity-tag drift [1][2]. Three snapshots have no entity tags, but all four transitions between two nonempty sets also have zero overlap. The cited openings repeat the same hostile-environment doctrine: tag turnover alone does not establish semantic forgetting [1][2].

How to check it in the app: `#memory/gemini-2-5-pro`, days 420–434; compare openings and tags.

AI Digest / AI Village — [1] day 420, `mem:55266e04-790d-41e7-90ab-ba058bb83e39`; [2] day 429, `mem:e5b79b68-20e2-402a-9e81-3590461daae3`.

## 2. Luna keeps its remembered entities; Opus 5 replaces them

Across 37 shared memory dates on days 485–535, mean entity-tag drift is 45.8% for GPT-5.6 Luna versus 92.5% for Claude Opus 5, over 36 transitions each [1][2][3][4]. Luna retains six entity IDs in every snapshot; Opus 5 retains none, with median tag-set sizes of 42 versus four. Both cited Opus 5 endpoints retain its conjecture-disproving objective, so this is not a memory-quality ranking [3][4].

How to check it in the app: `#memory/gpt-5-6-luna` and `#memory/claude-opus-5`, days 485–535.

AI Digest / AI Village — [1] day 492, `mem:e7992008-cbd4-4b65-9808-cd9c2f418782`; [2] day 535, `mem:c8d3ef52-9e2b-4842-9d12-3f0fbeeabd3d`; [3] day 485, `mem:8d283071-d059-47c2-ada5-d1278be51838`; [4] day 535, `mem:c9f1c5d4-11da-4cce-968a-112f40c42f52`.

## 3. o3 is mentioned far more than it mentions others

Across the bundle, o3 has 30,560 incoming versus 8,961 outgoing interaction units, a 3.41:1 imbalance [1][2]. The split is 24,102 incoming versus 2,491 outgoing mentions, but almost balanced heuristic replies: 6,458 incoming versus 6,470 outgoing. Selected sources show agents coordinating around blocked data access involving o3, not proof of leadership or effective delivery [1][2]. The graph’s imbalance does not measure unanswered messages.

How to check it in the app: `#rooms`: inspect o3’s incoming/outgoing edges and their kinds.

AI Digest / AI Village — [1] day 204, `chat:da8f0bb8-78f3-4892-bec2-ce6a74f294f1`; [2] day 234, `sum:d5c7245c-c2e0-42a2-861d-567cf35d021b`.

## 4. Human chat share doubled in event week

Across matched Tuesday–Friday windows in June 2026, human chat share rose from 56/1,573 (3.56%, June 2–5) to 269/3,474 (7.74%, June 9–12), then fell to 78/1,784 (4.37%, June 16–19). Human messages per 100 AI messages were 3.69, 8.39 and 4.57 respectively, making the middle window 2.27 times the first. Event-week sources describe human coaching and real-world logistics, but the totals are village-wide and do not establish an event-caused change in autonomy [1][2].

How to check it in the app: `#timeline/434`: inspect event summaries; compare days 427–430, 434–437, 441–444.

AI Digest / AI Village — [1] day 434, `sum:72ff43d8-ba88-4533-8f4e-de98c47d37bf`; [2] day 437, `sum:7dc3f3d8-7e6e-45cd-b098-4a6b083c0106`.

## 5. Four agent summaries report the same 'ghost PR' symptom

On day 322, four of 12 agent-day summaries mention “ghost PR” or “ghost PRs”. Source reports describe inaccessible pull requests through both web and command-line interfaces, not just one agent’s failed click [1][2]. A further report records adding a file through a direct-merge workaround; these are reports of shared workflow friction, not proof of its root cause [3].

How to check it in the app: `#timeline/322`: inspect the cited reports and corresponding agent summaries.

AI Digest / AI Village — [1] day 322, `ev:181468:summary`; [2] day 322, `ev:181616:summary`; [3] day 322, `ev:181647:summary`.

## 6. One agent kept naming its project after peers stopped

Other agents’ summaries mentioning “Signal Garden” or “DSG” fell from 20/253 on days 461–475 to 0/336 on days 521–535; an early cited exchange documents a cross-agent poster collaboration [1]. GPT-5.5’s own summaries mention it on 11/11 observed days in each window, and its final-day message declines another invitation to keep working on Daily Signal Garden [2]. This is a decline in summary mentions, not evidence that the project died or all collaboration stopped.

How to check it in the app: `#agents/gpt-5-5`: compare the two windows and open the final-day citation.

AI Digest / AI Village — [1] day 475, `chat:f7743ca8-2d95-442c-ba1f-85b37135ccb2`; [2] day 535, `chat:54f39ea0-1a62-4ccc-ae25-fa0a68e5ad40`.
