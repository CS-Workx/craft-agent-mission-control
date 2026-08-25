---
name: "Mission Control"
description: "Interactive cross-workspace dashboard with kanban board, drag-and-drop, batch operations, label management, CSV export, and archive view"
alwaysAllow:
  - "browser_tool"
  - "Read"
  - "Bash(launchctl:*)"
  - "Bash(systemctl:*)"
  - "Bash(schtasks:*)"
  - "Bash(python3:*)"
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
- **Responsive** — adapts to window width, fixed-width kanban columns
- **CSV export** — download visible/filtered sessions as a CSV file
- **New session** — create sessions directly from the dashboard via Craft Agents deeplinks (`craftagents://workspace/{id}/action/new-chat`)
- **Label management** — add/remove labels on sessions via a label picker on each card
- **Batch operations** — multi-select cards and change status in bulk via a floating action bar
- **Archive view** — dedicated view for closed sessions with filters (status, workspace, date range), sorting, pagination, and reopen action
- **Stale alerts API** — `GET /api/alerts` returns sessions stale 7+ days for use with scheduled automations

## Agent tasking (`mc.py`)

`mc.py` is the agent-facing companion to the dashboard: a stdlib-only CLI a
Bash-driven agent uses to **see and task work across every workspace at once** —
a reach the in-session tools (`create_task`, `spawn_session`) do not have, since
those are scoped to the workspace the agent runs in.

```bash
python3 ~/.agents/skills/mission-control/mc.py <command> [args]
```

Four tiers, increasing in effect:

| Tier | Command | Mechanism | Effect |
|---|---|---|---|
| 1 read | `list`, `queue`, `show` | `GET /api/data` | cross-workspace triage; always safe |
| 2 trigger | `status`, `label` | `POST /api/status \| /api/labels` (edits `session.jsonl`) | fires that workspace's `SessionStatusChange` / `LabelAdd` automations |
| 3 dispatch | `dispatch` | injects a one-shot `SchedulerTick` **prompt** automation | the scheduler spawns a real, **briefed** agent session in the target workspace |
| aux | `open`, `new` | `craftagents://` deeplink | opens the app UI (the deeplink cannot carry a prompt) |

**Tier 3 is the only way to hand a briefed task to another workspace.** The
`craftagents://` deeplink reads `window`/`sidebar` params only — never a prompt —
so `new` just opens a blank chat. `dispatch` writes a `[mc-dispatch]` automation
(id `mc-…`) whose `prompt` action runs at `--at` (default `+2m`), the same
mechanism the `triggeredBy` sessions were born from.

**Safety gates:** `status`, `label`, `dispatch`, and `cleanup` are **dry-run
unless you pass `--go`.** `dispatch` spawns a live agent in another workspace — a
real side effect — so confirm intent with the user before `--go`. Every dispatch
is a one-shot; remove it early or after firing with `mc.py cleanup`.

```bash
# Tier 1 — triage
python3 mc.py list --status needs-review
python3 mc.py queue

# Tier 2 — trigger an existing automation (dry-run, then --go)
python3 mc.py status master 260804-keen-boulder needs-review --go
python3 mc.py label  webmaster 260101-foo-bar priority,client --go

# Tier 3 — brief-and-run an agent in another workspace
python3 mc.py dispatch assistant \
  --prompt "Draft this week's newsletter outline and save to 09-outputs/drafts/." \
  --at +5m --labels scheduled --mode allow-all            # dry-run preview
python3 mc.py dispatch assistant --prompt "…" --at +5m --go   # arm it

python3 mc.py dispatched              # list armed one-shots across all workspaces
python3 mc.py cleanup --ws assistant --id mc-1a2b3c4d --go    # remove one early
```

Env: `MC_PORT` (default `9753`), `CRAFT_HOME` (default `~/.craft-agent`).

**Which workspace to task?** Read [`ROUTING.md`](./ROUTING.md) — the workspace map (what each
workspace is for and which to send a given job to) plus guardrails. Never task `ali`; test the
mechanism against a `demo` workspace.
