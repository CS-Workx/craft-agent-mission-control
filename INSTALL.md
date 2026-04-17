# Installing Mission Control

Mission Control installs as a **generic agent skill** in `~/.agents/skills/mission-control/`. This is the cross-tool skill folder that AI agents — Claude Code, Craft Agents, Cursor, and other skill-aware tools — all read from. One install, every agent sees it.

> ⚠️ **Do NOT install into `~/.craft-agent/workspaces/<workspace>/skills/`.**
> That folder is workspace-local. Mission Control is designed to span every workspace on your machine, so it must live at the global `~/.agents/` level. If you install it into a workspace folder, only that one workspace will see it, and the dashboard won't be able to aggregate sessions across workspaces.

## What `~/.agents/` is

`~/.agents/skills/` is a filesystem convention used by skill-aware AI agents to share reusable instruction sets and tools. Each subfolder is a skill; each skill has a `SKILL.md` describing what it does and how to invoke it. Putting Mission Control here means:

- **Every AI agent on your machine can use it.** Claude Code, Craft Agents, and other tools all read from `~/.agents/skills/` by convention.
- **No app-internal modification.** Mission Control runs as a standalone local HTTP server on port 9753. The host AI tool simply invokes it via the `SKILL.md` contract.
- **Easy to update.** `git pull` + re-run the installer updates every agent at once.

## Prerequisites

- **macOS, Linux, or Windows**
- **Python 3.9 or newer** — check with `python3 --version` (Windows: `python --version`)
- **git** — for the clone-based install
- Port **9753** free on localhost

**Per-OS notes:**

- **macOS:** no extras. Auto-start uses a user Launch Agent.
- **Linux:** auto-start uses a systemd user unit. Needs `systemctl` (part of `systemd`, present on all major desktop distributions). For deeplinks (the "Open in Craft Agents" button on cards), `xdg-utils` is required — preinstalled on most desktops, `sudo apt install xdg-utils` on minimal Debian/Ubuntu.
- **Windows:** auto-start uses a user-level Scheduled Task via `schtasks.exe` (built into Windows). `pythonw.exe` is preferred so the server runs without a console window.

## Installation

Three paths below. Pick the one that fits your situation.

### Option 1 — Manual install (recommended for humans)

Clone the repo and run the installer. You see every command; nothing is piped from the network into a shell.

**macOS / Linux:**

```bash
git clone https://github.com/CS-Workx/craft-agent-mission-control.git
cd craft-agent-mission-control
bash install.sh
```

**Windows (PowerShell):**

```powershell
git clone https://github.com/CS-Workx/craft-agent-mission-control.git
cd craft-agent-mission-control
pwsh install.ps1
```

**Any platform (direct):**

```bash
python3 install.py
```

The installer is interactive — it will ask before overwriting an existing install. Pass `--yes` to skip prompts.

### Option 2 — One-liner (for humans who trust the source, macOS/Linux)

```bash
curl -fsSL https://raw.githubusercontent.com/CS-Workx/craft-agent-mission-control/main/install.sh | bash
```

This is convenient but pipes a remote script directly into your shell. Only use it if you're comfortable with the trust model. Option 1 lets you read the script first.

### Option 3 — Agent-driven install (for AI agents)

Paste this exact block. It's deterministic, non-interactive, and self-verifying. Any skill-aware AI agent (Claude Code, Cursor, etc.) can run it without human intervention.

```bash
set -e
TMP=$(mktemp -d)
git clone --depth 1 https://github.com/CS-Workx/craft-agent-mission-control.git "$TMP"
python3 "$TMP/install.py" --yes
curl -fsS http://localhost:9753/health
```

**Success criteria:** the final `curl` returns JSON matching `{"ok": true, "version": "2.1.0"}`.

**Failure recovery:** if the health check fails, check the log (`/tmp/mission-control.log` on macOS, `~/.local/state/mission-control/mission-control.log` on Linux, Event Viewer on Windows) and check for port conflicts with `lsof -i :9753` (macOS/Linux) or `netstat -ano | findstr :9753` (Windows).

## What the installer does

1. Creates `~/.agents/skills/mission-control/`
2. Copies the skill files (`dashboard.py`, `SKILL.md`, `icon.svg`, `README.md`, `LICENSE`, docs, installer scripts) and the `setup/` folder
3. Sets up auto-start for your OS:
   - **macOS** → writes `~/Library/LaunchAgents/com.craft-agent.mission-control.plist` with your real `$HOME` substituted, then runs `launchctl load`
   - **Linux** → writes `~/.config/systemd/user/mission-control.service`, runs `systemctl --user daemon-reload`, then `systemctl --user enable --now mission-control.service`
   - **Windows** → creates a scheduled task `CraftAgentMissionControl` via `schtasks.exe` triggered on logon, then runs it immediately
4. Polls `http://localhost:9753/health` for up to 10 seconds

The installer is **idempotent** — re-running it is safe and simply refreshes the autostart entry.

## Verifying installation

```bash
# 1. Files exist in the skill folder
ls ~/.agents/skills/mission-control/

# 2. Server responds
curl http://localhost:9753/health
# → {"ok": true, "version": "2.1.0"}

# 3. Auto-start is registered
#    macOS:
launchctl list | grep mission-control
#    Linux:
systemctl --user status mission-control.service
#    Windows (PowerShell):
schtasks.exe /Query /TN CraftAgentMissionControl

# 4. Open the dashboard
open http://localhost:9753           # macOS
xdg-open http://localhost:9753       # Linux
start http://localhost:9753          # Windows
```

## Installer flags

| Flag | What it does |
|------|--------------|
| `--yes`, `-y` | Non-interactive — skip confirmation prompts |
| `--no-autostart` | Install files only; don't set up OS auto-start |
| `--port N` | Override the default port 9753 |
| `--skill-dir PATH` | Override install target (default `~/.agents/skills/mission-control`) |
| `-h`, `--help` | Print usage |

## Uninstalling

**macOS / Linux:**

```bash
bash ~/.agents/skills/mission-control/uninstall.sh
```

**Windows:**

```powershell
pwsh ~\.agents\skills\mission-control\uninstall.ps1
```

**Any platform (direct):**

```bash
python3 ~/.agents/skills/mission-control/uninstall.py
```

Removes the autostart entry (Launch Agent / systemd unit / scheduled task), the skill folder, and the log file. Pass `--dry-run` to preview without removing, or `--yes` to skip confirmation.

## Platform-specific notes

### Linux — persistent service across logouts

systemd user units only run while the user session is active. If you want Mission Control to stay running after you log out, enable lingering once:

```bash
loginctl enable-linger $USER
```

This is a user choice, not enabled by default. To disable: `loginctl disable-linger $USER`.

### Windows — Craft Agents desktop app

Mission Control operates on `~/.craft-agent/workspaces/` which the Craft Agents **desktop app** populates. If you use the headless SDK server on Windows, note that the SDK headless server is not supported on Windows — Mission Control is a companion to the desktop app in that case.

## What Mission Control assumes

Mission Control is a **local-machine tool**. It assumes:

- `~/.craft-agent/workspaces/` is the authoritative source of session state
- Session files are owned and writable by the same user running the server
- All workspaces the user wants to see live on one machine

If you use Craft Agents in a headless SDK or remote configuration (workspaces on a server, team-shared instances), Mission Control won't see those sessions. Shared-instance mode is a v3.0 roadmap item.

## Updating to a new version

```bash
cd /path/to/your/clone/of/craft-agent-mission-control
git pull
python3 install.py --yes
```

Or just re-run the one-liner. The installer overwrites the skill folder with the new files.

## Troubleshooting

**`Port 9753 in use`**

```bash
# macOS / Linux:
lsof -i :9753           # find the process using the port
kill <pid>              # stop it if it's a stale Mission Control instance

# Windows:
netstat -ano | findstr :9753
taskkill /F /PID <pid>
```

**Autostart command fails or returns non-zero**

- **macOS** — check `/tmp/mission-control.log` for Python errors.
- **Linux** — run `journalctl --user -u mission-control.service` and `systemctl --user status mission-control.service`.
- **Windows** — `schtasks.exe /Query /TN CraftAgentMissionControl /V /FO LIST` shows last run status and any errors.

**Dashboard loads but shows no workspaces**

The dashboard scans `~/.craft-agent/workspaces/`. If that folder doesn't exist yet, create at least one workspace in Craft Agents first, then reload.

**Server responds but labels/statuses look wrong**

Mission Control reads `config.json`, `statuses/`, `labels/`, and `sessions/` under each workspace. Make sure those exist and are readable.

**Deeplinks don't open sessions**

Deeplinks rely on the workspace UUID in `~/.craft-agent/config.json` (top-level). If this file is missing or malformed, clicks on cards fall back to clipboard mode. On Linux, also verify `xdg-utils` is installed.

## Security note

Mission Control binds to `127.0.0.1:9753` (localhost only — not accessible from the network). There is **no authentication** — it assumes a single-user machine. See [SECURITY.md](SECURITY.md) for the full threat model.

---

Built by [Steff Vanhaverbeke](https://github.com/CoachSteff) — part of the [Superworker Toolbox](https://github.com/CoachSteff).
