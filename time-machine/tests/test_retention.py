"""Retention tests: keep counts, pin exemption, newest-never-pruned guard,
degraded-only preservation, and object GC after prune."""

from __future__ import annotations

from tm.retention import prune
from tm.snapshot import create_checkpoint


def _seed(store, catalog, root, n_auto, n_manual, pin_indices=()):
    """Create n_auto auto checkpoints then n_manual manual ones, pinning the
    manual checkpoints at pin_indices (0 = first manual)."""
    ids = []
    for i in range(n_auto):
        (root / "config.yaml").write_text(f"model: auto-{i}\n")
        m = create_checkpoint(store, catalog, "default", "auto")
        ids.append(m["id"])
    manual_ids = []
    for i in range(n_manual):
        (root / "config.yaml").write_text(f"model: manual-{i}\n")
        m = create_checkpoint(store, catalog, "default", "manual", label=f"M{i}")
        manual_ids.append(m["id"])
    for idx in pin_indices:
        with store.lock():
            store.set_pinned("default", manual_ids[idx], True)
    return ids, manual_ids


def test_prune_keeps_recent_auto_and_exempts_pinned(profile_fixture, store_and_catalog):
    store, catalog = store_and_catalog
    _, manual_ids = _seed(
        store, catalog, profile_fixture, n_auto=8, n_manual=2, pin_indices=(0,)
    )
    result = prune(store, catalog, profile="default", keep_auto=3, keep_manual=1)
    assert result["removed_checkpoints"] == 5  # 8 auto - 3 kept

    remaining = [m["id"] for m in store.list_manifests("default")]
    # Newest checkpoint (a manual) is never pruned.
    assert manual_ids[1] in remaining
    # Pinned manual is exempt.
    assert manual_ids[0] in remaining
    assert len(remaining) == 5  # 3 auto + pinned manual + newest manual


def test_newest_never_pruned_even_over_keep(profile_fixture, store_and_catalog):
    store, catalog = store_and_catalog
    _seed(store, catalog, profile_fixture, n_auto=5, n_manual=0)
    result = prune(store, catalog, profile="default", keep_auto=1, keep_manual=1)
    assert result["removed_checkpoints"] == 3  # 5 auto → keep 1 + newest
    remaining = [m["id"] for m in store.list_manifests("default")]
    assert len(remaining) == 2  # newest + one kept


def test_pinned_survives_prune_of_only_auto(profile_fixture, store_and_catalog):
    store, catalog = store_and_catalog
    _seed(store, catalog, profile_fixture, n_auto=2, n_manual=1, pin_indices=(0,))
    result = prune(store, catalog, profile="default", keep_auto=0, keep_manual=0)
    assert result["removed_checkpoints"] == 2  # autos pruned; pinned manual kept
    remaining = [m["id"] for m in store.list_manifests("default")]
    assert len(remaining) == 1


def test_degraded_only_profile_preserved(profile_fixture, store_and_catalog):
    store, catalog = store_and_catalog
    _seed(store, catalog, profile_fixture, n_auto=1, n_manual=0)
    # Corrupt the only manifest → prune must NOT delete it (only recovery point).
    m = store.latest_checkpoint("default")
    (store.checkpoints_dir / "default" / f"{m['id']}.json").write_text("{corrupt")
    result = prune(store, catalog, profile="default", keep_auto=0, keep_manual=0)
    assert result["removed_checkpoints"] == 0
    assert len(store.list_manifests("default")) == 1


def test_prune_gcs_orphaned_objects(profile_fixture, store_and_catalog):
    store, catalog = store_and_catalog
    _seed(store, catalog, profile_fixture, n_auto=6, n_manual=0)
    before = store.put_object(b"orphan-bytes")
    result = prune(store, catalog, profile="default", keep_auto=2, keep_manual=2)
    assert result["freed_objects"] >= 1
    assert not store.object_exists(before)
