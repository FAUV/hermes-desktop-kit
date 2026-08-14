"""Diff engine tests: created/modified/deleted semantics, text diffs,
binary metadata-only, secret-value redaction, and cap behavior."""

from __future__ import annotations

from tm.diff import diff_checkpoint
from tm.snapshot import create_checkpoint


def _checkpoint(store, catalog, profile_fixture, label):
    return create_checkpoint(store, catalog, "default", "manual", label=label)


def test_diff_against_adjacent(profile_fixture, store_and_catalog):
    store, catalog = store_and_catalog
    _checkpoint(store, catalog, profile_fixture, "A")
    (profile_fixture / "config.yaml").write_text("model: changed\n")
    (profile_fixture / "scripts" / "new.sh").write_text("#!/bin/sh\necho new\n")
    (profile_fixture / "scripts" / "daily.sh").unlink()
    b = _checkpoint(store, catalog, profile_fixture, "B")

    diff = diff_checkpoint(store, catalog, "default", b["id"], against="adjacent")
    assert diff["summary"] == {"created": 1, "modified": 1, "deleted": 1}
    by_path = {i["path"]: i for i in diff["items"]}
    assert by_path["config.yaml"]["action"] == "modified"
    assert "text_diff" in by_path["config.yaml"]
    assert "model: changed" in by_path["config.yaml"]["text_diff"]
    assert by_path["scripts/new.sh"]["action"] == "created"
    assert by_path["scripts/daily.sh"]["action"] == "deleted"


def test_diff_against_current(profile_fixture, store_and_catalog):
    store, catalog = store_and_catalog
    a = _checkpoint(store, catalog, profile_fixture, "A")
    # Live state diverges from the checkpoint.
    (profile_fixture / "config.yaml").write_text("model: live-now\n")
    (profile_fixture / "scripts" / "daily.sh").unlink()

    diff = diff_checkpoint(store, catalog, "default", a["id"], against="current")
    assert diff["against"] == "current"
    by_path = {i["path"]: i for i in diff["items"]}
    assert by_path["config.yaml"]["action"] == "modified"
    assert by_path["config.yaml"]["text_diff"]
    assert by_path["scripts/daily.sh"]["action"] == "deleted"


def test_diff_identical_returns_empty(profile_fixture, store_and_catalog):
    store, catalog = store_and_catalog
    a = _checkpoint(store, catalog, profile_fixture, "A")
    diff = diff_checkpoint(store, catalog, "default", a["id"], against="current")
    assert diff["items"] == []
    assert diff["summary"] == {"created": 0, "modified": 0, "deleted": 0}


def test_binary_metadata_only(profile_fixture, store_and_catalog):
    store, catalog = store_and_catalog
    a = _checkpoint(store, catalog, profile_fixture, "A")
    (profile_fixture / "skills" / "my-skill" / "logo.bin").write_bytes(
        b"\x00\xffother-bytes"
    )
    diff = diff_checkpoint(store, catalog, "default", a["id"], against="current")
    entry = next(i for i in diff["items"] if i["path"] == "skills/my-skill/logo.bin")
    assert entry["kind"] == "binary"
    assert "text_diff" not in entry  # honest metadata-only, never a fake diff
    assert entry["old"]["size"] == 11
    assert entry["new"]["size"] == 13


def test_secret_values_redacted_in_diff(profile_fixture, store_and_catalog):
    store, catalog = store_and_catalog
    _checkpoint(store, catalog, profile_fixture, "A")
    # A secret-shaped value lands inside an otherwise tracked file.
    (profile_fixture / "config.yaml").write_text(
        "model: default\napi_key = sk-1234567890abcdefghij\n"
    )
    diff = diff_checkpoint(store, catalog, "default", _latest(store), against="current")
    text = "\n".join((i.get("text_diff") or "") for i in diff["items"])
    assert "sk-1234567890abcdefghij" not in text
    assert "[REDACTED]" in text


def _latest(store):
    return store.latest_checkpoint("default")["id"]
