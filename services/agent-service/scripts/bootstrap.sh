#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UV_BIN="${UV_BIN:-$(command -v uv || true)}"
if [[ -z "$UV_BIN" && -x /opt/pyvenv/bin/uv ]]; then UV_BIN=/opt/pyvenv/bin/uv; fi
if [[ -z "$UV_BIN" ]]; then
  echo "uv is required for bootstrap. Install it or set UV_BIN." >&2
  exit 1
fi
# Install the exact release only when resolver cannot already find it.
if ! "$ROOT/scripts/resolve_python.py" --baseline >/dev/null 2>&1; then
  "$UV_BIN" python install 3.12.13
fi
PYTHON_BIN="$("$ROOT/scripts/resolve_python.py" --baseline)"
"$UV_BIN" venv --python "$PYTHON_BIN" "$ROOT/.venv"
"$UV_BIN" sync --python "$ROOT/.venv/bin/python" --all-groups --locked
printf 'Bootstrap complete: %s\n' "$ROOT/.venv/bin/python"
