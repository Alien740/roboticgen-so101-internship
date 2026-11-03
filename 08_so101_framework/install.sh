#!/usr/bin/env bash
# SO-101 framework installer (Linux / macOS).   Usage: bash install.sh
set -euo pipefail
cd "$(dirname "$0")"

PY=""
for v in python3.12 python3.11 python3.10 python3; do
  if command -v "$v" >/dev/null && "$v" -c 'import sys; exit(0 if (3,10)<=sys.version_info[:2]<=(3,12) else 1)'; then PY="$v"; break; fi
done
[ -n "$PY" ] || { echo "Python 3.10-3.12 not found (3.12 tested)."; exit 1; }
echo "Using $PY"

[ -d .venv ] || "$PY" -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -e .

if [ "$(uname)" = "Linux" ] && ! id -nG "$USER" | grep -qw dialout; then
  echo "NOTE: add yourself to the 'dialout' group for serial access:  sudo usermod -aG dialout $USER  (then log out/in)"
fi
echo; echo "Running: so101 doctor"; echo
so101 doctor || true
echo; echo "Activate later with:  source .venv/bin/activate"
