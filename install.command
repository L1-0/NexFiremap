#!/usr/bin/env bash
# NexFiremap installer - macOS double-click entry point (Finder runs
# .command files in Terminal.app). Just delegates to install.sh.
cd "$(dirname "${BASH_SOURCE[0]}")"
exec ./install.sh
