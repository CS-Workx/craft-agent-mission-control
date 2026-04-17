#!/usr/bin/env python3
"""
Mission Control uninstaller (cross-platform).

Reverses install.py:

  macOS   → launchctl unload + remove plist
  Linux   → systemctl --user disable --now + remove unit
  Windows → schtasks /Delete

Then removes the skill directory and the log file.

Usage:
  python3 uninstall.py             # interactive
  python3 uninstall.py --yes       # non-interactive
  python3 uninstall.py --dry-run   # show what would be removed
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


SKILL_DIR_DEFAULT = Path.home() / ".agents" / "skills" / "mission-control"
PLIST_DST = Path.home() / "Library" / "LaunchAgents" / "com.craft-agent.mission-control.plist"
LINUX_UNIT = Path.home() / ".config" / "systemd" / "user" / "mission-control.service"
LINUX_LOG_DIR = Path.home() / ".local" / "state" / "mission-control"
MACOS_LOG = Path(tempfile.gettempdir()) / "mission-control.log"
WIN_TASK_NAME = "CraftAgentMissionControl"


def _tty():
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None

def info(msg: str) -> None:
    prefix = "\033[1;34m[uninstall]\033[0m " if _tty() else "[uninstall] "
    print(f"{prefix}{msg}")

def warn(msg: str) -> None:
    prefix = "\033[1;33m[warn]\033[0m " if _tty() else "[warn] "
    print(f"{prefix}{msg}", file=sys.stderr)

def success(msg: str) -> None:
    prefix = "\033[1;32m✓\033[0m " if _tty() else "✓ "
    print(f"\n{prefix}{msg}\n")


def confirm(prompt: str, auto_yes: bool) -> bool:
    if auto_yes:
        return True
    reply = input(f"{prompt} [y/N] ").strip().lower()
    return reply in ("y", "yes")


def uninstall_macos(dry_run: bool) -> None:
    if PLIST_DST.exists():
        info(f"Unloading Launch Agent")
        if not dry_run:
            subprocess.run(["launchctl", "unload", str(PLIST_DST)], capture_output=True)
            try:
                PLIST_DST.unlink()
            except FileNotFoundError:
                pass
        info(f"  removed {PLIST_DST}")
    if MACOS_LOG.exists():
        info(f"Removing log {MACOS_LOG}")
        if not dry_run:
            try:
                MACOS_LOG.unlink()
            except FileNotFoundError:
                pass


def uninstall_linux(dry_run: bool) -> None:
    if shutil.which("systemctl"):
        info("Disabling systemd user unit")
        if not dry_run:
            subprocess.run(["systemctl", "--user", "disable", "--now", "mission-control.service"],
                           capture_output=True)
    if LINUX_UNIT.exists():
        info(f"Removing {LINUX_UNIT}")
        if not dry_run:
            try:
                LINUX_UNIT.unlink()
            except FileNotFoundError:
                pass
            if shutil.which("systemctl"):
                subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
    if LINUX_LOG_DIR.exists():
        info(f"Removing log dir {LINUX_LOG_DIR}")
        if not dry_run:
            shutil.rmtree(LINUX_LOG_DIR, ignore_errors=True)


def uninstall_windows(dry_run: bool) -> None:
    info(f"Removing scheduled task '{WIN_TASK_NAME}'")
    if not dry_run:
        subprocess.run(["schtasks.exe", "/Delete", "/F", "/TN", WIN_TASK_NAME],
                       capture_output=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Remove Mission Control and its autostart entries.",
    )
    parser.add_argument("--yes", "-y", action="store_true",
                        help="non-interactive (do not prompt)")
    parser.add_argument("--dry-run", action="store_true",
                        help="show what would be removed, don't remove")
    parser.add_argument("--skill-dir", type=Path, default=SKILL_DIR_DEFAULT,
                        help=f"install target to remove (default: {SKILL_DIR_DEFAULT})")
    args = parser.parse_args()

    info("The following will be removed:")
    if args.skill_dir.exists():
        print(f"  - directory: {args.skill_dir}")
    if sys.platform == "darwin" and PLIST_DST.exists():
        print(f"  - plist:     {PLIST_DST}")
    if sys.platform == "darwin" and MACOS_LOG.exists():
        print(f"  - log:       {MACOS_LOG}")
    if sys.platform.startswith("linux") and LINUX_UNIT.exists():
        print(f"  - unit:      {LINUX_UNIT}")
    if sys.platform.startswith("linux") and LINUX_LOG_DIR.exists():
        print(f"  - log dir:   {LINUX_LOG_DIR}")
    if sys.platform == "win32":
        print(f"  - task:      {WIN_TASK_NAME}")

    if args.dry_run:
        info("Dry run — nothing removed.")
        return 0

    if not confirm("Proceed with uninstall?", args.yes):
        info("Aborted.")
        return 0

    if sys.platform == "darwin":
        uninstall_macos(args.dry_run)
    elif sys.platform.startswith("linux"):
        uninstall_linux(args.dry_run)
    elif sys.platform == "win32":
        uninstall_windows(args.dry_run)
    else:
        warn(f"Unsupported platform {sys.platform!r} — skipping autostart teardown")

    if args.skill_dir.exists():
        info(f"Removing {args.skill_dir}")
        shutil.rmtree(args.skill_dir, ignore_errors=True)

    success("Mission Control uninstalled.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
