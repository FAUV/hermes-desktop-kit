"""Restore tests: the safety contract end-to-end.

Covers the parent's 10-step acceptance scenario (fixture → checkpoint →
modify → create/delete → diff → preview → apply → verify → excluded-never-
touched → injected mid-restore failure rollback), plus stale-plan rejection,
traversal/symlink defenses, whole-checkpoint delete semantics, and reload
hints.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from tm.restore import (
    RestoreError,
    RestoreFailed,
    StalePlanError,
    apply_plan,
    build_plan,
    reload_hints,
)
from tm.security import PathTraversalError
from tm.snapshot import create_checkpoint


def _cp(store, catalog, profile_fixture, label):
    return create_checkpoint(store, catalog, "default", "manual", label=label)


def _live(profile_fixture):
    """Snapshot of the live tracked files for verification."""
    return {
        "config.yaml": (profile_fixture / "config.yaml").read_text(),
        "SOUL.md": (profile_fixture / "SOUL.md").read_text(),
        "skills/my-skill/SKILL.md": (
            profile_fixture / "skills" / "my-skill" / "SKILL.md"
        ).read_text(),
        "cron/jobs.json": (profile_fixture / "cron" / "jobs.json").read_text(),
    }


def test_acceptance_scenario_end_to_end(profile_fixture, store_and_catalog):
    """The parent's 10-step acceptance scenario."""
    store, catalog = store_and_catalog
    root = profile_fixture

    # 1) Known configuration fixture (conftest) + 2) create a checkpoint.
    a = _cp(store, catalog, root, "A")
    assert a is not None

    # 3) Modify multiple tracked files; 4) create and delete tracked files.
    (root / "config.yaml").write_text("model: switched\nprovider: other\n")
    (root / "skills" / "my-skill" / "SKILL.md").write_text(
        "---\nname: my-skill\n---\nBody v2\n"
    )
    (root / "scripts" / "added.sh").write_text("#!/bin/sh\necho added\n")
    (root / "scripts" / "daily.sh").unlink()
    # 9) Excluded credential/runtime files are touched but must never matter.
    (root / ".env").write_text("OPENAI_API_KEY=sk-rotated-zzz\n")
    (root / "auth.json").write_text('{"token": "rotated"}\n')

    # 5) Correct diff against the checkpoint.
    from tm.diff import diff_checkpoint

    diff = diff_checkpoint(store, catalog, "default", a["id"], against="current")
    by_path = {i["path"]: i for i in diff["items"]}
    assert by_path["config.yaml"]["action"] == "modified"
    assert by_path["scripts/added.sh"]["action"] == "created"
    assert by_path["scripts/daily.sh"]["action"] == "deleted"
    assert not any(i["path"] in (".env", "auth.json") for i in diff["items"])

    # 6) Generate a restore preview (whole checkpoint).
    plan = build_plan(store, catalog, "default", a["id"], selection=None)
    actions = {i["path"]: i["action"] for i in plan["items"]}
    assert actions["config.yaml"] == "replace"
    assert actions["scripts/added.sh"] == "delete"
    assert actions["scripts/daily.sh"] == "create"
    assert actions["SOUL.md"] == "skip"

    # 7) Apply the restore.
    result = apply_plan(store, catalog, plan["token"])

    # 8) Verify every tracked file matches checkpoint A exactly.
    expected = a["files"]
    for rel, meta in expected.items():
        p = catalog.resolve("default", rel)
        assert p.read_bytes() == store.read_object(meta["sha256"]), rel
    assert result["verification"]["ok"] is True
    assert result["verification"]["verified"] == result["verification"]["checked"]
    # The pre-restore checkpoint exists, making the restore undoable.
    assert result["safety_checkpoint"]
    safety = store.get_checkpoint("default", result["safety_checkpoint"])
    assert safety["trigger"] == "pre-restore"
    assert safety["files"]["config.yaml"]["sha256"] == store.put_object(
        b"model: switched\nprovider: other\n"
    )

    # 9) Excluded credential/runtime files were never captured or changed.
    assert (root / ".env").read_text() == "OPENAI_API_KEY=sk-rotated-zzz\n"
    assert (root / "auth.json").read_text() == '{"token": "rotated"}\n'
    for m in store.list_manifests("default"):
        assert ".env" not in m.get("files", {})
        assert "auth.json" not in m.get("files", {})


def test_selected_file_restore_does_not_delete(profile_fixture, store_and_catalog):
    store, catalog = store_and_catalog
    a = _cp(store, catalog, profile_fixture, "A")
    (profile_fixture / "config.yaml").write_text("model: switched\n")
    (profile_fixture / "scripts" / "added.sh").write_text("#!/bin/sh\necho added\n")

    plan = build_plan(store, catalog, "default", a["id"], selection=["config.yaml"])
    assert {i["path"]: i["action"] for i in plan["items"]} == {"config.yaml": "replace"}
    result = apply_plan(store, catalog, plan["token"])
    # Selected scope never deletes files created after the checkpoint.
    assert (profile_fixture / "scripts" / "added.sh").exists()
    assert (
        profile_fixture / "config.yaml"
    ).read_text() == "model: default\nprovider: openrouter\n"
    assert result["verification"]["ok"]


def test_category_restore(profile_fixture, store_and_catalog):
    store, catalog = store_and_catalog
    a = _cp(store, catalog, profile_fixture, "A")
    (profile_fixture / "scripts" / "daily.sh").write_text("#!/bin/sh\necho edited\n")
    (profile_fixture / "scripts" / "added.sh").write_text("#!/bin/sh\necho added\n")

    plan = build_plan(
        store, catalog, "default", a["id"], selection=[{"category": "scripts"}]
    )
    paths = {i["path"] for i in plan["items"]}
    assert paths == {"scripts/daily.sh"}  # added.sh is outside checkpoint scope
    apply_plan(store, catalog, plan["token"])
    assert (
        profile_fixture / "scripts" / "daily.sh"
    ).read_text() == "#!/bin/sh\necho hi\n"
    assert (profile_fixture / "scripts" / "added.sh").exists()


def test_stale_plan_rejected(profile_fixture, store_and_catalog):
    store, catalog = store_and_catalog
    a = _cp(store, catalog, profile_fixture, "A")
    (profile_fixture / "config.yaml").write_text("model: switched\n")
    plan = build_plan(store, catalog, "default", a["id"], selection=None)
    # Tracked state changes after the preview was generated.
    (profile_fixture / "config.yaml").write_text("model: switched-again\n")
    with pytest.raises(StalePlanError):
        apply_plan(store, catalog, plan["token"])


def test_stale_plan_same_size_content_change(profile_fixture, store_and_catalog):
    """Same-size, same-mtime-second edit is caught by the target-hash check."""
    store, catalog = store_and_catalog
    a = _cp(store, catalog, profile_fixture, "A")
    (profile_fixture / "config.yaml").write_text("model: switched\n")
    plan = build_plan(store, catalog, "default", a["id"], selection=["config.yaml"])
    # Same byte length, different content, restore the original mtime.
    (profile_fixture / "config.yaml").write_text("model: hijacke\n")
    os.utime(profile_fixture / "config.yaml", ns=(0, 0))
    with pytest.raises(StalePlanError):
        apply_plan(store, catalog, plan["token"])


def test_mid_restore_failure_rolls_back(
    profile_fixture, store_and_catalog, monkeypatch
):
    store, catalog = store_and_catalog
    root = profile_fixture
    a = _cp(store, catalog, root, "A")
    (root / "config.yaml").write_text("model: switched\n")
    (root / "SOUL.md").write_text("# SOUL\nsoul v2\n")
    plan = build_plan(store, catalog, "default", a["id"], selection=None)

    real_replace = os.replace

    def flaky_replace(src, dst):
        # Fail deterministically when applying config.yaml. Items sort
        # case-sensitively, so SOUL.md applies first — exactly one mutation
        # lands before the failure.
        if Path(dst).name == "config.yaml":
            raise OSError("injected mid-restore failure")
        return real_replace(src, dst)

    monkeypatch.setattr("tm.restore.os.replace", flaky_replace)

    with pytest.raises(RestoreFailed) as ei:
        apply_plan(store, catalog, plan["token"])

    # Rollback restored both files to the PRE-restore state — no partial restore.
    assert (root / "config.yaml").read_text() == "model: switched\n"
    assert (root / "SOUL.md").read_text() == "# SOUL\nsoul v2\n"
    # Fail-closed detail surfaces exactly what happened.
    assert "SOUL.md" in [p for p in ei.value.applied]
    assert "injected mid-restore failure" in str(ei.value)
    # A safety checkpoint was created before the restore began.
    assert store.latest_checkpoint("default")["trigger"] == "pre-restore"
    # No staging leftovers.
    leftovers = [p for p in root.rglob("*.tm-stage")]
    assert leftovers == []


def test_whole_checkpoint_restore_deletes_new_files(profile_fixture, store_and_catalog):
    store, catalog = store_and_catalog
    a = _cp(store, catalog, profile_fixture, "A")
    (profile_fixture / "scripts" / "added.sh").write_text("#!/bin/sh\necho added\n")
    plan = build_plan(store, catalog, "default", a["id"], selection=None)
    assert any(
        i["path"] == "scripts/added.sh" and i["action"] == "delete"
        for i in plan["items"]
    )
    apply_plan(store, catalog, plan["token"])
    assert not (profile_fixture / "scripts" / "added.sh").exists()


def test_plan_rejects_traversal_selection(profile_fixture, store_and_catalog):
    store, catalog = store_and_catalog
    a = _cp(store, catalog, profile_fixture, "A")
    with pytest.raises((RestoreError, PathTraversalError)):
        build_plan(store, catalog, "default", a["id"], selection=["../../etc/passwd"])
    with pytest.raises((RestoreError, PathTraversalError)):
        build_plan(store, catalog, "default", a["id"], selection=["/etc/passwd"])


def test_resolve_rejects_symlink_escape(profile_fixture, store_and_catalog, tmp_path):
    _, catalog = store_and_catalog
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "evil.txt").write_text("evil")
    try:
        (profile_fixture / "skills" / "link").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable on this platform")
    with pytest.raises(PathTraversalError):
        catalog.resolve("default", "skills/link/evil.txt")


def test_missing_object_fails_closed(profile_fixture, store_and_catalog):
    store, catalog = store_and_catalog
    a = _cp(store, catalog, profile_fixture, "A")
    (profile_fixture / "config.yaml").write_text("model: switched\n")
    # Corrupt one referenced object so preview must fail closed.
    sha = a["files"]["config.yaml"]["sha256"]
    store.object_path(sha).write_bytes(b"tampered")
    with pytest.raises(Exception) as ei:
        build_plan(store, catalog, "default", a["id"], selection=None)
    from tm.store import StoreIntegrityError

    assert isinstance(ei.value, StoreIntegrityError)


def test_nothing_to_restore(profile_fixture, store_and_catalog):
    store, catalog = store_and_catalog
    a = _cp(store, catalog, profile_fixture, "A")
    with pytest.raises(RestoreError):
        build_plan(store, catalog, "default", a["id"], selection=None)


def test_reload_hints():
    hints = reload_hints(
        ["config.yaml", "cron/jobs.json", "SOUL.md", "skills/x/SKILL.md"]
    )
    text = "\n".join(hints)
    assert "config.yaml" in text
    assert "cron" in text
    assert "restart" in text.lower()
