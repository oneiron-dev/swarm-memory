# Chat register check (2026-10-04 00:1x JST)
Blind LLM judge (Sol gpt-6.1, -t high) on 80 chat messages sampled from the 1,469 of 2,000 where the two registers' spans differ (seed 20261004; X/Y order randomised per row; key kept locally).
- Row register (one message per 512-token window): 131 correct, 70 wrong → precision 0.652
- Conversation register (room windows up to 2,048 tokens, role prefixes A:/B:): 222 correct, 103 wrong → precision 0.683
- Correct spans conversation/row: 1.69x. Net (correct − wrong) better per message: conversation 45, row 27, tie 8. Missed by both: 52.
- Caveats: silver judge, a sample biased to disagreements, label leniency for agent names.
Decision: keep conversation register for chat.
