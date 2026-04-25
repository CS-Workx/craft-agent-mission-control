# Mission Control

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![Platform: macOS / Linux / Windows](https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey.svg)]()
[![CI](https://github.com/CS-Workx/craft-agent-mission-control/actions/workflows/ci.yml/badge.svg)](https://github.com/CS-Workx/craft-agent-mission-control/actions)

Cross-workspace session dashboard for [Craft Agents](https://craft.do) — a self-contained kanban board that gives you a bird's-eye view of every session across all your workspaces. Search, filter, sort, drag-and-drop status changes, label management, batch operations, archive view, workspace theming, and deeplink navigation, all from a single browser tab. Zero runtime dependencies — Python 3 stdlib only.

Part of the [Superworker Toolbox](https://github.com/CoachSteff).

> ## 📦 What this is
> Mission Control is a **generic agent skill**, not a Craft Agents plugin. It installs to `~/.agents/skills/mission-control/` — the cross-tool skill folder that Claude Code, Craft Agents, and other skill-aware AI agents all read from. **Do not install it into a Craft Agents workspace folder** (`~/.craft-agent/workspaces/<ws>/skills/`) — that would tie the dashboard to a single workspace instead of spanning all of them.

## Quick Install

Mission Control ships with one installer brain (`install.py`) and thin shell + PowerShell wrappers. Pick the flavor for your platform.

### macOS / Linux

```bash
git clone https://github.com/CS-Workx/craft-agent-mission-control.git
cd craft-agent-mission-control
bash install.sh
```

### Windows

```powershell
git clone https://github.com/CS-Workx/craft-agent-mission-control.git
cd craft-agent-mission-control
pwsh install.ps1
```

### Any platform (direct)

```bash
python3 install.py
```

### One-liner (for the trusting, macOS/Linux only)

```bash
curl -fsSL https://raw.githubusercontent.com/CS-Workx/craft-agent-mission-control/main/install.sh | bash
```

### For AI agents (deterministic, self-verifying)

```bash
set -e
TMP=$(mktemp -d)
git clone --depth 1 https://github.com/CS-Workx/craft-agent-mission-control.git "$TMP"
python3 "$TMP/install.py" --yes
curl -fsS http://localhost:9753/health
```

Success looks like `{"ok": true, "version": "2.1.0"}`.

👉 **Full installation guide, prerequisites, and troubleshooting: [INSTALL.md](INSTALL.md)**

## Features

- **Live kanban board** — sessions organized by status, with name, preview, staleness indicator, cost, model badge, labels, message count, and relative timestamps
- **Workspace selector** — switch between "All Workspaces" overview and individual workspace management
- **Drag-and-drop** — move session cards between status columns to change their state (persisted to `session.jsonl`)
- **Open sessions** — click any card to open the session directly in Craft Agents via deeplinks
- **Search** — filter across session name, preview, workspace, session ID, and labels (Cmd+K)
- **Sort** — by last activity, name, cost, messages, or staleness
- **Workspace filter pills** — toggle workspace visibility in overview mode
- **Expandable cards** — click to see full details (session ID, created date, tokens, cost, messages, model)
- **CSV export** — download visible/filtered sessions as a CSV file
- **New session button** — create sessions directly from the dashboard via Craft Agents deeplinks
- **Label management** — tag icon on each card opens a label picker dropdown
- **Batch operations** — multi-select cards and change status in bulk via a floating action bar
- **Archive view** — dedicated view for closed sessions with filters, sorting, pagination, and reopen action
- **Queue view** — six triage lanes (Needs decision, Blocked, Cost spike, Stale but important, Idle automations, Fresh) derived from session metadata. A session can appear in multiple lanes; each card shows the lane rule it matched.
- **Workspace Health view** — per-workspace cards with open/stale/cost stats, 14-day trend sparklines, review backlog, idle automations, and a health badge (healthy / attention / overloaded). Click a card to drill into the Board scoped to that workspace.
- **Saved Lenses** — one-click answers to "show me the specific mess I care about." Six built-in stock lenses (Stale > 7d, No labels, Cost spike, Needs Review, Active 24h, Abandoned) plus user-saved lenses that follow you across browsers via a small JSON file. Lenses are bookmarkable via `?lens=ID`.
- **Stale alerts API** — `GET /api/alerts` returns sessions stale 7+ days for use with scheduled automations
- **Workspace theming** — loads theme colors from Craft Agents workspace config
- **Light/dark mode** — responds to system `prefers-color-scheme`
- **Stats header** — total visible, open, active 24h, stale 7d+, total cost, workspace count
- **Auto-start** — macOS Launch Agent / Linux systemd user unit / Windows Scheduled Task — the server starts on login and restarts on crash on all three platforms
- **Zero dependencies** — Python 3 stdlib only, nothing to install

## Usage

Once installed, open the dashboard at [http://localhost:9753](http://localhost:9753).

### Server mode (interactive)

```bash
python3 dashboard.py --serve 9753
```

Enables drag-and-drop status changes, label editing, batch operations, and session deeplinks.

### Static mode (view-only snapshot)

```bash
python3 dashboard.py /tmp/dashboard.html
open /tmp/dashboard.html
```

Generates a self-contained HTML file. Useful for sharing or archiving.

### Skill invocation

From any Craft Agents session, invoke the `[skill:mission-control]` skill. It opens the dashboard in the in-app browser, pre-filtered to your current workspace.

### URL parameters

- `?ws=my-workspace` — auto-select a workspace on load (use the workspace folder name)

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/` | Serves the dashboard (re-collects data on every load) |
| GET | `/api/data` | Returns collected data as JSON |
| GET | `/health` | Health check (`{"ok": true, "version": "2.1.0"}`) |
| GET | `/api/alerts` | Stale session alerts (sessions inactive 7+ days) |
| GET | `/api/workspace-labels` | Label config for a workspace |
| POST | `/api/status` | Update session status |
| POST | `/api/batch/status` | Bulk status change |
| POST | `/api/labels` | Update session labels |
| POST | `/api/open` | Open session in Craft Agents |
| POST | `/api/open-url` | Open any `craftagents://` deeplink |

## Architecture

```
dashboard.py --serve 9753
  │
  ├── collect()         Scans ~/.craft-agent/workspaces/
  │   ├── config.json   Workspace name, slug, UUID, theme
  │   ├── statuses/     Status definitions per workspace
  │   ├── labels/       Label definitions (flattened hierarchy)
  │   ├── themes/       Theme JSON files
  │   ├── sessions/     First line of each session.jsonl
  │   └── top-level config.json for workspace UUIDs (deeplinks)
  │
  ├── build_data()      Transforms into JSON for the frontend
  ├── generate_html()   Self-contained HTML with embedded JSON + CSS + JS
  └── HTTP Server       Serves HTML, JSON API, status updates, session open
```

The entire dashboard is a single self-contained HTML page. Data is embedded as a JSON blob. No external CDN, no build step, no `node_modules`.

## Management

**macOS:**

```bash
launchctl unload ~/Library/LaunchAgents/com.craft-agent.mission-control.plist
launchctl load ~/Library/LaunchAgents/com.craft-agent.mission-control.plist
tail -n 50 /tmp/mission-control.log
```

**Linux:**

```bash
systemctl --user restart mission-control.service
systemctl --user status mission-control.service
tail -n 50 ~/.local/state/mission-control/mission-control.log
```

**Windows (PowerShell):**

```powershell
schtasks.exe /End /TN CraftAgentMissionControl
schtasks.exe /Run /TN CraftAgentMissionControl
schtasks.exe /Query /TN CraftAgentMissionControl
```

**Health check (any OS):**

```bash
curl http://localhost:9753/health
```

## Known Limitations

1. **Status changes via drag-and-drop** modify `session.jsonl` directly. This does not trigger Craft Agents automations (e.g., `SessionStatusChange` events) — automations only fire when status changes go through the app's internal API. See [ROADMAP](ROADMAP.md) for the planned fix.

2. **Local workspaces only.** Mission Control reads `~/.craft-agent/workspaces/` from the local filesystem. Headless SDK deployments, remote workspaces, and team-shared instances are out of scope for v2.x — shared-instance mode is a v3.0 roadmap item.

3. **Deeplink session navigation** requires the correct workspace UUID from `~/.craft-agent/config.json` (top-level). The workspace's own config uses a different ID format that won't work for deeplinks.

4. **Linux deeplinks** require `xdg-utils` (preinstalled on most desktop distributions; `apt install xdg-utils` on minimal Debian/Ubuntu). Without it, clicking "Open" on a card will fail cleanly with a toast.

## Documentation

- **[INSTALL.md](INSTALL.md)** — full installation guide (humans + AI agents)
- **[SECURITY.md](SECURITY.md)** — threat model and how to report vulnerabilities
- **[ROADMAP.md](ROADMAP.md)** — what's coming next
- **[CONTRIBUTING.md](CONTRIBUTING.md)** — how to contribute
- **[CHANGELOG.md](CHANGELOG.md)** — release history

## License

[MIT](LICENSE) — free for personal and commercial use.

## Author & Credits

Built by **[Steff Vanhaverbeke](https://github.com/CoachSteff)** — AI adoption expert, trainer, coach, and founder of [CS Workx](https://github.com/CS-Workx). Mission Control is part of the [Superworker Toolbox](https://github.com/CoachSteff), a collection of open-source tools designed to help people and teams thrive in an AI-driven world.

If Mission Control saves you time, a GitHub ⭐️ is a nice way to say thanks.
