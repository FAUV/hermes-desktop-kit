"""Catalog tests: include allow-list, exclusion catalog, depth awareness,
symlink skipping, and self-exclusion of Time Machine's own storage."""

from __future__ import annotations

import pytest
from tm.catalog import TRACKED_TOP, Catalog
from tm.security import PathTraversalError, should_exclude


def test_tracked_top_matches_upstream_export_catalog():
    # Upstream _DEFAULT_EXPORT_INCLUDE_ROOT minus "sessions" (transcripts),
    # plus desktop-plugins (parent explicitly wants Desktop plugin source).
    expected = {
        "config.yaml",
        "SOUL.md",
        "MEMORY.md",
        "USER.md",
        "todo.json",
        "system_prompt.md",
        "AGENTS.md",
        "CLAUDE.md",
        ".cursorrules",
        "desktop.json",
        "skills",
        "cron",
        "scripts",
        "plugins",
        "memories",
        "knowledge",
        "preferences",
        "desktop-plugins",
    }
    assert TRACKED_TOP == expected
    assert "sessions" not in TRACKED_TOP


def test_walk_captures_tracked_files_and_skips_excluded(
    profile_fixture, store_and_catalog
):
    _, catalog = store_and_catalog
    rels = {"/".join(rel) for rel, _ in catalog.walk(profile_fixture)}

    assert "config.yaml" in rels
    assert "SOUL.md" in rels
    assert "MEMORY.md" in rels
    assert "skills/my-skill/SKILL.md" in rels
    assert "cron/jobs.json" in rels
    assert "scripts/daily.sh" in rels
    assert "desktop-plugins/other/plugin.js" in rels
    assert "skills/my-skill/logo.bin" in rels

    # Excluded surfaces never captured:
    assert ".env" not in rels
    assert "auth.json" not in rels
    assert "state.db" not in rels
    assert not any(r.startswith("sessions/") for r in rels)
    assert not any(r.startswith("node_modules/") for r in rels)
    assert not any(r.startswith("logs/") for r in rels)


def test_depth_aware_exclusions():
    # Root-only: hermes-agent + profiles at depth 0.
    assert should_exclude(("hermes-agent",), False)
    assert should_exclude(("profiles",), True)
    # Nested copies preserved (a skill directory named hermes-agent).
    assert not should_exclude(("skills", "hermes-agent", "SKILL.md"), False)
    assert not should_exclude(("skills", "profiles", "x.md"), False)
    # Any-depth exclusions (checked on the directory entry, which prunes the
    # whole subtree before files beneath it are considered).
    assert should_exclude(("skills", ".git"), True)
    assert should_exclude(("cron", "executions.db"), False)
    assert should_exclude(("plugins", "__pycache__"), True)
    assert should_exclude(("skills", "node_modules"), True)
    assert should_exclude(("memories", ".env.local"), False)
    assert should_exclude(("plugins", "auth.json"), False)
    assert should_exclude(("scripts", "id_rsa"), False)
    assert should_exclude(("knowledge", "tokens.yaml"), False)


def test_walk_skips_symlinks(profile_fixture, store_and_catalog, tmp_path):
    _, catalog = store_and_catalog
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "evil.txt").write_text("evil")
    try:
        (profile_fixture / "skills" / "link").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable on this platform")
    rels = {"/".join(rel) for rel, _ in catalog.walk(profile_fixture)}
    assert not any(r.startswith("skills/link") for r in rels)


def test_self_excludes_never_capture_store_or_plugin(
    profile_fixture, store_and_catalog
):
    store, catalog = store_and_catalog
    # Store lives inside the default profile root — must never be captured.
    store.write_manifest("default", {"id": "20260101-000000", "files": {}})
    (store.root / "objects" / "ab").mkdir(parents=True)
    (store.root / "objects" / "ab" / "cd").mkdir()
    (store.root / "objects" / "ab" / "cd" / "deadbeef").write_bytes(b"x")

    # Plugin's own implementation dirs must never be captured.
    (profile_fixture / "plugins" / "time-machine").mkdir(parents=True)
    (profile_fixture / "plugins" / "time-machine" / "plugin_api.py").write_text("x")
    (profile_fixture / "desktop-plugins" / "time-machine").mkdir(parents=True)
    (profile_fixture / "desktop-plugins" / "time-machine" / "plugin.js").write_text("x")

    rels = {"/".join(rel) for rel, _ in catalog.walk(profile_fixture)}
    assert not any(r.startswith("time-machine/") for r in rels)
    assert not any(r.startswith("plugins/time-machine") for r in rels)
    assert not any(r.startswith("desktop-plugins/time-machine") for r in rels)


def test_profile_roots(hermes_home):
    # Named profile roots come from <home>/profiles/<name>.
    (hermes_home / "profiles" / "work").mkdir(parents=True)
    catalog = Catalog(hermes_home)
    assert catalog.profile_ids() == ["default", "work"]
    assert catalog.profile_root("default") == hermes_home.resolve()
    assert catalog.profile_root("work") == (hermes_home / "profiles" / "work").resolve()
    assert catalog.profile_root("nope") is None
    assert catalog.profile_root("../evil") is None
    assert catalog.profile_root("a/b") is None


def test_resolve_rejects_traversal_and_absolutes(store_and_catalog):
    _, catalog = store_and_catalog
    with pytest.raises(PathTraversalError):
        catalog.resolve("default", "../../etc/passwd")
    with pytest.raises(PathTraversalError):
        catalog.resolve("default", "/etc/passwd")
    with pytest.raises(PathTraversalError):
        catalog.resolve("default", "skills/../..")
    with pytest.raises(PathTraversalError):
        catalog.resolve("default", "config.yaml\x00x")
    # Legit tracked paths resolve inside the root.
    ok = catalog.resolve("default", "skills/my-skill/SKILL.md")
    assert str(ok).startswith(str(catalog.profile_root("default")))
