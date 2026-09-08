"""Phase 3 — tests for mc.py, the cross-workspace tasking CLI.

Priority target: the write paths (dispatch, cleanup) and their guards, since
those mutate real workspace automation files and spawn live agents. No network
is touched — every test drives the filesystem/pure logic directly. HTTP-backed
commands (list/queue/show/status/label/open/new) are out of scope here; they'd
need a fake server and belong to a later phase.
"""

import json
from datetime import datetime, timedelta

import pytest

import mc


DAY_MS = 86_400_000


# ─── pure helpers ────────────────────────────────────────────────────────────

class TestDaysSince:
    def test_zero_when_ms_falsy(self):
        assert mc.days_since(0, 1_000) == 0.0
        assert mc.days_since(None, 1_000) == 0.0

    def test_exact_days(self):
        now = 10 * DAY_MS
        assert mc.days_since(3 * DAY_MS, now) == 7.0

    def test_fractional_days(self):
        assert mc.days_since(0 + DAY_MS // 2, DAY_MS) == 0.5


class TestResolveUuid:
    def _data(self):
        return {"workspaces": [
            {"id": "alpha", "wsUuid": "uuid-a"},
            {"id": "beta", "wsUuid": "uuid-b"},
            {"id": "gamma"},  # no wsUuid
        ]}

    def test_found(self):
        assert mc.resolve_uuid(self._data(), "beta") == "uuid-b"

    def test_missing_wsuuid_returns_empty(self):
        assert mc.resolve_uuid(self._data(), "gamma") == ""

    def test_unknown_slug_returns_empty(self):
        assert mc.resolve_uuid(self._data(), "nope") == ""


class TestCronAt:
    def test_relative_minutes_returns_five_field_cron(self):
        cron, fire = mc._cron_at("+5m", "Europe/Brussels")
        assert len(cron.split()) == 5
        assert isinstance(fire, datetime)
        assert fire > datetime.now()

    def test_relative_hours(self):
        _, fire = mc._cron_at("+2h", "Europe/Brussels")
        # ~2h in the future (allow a little slack for execution time)
        delta = fire - datetime.now()
        assert timedelta(hours=1, minutes=59) < delta <= timedelta(hours=2, seconds=5)

    def test_absolute_time_in_past_rolls_to_next_day(self):
        past = (datetime.now() - timedelta(minutes=5)).strftime("%H:%M")
        _, fire = mc._cron_at(past, "Europe/Brussels")
        assert fire > datetime.now()

    def test_cron_encodes_fire_stamp(self):
        cron, fire = mc._cron_at("+3m", "Europe/Brussels")
        assert cron == f"{fire.minute} {fire.hour} {fire.day} {fire.month} *"

    def test_bad_spec_exits(self):
        with pytest.raises(SystemExit):
            mc._cron_at("soon", "Europe/Brussels")

    def test_bad_relative_unit_exits(self):
        with pytest.raises(SystemExit):
            mc._cron_at("+5d", "Europe/Brussels")


class TestWsDirGuard:
    def test_slash_is_rejected(self, mc_home):
        with pytest.raises(SystemExit):
            mc.ws_dir("foo/bar")

    def test_dotdot_is_rejected(self, mc_home):
        with pytest.raises(SystemExit):
            mc.ws_dir("../etc")

    def test_leading_dot_is_rejected(self, mc_home):
        with pytest.raises(SystemExit):
            mc.ws_dir(".hidden")

    def test_unknown_workspace_without_config_is_rejected(self, mc_home):
        (mc_home.workspaces / "ghost").mkdir()  # dir but no config.json
        with pytest.raises(SystemExit):
            mc.ws_dir("ghost")

    def test_valid_workspace_returns_path(self, mc_home):
        d = mc_home.make_ws("alpha")
        assert mc.ws_dir("alpha") == d


# ─── automations read/write round-trip ───────────────────────────────────────

class TestAutomationsIO:
    def test_missing_file_returns_default(self, mc_home):
        d = mc_home.make_ws("alpha")
        obj = mc._read_automations(d)
        assert obj == {"version": 2, "automations": {}}

    def test_write_then_read_roundtrip(self, mc_home):
        d = mc_home.make_ws("alpha")
        payload = {"version": 2, "automations": {"SchedulerTick": [{"id": "mc-1"}]}}
        mc._write_automations(d, payload)
        assert json.loads((d / "automations.json").read_text()) == payload
        assert mc._read_automations(d) == payload

    def test_defaults_filled_on_partial_file(self, mc_home):
        d = mc_home.make_ws("alpha")
        (d / "automations.json").write_text(json.dumps({"automations": {}}))
        obj = mc._read_automations(d)
        assert obj["version"] == 2  # defaulted

    def test_corrupt_file_tolerant_flags_unreadable(self, mc_home):
        d = mc_home.make_ws("alpha")
        (d / "automations.json").write_text("{not json")
        obj = mc._read_automations(d, tolerant=True)
        assert obj.get("_unreadable") is True

    def test_corrupt_file_strict_exits(self, mc_home):
        d = mc_home.make_ws("alpha")
        (d / "automations.json").write_text("{not json")
        with pytest.raises(SystemExit):
            mc._read_automations(d)

    def test_write_is_atomic_leaves_no_tmp(self, mc_home):
        d = mc_home.make_ws("alpha")
        mc._write_automations(d, {"version": 2, "automations": {}})
        assert not list(d.glob("*.tmp"))


# ─── dispatch (Tier 3 write path) ─────────────────────────────────────────────

FOOTER_MARKER = "WHEN YOU ARE DONE"


def _automations(ws_dir):
    return json.loads((ws_dir / "automations.json").read_text())


class TestDispatch:
    def test_dry_run_writes_nothing(self, mc_home, capsys):
        mc_home.make_ws("alpha")
        mc.main(["dispatch", "alpha", "--prompt", "Do the thing"])
        out = capsys.readouterr().out
        assert "DRY-RUN" in out
        assert not (mc_home.workspaces / "alpha" / "automations.json").exists()

    def test_go_arms_one_scheduler_tick(self, mc_home, capsys):
        d = mc_home.make_ws("alpha")
        mc.main(["dispatch", "alpha", "--prompt", "Do the thing", "--at", "+5m", "--go"])
        ticks = _automations(d)["automations"]["SchedulerTick"]
        assert len(ticks) == 1
        e = ticks[0]
        assert e["id"].startswith(mc.DISPATCH_ID_PREFIX)
        assert e["name"].startswith(mc.DISPATCH_NAME_PREFIX)
        assert e["labels"] == ["mc-dispatch"]
        assert e["permissionMode"] == "allow-all"  # default
        assert e["actions"][0]["type"] == "prompt"

    def test_footer_appended_by_default(self, mc_home):
        d = mc_home.make_ws("alpha")
        mc.main(["dispatch", "alpha", "--prompt", "Do the thing", "--go"])
        prompt = _automations(d)["automations"]["SchedulerTick"][0]["actions"][0]["prompt"]
        assert FOOTER_MARKER in prompt

    def test_no_footer_flag_omits_footer(self, mc_home):
        d = mc_home.make_ws("alpha")
        mc.main(["dispatch", "alpha", "--prompt", "Do the thing", "--no-footer", "--go"])
        prompt = _automations(d)["automations"]["SchedulerTick"][0]["actions"][0]["prompt"]
        assert FOOTER_MARKER not in prompt
        assert prompt == "Do the thing"

    def test_footer_not_double_appended(self, mc_home):
        d = mc_home.make_ws("alpha")
        already = "Do the thing" + mc.COMPLETION_FOOTER
        mc.main(["dispatch", "alpha", "--prompt", already, "--go"])
        prompt = _automations(d)["automations"]["SchedulerTick"][0]["actions"][0]["prompt"]
        assert prompt.count(FOOTER_MARKER) == 1

    def test_custom_labels_and_name(self, mc_home):
        d = mc_home.make_ws("alpha")
        mc.main(["dispatch", "alpha", "--prompt", "X", "--labels", "urgent,triage",
                 "--name", "nightly sweep", "--go"])
        e = _automations(d)["automations"]["SchedulerTick"][0]
        assert e["labels"] == ["urgent", "triage"]
        assert e["name"].startswith(mc.DISPATCH_NAME_PREFIX)  # prefix forced on
        assert "nightly sweep" in e["name"]

    def test_model_and_conn_optional_fields(self, mc_home):
        d = mc_home.make_ws("alpha")
        mc.main(["dispatch", "alpha", "--prompt", "X", "--model", "claude-opus-5",
                 "--conn", "anthropic", "--go"])
        e = _automations(d)["automations"]["SchedulerTick"][0]
        assert e["model"] == "claude-opus-5"
        assert e["llmConnection"] == "anthropic"

    def test_dispatch_appends_not_overwrites(self, mc_home):
        d = mc_home.make_ws("alpha")
        mc.main(["dispatch", "alpha", "--prompt", "first", "--go"])
        mc.main(["dispatch", "alpha", "--prompt", "second", "--go"])
        assert len(_automations(d)["automations"]["SchedulerTick"]) == 2


# ─── dispatched listing + cleanup ─────────────────────────────────────────────

class TestDispatchedAndCleanup:
    def _arm(self, mc_home, slug="alpha", prompt="X"):
        mc_home.make_ws(slug)
        mc.main(["dispatch", slug, "--prompt", prompt, "--go"])
        return mc_home.workspaces / slug

    def test_iter_dispatched_finds_armed_entry(self, mc_home):
        self._arm(mc_home)
        found = list(mc._iter_dispatched(None, "alpha"))
        assert len(found) == 1
        slug, ev, e = found[0]
        assert slug == "alpha" and ev == "SchedulerTick"
        assert e["id"].startswith(mc.DISPATCH_ID_PREFIX)

    def test_iter_dispatched_scans_all_workspaces_when_unfiltered(self, mc_home):
        self._arm(mc_home, "alpha")
        self._arm(mc_home, "beta")
        slugs = {slug for slug, _, _ in mc._iter_dispatched(None)}
        assert slugs == {"alpha", "beta"}

    def test_cleanup_dry_run_keeps_entry(self, mc_home, capsys):
        d = self._arm(mc_home)
        mc.main(["cleanup", "--ws", "alpha"])
        out = capsys.readouterr().out
        assert "DRY-RUN" in out
        assert _automations(d)["automations"]["SchedulerTick"]  # still there

    def test_cleanup_go_removes_entry_and_empty_event(self, mc_home):
        d = self._arm(mc_home)
        mc.main(["cleanup", "--ws", "alpha", "--go"])
        # SchedulerTick had a single mc entry -> whole key removed when emptied
        assert _automations(d)["automations"] == {}

    def test_cleanup_by_id_only_targets_that_entry(self, mc_home):
        d = self._arm(mc_home)
        # add a second, non-mc automation by hand; cleanup must leave it
        obj = _automations(d)
        obj["automations"]["SchedulerTick"].append({"id": "keep-me", "name": "hand-rolled"})
        mc._write_automations(d, obj)
        mc_id = [e["id"] for e in obj["automations"]["SchedulerTick"]
                 if e["id"].startswith(mc.DISPATCH_ID_PREFIX)][0]
        mc.main(["cleanup", "--ws", "alpha", "--id", mc_id, "--go"])
        remaining = [e["id"] for e in _automations(d)["automations"]["SchedulerTick"]]
        assert remaining == ["keep-me"]

    def test_cleanup_nothing_to_do(self, mc_home, capsys):
        mc_home.make_ws("alpha")
        mc.main(["cleanup", "--ws", "alpha"])
        assert "nothing to clean up" in capsys.readouterr().out
