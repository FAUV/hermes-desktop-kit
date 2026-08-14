"""Shared fixtures for the time-machine backend test suite.

The tests import the ``tm`` package from the plugin's dashboard dir; the
web_server loader contract (sys.path self-registration) is covered by
test_plugin_api_loader_contract.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
DASHBOARD = HERE.parent / "dashboard"
if str(DASHBOARD) not in sys.path:
    sys.path.insert(0, str(DASHBOARD))


@pytest.fixture
def hermes_home(tmp_path):
    """A scratch HERMES_HOME (default profile root)."""
    home = tmp_path / "hermes-home"
    home.mkdir()
    return home


@pytest.fixture
def profile_fixture(hermes_home):
    """A known human-maintained configuration tree, including excluded
    surfaces that must NEVER be captured (secrets, runtime DBs, transcripts,
    logs, dependency trees)."""
    root = hermes_home
    (root / "config.yaml").write_text("model: default\nprovider: openrouter\n")
    (root / "SOUL.md").write_text("# SOUL\nOriginal soul text.\n")
    (root / "MEMORY.md").write_text("# Memory\nremember this\n")

    (root / "skills" / "my-skill").mkdir(parents=True)
    (root / "skills" / "my-skill" / "SKILL.md").write_text(
        "---\nname: my-skill\n---\nBody v1\n"
    )
    (root / "cron").mkdir()
    (root / "cron" / "jobs.json").write_text('{"jobs": [{"id": "j1"}]}\n')
    (root / "scripts").mkdir()
    (root / "scripts" / "daily.sh").write_text("#!/bin/sh\necho hi\n")
    (root / "desktop-plugins").mkdir()
    (root / "desktop-plugins" / "other").mkdir()
    (root / "desktop-plugins" / "other" / "plugin.js").write_text("export default {}\n")

    # Binary/unsupported file — metadata-only in diffs. Lives inside a
    # tracked top-level dir (top-level allow-list only covers the export
    # catalog entries, so "assets" at root is not tracked).
    (root / "skills" / "my-skill" / "logo.bin").write_bytes(
        b"\x00\x01\x02binary\xff\xfe"
    )

    # Excluded surfaces that must never be captured or restored:
    (root / ".env").write_text("OPENAI_API_KEY=sk-secret123\n")
    (root / "auth.json").write_text('{"token": "secret"}\n')
    (root / "state.db").write_bytes(b"sqlite-bytes")
    (root / "sessions").mkdir()
    (root / "sessions" / "last.json").write_text('{"transcript": "secret"}\n')
    (root / "node_modules").mkdir()
    (root / "node_modules" / "pkg").write_text("dep\n")
    (root / "logs").mkdir()
    (root / "logs" / "agent.log").write_text("log line\n")
    return root


@pytest.fixture
def store_and_catalog(hermes_home):
    from tm.catalog import Catalog
    from tm.store import Store

    store = Store(hermes_home)
    catalog = Catalog(hermes_home, store_root=store.root)
    return store, catalog
