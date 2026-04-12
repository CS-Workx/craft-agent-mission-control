---
name: "Mission Control"
description: "Interactive cross-workspace dashboard with kanban board, drag-and-drop status management, search, and filters"
alwaysAllow: ["Bash"]
---

# Mission Control

Interactive dashboard for managing all Craft Agent sessions across every workspace.

## Execution

The Mission Control server runs automatically on login (via Launch Agent). Just open it in the browser.

**Important:** Determine which workspace the user is currently in and pass it via the `?ws=` parameter so the dashboard opens to the right workspace.

```
browser_tool open --foreground
browser_tool navigate http://localhost:9753?ws={WORKSPACE_SLUG}
```

Replace `{WORKSPACE_SLUG}` with the current workspace's directory name (e.g., `assistant`, `webmaster`, `my-workspace`). The workspace slug is the folder name under `~/.craft-agent/workspaces/`. If unsure, check the session's `workspaceRootPath`.

If the server is not running, start it:

```bash
launchctl load ~/Library/LaunchAgents/com.craft-agent.mission-control.plist
```

### Static mode (view-only snapshot)

Generate a static HTML file. Replace `{DATA_FOLDER}` with the session's `dataFolderPath`:

```bash
python3 ~/.agents/skills/mission-control/dashboard.py "{DATA_FOLDER}/mission-control.html"
```

Then display inline:

````
```html-preview
{
  "src": "{DATA_FOLDER}/mission-control.html",
  "title": "Mission Control"
}
```
````

## Features

- **Auto-select workspace** — opens to the workspace you launched from via `?ws=` parameter
- **Workspace themes** — adapts colors to the selected workspace's theme (light/dark)
- **Workspace selector** — choose a workspace to manage, or "All Workspaces" for overview
- **Manage mode** — selecting a workspace shows all its status columns (including Done/Cancelled) and enables drag-and-drop
- **Drag-and-drop** — drag cards between status columns to change session status
- **Search** — filter by name, preview, workspace, or labels (Cmd+K to focus)
- **Sort** — by last activity, name, cost, messages, or staleness
- **Workspace filters** — toggle workspace visibility in overview mode
- **Expandable cards** — click to see full details (ID, tokens, cost, model, created date)
- **Closed sessions** — collapsible section in overview mode with sortable table
- **Responsive** — adapts to window width, fixed-width kanban columns
