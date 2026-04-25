# Roadmap

Mission Control is a zero-dependency, single-file Python dashboard. The roadmap below is a living document — items move up and down as real needs emerge. If you want something listed here, open an issue or PR.

## Direction

With v3.0, Mission Control crossed from "kanban dashboard" into **operations layer for Craft Agents** — visibility, governance, and triage on top of native primitives (workspaces, sessions, statuses, labels). The constraints stay the same: zero dependencies, single `dashboard.py`, local-first, no `pip install`. Anything that would break those is in *Won't do*.

## Near-term (v3.x)

- **Tests** — unit tests for `collect()`, `build_data()`, the status-change API, and the new v3.0 helpers (`compute_health`, `assign_queue_lanes`, `read_user_lenses`, `write_user_lenses`). Pytest with fixtures mocking a fake `~/.craft-agent/workspaces/` tree.
- **Automation integration** — route status changes through Craft Agents' internal API (if exposed) so `SessionStatusChange` automations fire correctly. Today the dashboard writes `session.jsonl` directly, which bypasses the event bus.
- **Keyboard shortcuts** — batch-select navigation, archive paging (j/k), quick status change (1/2/3 for todo/in-progress/done), lens menu via Cmd+L.
- **Configurable health thresholds** — currently hardcoded (>20 open → overloaded, >5 stale → attention, etc.). Move to a small JSON config in `~/.craft-agent/workspaces/.mission-control/`.
- **Per-workspace stale thresholds** — configurable stale window (default 7 days) per workspace instead of hardcoded.
- **Bulk label operations** — apply/remove labels on selected sessions via the batch bar (labels currently work one card at a time).

## Recently shipped

- **Saved Lenses** (v3.0) — six stock lenses, file-backed user lenses, two API endpoints, `?lens=` URL parameter. Replaces the older "Saved filter presets" item.
- **Queue tab** (v2.4) — six fixed triage lanes derived from session metadata.
- **Workspace Health view** (v2.3) — per-workspace cards, 14-day trend sparklines, health badge, daily snapshot file at `~/.craft-agent/workspaces/.mission-control/history.jsonl`.
- **Cross-platform auto-start** (v2.1.0) — systemd user unit for Linux, Scheduled Task via `schtasks.exe` for Windows, plus a single `install.py` that dispatches per-OS. macOS Launch Agent still works unchanged.

## Mid-term

- **WebSocket live updates** — replace the re-collect-on-every-load model with push updates when `session.jsonl` changes. Use stdlib-only where possible (likely just long-polling to stay dependency-free).
- **Session-preview thumbnails** — render the first 200 chars of each session's last assistant message as a small card preview.
- **Mobile-friendly view** — the current CSS assumes ≥1024px; a compact single-column mobile view would make the dashboard useful on iPad.
- **Shared-instance mode** — optional basic auth + HTTPS for teams that want to host Mission Control on a shared machine. Opt-in only; default stays localhost-no-auth.

## Long-term / ideas — bigger swings

These are the directions an "AgentOps cockpit" could grow into. Each is deferred for a real reason — usually that it requires Craft Agents to expose APIs that aren't there yet, or that it would push past the single-file constraint. Listed here as deliberate choices, not as backlog.

- **Orchestration graph** — parent/child session relationships visualised as a network. Requires those relationships to be first-class data in `session.jsonl`. Until that's verified, anything we build is inferred.
- **Automation Lab / visual rule builder** — needs Craft Agents to expose automation execution history through a stable API. Without that, the UI would be mocked.
- **Workflow templates / Flow Studio** — duplicates Craft Agents' native status concept. Risk of becoming a parallel system that drifts from the source of truth.
- **Label Garden / taxonomy designer** — useful but its own scope. Belongs in a future governance release.
- **Analytics view** — cost trends over time, session velocity, stale-rate by workspace, most-used models. The Health view's daily snapshot file already provides the raw data.
- **SLA policies, runbooks, review desk as separate object** — premature abstraction at current scale.
- **Plugin API** — allow custom card renderers (e.g., one repo's sessions show different metadata than another's).
- **Export snapshots** — export visible kanban state as a Craft Agents-native archive file.

## Won't do

- **A web framework rewrite.** The zero-dependencies rule is load-bearing — it makes Mission Control trivially auditable and trivially installable. Anything that would require `pip install` stays out.
- **A cloud-hosted version.** Mission Control is deliberately local-first. Sessions contain potentially sensitive work; centralizing them would be a regression.

---

See [CHANGELOG.md](CHANGELOG.md) for what's already shipped.
