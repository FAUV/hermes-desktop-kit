"""Retention / prune with pin semantics.

Prunes automatic checkpoints oldest-first beyond a keep count, manual
checkpoints beyond a (higher) keep count, always exempting pinned
checkpoints and the newest checkpoint of each profile (so pruning can never
destroy the only safe recovery point). Degraded (corrupt) manifests are only
removed when a healthy manifest remains for that profile. Runs under the
store lock and garbage-collects objects orphaned by the prune.
"""

from __future__ import annotations

from .catalog import Catalog
from .store import Store


def prune(
    store: Store,
    catalog: Catalog,
    profile: str | None = None,
    keep_auto: int = 20,
    keep_manual: int = 50,
) -> dict:
    """Prune checkpoints per the retention policy. Returns removal counts."""
    keep_auto = max(0, int(keep_auto))
    keep_manual = max(0, int(keep_manual))

    with store.lock():
        profiles = [profile] if profile else catalog.profile_ids()
        removed = 0
        for prof in profiles:
            manifests = store.list_manifests(prof)
            if not manifests:
                continue
            newest_id = next(
                (m["id"] for m in manifests if not m.get("degraded")), None
            )
            auto_seen = 0
            manual_seen = 0
            keep: set[str] = set()

            for m in manifests:
                if m.get("degraded"):
                    # Corrupt manifests are only pruned when the profile still
                    # has at least one healthy recovery point.
                    if newest_id is not None:
                        removed += 1
                    continue
                cid = m["id"]
                if m.get("pinned") or cid == newest_id:
                    keep.add(cid)
                    continue
                if m.get("trigger") == "auto":
                    auto_seen += 1
                    if auto_seen <= keep_auto:
                        keep.add(cid)
                    else:
                        removed += 1
                else:  # manual / pre-restore
                    manual_seen += 1
                    if manual_seen <= keep_manual:
                        keep.add(cid)
                    else:
                        removed += 1

            for m in manifests:
                cid = m.get("id")
                if not cid:
                    continue
                if m.get("degraded"):
                    # Corrupt manifests are only pruned when the profile still
                    # has at least one healthy recovery point.
                    if newest_id is not None:
                        store.delete_manifest(prof, cid)
                    continue
                if cid not in keep:
                    store.delete_manifest(prof, cid)

        freed_objects = store.gc_objects()
        return {"removed_checkpoints": removed, "freed_objects": freed_objects}
