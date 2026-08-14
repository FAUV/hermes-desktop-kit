"""Snapshot tests: capture, dedup, adjacent summaries, and the debounced
auto-checkpoint watcher."""

from __future__ import annotations

from tm.snapshot import AutoWatcher, ProfileNotFoundError, create_checkpoint


def test_manual_checkpoint_captures_tracked_state(profile_fixture, store_and_catalog):
    store, catalog = store_and_catalog
    manifest = create_checkpoint(store, catalog, "default", "manual", label="first")
    assert manifest is not None
    assert manifest["trigger"] == "manual"
    assert manifest["label"] == "first"
    assert "config.yaml" in manifest["files"]
    assert "skills/my-skill/SKILL.md" in manifest["files"]
    assert "skills/my-skill/logo.bin" in manifest["files"]
    # Excluded surfaces never captured:
    assert ".env" not in manifest["files"]
    assert "auth.json" not in manifest["files"]
    assert not any(k.startswith("sessions/") for k in manifest["files"])
    assert not any(k.startswith("node_modules/") for k in manifest["files"])
    # File content is content-addressed in the store.
    sha = manifest["files"]["config.yaml"]["sha256"]
    assert store.read_object(sha) == b"model: default\nprovider: openrouter\n"


def test_auto_checkpoint_dedup_identical(profile_fixture, store_and_catalog):
    store, catalog = store_and_catalog
    first = create_checkpoint(store, catalog, "default", "manual", label="first")
    assert first is not None
    # Identical content → auto checkpoint is suppressed (no spam).
    second = create_checkpoint(store, catalog, "default", "auto")
    assert second is None
    assert len(store.list_manifests("default")) == 1
    # Manual is never deduplicated away.
    third = create_checkpoint(store, catalog, "default", "manual", label="again")
    assert third is not None
    assert len(store.list_manifests("default")) == 2


def test_adjacent_summary(profile_fixture, store_and_catalog):
    store, catalog = store_and_catalog
    a = create_checkpoint(store, catalog, "default", "manual", label="A")
    (profile_fixture / "config.yaml").write_text("model: changed\n")
    (profile_fixture / "scripts" / "new.sh").write_text("#!/bin/sh\necho new\n")
    (profile_fixture / "scripts" / "daily.sh").unlink()
    b = create_checkpoint(store, catalog, "default", "manual", label="B")
    assert b["prev_id"] == a["id"]
    assert b["summary"]["modified"] == ["config.yaml"]
    assert b["summary"]["created"] == ["scripts/new.sh"]
    assert b["summary"]["deleted"] == ["scripts/daily.sh"]


def test_unknown_profile(store_and_catalog):
    store, catalog = store_and_catalog
    try:
        create_checkpoint(store, catalog, "ghost", "manual")
        raise AssertionError("expected ProfileNotFoundError")
    except ProfileNotFoundError:
        pass


def test_watcher_baseline_then_auto(profile_fixture, store_and_catalog):
    store, catalog = store_and_catalog
    watcher = AutoWatcher(store, catalog, poll_s=30, debounce_s=0)
    # First tick: baseline only, no checkpoint spam.
    watcher._tick()
    assert store.list_manifests("default") == []
    # Change → auto checkpoint created immediately (debounce 0).
    (profile_fixture / "config.yaml").write_text("model: changed\n")
    watcher._tick()
    assert len(store.list_manifests("default")) == 1
    # No further change → no new checkpoint.
    watcher._tick()
    assert len(store.list_manifests("default")) == 1


def test_watcher_debounce_coalesces_and_reverts(
    profile_fixture, store_and_catalog, monkeypatch
):
    store, catalog = store_and_catalog

    # Controllable clock so the debounce window can be advanced deterministically.
    class FakeClock:
        def __init__(self):
            self.t = 1000.0

        def __call__(self):
            return self.t

    clock = FakeClock()
    monkeypatch.setattr("tm.snapshot.time.time", clock)
    watcher = AutoWatcher(store, catalog, poll_s=30, debounce_s=60)
    watcher._tick()  # baseline
    original = (profile_fixture / "config.yaml").read_text()

    # Change inside the debounce window → pending, no checkpoint yet.
    (profile_fixture / "config.yaml").write_text("model: changed\n")
    watcher._tick()
    assert store.list_manifests("default") == []
    # Still inside the window on the next tick — coalesced, not spammed.
    watcher._tick()
    assert store.list_manifests("default") == []

    # Debounce elapses → exactly ONE checkpoint captures the burst.
    clock.t += 61
    watcher._tick()
    assert len(store.list_manifests("default")) == 1

    # Stable afterwards → no more checkpoints.
    watcher._tick()
    assert len(store.list_manifests("default")) == 1

    # A later revert is itself a change → one checkpoint, then stable.
    (profile_fixture / "config.yaml").write_text(original)
    watcher._tick()  # change seen → pending (debounce restarts)
    assert len(store.list_manifests("default")) == 1
    clock.t += 61
    watcher._tick()
    assert len(store.list_manifests("default")) == 2
    watcher._tick()
    assert len(store.list_manifests("default")) == 2
