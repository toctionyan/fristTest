#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="$("$ROOT/scripts/resolve_python.py")"
export PYTHONPATH="$ROOT:$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export APP_PROFILE="${APP_PROFILE:-local}"
if [[ "$APP_PROFILE" == "local" ]]; then export LOCAL_DEV="${LOCAL_DEV:-true}"; fi
"$PYTHON_BIN" -m pytest -q "$ROOT/tests" "$@"
