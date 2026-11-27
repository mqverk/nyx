#!/usr/bin/env bash
# nyx bootstrapper wrapper — runs the stdlib-only Python bootstrapper.
set -euo pipefail
cd "$(dirname "$0")"
exec python3 bootstrap.py "$@"
