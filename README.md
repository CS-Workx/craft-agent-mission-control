# Mission Control

Cross-workspace session dashboard for [Craft Agents](https://craft.do) — a self-contained kanban board that gives you a bird's-eye view of every session across all your workspaces. Search, filter, sort, drag-and-drop status changes, workspace theming, and deeplink navigation, all from a single browser tab. Zero dependencies, pure Python 3 stdlib.

Part of the [Superworker Toolbox](https://github.com/CoachSteff).

## Features

- **Live kanban board** — sessions organized by status, with name, preview, staleness indicator, cost, model badge, labels, message count, and relative timestamps
- **Workspace selector** — switch between "All Workspaces" overview and individual workspace management
- **Drag-and-drop** — move session cards between status columns to change their state (persisted to `session.jsonl`)
- **Open sessions** — click any card to open the session directly in Craft Agents via deeplinks
- **Search** — filter across session name, preview, workspace, session ID, and labels (Cmd+K)
- **Sort** — by last activity, name, cost, messages, or staleness
- **Workspace filter pills** — toggle workspace visibility in overview mode
- **Expandable cards** — click to see full details (session ID, created date, tokens, cost, messages, model)
- **Closed sessions** — collapsible table in overview mode with sortable columns
- **Workspace theming** — loads theme colors from Craft Agents workspace config
- **Light/dark mode** — responds to system `prefers-color-scheme`
- **Stats header** — total visible, open, active 24h, stale 7d+, total cost, workspace count
- **Auto-start** — macOS Launch Agent starts the server on login and restarts on crash
- **Health endpoint** — `GET /health` for monitoring
- **Zero dependencies** — Python 3 stdlib only, nothing to install

## Installation

### 1. Copy skill files

Place the skill files where Craft Agents can find them:

```bash
mkdir -p ~/.agents/skills/mission-control
cp dashboard.py SKILL.md icon.svg ~/.agents/skills/mission-control/
```

### 2. Set up auto-start (macOS)

Copy the Launch Agent plist and load it. Edit the path inside the plist first if your home directory differs:

```bash
cp setup/com.craft-agent.mission-control.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.craft-agent.mission-control.plist
```

The server starts automatically on login at `http://localhost:9753`.

### 3. Verify

```bash
curl http://localhost:9753/health
# {"ok": true, "pid": 12345}
```

Open [http://localhost:9753](http://localhost:9753) in your browser.

## Usage

### Server mode (interactive)

```bash
python3 dashboard.py --serve 9753
```

This is the default mode when running via the Launch Agent. Enables drag-and-drop status changes and session deeplinks.

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
| GET | `/health` | Health check (`{"ok": true, "pid": N}`) |
| POST | `/api/status` | Update session status — `{"sessionId", "wsDir", "newStatus"}` |
| POST | `/api/open` | Open session in Craft Agents — `{"sessionId", "sdkSessionId", "wsUuid"}` |

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

The entire dashboard is a single self-contained HTML page. Data is embedded as a JSON blob. No external CDN, no build step, no node_modules.

## Management

```bash
# Restart the server
launchctl unload ~/Library/LaunchAgents/com.craft-agent.mission-control.plist
launchctl load ~/Library/LaunchAgents/com.craft-agent.mission-control.plist

# View logs
cat /tmp/mission-control.log

# Health check
curl http://localhost:9753/health
```

## Known Limitations

1. **Status changes via drag-and-drop** modify `session.jsonl` directly. This does not trigger Craft Agents automations (e.g., `SessionStatusChange` events) — automations only fire when status changes go through the app's internal API.

2. **macOS only** for auto-start and deeplinks. The dashboard itself is cross-platform Python, but the Launch Agent and `open` command for deeplinks are macOS-specific.

3. **Deeplink session navigation** requires the correct workspace UUID from `~/.craft-agent/config.json` (top-level). The workspace's own config uses a different ID format that won't work for deeplinks.

## License

[MIT](LICENSE)
