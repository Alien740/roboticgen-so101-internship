#!/usr/bin/env bash
# SO-101 dashboard installer (Linux/macOS). Usage: bash install.sh
# Installs into the framework's virtual environment (run ../08_so101_framework/install.sh first).
set -euo pipefail
cd "$(dirname "$0")"

PY=../08_so101_framework/.venv/bin/python
if [ ! -x "$PY" ]; then
  echo "Framework not installed. Run: bash ../08_so101_framework/install.sh" >&2; exit 1
fi
"$PY" -c "import so101" || { echo "The framework's .venv has no 'so101' package. Re-run its installer." >&2; exit 1; }
"$PY" -m pip install -e ".[ai]"   # [ai]: the anthropic SDK for the AI tab

echo
echo "Installed. Start it with:"
echo "  source ../08_so101_framework/.venv/bin/activate"
echo "  so101-dashboard --sim      (or without --sim to connect the real arm)"
