#!/usr/bin/env python3
"""
mc — cross-workspace agent tasking CLI for Craft Agents.

A thin, agent-facing companion to the Mission Control dashboard (dashboard.py).
Where the dashboard is a human UI, `mc` is a command surface a Bash-driven agent
can use to see and task work across *every* Craft Agents workspace at once — a
reach the workspace-scoped in-session tools (create_task / spawn_session) do not
have.

Four tiers of capability (see SKILL.md "Agent tasking"):

  Tier 1  read / triage      GET /api/data                    always safe
  Tier 2  trigger            POST /api/status | /api/labels   fires that ws's
                             (edits session.jsonl)             Label/Status automations
  Tier 3  briefed dispatch   inject a one-shot SchedulerTick   scheduler spawns a
                             prompt automation into the ws      real briefed session
  Aux     open / new chat    craftagents:// deeplink           opens the app UI

Writes are gated: status/label/dispatch/cleanup are DRY-RUN unless you pass --go.
Dispatch injects a one-shot automation tagged `[mc-dispatch]` with id `mc-...`,
so `mc cleanup` can find and remove it after it fires.

Usage:
  mc list [--ws SLUG] [--status S] [--stale N] [--lens L] [--json]
  mc queue [--json]                       # attention lanes (needs-review, stale, blocked)
  mc show <ws> <sessionId>
  mc status <ws> <sessionId> <newStatus> [--go]
  mc label  <ws> <sessionId> a,b,c [--go]
  mc open   <ws> <sessionId>
  mc new    <ws>
  mc dispatch <ws> --prompt "..." [--at +5m|HH:MM] [--labels a,b]
       [--mode allow-all] [--model ID] [--conn SLUG] [--name "..."] [--tz TZ] [--go]
  mc dispatched [--ws SLUG]
  mc cleanup [--ws SLUG] [--id mc-xxxx] [--go]

Env: MC_PORT (default 9753), CRAFT_HOME (default ~/.craft-agent).
Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from urllib import request, error

MC_PORT = int(os.environ.get("MC_PORT", "9753"))
API = f"http://localhost:{MC_PORT}"
CRAFT_HOME = Path(os.environ.get("CRAFT_HOME") or os.path.expanduser("~/.craft-agent"))
WORKSPACES_DIR = CRAFT_HOME / "workspaces"
DEFAULT_TZ = "Europe/Brussels"
DISPATCH_NAME_PREFIX = "[mc-dispatch]"
DISPATCH_ID_PREFIX = "mc-"
SESSION_ID_RE = re.compile(r"^\d{6}-[a-z]+-[a-z]+$")


# ── low-level HTTP to the Mission Control server ──────────────────────────────
def _get(path: str):
    with request.urlopen(f"{API}{path}", timeout=15) as r:
        return json.loads(r.read().decode())


def _post(path: str, payload: dict):
    body = json.dumps(payload).encode()
    req = request.Request(
        f"{API}{path}", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode())
    except error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {"ok": False, "error": str(e)}


def _server_up() -> bool:
    try:
        return bool(_get("/health").get("ok"))
    except Exception:
        return False


def _load_data():
    if not _server_up():
        die(f"Mission Control server is not answering on {API}. "
            f"Start it: launchctl load ~/Library/LaunchAgents/com.craft-agent.mission-control.plist")
    return _get("/api/data")


# ── helpers ───────────────────────────────────────────────────────────────────
def die(msg: str, code: int = 1):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def days_since(ms: int, now_ms: int) -> float:
    if not ms:
        return 0.0
    return (now_ms - ms) / 86_400_000


def ws_dir(slug: str) -> Path:
    # guard against traversal; slug must be a real workspace dir
    if "/" in slug or ".." in slug or slug.startswith("."):
        die(f"invalid workspace slug: {slug}")
    d = WORKSPACES_DIR / slug
    if not (d / "config.json").exists():
        die(f"unknown workspace '{slug}' (no config.json under {d})")
    return d


def resolve_uuid(data: dict, slug: str) -> str:
    for w in data["workspaces"]:
        if w["id"] == slug:
            return w.get("wsUuid", "")
    return ""


def open_deeplink(url: str):
    status, resp = _post("/api/open-url", {"url": url})
    if resp.get("ok"):
        print(f"opened: {url}")
    else:
        die(f"deeplink failed ({status}): {resp.get('error')}")


# ── commands ──────────────────────────────────────────────────────────────────
def cmd_list(a):
    data = _load_data()
    now = data["now"]
    rows = []
    for s in data["sessions"]:
        if a.ws and s["wsId"] != a.ws:
            continue
        if a.status and s["status"] != a.status:
            continue
        stale = days_since(s.get("lastUsedAt") or s.get("createdAt"), now)
        if a.stale and stale < a.stale:
            continue
        rows.append({
            "ws": s["wsId"], "id": s["id"], "name": (s.get("name") or "")[:48],
            "status": s["status"], "staleDays": round(stale, 1),
            "cost": round(s.get("cost") or 0, 2), "msgs": s.get("msgs") or 0,
            "labels": s.get("rawLabels") or [],
        })
    rows.sort(key=lambda r: r["staleDays"], reverse=True)
    if a.json:
        print(json.dumps(rows, indent=2))
        return
    if not rows:
        print("(no matching sessions)")
        return
    print(f"{'WORKSPACE':16} {'SESSION ID':22} {'STATUS':13} {'STALE':>6} {'$':>7}  NAME")
    for r in rows:
        print(f"{r['ws']:16.16} {r['id']:22} {r['status']:13.13} "
              f"{r['staleDays']:6.1f} {r['cost']:7.2f}  {r['name']}")
    print(f"\n{len(rows)} session(s)")


def cmd_queue(a):
    data = _load_data()
    now = data["now"]
    lanes = {"needs-review": [], "blocked": [], "stale-important": []}
    for s in data["sessions"]:
        labels = [str(l).lower() for l in (s.get("rawLabels") or [])]
        stale = days_since(s.get("lastUsedAt") or s.get("createdAt"), now)
        entry = {"ws": s["wsId"], "id": s["id"],
                 "name": (s.get("name") or "")[:44], "staleDays": round(stale, 1)}
        if s["status"] == "needs-review":
            lanes["needs-review"].append(entry)
        if any(k in labels for k in ("blocked", "waiting-on", "waiting")):
            lanes["blocked"].append(entry)
        if stale >= 7 and any(k in labels for k in ("priority", "important", "client")):
            lanes["stale-important"].append(entry)
    if a.json:
        print(json.dumps(lanes, indent=2))
        return
    for lane, items in lanes.items():
        print(f"\n### {lane}  ({len(items)})")
        for e in sorted(items, key=lambda x: x["staleDays"], reverse=True)[:20]:
            print(f"  [{e['ws']:14.14}] {e['id']:22} {e['staleDays']:5.1f}d  {e['name']}")


def cmd_show(a):
    data = _load_data()
    for s in data["sessions"]:
        if s["id"] == a.session and s["wsId"] == a.ws:
            print(json.dumps(s, indent=2))
            return
    die(f"session {a.session} not found in workspace {a.ws}")


def cmd_status(a):
    if not a.go:
        print(f"DRY-RUN: would set status of {a.ws}/{a.session} -> '{a.newStatus}' "
              f"(fires {a.ws}'s SessionStatusChange automations). Re-run with --go.")
        return
    status, resp = _post("/api/status",
                         {"sessionId": a.session, "wsDir": a.ws, "newStatus": a.newStatus})
    if resp.get("ok"):
        print(f"status: {a.ws}/{a.session} -> {resp.get('message')}")
    else:
        die(f"status change failed ({status}): {resp.get('error')}")


def cmd_label(a):
    labels = [x.strip() for x in a.labels.split(",") if x.strip()]
    if not a.go:
        print(f"DRY-RUN: would set labels of {a.ws}/{a.session} -> {labels} "
              f"(fires {a.ws}'s LabelAdd/LabelRemove automations). Re-run with --go.")
        return
    status, resp = _post("/api/labels",
                         {"sessionId": a.session, "wsDir": a.ws, "labels": labels})
    if resp.get("ok"):
        print(f"labels: {a.ws}/{a.session} -> {resp.get('labels')}")
    else:
        die(f"label change failed ({status}): {resp.get('error')}")


def cmd_open(a):
    if not SESSION_ID_RE.match(a.session):
        die(f"invalid session id: {a.session}")
    data = _load_data()
    uuid_ = resolve_uuid(data, a.ws)
    url = (f"craftagents://workspace/{uuid_}/allSessions/session/{a.session}"
           if uuid_ else f"craftagents://allSessions/session/{a.session}")
    open_deeplink(url)


def cmd_new(a):
    data = _load_data()
    uuid_ = resolve_uuid(data, a.ws)
    url = (f"craftagents://workspace/{uuid_}/action/new-chat?window=focused"
           if uuid_ else "craftagents://action/new-chat?window=focused")
    open_deeplink(url)


# ── Tier 3: briefed dispatch via one-shot automation ──────────────────────────
def _read_automations(d: Path, tolerant: bool = False) -> dict:
    p = d / "automations.json"
    if p.exists():
        try:
            obj = json.loads(p.read_text())
        except Exception as e:
            if tolerant:
                return {"version": 2, "automations": {}, "_unreadable": True}
            die(f"cannot parse {p}: {e}")
        obj.setdefault("version", 2)
        obj.setdefault("automations", {})
        return obj
    return {"version": 2, "automations": {}}


def _write_automations(d: Path, obj: dict):
    p = d / "automations.json"
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False))
    tmp.replace(p)


def _cron_at(when: str, tz: str) -> tuple[str, datetime]:
    """Return (cron, fire_dt) for an absolute HH:MM or relative +Nm/+Nh."""
    now = datetime.now()
    if when.startswith("+"):
        m = re.match(r"^\+(\d+)([mh])$", when)
        if not m:
            die(f"bad --at '{when}'. Use +5m, +2h, or HH:MM")
        n, unit = int(m.group(1)), m.group(2)
        fire = now + (timedelta(minutes=n) if unit == "m" else timedelta(hours=n))
    else:
        m = re.match(r"^(\d{1,2}):(\d{2})$", when)
        if not m:
            die(f"bad --at '{when}'. Use +5m, +2h, or HH:MM")
        hh, mm = int(m.group(1)), int(m.group(2))
        fire = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if fire <= now:
            fire += timedelta(days=1)
    # minute hour day-of-month month * -> fires once at that stamp (re-fires only
    # next year on the same date; cleanup removes it before then).
    cron = f"{fire.minute} {fire.hour} {fire.day} {fire.month} *"
    return cron, fire


# Appended to every dispatched brief. A cross-workspace agent starts cold and has no way
# to know anyone is waiting on it; without this, finished work sits in `todo` and reads as
# an open loop. `done` is deliberately not offered: an agent cannot set a closed status
# from inside its own session, so it reports and the dispatcher closes.
COMPLETION_FOOTER = """

WHEN YOU ARE DONE
Say so in the session, not just in your files. End by setting your session status to
`needs-review`, whether you finished or got stuck, and say which it was in your last
message. Leave the status alone only while you are still working. Whoever dispatched you
is watching that status, not your transcript."""


def cmd_dispatch(a):
    d = ws_dir(a.ws)
    if not getattr(a, "no_footer", False) and COMPLETION_FOOTER.strip() not in a.prompt:
        a.prompt = a.prompt.rstrip() + COMPLETION_FOOTER
    cron, fire = _cron_at(a.at, a.tz)
    auto_id = DISPATCH_ID_PREFIX + uuid.uuid4().hex[:8]
    name = a.name or f"{DISPATCH_NAME_PREFIX} {a.prompt[:48].strip()}"
    if not name.startswith(DISPATCH_NAME_PREFIX):
        name = f"{DISPATCH_NAME_PREFIX} {name}"
    entry = {
        "name": name,
        "cron": cron,
        "timezone": a.tz,
        "permissionMode": a.mode,
        "labels": [x.strip() for x in (a.labels or "").split(",") if x.strip()] or ["mc-dispatch"],
        "actions": [{"type": "prompt", "prompt": a.prompt}],
        "id": auto_id,
    }
    if a.model:
        entry["model"] = a.model
    if a.conn:
        entry["llmConnection"] = a.conn

    print(f"target workspace : {a.ws}  ({d})")
    print(f"fires (local)    : {fire:%Y-%m-%d %H:%M} {a.tz}   cron: {cron}")
    print(f"automation id    : {auto_id}")
    print(f"permission mode  : {a.mode}")
    print(f"labels           : {entry['labels']}")
    if a.model: print(f"model            : {a.model}")
    if a.conn:  print(f"llm connection   : {a.conn}")
    print("prompt:")
    print("  " + a.prompt.replace("\n", "\n  "))

    if not a.go:
        print("\nDRY-RUN. This writes a one-shot prompt automation that SPAWNS A LIVE "
              f"AGENT in '{a.ws}' at the time above. Re-run with --go to arm it.")
        return

    obj = _read_automations(d)
    obj["automations"].setdefault("SchedulerTick", []).append(entry)
    _write_automations(d, obj)
    print(f"\nARMED. Agent will start in '{a.ws}' at {fire:%H:%M}. "
          f"Remove early with:  mc cleanup --ws {a.ws} --id {auto_id} --go")


def _iter_dispatched(data_or_none, only_ws=None):
    slugs = [only_ws] if only_ws else [
        p.name for p in WORKSPACES_DIR.iterdir()
        if (p / "config.json").exists()
    ]
    for slug in slugs:
        d = WORKSPACES_DIR / slug
        obj = _read_automations(d, tolerant=True)
        for ev, entries in obj.get("automations", {}).items():
            for e in entries:
                if str(e.get("id", "")).startswith(DISPATCH_ID_PREFIX) or \
                   str(e.get("name", "")).startswith(DISPATCH_NAME_PREFIX):
                    yield slug, ev, e


def cmd_dispatched(a):
    found = list(_iter_dispatched(None, a.ws))
    if not found:
        print("(no mc-dispatch automations found)")
        return
    for slug, ev, e in found:
        print(f"[{slug:14.14}] {e.get('id'):12} cron={e.get('cron','?'):16} "
              f"{e.get('name','')[:50]}")
    print(f"\n{len(found)} dispatched automation(s)")


def cmd_cleanup(a):
    targets = [(s, ev, e) for s, ev, e in _iter_dispatched(None, a.ws)
               if not a.id or e.get("id") == a.id]
    if not targets:
        print("(nothing to clean up)")
        return
    for slug, ev, e in targets:
        print(f"{'REMOVE' if a.go else 'DRY-RUN remove'}: [{slug}] {e.get('id')} {e.get('name','')[:40]}")
    if not a.go:
        print("\nRe-run with --go to remove.")
        return
    # apply per workspace
    by_ws = {}
    for slug, ev, e in targets:
        by_ws.setdefault(slug, []).append(e.get("id"))
    for slug, ids in by_ws.items():
        d = WORKSPACES_DIR / slug
        obj = _read_automations(d)
        for ev, entries in list(obj.get("automations", {}).items()):
            obj["automations"][ev] = [x for x in entries if x.get("id") not in ids]
            if not obj["automations"][ev]:
                del obj["automations"][ev]
        _write_automations(d, obj)
    print(f"removed {len(targets)} automation(s)")


# ── argparse ──────────────────────────────────────────────────────────────────
def build_parser():
    p = argparse.ArgumentParser(prog="mc", description="Cross-workspace agent tasking for Craft Agents")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("list", help="list sessions across workspaces")
    s.add_argument("--ws"); s.add_argument("--status"); s.add_argument("--stale", type=float)
    s.add_argument("--lens"); s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("queue", help="attention lanes (needs-review, blocked, stale-important)")
    s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_queue)

    s = sub.add_parser("show", help="show one session header")
    s.add_argument("ws"); s.add_argument("session"); s.set_defaults(fn=cmd_show)

    s = sub.add_parser("status", help="set a session status (Tier 2 trigger)")
    s.add_argument("ws"); s.add_argument("session"); s.add_argument("newStatus")
    s.add_argument("--go", action="store_true"); s.set_defaults(fn=cmd_status)

    s = sub.add_parser("label", help="set session labels (Tier 2 trigger)")
    s.add_argument("ws"); s.add_argument("session"); s.add_argument("labels")
    s.add_argument("--go", action="store_true"); s.set_defaults(fn=cmd_label)

    s = sub.add_parser("open", help="open an existing session in the app (deeplink)")
    s.add_argument("ws"); s.add_argument("session"); s.set_defaults(fn=cmd_open)

    s = sub.add_parser("new", help="open a new blank chat in a workspace (deeplink)")
    s.add_argument("ws"); s.set_defaults(fn=cmd_new)

    s = sub.add_parser("dispatch", help="brief-and-run an agent in a workspace (Tier 3)")
    s.add_argument("ws")
    s.add_argument("--prompt", required=True)
    s.add_argument("--at", default="+2m", help="+5m | +2h | HH:MM (default +2m)")
    s.add_argument("--labels", default="")
    s.add_argument("--mode", default="allow-all",
                   choices=["allow-all", "plan", "default", "ask", "acceptEdits"])
    s.add_argument("--model"); s.add_argument("--conn")
    s.add_argument("--name"); s.add_argument("--tz", default=DEFAULT_TZ)
    s.add_argument("--no-footer", dest="no_footer", action="store_true",
                   help="omit the 'set your session status when done' footer")
    s.add_argument("--go", action="store_true"); s.set_defaults(fn=cmd_dispatch)

    s = sub.add_parser("dispatched", help="list mc-injected one-shot automations")
    s.add_argument("--ws"); s.set_defaults(fn=cmd_dispatched)

    s = sub.add_parser("cleanup", help="remove mc-injected automations")
    s.add_argument("--ws"); s.add_argument("--id")
    s.add_argument("--go", action="store_true"); s.set_defaults(fn=cmd_cleanup)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
