# Changelog

All notable changes to Mission Control will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

_Nothing yet — see [ROADMAP.md](ROADMAP.md) for what's coming._

## [2.0.0] — 2026-04-16

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

### Docs & Project

- **INSTALL.md** — dedicated installation guide with three clearly labeled paths: human manual, human one-liner, and agent-driven. Explains what `~/.agents/` is and why NOT to install into a Craft Agents workspace folder.
- **install.sh / uninstall.sh** — idempotent installer and symmetric uninstaller. Handle Launch Agent setup, plist `$HOME` substitution, health check verification, and `--yes` / `--dry-run` flags for non-interactive use by AI agents.
- **ROADMAP.md** — public roadmap with near-term, mid-term, and long-term items, plus an explicit "won't do" list.
- **CONTRIBUTING.md** — contribution guidelines with the zero-dependencies rule, commit conventions, and testing checklist.
- **SECURITY.md** — threat model (local-only, no auth, single-user machine) and private-advisory reporting channel.
- **README.md** — restructured with installation-first flow, status badges, a "What this is" box clarifying the `~/.agents/skills/` convention, and an Author & Credits section.
- **GitHub templates** — bug report, feature request, and pull request templates.
- **CI** — GitHub Actions workflow: Python syntax check across 3.9–3.12, plist XML validation, shellcheck on installers, and a guard against hardcoded personal paths.
- **`.gitignore`** — expanded with defensive patterns for env files, secrets, logs, and local-only artifacts.
- **Plist** — replaced hardcoded `/Users/<personal-username>/…` path with a `/Users/YOUR_USERNAME/…` placeholder that the installer substitutes at install time.
- **LICENSE** — updated copyright attribution to `Steff Vanhaverbeke (CS Workx)`.

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

[Unreleased]: https://github.com/CS-Workx/craft-agent-mission-control/compare/v2.0.0...HEAD
[2.0.0]: https://github.com/CS-Workx/craft-agent-mission-control/releases/tag/v2.0.0
[1.0.0]: https://github.com/CS-Workx/craft-agent-mission-control/releases/tag/v1.0.0
