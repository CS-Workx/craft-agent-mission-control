# Changelog

All notable changes to Mission Control will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

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

[1.0.0]: https://github.com/CS-Workx/craft-agent-mission-control/releases/tag/v1.0.0
