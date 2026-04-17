#!/usr/bin/env bash
# Mission Control installer — thin wrapper around install.py.
#
# The real logic lives in install.py (cross-platform Python, stdlib-only).
# This wrapper exists so the README one-liner `bash install.sh` keeps working.
#
# Usage:
#   bash install.sh                 # interactive
#   bash install.sh --yes           # non-interactive
#   bash install.sh --no-autostart  # skip Launch Agent / systemd / scheduled task
#   bash install.sh --help          # pass through to install.py

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 is required but not installed." >&2
    exit 1
fi

exec python3 "$SCRIPT_DIR/install.py" "$@"
