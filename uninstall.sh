#!/usr/bin/env bash
# Mission Control uninstaller — thin wrapper around uninstall.py.
#
# Usage:
#   bash uninstall.sh             # interactive
#   bash uninstall.sh --yes       # non-interactive
#   bash uninstall.sh --dry-run   # show what would be removed

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 is required but not installed." >&2
    exit 1
fi

exec python3 "$SCRIPT_DIR/uninstall.py" "$@"
