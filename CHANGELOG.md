# Changelog

All notable changes to Mission Control will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

_Nothing yet — see [ROADMAP.md](ROADMAP.md) for what's coming._

## [3.0.0] — 2026-04-25

The 3.0 release marks the completed shift from "kanban dashboard" to "operations layer" — same constraints (zero dependencies, single file, local-first), larger identity. Three sequential additions land here: Workspace Health (v2.3), Queue tab (v2.4), and Saved Lenses (this release).

### Added

- **Saved Lenses.** A new lens dropdown sits next to the workspace selector. Six built-in stock lenses ship with the app — *Stale > 7d, not done*, *No labels*, *Cost > $5 and idle > 24h*, *Needs Review (any workspace)*, *Active in last 24h*, *Abandoned (≤1 msg, idle 24h+)*. User lenses can be saved from the current view (workspace + search + sort) and follow you across browsers via a small JSON file at `~/.craft-agent/workspaces/.mission-control/lenses.json`.
- **`?lens=ID` URL parameter.** Lenses are bookmarkable and shareable. Loaded on page init, applied if it matches a stock or user lens.
- **Two new API endpoints.** `GET /api/lenses` returns saved user lenses; `POST /api/lenses` replaces them. Validated server-side: lens id format, name length, sort value, max 50 lenses. Atomic write via the same `_atomic_write_lines` helper used for session updates.

### Changed

- **`matchesFilters` now consults the active stock lens** in addition to workspace + search filters. User lenses don't add a predicate — they restore filter state — so they're orthogonal.

## [2.4.0] — 2026-04-25

### Added

- **Queue tab.** New tab next to Board — same data, different lens. Six fixed triage lanes derived from session metadata: *Needs decision*, *Blocked*, *Cost spike*, *Stale but important*, *Idle automations*, *Fresh*. A session can appear in multiple lanes (intentional for triage). Each card shows a lane-match hint in its corner so you can see *why* it's there. Cards keep their click-to-expand, label-edit, open-in-Craft, and batch-select behaviour from the Board.
- **`assign_queue_lanes(session, ws_median, now_ms)`** helper, plus `_workspace_cost_medians` for per-workspace cost-spike detection (median computed over sessions with cost > 0; needs ≥3 paid sessions before the lane fires for that workspace, so noise from cold workspaces stays suppressed).
- **`queueLanes` payload** — `build_data()` now emits the lane definitions and attaches a `lanes` array to each open session. Auto-refresh keeps lane assignments fresh when sessions move, get relabelled, or accumulate cost.

### Notes

- The Queue view is read-only with respect to lanes (lanes are derived, not editable). Drag-and-drop is not enabled in Queue — there's no canonical drop target for a derived lane. Use the Board for status changes; use Queue for triage.

## [2.3.0] — 2026-04-25

### Added

- **Workspace Health view.** New tab next to Board and Archive — a portfolio-level read on every workspace. Each workspace gets a card showing open count, stale (7d+) count, total cost, a 14-day open/cost trend sparkline, review backlog (count + age of oldest), idle automations, and a health badge: `healthy`, `attention`, or `overloaded`. Click a card to drill into the Board pre-filtered to that workspace.
- **Daily snapshots** at `~/.craft-agent/workspaces/.mission-control/history.jsonl`. One line per (workspace, date), retained for 14 days, written idempotently when `build_data()` runs. The first snapshot writes today; the trend fills in over time.
- **Health badge thresholds** (constants at the top of `dashboard.py`): >20 open → overloaded; >5 stale or any review > 3d → attention; else healthy. Deliberately fixed in this release.

### Changed

- **`build_data()` now emits a `health` payload** alongside `workspaces` and `sessions`. Auto-refresh keeps it fresh. The history file is mtime-cached so the 3-second poll doesn't re-parse it on every tick.

## [2.2.0] — 2026-04-17

### Added

- **Live auto-refresh.** The dashboard now polls `/api/data` every 3 seconds (when the tab is visible) and picks up external session changes — relabels, status moves, renames — without a page reload. Manual trigger exposed as `window.__mcRefresh()` for debugging.
- **Animated column-to-column moves.** When a session's status changes (from anywhere — a drag, a batch op, or an external script), the card node is reused and glides from its old column to its new one using the FLIP technique (First-Last-Invert-Play). A short accent pulse highlights cards that actually changed columns, with a gentle stagger when multiple cards move in the same tick.
- **`prefers-reduced-motion` support.** Animations are suppressed for users with the OS-level reduced-motion preference enabled.

### Changed

- **`renderBoard` reconciles instead of wiping `innerHTML`.** Card DOM nodes persist across renders in a registry keyed by session id, so each card keeps its identity when it moves. Column shells are still rebuilt on each render (cheap) but cards are re-parented, which is what makes the FLIP animation possible.
- **`renderCard` split into `buildCardInnerHTML` + `applyCardAttrs`.** The inner HTML is recomputed on every render (still cheap), but the outer `.card` element is reused. Drag listeners are now attached via a `WeakSet` guard so reused cards never accumulate duplicate handlers.
- **Refresh is suppressed while the user is mid-drag, has a label picker open, or the tab is hidden** — avoids yanking the UI out from under the user.

## [2.1.0] — 2026-04-17

### Added

- **Cross-platform auto-start.** Linux support via a systemd user unit (`~/.config/systemd/user/mission-control.service`) and Windows support via a Scheduled Task (`schtasks.exe /TN CraftAgentMissionControl`). Mission Control now runs on all three major platforms out of the box. Closes the "Cross-platform auto-start" near-term roadmap item.
- **`install.py` — single stdlib-only installer brain.** Dispatches per-OS autostart (`launchctl` / `systemctl --user` / `schtasks.exe`), does health check via `urllib` (not `curl`), supports `--port N`, `--no-autostart`, `--yes`, and `--skill-dir PATH`.
- **`install.ps1` / `uninstall.ps1`** — PowerShell wrappers for Windows users that forward to the Python brain.
- **`uninstall.py`** — symmetric cross-platform uninstaller that reverses the autostart entries.
- **Linux systemd user unit template** at `setup/mission-control.service` with parameterized `{PYTHON}`, `{DASHBOARD}`, `{PORT}`, `{LOG_PATH}`.
- **`CRAFT_HOME` env override.** Setting `CRAFT_HOME` points the server at a different workspaces directory — primarily for the CI smoke test and local development against a sandbox tree.

### Changed

- **`install.sh` and `uninstall.sh` slimmed to thin wrappers** that exec `python3 install.py "$@"` / `python3 uninstall.py "$@"`. Logic lives in one place now.
- **Deeplink dispatcher** in `dashboard.py` (`/api/open-url`, `/api/open`) is now cross-platform: `open` on macOS, `xdg-open` on Linux, `os.startfile` on Windows. Old code hardcoded `["open", url]`.
- **Plist template** parameterizes `{PORT}` and `{LOG_PATH}` (previously hardcoded `9753` and `/tmp/mission-control.log`). `install.py` substitutes them at install time.
- **`/health` response** drops the `pid` field; now returns `{"ok": true, "version": "2.1.0"}`. No known downstream consumers.
- **CI matrix expanded** to `[ubuntu-latest, macos-latest, windows-latest] × ['3.9', '3.10', '3.11', '3.12']` = 12 configurations for `py_compile`. Added a **PSScriptAnalyzer** job for `install.ps1` / `uninstall.ps1` and a **server smoke test** that boots the server against a `CRAFT_HOME` sandbox and verifies `/health`.
- **`SKILL.md` `alwaysAllow`** tightened from the blanket `["Bash"]` to scoped entries: `browser_tool`, `Read`, and per-command `Bash(launchctl:*)` / `Bash(systemctl:*)` / `Bash(schtasks:*)` / `Bash(python3:*)`.

### Security

- **Input validation on POST endpoints.** New `_read_json_body()` helper enforces a 64 KB body cap (413) and a JSON parse guard (400 "Invalid JSON" instead of leaking a stack trace). Status values are validated against `^[a-z][a-z0-9-]{0,31}$`, labels must be a list of ≤32 strings each ≤128 chars, and `craftagents://` URLs are capped at 2048 chars with a control-character check.
- **`session.jsonl` reads now use an mtime-keyed cache.** Repeated `/api/data` requests skip re-parsing session files that haven't changed — mitigates a minor amplification risk on very large workspaces in addition to the performance win.

### Fixed

- Silent `except Exception: pass` blocks in `collect()` replaced with `logger.warning("failed to parse %s: %s", path, e)`. A malformed workspace `config.json` is now visible in stderr instead of being silently dropped from the board.
- `NOW_MS` module global removed. `build_data()`, `get_alerts()`, and `_refresh_data()` now compute `now_ms` locally and pass it as a parameter — eliminates the latent race if the server ever moves to a threaded request handler.
- Hardcoded `'automated'` status fallback removed from the JS `ALWAYS_SHOW` set. Only `'todo'` remains pinned (with a comment explaining why).
- Missing docs — `INSTALL.md`, `SECURITY.md`, `ROADMAP.md`, `CONTRIBUTING.md` — are now copied to `~/.agents/skills/mission-control/` at install time. Previously they existed in the repo but weren't part of `SKILL_FILES`.
- Data attributes on rendered HTML (`data-id`, `data-ws`, `data-status`, etc.) now pass through `esc()` — defense in depth against injection via maliciously crafted session/workspace identifiers.

### Migration notes

- **Existing macOS users:** re-run `bash install.sh` (now a wrapper → `install.py`). The plist regenerates cleanly. If you consume the `/health` response programmatically, the `pid` field is gone — check for `ok` / `version` only.
- **No data-file migrations.**

## [2.0.1] — 2026-04-17

### Security

- **Closed CSRF vector.** Removed `Access-Control-Allow-Origin: *` from API responses and preflight. Mutating `POST` endpoints (`/api/status`, `/api/labels`, `/api/batch/status`, `/api/open-url`, `/api/open`) now reject cross-origin requests with `403 Forbidden`. Any browser tab could previously issue state-changing requests while the dashboard was running.
- **Path-traversal hardening on session writes.** Workspace and session identifiers coming in via `POST` bodies are now validated against strict patterns (`^\d{6}-[a-z]+-[a-z]+$` for session IDs; `^[A-Za-z0-9_-]+$` for workspace slugs) and resolved paths are confirmed to stay inside `~/.craft-agent/workspaces/` before any write. Defense in depth against symlink escape and crafted inputs.
- **Atomic writes to `session.jsonl`.** `update_session_status` and `update_session_labels` now write via `tempfile.mkstemp` + `os.replace()`. A process kill or crash mid-write can no longer leave a truncated session file.

### Fixed

- Sort dropdown options rendered as literal `Sort: Name (A\u2013Z)` / `Sort: Cost (High\u2013Low)`. Now render as `Sort: Name (A–Z)` / `Sort: Cost (High–Low)` as intended.
- Toggling a label in the label picker no longer forces a full `location.reload()`. Label pills update in place, preserving scroll position, expanded cards, selection, and search state.
- `python3 dashboard.py --help` and `--version` no longer try to write HTML to a file named `--help`. They now print usage/version and exit cleanly. `-h` / `-V` short forms are also accepted.

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

[Unreleased]: https://github.com/CS-Workx/craft-agent-mission-control/compare/v2.1.0...HEAD
[2.1.0]: https://github.com/CS-Workx/craft-agent-mission-control/releases/tag/v2.1.0
[2.0.1]: https://github.com/CS-Workx/craft-agent-mission-control/releases/tag/v2.0.1
[2.0.0]: https://github.com/CS-Workx/craft-agent-mission-control/releases/tag/v2.0.0
[1.0.0]: https://github.com/CS-Workx/craft-agent-mission-control/releases/tag/v1.0.0
