#!/usr/bin/env bash
# One setup line for the whole pipeline: a Python 3.12 venv with every stage's requirements.
#   scripts/setup.sh              core: slice, summarize, bundle, book, serve, questions (+ the mock model)
#   scripts/setup.sh --tagger     + the gliner_vad reference tagger (torch, GLiNER; CPU is enough)
#   scripts/setup.sh --pf         + the privacy-filter sweep (MLX: Apple Silicon only)
# The venv is $SWARM_VENV (default <repo>/.venv). Uses uv when it is installed, else python3 -m venv and pip.
set -euo pipefail
R=$(cd "$(dirname "$0")/.." && pwd)
VENV=${SWARM_VENV:-$R/.venv}
TAGGER=0; PF=0
for arg in "$@"; do
  case $arg in
    --tagger) TAGGER=1 ;;
    --pf) PF=1 ;;
    *) echo "usage: scripts/setup.sh [--tagger] [--pf]" >&2; exit 2 ;;
  esac
done
if command -v uv >/dev/null 2>&1; then
  [ -x "$VENV/bin/python" ] || uv venv -q -p 3.12 "$VENV"
  pip_install() { uv pip install -q -p "$VENV/bin/python" "$@"; }
else
  [ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
  pip_install() { "$VENV/bin/python" -m pip install -q "$@"; }
fi
pip_install -r "$R/requirements.txt"
if [ "$TAGGER" = 1 ]; then
  if [ "$(uname -s)" = Linux ] && ! command -v nvidia-smi >/dev/null 2>&1; then
    pip_install --extra-index-url https://download.pytorch.org/whl/cpu -r "$R/pipeline/tag/requirements-gliner.txt"
  else
    pip_install -r "$R/pipeline/tag/requirements-gliner.txt"
  fi
fi
if [ "$PF" = 1 ]; then
  if [ "$(uname -s)-$(uname -m)" != Darwin-arm64 ]; then
    echo "setup: --pf needs MLX, which runs on Apple Silicon only; skipped" >&2
  else
    pip_install -r "$R/pipeline/pf/requirements.txt"
  fi
fi
echo "setup: ready: $VENV/bin/python ($("$VENV/bin/python" --version))"
