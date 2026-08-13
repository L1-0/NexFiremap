#!/usr/bin/env bash
# NexFiremap launcher - Linux/macOS.
# Prefers the project virtual environment (created by install.sh) and
# falls back to the system Python if there isn't one.
set -u
cd "$(dirname "${BASH_SOURCE[0]}")"

if [ -x ".venv/bin/python" ]; then
    PYEXE=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYEXE="python3"
elif command -v python >/dev/null 2>&1; then
    PYEXE="python"
else
    echo
    echo "Python was not found. Run ./install.sh first, or install Python 3.10+."
    echo
    exit 1
fi

exec "$PYEXE" run.py --open "$@"
