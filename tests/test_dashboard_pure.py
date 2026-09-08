"""Phase 1 — pure-helper tests for dashboard.py.

Covers the side-effect-free logic named in the ROADMAP (using the actual,
underscored identifiers): resolve_label_color, _compute_health_badge,
_workspace_cost_medians, _assign_queue_lanes, plus the build_data pipeline.

No server and (except build_data's snapshot write) no filesystem: these assert
behaviour, thresholds, and boundary conditions directly.
"""

import dashboard


DAY_MS = 86_400_000
NOW = 1_700_000_000_000  # fixed "now" so age math is deterministic


# ─── resolve_label_color ─────────────────────────────────────────────────────

class TestResolveLabelColor:
    def test_dict_with_light_and_dark(self):
        assert dashboard.resolve_label_color({"light": "#111", "dark": "#eee"}) == ("#111", "#eee")

    def test_dict_missing_keys_falls_back_to_defaults(self):
        assert dashboard.resolve_label_color({}) == ("#888", "#aaa")

    def test_none_returns_defaults(self):
        assert dashboard.resolve_label_color(None) == ("#888", "#aaa")

    def test_non_string_returns_defaults(self):
        assert dashboard.resolve_label_color(42) == ("#888", "#aaa")

    def test_system_color_name(self):
        assert dashboard.resolve_label_color("accent") == dashboard.SYSTEM_COLORS["accent"]

    def test_slash_prefixed_uses_first_segment(self):
        # e.g. "success/whatever" -> SYSTEM_COLORS["success"]
        assert dashboard.resolve_label_color("success/foo") == dashboard.SYSTEM_COLORS["success"]

    def test_unknown_slash_prefix_defaults(self):
        assert dashboard.resolve_label_color("nope/foo") == ("#888", "#aaa")

    def test_plain_hex_string_used_for_both_modes(self):
        # An unknown, non-system string is treated as an explicit colour.
        assert dashboard.resolve_label_color("#abcdef") == ("#abcdef", "#abcdef")


# ─── _compute_health_badge ───────────────────────────────────────────────────

class TestHealthBadge:
    def test_healthy_when_all_below_thresholds(self):
        assert dashboard._compute_health_badge(0, 0, 0) == "healthy"

    def test_open_at_threshold_is_still_healthy(self):
        # strictly greater-than: == HEALTH_OVERLOADED_OPEN is NOT overloaded
        assert dashboard._compute_health_badge(dashboard.HEALTH_OVERLOADED_OPEN, 0, 0) == "healthy"

    def test_open_above_threshold_is_overloaded(self):
        assert dashboard._compute_health_badge(dashboard.HEALTH_OVERLOADED_OPEN + 1, 0, 0) == "overloaded"

    def test_overloaded_takes_precedence_over_attention(self):
        # many open AND many stale -> overloaded wins (checked first)
        assert dashboard._compute_health_badge(
            dashboard.HEALTH_OVERLOADED_OPEN + 1,
            dashboard.HEALTH_ATTENTION_STALE + 1,
            0,
        ) == "overloaded"

    def test_stale_above_threshold_is_attention(self):
        assert dashboard._compute_health_badge(0, dashboard.HEALTH_ATTENTION_STALE + 1, 0) == "attention"

    def test_stale_at_threshold_is_healthy(self):
        assert dashboard._compute_health_badge(0, dashboard.HEALTH_ATTENTION_STALE, 0) == "healthy"

    def test_review_age_above_threshold_is_attention(self):
        assert dashboard._compute_health_badge(0, 0, dashboard.HEALTH_ATTENTION_REVIEW_DAYS + 1) == "attention"

    def test_review_age_at_threshold_is_healthy(self):
        assert dashboard._compute_health_badge(0, 0, dashboard.HEALTH_ATTENTION_REVIEW_DAYS) == "healthy"


# ─── _workspace_cost_medians ─────────────────────────────────────────────────

def _sess(ws, cost, status="todo"):
    return {"wsId": ws, "cost": cost, "status": status}


class TestCostMedians:
    def test_below_min_samples_yields_none(self):
        # 2 paid sessions < QUEUE_COST_SPIKE_MIN_SAMPLES (3) -> None
        sessions = [_sess("a", 1.0), _sess("a", 5.0)]
        medians = dashboard._workspace_cost_medians(sessions, closed_status_ids=set())
        assert medians["a"] is None

    def test_odd_count_median_is_middle_value(self):
        sessions = [_sess("a", 1.0), _sess("a", 3.0), _sess("a", 9.0)]
        medians = dashboard._workspace_cost_medians(sessions, closed_status_ids=set())
        assert medians["a"] == 3.0

    def test_even_count_median_is_mean_of_middle_two(self):
        sessions = [_sess("a", 1.0), _sess("a", 2.0), _sess("a", 3.0), _sess("a", 4.0)]
        medians = dashboard._workspace_cost_medians(sessions, closed_status_ids=set())
        assert medians["a"] == 2.5

    def test_zero_cost_sessions_are_excluded(self):
        # Two paid + one zero-cost -> only 2 paid -> below min samples -> None
        sessions = [_sess("a", 4.0), _sess("a", 6.0), _sess("a", 0.0)]
        medians = dashboard._workspace_cost_medians(sessions, closed_status_ids=set())
        assert medians["a"] is None

    def test_closed_sessions_are_excluded(self):
        sessions = [
            _sess("a", 4.0),
            _sess("a", 6.0),
            _sess("a", 8.0, status="done"),  # closed -> excluded
        ]
        medians = dashboard._workspace_cost_medians(sessions, closed_status_ids={"done"})
        assert medians["a"] is None

    def test_medians_are_per_workspace(self):
        sessions = [
            _sess("a", 1.0), _sess("a", 2.0), _sess("a", 3.0),
            _sess("b", 10.0), _sess("b", 20.0), _sess("b", 30.0),
        ]
        medians = dashboard._workspace_cost_medians(sessions, closed_status_ids=set())
        assert medians["a"] == 2.0
        assert medians["b"] == 20.0


# ─── _assign_queue_lanes ─────────────────────────────────────────────────────

def _lane_session(**overrides):
    base = {
        "status": "todo",
        "rawLabels": [],
        "cost": 0,
        "lastUsedAt": NOW,      # fresh by default
        "createdAt": NOW - 10 * DAY_MS,  # old by default (not "fresh")
    }
    base.update(overrides)
    return base


class TestQueueLanes:
    def test_needs_review_maps_to_needs_decision(self):
        s = _lane_session(status="needs-review")
        assert "needs-decision" in dashboard._assign_queue_lanes(s, None, NOW)

    def test_blocked_label(self):
        s = _lane_session(rawLabels=["blocked"])
        assert "blocked" in dashboard._assign_queue_lanes(s, None, NOW)

    def test_waiting_on_label_is_blocked(self):
        s = _lane_session(rawLabels=["waiting-on::alice"])
        assert "blocked" in dashboard._assign_queue_lanes(s, None, NOW)

    def test_cost_spike_requires_median_and_multiplier(self):
        # median 2.0, mult 3 -> threshold 6.0; cost 7 > 6 -> spike
        s = _lane_session(cost=7.0)
        assert "cost-spike" in dashboard._assign_queue_lanes(s, 2.0, NOW)

    def test_cost_at_threshold_is_not_spike(self):
        # cost exactly median*mult is NOT > threshold
        s = _lane_session(cost=6.0)
        assert "cost-spike" not in dashboard._assign_queue_lanes(s, 2.0, NOW)

    def test_no_cost_spike_when_median_is_none(self):
        s = _lane_session(cost=1000.0)
        assert "cost-spike" not in dashboard._assign_queue_lanes(s, None, NOW)

    def test_stale_important_requires_age_and_priority_label(self):
        s = _lane_session(lastUsedAt=NOW - 8 * DAY_MS, rawLabels=["priority::high"])
        assert "stale-important" in dashboard._assign_queue_lanes(s, None, NOW)

    def test_stale_without_priority_label_is_not_stale_important(self):
        s = _lane_session(lastUsedAt=NOW - 8 * DAY_MS, rawLabels=["misc"])
        assert "stale-important" not in dashboard._assign_queue_lanes(s, None, NOW)

    def test_idle_automation(self):
        s = _lane_session(status="automated", lastUsedAt=NOW - 2 * DAY_MS)
        assert "idle-automation" in dashboard._assign_queue_lanes(s, None, NOW)

    def test_fresh_todo_created_recently(self):
        s = _lane_session(status="todo", createdAt=NOW - 3600_000)  # 1h ago
        assert "fresh" in dashboard._assign_queue_lanes(s, None, NOW)

    def test_fresh_only_for_idea_or_todo(self):
        s = _lane_session(status="in-progress", createdAt=NOW - 3600_000)
        assert "fresh" not in dashboard._assign_queue_lanes(s, None, NOW)

    def test_session_can_match_multiple_lanes(self):
        s = _lane_session(status="needs-review", rawLabels=["blocked"])
        lanes = dashboard._assign_queue_lanes(s, None, NOW)
        assert "needs-decision" in lanes and "blocked" in lanes


# ─── build_data (pipeline) ───────────────────────────────────────────────────

def _workspace(dir_name="alpha", sessions=None, statuses_raw=None):
    return {
        "dir_name": dir_name,
        "name": dir_name.title(),
        "ws_id": f"{dir_name}-id",
        "app_uuid": f"uuid-{dir_name}",
        "statuses_raw": statuses_raw or [],
        "labels": {},
        "sessions": sessions or [],
        "theme": None,
    }


class TestBuildData:
    def test_returns_expected_top_level_shape(self, craft_home):
        data = dashboard.build_data([_workspace()], now_ms=NOW)
        assert set(data) >= {
            "now", "workspaces", "sessions", "health", "queueLanes", "stockLenses",
        }
        assert data["now"] == NOW
        assert data["queueLanes"] == dashboard.QUEUE_LANES
        assert data["stockLenses"] == dashboard.STOCK_LENSES

    def test_session_defaults_and_status_fallback(self, craft_home):
        ws = _workspace(sessions=[{"id": "260101-a-b"}])  # minimal header
        data = dashboard.build_data([ws], now_ms=NOW)
        s = data["sessions"][0]
        assert s["id"] == "260101-a-b"
        assert s["status"] == "todo"      # default when sessionStatus absent
        assert s["name"] == "260101-a-b"  # falls back to id
        assert s["cost"] == 0

    def test_default_statuses_used_when_none_configured(self, craft_home):
        data = dashboard.build_data([_workspace()], now_ms=NOW)
        status_ids = {st["id"] for st in data["workspaces"][0]["statuses"]}
        assert {"todo", "done", "cancelled"} <= status_ids

    def test_ws_uuid_prefers_app_uuid(self, craft_home):
        data = dashboard.build_data([_workspace(dir_name="alpha")], now_ms=NOW)
        assert data["workspaces"][0]["wsUuid"] == "uuid-alpha"

    def test_closed_session_gets_no_lanes(self, craft_home):
        statuses = [
            {"id": "todo", "label": "Todo", "category": "open", "order": 1},
            {"id": "done", "label": "Done", "category": "closed", "order": 4},
        ]
        ws = _workspace(
            sessions=[{"id": "260101-a-b", "sessionStatus": "done"}],
            statuses_raw=statuses,
        )
        data = dashboard.build_data([ws], now_ms=NOW)
        assert data["sessions"][0]["lanes"] == []

    def test_open_session_gets_lane_assignment(self, craft_home):
        ws = _workspace(sessions=[{"id": "260101-a-b", "sessionStatus": "needs-review"}])
        data = dashboard.build_data([ws], now_ms=NOW)
        assert "needs-decision" in data["sessions"][0]["lanes"]

    def test_health_card_per_workspace(self, craft_home):
        data = dashboard.build_data([_workspace("alpha"), _workspace("beta")], now_ms=NOW)
        ids = {card["id"] for card in data["health"]}
        assert ids == {"alpha", "beta"}
