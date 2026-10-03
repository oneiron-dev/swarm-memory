"""Reasoning text from provider-shaped raw model output (events `data.output`).

Returns (shape, [texts]). `shape` names the structure that matched; texts are
reasoning only: visible replies and tool calls are skipped.
"""

UNPARSED = "unparsed"


def _anthropic_blocks(blocks):
    out = []
    for b in blocks:
        if isinstance(b, dict) and b.get("type") == "thinking":
            t = b.get("thinking")
            if isinstance(t, str) and t.strip():
                out.append(t)
    return out


def _responses_items(items):
    out = []
    for it in items:
        if not isinstance(it, dict) or it.get("type") != "reasoning":
            continue
        parts = [s.get("text") for s in it.get("summary") or [] if isinstance(s, dict)]
        if not any(isinstance(p, str) and p.strip() for p in parts):
            # Open-weight Responses models put raw reasoning in content[].
            parts = [c.get("text") for c in it.get("content") or []
                     if isinstance(c, dict) and c.get("type") == "reasoning_text"]
        out.extend(p for p in parts if isinstance(p, str) and p.strip())
    return out


def _gemini(cands):
    out = []
    for c in cands or []:
        parts = ((c or {}).get("content") or {}).get("parts") or []
        for p in parts:
            if isinstance(p, dict) and p.get("thought") and isinstance(p.get("text"), str) and p["text"].strip():
                out.append(p["text"])
    return out


def _chat_message(m):
    for key in ("reasoning_content", "reasoning"):
        v = m.get(key)
        if isinstance(v, str) and v.strip():
            return [v]
    out = []
    for d in m.get("reasoning_details") or []:
        if not isinstance(d, dict):
            continue
        v = d.get("text") if d.get("type") == "reasoning.text" else d.get("summary") if d.get("type") == "reasoning.summary" else None
        if isinstance(v, str) and v.strip():
            out.append(v)
    return out


CHAT_KEYS = {"reasoning", "reasoning_content", "reasoning_details", "tool_calls", "refusal"}


def reasoning(output):
    """Return (shape, texts) for one raw model output."""
    if isinstance(output, list):
        types = {it.get("type") for it in output if isinstance(it, dict)}
        if types & {"reasoning", "function_call", "message"}:
            return "responses_items", _responses_items(output)
        if types & {"thinking", "text", "tool_use", "redacted_thinking"}:
            return "anthropic_blocks", _anthropic_blocks(output)
        return UNPARSED, []
    if isinstance(output, dict):
        if "candidates" in output:
            return "gemini_candidates", _gemini(output["candidates"])
        if isinstance(output.get("choices"), list):
            out = []
            for c in output["choices"]:
                out.extend(_chat_message((c or {}).get("message") or {}))
            return "chat_choices", out
        if isinstance(output.get("content"), list) and output.get("type") == "message":
            return "anthropic_message", _anthropic_blocks(output["content"])
        if isinstance(output.get("output"), list):
            return "responses_object", _responses_items(output["output"])
        if "role" in output and (CHAT_KEYS & output.keys() or "content" in output):
            return "chat_message", _chat_message(output)
        return UNPARSED, []
    return UNPARSED, []
