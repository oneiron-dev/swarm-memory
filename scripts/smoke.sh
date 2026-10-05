#!/usr/bin/env bash
# The whole swarm-reader runbook on an invented archive, with no model and no key: the integration test.
#
#   scripts/smoke.sh                       # setup (first time), then every step; prints timings; exit 0 = it works
#   SMOKE_WORK=/tmp/w scripts/smoke.sh     # where the work area goes (default ${TMPDIR:-/tmp}/swarm-smoke)
#   SMOKE_ADAPTER=none scripts/smoke.sh    # skip the tagger download (no entities, no mood)
#   SMOKE_LIMIT=50 scripts/smoke.sh        # tag only the first 50 rows of each kind
#   SMOKE_PF_MODEL=<dir> scripts/smoke.sh  # also run the privacy-filter sweep (Apple Silicon, a local MLX checkout)
#   SMOKE_CLIENT=endpoint scripts/smoke.sh # model steps over HTTP to a local fake OpenAI-compatible server instead
#
# Every model step runs with SWARM_LLM_MOCK=1 (pipeline/common/swarm_mock.py): templated, cited placeholder text, set
# once as the runbook's dry run does. The run ends with the validator at 0 errors, serve.py --check at ok, a scan that
# no planted identifier reached the bundle, and a check that the mock's placeholder sentence is served uncut.
set -euo pipefail
R=$(cd "$(dirname "$0")/.." && pwd)
W=${SMOKE_WORK:-${TMPDIR:-/tmp}/swarm-smoke}
W=${W%/}
ADAPTER=${SMOKE_ADAPTER:-gliner_vad}
LIMIT=${SMOKE_LIMIT:-}
VENV=${SWARM_VENV:-$R/.venv}
PY=$VENV/bin/python
export SWARM_WORK=$W
unset SWARM_LISTS SWARM_LLM_BASE_URL SWARM_LLM_MOCK SWARM_LLM_MODEL PRIME_AGENT || true
CLIENT=${SMOKE_CLIENT:-mock}
if [ "$CLIENT" = mock ]; then
  export SWARM_LLM_MOCK=1  # the runbook's dry run: every model step reads it, no flag needed
fi

T0=$(date +%s)
TIMES=()
step_start=0
step() { step_name=$1; step_start=$(date +%s); printf '\n== %s\n' "$1"; }
done_step() { local d=$(( $(date +%s) - step_start )); TIMES+=("$(printf '%-34s %4ss' "$step_name" "$d")"); }
fail() { echo "SMOKE FAIL: $*" >&2; exit 1; }

if [ -e "$W" ]; then
  [ -f "$W/.swarm-smoke" ] || fail "$W exists and is not a smoke work area; pick another SMOKE_WORK"
  rm -rf "$W"
fi
mkdir -p "$W" && touch "$W/.swarm-smoke"

step "setup (venv, requirements)"
if [ "$ADAPTER" = gliner_vad ]; then
  "$R/scripts/setup.sh" --tagger
else
  "$R/scripts/setup.sh"
fi
done_step

if [ "$CLIENT" = endpoint ]; then
  PORT=$("$PY" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
  "$PY" "$R/pipeline/common/fake_openai.py" --port "$PORT" --model fake-1 > "$W/fake-openai.log" 2>&1 &
  FAKE_PID=$!
  trap 'kill $FAKE_PID 2>/dev/null || true' EXIT
  export SWARM_LLM_BASE_URL=http://127.0.0.1:$PORT/v1 SWARM_LLM_MODEL=fake-1
  sleep 1
  echo "model client: fake endpoint $SWARM_LLM_BASE_URL (model fake-1)"
fi

step "0 sample archive"
"$PY" "$R/samples/tiny-village/make_tiny_village.py" --out "$W/archive.jsonl"
done_step

step "1 slice (from_jsonl)"
"$PY" "$R/pipeline/slice/from_jsonl.py" "$W/archive.jsonl" \
  --name "Tiny Village (invented sample)" --cite "Data: Tiny Village, an invented sample archive"
done_step

step "2 tag the slice ($ADAPTER)"
"$PY" "$R/pipeline/tag/run_tagger.py" --adapter "$ADAPTER" ${LIMIT:+--limit "$LIMIT"}
done_step

step "3 summaries (estimate, run, audit)"
"$PY" "$R/pipeline/summarize/summarize.py" estimate > "$W/estimate.json"
"$PY" "$R/pipeline/summarize/summarize.py" run
"$PY" "$R/pipeline/summarize/summarize.py" audit-existing
done_step

step "4 tag the summaries"
"$PY" "$R/pipeline/tag/run_tagger.py" --adapter "$ADAPTER" --summaries
done_step

step "5 lists (human names)"
"$PY" "$R/pipeline/bundle/human_names.py" --archive "$W/archive.jsonl"
done_step

step "6 first bundle"
"$PY" "$R/pipeline/bundle/build_bundle.py" > "$W/build-1.log" || { cat "$W/build-1.log"; fail "first build"; }
tail -1 "$W/build-1.log"
"$PY" "$R/pipeline/bundle/validate_bundle.py"
done_step

step "7 handle sweep (human_ids)"
"$PY" "$R/pipeline/bundle/human_ids.py" candidates
"$PY" "$R/pipeline/bundle/human_ids.py" classify
"$PY" "$R/pipeline/bundle/human_ids.py" scan
"$PY" "$R/pipeline/bundle/human_ids.py" hits
# a person reviews every hit; the smoke run accepts each mock hit, which redacts more, never less
"$PY" - "$W/human_ids" <<'PYEOF'
import json, os, sys
work = sys.argv[1]
hits = json.load(open(os.path.join(work, "hits.json")))
with open(os.path.join(work, "review.txt"), "w") as f:
    for k in hits:
        if k.startswith("h"):
            f.write(f"{k} accept smoke run: every mock classifier hit is accepted\n")
print(f"review: {sum(1 for k in hits if k.startswith('h'))} classifier hits accepted")
PYEOF
"$PY" "$R/pipeline/bundle/human_ids.py" write --review "$W/human_ids/review.txt"
done_step

step "8 book (eight steps)"
for s in days gaps chapters hooks pairs stage adherence rephrase; do
  "$PY" "$R/pipeline/book/book.py" "$s" --parallel 4 > "$W/book-$s.log" || { tail -20 "$W/book-$s.log"; fail "book $s"; }
  f=$s; [ "$s" = rephrase ] && f=rewrites
  echo "book $s: $(grep -c . "$W/book/$f.jsonl" 2>/dev/null || echo 0) records"
done
[ -s "$W/book/pairs.jsonl" ] || fail "book pairs wrote nothing"
done_step

step "9 full bundle"
"$PY" "$R/pipeline/bundle/build_bundle.py" > "$W/build-2.log" || { cat "$W/build-2.log"; fail "second build"; }
tail -1 "$W/build-2.log"
"$PY" "$R/pipeline/bundle/validate_bundle.py"
done_step

step "10 red-team fold"
mkdir -p "$W/redteam"
# a red-team agent writes one row per identifier it finds in the served bundle (SKILL.md section 5.5)
printf '%s\n' '{"file": "data/sources/2.json", "ref_or_path": "$", "identifier": "pellv42", "kind": "handle", "context": "smoke run: the planted handle", "confidence": "high"}' > "$W/redteam/leaks.jsonl"
: > "$W/redteam/review.txt"
"$PY" "$R/pipeline/bundle/human_ids.py" redteam --leaks "$W/redteam/leaks.jsonl" --review "$W/redteam/review.txt"
done_step

if [ -n "${SMOKE_PF_MODEL:-}" ]; then
  step "11 privacy-filter sweep"
  "$R/scripts/setup.sh" --pf
  "$PY" "$R/pipeline/pf/pf.py" collect
  "$PY" "$R/pipeline/pf/pf.py" run --model "$SMOKE_PF_MODEL"
  "$PY" "$R/pipeline/pf/pf.py" filter
  "$PY" "$R/pipeline/pf/pf.py" classify
  "$PY" "$R/pipeline/pf/pf.py" review
  "$PY" - "$W/pf" <<'PYEOF'
import json, os, sys
work = sys.argv[1]
hits = json.load(open(os.path.join(work, "review.json")))
with open(os.path.join(work, "review.txt"), "w") as f:
    for k in hits:
        f.write(f"{k} accept smoke run: every mock hit is accepted\n")
PYEOF
  "$PY" "$R/pipeline/pf/pf.py" write --review "$W/pf/review.txt"
  done_step
fi

step "12 questions"
"$PY" "$R/docs/questions/make_questions.py" > "$W/questions.log" || { tail -20 "$W/questions.log"; fail "questions"; }
tail -3 "$W/questions.log" | head -1
done_step

step "13 rebuild with the lists and the questions"
"$PY" "$R/pipeline/bundle/build_bundle.py" > "$W/build-3.log" || { cat "$W/build-3.log"; fail "third build"; }
tail -1 "$W/build-3.log"
"$PY" "$R/pipeline/bundle/validate_bundle.py"
[ -f "$W/bundle/data/questions.json" ] || fail "questions.json is not in the bundle"
[ -f "$W/bundle/data/pairs.json" ] || fail "pairs.json is not in the bundle"
done_step

step "14 web build"
command -v bun >/dev/null 2>&1 || fail "bun is not installed (https://bun.sh)"
( cd "$R/web" && bun install --frozen-lockfile >/dev/null && bun run build >/dev/null )
rm -rf "$W/web-dist" && cp -R "$R/web/dist" "$W/web-dist"
echo "web: $(find "$W/web-dist" -type f | wc -l | tr -d ' ') files in $W/web-dist"
done_step

step "15 serve and check"
"$PY" "$R/pipeline/serve/serve.py" --check
done_step

step "16 privacy scan of the served bundle"
"$PY" - "$W" <<'PYEOF'
import json, os, sys
w = sys.argv[1]
planted = json.load(open(os.path.join(w, "planted.json")))
data = os.path.join(w, "bundle", "data")
found = []
for root, _, files in os.walk(data):
    for fn in files:
        path = os.path.join(root, fn)
        blob = (os.path.relpath(path, data) + "\n" + open(path, encoding="utf-8").read()).lower()
        for what, s in planted.items():
            for piece in {s.lower(), s.lower().lstrip("@"), *s.lower().split()}:
                if len(piece) >= 4 and piece in blob:
                    found.append((what, piece, os.path.relpath(path, data)))
for f in found[:20]:
    print("LEAK", *f)
print(f"privacy scan: {len(planted)} planted identifiers, {len(found)} found in the served bundle")
sys.exit(1 if found else 0)
PYEOF
if [ "$CLIENT" = mock ]; then
  # the mock's placeholder sentence has no person-like phrase, so the tagger leaves it and the builder serves it whole
  n=$( (grep -rho "Placeholder text, written without a model, from the numbered source lines" "$W/bundle/data/summaries" || true) | wc -l | tr -d ' ')
  cut=$( (grep -rhoE "Placeholder text, written[^.]*\[(human|id|email)\]" "$W/bundle/data" || true) | wc -l | tr -d ' ')
  echo "placeholder sentence: served whole in $n summaries, cut in $cut"
  [ "$n" -gt 0 ] && [ "$cut" -eq 0 ] || fail "the mock's placeholder sentence was cut or is missing"
fi
done_step

TOTAL=$(( $(date +%s) - T0 ))
printf '\n== timings\n'
for t in "${TIMES[@]}"; do echo "$t"; done
printf '%-34s %4ss\n' "total" "$TOTAL"
echo "SMOKE OK: work area $W; to look at the site: python3 $R/pipeline/serve/serve.py --work $W  (http://127.0.0.1:8790/)"
