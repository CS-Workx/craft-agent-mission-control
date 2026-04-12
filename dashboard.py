#!/usr/bin/env python3
"""
Mission Control — Interactive cross-workspace session dashboard for Craft Agent.

Usage:
  python3 dashboard.py OUTPUT_FILE          Generate static HTML file
  python3 dashboard.py --serve [PORT]       Start interactive server (default: 9753)

Server mode enables drag-and-drop status changes via a local API.
"""

__version__ = "1.0.0"

import json
import os
import subprocess
import sys
import time
import threading
from pathlib import Path
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# ─── Config ──────────────────────────────────────────────────────────────────

NOW_MS = int(time.time() * 1000)
CRAFT_DIR = Path(os.path.expanduser("~/.craft-agent"))
WORKSPACES_DIR = CRAFT_DIR / "workspaces"

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

def collect():
    workspaces = []
    if not WORKSPACES_DIR.exists():
        return workspaces

    # Read top-level config for workspace UUIDs (needed for deeplinks)
    app_uuid_map = {}
    top_config = CRAFT_DIR / "config.json"
    if top_config.exists():
        try:
            for ws_entry in json.loads(top_config.read_text()).get("workspaces", []):
                slug = ws_entry.get("slug", "")
                if slug:
                    app_uuid_map[slug] = ws_entry.get("id", "")
        except Exception:
            pass

    for ws_dir in sorted(WORKSPACES_DIR.iterdir()):
        if not ws_dir.is_dir() or ws_dir.name.startswith("."):
            continue
        config_path = ws_dir / "config.json"
        if not config_path.exists():
            continue
        try:
            config = json.loads(config_path.read_text())
        except Exception:
            continue

        # Load workspace theme
        theme_name = config.get("defaults", {}).get("colorTheme", "")
        theme = None
        if theme_name:
            theme_path = CRAFT_DIR / "themes" / f"{theme_name}.json"
            if theme_path.exists():
                try:
                    theme = json.loads(theme_path.read_text())
                except Exception:
                    pass
        if not theme:
            # Load default theme
            default_path = CRAFT_DIR / "themes" / "default.json"
            if default_path.exists():
                try:
                    theme = json.loads(default_path.read_text())
                except Exception:
                    pass

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
            try:
                ws["statuses_raw"] = json.loads(st_path.read_text()).get("statuses", [])
            except Exception:
                pass

        lb_path = ws_dir / "labels" / "config.json"
        if lb_path.exists():
            try:
                def flatten(labels):
                    for lb in labels:
                        light, dark = resolve_label_color(lb.get("color"))
                        ws["labels"][lb["id"]] = {"name": lb.get("name", lb["id"]), "light": light, "dark": dark}
                        if "children" in lb:
                            flatten(lb["children"])
                flatten(json.loads(lb_path.read_text()).get("labels", []))
            except Exception:
                pass

        sessions_dir = ws_dir / "sessions"
        if sessions_dir.exists():
            for sess_dir in sessions_dir.iterdir():
                if not sess_dir.is_dir():
                    continue
                jsonl = sess_dir / "session.jsonl"
                if not jsonl.exists():
                    continue
                try:
                    with open(jsonl) as f:
                        line = f.readline().strip()
                    if line:
                        ws["sessions"].append(json.loads(line))
                except Exception:
                    continue
        workspaces.append(ws)
    return workspaces


def build_data(workspaces):
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

        ws_list.append({
            "id": ws["dir_name"], "name": ws["name"],
            "wsUuid": ws.get("app_uuid") or ws.get("ws_id", ""),
            "cl": light, "cd": dark,
            "count": len(ws["sessions"]),
            "statuses": sorted(statuses, key=lambda s: s["order"]),
            "theme": ws_theme,
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
                "tokens": tu.get("totalTokens", 0) or 0,
                "sdkSid": sess.get("sdkSessionId", ""),
            })

    return {
        "now": NOW_MS,
        "workspaces": ws_list,
        "sessions": sessions,
    }


# ─── Status Update ──────────────────────────────────────────────────────────

def update_session_status(ws_dir_name, session_id, new_status):
    """Update sessionStatus in the first line of session.jsonl."""
    jsonl = WORKSPACES_DIR / ws_dir_name / "sessions" / session_id / "session.jsonl"
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
        with open(jsonl, "w") as f:
            f.writelines(lines)
        return True, f"{old_status} -> {new_status}"
    except Exception as e:
        return False, str(e)


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

.drag-hint{{
  display:none;margin-left:auto;font-size:11px;color:var(--tx3);
  font-weight:400;font-style:italic
}}
.drag-enabled .drag-hint{{display:inline}}

.mode-badge{{
  display:inline-block;padding:3px 10px;border-radius:6px;font-size:11px;font-weight:600;
  margin-left:12px
}}
.mode-manage{{background:var(--oks);color:var(--ok)}}
.mode-view{{background:var(--acs);color:var(--ac)}}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="htop">
      <div class="htop-left">
        <h1>Mission Control</h1>
        <span id="mode-badge" class="mode-badge"></span>
        <span class="ts">Updated {ts}</span>
      </div>
    </div>
    <div class="stats" id="stats"></div>
  </header>

  <div class="toolbar">
    <select class="select ws-selector" id="ws-select">
      <option value="all">All Workspaces</option>
    </select>
    <div class="toolbar-right">
      <div class="search-wrap">
        <span class="search-icon">&#x1F50D;</span>
        <input type="text" class="search-box" id="search" placeholder="Search sessions\u2026" autocomplete="off" spellcheck="false">
      </div>
      <select class="select" id="sort">
        <option value="activity">Sort: Last Activity</option>
        <option value="name">Sort: Name (A\\u2013Z)</option>
        <option value="cost">Sort: Cost (High\\u2013Low)</option>
        <option value="messages">Sort: Messages</option>
        <option value="staleness">Sort: Most Stale</option>
      </select>
    </div>
  </div>

  <div class="filters" id="filters"></div>
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
<div class="toast-container" id="toasts"></div>

<script>
const DATA = {data_json};
const API = "{api_base}";
const NOW = Date.now();

// ─── State ───────────────────────────────────────────
const state = {{
  selectedWs: 'all',
  activeWs: new Set(DATA.workspaces.map(w => w.id)),
  search: '',
  sort: 'activity',
  expanded: null,
  closedSort: {{ key: 'activity', asc: false }},
  dragging: null,
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

function getStatusColumns() {{
  if (isManageMode()) {{
    const ws = getWsConfig(state.selectedWs);
    return ws ? ws.statuses : [];
  }}
  // All workspaces overview: show columns that have visible sessions + always-show columns
  const closedIds = new Set(getClosedStatuses().map(s => s.id));
  const ALWAYS_SHOW = new Set(['todo', 'automated']);
  // Build a map of all known open status definitions
  const statusMap = {{}};
  for (const ws of DATA.workspaces) {{
    for (const s of ws.statuses) {{
      if (s.category === 'open' && !statusMap[s.id]) statusMap[s.id] = s;
    }}
  }}
  // Find which statuses actually have visible sessions
  const visibleSessions = DATA.sessions.filter(s => !closedIds.has(s.status) && matchesFilters(s));
  const usedStatuses = new Set(visibleSessions.map(s => s.status));
  // Include: always-show columns + any column with sessions
  const cols = [];
  const seen = new Set();
  for (const [id, def] of Object.entries(statusMap)) {{
    if (ALWAYS_SHOW.has(id) || usedStatuses.has(id)) {{
      cols.push(def);
      seen.add(id);
    }}
  }}
  // Catch any referenced status not in any workspace definition
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

function matchesFilters(s) {{
  if (isManageMode()) {{
    if (s.wsId !== state.selectedWs) return false;
  }} else {{
    if (!state.activeWs.has(s.wsId)) return false;
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
      // Update local data
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
    return `<span class="fpill ${{active?'active':'inactive'}}" data-ws="${{ws.id}}" style="${{st}}">${{esc(ws.name)}} <span class="pc">${{ws.count}}</span></span>`;
  }}).join('');
}}

function renderCard(s) {{
  const dark = isDark();
  const wsBg = dark ? s.wsCd : s.wsCl;
  const expanded = state.expanded === s.id;
  const draggable = isManageMode() && !!API;
  const labels = (s.labels||[]).slice(0, expanded ? 20 : 4).map(l => {{
    const c = dark ? l.cd : l.cl;
    return `<span class="label-pill" style="border-color:${{c}};color:${{c}}">${{esc(l.d)}}</span>`;
  }}).join('');

  const details = expanded ? `
    <div class="card-details">
      <div class="detail-row"><span class="detail-label">Session ID</span><span>${{esc(s.id)}}</span></div>
      <div class="detail-row"><span class="detail-label">Created</span><span>${{s.createdAt ? new Date(s.createdAt).toLocaleDateString('en-GB',{{day:'numeric',month:'short',year:'numeric'}}) : '?'}}</span></div>
      <div class="detail-row"><span class="detail-label">Tokens</span><span>${{(s.tokens||0).toLocaleString()}}</span></div>
      <div class="detail-row"><span class="detail-label">Cost</span><span>${{fmtCost(s.cost)||'$0.00'}}</span></div>
      <div class="detail-row"><span class="detail-label">Messages</span><span>${{s.msgs}}</span></div>
      <div class="detail-row"><span class="detail-label">Model</span><span>${{s.model||'?'}}</span></div>
    </div>` : '';

  return `<div class="card ${{expanded?'expanded':''}} ${{draggable?'draggable':''}}" data-id="${{s.id}}" data-ws="${{s.wsId}}" ${{draggable?'draggable="true"':''}}>
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
    <button class="open-btn" data-sid="${{s.id}}" data-sdksid="${{s.sdkSid}}" data-wsuuid="${{s.wsUuid}}" title="Open in Craft Agents">
      <svg viewBox="0 0 24 24"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
      Open
    </button>
  </div>
  ${{details}}
</div>`;
}}

function renderBoard() {{
  const manage = isManageMode();
  const closedIds = new Set(getClosedStatuses().map(s => s.id));
  // In manage mode: show ALL sessions (including closed) on the board
  // In overview mode: only show open sessions
  const boardSessions = manage
    ? DATA.sessions.filter(matchesFilters)
    : DATA.sessions.filter(s => !closedIds.has(s.status) && matchesFilters(s));
  const statusCols = manage
    ? getStatusColumns()
    : getStatusColumns().filter(s => s.category === 'open');
  const boardEl = document.getElementById('board');

  if (boardSessions.length === 0 && state.search) {{
    boardEl.innerHTML = `<div class="no-results"><div class="no-results-icon">&#128269;</div><div class="no-results-text">No sessions match "${{esc(state.search)}}"</div></div>`;
    boardEl.classList.remove('drag-enabled');
    return;
  }}

  boardEl.classList.toggle('drag-enabled', manage && !!API);

  boardEl.innerHTML = statusCols.map(col => {{
    const sessions = boardSessions.filter(s => s.status === col.id).sort(sortFn(state.sort));
    const cards = sessions.map(renderCard).join('') || '<div class="empty">No sessions</div>';
    const isClosed = col.category === 'closed';
    return `<div class="col ${{isClosed ? 'col-closed' : ''}}" data-status="${{col.id}}">
      <div class="col-head ${{isClosed ? 'col-head-closed' : ''}}">
        <span class="col-title">${{esc(col.label)}}</span>
        <span class="drag-hint">drag to move</span>
        <span class="col-count">${{sessions.length}}</span>
      </div>
      <div class="col-cards" data-status="${{col.id}}">${{cards}}</div>
    </div>`;
  }}).join('');

  // Attach drag-and-drop if manage mode
  if (manage && API) setupDragDrop();
}}

function renderClosed() {{
  const section = document.getElementById('closed-section');
  // In manage mode, closed statuses are on the board — hide this section
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
  // Reset to CSS defaults
  VARS.forEach(v => r.removeProperty(v));
}}

function renderAll() {{
  applyTheme();
  renderModeBadge();
  renderStats();
  renderFilters();
  renderBoard();
  renderClosed();
}}

// ─── Drag & Drop ─────────────────────────────────────
function setupDragDrop() {{
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

  document.querySelectorAll('.card.draggable').forEach(card => {{
    card.addEventListener('dragstart', e => {{
      e.dataTransfer.setData('text/plain', card.dataset.id);
      e.dataTransfer.setData('application/x-ws', card.dataset.ws);
      e.dataTransfer.effectAllowed = 'move';
      card.classList.add('dragging');
      setTimeout(() => card.style.display = 'none', 0);
    }});
    card.addEventListener('dragend', e => {{
      card.classList.remove('dragging');
      card.style.display = '';
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
  document.getElementById('search').value = '';
  renderAll();
}});

document.getElementById('filters').addEventListener('click', e => {{
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

document.getElementById('board').addEventListener('click', e => {{
  // Handle open button click
  const openBtn = e.target.closest('.open-btn');
  if (openBtn) {{
    e.stopPropagation();
    openSession(openBtn);
    return;
  }}
  // Handle card expansion
  const card = e.target.closest('.card');
  if (!card) return;
  state.expanded = state.expanded === card.dataset.id ? null : card.dataset.id;
  renderBoard();
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

matchMedia('(prefers-color-scheme:dark)').addEventListener('change', renderAll);

document.addEventListener('keydown', e => {{
  if ((e.metaKey||e.ctrlKey) && e.key==='k') {{ e.preventDefault(); document.getElementById('search').focus(); }}
  if (e.key==='Escape') {{
    document.getElementById('search').blur();
    state.search = '';
    document.getElementById('search').value = '';
    state.expanded = null;
    renderAll();
  }}
}});

// ─── Init ────────────────────────────────────────────
// Auto-select workspace from URL parameter ?ws=slug
const urlWs = new URLSearchParams(location.search).get('ws');
if (urlWs && DATA.workspaces.some(w => w.id === urlWs)) {{
  state.selectedWs = urlWs;
  document.getElementById('ws-select').value = urlWs;
}}
renderAll();
if (!API) toast('View-only mode (open via --serve for drag-and-drop)', 'error');
</script>
</body>
</html>'''


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
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, code, html):
        body = html.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _refresh_data(self):
        """Re-collect workspace/session data for live updates."""
        global NOW_MS
        NOW_MS = int(time.time() * 1000)
        workspaces = collect()
        Handler.data = build_data(workspaces)
        Handler.html = generate_html(Handler.data, api_base=Handler.api_base)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            self._refresh_data()
            self._html(200, Handler.html)
        elif path == "/api/data":
            self._refresh_data()
            self._json(200, Handler.data)
        elif path == "/health":
            self._json(200, {"ok": True, "pid": os.getpid()})
        else:
            self.send_error(404)

    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/api/status":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            sid = body.get("sessionId")
            ws = body.get("wsDir")
            ns = body.get("newStatus")
            if not all([sid, ws, ns]):
                self._json(400, {"ok": False, "error": "Missing fields"})
                return
            ok, msg = update_session_status(ws, sid, ns)
            if ok:
                self._refresh_data()
            self._json(200 if ok else 400, {"ok": ok, "message": msg} if ok else {"ok": False, "error": msg})
        elif path == "/api/open":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            session_id = body.get("sessionId", "")
            sdk_sid = body.get("sdkSessionId", "")
            ws_uuid = body.get("wsUuid", "")
            if not session_id:
                self._json(400, {"ok": False, "error": "Missing sessionId"})
                return
            # Use OS-level deeplink via macOS open command (bypasses browser context)
            # Workspace-targeted to avoid "focused window" routing to the browser overlay
            if ws_uuid:
                url = f"craftagents://workspace/{ws_uuid}/allSessions/session/{session_id}"
            else:
                url = f"craftagents://allSessions/session/{session_id}"
            try:
                result = subprocess.run(["open", url], capture_output=True, timeout=5)
                if result.returncode == 0:
                    self._json(200, {"ok": True, "method": "deeplink", "url": url})
                else:
                    # Fallback: try with SDK UUID format
                    if sdk_sid:
                        fb_url = f"craftagents://allSessions/session/{sdk_sid}"
                        result2 = subprocess.run(["open", fb_url], capture_output=True, timeout=5)
                        if result2.returncode == 0:
                            self._json(200, {"ok": True, "method": "deeplink-uuid", "url": fb_url})
                            return
                    self._json(500, {"ok": False, "error": "deeplink failed"})
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
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
