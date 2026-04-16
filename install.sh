#!/usr/bin/env bash
# Mission Control installer
# Installs the skill to the generic ~/.agents/skills/mission-control/ folder
# and sets up the macOS Launch Agent for auto-start on login.
#
# Usage:
#   bash install.sh              # interactive (prompts before overwrite)
#   bash install.sh --yes        # non-interactive (for CI / agents)
#   bash install.sh --no-launchd # skip Launch Agent (manual start only)

set -euo pipefail

# --- configuration ---------------------------------------------------------
SKILL_DIR="$HOME/.agents/skills/mission-control"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
PLIST_NAME="com.craft-agent.mission-control.plist"
PLIST_DST="$LAUNCH_AGENTS_DIR/$PLIST_NAME"
HEALTH_URL="http://localhost:9753/health"
REPO_URL="https://github.com/CS-Workx/craft-agent-mission-control"

# files copied into $SKILL_DIR
SKILL_FILES=(
    "dashboard.py"
    "SKILL.md"
    "icon.svg"
    "README.md"
    "LICENSE"
    "CHANGELOG.md"
    "install.sh"
    "uninstall.sh"
)

# --- arg parsing -----------------------------------------------------------
AUTO_YES=false
INSTALL_LAUNCHD=true
for arg in "$@"; do
    case "$arg" in
        --yes|-y) AUTO_YES=true ;;
        --no-launchd) INSTALL_LAUNCHD=false ;;
        -h|--help)
            sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) echo "Unknown argument: $arg" >&2; exit 2 ;;
    esac
done

# --- helpers ---------------------------------------------------------------
log()  { printf "\033[1;34m[install]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[warn]\033[0m %s\n" "$*" >&2; }
fail() { printf "\033[1;31m[error]\033[0m %s\n" "$*" >&2; exit 1; }

confirm() {
    $AUTO_YES && return 0
    local prompt="$1"
    read -r -p "$prompt [y/N] " reply
    [[ "$reply" =~ ^[Yy]$ ]]
}

# --- preflight -------------------------------------------------------------
log "Mission Control installer"
log "Target: $SKILL_DIR"

command -v python3 >/dev/null 2>&1 || fail "python3 is required but not installed."
PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
log "Found Python $PY_VERSION"

# --- locate source files ---------------------------------------------------
# Resolve the directory containing this script (so the installer works whether
# run from a clone, an extracted tarball, or a temp dir via curl|bash).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLIST_SRC="$SCRIPT_DIR/setup/$PLIST_NAME"

for f in "${SKILL_FILES[@]}"; do
    [[ -f "$SCRIPT_DIR/$f" ]] || fail "Missing source file: $SCRIPT_DIR/$f"
done
[[ -f "$PLIST_SRC" ]] || fail "Missing plist: $PLIST_SRC"

# --- existing install check ------------------------------------------------
if [[ -d "$SKILL_DIR" ]]; then
    warn "Existing installation found at $SKILL_DIR"
    confirm "Overwrite?" || fail "Aborted by user."
fi

# --- install skill files ---------------------------------------------------
log "Creating $SKILL_DIR"
mkdir -p "$SKILL_DIR"

if [[ "$SCRIPT_DIR" == "$SKILL_DIR" ]]; then
    log "Source and target are the same directory — skipping file copy"
    log "(running install.sh in-place to refresh Launch Agent)"
else
    for f in "${SKILL_FILES[@]}"; do
        cp "$SCRIPT_DIR/$f" "$SKILL_DIR/$f"
        log "  copied $f"
    done

    # also ship the setup/ folder so uninstall / reinstall can find the plist
    mkdir -p "$SKILL_DIR/setup"
    cp "$PLIST_SRC" "$SKILL_DIR/setup/$PLIST_NAME"
fi

chmod +x "$SKILL_DIR/install.sh" 2>/dev/null || true
chmod +x "$SKILL_DIR/uninstall.sh" 2>/dev/null || true

# --- install Launch Agent --------------------------------------------------
if $INSTALL_LAUNCHD; then
    if [[ "$(uname)" != "Darwin" ]]; then
        warn "Not on macOS — skipping Launch Agent setup."
        warn "Start manually with: python3 $SKILL_DIR/dashboard.py --serve 9753"
    else
        log "Installing Launch Agent to $PLIST_DST"
        mkdir -p "$LAUNCH_AGENTS_DIR"

        # substitute the real absolute path into the plist
        sed "s|/Users/YOUR_USERNAME/|$HOME/|g" "$PLIST_SRC" > "$PLIST_DST"

        # reload cleanly: unload if present, then load
        if launchctl list 2>/dev/null | grep -q "com.craft-agent.mission-control"; then
            log "Reloading existing Launch Agent"
            launchctl unload "$PLIST_DST" 2>/dev/null || true
            # small grace period for launchd to release the label
            sleep 1
        fi
        launchctl load "$PLIST_DST" 2>/dev/null || \
            warn "launchctl load returned non-zero; checking health anyway..."

        # --- health check --------------------------------------------------
        log "Waiting for server to start..."
        for i in 1 2 3 4 5; do
            sleep 1
            if curl -fsS "$HEALTH_URL" >/dev/null 2>&1; then
                RESPONSE=$(curl -fsS "$HEALTH_URL")
                log "Server is up: $RESPONSE"
                break
            fi
            if [[ $i -eq 5 ]]; then
                warn "Health check did not respond after 5s."
                warn "Check logs: tail -n 50 /tmp/mission-control.log"
            fi
        done
    fi
fi

# --- done ------------------------------------------------------------------
printf "\n\033[1;32m✓ Mission Control installed.\033[0m\n\n"
printf "  Location:    %s\n" "$SKILL_DIR"
printf "  Dashboard:   http://localhost:9753\n"
printf "  Uninstall:   bash %s/uninstall.sh\n" "$SKILL_DIR"
printf "  Docs:        %s\n\n" "$REPO_URL"
printf "From any Craft Agents session, invoke with: [skill:mission-control]\n"
