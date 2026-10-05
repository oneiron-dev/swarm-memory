# pipeline/common: the model client, the mock, the work area

Shared by every stage. Python 3.12, stdlib only.

## The model client (`swarm_llm.py`)

Every step that calls a model (`summarize.py run` and `audit-existing`, the eight `book.py` steps, `human_ids.py classify`, `pf.py classify`, `docs/questions/make_questions.py`) takes the same flags. Only an explicit choice picks a backend: a flag first (`--mock`, then `--endpoint`, then `--prime-agent`), else the environment in this order: `SWARM_LLM_MOCK=1`, `SWARM_LLM_BASE_URL`, `PRIME_AGENT`. Nothing is found on PATH. With none of them the step stops with `no model backend configured` and the line that sets one.

| backend | flags | environment |
|---|---|---|
| mock: no model, templated and cited placeholders | `--mock` | `SWARM_LLM_MOCK=1` |
| any OpenAI-compatible chat-completions server (OpenAI, OpenRouter, vLLM, llama.cpp, Ollama, ...) | `--endpoint <base URL> --model <id>` | `SWARM_LLM_BASE_URL`, `SWARM_LLM_MODEL`, `SWARM_LLM_API_KEY` (or `OPENAI_API_KEY`) |
| prime-agent, our own harness (the real run) | `--prime-agent <path>` (`--provider`, `--thinking`) | `PRIME_AGENT=<path>` (a `prime-agent` on PATH is never picked) |

- For a dry run of every model step, `export SWARM_LLM_MOCK=1` once; it wins over `SWARM_LLM_BASE_URL` and `PRIME_AGENT`, so a forgotten flag never spends a real backend.
- The key is read from the environment inside the process. It is never printed, logged, written to a file or put in an error message. A local server needs no key.
- An endpoint gets one system message and one user message: the material first, then the task line, as prime-agent gets them on stdin and as its prompt. Steps whose reply is one JSON object ask for JSON mode (`response_format: json_object`); a server that refuses it with a 400 is asked in words from then on (`SWARM_LLM_JSON_MODE=0` turns it off up front).
- `--reasoning-effort` (or `SWARM_LLM_REASONING_EFFORT`) is sent as `reasoning_effort` only when set.
- **Model check.** A reply that names another model than the one asked for raises `ModelMismatch`, and the step stops (exit 3) without keeping its text. A dated or tagged snapshot of the asked model (`gpt-4o-mini-2024-07-18` for `gpt-4o-mini`) and a provider prefix (`openai/gpt-4o-mini`) count as the same.
- One attempt per call; 429, 5xx and connection errors are marked rate-limited. The steps keep their own retries and backoff.
- `pf.py` names the model flag `--llm-model`, because its `--model` is the privacy-filter checkpoint.

## The mock (`swarm_mock.py`)

Writes, for each step, a reply that passes the same acceptance rules a model's reply must pass: word bounds, citation numbers taken from the material, JSON shapes, the book's hook, beat, pair and stage rules. It never copies source text (it writes counts, times, agent names and line numbers), so it cannot carry a human's name into the bundle. Every record it makes carries `"model": "mock"` and says it is a placeholder. Use it to see the pipeline run (`scripts/smoke.sh`), never for a bundle you publish as a reading of the archive.

`fake_openai.py` serves the mock as a local OpenAI-compatible endpoint, to try the `--endpoint` path with no key:

```bash
python3 pipeline/common/fake_openai.py --port 8901 --model fake-1 &
export SWARM_LLM_BASE_URL=http://127.0.0.1:8901/v1 SWARM_LLM_MODEL=fake-1
```

## The work area and the lists (`swarm_paths.py`)

Every stage defaults to the work area `$SWARM_WORK` (`--root`, `--work` or `--out`, as each stage always named it). The redaction lists live in `--lists <dir>` (or `$SWARM_LISTS`), by default `$SWARM_WORK/meta`; the builder creates a missing list empty and says what the rules alone cut (see `pipeline/bundle/README.md`, "The lists").

Tests: `python3 -m unittest discover -s pipeline/common/tests` (the client against a local fake server, the pick order, the mock).
