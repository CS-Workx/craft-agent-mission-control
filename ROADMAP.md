# Roadmap

Mission Control is a zero-dependency, single-file Python dashboard. The roadmap below is a living document — items move up and down as real needs emerge. If you want something listed here, open an issue or PR.

## Near-term (v2.x)

- **Tests** — unit tests for `collect()`, `build_data()`, and the status-change API. Pytest with fixtures mocking a fake `~/.craft-agent/workspaces/` tree.
- **Cross-platform auto-start** — systemd unit for Linux, Task Scheduler script for Windows. Current Launch Agent is macOS-only.
- **Automation integration** — route status changes through Craft Agents' internal API (if exposed) so `SessionStatusChange` automations fire correctly. Today the dashboard writes `session.jsonl` directly, which bypasses the event bus.
- **Keyboard shortcuts** — batch-select navigation, archive paging (j/k), quick status change (1/2/3 for todo/in-progress/done).
- **Saved filter presets** — persist `?ws=…&search=…&sort=…` combinations locally as named views.

## Mid-term (v3.0)

- **WebSocket live updates** — replace the re-collect-on-every-load model with push updates when `session.jsonl` changes. Use stdlib-only where possible (likely just long-polling to stay dependency-free).
- **Shared-instance mode** — optional basic auth + HTTPS for teams that want to host Mission Control on a shared machine. Opt-in only; default stays localhost-no-auth.
- **Session-preview thumbnails** — render the first 200 chars of each session's last assistant message as a small card preview.
- **Per-workspace stale thresholds** — configurable stale window (default 7 days) per workspace instead of hardcoded.

## Long-term / ideas

- **Analytics view** — cost trends over time, session velocity, stale-rate by workspace, most-used models.
- **Export snapshots** — export visible kanban state as a Craft Agents-native archive file.
- **Plugin API** — allow custom card renderers (e.g., one repo's sessions show different metadata than another's).
- **Mobile-friendly view** — the current CSS assumes ≥1024px; a compact single-column mobile view would make the dashboard useful on iPad.
- **Bulk label operations** — apply/remove labels on selected sessions via the batch bar (labels currently work one card at a time).

## Won't do

- **A web framework rewrite.** The zero-dependencies rule is load-bearing — it makes Mission Control trivially auditable and trivially installable. Anything that would require `pip install` stays out.
- **A cloud-hosted version.** Mission Control is deliberately local-first. Sessions contain potentially sensitive work; centralizing them would be a regression.

---

See [CHANGELOG.md](CHANGELOG.md) for what's already shipped.
