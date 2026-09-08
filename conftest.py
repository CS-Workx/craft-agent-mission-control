"""Shared pytest configuration and fixtures for Mission Control.

Both ``dashboard.py`` and ``mc.py`` compute ``CRAFT_DIR`` / ``WORKSPACES_DIR`` at
import time from the ``CRAFT_HOME`` environment variable and cache them as module
globals. Tests therefore isolate the filesystem by monkeypatching those globals
(and resetting the module-level caches) rather than relying on the env var, so
the suite runs correctly regardless of import order.
"""

import sys
from pathlib import Path

import pytest

# Make the repo-root modules (dashboard.py, mc.py) importable from tests/.
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def craft_home(tmp_path, monkeypatch):
    """Point dashboard.py at a throwaway ~/.craft-agent tree under tmp_path.

    Returns the fake CRAFT_HOME root (``tmp_path``). ``tmp_path/workspaces`` is
    created; ``tmp_path/workspaces/.mission-control`` is created on demand by the
    code under test (history snapshots, saved lenses).
    """
    import dashboard

    craft_dir = tmp_path
    workspaces = craft_dir / "workspaces"
    workspaces.mkdir()

    monkeypatch.setattr(dashboard, "CRAFT_DIR", craft_dir)
    monkeypatch.setattr(dashboard, "WORKSPACES_DIR", workspaces)
    # Reset caches so state can't leak between tests.
    monkeypatch.setattr(dashboard, "_COLLECT_CACHE", {})
    monkeypatch.setattr(dashboard, "_HISTORY_CACHE", None)

    return craft_dir


@pytest.fixture
def mc_home(tmp_path, monkeypatch):
    """Point mc.py at a throwaway ~/.craft-agent tree under tmp_path.

    Returns a small handle with ``.workspaces`` (the Path) and a ``make_ws(slug)``
    helper that creates a workspace directory with a minimal ``config.json`` so
    ``mc.ws_dir()`` accepts it.
    """
    import mc

    craft = tmp_path
    workspaces = craft / "workspaces"
    workspaces.mkdir()

    monkeypatch.setattr(mc, "CRAFT_HOME", craft)
    monkeypatch.setattr(mc, "WORKSPACES_DIR", workspaces)

    class Handle:
        def __init__(self, ws):
            self.workspaces = ws

        def make_ws(self, slug):
            d = self.workspaces / slug
            d.mkdir(parents=True, exist_ok=True)
            (d / "config.json").write_text("{}")
            return d

    return Handle(workspaces)
