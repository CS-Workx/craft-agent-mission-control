#!/usr/bin/env python3
"""
Mission Control — Interactive cross-workspace session dashboard for Craft Agent.

Usage:
  python3 dashboard.py OUTPUT_FILE          Generate static HTML file
  python3 dashboard.py --serve [PORT]       Start interactive server (default: 9753)

Server mode enables drag-and-drop status changes via a local API.
"""

__version__ = "3.0.0"

import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
import threading
from pathlib import Path
from datetime import datetime, timedelta, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

logger = logging.getLogger("mission-control")
logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")

# ─── Config ──────────────────────────────────────────────────────────────────

CRAFT_DIR = Path(os.environ.get("CRAFT_HOME") or os.path.expanduser("~/.craft-agent"))
WORKSPACES_DIR = CRAFT_DIR / "workspaces"

SESSION_ID_RE = re.compile(r"^\d{6}-[a-z]+-[a-z]+$")
WS_DIR_RE = re.compile(r"^[A-Za-z0-9_-]+$")
STATUS_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
MAX_BODY = 64 * 1024
MAX_LABELS = 32
MAX_LABEL_LEN = 128
MAX_URL_LEN = 2048

WS_COLORS = [
    ("#8b5cf6", "#a78bfa"), ("#3b82f6", "#60a5fa"), ("#10b981", "#34d399"),
    ("#f59e0b", "#fbbf24"), ("#ef4444", "#f87171"), ("#ec4899", "#f472b6"),
    ("#06b6d4", "#22d3ee"), ("#f97316", "#fb923c"), ("#14b8a6", "#2dd4bf"),
    ("#6366f1", "#818cf8"), ("#84cc16", "#a3e635"), ("#d946ef", "#e879f9"),
    ("#0ea5e9", "#38bdf8"), ("#a855f7", "#c084fc"), ("#eab308", "#facc15"),
]

SYSTEM_COLORS = {
    "accent": ("#8b5cf6", "#a78bfa"), "info": ("#f59e0b", "#fbbf24"),
    "success": ("#10b981", "#34d399"), "destructive": ("#ef4444", "#f87171"),
    "foreground": ("#6b7280", "#9ca3af"),
}


# ─── Helpers ─────────────────────────────────────────────────────────────────

def resolve_label_color(color):
    if isinstance(color, dict):
        return (color.get("light", "#888"), color.get("dark", "#aaa"))
    if not color or not isinstance(color, str):
        return ("#888", "#aaa")
    if "/" in color:
        return SYSTEM_COLORS.get(color.split("/")[0], ("#888", "#aaa"))
    return SYSTEM_COLORS.get(color, (color, color))


def model_short(model):
    if not model: return ""
    m = model.lower()
    if "opus" in m: return "Opus"
    if "sonnet" in m: return "Sonnet"
    if "haiku" in m: return "Haiku"
    return ""


# ─── Data Collection ─────────────────────────────────────────────────────────

# Cache: path -> (mtime_ns, parsed_header) so repeated /api/data requests
# don't re-parse every session file when nothing has changed.
_COLLECT_CACHE = {}  # type: dict


def _read_json_file(path):
    try:
        return json.loads(path.read_text())
    except Exception as e:
        logger.warning("failed to parse %s: %s", path, e)
        return None


def _read_session_header(path):
    """Return the parsed first-line header of session.jsonl, using an mtime cache."""
    try:
        st = path.stat()
    except OSError as e:
        logger.warning("failed to stat %s: %s", path, e)
        return None
    key = str(path)
    cached = _COLLECT_CACHE.get(key)
    if cached and cached[0] == st.st_mtime_ns:
        return cached[1]
    try:
        with open(path) as f:
            line = f.readline().strip()
        if not line:
            return None
        header = json.loads(line)
    except Exception as e:
        logger.warning("failed to parse %s: %s", path, e)
        return None
    _COLLECT_CACHE[key] = (st.st_mtime_ns, header)
    return header


def collect():
    workspaces = []
    if not WORKSPACES_DIR.exists():
        return workspaces

    # Read top-level config for workspace UUIDs (needed for deeplinks)
    app_uuid_map = {}
    top_config = CRAFT_DIR / "config.json"
    if top_config.exists():
        top_data = _read_json_file(top_config)
        if top_data:
            for ws_entry in top_data.get("workspaces", []):
                slug = ws_entry.get("slug", "")
                if slug:
                    app_uuid_map[slug] = ws_entry.get("id", "")

    for ws_dir in sorted(WORKSPACES_DIR.iterdir()):
        if not ws_dir.is_dir() or ws_dir.name.startswith("."):
            continue
        config_path = ws_dir / "config.json"
        if not config_path.exists():
            continue
        config = _read_json_file(config_path)
        if config is None:
            continue

        # Load workspace theme
        theme_name = config.get("defaults", {}).get("colorTheme", "")
        theme = None
        if theme_name:
            theme_path = CRAFT_DIR / "themes" / f"{theme_name}.json"
            if theme_path.exists():
                theme = _read_json_file(theme_path)
        if not theme:
            default_path = CRAFT_DIR / "themes" / "default.json"
            if default_path.exists():
                theme = _read_json_file(default_path)

        ws = {
            "dir_name": ws_dir.name,
            "name": config.get("name", ws_dir.name),
            "ws_id": config.get("id", ""),
            "app_uuid": app_uuid_map.get(config.get("slug", ws_dir.name), ""),
            "statuses_raw": [],
            "labels": {},
            "sessions": [],
            "theme": theme,
        }

        st_path = ws_dir / "statuses" / "config.json"
        if st_path.exists():
            st_data = _read_json_file(st_path)
            if st_data:
                ws["statuses_raw"] = st_data.get("statuses", [])

        lb_path = ws_dir / "labels" / "config.json"
        if lb_path.exists():
            lb_data = _read_json_file(lb_path)
            if lb_data:
                def flatten(labels):
                    for lb in labels:
                        light, dark = resolve_label_color(lb.get("color"))
                        ws["labels"][lb["id"]] = {"name": lb.get("name", lb["id"]), "light": light, "dark": dark}
                        if "children" in lb:
                            flatten(lb["children"])
                flatten(lb_data.get("labels", []))

        sessions_dir = ws_dir / "sessions"
        if sessions_dir.exists():
            for sess_dir in sessions_dir.iterdir():
                if not sess_dir.is_dir():
                    continue
                jsonl = sess_dir / "session.jsonl"
                if not jsonl.exists():
                    continue
                header = _read_session_header(jsonl)
                if header is not None:
                    ws["sessions"].append(header)
        workspaces.append(ws)
    return workspaces


def build_data(workspaces, now_ms=None):
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    color_map = {}
    ws_list = []
    for i, ws in enumerate(workspaces):
        light, dark = WS_COLORS[i % len(WS_COLORS)]
        color_map[ws["dir_name"]] = (light, dark)

        statuses = []
        for s in ws["statuses_raw"]:
            statuses.append({
                "id": s["id"],
                "label": s.get("label", s["id"].replace("-", " ").title()),
                "category": s.get("category", "open"),
                "order": s.get("order", 99),
            })
        if not statuses:
            statuses = [
                {"id": "backlog", "label": "Backlog", "category": "open", "order": 0},
                {"id": "todo", "label": "Todo", "category": "open", "order": 1},
                {"id": "needs-review", "label": "Needs Review", "category": "open", "order": 3},
                {"id": "done", "label": "Done", "category": "closed", "order": 4},
                {"id": "cancelled", "label": "Cancelled", "category": "closed", "order": 5},
            ]

        # Build theme data for this workspace
        theme = ws.get("theme") or {}
        dark_theme = theme.get("dark", {})
        ws_theme = {
            "light": {
                "bg": theme.get("background", "#faf9fb"),
                "fg": theme.get("foreground", "#1a1625"),
                "ac": theme.get("accent", "#8b5cf6"),
                "ok": theme.get("success", "#16a34a"),
                "wn": theme.get("info", "#d97706"),
                "er": theme.get("destructive", "#dc2626"),
            },
            "dark": {
                "bg": dark_theme.get("background", "#1e1d21"),
                "fg": dark_theme.get("foreground", "#f5f5f7"),
                "ac": dark_theme.get("accent", "#a78bfa"),
                "ok": dark_theme.get("success", "#22c55e"),
                "wn": dark_theme.get("info", "#fbbf24"),
                "er": dark_theme.get("destructive", "#ef4444"),
            },
        }

        # Build flat label list for the label picker
        label_list = []
        for lb_id, lb_cfg in ws["labels"].items():
            label_list.append({"id": lb_id, "name": lb_cfg["name"], "cl": lb_cfg["light"], "cd": lb_cfg["dark"]})

        ws_list.append({
            "id": ws["dir_name"], "name": ws["name"],
            "wsUuid": ws.get("app_uuid") or ws.get("ws_id", ""),
            "cl": light, "cd": dark,
            "count": len(ws["sessions"]),
            "statuses": sorted(statuses, key=lambda s: s["order"]),
            "theme": ws_theme,
            "labelDefs": label_list,
        })

    sessions = []
    for ws in workspaces:
        ws_id = ws["dir_name"]
        cl, cd = color_map[ws_id]
        for sess in ws["sessions"]:
            parsed_labels = []
            for lb in (sess.get("labels") or []):
                parts = lb.split("::", 1)
                if parts[0] == "url": continue
                lb_cfg = ws["labels"].get(parts[0], {})
                parsed_labels.append({
                    "d": (parts[1] if len(parts) > 1 else lb_cfg.get("name", parts[0]))[:20],
                    "cl": lb_cfg.get("light", "#888"), "cd": lb_cfg.get("dark", "#aaa"),
                })
            tu = sess.get("tokenUsage") or {}
            sessions.append({
                "id": sess.get("id", ""), "name": sess.get("name", sess.get("id", "Unnamed")),
                "preview": (sess.get("preview") or "")[:120],
                "status": sess.get("sessionStatus", "todo"),
                "wsId": ws_id, "wsName": ws["name"], "wsUuid": ws.get("app_uuid") or ws.get("ws_id", ""), "wsCl": cl, "wsCd": cd,
                "model": model_short(sess.get("model", "")),
                "cost": tu.get("costUsd", 0) or 0,
                "lastUsedAt": sess.get("lastUsedAt", 0) or 0,
                "createdAt": sess.get("createdAt", 0) or 0,
                "msgs": sess.get("messageCount", 0) or 0,
                "flagged": sess.get("isFlagged", False),
                "labels": parsed_labels,
                "rawLabels": sess.get("labels") or [],
                "tokens": tu.get("totalTokens", 0) or 0,
                "sdkSid": sess.get("sdkSessionId", ""),
            })

    health = _compute_health(ws_list, sessions, color_map, now_ms)

    closed_status_ids = set()
    for ws in ws_list:
        for s in ws["statuses"]:
            if s.get("category") == "closed":
                closed_status_ids.add(s["id"])
    medians = _workspace_cost_medians(sessions, closed_status_ids)
    for s in sessions:
        if s["status"] in closed_status_ids:
            s["lanes"] = []
            continue
        s["lanes"] = _assign_queue_lanes(s, medians.get(s["wsId"]), now_ms)

    return {
        "now": now_ms,
        "workspaces": ws_list,
        "sessions": sessions,
        "health": health,
        "queueLanes": QUEUE_LANES,
        "stockLenses": STOCK_LENSES,
    }


def _compute_health(ws_list, sessions, color_map, now_ms):
    """Build the per-workspace health payload, write today's snapshot if
    needed, and attach the 14-day trend. Returns a list of cards keyed by
    workspace id, ordered the same as ws_list."""
    closed_status_ids = set()
    for ws in ws_list:
        for s in ws["statuses"]:
            if s.get("category") == "closed":
                closed_status_ids.add(s["id"])

    # Group sessions by workspace once.
    by_ws = {}
    for s in sessions:
        by_ws.setdefault(s["wsId"], []).append(s)

    summaries = []
    for ws in ws_list:
        ws_sessions = by_ws.get(ws["id"], [])
        open_sessions = [s for s in ws_sessions if s["status"] not in closed_status_ids]
        cost = sum((s.get("cost") or 0) for s in ws_sessions)
        stale_count = 0
        review_count = 0
        review_oldest_ms = 0
        automation_stale = 0
        for s in open_sessions:
            last = s.get("lastUsedAt") or 0
            age_ms = (now_ms - last) if last else now_ms
            age_days = age_ms / 86400000
            if age_days >= 7:
                stale_count += 1
            if s["status"] == "needs-review":
                review_count += 1
                if last and (now_ms - last) > review_oldest_ms:
                    review_oldest_ms = now_ms - last
            if s["status"] == "automated" and age_ms > HEALTH_AUTOMATION_STALE_HOURS * 3600000:
                automation_stale += 1
        review_oldest_days = int(review_oldest_ms / 86400000) if review_oldest_ms else 0
        summaries.append({
            "id": ws["id"],
            "name": ws["name"],
            "open": len(open_sessions),
            "stale": stale_count,
            "cost": cost,
            "review": {"count": review_count, "oldestDays": review_oldest_days},
            "automationStale": automation_stale,
        })

    # Persist today's open + cost snapshot before reading the trend, so today
    # shows up in the sparkline.
    _record_snapshot_if_needed(summaries)
    history = _read_history()

    cards = []
    for ws, summary in zip(ws_list, summaries):
        cl, cd = color_map.get(ws["id"], ("#888", "#aaa"))
        badge = _compute_health_badge(
            summary["open"], summary["stale"], summary["review"]["oldestDays"]
        )
        cards.append({
            "id": ws["id"],
            "name": ws["name"],
            "cl": cl,
            "cd": cd,
            "open": summary["open"],
            "stale": summary["stale"],
            "cost": round(summary["cost"], 2),
            "review": summary["review"],
            "automationStale": summary["automationStale"],
            "trend": _build_trend(history, ws["id"]),
            "badge": badge,
        })
    return cards


# ─── Status Update ──────────────────────────────────────────────────────────

def _safe_session_path(ws_dir_name, session_id):
    """Validate inputs and resolve to a session.jsonl path inside WORKSPACES_DIR.

    Returns the resolved Path on success, or None on any validation failure.
    """
    if not ws_dir_name or not WS_DIR_RE.match(ws_dir_name):
        return None
    if not session_id or not SESSION_ID_RE.match(session_id):
        return None
    try:
        target = (WORKSPACES_DIR / ws_dir_name / "sessions" / session_id / "session.jsonl").resolve()
        target.relative_to(WORKSPACES_DIR.resolve())
    except (ValueError, OSError):
        return None
    return target


def _safe_workspace_dir(ws_dir_name):
    """Validate workspace slug and resolve to a workspace directory inside WORKSPACES_DIR."""
    if not ws_dir_name or not WS_DIR_RE.match(ws_dir_name):
        return None
    try:
        target = (WORKSPACES_DIR / ws_dir_name).resolve()
        target.relative_to(WORKSPACES_DIR.resolve())
    except (ValueError, OSError):
        return None
    return target


def _atomic_write_lines(path, lines):
    """Write lines to path atomically via tmp file + os.replace()."""
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent), prefix=".sess-", suffix=".tmp"
    )
    try:
        with os.fdopen(tmp_fd, "w") as f:
            f.writelines(lines)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
        raise


def update_session_status(ws_dir_name, session_id, new_status):
    """Update sessionStatus in the first line of session.jsonl."""
    jsonl = _safe_session_path(ws_dir_name, session_id)
    if jsonl is None:
        return False, "Invalid workspace or session id"
    if not jsonl.exists():
        return False, "Session not found"
    try:
        with open(jsonl, "r") as f:
            lines = f.readlines()
        if not lines:
            return False, "Empty session file"
        meta = json.loads(lines[0])
        old_status = meta.get("sessionStatus", "todo")
        meta["sessionStatus"] = new_status
        lines[0] = json.dumps(meta, separators=(",", ":")) + "\n"
        _atomic_write_lines(jsonl, lines)
        return True, f"{old_status} -> {new_status}"
    except Exception as e:
        return False, str(e)


def update_session_labels(ws_dir_name, session_id, labels):
    """Update labels in the first line of session.jsonl."""
    jsonl = _safe_session_path(ws_dir_name, session_id)
    if jsonl is None:
        return False, "Invalid workspace or session id"
    if not jsonl.exists():
        return False, "Session not found"
    try:
        with open(jsonl, "r") as f:
            lines = f.readlines()
        if not lines:
            return False, "Empty session file"
        meta = json.loads(lines[0])
        meta["labels"] = labels
        lines[0] = json.dumps(meta, separators=(",", ":")) + "\n"
        _atomic_write_lines(jsonl, lines)
        return True, labels
    except Exception as e:
        return False, str(e)


def get_workspace_labels(ws_dir_name):
    """Return the full label tree for a workspace."""
    ws_dir = _safe_workspace_dir(ws_dir_name)
    if ws_dir is None:
        return []
    lb_path = ws_dir / "labels" / "config.json"
    if not lb_path.exists():
        return []
    try:
        return json.loads(lb_path.read_text()).get("labels", [])
    except Exception:
        return []


def get_alerts():
    """Return sessions needing attention (stale 7d+, open status)."""
    now_ms = int(time.time() * 1000)
    workspaces = collect()
    data = build_data(workspaces, now_ms=now_ms)
    alerts = []
    for s in data["sessions"]:
        if s["status"] in ("done", "cancelled"):
            continue
        age_days = (now_ms - (s.get("lastUsedAt") or 0)) / 86400000
        if age_days >= 7:
            alerts.append({
                "type": "stale",
                "sessionId": s["id"],
                "name": s["name"],
                "workspace": s["wsName"],
                "wsId": s["wsId"],
                "staleDays": int(age_days),
                "status": s["status"],
            })
    return alerts


# ─── Workspace Health (v2.3) ─────────────────────────────────────────────────

# Daily snapshots feed the 14-day trend sparklines on the Health tab. One line
# per (workspace, date), retained for the trend window only. Lives alongside
# Mission Control's other local state.
HEALTH_TREND_DAYS = 14
HEALTH_OVERLOADED_OPEN = 20
HEALTH_ATTENTION_STALE = 5
HEALTH_ATTENTION_REVIEW_DAYS = 3
HEALTH_AUTOMATION_STALE_HOURS = 24

_HISTORY_CACHE = None  # (mtime_ns, [records])


def _mc_state_dir():
    """Local-state directory for Mission Control (history, saved lenses, ...)."""
    return WORKSPACES_DIR / ".mission-control"


def _history_path():
    return _mc_state_dir() / "history.jsonl"


def _today_iso():
    return datetime.now().strftime("%Y-%m-%d")


def _read_history():
    """Return all history records, mtime-cached so polling doesn't re-parse."""
    global _HISTORY_CACHE
    path = _history_path()
    try:
        st = path.stat()
    except OSError:
        _HISTORY_CACHE = (0, [])
        return []
    if _HISTORY_CACHE and _HISTORY_CACHE[0] == st.st_mtime_ns:
        return _HISTORY_CACHE[1]
    records = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except (ValueError, json.JSONDecodeError):
                    pass
    except OSError as e:
        logger.warning("failed to read %s: %s", path, e)
        return []
    _HISTORY_CACHE = (st.st_mtime_ns, records)
    return records


def _record_snapshot_if_needed(ws_summaries, today=None):
    """Append today's per-workspace snapshot (open + cost) if missing, and
    trim records older than HEALTH_TREND_DAYS days. Idempotent within a day.
    Skips quietly on any I/O failure — the dashboard degrades to no-trend mode.
    """
    today = today or _today_iso()
    path = _history_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.warning("failed to create %s: %s", path.parent, e)
        return
    records = _read_history()
    have_today = {r.get("ws") for r in records if r.get("date") == today}
    needs_write = any(s["id"] not in have_today for s in ws_summaries)
    cutoff = (datetime.now() - timedelta(days=HEALTH_TREND_DAYS)).strftime("%Y-%m-%d")
    needs_trim = any((r.get("date") or "") < cutoff for r in records)
    if not needs_write and not needs_trim:
        return
    kept = [r for r in records if (r.get("date") or "") >= cutoff]
    for s in ws_summaries:
        if s["id"] in have_today:
            continue
        kept.append({
            "date": today,
            "ws": s["id"],
            "open": s["open"],
            "cost": round(s["cost"], 4),
        })
    lines = [json.dumps(r, separators=(",", ":")) + "\n" for r in kept]
    try:
        _atomic_write_lines(path, lines)
    except OSError as e:
        logger.warning("failed to write %s: %s", path, e)
        return
    # Force re-read on next access so the cache reflects what we just wrote.
    global _HISTORY_CACHE
    _HISTORY_CACHE = None


def _build_trend(records, ws_id, days=HEALTH_TREND_DAYS):
    """Return a `days`-long series oldest→newest with `null` for missing dates."""
    by_date = {r.get("date"): r for r in records if r.get("ws") == ws_id}
    today = datetime.now()
    series = []
    for i in range(days - 1, -1, -1):
        d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        rec = by_date.get(d)
        series.append({
            "d": d,
            "open": rec.get("open") if rec else None,
            "cost": rec.get("cost") if rec else None,
        })
    return series


def _compute_health_badge(open_count, stale_count, review_oldest_days):
    if open_count > HEALTH_OVERLOADED_OPEN:
        return "overloaded"
    if stale_count > HEALTH_ATTENTION_STALE or review_oldest_days > HEALTH_ATTENTION_REVIEW_DAYS:
        return "attention"
    return "healthy"


# ─── Queue Lanes (v2.4) ──────────────────────────────────────────────────────

# Triage-first re-grouping of open sessions. A session can appear in multiple
# lanes — that's intentional for triage. Lane order here is the display order.
QUEUE_LANES = [
    {"id": "needs-decision", "label": "Needs decision",
     "hint": "status is needs-review"},
    {"id": "blocked", "label": "Blocked",
     "hint": "labelled blocked or waiting-on"},
    {"id": "cost-spike", "label": "Cost spike",
     "hint": "cost > workspace median \u00d7 3"},
    {"id": "stale-important", "label": "Stale but important",
     "hint": "stale 7d+ AND priority/important/client label"},
    {"id": "idle-automation", "label": "Idle automations",
     "hint": "automated AND idle 24h+"},
    {"id": "fresh", "label": "Fresh",
     "hint": "created in last 24h"},
]
QUEUE_COST_SPIKE_MULT = 3
QUEUE_COST_SPIKE_MIN_SAMPLES = 3


def _workspace_cost_medians(sessions, closed_status_ids):
    """Median cost per workspace, computed over non-closed sessions whose cost
    is > 0 — zero-cost sessions would drag the threshold down to noise."""
    by_ws = {}
    for s in sessions:
        if s["status"] in closed_status_ids:
            continue
        cost = s.get("cost") or 0
        if cost <= 0:
            continue
        by_ws.setdefault(s["wsId"], []).append(cost)
    medians = {}
    for ws_id, costs in by_ws.items():
        if len(costs) < QUEUE_COST_SPIKE_MIN_SAMPLES:
            medians[ws_id] = None
            continue
        ordered = sorted(costs)
        n = len(ordered)
        medians[ws_id] = ordered[n // 2] if n % 2 else (ordered[n // 2 - 1] + ordered[n // 2]) / 2
    return medians


def _has_label_with_prefix(raw_labels, prefixes):
    """True if any raw label matches one of the prefixes (exact or `prefix::*`)."""
    for lb in raw_labels or []:
        for p in prefixes:
            if lb == p or lb.startswith(p + "::"):
                return True
    return False


def _lenses_path():
    return _mc_state_dir() / "lenses.json"


# Built-in lenses are codified predicates evaluated client-side. Display order
# mirrors this list. User lenses are appended after a divider.
STOCK_LENSES = [
    {"id": "stale-7d", "name": "Stale > 7d, not done", "kind": "stock"},
    {"id": "no-labels", "name": "No labels", "kind": "stock"},
    {"id": "cost-idle", "name": "Cost > $5 and idle > 24h", "kind": "stock"},
    {"id": "needs-review", "name": "Needs Review (any workspace)", "kind": "stock"},
    {"id": "active-24h", "name": "Active in last 24h", "kind": "stock"},
    {"id": "abandoned", "name": "Abandoned (\u22641 msg, idle 24h+)", "kind": "stock"},
]
MAX_USER_LENSES = 50
MAX_LENS_NAME_LEN = 80
LENS_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def read_user_lenses():
    """Return the saved user lenses, or [] if none."""
    path = _lenses_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError, json.JSONDecodeError) as e:
        logger.warning("failed to parse %s: %s", path, e)
        return []
    if not isinstance(data, list):
        return []
    return [l for l in data if isinstance(l, dict)]


def write_user_lenses(lenses):
    """Replace saved user lenses with the given list. Validates each entry;
    returns (ok, error_or_count)."""
    if not isinstance(lenses, list):
        return False, "Body must be a list"
    if len(lenses) > MAX_USER_LENSES:
        return False, f"Too many lenses (max {MAX_USER_LENSES})"
    cleaned = []
    seen_ids = set()
    for lens in lenses:
        if not isinstance(lens, dict):
            return False, "Each lens must be an object"
        lid = lens.get("id")
        name = lens.get("name")
        if not isinstance(lid, str) or not LENS_ID_RE.match(lid):
            return False, f"Invalid lens id: {lid!r}"
        if lid in seen_ids:
            return False, f"Duplicate lens id: {lid}"
        seen_ids.add(lid)
        if not isinstance(name, str) or not (0 < len(name) <= MAX_LENS_NAME_LEN):
            return False, "Invalid lens name"
        ws = lens.get("ws", "all")
        if not isinstance(ws, str) or len(ws) > 64:
            return False, "Invalid lens ws"
        search = lens.get("search", "") or ""
        if not isinstance(search, str) or len(search) > 200:
            return False, "Invalid lens search"
        sort = lens.get("sort", "activity")
        if sort not in ("activity", "name", "cost", "messages", "staleness"):
            return False, "Invalid lens sort"
        cleaned.append({"id": lid, "name": name, "ws": ws, "search": search, "sort": sort, "kind": "user"})
    path = _lenses_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_lines(path, [json.dumps(cleaned, separators=(",", ":"))])
    except OSError as e:
        return False, str(e)
    return True, len(cleaned)


def _assign_queue_lanes(s, ws_median, now_ms):
    """Return the list of lane ids this session belongs to."""
    raw = s.get("rawLabels") or []
    lanes = []
    if s["status"] == "needs-review":
        lanes.append("needs-decision")
    if _has_label_with_prefix(raw, ["blocked"]) or any(
        lb.startswith("waiting-on") for lb in raw
    ):
        lanes.append("blocked")
    cost = s.get("cost") or 0
    if ws_median and cost > ws_median * QUEUE_COST_SPIKE_MULT:
        lanes.append("cost-spike")
    last = s.get("lastUsedAt") or 0
    age_ms = (now_ms - last) if last else now_ms
    if age_ms >= 7 * 86400000 and _has_label_with_prefix(
        raw, ["priority", "important", "client"]
    ):
        lanes.append("stale-important")
    if s["status"] == "automated" and age_ms > 86400000:
        lanes.append("idle-automation")
    created = s.get("createdAt") or 0
    if created and (now_ms - created) < 86400000 and s["status"] in ("idea", "todo"):
        lanes.append("fresh")
    return lanes


# ─── HTML Template ───────────────────────────────────────────────────────────

def generate_html(data, api_base=""):
    ts = datetime.now(timezone.utc).strftime("%b %d, %Y at %H:%M UTC")
    data_json = json.dumps(data, separators=(",", ":"))

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mission Control</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
:root{{
  --bg:#f5f5f7;--bg2:#fff;--bg3:#fff;
  --tx:#1a1a2e;--tx2:#6b7280;--tx3:#9ca3af;
  --bd:#e5e7eb;--bd2:#d1d5db;
  --ac:#8b5cf6;--acs:rgba(139,92,246,.08);
  --ok:#10b981;--oks:rgba(16,185,129,.1);
  --wn:#f59e0b;--wns:rgba(245,158,11,.1);
  --er:#ef4444;--ers:rgba(239,68,68,.1);
  --sh:0 1px 3px rgba(0,0,0,.06);
  --sh2:0 4px 12px rgba(0,0,0,.08);
  --r:10px;--rs:6px;
  --input-bg:#f0f0f3;
  color-scheme:light dark
}}
@media(prefers-color-scheme:dark){{:root{{
  --bg:#1a1920;--bg2:#252430;--bg3:#201f28;
  --tx:#f0f0f5;--tx2:#9ca3af;--tx3:#6b7280;
  --bd:#35343f;--bd2:#45445f;
  --ac:#a78bfa;--acs:rgba(167,139,250,.12);
  --ok:#34d399;--oks:rgba(52,211,153,.12);
  --wn:#fbbf24;--wns:rgba(251,191,36,.12);
  --er:#f87171;--ers:rgba(248,113,113,.12);
  --sh:0 1px 3px rgba(0,0,0,.25);
  --sh2:0 4px 12px rgba(0,0,0,.35);
  --input-bg:#2e2d38
}}}}
body{{
  font-family:-apple-system,BlinkMacSystemFont,'SF Pro Text',Inter,system-ui,sans-serif;
  background:var(--bg);color:var(--tx);line-height:1.5;
  -webkit-font-smoothing:antialiased
}}
.wrap{{margin:0 auto;padding:24px}}

header{{background:var(--bg3);border:1px solid var(--bd);border-radius:var(--r);padding:20px 24px;margin-bottom:16px}}
.htop{{display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;gap:16px;flex-wrap:wrap}}
.htop-left{{display:flex;align-items:baseline;gap:12px}}
h1{{font-size:20px;font-weight:700;letter-spacing:-.02em}}
.ts{{font-size:12px;color:var(--tx3)}}
.stats{{display:flex;gap:28px;flex-wrap:wrap}}
.st{{display:flex;flex-direction:column;gap:2px}}
.sv{{font-size:22px;font-weight:700;letter-spacing:-.02em}}
.sl{{font-size:11px;font-weight:500;text-transform:uppercase;letter-spacing:.05em;color:var(--tx3)}}
.st-w .sv{{color:var(--wn)}}

.toolbar{{display:flex;gap:12px;align-items:center;margin-bottom:16px;flex-wrap:wrap}}
.toolbar-right{{display:flex;gap:12px;align-items:center;margin-left:auto;flex-wrap:wrap}}
.search-wrap{{position:relative;min-width:180px;width:280px}}
.search-icon{{position:absolute;left:12px;top:50%;transform:translateY(-50%);color:var(--tx3);pointer-events:none;font-size:14px}}
.search-box{{
  width:100%;padding:8px 14px 8px 36px;border-radius:8px;border:1px solid var(--bd);
  background:var(--input-bg);color:var(--tx);font-size:13px;outline:none
}}
.search-box:focus{{border-color:var(--ac)}}
.select{{
  padding:8px 12px;border-radius:8px;border:1px solid var(--bd);
  background:var(--input-bg);color:var(--tx);font-size:13px;cursor:pointer;outline:none
}}
.select:focus{{border-color:var(--ac)}}
.ws-selector{{font-weight:600;min-width:160px}}

.filters{{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px}}
.fpill{{
  display:inline-flex;align-items:center;gap:6px;
  padding:5px 12px;border-radius:20px;font-size:12px;font-weight:600;
  cursor:pointer;user-select:none;border:2px solid transparent;transition:all .15s
}}
.fpill.active{{color:#fff}}
.fpill.inactive{{opacity:.4}}
.pc{{font-size:11px;opacity:.75}}

.board{{display:flex;gap:16px;overflow-x:auto;padding-bottom:16px;align-items:flex-start}}
.col{{flex:0 0 280px;width:280px}}
.col-head{{
  display:flex;justify-content:space-between;align-items:center;
  padding:8px 12px;margin-bottom:8px;border-radius:var(--rs);
  background:var(--acs);position:sticky;top:0;z-index:1
}}
.col-closed{{opacity:.75}}
.col-head-closed{{background:var(--oks)}}
.col-closed .col-count{{color:var(--ok)}}
.col-title{{font-size:13px;font-weight:600}}
.col-count{{
  font-size:12px;font-weight:600;color:var(--ac);
  background:var(--bg);padding:1px 8px;border-radius:10px
}}
.col-cards{{display:flex;flex-direction:column;gap:8px;min-height:60px;border-radius:var(--rs);
  padding:4px;transition:background .15s}}
.col-cards.drag-over{{background:var(--acs);outline:2px dashed var(--ac);outline-offset:-2px}}
.empty{{text-align:center;padding:32px 16px;color:var(--tx3);font-size:13px;font-style:italic}}

.card{{
  background:var(--bg2);border:1px solid var(--bd);border-radius:var(--r);
  padding:14px;box-shadow:var(--sh);transition:all .15s
}}
.card:hover{{box-shadow:var(--sh2);border-color:var(--bd2)}}
.card.expanded{{border-color:var(--ac);box-shadow:0 0 0 1px var(--ac),var(--sh2)}}
.card.draggable{{cursor:grab}}
.card.draggable:active{{cursor:grabbing}}
.card.dragging{{opacity:.4;transform:scale(.97)}}
.card-top{{display:flex;align-items:center;gap:6px;margin-bottom:4px}}
.dot{{width:8px;height:8px;border-radius:50%;flex-shrink:0}}
.s-green{{background:var(--ok)}}.s-amber{{background:var(--wn)}}.s-red{{background:var(--er)}}
.flag{{color:var(--wn);font-size:12px;flex-shrink:0}}
.card-name{{font-size:13px;font-weight:600;flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.card-cost{{font-size:11px;color:var(--tx3);font-weight:500;flex-shrink:0}}
.card-preview{{font-size:12px;color:var(--tx2);margin-bottom:8px;line-height:1.4;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}}
.card.expanded .card-preview{{-webkit-line-clamp:unset;display:block}}
.badges{{display:flex;gap:4px;flex-wrap:wrap;margin-bottom:8px}}
.ws-badge{{display:inline-block;padding:2px 8px;border-radius:12px;font-size:10px;font-weight:600;color:#fff;white-space:nowrap}}
.model-badge{{display:inline-block;padding:2px 8px;border-radius:12px;font-size:10px;font-weight:500;background:var(--acs);color:var(--ac);white-space:nowrap}}
.label-pill{{display:inline-block;padding:1px 7px;border-radius:12px;font-size:10px;font-weight:500;white-space:nowrap;border:1px solid}}
.card-meta{{font-size:11px;color:var(--tx3);display:flex;justify-content:space-between;align-items:center}}
.open-btn{{
  display:inline-flex;align-items:center;gap:4px;
  padding:3px 10px;border-radius:6px;font-size:11px;font-weight:500;
  background:var(--acs);color:var(--ac);border:none;cursor:pointer;
  text-decoration:none;transition:all .15s;white-space:nowrap
}}
.open-btn:hover{{background:var(--ac);color:#fff}}
.open-btn svg{{width:12px;height:12px;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}}

.card-details{{display:none;margin-top:10px;padding-top:10px;border-top:1px solid var(--bd);font-size:12px;color:var(--tx2)}}
.card.expanded .card-details{{display:block}}
.detail-row{{display:flex;justify-content:space-between;padding:3px 0}}
.detail-label{{color:var(--tx3);font-weight:500}}

/* ─── FLIP animations ────────────────────────────────── */
.card{{will-change:transform}}
.card.flip-enter{{opacity:0;transform:scale(.96)}}
.card.just-moved{{animation:mc-pulse .7s ease-out}}
@keyframes mc-pulse{{
  0%{{box-shadow:0 0 0 0 var(--ac),var(--sh)}}
  60%{{box-shadow:0 0 0 8px rgba(0,0,0,0),var(--sh)}}
  100%{{box-shadow:0 0 0 0 rgba(0,0,0,0),var(--sh)}}
}}
@media(prefers-reduced-motion:reduce){{
  .card,.card.just-moved{{transition:none!important;animation:none!important}}
}}

.closed-section{{margin-top:24px;background:var(--bg3);border:1px solid var(--bd);border-radius:var(--r);overflow:hidden}}
.closed-toggle{{
  padding:14px 20px;font-size:14px;font-weight:600;
  cursor:pointer;color:var(--tx2);display:flex;align-items:center;gap:8px;
  background:none;border:none;width:100%;text-align:left
}}
.closed-toggle:hover{{color:var(--tx)}}
.closed-arrow{{transition:transform .2s;font-size:10px}}
.closed-section.open .closed-arrow{{transform:rotate(90deg)}}
.closed-body{{display:none}}
.closed-section.open .closed-body{{display:block}}
.ct{{width:100%;border-collapse:collapse;font-size:12px}}
.ct th{{text-align:left;padding:8px 16px;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.05em;color:var(--tx3);border-bottom:1px solid var(--bd);cursor:pointer;user-select:none}}
.ct th:hover{{color:var(--tx)}}
.ct th .arrow{{margin-left:4px;font-size:9px;opacity:.5}}
.ct th.sorted .arrow{{opacity:1;color:var(--ac)}}
.ct td{{padding:8px 16px;border-bottom:1px solid var(--bd);color:var(--tx2)}}
.cn{{font-weight:500;color:var(--tx)!important;max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.cm{{white-space:nowrap}}
.sb{{display:inline-block;padding:2px 8px;border-radius:12px;font-size:10px;font-weight:500}}
.sb-done{{background:var(--oks);color:var(--ok)}}
.sb-cancelled{{background:var(--ers);color:var(--er)}}
.no-results{{text-align:center;padding:48px 24px;color:var(--tx3)}}
.no-results-icon{{font-size:32px;margin-bottom:8px}}

.toast-container{{position:fixed;bottom:24px;right:24px;z-index:999;display:flex;flex-direction:column;gap:8px}}
.toast{{
  padding:10px 18px;border-radius:8px;font-size:13px;font-weight:500;
  background:var(--bg2);border:1px solid var(--bd);box-shadow:var(--sh2);
  transform:translateY(20px);opacity:0;transition:all .25s ease
}}
.toast.show{{transform:translateY(0);opacity:1}}
.toast.success{{border-left:3px solid var(--ok);color:var(--ok)}}
.toast.error{{border-left:3px solid var(--er);color:var(--er)}}

.col-new-btn{{width:22px;height:22px;font-size:14px;border-radius:50%;border-width:1px}}

.mode-badge{{
  display:inline-block;padding:3px 10px;border-radius:6px;font-size:11px;font-weight:600;
  margin-left:12px
}}
.mode-manage{{background:var(--oks);color:var(--ok)}}
.mode-view{{background:var(--acs);color:var(--ac)}}

/* ─── Feature 1: Export ──────────────────────────────── */
.toolbar-btn{{
  padding:7px 14px;border-radius:8px;border:1px solid var(--bd);
  background:var(--input-bg);color:var(--tx);font-size:13px;font-weight:500;
  cursor:pointer;outline:none;display:inline-flex;align-items:center;gap:6px;transition:all .15s
}}
.toolbar-btn:hover{{border-color:var(--ac);color:var(--ac)}}
.toolbar-btn svg{{width:14px;height:14px;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}}

/* ─── Feature 2: New Session ─────────────────────────── */
.new-btn{{
  width:28px;height:28px;border-radius:50%;border:2px dashed var(--bd2);
  background:none;color:var(--tx3);font-size:16px;cursor:pointer;
  display:inline-flex;align-items:center;justify-content:center;transition:all .15s;flex-shrink:0
}}
.new-btn:hover{{border-color:var(--ac);color:var(--ac);background:var(--acs)}}
.toolbar .new-btn{{width:34px;height:34px;font-size:18px;border-radius:8px}}

/* ─── Feature 3: Label Picker ────────────────────────── */
.label-btn{{
  display:inline-flex;align-items:center;gap:4px;
  padding:3px 8px;border-radius:6px;font-size:11px;font-weight:500;
  background:transparent;color:var(--tx3);border:none;cursor:pointer;transition:all .15s
}}
.label-btn:hover{{background:var(--acs);color:var(--ac)}}
.label-btn svg{{width:12px;height:12px;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}}
.label-picker{{
  position:absolute;z-index:50;top:100%;left:0;min-width:200px;max-width:260px;max-height:280px;overflow-y:auto;
  background:var(--bg2);border:1px solid var(--bd);border-radius:var(--r);box-shadow:var(--sh2);padding:6px
}}
.label-picker-item{{
  display:flex;align-items:center;gap:8px;padding:6px 8px;border-radius:6px;cursor:pointer;font-size:12px;transition:background .1s
}}
.label-picker-item:hover{{background:var(--acs)}}
.label-dot{{width:10px;height:10px;border-radius:50%;flex-shrink:0}}
.label-check{{width:14px;height:14px;flex-shrink:0;accent-color:var(--ac)}}

/* ─── Feature 4: Batch Operations ────────────────────── */
.card-check-wrap{{display:none;position:absolute;top:10px;left:-4px;z-index:5}}
.select-mode .card-check-wrap{{display:block}}
.card-check{{width:16px;height:16px;accent-color:var(--ac);cursor:pointer}}
.card{{position:relative}}
.card.selected{{border-color:var(--ac);background:var(--acs)}}
.batch-bar{{
  position:fixed;bottom:24px;left:50%;transform:translateX(-50%);z-index:100;
  display:flex;align-items:center;gap:12px;padding:10px 20px;
  background:var(--bg2);border:1px solid var(--bd);border-radius:var(--r);
  box-shadow:var(--sh2);backdrop-filter:blur(12px)
}}
.batch-bar .select{{font-size:12px;padding:6px 10px}}
.batch-count{{font-size:13px;font-weight:600;color:var(--ac)}}
.batch-apply{{
  padding:6px 14px;border-radius:6px;border:none;font-size:12px;font-weight:600;
  background:var(--ac);color:#fff;cursor:pointer;transition:opacity .15s
}}
.batch-apply:hover{{opacity:.85}}
.batch-deselect{{
  padding:6px 14px;border-radius:6px;border:1px solid var(--bd);font-size:12px;font-weight:500;
  background:transparent;color:var(--tx2);cursor:pointer
}}
.batch-deselect:hover{{color:var(--tx);border-color:var(--bd2)}}

/* ─── Feature 5: Archive View ────────────────────────── */
.view-tabs{{display:flex;gap:2px;background:var(--input-bg);border-radius:8px;padding:2px}}
.view-tab{{
  padding:6px 16px;border-radius:6px;border:none;font-size:13px;font-weight:500;
  background:transparent;color:var(--tx2);cursor:pointer;transition:all .15s
}}
.view-tab.active{{background:var(--bg2);color:var(--tx);box-shadow:var(--sh)}}
.archive-view{{display:none}}
.archive-view.active{{display:block}}
.archive-filters{{display:flex;gap:10px;align-items:center;margin-bottom:16px;flex-wrap:wrap}}
.archive-filters .select{{font-size:12px;padding:6px 10px}}
.archive-table{{width:100%;border-collapse:collapse;font-size:12px;background:var(--bg2);border:1px solid var(--bd);border-radius:var(--r);overflow:hidden}}
.archive-table th{{text-align:left;padding:10px 14px;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.04em;color:var(--tx3);border-bottom:1px solid var(--bd);cursor:pointer;user-select:none;background:var(--bg3)}}
.archive-table th:hover{{color:var(--tx)}}
.archive-table th .arrow{{margin-left:4px;font-size:9px;opacity:.5}}
.archive-table th.sorted .arrow{{opacity:1;color:var(--ac)}}
.archive-table td{{padding:8px 14px;border-bottom:1px solid var(--bd);color:var(--tx2)}}
.archive-table .cn{{font-weight:500;color:var(--tx)!important;max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.archive-actions{{display:flex;gap:6px}}
.reopen-btn{{
  padding:3px 10px;border-radius:6px;font-size:11px;font-weight:500;
  background:var(--acs);color:var(--ac);border:none;cursor:pointer;transition:all .15s
}}
.reopen-btn:hover{{background:var(--ac);color:#fff}}
.archive-page{{display:flex;align-items:center;justify-content:center;gap:12px;padding:16px;font-size:13px;color:var(--tx2)}}
.archive-page button{{
  padding:6px 14px;border-radius:6px;border:1px solid var(--bd);font-size:12px;
  background:var(--input-bg);color:var(--tx);cursor:pointer
}}
.archive-page button:disabled{{opacity:.3;cursor:default}}

/* ─── v2.3: Workspace Health ─────────────────────────── */
.health-view{{display:none}}
.health-view.active{{display:block}}
.health-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px}}
.health-card{{
  background:var(--bg2);border:1px solid var(--bd);border-radius:var(--r);
  padding:16px;box-shadow:var(--sh);cursor:pointer;transition:all .15s;
  display:flex;flex-direction:column;gap:10px
}}
.health-card:hover{{box-shadow:var(--sh2);border-color:var(--bd2)}}
.h-head{{display:flex;align-items:center;gap:10px}}
.h-swatch{{width:14px;height:14px;border-radius:4px;flex-shrink:0}}
.h-name{{font-size:14px;font-weight:600;flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.h-badge{{
  font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.04em;
  padding:2px 8px;border-radius:10px
}}
.h-badge.healthy{{background:var(--oks);color:var(--ok)}}
.h-badge.attention{{background:var(--wns);color:var(--wn)}}
.h-badge.overloaded{{background:var(--ers);color:var(--er)}}
.h-stats{{display:grid;grid-template-columns:repeat(3,1fr);gap:6px}}
.h-stat{{display:flex;flex-direction:column;gap:1px}}
.h-stat-v{{font-size:18px;font-weight:700;letter-spacing:-.02em}}
.h-stat-l{{font-size:10px;font-weight:500;text-transform:uppercase;letter-spacing:.04em;color:var(--tx3)}}
.h-stat.warn .h-stat-v{{color:var(--wn)}}
.h-stat.danger .h-stat-v{{color:var(--er)}}
.h-trend{{display:flex;flex-direction:column;gap:4px}}
.h-trend-row{{display:flex;align-items:center;gap:8px;font-size:11px;color:var(--tx3)}}
.h-trend-l{{width:36px;font-weight:500}}
.h-trend-svg{{flex:1;height:24px;display:block}}
.h-trend-svg .h-line{{fill:none;stroke:var(--ac);stroke-width:1.5;stroke-linejoin:round;stroke-linecap:round}}
.h-trend-svg .h-line-cost{{stroke:var(--wn)}}
.h-trend-svg .h-dot{{fill:var(--ac)}}
.h-trend-svg .h-dot-cost{{fill:var(--wn)}}
.h-trend-svg .h-axis{{stroke:var(--bd);stroke-width:1;opacity:.5}}
.h-extra{{font-size:11px;color:var(--tx3);display:flex;justify-content:space-between;border-top:1px solid var(--bd);padding-top:8px;margin-top:auto}}
.h-extra strong{{color:var(--tx2);font-weight:600}}
.h-empty{{text-align:center;padding:48px 24px;color:var(--tx3);font-size:13px}}

/* ─── v2.4: Queue ────────────────────────────────────── */
.queue-view{{display:none}}
.queue-view.active{{display:block}}
.queue-board{{display:flex;gap:16px;overflow-x:auto;padding-bottom:16px;align-items:flex-start}}
.q-col{{flex:0 0 300px;width:300px}}
.q-col-head{{
  display:flex;justify-content:space-between;align-items:center;
  padding:8px 12px;margin-bottom:8px;border-radius:var(--rs);
  background:var(--acs)
}}
.q-col-head .col-title{{font-size:13px;font-weight:600}}
.q-col-head .col-count{{
  font-size:12px;font-weight:600;color:var(--ac);
  background:var(--bg);padding:1px 8px;border-radius:10px
}}
.q-col-hint{{font-size:11px;color:var(--tx3);padding:0 12px 6px;font-style:italic}}
.q-col-cards{{display:flex;flex-direction:column;gap:8px;min-height:60px}}
.q-card-wrap{{position:relative}}
.q-lane-hint{{
  position:absolute;top:8px;right:10px;z-index:2;
  font-size:9px;font-weight:600;text-transform:uppercase;letter-spacing:.04em;
  padding:2px 6px;border-radius:10px;background:var(--acs);color:var(--ac);pointer-events:none
}}
.q-empty{{text-align:center;padding:24px 12px;color:var(--tx3);font-size:12px;font-style:italic}}

/* ─── v3.0: Saved Lenses ─────────────────────────────── */
.lens-wrap{{position:relative}}
.lens-btn{{
  padding:7px 14px;border-radius:8px;border:1px solid var(--bd);
  background:var(--input-bg);color:var(--tx);font-size:13px;font-weight:500;
  cursor:pointer;outline:none;display:inline-flex;align-items:center;gap:6px;transition:all .15s
}}
.lens-btn:hover{{border-color:var(--ac);color:var(--ac)}}
.lens-btn.active{{background:var(--acs);border-color:var(--ac);color:var(--ac)}}
.lens-btn svg{{width:14px;height:14px;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}}
.lens-menu{{
  position:absolute;z-index:60;top:100%;left:0;margin-top:4px;min-width:260px;max-width:320px;
  background:var(--bg2);border:1px solid var(--bd);border-radius:var(--r);box-shadow:var(--sh2);padding:4px
}}
.lens-section{{
  font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.04em;
  color:var(--tx3);padding:8px 10px 4px
}}
.lens-item{{
  display:flex;align-items:center;gap:6px;padding:7px 10px;border-radius:6px;cursor:pointer;
  font-size:13px;transition:background .1s
}}
.lens-item:hover{{background:var(--acs)}}
.lens-item.current{{color:var(--ac);font-weight:600}}
.lens-item-name{{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.lens-del{{
  width:18px;height:18px;border-radius:4px;border:none;background:transparent;color:var(--tx3);
  cursor:pointer;display:none;align-items:center;justify-content:center;font-size:14px;line-height:1
}}
.lens-item:hover .lens-del{{display:inline-flex}}
.lens-del:hover{{background:var(--ers);color:var(--er)}}
.lens-divider{{height:1px;background:var(--bd);margin:4px 0}}
.lens-action{{
  display:flex;align-items:center;gap:6px;padding:7px 10px;border-radius:6px;cursor:pointer;
  font-size:13px;color:var(--tx2);font-weight:500
}}
.lens-action:hover{{background:var(--acs);color:var(--ac)}}
.lens-empty{{padding:7px 10px;font-size:12px;color:var(--tx3);font-style:italic}}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="htop">
      <div class="htop-left">
        <h1>Mission Control</h1>
        <span id="mode-badge" class="mode-badge"></span>
        <span class="ts" title="Updated {ts}">v{__version__}</span>
      </div>
    </div>
    <div class="stats" id="stats"></div>
  </header>

  <div class="toolbar">
    <select class="select ws-selector" id="ws-select">
      <option value="all">All Workspaces</option>
    </select>

    <div class="lens-wrap">
      <button class="lens-btn" id="lens-btn" title="Apply a saved lens or filter">
        <svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
        <span id="lens-btn-label">Lens</span>
      </button>
    </div>

    <div class="view-tabs" id="view-tabs">
      <button class="view-tab active" data-view="board">Board</button>
      <button class="view-tab" data-view="queue">Queue</button>
      <button class="view-tab" data-view="health">Health</button>
      <button class="view-tab" data-view="archive">Archive</button>
    </div>
    <div class="toolbar-right">
      <div class="search-wrap">
        <span class="search-icon">&#x1F50D;</span>
        <input type="text" class="search-box" id="search" placeholder="Search sessions\u2026" autocomplete="off" spellcheck="false">
      </div>
      <select class="select" id="sort">
        <option value="activity">Sort: Last Activity</option>
        <option value="name">Sort: Name (A\u2013Z)</option>
        <option value="cost">Sort: Cost (High\u2013Low)</option>
        <option value="messages">Sort: Messages</option>
        <option value="staleness">Sort: Most Stale</option>
      </select>
      <button class="toolbar-btn" id="select-toggle" title="Select multiple sessions">
        <svg viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M9 12l2 2 4-4"/></svg>
      </button>
      <button class="toolbar-btn" id="export-btn" title="Export visible sessions as CSV">
        <svg viewBox="0 0 24 24"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
        CSV
      </button>
    </div>
  </div>

  <div class="filters" id="filters"></div>

  <!-- Board view (default) -->
  <div id="board-view">
    <div class="board" id="board"></div>
    <div class="closed-section" id="closed-section">
      <button class="closed-toggle" id="closed-toggle">
        <span class="closed-arrow">&#9654;</span>
        <span id="closed-label">Closed Sessions</span>
      </button>
      <div class="closed-body">
        <table class="ct">
          <thead><tr>
            <th data-key="name">Session <span class="arrow">&uarr;</span></th>
            <th data-key="ws">Workspace <span class="arrow">&uarr;</span></th>
            <th data-key="status">Status <span class="arrow">&uarr;</span></th>
            <th data-key="activity">Last Activity <span class="arrow">&uarr;</span></th>
            <th data-key="cost">Cost <span class="arrow">&uarr;</span></th>
          </tr></thead>
          <tbody id="closed-body"></tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- Queue view (v2.4) -->
  <div id="queue-view" class="queue-view">
    <div class="queue-board" id="queue-board"></div>
  </div>

  <!-- Health view (v2.3) -->
  <div id="health-view" class="health-view">
    <div class="health-grid" id="health-grid"></div>
  </div>

  <!-- Archive view -->
  <div id="archive-view" class="archive-view">
    <div class="archive-filters">
      <select class="select" id="archive-status"><option value="all">All Closed</option></select>
      <select class="select" id="archive-ws"><option value="all">All Workspaces</option></select>
      <select class="select" id="archive-date">
        <option value="all">All Time</option>
        <option value="7">Last 7 Days</option>
        <option value="30">Last 30 Days</option>
        <option value="90">Last 90 Days</option>
      </select>
      <div class="search-wrap" style="width:200px;min-width:140px">
        <span class="search-icon">&#x1F50D;</span>
        <input type="text" class="search-box" id="archive-search" placeholder="Search archive\u2026" autocomplete="off">
      </div>
    </div>
    <table class="archive-table" id="archive-table">
      <thead><tr>
        <th data-key="name">Session <span class="arrow">&uarr;</span></th>
        <th data-key="ws">Workspace <span class="arrow">&uarr;</span></th>
        <th data-key="status">Status <span class="arrow">&uarr;</span></th>
        <th data-key="activity">Last Activity <span class="arrow">&uarr;</span></th>
        <th data-key="created">Created <span class="arrow">&uarr;</span></th>
        <th data-key="cost">Cost <span class="arrow">&uarr;</span></th>
        <th data-key="msgs">Msgs <span class="arrow">&uarr;</span></th>
        <th>Actions</th>
      </tr></thead>
      <tbody id="archive-body"></tbody>
    </table>
    <div class="archive-page" id="archive-page"></div>
  </div>

  <!-- Batch action bar -->
  <div class="batch-bar" id="batch-bar" style="display:none">
    <span class="batch-count" id="batch-count">0 selected</span>
    <select class="select" id="batch-status-select"></select>
    <button class="batch-apply" id="batch-apply-status">Move</button>
    <button class="batch-deselect" id="batch-deselect">Deselect All</button>
  </div>
</div>
<div class="toast-container" id="toasts"></div>

<script>
const DATA = {data_json};
const API = "{api_base}";
const NOW = Date.now();
const HEALTH_ATTENTION_STALE = {HEALTH_ATTENTION_STALE};
const HEALTH_ATTENTION_REVIEW_DAYS = {HEALTH_ATTENTION_REVIEW_DAYS};
const HEALTH_OVERLOADED_OPEN = {HEALTH_OVERLOADED_OPEN};

// ─── State ───────────────────────────────────────────
const state = {{
  selectedWs: 'all',
  activeWs: new Set(DATA.workspaces.map(w => w.id)),
  search: '',
  sort: 'activity',
  expanded: null,
  closedSort: {{ key: 'activity', asc: false }},
  dragging: null,
  // Feature 4: Batch
  selected: new Set(),
  selectMode: false,
  // Feature 5: Archive
  view: 'board',
  archiveSort: {{ key: 'activity', asc: false }},
  archivePage: 0,
  archivePageSize: 25,
  // Feature 3: Label picker
  labelPicker: null,
  // v3.0: Lenses
  lens: null,                  // active lens id (stock or user)
  lensKind: null,              // 'stock' or 'user'
  userLenses: [],              // loaded from /api/lenses
  lensMenu: null,              // open menu element
}};

// ─── Helpers ─────────────────────────────────────────
function relTime(ms) {{
  if (!ms) return 'never';
  const d = (NOW - ms) / 1000;
  if (d < 60) return 'just now';
  if (d < 3600) return Math.floor(d/60) + 'm ago';
  if (d < 86400) return Math.floor(d/3600) + 'h ago';
  const days = Math.floor(d/86400);
  return days + ' day' + (days !== 1 ? 's' : '') + ' ago';
}}

function fmtDate(ms) {{
  if (!ms) return '?';
  return new Date(ms).toLocaleDateString('en-GB',{{day:'numeric',month:'short',year:'numeric'}});
}}

function stale(ms) {{
  if (!ms) return 'red';
  const days = (NOW - ms) / 86400000;
  return days < 3 ? 'green' : days < 7 ? 'amber' : 'red';
}}

function fmtCost(c) {{ return !c ? '' : c < 0.01 ? '<$0.01' : '$' + c.toFixed(2); }}

function esc(s) {{
  const d = document.createElement('div');
  d.textContent = s || '';
  return d.innerHTML;
}}

function isDark() {{ return matchMedia('(prefers-color-scheme:dark)').matches; }}

function isManageMode() {{ return state.selectedWs !== 'all'; }}

function getWsConfig(wsId) {{ return DATA.workspaces.find(w => w.id === wsId); }}

function deriveLabels(rawLabels, wsConfig) {{
  const defs = Object.fromEntries(((wsConfig && wsConfig.labelDefs) || []).map(l => [l.id, l]));
  return (rawLabels || []).flatMap(lb => {{
    const idx = lb.indexOf('::');
    const id = idx === -1 ? lb : lb.slice(0, idx);
    const override = idx === -1 ? '' : lb.slice(idx + 2);
    if (id === 'url') return [];
    const def = defs[id] || {{}};
    return [{{
      d: (override || def.name || id).slice(0, 20),
      cl: def.cl || '#888',
      cd: def.cd || '#aaa',
    }}];
  }});
}}

function cardKey(s) {{ return s.wsId + ':' + s.id; }}

function getStatusColumns() {{
  if (isManageMode()) {{
    const ws = getWsConfig(state.selectedWs);
    return ws ? ws.statuses : [];
  }}
  const closedIds = new Set(getClosedStatuses().map(s => s.id));
  // 'todo' is the default status for new sessions; always show it even when
  // no sessions are in that column yet.
  const ALWAYS_SHOW = new Set(['todo']);
  const statusMap = {{}};
  for (const ws of DATA.workspaces) {{
    for (const s of ws.statuses) {{
      if (s.category === 'open' && !statusMap[s.id]) statusMap[s.id] = s;
    }}
  }}
  const visibleSessions = DATA.sessions.filter(s => !closedIds.has(s.status) && matchesFilters(s));
  const usedStatuses = new Set(visibleSessions.map(s => s.status));
  const cols = [];
  const seen = new Set();
  for (const [id, def] of Object.entries(statusMap)) {{
    if (ALWAYS_SHOW.has(id) || usedStatuses.has(id)) {{
      cols.push(def);
      seen.add(id);
    }}
  }}
  for (const id of usedStatuses) {{
    if (!seen.has(id)) {{
      cols.push({{ id, label: id.replace(/-/g,' ').replace(/\\b\\w/g,c=>c.toUpperCase()), category:'open', order: 99 }});
    }}
  }}
  return cols.sort((a, b) => (a.order||0) - (b.order||0));
}}

function getClosedStatuses() {{
  const seen = new Set();
  const result = [];
  for (const ws of DATA.workspaces) {{
    for (const s of ws.statuses) {{
      if (!seen.has(s.id) && s.category === 'closed') {{
        seen.add(s.id);
        result.push(s);
      }}
    }}
  }}
  return result;
}}

function getAllStatuses() {{
  const seen = new Set();
  const result = [];
  for (const ws of DATA.workspaces) {{
    for (const s of ws.statuses) {{
      if (!seen.has(s.id)) {{ seen.add(s.id); result.push(s); }}
    }}
  }}
  return result.sort((a,b) => (a.order||0) - (b.order||0));
}}

// v3.0: Stock lens predicates. User lenses restore filter state instead of
// adding a predicate, so they don't appear here.
function stockLensMatches(lensId, s, closedIds) {{
  const last = s.lastUsedAt || 0;
  const ageMs = NOW - last;
  if (lensId === 'stale-7d') return last > 0 && !closedIds.has(s.status) && ageMs >= 7 * 86400000;
  if (lensId === 'no-labels') return !((s.rawLabels||[]).length);
  if (lensId === 'cost-idle') return (s.cost||0) > 5 && last > 0 && ageMs > 86400000;
  if (lensId === 'needs-review') return s.status === 'needs-review';
  if (lensId === 'active-24h') return last > 0 && ageMs < 86400000;
  if (lensId === 'abandoned') return (s.msgs||0) <= 1 && last > 0 && ageMs > 86400000;
  return true;
}}

function matchesFilters(s) {{
  if (isManageMode()) {{
    if (s.wsId !== state.selectedWs) return false;
  }} else {{
    if (!state.activeWs.has(s.wsId)) return false;
  }}
  if (state.lens && state.lensKind === 'stock') {{
    const closedIds = new Set(getClosedStatuses().map(x => x.id));
    if (!stockLensMatches(state.lens, s, closedIds)) return false;
  }}
  if (state.search) {{
    const q = state.search.toLowerCase();
    return (s.name||'').toLowerCase().includes(q) || (s.preview||'').toLowerCase().includes(q)
      || (s.wsName||'').toLowerCase().includes(q) || (s.id||'').toLowerCase().includes(q)
      || (s.labels||[]).some(l => l.d.toLowerCase().includes(q));
  }}
  return true;
}}

function sortFn(key) {{
  return {{
    activity: (a,b) => (b.lastUsedAt||0) - (a.lastUsedAt||0),
    name: (a,b) => (a.name||'').localeCompare(b.name||''),
    cost: (a,b) => (b.cost||0) - (a.cost||0),
    messages: (a,b) => (b.msgs||0) - (a.msgs||0),
    staleness: (a,b) => (a.lastUsedAt||0) - (b.lastUsedAt||0),
  }}[key] || ((a,b) => (b.lastUsedAt||0) - (a.lastUsedAt||0));
}}

// ─── Toast ───────────────────────────────────────────
function toast(msg, type='success') {{
  const el = document.createElement('div');
  el.className = 'toast ' + type;
  el.textContent = msg;
  document.getElementById('toasts').appendChild(el);
  requestAnimationFrame(() => el.classList.add('show'));
  setTimeout(() => {{ el.classList.remove('show'); setTimeout(() => el.remove(), 300); }}, 3000);
}}

// ─── API ─────────────────────────────────────────────
async function updateStatus(sessionId, wsId, newStatus) {{
  if (!API) {{
    toast('Server mode required for status changes', 'error');
    return false;
  }}
  try {{
    const res = await fetch(API + '/api/status', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ sessionId, wsDir: wsId, newStatus }}),
    }});
    const data = await res.json();
    if (data.ok) {{
      const sess = DATA.sessions.find(s => s.id === sessionId && s.wsId === wsId);
      if (sess) sess.status = newStatus;
      toast('Moved to ' + newStatus.replace(/-/g, ' '));
      renderAll();
      return true;
    }} else {{
      toast(data.error || 'Update failed', 'error');
      return false;
    }}
  }} catch (e) {{
    toast('Connection error: ' + e.message, 'error');
    return false;
  }}
}}

async function updateLabels(sessionId, wsId, labels) {{
  if (!API) {{ toast('Server mode required', 'error'); return false; }}
  try {{
    const res = await fetch(API + '/api/labels', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ sessionId, wsDir: wsId, labels }}),
    }});
    const data = await res.json();
    if (data.ok) {{
      const sess = DATA.sessions.find(s => s.id === sessionId && s.wsId === wsId);
      if (sess) {{
        sess.rawLabels = data.labels;
        sess.labels = deriveLabels(sess.rawLabels, getWsConfig(sess.wsId));
      }}
      toast('Labels updated');
      return true;
    }} else {{
      toast(data.error || 'Label update failed', 'error');
      return false;
    }}
  }} catch (e) {{
    toast('Connection error: ' + e.message, 'error');
    return false;
  }}
}}

async function openSession(btn) {{
  const sid = btn.dataset.sid;
  const sdkSid = btn.dataset.sdksid;
  const wsUuid = btn.dataset.wsuuid;
  const sess = DATA.sessions.find(s => s.id === sid);
  const sessionName = sess ? sess.name : sid;
  try {{
    const res = await fetch(`${{API}}/api/open`, {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{ sessionId: sid, sdkSessionId: sdkSid, wsUuid: wsUuid }}),
    }});
    const data = await res.json();
    if (data.ok) {{
      toast('Opening session in Craft Agents\u2026');
    }} else {{
      await navigator.clipboard.writeText(sessionName);
      toast('Could not open directly \u2014 copied \u201c' + sessionName + '\u201d (press \u2318K to search)');
    }}
  }} catch (e) {{
    await navigator.clipboard.writeText(sessionName);
    toast('Copied \u201c' + sessionName + '\u201d \u2014 press \u2318K in Craft Agents to search');
  }}
}}

// Feature 2: New session via deeplink
async function newSession(wsId) {{
  const ws = getWsConfig(wsId || state.selectedWs);
  if (!ws) return;
  const url = ws.wsUuid
    ? 'craftagents://workspace/' + ws.wsUuid + '/action/new-chat?window=focused'
    : 'craftagents://action/new-chat?window=focused';
  if (API) {{
    try {{
      const res = await fetch(API + '/api/open-url', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ url }}),
      }});
      const data = await res.json();
      if (data.ok) toast('Opening new session\u2026');
      else toast('Could not open Craft Agents', 'error');
    }} catch (e) {{
      toast('Connection error', 'error');
    }}
  }} else {{
    window.location.href = url;
  }}
}}

// Feature 1: CSV Export
function exportCSV() {{
  const sessions = DATA.sessions.filter(matchesFilters).sort(sortFn(state.sort));
  if (!sessions.length) {{ toast('No sessions to export', 'error'); return; }}
  const cols = ['Name','Session ID','Workspace','Status','Last Activity','Created','Cost','Messages','Model','Labels','Tokens'];
  const rows = sessions.map(s => [
    '"' + (s.name||'').replace(/"/g,'""') + '"',
    s.id,
    '"' + (s.wsName||'').replace(/"/g,'""') + '"',
    s.status,
    s.lastUsedAt ? new Date(s.lastUsedAt).toISOString() : '',
    s.createdAt ? new Date(s.createdAt).toISOString() : '',
    (s.cost||0).toFixed(4),
    s.msgs||0,
    s.model||'',
    '"' + (s.rawLabels||[]).join(';').replace(/"/g,'""') + '"',
    s.tokens||0,
  ]);
  const csv = [cols.join(','), ...rows.map(r => r.join(','))].join('\\n');
  const blob = new Blob([csv], {{type: 'text/csv;charset=utf-8;'}});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'mission-control-' + new Date().toISOString().slice(0,10) + '.csv';
  a.click();
  URL.revokeObjectURL(a.href);
  toast('Exported ' + sessions.length + ' sessions');
}}

// Feature 4: Batch operations
async function batchUpdateStatus(newStatus) {{
  if (!API || !state.selected.size) return;
  const items = [...state.selected].map(k => {{ const [ws, id] = k.split(':'); return {{ sessionId: id, wsDir: ws }}; }});
  try {{
    const res = await fetch(API + '/api/batch/status', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ items, newStatus }}),
    }});
    const data = await res.json();
    if (data.ok) {{
      items.forEach(i => {{
        const sess = DATA.sessions.find(s => s.id === i.sessionId && s.wsId === i.wsDir);
        if (sess) sess.status = newStatus;
      }});
      toast(`Moved ${{data.updated}} session${{data.updated!==1?'s':''}} to ${{newStatus.replace(/-/g,' ')}}`);
      state.selected.clear();
      state.selectMode = false;
      document.querySelector('.wrap').classList.remove('select-mode');
      renderAll();
      renderBatchBar();
    }} else {{
      toast(data.error || 'Batch update failed', 'error');
    }}
  }} catch (e) {{
    toast('Connection error: ' + e.message, 'error');
  }}
}}

// ─── Renderers ───────────────────────────────────────
function renderStats() {{
  const closedIds = new Set(getClosedStatuses().map(s => s.id));
  const visible = DATA.sessions.filter(matchesFilters);
  const open = visible.filter(s => !closedIds.has(s.status));
  const active = visible.filter(s => s.lastUsedAt && (NOW - s.lastUsedAt) < 86400000).length;
  const staleN = open.filter(s => stale(s.lastUsedAt) === 'red').length;
  const cost = visible.reduce((a, s) => a + (s.cost||0), 0);
  document.getElementById('stats').innerHTML = `
    <div class="st"><span class="sv">${{visible.length}}</span><span class="sl">Visible</span></div>
    <div class="st"><span class="sv">${{open.length}}</span><span class="sl">Open</span></div>
    <div class="st"><span class="sv">${{active}}</span><span class="sl">Active 24h</span></div>
    <div class="st st-w"><span class="sv">${{staleN}}</span><span class="sl">Stale (7d+)</span></div>
    <div class="st"><span class="sv">$${{cost.toFixed(2)}}</span><span class="sl">Total Cost</span></div>
    <div class="st"><span class="sv">${{isManageMode() ? 1 : state.activeWs.size}}</span><span class="sl">Workspaces</span></div>
  `;
}}

function renderModeBadge() {{
  const el = document.getElementById('mode-badge');
  if (isManageMode()) {{
    const ws = getWsConfig(state.selectedWs);
    el.className = 'mode-badge mode-manage';
    el.textContent = 'Manage: ' + (ws ? ws.name : state.selectedWs);
  }} else {{
    el.className = 'mode-badge mode-view';
    el.textContent = 'Overview';
  }}
}}

function renderFilters() {{
  const el = document.getElementById('filters');
  if (isManageMode()) {{
    el.innerHTML = '';
    el.style.display = 'none';
    return;
  }}
  el.style.display = 'flex';
  const dark = isDark();
  el.innerHTML = DATA.workspaces.map(ws => {{
    const active = state.activeWs.has(ws.id);
    const c = dark ? ws.cd : ws.cl;
    const st = active ? `background:${{c}};color:#fff;border-color:${{c}}` : `background:transparent;color:${{c}};border-color:${{c}}`;
    return `<span class="fpill ${{active?'active':'inactive'}}" data-ws="${{esc(ws.id)}}" style="${{st}}">${{esc(ws.name)}} <span class="pc">${{ws.count}}</span></span>`;
  }}).join('') + DATA.workspaces.map(ws =>
    `<button class="new-btn" data-newws="${{esc(ws.id)}}" title="New session in ${{esc(ws.name)}}">+</button>`
  ).join('');
}}

// Card registry for FLIP reconciliation: session.id -> HTMLElement
const __mcCards = new Map();
// Track which card elements already have drag listeners (WeakSet so detached
// cards get GCed).
const __mcDragAttached = new WeakSet();

function buildCardInnerHTML(s) {{
  const dark = isDark();
  const wsBg = dark ? s.wsCd : s.wsCl;
  const expanded = state.expanded === s.id;
  const isSelected = state.selected.has(cardKey(s));
  const labels = (s.labels||[]).slice(0, expanded ? 20 : 4).map(l => {{
    const c = dark ? l.cd : l.cl;
    return `<span class="label-pill" style="border-color:${{c}};color:${{c}}">${{esc(l.d)}}</span>`;
  }}).join('');

  const details = expanded ? `
    <div class="card-details">
      <div class="detail-row"><span class="detail-label">Session ID</span><span>${{esc(s.id)}}</span></div>
      <div class="detail-row"><span class="detail-label">Created</span><span>${{fmtDate(s.createdAt)}}</span></div>
      <div class="detail-row"><span class="detail-label">Tokens</span><span>${{(s.tokens||0).toLocaleString()}}</span></div>
      <div class="detail-row"><span class="detail-label">Cost</span><span>${{fmtCost(s.cost)||'$0.00'}}</span></div>
      <div class="detail-row"><span class="detail-label">Messages</span><span>${{s.msgs}}</span></div>
      <div class="detail-row"><span class="detail-label">Model</span><span>${{s.model||'?'}}</span></div>
    </div>` : '';

  return `<div class="card-check-wrap"><input type="checkbox" class="card-check" ${{isSelected?'checked':''}}></div>
  <div class="card-top">
    <span class="dot s-${{stale(s.lastUsedAt)}}"></span>
    ${{s.flagged?'<span class="flag">&#9733;</span>':''}}
    <span class="card-name">${{esc(s.name)}}</span>
    <span class="card-cost">${{fmtCost(s.cost)}}</span>
  </div>
  <div class="card-preview">${{esc(s.preview)}}</div>
  <div class="badges">
    ${{!isManageMode() ? `<span class="ws-badge" style="background:${{wsBg}}">${{esc(s.wsName)}}</span>` : ''}}
    ${{s.model?`<span class="model-badge">${{s.model}}</span>`:''}}
    ${{labels}}
  </div>
  <div class="card-meta">
    <span>${{relTime(s.lastUsedAt)}} &middot; ${{s.msgs}} msgs</span>
    <span style="display:flex;gap:4px;align-items:center">
      ${{API ? `<button class="label-btn" data-sid="${{esc(s.id)}}" data-ws="${{esc(s.wsId)}}" title="Manage labels">
        <svg viewBox="0 0 24 24"><path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/><line x1="7" y1="7" x2="7.01" y2="7"/></svg>
      </button>` : ''}}
      <button class="open-btn" data-sid="${{esc(s.id)}}" data-sdksid="${{esc(s.sdkSid)}}" data-wsuuid="${{esc(s.wsUuid)}}" title="Open in Craft Agents">
        <svg viewBox="0 0 24 24"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
        Open
      </button>
    </span>
  </div>
  ${{details}}`;
}}

function applyCardAttrs(el, s) {{
  const expanded = state.expanded === s.id;
  const draggable = isManageMode() && !!API && !state.selectMode;
  const isSelected = state.selected.has(cardKey(s));
  el.className = 'card' +
    (expanded ? ' expanded' : '') +
    (draggable ? ' draggable' : '') +
    (isSelected ? ' selected' : '');
  el.dataset.id = s.id;
  el.dataset.ws = s.wsId;
  if (draggable) el.setAttribute('draggable', 'true');
  else el.removeAttribute('draggable');
}}

function getOrCreateCardEl(s) {{
  let el = __mcCards.get(s.id);
  const isNew = !el;
  if (isNew) {{
    el = document.createElement('div');
    __mcCards.set(s.id, el);
  }}
  applyCardAttrs(el, s);
  el.innerHTML = buildCardInnerHTML(s);
  return {{ el, isNew }};
}}

function renderBoard() {{
  const manage = isManageMode();
  const closedIds = new Set(getClosedStatuses().map(s => s.id));
  const boardSessions = manage
    ? DATA.sessions.filter(matchesFilters)
    : DATA.sessions.filter(s => !closedIds.has(s.status) && matchesFilters(s));
  const statusCols = manage
    ? getStatusColumns()
    : getStatusColumns().filter(s => s.category === 'open');
  const boardEl = document.getElementById('board');

  if (boardSessions.length === 0 && state.search) {{
    __mcCards.forEach(el => {{ if (el.isConnected) el.remove(); }});
    __mcCards.clear();
    boardEl.innerHTML = `<div class="no-results"><div class="no-results-icon">&#128269;</div><div class="no-results-text">No sessions match "${{esc(state.search)}}"</div></div>`;
    boardEl.classList.remove('drag-enabled');
    return;
  }}

  boardEl.classList.toggle('drag-enabled', manage && !!API && !state.selectMode);

  // F (First): snapshot positions of currently mounted cards before any mutation.
  const first = new Map();
  const prevStatus = new Map();
  __mcCards.forEach((el, id) => {{
    if (el.isConnected) {{
      first.set(id, el.getBoundingClientRect());
      prevStatus.set(id, el.dataset.status || '');
    }}
  }});

  // Build column shells (empty); cards will be appended next so they retain identity.
  const visibleIds = new Set(boardSessions.map(s => s.id));
  const countsByCol = {{}};
  for (const col of statusCols) countsByCol[col.id] = 0;
  for (const s of boardSessions) if (countsByCol[s.status] !== undefined) countsByCol[s.status]++;

  boardEl.innerHTML = statusCols.map(col => {{
    const isClosed = col.category === 'closed';
    return `<div class="col ${{isClosed ? 'col-closed' : ''}}" data-status="${{esc(col.id)}}">
      <div class="col-head ${{isClosed ? 'col-head-closed' : ''}}">
        <span class="col-title">${{esc(col.label)}}</span>
        <span class="col-count">${{countsByCol[col.id]||0}}</span>
        ${{manage && !isClosed ? `<button class="new-btn col-new-btn" data-newws="${{esc(state.selectedWs)}}" title="New session">+</button>` : ''}}
      </div>
      <div class="col-cards" data-status="${{esc(col.id)}}"></div>
    </div>`;
  }}).join('');

  // Populate columns with reused/new card nodes.
  const entering = new Set();
  statusCols.forEach(col => {{
    const zone = boardEl.querySelector(`.col-cards[data-status="${{CSS.escape(col.id)}}"]`);
    if (!zone) return;
    const sessions = boardSessions.filter(s => s.status === col.id).sort(sortFn(state.sort));
    if (sessions.length === 0) {{
      zone.innerHTML = '<div class="empty">No sessions</div>';
      return;
    }}
    sessions.forEach(s => {{
      const {{ el, isNew }} = getOrCreateCardEl(s);
      if (isNew) entering.add(el);
      el.dataset.status = col.id;
      zone.appendChild(el);
    }});
  }});

  // Drop cards no longer visible.
  __mcCards.forEach((el, id) => {{
    if (!visibleIds.has(id)) {{
      if (el.isConnected) el.remove();
      __mcCards.delete(id);
    }}
  }});

  // FLIP play. Skip when user prefers reduced motion.
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (!reduced) {{
    const tasks = [];
    __mcCards.forEach((el, id) => {{
      if (entering.has(el)) {{
        el.classList.add('flip-enter');
        tasks.push({{ el, kind: 'enter', moved: false }});
        return;
      }}
      const prev = first.get(id);
      if (!prev) return;
      const now = el.getBoundingClientRect();
      const dx = prev.left - now.left;
      const dy = prev.top - now.top;
      if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5) return;
      el.style.transition = 'none';
      el.style.transform = `translate(${{dx}}px, ${{dy}}px)`;
      const movedCol = (prevStatus.get(id) || '') !== (el.dataset.status || '');
      tasks.push({{ el, kind: 'move', moved: movedCol }});
    }});

    if (tasks.length) {{
      // Force styles to apply, then play on next frame with a gentle stagger.
      void boardEl.offsetHeight;
      requestAnimationFrame(() => {{
        tasks.forEach((t, i) => {{
          const delay = Math.min(i * 30, 180);
          t.el.style.transition = `transform 420ms cubic-bezier(.2,.8,.2,1) ${{delay}}ms, opacity 260ms ease ${{delay}}ms`;
          if (t.kind === 'enter') {{
            t.el.classList.remove('flip-enter');
          }} else {{
            t.el.style.transform = '';
            if (t.moved) {{
              t.el.classList.add('just-moved');
              setTimeout(() => t.el.classList.remove('just-moved'), 700 + delay);
            }}
          }}
          const cleanup = () => {{
            t.el.style.transition = '';
            t.el.style.transform = '';
            t.el.removeEventListener('transitionend', cleanup);
          }};
          t.el.addEventListener('transitionend', cleanup);
        }});
      }});
    }}
  }}

  if (manage && API && !state.selectMode) setupDragDrop();
}}

function renderClosed() {{
  const section = document.getElementById('closed-section');
  if (isManageMode()) {{
    section.style.display = 'none';
    return;
  }}
  section.style.display = '';
  const closedIds = new Set(getClosedStatuses().map(s => s.id));
  const closed = DATA.sessions.filter(s => closedIds.has(s.status) && matchesFilters(s));
  document.getElementById('closed-label').textContent = `Closed Sessions (${{closed.length}})`;

  const sorted = [...closed].sort((a, b) => {{
    const k = state.closedSort.key, dir = state.closedSort.asc ? 1 : -1;
    if (k==='name') return dir * (a.name||'').localeCompare(b.name||'');
    if (k==='ws') return dir * (a.wsName||'').localeCompare(b.wsName||'');
    if (k==='status') return dir * (a.status||'').localeCompare(b.status||'');
    if (k==='cost') return dir * ((a.cost||0)-(b.cost||0));
    return dir * ((a.lastUsedAt||0)-(b.lastUsedAt||0));
  }});

  const dark = isDark();
  const closedStatusLabels = {{}};
  getClosedStatuses().forEach(s => closedStatusLabels[s.id] = s.label);

  document.getElementById('closed-body').innerHTML = sorted.map(s => {{
    const wsBg = dark ? s.wsCd : s.wsCl;
    return `<tr><td class="cn">${{esc((s.name||'').slice(0,50))}}</td>
      <td><span class="ws-badge" style="background:${{wsBg}}">${{esc(s.wsName)}}</span></td>
      <td><span class="sb sb-${{s.status}}">${{esc(closedStatusLabels[s.status]||s.status)}}</span></td>
      <td class="cm">${{relTime(s.lastUsedAt)}}</td>
      <td class="cm">${{fmtCost(s.cost)}}</td></tr>`;
  }}).join('');

  document.querySelectorAll('.ct th').forEach(th => {{
    th.classList.toggle('sorted', th.dataset.key === state.closedSort.key);
    const a = th.querySelector('.arrow');
    if (a) a.textContent = th.dataset.key === state.closedSort.key ? (state.closedSort.asc ? '\\u2191' : '\\u2193') : '\\u2191';
  }});
}}

// Feature 5: Archive view
function renderArchive() {{
  const closedIds = new Set(getClosedStatuses().map(s => s.id));
  const statusFilter = document.getElementById('archive-status').value;
  const wsFilter = document.getElementById('archive-ws').value;
  const dateFilter = document.getElementById('archive-date').value;
  const searchQ = (document.getElementById('archive-search').value||'').toLowerCase();

  let sessions = DATA.sessions.filter(s => closedIds.has(s.status));

  if (statusFilter !== 'all') sessions = sessions.filter(s => s.status === statusFilter);
  if (wsFilter !== 'all') sessions = sessions.filter(s => s.wsId === wsFilter);
  if (dateFilter !== 'all') {{
    const cutoff = NOW - parseInt(dateFilter) * 86400000;
    sessions = sessions.filter(s => (s.lastUsedAt||0) >= cutoff);
  }}
  if (searchQ) sessions = sessions.filter(s =>
    (s.name||'').toLowerCase().includes(searchQ) || (s.wsName||'').toLowerCase().includes(searchQ)
    || (s.id||'').toLowerCase().includes(searchQ));

  const sk = state.archiveSort.key, dir = state.archiveSort.asc ? 1 : -1;
  sessions.sort((a, b) => {{
    if (sk==='name') return dir * (a.name||'').localeCompare(b.name||'');
    if (sk==='ws') return dir * (a.wsName||'').localeCompare(b.wsName||'');
    if (sk==='status') return dir * (a.status||'').localeCompare(b.status||'');
    if (sk==='cost') return dir * ((a.cost||0)-(b.cost||0));
    if (sk==='msgs') return dir * ((a.msgs||0)-(b.msgs||0));
    if (sk==='created') return dir * ((a.createdAt||0)-(b.createdAt||0));
    return dir * ((a.lastUsedAt||0)-(b.lastUsedAt||0));
  }});

  const total = sessions.length;
  const pageSize = state.archivePageSize;
  const maxPage = Math.max(0, Math.ceil(total / pageSize) - 1);
  state.archivePage = Math.min(state.archivePage, maxPage);
  const page = sessions.slice(state.archivePage * pageSize, (state.archivePage + 1) * pageSize);

  const dark = isDark();
  const closedStatusLabels = {{}};
  getClosedStatuses().forEach(s => closedStatusLabels[s.id] = s.label);

  document.getElementById('archive-body').innerHTML = page.length ? page.map(s => {{
    const wsBg = dark ? s.wsCd : s.wsCl;
    return `<tr>
      <td class="cn">${{esc((s.name||'').slice(0,60))}}</td>
      <td><span class="ws-badge" style="background:${{wsBg}}">${{esc(s.wsName)}}</span></td>
      <td><span class="sb sb-${{s.status}}">${{esc(closedStatusLabels[s.status]||s.status)}}</span></td>
      <td class="cm">${{relTime(s.lastUsedAt)}}</td>
      <td class="cm">${{fmtDate(s.createdAt)}}</td>
      <td class="cm">${{fmtCost(s.cost)}}</td>
      <td class="cm">${{s.msgs}}</td>
      <td class="archive-actions">
        ${{API ? `<button class="reopen-btn" data-sid="${{esc(s.id)}}" data-ws="${{esc(s.wsId)}}">Reopen</button>` : ''}}
        <button class="open-btn" data-sid="${{esc(s.id)}}" data-sdksid="${{esc(s.sdkSid)}}" data-wsuuid="${{esc(s.wsUuid)}}" title="Open in Craft Agents">
          <svg viewBox="0 0 24 24"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
        </button>
      </td></tr>`;
  }}).join('') : '<tr><td colspan="8" style="text-align:center;padding:32px;color:var(--tx3)">No archived sessions</td></tr>';

  document.getElementById('archive-page').innerHTML = total > pageSize ? `
    <button ${{state.archivePage===0?'disabled':''}} id="arch-prev">&larr; Prev</button>
    <span>Page ${{state.archivePage+1}} of ${{maxPage+1}} (${{total}} sessions)</span>
    <button ${{state.archivePage>=maxPage?'disabled':''}} id="arch-next">Next &rarr;</button>
  ` : total ? `<span>${{total}} session${{total!==1?'s':''}}</span>` : '';

  // Update sort indicators
  document.querySelectorAll('#archive-table th').forEach(th => {{
    th.classList.toggle('sorted', th.dataset.key === state.archiveSort.key);
    const a = th.querySelector('.arrow');
    if (a) a.textContent = th.dataset.key === state.archiveSort.key ? (state.archiveSort.asc ? '\\u2191' : '\\u2193') : '\\u2191';
  }});
}}

// Feature 4: Batch bar
function renderBatchBar() {{
  const bar = document.getElementById('batch-bar');
  if (!state.selectMode || state.selected.size === 0) {{
    bar.style.display = 'none';
    return;
  }}
  bar.style.display = 'flex';
  document.getElementById('batch-count').textContent = state.selected.size + ' selected';
}}

// Feature 3: Label picker
function showLabelPicker(sid, wsId, anchorEl) {{
  closeLabelPicker();
  const sess = DATA.sessions.find(s => s.id === sid && s.wsId === wsId);
  if (!sess) return;
  const ws = getWsConfig(wsId);
  const labelDefs = ws ? ws.labelDefs || [] : [];
  if (!labelDefs.length) {{ toast('No labels configured for this workspace', 'error'); return; }}

  const rawLabels = new Set((sess.rawLabels||[]).map(l => l.split('::')[0]));
  const dark = isDark();

  const picker = document.createElement('div');
  picker.className = 'label-picker';
  picker.innerHTML = labelDefs.map(lb => {{
    const checked = rawLabels.has(lb.id);
    const c = dark ? lb.cd : lb.cl;
    return `<label class="label-picker-item" data-lid="${{esc(lb.id)}}">
      <input type="checkbox" class="label-check" value="${{esc(lb.id)}}" ${{checked?'checked':''}}>
      <span class="label-dot" style="background:${{c}}"></span>
      <span>${{esc(lb.name)}}</span>
    </label>`;
  }}).join('');

  // Position relative to anchor
  const rect = anchorEl.getBoundingClientRect();
  picker.style.position = 'fixed';
  picker.style.top = (rect.bottom + 4) + 'px';
  picker.style.left = Math.max(8, rect.left - 80) + 'px';

  picker.addEventListener('change', async e => {{
    const cb = e.target;
    if (!cb.classList.contains('label-check')) return;
    const lid = cb.value;
    let labels = [...(sess.rawLabels||[])];
    if (cb.checked) {{
      if (!labels.some(l => l === lid || l.startsWith(lid + '::'))) labels.push(lid);
    }} else {{
      labels = labels.filter(l => l !== lid && !l.startsWith(lid + '::'));
    }}
    const ok = await updateLabels(sid, wsId, labels);
    if (ok) {{
      renderAll();
    }}
  }});

  document.body.appendChild(picker);
  state.labelPicker = picker;
}}

function closeLabelPicker() {{
  if (state.labelPicker) {{
    state.labelPicker.remove();
    state.labelPicker = null;
  }}
}}

function renderViewToggle() {{
  const boardView = document.getElementById('board-view');
  const archiveView = document.getElementById('archive-view');
  const healthView = document.getElementById('health-view');
  const queueView = document.getElementById('queue-view');
  // Reset all
  boardView.style.display = 'none';
  archiveView.className = 'archive-view';
  healthView.className = 'health-view';
  queueView.className = 'queue-view';
  if (state.view === 'archive') {{
    archiveView.className = 'archive-view active';
    renderArchive();
  }} else if (state.view === 'health') {{
    healthView.className = 'health-view active';
    renderHealth();
  }} else if (state.view === 'queue') {{
    queueView.className = 'queue-view active';
    renderQueue();
  }} else {{
    boardView.style.display = '';
  }}
}}

// v2.4: Queue tab — six fixed lanes derived from session metadata
function renderQueue() {{
  const board = document.getElementById('queue-board');
  const lanes = (DATA.queueLanes || []);
  if (!lanes.length) {{
    board.innerHTML = '<div class="q-empty">No queue lanes configured.</div>';
    return;
  }}
  // Sessions visible in the queue obey the same workspace filter and search
  // as the Board, but ignore status (lanes ARE the grouping).
  const visible = DATA.sessions.filter(s => (s.lanes||[]).length && matchesFilters(s));
  const sortKey = state.sort;
  board.innerHTML = lanes.map(lane => {{
    const matches = visible.filter(s => (s.lanes||[]).includes(lane.id)).sort(sortFn(sortKey));
    const cards = matches.length
      ? matches.map(s => renderQueueCard(s, lane.id)).join('')
      : '<div class="q-empty">Nothing here.</div>';
    return `<div class="q-col" data-lane="${{esc(lane.id)}}">
      <div class="q-col-head">
        <span class="col-title">${{esc(lane.label)}}</span>
        <span class="col-count">${{matches.length}}</span>
      </div>
      <div class="q-col-hint">${{esc(lane.hint||'')}}</div>
      <div class="q-col-cards">${{cards}}</div>
    </div>`;
  }}).join('');
}}

function renderQueueCard(s, laneId) {{
  // Build a fresh card element wrapped with the lane hint. We don't reuse the
  // FLIP registry here (cards may appear in multiple lanes) — the click
  // handlers are delegated from the queue board so this stays cheap.
  const expanded = state.expanded === s.id;
  const draggable = false; // Queue is a derived view; no drag-and-drop here.
  const isSelected = state.selected.has(cardKey(s));
  const cls = 'card' + (expanded ? ' expanded' : '') + (isSelected ? ' selected' : '');
  return `<div class="q-card-wrap">
    <span class="q-lane-hint">${{esc(laneId.replace(/-/g,' '))}}</span>
    <div class="${{cls}}" data-id="${{esc(s.id)}}" data-ws="${{esc(s.wsId)}}">${{buildCardInnerHTML(s)}}</div>
  </div>`;
}}

// v2.3: Workspace Health
function sparkline(series, key, klass) {{
  // Render a polyline + dots through non-null entries. Width fills container.
  const W = 160, H = 24, PAD = 2;
  const vals = series.map(p => p[key]);
  const present = vals.filter(v => v !== null && v !== undefined);
  if (!present.length) return `<svg class="h-trend-svg" viewBox="0 0 ${{W}} ${{H}}" preserveAspectRatio="none"></svg>`;
  const min = Math.min(...present), max = Math.max(...present);
  const range = max - min || 1;
  const step = (W - PAD * 2) / Math.max(1, series.length - 1);
  let path = '', dots = '';
  let prevPresent = false;
  series.forEach((p, i) => {{
    const v = p[key];
    if (v === null || v === undefined) {{ prevPresent = false; return; }}
    const x = PAD + i * step;
    const y = H - PAD - ((v - min) / range) * (H - PAD * 2);
    path += (prevPresent ? ' L ' : ' M ') + x.toFixed(1) + ' ' + y.toFixed(1);
    if (i === series.length - 1) dots += `<circle class="h-dot ${{klass}}" cx="${{x.toFixed(1)}}" cy="${{y.toFixed(1)}}" r="2"/>`;
    prevPresent = true;
  }});
  return `<svg class="h-trend-svg" viewBox="0 0 ${{W}} ${{H}}" preserveAspectRatio="none">
    <path class="h-line ${{klass||''}}" d="${{path.trim()}}"/>${{dots}}
  </svg>`;
}}

function renderHealth() {{
  const grid = document.getElementById('health-grid');
  const cards = (DATA.health || []);
  if (!cards.length) {{
    grid.innerHTML = '<div class="h-empty">No workspace data yet.</div>';
    return;
  }}
  const dark = isDark();
  grid.innerHTML = cards.map(c => {{
    const swatch = dark ? c.cd : c.cl;
    const staleClass = c.stale > HEALTH_ATTENTION_STALE ? 'danger' : c.stale > 0 ? 'warn' : '';
    const reviewClass = c.review.oldestDays > HEALTH_ATTENTION_REVIEW_DAYS ? 'warn' : '';
    const reviewSummary = c.review.count
      ? `<strong>${{c.review.count}}</strong> review${{c.review.count!==1?'s':''}}, oldest ${{c.review.oldestDays}}d`
      : 'No review backlog';
    const automationLine = c.automationStale
      ? `<strong>${{c.automationStale}}</strong> automation${{c.automationStale!==1?'s':''}} idle 24h+`
      : '';
    return `<div class="health-card" data-ws="${{esc(c.id)}}">
      <div class="h-head">
        <span class="h-swatch" style="background:${{swatch}}"></span>
        <span class="h-name">${{esc(c.name)}}</span>
        <span class="h-badge ${{c.badge}}">${{c.badge}}</span>
      </div>
      <div class="h-stats">
        <div class="h-stat"><span class="h-stat-v">${{c.open}}</span><span class="h-stat-l">Open</span></div>
        <div class="h-stat ${{staleClass}}"><span class="h-stat-v">${{c.stale}}</span><span class="h-stat-l">Stale 7d+</span></div>
        <div class="h-stat"><span class="h-stat-v">$${{c.cost.toFixed(2)}}</span><span class="h-stat-l">Cost</span></div>
      </div>
      <div class="h-trend">
        <div class="h-trend-row"><span class="h-trend-l">Open</span>${{sparkline(c.trend, 'open', '')}}</div>
        <div class="h-trend-row"><span class="h-trend-l">Cost</span>${{sparkline(c.trend, 'cost', 'h-line-cost')}}</div>
      </div>
      <div class="h-extra">
        <span class="${{reviewClass}}">${{reviewSummary}}</span>
        <span>${{automationLine}}</span>
      </div>
    </div>`;
  }}).join('');
}}

function applyTheme() {{
  const r = document.documentElement.style;
  const dark = isDark();
  const VARS = ['--bg','--bg2','--bg3','--tx','--ac','--acs','--ok','--oks','--wn','--wns','--er','--ers','--bd','--bd2','--input-bg'];
  if (isManageMode()) {{
    const ws = getWsConfig(state.selectedWs);
    if (ws?.theme) {{
      const t = dark ? ws.theme.dark : ws.theme.light;
      r.setProperty('--bg', t.bg);
      r.setProperty('--bg2', dark ? 'color-mix(in srgb, ' + t.bg + ', #fff 8%)' : '#fff');
      r.setProperty('--bg3', dark ? 'color-mix(in srgb, ' + t.bg + ', #fff 5%)' : '#fff');
      r.setProperty('--tx', t.fg);
      r.setProperty('--bd', dark ? 'color-mix(in srgb, ' + t.fg + ', transparent 82%)' : '#e5e7eb');
      r.setProperty('--bd2', dark ? 'color-mix(in srgb, ' + t.fg + ', transparent 72%)' : '#d1d5db');
      r.setProperty('--input-bg', dark ? 'color-mix(in srgb, ' + t.bg + ', #fff 10%)' : '#f0f0f3');
      r.setProperty('--ac', t.ac);
      r.setProperty('--acs', 'color-mix(in srgb, ' + t.ac + ', transparent 88%)');
      r.setProperty('--ok', t.ok);
      r.setProperty('--oks', 'color-mix(in srgb, ' + t.ok + ', transparent 88%)');
      r.setProperty('--wn', t.wn);
      r.setProperty('--wns', 'color-mix(in srgb, ' + t.wn + ', transparent 88%)');
      r.setProperty('--er', t.er);
      r.setProperty('--ers', 'color-mix(in srgb, ' + t.er + ', transparent 88%)');
      return;
    }}
  }}
  VARS.forEach(v => r.removeProperty(v));
}}

function renderAll() {{
  applyTheme();
  renderModeBadge();
  renderStats();
  renderFilters();
  renderBoard();
  renderClosed();
  renderViewToggle();
  renderBatchBar();
  renderLensButton();
}}

// ─── v3.0: Lenses ────────────────────────────────────
function renderLensButton() {{
  const btn = document.getElementById('lens-btn');
  const label = document.getElementById('lens-btn-label');
  if (!btn) return;
  const active = currentLens();
  btn.classList.toggle('active', !!state.lens);
  label.textContent = active ? active.name : 'Lens';
}}

function currentLens() {{
  if (!state.lens) return null;
  if (state.lensKind === 'stock') {{
    return (DATA.stockLenses || []).find(l => l.id === state.lens) || null;
  }}
  return state.userLenses.find(l => l.id === state.lens) || null;
}}

function applyLens(lens) {{
  // Stock lens: predicate filter, leaves ws/search/sort alone.
  // User lens: restores ws/search/sort, no predicate.
  if (lens.kind === 'user') {{
    state.lens = lens.id;
    state.lensKind = 'user';
    state.selectedWs = lens.ws || 'all';
    state.search = lens.search || '';
    state.sort = lens.sort || 'activity';
    document.getElementById('ws-select').value = state.selectedWs;
    document.getElementById('search').value = state.search;
    document.getElementById('sort').value = state.sort;
  }} else {{
    state.lens = lens.id;
    state.lensKind = 'stock';
  }}
  state.expanded = null;
  syncLensUrl();
  closeLensMenu();
  renderAll();
}}

function clearLens() {{
  state.lens = null;
  state.lensKind = null;
  syncLensUrl();
  closeLensMenu();
  renderAll();
}}

function syncLensUrl() {{
  // Keep the URL bookmarkable. We replace state to avoid history pollution.
  const url = new URL(window.location.href);
  if (state.lens) url.searchParams.set('lens', state.lens);
  else url.searchParams.delete('lens');
  history.replaceState(null, '', url.toString());
}}

function slugify(name) {{
  return (name||'').toLowerCase()
    .replace(/[^a-z0-9-]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 60) || 'lens';
}}

async function saveCurrentAsLens() {{
  closeLensMenu();
  const name = (prompt('Lens name?') || '').trim();
  if (!name) return;
  // Avoid colliding with stock lens ids.
  const stockIds = new Set((DATA.stockLenses||[]).map(l => l.id));
  let id = slugify(name);
  if (stockIds.has(id) || state.userLenses.some(l => l.id === id)) {{
    id = id + '-' + Date.now().toString(36);
  }}
  const lens = {{
    id, name, kind: 'user',
    ws: state.selectedWs,
    search: state.search,
    sort: state.sort,
  }};
  const next = [...state.userLenses, lens];
  const ok = await postLenses(next);
  if (ok) {{
    toast('Saved lens "' + name + '"');
    state.userLenses = next.map(l => ({{ ...l, kind: 'user' }}));
    applyLens(lens);
  }}
}}

async function deleteUserLens(lensId) {{
  const next = state.userLenses.filter(l => l.id !== lensId);
  const ok = await postLenses(next);
  if (ok) {{
    state.userLenses = next;
    if (state.lens === lensId) clearLens();
    else renderLensMenu(); // refresh menu if open
    toast('Lens deleted');
  }}
}}

async function postLenses(lenses) {{
  if (!API) {{ toast('Server mode required to save lenses', 'error'); return false; }}
  try {{
    const res = await fetch(API + '/api/lenses', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ lenses }}),
    }});
    const data = await res.json();
    if (!data.ok) {{ toast(data.error || 'Save failed', 'error'); return false; }}
    return true;
  }} catch (e) {{
    toast('Connection error: ' + e.message, 'error');
    return false;
  }}
}}

async function loadUserLenses() {{
  if (!API) return;
  try {{
    const res = await fetch(API + '/api/lenses');
    const data = await res.json();
    if (data.ok && Array.isArray(data.lenses)) {{
      state.userLenses = data.lenses.map(l => ({{ ...l, kind: 'user' }}));
    }}
  }} catch (_) {{}}
}}

function openLensMenu() {{
  closeLensMenu();
  const wrap = document.querySelector('.lens-wrap');
  if (!wrap) return;
  const menu = document.createElement('div');
  menu.className = 'lens-menu';
  state.lensMenu = menu;
  wrap.appendChild(menu);
  renderLensMenu();
}}

function closeLensMenu() {{
  if (state.lensMenu) {{
    state.lensMenu.remove();
    state.lensMenu = null;
  }}
}}

function renderLensMenu() {{
  if (!state.lensMenu) return;
  const stock = (DATA.stockLenses || []);
  const user = state.userLenses;
  const cur = state.lens;
  let html = '<div class="lens-section">Stock</div>';
  html += stock.map(l =>
    `<div class="lens-item ${{cur===l.id?'current':''}}" data-lens-id="${{esc(l.id)}}" data-lens-kind="stock">
      <span class="lens-item-name">${{esc(l.name)}}</span>
    </div>`
  ).join('');
  html += '<div class="lens-divider"></div><div class="lens-section">Saved</div>';
  if (user.length) {{
    html += user.map(l =>
      `<div class="lens-item ${{cur===l.id?'current':''}}" data-lens-id="${{esc(l.id)}}" data-lens-kind="user">
        <span class="lens-item-name">${{esc(l.name)}}</span>
        <button class="lens-del" data-del="${{esc(l.id)}}" title="Delete lens">\u00d7</button>
      </div>`
    ).join('');
  }} else {{
    html += '<div class="lens-empty">No saved lenses yet.</div>';
  }}
  html += '<div class="lens-divider"></div>';
  html += '<div class="lens-action" data-action="save">+ Save current view as lens\u2026</div>';
  if (cur) html += '<div class="lens-action" data-action="clear">Clear lens</div>';
  state.lensMenu.innerHTML = html;
}}

// ─── Drag & Drop ─────────────────────────────────────
function setupDragDrop() {{
  // Drop-zone listeners: zones are rebuilt each render, so always re-attach.
  document.querySelectorAll('.col-cards[data-status]').forEach(zone => {{
    zone.addEventListener('dragover', e => {{
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      zone.classList.add('drag-over');
    }});
    zone.addEventListener('dragleave', e => {{
      if (!zone.contains(e.relatedTarget)) zone.classList.remove('drag-over');
    }});
    zone.addEventListener('drop', async e => {{
      e.preventDefault();
      zone.classList.remove('drag-over');
      const id = e.dataTransfer.getData('text/plain');
      const ws = e.dataTransfer.getData('application/x-ws');
      const newStatus = zone.dataset.status;
      const sess = DATA.sessions.find(s => s.id === id && s.wsId === ws);
      if (!sess || sess.status === newStatus) return;
      await updateStatus(id, ws, newStatus);
    }});
  }});

  // Card listeners: cards are reused across renders, so attach once per element.
  document.querySelectorAll('.card.draggable').forEach(card => {{
    if (__mcDragAttached.has(card)) return;
    __mcDragAttached.add(card);
    card.addEventListener('dragstart', e => {{
      e.dataTransfer.setData('text/plain', card.dataset.id);
      e.dataTransfer.setData('application/x-ws', card.dataset.ws);
      e.dataTransfer.effectAllowed = 'move';
      card.classList.add('dragging');
      state.dragging = card.dataset.id;
      setTimeout(() => {{ card.style.display = 'none'; }}, 0);
    }});
    card.addEventListener('dragend', e => {{
      card.classList.remove('dragging');
      card.style.display = '';
      state.dragging = null;
      document.querySelectorAll('.drag-over').forEach(z => z.classList.remove('drag-over'));
    }});
  }});
}}

// ─── Events ──────────────────────────────────────────
document.getElementById('ws-select').innerHTML =
  '<option value="all">All Workspaces (Overview)</option>' +
  DATA.workspaces.map(ws => `<option value="${{ws.id}}">${{esc(ws.name)}}</option>`).join('');

document.getElementById('ws-select').addEventListener('change', e => {{
  state.selectedWs = e.target.value;
  state.expanded = null;
  state.search = '';
  state.selected.clear();
  document.getElementById('search').value = '';
  renderAll();
}});

document.getElementById('filters').addEventListener('click', e => {{
  // New session button in filter pills
  const newBtn = e.target.closest('[data-newws]');
  if (newBtn) {{ newSession(newBtn.dataset.newws); return; }}
  const pill = e.target.closest('.fpill');
  if (!pill) return;
  const ws = pill.dataset.ws;
  if (state.activeWs.has(ws)) {{
    if (state.activeWs.size === 1) DATA.workspaces.forEach(w => state.activeWs.add(w.id));
    else state.activeWs.delete(ws);
  }} else state.activeWs.add(ws);
  renderAll();
}});

document.getElementById('search').addEventListener('input', e => {{
  state.search = e.target.value;
  state.expanded = null;
  renderAll();
}});

document.getElementById('sort').addEventListener('change', e => {{
  state.sort = e.target.value;
  renderBoard();
}});

// Feature 1: Export
document.getElementById('export-btn').addEventListener('click', exportCSV);

// Feature 4: Select mode toggle
document.getElementById('select-toggle').addEventListener('click', () => {{
  state.selectMode = !state.selectMode;
  state.selected.clear();
  document.querySelector('.wrap').classList.toggle('select-mode', state.selectMode);
  renderBoard();
  renderBatchBar();
}});

// Board click handler — cards, open buttons, label buttons, checkboxes
document.getElementById('board').addEventListener('click', e => {{
  // New session button in column header
  const colNewBtn = e.target.closest('.col-new-btn');
  if (colNewBtn) {{ e.stopPropagation(); newSession(colNewBtn.dataset.newws); return; }}

  // Checkbox in select mode
  const cb = e.target.closest('.card-check');
  if (cb) {{
    e.stopPropagation();
    const card = cb.closest('.card');
    const key = card.dataset.ws + ':' + card.dataset.id;
    if (cb.checked) state.selected.add(key);
    else state.selected.delete(key);
    card.classList.toggle('selected', cb.checked);
    renderBatchBar();
    return;
  }}

  // Label button
  const labelBtn = e.target.closest('.label-btn');
  if (labelBtn) {{
    e.stopPropagation();
    showLabelPicker(labelBtn.dataset.sid, labelBtn.dataset.ws, labelBtn);
    return;
  }}

  // Open button
  const openBtn = e.target.closest('.open-btn');
  if (openBtn) {{
    e.stopPropagation();
    openSession(openBtn);
    return;
  }}

  // Card expansion (not in select mode)
  const card = e.target.closest('.card');
  if (!card) return;
  if (state.selectMode) {{
    // Toggle selection on card click in select mode
    const key = card.dataset.ws + ':' + card.dataset.id;
    if (state.selected.has(key)) state.selected.delete(key);
    else state.selected.add(key);
    renderBoard();
    renderBatchBar();
    return;
  }}
  state.expanded = state.expanded === card.dataset.id ? null : card.dataset.id;
  renderBoard();
}});

// Close label picker on outside click
document.addEventListener('click', e => {{
  if (state.labelPicker && !state.labelPicker.contains(e.target) && !e.target.closest('.label-btn')) {{
    closeLabelPicker();
  }}
}});

// Feature 4: Batch actions
document.getElementById('batch-apply-status').addEventListener('click', () => {{
  const newStatus = document.getElementById('batch-status-select').value;
  if (newStatus) batchUpdateStatus(newStatus);
}});
document.getElementById('batch-deselect').addEventListener('click', () => {{
  state.selected.clear();
  state.selectMode = false;
  document.querySelector('.wrap').classList.remove('select-mode');
  renderBoard();
  renderBatchBar();
}});

// Populate batch status dropdown
const batchSelect = document.getElementById('batch-status-select');
getAllStatuses().forEach(s => {{
  const opt = document.createElement('option');
  opt.value = s.id;
  opt.textContent = s.label;
  batchSelect.appendChild(opt);
}});

document.getElementById('closed-toggle').addEventListener('click', () => {{
  document.getElementById('closed-section').classList.toggle('open');
}});

document.querySelector('.ct thead').addEventListener('click', e => {{
  const th = e.target.closest('th');
  if (!th?.dataset.key) return;
  if (state.closedSort.key === th.dataset.key) state.closedSort.asc = !state.closedSort.asc;
  else {{ state.closedSort.key = th.dataset.key; state.closedSort.asc = th.dataset.key==='name'||th.dataset.key==='ws'; }}
  renderClosed();
}});

// Feature 5: View tabs
document.getElementById('view-tabs').addEventListener('click', e => {{
  const tab = e.target.closest('.view-tab');
  if (!tab) return;
  state.view = tab.dataset.view;
  document.querySelectorAll('.view-tab').forEach(t => t.classList.toggle('active', t.dataset.view === state.view));
  renderViewToggle();
}});

// v3.0: Lens dropdown toggle + menu actions
document.getElementById('lens-btn').addEventListener('click', e => {{
  e.stopPropagation();
  if (state.lensMenu) closeLensMenu();
  else openLensMenu();
}});

document.addEventListener('click', e => {{
  if (!state.lensMenu) return;
  if (!state.lensMenu.contains(e.target) && !e.target.closest('#lens-btn')) {{
    closeLensMenu();
    return;
  }}
  const del = e.target.closest('[data-del]');
  if (del) {{
    e.stopPropagation();
    if (confirm('Delete this lens?')) deleteUserLens(del.dataset.del);
    return;
  }}
  const item = e.target.closest('.lens-item');
  if (item) {{
    const stock = (DATA.stockLenses || []).find(l => l.id === item.dataset.lensId);
    const userLens = state.userLenses.find(l => l.id === item.dataset.lensId);
    const lens = item.dataset.lensKind === 'stock' ? stock : userLens;
    if (lens) applyLens(lens);
    return;
  }}
  const action = e.target.closest('[data-action]');
  if (action) {{
    if (action.dataset.action === 'save') saveCurrentAsLens();
    else if (action.dataset.action === 'clear') clearLens();
  }}
}});

// v2.4: Queue board reuses the Board's card-click semantics
document.getElementById('queue-board').addEventListener('click', e => {{
  // Checkbox in select mode
  const cb = e.target.closest('.card-check');
  if (cb) {{
    e.stopPropagation();
    const card = cb.closest('.card');
    const key = card.dataset.ws + ':' + card.dataset.id;
    if (cb.checked) state.selected.add(key);
    else state.selected.delete(key);
    card.classList.toggle('selected', cb.checked);
    renderBatchBar();
    return;
  }}
  const labelBtn = e.target.closest('.label-btn');
  if (labelBtn) {{
    e.stopPropagation();
    showLabelPicker(labelBtn.dataset.sid, labelBtn.dataset.ws, labelBtn);
    return;
  }}
  const openBtn = e.target.closest('.open-btn');
  if (openBtn) {{
    e.stopPropagation();
    openSession(openBtn);
    return;
  }}
  const card = e.target.closest('.card');
  if (!card) return;
  if (state.selectMode) {{
    const key = card.dataset.ws + ':' + card.dataset.id;
    if (state.selected.has(key)) state.selected.delete(key);
    else state.selected.add(key);
    renderQueue();
    renderBatchBar();
    return;
  }}
  state.expanded = state.expanded === card.dataset.id ? null : card.dataset.id;
  renderQueue();
}});

// v2.3: Health card click → switch to Board, scoped to that workspace
document.getElementById('health-grid').addEventListener('click', e => {{
  const card = e.target.closest('.health-card');
  if (!card) return;
  const wsId = card.dataset.ws;
  if (!wsId) return;
  state.selectedWs = wsId;
  document.getElementById('ws-select').value = wsId;
  state.view = 'board';
  document.querySelectorAll('.view-tab').forEach(t => t.classList.toggle('active', t.dataset.view === 'board'));
  state.expanded = null;
  state.search = '';
  document.getElementById('search').value = '';
  renderAll();
}});

// Feature 5: Archive filters & sorting
['archive-status','archive-ws','archive-date'].forEach(id => {{
  document.getElementById(id).addEventListener('change', () => {{ state.archivePage = 0; renderArchive(); }});
}});
document.getElementById('archive-search').addEventListener('input', () => {{ state.archivePage = 0; renderArchive(); }});

document.querySelector('#archive-table thead').addEventListener('click', e => {{
  const th = e.target.closest('th');
  if (!th?.dataset.key) return;
  if (state.archiveSort.key === th.dataset.key) state.archiveSort.asc = !state.archiveSort.asc;
  else {{ state.archiveSort.key = th.dataset.key; state.archiveSort.asc = th.dataset.key==='name'||th.dataset.key==='ws'; }}
  renderArchive();
}});

// Feature 5: Archive pagination and actions
document.getElementById('archive-page').addEventListener('click', e => {{
  if (e.target.id === 'arch-prev') {{ state.archivePage--; renderArchive(); }}
  if (e.target.id === 'arch-next') {{ state.archivePage++; renderArchive(); }}
}});

document.getElementById('archive-view').addEventListener('click', async e => {{
  const reopenBtn = e.target.closest('.reopen-btn');
  if (reopenBtn) {{
    await updateStatus(reopenBtn.dataset.sid, reopenBtn.dataset.ws, 'todo');
    renderArchive();
    return;
  }}
  const openBtn = e.target.closest('.open-btn');
  if (openBtn) {{ openSession(openBtn); return; }}
}});

// Populate archive filter dropdowns
const archStatusSel = document.getElementById('archive-status');
getClosedStatuses().forEach(s => {{
  const opt = document.createElement('option');
  opt.value = s.id;
  opt.textContent = s.label;
  archStatusSel.appendChild(opt);
}});
const archWsSel = document.getElementById('archive-ws');
DATA.workspaces.forEach(ws => {{
  const opt = document.createElement('option');
  opt.value = ws.id;
  opt.textContent = ws.name;
  archWsSel.appendChild(opt);
}});

matchMedia('(prefers-color-scheme:dark)').addEventListener('change', renderAll);

document.addEventListener('keydown', e => {{
  if ((e.metaKey||e.ctrlKey) && e.key==='k') {{ e.preventDefault(); document.getElementById('search').focus(); }}
  if (e.key==='Escape') {{
    closeLabelPicker();
    if (state.selectMode) {{
      state.selectMode = false;
      state.selected.clear();
      document.querySelector('.wrap').classList.remove('select-mode');
      renderBoard();
      renderBatchBar();
      return;
    }}
    document.getElementById('search').blur();
    state.search = '';
    document.getElementById('search').value = '';
    state.expanded = null;
    renderAll();
  }}
}});

// ─── Init ────────────────────────────────────────────
const urlWs = new URLSearchParams(location.search).get('ws');
if (urlWs && DATA.workspaces.some(w => w.id === urlWs)) {{
  state.selectedWs = urlWs;
  document.getElementById('ws-select').value = urlWs;
}}
renderAll();
if (!API) toast('View-only mode (open via --serve for drag-and-drop)', 'error');

// v3.0: load saved user lenses, then apply ?lens= if any matches.
(async () => {{
  await loadUserLenses();
  const urlLens = new URLSearchParams(location.search).get('lens');
  if (urlLens) {{
    const stock = (DATA.stockLenses||[]).find(l => l.id === urlLens);
    const user = state.userLenses.find(l => l.id === urlLens);
    if (stock) applyLens(stock);
    else if (user) applyLens(user);
  }} else {{
    renderLensButton();
  }}
}})();

// ─── Auto-refresh (polling) ──────────────────────────
// Signature captures only fields that affect board layout/appearance so we
// skip re-renders when nothing interesting changed.
function __mcSignature(sessions) {{
  return sessions.map(s => [
    s.id, s.status, s.name, s.msgs, s.lastUsedAt,
    (s.labels || []).map(l => l.d).join(','),
  ].join('|')).join('\\n');
}}
let __mcLastSig = __mcSignature(DATA.sessions);
let __mcRefreshInFlight = false;

async function refreshData() {{
  if (!API || __mcRefreshInFlight) return;
  if (document.hidden) return;
  // Don't yank cards while user is mid-drag, mid-batch, or has a picker open.
  if (state.dragging) return;
  if (document.querySelector('.card.dragging')) return;
  if (state.labelPicker) return;
  __mcRefreshInFlight = true;
  try {{
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 4000);
    const res = await fetch(API + '/api/data', {{ signal: ctrl.signal, credentials: 'same-origin' }});
    clearTimeout(t);
    if (!res.ok) return;
    const fresh = await res.json();
    if (!fresh || !Array.isArray(fresh.sessions)) return;
    const sig = __mcSignature(fresh.sessions);
    if (sig === __mcLastSig) return;
    __mcLastSig = sig;
    DATA.sessions = fresh.sessions;
    if (Array.isArray(fresh.workspaces)) DATA.workspaces = fresh.workspaces;
    if (Array.isArray(fresh.statuses)) DATA.statuses = fresh.statuses;
    if (Array.isArray(fresh.health)) DATA.health = fresh.health;
    renderAll();
  }} catch (_) {{
    // Network hiccup / abort; next tick will try again.
  }} finally {{
    __mcRefreshInFlight = false;
  }}
}}

window.__mcRefresh = refreshData;

if (API) {{
  setInterval(refreshData, 3000);
  document.addEventListener('visibilitychange', () => {{
    if (!document.hidden) refreshData();
  }});
}}
</script>
</body>
</html>'''


# ─── Cross-platform URL open ────────────────────────────────────────────────

def _open_url(url):
    """Open a URL via the platform's default handler.

    Returns True on success, False otherwise. macOS → `open`, Linux → `xdg-open`,
    Windows → `os.startfile`. Failures are logged but not raised.
    """
    try:
        if sys.platform == "darwin":
            rc = subprocess.run(["open", url], capture_output=True, timeout=5).returncode
            return rc == 0
        if sys.platform.startswith("linux"):
            rc = subprocess.run(["xdg-open", url], capture_output=True, timeout=5).returncode
            return rc == 0
        if sys.platform == "win32":
            os.startfile(url)  # type: ignore[attr-defined]  # Windows-only
            return True
    except Exception as e:
        logger.warning("deeplink open failed for %s: %s", url, e)
    return False


# ─── HTTP Server ─────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    data = None
    html = None

    def log_message(self, fmt, *args):
        pass  # suppress logs

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _same_origin(self):
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        port = self.server.server_address[1]
        allowed = (f"http://localhost:{port}", f"http://127.0.0.1:{port}")
        return origin in allowed

    def _read_json_body(self):
        """Read and parse a JSON POST body with size cap + parse guard.

        On success returns the parsed object. On failure writes the error
        response and returns None — the caller should bail immediately.
        """
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            self._json(400, {"ok": False, "error": "Invalid Content-Length"})
            return None
        if length <= 0:
            self._json(400, {"ok": False, "error": "Empty body"})
            return None
        if length > MAX_BODY:
            self._json(413, {"ok": False, "error": "Body too large"})
            return None
        try:
            raw = self.rfile.read(length)
            return json.loads(raw)
        except (ValueError, json.JSONDecodeError):
            self._json(400, {"ok": False, "error": "Invalid JSON"})
            return None

    def _valid_status(self, value):
        return isinstance(value, str) and bool(STATUS_RE.match(value))

    def _valid_labels(self, value):
        if not isinstance(value, list):
            return False
        if len(value) > MAX_LABELS:
            return False
        return all(isinstance(lb, str) and 0 < len(lb) <= MAX_LABEL_LEN for lb in value)

    def _html(self, code, html):
        body = html.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def _refresh_data(self):
        """Re-collect workspace/session data for live updates."""
        now_ms = int(time.time() * 1000)
        workspaces = collect()
        Handler.data = build_data(workspaces, now_ms=now_ms)
        Handler.html = generate_html(Handler.data, api_base=Handler.api_base)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            self._refresh_data()
            self._html(200, Handler.html)
        elif path == "/api/data":
            self._refresh_data()
            self._json(200, Handler.data)
        elif path == "/api/alerts":
            self._json(200, {"ok": True, "alerts": get_alerts()})
        elif path == "/api/workspace-labels":
            qs = parse_qs(urlparse(self.path).query)
            ws_dir = qs.get("ws", [""])[0]
            if not ws_dir:
                self._json(400, {"ok": False, "error": "Missing ws param"})
            else:
                self._json(200, {"ok": True, "labels": get_workspace_labels(ws_dir)})
        elif path == "/api/lenses":
            self._json(200, {"ok": True, "lenses": read_user_lenses()})
        elif path == "/health":
            self._json(200, {"ok": True, "version": __version__})
        else:
            self.send_error(404)

    def do_POST(self):
        if not self._same_origin():
            self._json(403, {"ok": False, "error": "Forbidden"})
            return
        path = self.path.split("?")[0]
        if path == "/api/status":
            body = self._read_json_body()
            if body is None:
                return
            sid = body.get("sessionId")
            ws = body.get("wsDir")
            ns = body.get("newStatus")
            if not sid or not ws or not ns:
                self._json(400, {"ok": False, "error": "Missing fields"})
                return
            if not self._valid_status(ns):
                self._json(400, {"ok": False, "error": "Invalid status"})
                return
            ok, msg = update_session_status(ws, sid, ns)
            if ok:
                self._refresh_data()
            self._json(200 if ok else 400, {"ok": ok, "message": msg} if ok else {"ok": False, "error": msg})
        elif path == "/api/labels":
            body = self._read_json_body()
            if body is None:
                return
            sid = body.get("sessionId")
            ws = body.get("wsDir")
            labels = body.get("labels")
            if not sid or not ws or labels is None:
                self._json(400, {"ok": False, "error": "Missing fields"})
                return
            if not self._valid_labels(labels):
                self._json(400, {"ok": False, "error": "Invalid labels"})
                return
            ok, result = update_session_labels(ws, sid, labels)
            if ok:
                self._refresh_data()
            self._json(200 if ok else 400, {"ok": ok, "labels": result} if ok else {"ok": False, "error": result})
        elif path == "/api/batch/status":
            body = self._read_json_body()
            if body is None:
                return
            items = body.get("items", [])
            new_status = body.get("newStatus")
            if not items or not new_status:
                self._json(400, {"ok": False, "error": "Missing fields"})
                return
            if not isinstance(items, list) or len(items) > 500:
                self._json(400, {"ok": False, "error": "Invalid items"})
                return
            if not self._valid_status(new_status):
                self._json(400, {"ok": False, "error": "Invalid status"})
                return
            updated, failed = 0, 0
            for item in items:
                if not isinstance(item, dict):
                    failed += 1
                    continue
                ok, _ = update_session_status(item.get("wsDir"), item.get("sessionId"), new_status)
                if ok:
                    updated += 1
                else:
                    failed += 1
            self._refresh_data()
            self._json(200, {"ok": True, "updated": updated, "failed": failed})
        elif path == "/api/lenses":
            body = self._read_json_body()
            if body is None:
                return
            lenses = body.get("lenses")
            if lenses is None:
                self._json(400, {"ok": False, "error": "Missing lenses"})
                return
            ok, result = write_user_lenses(lenses)
            if ok:
                self._json(200, {"ok": True, "saved": result, "lenses": read_user_lenses()})
            else:
                self._json(400, {"ok": False, "error": result})
        elif path == "/api/open-url":
            body = self._read_json_body()
            if body is None:
                return
            url = body.get("url", "")
            if not isinstance(url, str) or not url.startswith("craftagents://"):
                self._json(400, {"ok": False, "error": "Only craftagents:// URLs allowed"})
                return
            if len(url) > MAX_URL_LEN or any(ord(c) < 0x20 for c in url):
                self._json(400, {"ok": False, "error": "Invalid URL"})
                return
            ok = _open_url(url)
            self._json(200 if ok else 500, {"ok": ok, "url": url})
        elif path == "/api/open":
            body = self._read_json_body()
            if body is None:
                return
            session_id = body.get("sessionId", "")
            sdk_sid = body.get("sdkSessionId", "")
            ws_uuid = body.get("wsUuid", "")
            if not session_id:
                self._json(400, {"ok": False, "error": "Missing sessionId"})
                return
            if not isinstance(session_id, str) or not SESSION_ID_RE.match(session_id):
                self._json(400, {"ok": False, "error": "Invalid sessionId"})
                return
            if ws_uuid:
                url = f"craftagents://workspace/{ws_uuid}/allSessions/session/{session_id}"
            else:
                url = f"craftagents://allSessions/session/{session_id}"
            if _open_url(url):
                self._json(200, {"ok": True, "method": "deeplink", "url": url})
                return
            if sdk_sid and isinstance(sdk_sid, str):
                fb_url = f"craftagents://allSessions/session/{sdk_sid}"
                if _open_url(fb_url):
                    self._json(200, {"ok": True, "method": "deeplink-uuid", "url": fb_url})
                    return
            self._json(500, {"ok": False, "error": "deeplink failed"})
        else:
            self.send_error(404)


def serve(port=9753):
    workspaces = collect()
    data = build_data(workspaces)
    Handler.data = data
    Handler.api_base = f"http://localhost:{port}"
    Handler.html = generate_html(data, api_base=Handler.api_base)
    server = HTTPServer(("127.0.0.1", port), Handler)
    total = sum(len(ws["sessions"]) for ws in workspaces)
    print(f"Mission Control server running at http://localhost:{port}")
    print(f"  {len(workspaces)} workspaces, {total} sessions")
    print(f"  Press Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
        server.server_close()


# ─── Main ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)
    if len(sys.argv) >= 2 and sys.argv[1] in ("-V", "--version"):
        print(f"mission-control {__version__}")
        sys.exit(0)
    if len(sys.argv) >= 2 and sys.argv[1] == "--serve":
        port = int(sys.argv[2]) if len(sys.argv) > 2 else 9753
        serve(port)
    elif len(sys.argv) >= 2:
        workspaces = collect()
        data = build_data(workspaces)
        html = generate_html(data)
        Path(sys.argv[1]).write_text(html, encoding="utf-8")
        total = sum(len(ws["sessions"]) for ws in workspaces)
        print(f"Mission Control generated: {sys.argv[1]}")
        print(f"  {len(workspaces)} workspaces, {total} sessions")
    else:
        print("Usage:")
        print("  python3 dashboard.py OUTPUT_FILE       Generate static HTML")
        print("  python3 dashboard.py --serve [PORT]    Start interactive server")
