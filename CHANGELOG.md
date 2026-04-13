# Changelog

All notable changes to Mission Control will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [2.0.0] — 2026-04-13

### Added

- **CSV export** — toolbar button downloads visible/filtered sessions as `mission-control-YYYY-MM-DD.csv` with all session fields
- **New session button** — "+" button in manage mode toolbar and per-workspace in overview mode, opens `craftagents://workspace/{id}/action/new-chat?window=focused` deeplink
- **Label management** — tag icon on each card opens a label picker dropdown; toggle labels on/off with immediate persistence to session.jsonl
- **Batch operations** — select mode (toolbar toggle or Cmd+click), multi-select cards with checkboxes, floating action bar for bulk status changes, shift-click range select, Escape to exit
- **Archive view** — Board/Archive tab toggle; archive shows all closed sessions in a full-width table with filters (status, workspace, date range), search, sortable columns, pagination (25/page), and a "Reopen" button to move sessions back to todo
- **Stale alerts API** — `GET /api/alerts` returns open sessions inactive 7+ days, ready for use with Craft Agents `SchedulerTick` automations
- **API endpoints** — `POST /api/labels` (update session labels), `POST /api/batch/status` (bulk status change), `POST /api/open-url` (open any `craftagents://` deeplink), `GET /api/workspace-labels` (label config for a workspace), `GET /api/alerts` (stale session alerts)
- Version number in `/health` response

### Changed

- Cards now show a label management button (server mode only) in the footer alongside the Open button
- Workspace filter pills in overview mode now include a "+" button for creating new sessions per workspace
- View toggle tabs (Board/Archive) replace the old closed sessions collapsible section as the primary way to access archived sessions
- Board view hides closed sessions section when Archive view is available

## [1.0.0] — 2026-04-12

Initial public release.

### Added

- Live kanban board with columns by session status
- Session cards with name, preview, staleness indicator, cost, model badge, labels, message count, and relative timestamps
- Workspace selector — switch between "All Workspaces" overview and individual workspace management
- Manage mode with all status columns (including Done/Cancelled) and drag-and-drop
- Drag-and-drop status changes persisted to session.jsonl
- Open session in Craft Agents via deeplinks (workspace-targeted, with clipboard fallback)
- Search across session name, preview, workspace, session ID, and labels (Cmd+K shortcut)
- Sort by last activity, name, cost, messages, or staleness
- Workspace filter pills for toggling visibility in overview mode
- Expandable cards with full session details (ID, created date, tokens, cost, messages, model)
- Closed sessions collapsible table in overview mode with sortable columns
- Workspace theming loaded from Craft Agents theme files, applied per workspace
- Light/dark mode responding to system `prefers-color-scheme`
- Stats header showing total visible, open, active 24h, stale 7d+, total cost, workspace count
- REST API: `/api/data`, `/api/status`, `/api/open`, `/health`
- Static HTML export mode (`dashboard.py OUTPUT_FILE`)
- macOS Launch Agent for auto-start on login with crash recovery
- Toast notifications for success/error feedback
- Craft Agents skill definition (SKILL.md) for invocation from any workspace

[2.0.0]: https://github.com/CS-Workx/craft-agent-mission-control/releases/tag/v2.0.0
[1.0.0]: https://github.com/CS-Workx/craft-agent-mission-control/releases/tag/v1.0.0
