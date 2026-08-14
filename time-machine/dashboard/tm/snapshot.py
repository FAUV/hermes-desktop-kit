"""Checkpoint capture orchestration + automatic checkpoint watcher.

``create_checkpoint`` walks the tracked catalog for one profile, content-
addresses every file into the store (dedup: identical content is never
re-stored), writes a durable manifest, and records the created/modified/
deleted summary versus the adjacent checkpoint. Automatic checkpoints are
deduplicated (identical content → no new checkpoint) and debounced
(minimum gap between auto checkpoints, coalescing bursts of saves).

``AutoWatcher`` is a stdlib-only polling thread (the kit constraint forbids
new dependencies) started lazily by the backend: while the Desktop runs it
compares a cheap size+mtime fingerprint against the last known state and
creates auto checkpoints after a quiet debounce window, so normal saves do
not spam the timeline.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .catalog import Catalog
from .diff import detect_kind
from .store import MANIFEST_VERSION, Store

log = logging.getLogger("tm.snapshot")

TRIGGERS = {"auto", "manual", "pre-restore"}


class ProfileNotFoundError(KeyError):
    pass


def _summary_vs_adjacent(files: dict, prev_files: dict | None) -> dict:
    """created/modified/deleted rel lists versus the previous checkpoint."""
    prev_files = prev_files or {}
    created: list[str] = []
    modified: list[str] = []
    deleted: list[str] = []
    for rel, meta in files.items():
        old = prev_files.get(rel)
        if old is None:
            created.append(rel)
        elif old.get("sha256") != meta.get("sha256"):
            modified.append(rel)
    for rel in prev_files:
        if rel not in files:
            deleted.append(rel)
    created.sort()
    modified.sort()
    deleted.sort()
    return {"created": created, "modified": modified, "deleted": deleted}


def _create_checkpoint_locked(
    store: Store,
    catalog: Catalog,
    profile: str,
    root: Path,
    trigger: str,
    label: str | None,
    pinned: bool = False,
) -> dict | None:
    """Create a checkpoint. Caller must hold the store lock.

    Returns the manifest, or None when an automatic checkpoint would be
    byte-identical to the previous one (dedup — nothing changed to record).
    """
    files: dict = {}
    total_size = 0
    for rel_parts, abs_path in catalog.walk(root):
        try:
            data = abs_path.read_bytes()
        except OSError:
            continue  # transient read error — skip the file this pass
        sha = store.put_object(data)
        size = len(data)
        total_size += size
        files["/".join(rel_parts)] = {
            "size": size,
            "sha256": sha,
            "kind": detect_kind(abs_path, data),
        }

    prev = store.latest_checkpoint(profile)
    prev_files = prev.get("files") if prev else None
    if prev is not None and prev_files == files and trigger == "auto":
        # Nothing changed since the previous checkpoint — don't spam the
        # timeline with an identical automatic checkpoint.
        return None

    cid = store.make_checkpoint_id(profile)
    manifest = {
        "version": MANIFEST_VERSION,
        "id": cid,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "label": (label or "").strip(),
        "trigger": trigger,
        "profile": profile,
        "pinned": bool(pinned),
        "files": files,
        "file_count": len(files),
        "total_size": total_size,
        "prev_id": prev["id"] if prev is not None else None,
        "summary": _summary_vs_adjacent(files, prev_files),
    }
    store.write_manifest(profile, manifest)
    # Baseline the live state so the watcher does not immediately treat the
    # just-captured state as a new change.
    store.save_fingerprint(profile, catalog.fingerprint_digest(root))
    return manifest


def create_checkpoint(
    store: Store,
    catalog: Catalog,
    profile: str,
    trigger: str,
    label: str | None = None,
    pinned: bool = False,
) -> dict | None:
    """Create a checkpoint for a profile (store lock acquired internally)."""
    if trigger not in TRIGGERS:
        raise ValueError(f"invalid trigger: {trigger!r}")
    root = catalog.profile_root(profile)
    if root is None or not root.exists():
        raise ProfileNotFoundError(profile)
    with store.lock():
        return _create_checkpoint_locked(
            store, catalog, profile, root, trigger, label, pinned
        )


class AutoWatcher:
    """Debounced, deduplicated automatic checkpoints while the Desktop runs.

    Polls each profile's tracked fingerprint (size + mtime — cheap), and when
    a change persists for the debounce window creates one automatic
    checkpoint. A change that is reverted before the debounce elapses is
    never recorded.
    """

    def __init__(
        self,
        store: Store,
        catalog: Catalog,
        poll_s: float = 30.0,
        debounce_s: float = 60.0,
        prune_cb=None,
    ):
        self.store = store
        self.catalog = catalog
        self.poll_s = max(2.0, float(poll_s))
        self.debounce_s = max(0.0, float(debounce_s))
        self.prune_cb = (
            prune_cb  # optional callable(profile) run after an auto checkpoint
        )
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="tm-auto-watcher", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self.poll_s):
            try:
                self._tick()
            except Exception:  # never kill the watcher thread
                log.exception("time-machine auto watcher tick failed")

    def _tick(self) -> None:
        for profile in self.catalog.profile_ids():
            root = self.catalog.profile_root(profile)
            if root is None or not root.exists():
                continue
            digest = self.catalog.fingerprint_digest(root)
            stored = self.store.load_fingerprint(profile)
            if stored is None:
                # First sight: record the baseline without checkpointing so
                # enabling the plugin does not spam an initial checkpoint.
                self.store.save_fingerprint(profile, digest)
                continue
            if digest == stored:
                self.store.clear_pending(profile)
                continue
            pending = self.store.load_pending(profile)
            now = time.time()
            if pending is None:
                self.store.save_pending(profile, now)
                pending = now
            if now - pending < self.debounce_s:
                continue  # still inside the debounce window — wait
            try:
                created = create_checkpoint(
                    self.store, self.catalog, profile, trigger="auto"
                )
            except Exception:
                log.exception("auto checkpoint failed for profile %s", profile)
                created = None
            if created is not None and self.prune_cb is not None:
                try:
                    self.prune_cb(profile)
                except Exception:
                    log.exception("post-auto prune failed for profile %s", profile)
            self.store.save_fingerprint(profile, digest)
            self.store.clear_pending(profile)
