#!/usr/bin/env python3
"""
Mission Control installer (cross-platform).

Installs the skill to ~/.agents/skills/mission-control/ and sets up
auto-start on login:

  macOS   → ~/Library/LaunchAgents/com.craft-agent.mission-control.plist
  Linux   → ~/.config/systemd/user/mission-control.service
  Windows → Scheduled Task "CraftAgentMissionControl" (user, on-logon)

Usage:
  python3 install.py                 # interactive
  python3 install.py --yes           # non-interactive
  python3 install.py --no-autostart  # skip autostart setup
  python3 install.py --port 9753     # pick a port (default 9753)
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


# ─── Config ──────────────────────────────────────────────────────────────────

REPO_URL = "https://github.com/CS-Workx/craft-agent-mission-control"
SKILL_DIR_DEFAULT = Path.home() / ".agents" / "skills" / "mission-control"
DEFAULT_PORT = 9753

# Files copied into SKILL_DIR. The setup/ folder is copied wholesale.
SKILL_FILES = [
    "dashboard.py",
    "SKILL.md",
    "icon.svg",
    "README.md",
    "LICENSE",
    "CHANGELOG.md",
    "INSTALL.md",
    "SECURITY.md",
    "ROADMAP.md",
    "CONTRIBUTING.md",
    "install.sh",
    "install.ps1",
    "install.py",
    "uninstall.sh",
    "uninstall.ps1",
    "uninstall.py",
]

# ─── Output helpers ──────────────────────────────────────────────────────────

def _tty():
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None

def info(msg: str) -> None:
    prefix = "\033[1;34m[install]\033[0m " if _tty() else "[install] "
    print(f"{prefix}{msg}")

def warn(msg: str) -> None:
    prefix = "\033[1;33m[warn]\033[0m " if _tty() else "[warn] "
    print(f"{prefix}{msg}", file=sys.stderr)

def fail(msg: str) -> None:
    prefix = "\033[1;31m[error]\033[0m " if _tty() else "[error] "
    print(f"{prefix}{msg}", file=sys.stderr)
    sys.exit(1)

def success(msg: str) -> None:
    prefix = "\033[1;32m✓\033[0m " if _tty() else "✓ "
    print(f"\n{prefix}{msg}\n")


# ─── File install ────────────────────────────────────────────────────────────

def copy_skill_files(script_dir: Path, skill_dir: Path) -> None:
    info(f"Creating {skill_dir}")
    skill_dir.mkdir(parents=True, exist_ok=True)

    if script_dir.resolve() == skill_dir.resolve():
        info("Source and target are the same directory — skipping file copy")
        return

    for name in SKILL_FILES:
        src = script_dir / name
        if not src.exists():
            warn(f"  skipping missing file: {name}")
            continue
        dst = skill_dir / name
        shutil.copy2(src, dst)
        info(f"  copied {name}")

    # Copy setup/ wholesale so uninstall / reinstall can find the plist + service.
    setup_src = script_dir / "setup"
    setup_dst = skill_dir / "setup"
    if setup_src.is_dir():
        setup_dst.mkdir(exist_ok=True)
        for child in setup_src.iterdir():
            shutil.copy2(child, setup_dst / child.name)
        info("  copied setup/")

    # Make shell wrappers executable where it matters.
    for sh in ("install.sh", "uninstall.sh"):
        p = skill_dir / sh
        if p.exists():
            try:
                p.chmod(p.stat().st_mode | 0o111)
            except OSError:
                pass


# ─── Autostart: macOS ────────────────────────────────────────────────────────

def install_macos_plist(script_dir: Path, skill_dir: Path, port: int) -> None:
    info("Installing macOS Launch Agent")
    src = script_dir / "setup" / "com.craft-agent.mission-control.plist"
    if not src.exists():
        src = skill_dir / "setup" / "com.craft-agent.mission-control.plist"
    if not src.exists():
        fail(f"Missing plist template: {src}")

    launch_agents = Path.home() / "Library" / "LaunchAgents"
    launch_agents.mkdir(parents=True, exist_ok=True)
    dst = launch_agents / "com.craft-agent.mission-control.plist"

    log_path = Path(tempfile.gettempdir()) / "mission-control.log"
    rendered = (src.read_text()
                .replace("/Users/YOUR_USERNAME/", f"{Path.home()}/")
                .replace("{PORT}", str(port))
                .replace("{LOG_PATH}", str(log_path)))
    dst.write_text(rendered)

    # Reload cleanly: unload first (ignore errors), then load.
    subprocess.run(["launchctl", "unload", str(dst)], capture_output=True)
    time.sleep(1)
    result = subprocess.run(["launchctl", "load", str(dst)], capture_output=True)
    if result.returncode != 0:
        warn(f"launchctl load returned {result.returncode}; checking health anyway")


def install_linux_systemd(script_dir: Path, skill_dir: Path, port: int) -> None:
    info("Installing Linux systemd user unit")
    src = script_dir / "setup" / "mission-control.service"
    if not src.exists():
        src = skill_dir / "setup" / "mission-control.service"
    if not src.exists():
        fail(f"Missing unit template: {src}")

    python = shutil.which("python3") or "/usr/bin/python3"
    dashboard = skill_dir / "dashboard.py"
    log_dir = Path.home() / ".local" / "state" / "mission-control"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "mission-control.log"

    rendered = (src.read_text()
                .replace("{PYTHON}", python)
                .replace("{DASHBOARD}", str(dashboard))
                .replace("{PORT}", str(port))
                .replace("{LOG_PATH}", str(log_path)))

    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    dst = unit_dir / "mission-control.service"
    dst.write_text(rendered)

    if not shutil.which("systemctl"):
        warn("systemctl not found; unit written but not activated")
        return

    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    result = subprocess.run(
        ["systemctl", "--user", "enable", "--now", "mission-control.service"],
        capture_output=True,
    )
    if result.returncode != 0:
        warn(f"systemctl enable --now returned {result.returncode}")
        if result.stderr:
            warn(result.stderr.decode(errors="replace").strip())

    info("Tip: run `loginctl enable-linger $USER` to keep the service running after logout")


def install_windows_task(script_dir: Path, skill_dir: Path, port: int) -> None:
    info("Installing Windows scheduled task 'CraftAgentMissionControl'")
    python = shutil.which("pythonw.exe") or shutil.which("pythonw") or shutil.which("python.exe") or shutil.which("python")
    if not python:
        fail("Could not locate pythonw.exe or python.exe on PATH")
    dashboard = skill_dir / "dashboard.py"
    if not dashboard.exists():
        fail(f"Missing dashboard.py at {dashboard}")

    task_name = "CraftAgentMissionControl"
    # Action: `"C:\...\pythonw.exe" "C:\...\dashboard.py" --serve 9753`
    action = f'"{python}" "{dashboard}" --serve {port}'

    # /Create /F overwrites any existing task with the same name.
    cmd = [
        "schtasks.exe", "/Create", "/F",
        "/TN", task_name,
        "/SC", "ONLOGON",
        "/RL", "LIMITED",
        "/TR", action,
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        warn(f"schtasks /Create returned {result.returncode}")
        if result.stderr:
            warn(result.stderr.decode(errors="replace").strip())
        return

    # Run it immediately too — ONLOGON only fires at next login.
    subprocess.run(["schtasks.exe", "/Run", "/TN", task_name], capture_output=True)


# ─── Health check ────────────────────────────────────────────────────────────

def wait_for_health(port: int, timeout: int = 10):
    url = f"http://127.0.0.1:{port}/health"
    for _ in range(timeout):
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                return json.loads(r.read().decode())
        except (urllib.error.URLError, ConnectionError, OSError, ValueError):
            time.sleep(1)
    return None


# ─── Main ────────────────────────────────────────────────────────────────────

def confirm(prompt: str, auto_yes: bool) -> bool:
    if auto_yes:
        return True
    reply = input(f"{prompt} [y/N] ").strip().lower()
    return reply in ("y", "yes")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Install Mission Control and set up auto-start on login.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--yes", "-y", action="store_true",
                        help="non-interactive (overwrite existing install without prompting)")
    parser.add_argument("--no-autostart", action="store_true",
                        help="skip per-OS autostart setup")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"port for the dashboard server (default: {DEFAULT_PORT})")
    parser.add_argument("--skill-dir", type=Path, default=SKILL_DIR_DEFAULT,
                        help=f"install target (default: {SKILL_DIR_DEFAULT})")
    args = parser.parse_args()

    info("Mission Control installer")
    info(f"Platform:    {platform.platform()}")
    info(f"Python:      {sys.version_info.major}.{sys.version_info.minor}")
    info(f"Target:      {args.skill_dir}")

    if sys.version_info < (3, 9):
        fail(f"Python 3.9+ required (found {sys.version_info.major}.{sys.version_info.minor})")

    script_dir = Path(__file__).resolve().parent

    # Preflight: confirm source files exist.
    missing = [f for f in ("dashboard.py", "SKILL.md") if not (script_dir / f).exists()]
    if missing:
        fail(f"Missing source files in {script_dir}: {', '.join(missing)}")

    # Existing install check.
    if args.skill_dir.exists():
        warn(f"Existing installation found at {args.skill_dir}")
        if not confirm("Overwrite?", args.yes):
            fail("Aborted by user.")

    # Copy files.
    copy_skill_files(script_dir, args.skill_dir)

    # Autostart.
    if not args.no_autostart:
        try:
            if sys.platform == "darwin":
                install_macos_plist(script_dir, args.skill_dir, args.port)
            elif sys.platform.startswith("linux"):
                install_linux_systemd(script_dir, args.skill_dir, args.port)
            elif sys.platform == "win32":
                install_windows_task(script_dir, args.skill_dir, args.port)
            else:
                warn(f"Unsupported platform {sys.platform!r} — skipping autostart")
                warn(f"Start manually: python3 {args.skill_dir / 'dashboard.py'} --serve {args.port}")
        except subprocess.CalledProcessError as e:
            warn(f"Autostart command failed: {e}")

        info("Waiting for server to respond on /health ...")
        health = wait_for_health(args.port, timeout=10)
        if health:
            info(f"Server up: version {health.get('version', '?')}")
        else:
            warn("Health check did not respond after 10s — the server may start shortly")
            warn(f"Start manually if needed: python3 {args.skill_dir / 'dashboard.py'} --serve {args.port}")

    success("Mission Control installed.")
    print(f"  Location:    {args.skill_dir}")
    print(f"  Dashboard:   http://localhost:{args.port}")
    print(f"  Uninstall:   python3 {args.skill_dir / 'uninstall.py'}")
    print(f"  Docs:        {REPO_URL}\n")
    print("From any Craft Agents session, invoke with: [skill:mission-control]")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
