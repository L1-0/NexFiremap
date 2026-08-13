#!/usr/bin/env bash
# NexFiremap launcher - macOS double-click entry point (Finder runs
# .command files in Terminal.app). Just delegates to start.sh.
cd "$(dirname "${BASH_SOURCE[0]}")"
exec ./start.sh
