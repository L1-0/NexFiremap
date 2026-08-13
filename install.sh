#!/usr/bin/env bash
# NexFiremap installer - Linux/macOS.
# Finds a Python interpreter and hands off to the real (cross-platform)
# setup logic in scripts/setup_wizard.py.
set -u
cd "$(dirname "${BASH_SOURCE[0]}")"

if command -v python3 >/dev/null 2>&1; then
    PYEXE="python3"
elif command -v python >/dev/null 2>&1; then
    PYEXE="python"
else
    echo
    echo "Python was not found on PATH."
    echo "Install Python 3.10+ (your package manager, or https://www.python.org/downloads/) and run this again."
    echo
    exit 1
fi

exec "$PYEXE" "$(dirname "$0")/scripts/setup_wizard.py"
