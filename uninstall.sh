#!/usr/bin/env bash
# Mission Control uninstaller
# Unloads the Launch Agent and removes all installed files.
#
# Usage:
#   bash uninstall.sh            # interactive
#   bash uninstall.sh --yes      # non-interactive
#   bash uninstall.sh --dry-run  # show what would be removed

set -euo pipefail

SKILL_DIR="$HOME/.agents/skills/mission-control"
PLIST_DST="$HOME/Library/LaunchAgents/com.craft-agent.mission-control.plist"
LOG_FILE="/tmp/mission-control.log"

AUTO_YES=false
DRY_RUN=false
for arg in "$@"; do
    case "$arg" in
        --yes|-y) AUTO_YES=true ;;
        --dry-run) DRY_RUN=true ;;
        -h|--help)
            sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) echo "Unknown argument: $arg" >&2; exit 2 ;;
    esac
done

log()  { printf "\033[1;34m[uninstall]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[warn]\033[0m %s\n" "$*" >&2; }

confirm() {
    $AUTO_YES && return 0
    read -r -p "$1 [y/N] " reply
    [[ "$reply" =~ ^[Yy]$ ]]
}

log "The following will be removed:"
[[ -d "$SKILL_DIR" ]]  && echo "  - directory: $SKILL_DIR"
[[ -f "$PLIST_DST" ]]  && echo "  - plist:     $PLIST_DST"
[[ -f "$LOG_FILE" ]]   && echo "  - log:       $LOG_FILE"

$DRY_RUN && { log "Dry run — nothing removed."; exit 0; }

confirm "Proceed with uninstall?" || { log "Aborted."; exit 0; }

# unload Launch Agent
if [[ -f "$PLIST_DST" ]]; then
    if [[ "$(uname)" == "Darwin" ]]; then
        log "Unloading Launch Agent"
        launchctl unload "$PLIST_DST" 2>/dev/null || true
    fi
    rm -f "$PLIST_DST"
    log "Removed plist"
fi

# remove skill directory
if [[ -d "$SKILL_DIR" ]]; then
    rm -rf "$SKILL_DIR"
    log "Removed $SKILL_DIR"
fi

# remove log
if [[ -f "$LOG_FILE" ]]; then
    rm -f "$LOG_FILE"
    log "Removed $LOG_FILE"
fi

log "\033[1;32m✓ Mission Control uninstalled.\033[0m"
