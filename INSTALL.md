# Installing Mission Control

Mission Control installs as a **generic agent skill** in `~/.agents/skills/mission-control/`. This is the cross-tool skill folder that AI agents — Claude Code, Craft Agents, Cursor, and other skill-aware tools — all read from. One install, every agent sees it.

> ⚠️ **Do NOT install into `~/.craft-agent/workspaces/<workspace>/skills/`.**
> That folder is workspace-local. Mission Control is designed to span every workspace on your machine, so it must live at the global `~/.agents/` level. If you install it into a workspace folder, only that one workspace will see it, and the dashboard won't be able to aggregate sessions across workspaces.

## What `~/.agents/` is

`~/.agents/skills/` is a filesystem convention used by skill-aware AI agents to share reusable instruction sets and tools. Each subfolder is a skill; each skill has a `SKILL.md` describing what it does and how to invoke it. Putting Mission Control here means:

- **Every AI agent on your machine can use it.** Claude Code, Craft Agents, and other tools all read from `~/.agents/skills/` by convention.
- **No app-internal modification.** Mission Control runs as a standalone local HTTP server on port 9753. The host AI tool simply invokes it via the `SKILL.md` contract.
- **Easy to update.** `git pull` + `bash install.sh` updates every agent at once.

## Prerequisites

- macOS (for auto-start via Launch Agent) — Linux and Windows work too, but you'll need to start the server manually
- **Python 3.9 or newer** — check with `python3 --version`
- **git** — for the clone-based install
- Port **9753** free on localhost

## Installation

Three paths below. Pick the one that fits your situation.

### Option 1 — Manual install (recommended for humans)

Clone the repo and run the installer. You see every command; nothing is piped from the network into a shell.

```bash
git clone https://github.com/CS-Workx/craft-agent-mission-control.git
cd craft-agent-mission-control
bash install.sh
```

The installer is interactive — it will ask before overwriting an existing install. Pass `--yes` to skip prompts.

### Option 2 — One-liner (for humans who trust the source)

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
bash "$TMP/install.sh" --yes
curl -fsS http://localhost:9753/health
```

**Success criteria:** the final `curl` returns JSON matching `{"ok": true, "pid": <number>, "version": "2.0.0"}`.

**Failure recovery:** if the health check fails, run `tail -n 50 /tmp/mission-control.log` and check for port conflicts with `lsof -i :9753`.

## What the installer does

1. Creates `~/.agents/skills/mission-control/`
2. Copies the skill files (`dashboard.py`, `SKILL.md`, `icon.svg`, `README.md`, `LICENSE`, `CHANGELOG.md`, `install.sh`, `uninstall.sh`) and the `setup/` folder
3. Generates a patched plist from `setup/com.craft-agent.mission-control.plist` by substituting your real `$HOME` for the `/Users/YOUR_USERNAME/` placeholder
4. Copies the patched plist to `~/Library/LaunchAgents/`
5. Runs `launchctl unload` (if already loaded) then `launchctl load`
6. Polls `http://localhost:9753/health` for up to 5 seconds

The installer is **idempotent** — re-running it is safe and simply refreshes the Launch Agent.

## Verifying installation

```bash
# 1. Files exist in the skill folder
ls ~/.agents/skills/mission-control/

# 2. Server responds
curl http://localhost:9753/health
# → {"ok": true, "pid": <number>, "version": "2.0.0"}

# 3. Auto-start is registered (macOS)
launchctl list | grep mission-control

# 4. Open the dashboard
open http://localhost:9753
```

## Installer flags

| Flag | What it does |
|------|--------------|
| `--yes`, `-y` | Non-interactive — skip confirmation prompts |
| `--no-launchd` | Install files only; don't set up macOS auto-start |
| `-h`, `--help` | Print usage |

## Uninstalling

```bash
bash ~/.agents/skills/mission-control/uninstall.sh
```

Removes the Launch Agent, the skill folder, and the log file. Pass `--dry-run` to preview without removing, or `--yes` to skip confirmation.

## Linux / Windows

The dashboard itself is pure Python 3 stdlib and cross-platform. Auto-start via Launch Agent is macOS-only, so on other platforms:

```bash
# skip the Launch Agent
bash install.sh --no-launchd --yes

# start the server manually
python3 ~/.agents/skills/mission-control/dashboard.py --serve 9753
```

For Linux, a systemd unit is on the [roadmap](ROADMAP.md). For now, you can wire it into your own service manager or run it under `tmux`/`screen`.

## Updating to a new version

```bash
cd /path/to/your/clone/of/craft-agent-mission-control
git pull
bash install.sh --yes
```

Or just re-run the one-liner. The installer overwrites the skill folder with the new files.

## Troubleshooting

**`Port 9753 in use`**
```bash
lsof -i :9753           # find the process using the port
kill <pid>              # stop it if it's a stale Mission Control instance
```

**`launchctl load` fails or returns non-zero**
Check `/tmp/mission-control.log` for Python errors. Common causes: wrong Python path in the plist (the installer should handle this — if it's broken, open an issue), or Python 3 not at `/usr/bin/python3`.

**Dashboard loads but shows no workspaces**
The dashboard scans `~/.craft-agent/workspaces/`. If that folder doesn't exist yet, create at least one workspace in Craft Agents first, then reload.

**Server responds but labels/statuses look wrong**
Mission Control reads `config.json`, `statuses/`, `labels/`, and `sessions/` under each workspace. Make sure those exist and are readable.

**Deeplinks don't open sessions**
Deeplinks rely on the workspace UUID in `~/.craft-agent/config.json` (top-level). If this file is missing or malformed, clicks on cards fall back to clipboard mode.

## Security note

Mission Control binds to `127.0.0.1:9753` (localhost only — not accessible from the network). There is **no authentication** — it assumes a single-user machine. See [SECURITY.md](SECURITY.md) for the full threat model.

---

Built by [Steff Vanhaverbeke](https://github.com/CoachSteff) — part of the [Superworker Toolbox](https://github.com/CoachSteff).
