"""Store tests: content-addressed dedup, atomic publish, integrity checks,
checkpoint id uniqueness, plan lifecycle, locking, and object GC."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest
from tm.store import (
    PlanExpiredError,
    PlanNotFoundError,
    Store,
    StoreIntegrityError,
    StoreLockTimeout,
)


def test_put_object_dedup_and_layout(hermes_home):
    store = Store(hermes_home)
    sha1 = store.put_object(b"same-content")
    sha2 = store.put_object(b"same-content")
    assert sha1 == sha2
    obj = store.object_path(sha1)
    assert obj.is_file()
    # 2-level sharding: objects/<aa>/<bb>/<sha>
    assert obj.parent.name == sha1[2:4]
    assert obj.parent.parent.name == sha1[:2]
    # one object file exists for identical content
    n = sum(1 for _ in store.objects_dir.rglob("*") if _.is_file())
    assert n == 1


def test_read_object_integrity(hermes_home):
    store = Store(hermes_home)
    sha = store.put_object(b"hello")
    assert store.read_object(sha) == b"hello"

    # Missing object.
    with pytest.raises(StoreIntegrityError):
        store.read_object("a" * 64)

    # Invalid hash.
    with pytest.raises(StoreIntegrityError):
        store.read_object("zzz")

    # Corrupt object (hash mismatch).
    obj = store.object_path(sha)
    obj.write_bytes(b"tampered")
    with pytest.raises(StoreIntegrityError):
        store.read_object(sha)


def test_manifest_roundtrip_and_id_dedup(hermes_home):
    store = Store(hermes_home)
    cid = store.make_checkpoint_id("default")
    manifest = {
        "id": cid,
        "timestamp": "2026-08-13T00:00:00+00:00",
        "label": "first",
        "trigger": "manual",
        "profile": "default",
        "pinned": False,
        "files": {"config.yaml": {"size": 3, "sha256": "a" * 64, "kind": "text"}},
        "file_count": 1,
        "total_size": 3,
        "prev_id": None,
        "summary": {"created": ["config.yaml"], "modified": [], "deleted": []},
    }
    store.write_manifest("default", manifest)
    assert store.get_checkpoint("default", cid)["label"] == "first"
    assert store.latest_checkpoint("default")["id"] == cid

    # Same-second id dedup: -2 suffix.
    cid2 = store.make_checkpoint_id("default")
    assert cid2 == f"{cid}-2"

    # Corrupt manifest surfaces as degraded, not crash.
    (store.checkpoints_dir / "default" / "broken.json").write_text("{not json")
    listing = store.list_manifests("default")
    assert any(m.get("degraded") and m["id"] == "broken" for m in listing)


def test_atomic_publish_leaves_no_partials(hermes_home):
    store = Store(hermes_home)
    store.write_manifest("default", {"id": "x", "files": {}})
    leftovers = [p for p in store.root.rglob("*.partial")]
    assert leftovers == []
    # state file
    store.save_fingerprint("default", "digest123")
    assert store.load_fingerprint("default") == "digest123"
    assert [p for p in store.root.rglob("*.partial")] == []


def test_plan_lifecycle(hermes_home):
    store = Store(hermes_home)
    now = datetime.now(timezone.utc)
    plan = {
        "token": "ab" * 16,
        "profile": "default",
        "checkpoint_id": "x",
        "state_digest": "d",
        "target_hashes": {},
        "items": [],
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=10)).isoformat(),
    }
    store.write_plan(plan)
    assert store.load_plan(plan["token"])["state_digest"] == "d"

    with pytest.raises(PlanNotFoundError):
        store.load_plan("0" * 32)
    with pytest.raises(PlanNotFoundError):
        store.load_plan("../evil")

    store.delete_plan(plan["token"])
    with pytest.raises(PlanNotFoundError):
        store.load_plan(plan["token"])


def test_plan_expiry(hermes_home):
    store = Store(hermes_home)
    plan = {
        "token": "cd" * 16,
        "profile": "default",
        "checkpoint_id": "x",
        "state_digest": "d",
        "target_hashes": {},
        "items": [],
        "created_at": "2026-01-01T00:00:00+00:00",
        "expires_at": "2026-01-01T00:10:00+00:00",
    }
    store.write_plan(plan)
    with pytest.raises(PlanExpiredError):
        store.load_plan(plan["token"])


def test_lock_serializes_and_times_out(hermes_home):
    store = Store(hermes_home)
    # Reentrant within the same thread.
    with store.lock(), store.lock():
        pass

    # A different Store instance (different fd) contends and times out.
    store2 = Store(hermes_home)
    held = threading.Event()
    release = threading.Event()
    result = {}

    def holder():
        with store.lock(timeout=5):
            held.set()
            release.wait(5)

    def contender():
        try:
            with store2.lock(timeout=0.3):
                result["ok"] = True
        except StoreLockTimeout:
            result["timeout"] = True

    t1 = threading.Thread(target=holder)
    t1.start()
    assert held.wait(5)
    t2 = threading.Thread(target=contender)
    t2.start()
    t2.join(5)
    release.set()
    t1.join(5)
    assert result.get("timeout") is True
    assert "ok" not in result


def test_gc_removes_only_unreferenced_objects(hermes_home):
    store = Store(hermes_home)
    keep = store.put_object(b"keep-me")
    drop = store.put_object(b"drop-me")
    store.write_manifest(
        "default",
        {
            "id": "20260101-000000",
            "files": {"config.yaml": {"sha256": keep, "size": 7, "kind": "text"}},
        },
    )
    freed = store.gc_objects()
    assert freed == 1
    assert store.object_exists(keep)
    assert not store.object_exists(drop)
