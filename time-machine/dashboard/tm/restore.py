"""Restore planning + transactional apply for Time Machine.

Restore safety contract (parent): never a blind copy — a restore always
starts from a *plan* showing exactly what will be created, replaced, or
deleted; the plan is bound to the current tracked state (stale plans are
rejected); applying auto-checkpoints the current state first so the restore
is undoable; data is staged and validated before mutating; mutations use
same-filesystem atomic replacement; a mid-restore failure rolls back the
applied mutations from the pre-restore checkpoint and fails closed.

All destination paths resolve through the catalog (``catalog.resolve``) with
traversal + symlink-escape guards — the frontend can never name an arbitrary
filesystem destination.
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .catalog import Catalog
from .security import ensure_private_file
from .snapshot import ProfileNotFoundError, _create_checkpoint_locked
from .store import Store

log = logging.getLogger("tm.restore")

PLAN_TTL_S = 600

SKIP_ACTIONS = {"skip"}


class RestoreError(RuntimeError):
    pass


class StalePlanError(RestoreError):
    """Tracked state changed after the preview was generated."""


class RestoreFailed(RestoreError):
    """A restore failed mid-apply; rollback was attempted. Carries detail."""

    def __init__(self, applied, rollback_errors, cause):
        super().__init__(f"restore failed after {len(applied)} mutation(s): {cause}")
        self.applied = applied
        self.rollback_errors = rollback_errors
        self.cause = cause


def _checkpoint_with_integrity(store: Store, profile: str, cid: str) -> dict:
    """Load a manifest and verify every referenced object exists + hashes."""
    manifest = store.get_checkpoint(profile, cid)
    for meta in manifest.get("files", {}).values():
        store.read_object(meta["sha256"])  # raises StoreIntegrityError
    return manifest


def _expand_selection(manifest: dict, selection) -> set[str]:
    """Expand the frontend selection into a set of manifest rel paths.

    Accepts explicit rel paths, prefixes ("skills" matches "skills/..."), or
    ``{"category": "skills"}`` entries.
    """
    files = manifest.get("files", {})
    selected: set[str] = set()
    if selection is None:
        return set(files)
    for sel in selection:
        if isinstance(sel, str):
            rel = sel.strip("/")
            if not rel:
                continue
            if rel in files:
                selected.add(rel)
            else:
                prefix = rel + "/"
                selected.update(r for r in files if r.startswith(prefix))
        elif isinstance(sel, dict) and sel.get("category"):
            prefix = str(sel["category"]).strip("/")
            if prefix in files:
                selected.add(prefix)
            else:
                prefix += "/"
                selected.update(r for r in files if r.startswith(prefix))
    return selected


def build_plan(
    store: Store,
    catalog: Catalog,
    profile: str,
    checkpoint_id: str,
    selection=None,
) -> dict:
    """Build and persist a restore plan bound to the current tracked state.

    ``selection=None`` means whole-checkpoint scope (files in the checkpoint
    that are missing/changed are created/replaced, and files that exist now
    but not in the checkpoint are scheduled for deletion). A selection list
    limits the plan to create/replace for the chosen files — deletion is
    only ever part of a whole-checkpoint restore.
    """
    root = catalog.profile_root(profile)
    if root is None or not root.exists():
        raise ProfileNotFoundError(profile)

    with store.lock():
        manifest = _checkpoint_with_integrity(store, profile, checkpoint_id)
        files = manifest.get("files", {})
        current = catalog.current_files(root)

        selected_rels = _expand_selection(manifest, selection)
        # Defense in depth: every selected path must resolve inside the
        # catalog (raises PathTraversalError otherwise).
        for rel in selected_rels:
            catalog.resolve(profile, rel)

        items: list[dict] = []
        for rel in sorted(selected_rels):
            meta = files[rel]
            cur = current.get(rel)
            if cur is None:
                action = "create"
            elif cur["sha256"] != meta["sha256"]:
                action = "replace"
            else:
                action = "skip"
            items.append(
                {
                    "path": rel,
                    "action": action,
                    "size": meta["size"],
                    "sha256": meta["sha256"],
                    "kind": meta.get("kind", "text"),
                }
            )

        if selection is None:
            # Whole-checkpoint scope: delete files created since the checkpoint.
            for rel in sorted(current):
                if rel not in files:
                    cur = current[rel]
                    items.append(
                        {
                            "path": rel,
                            "action": "delete",
                            "size": cur["size"],
                            "sha256": cur["sha256"],
                            "kind": cur.get("kind", "text"),
                        }
                    )

        non_skip = [i for i in items if i["action"] not in SKIP_ACTIONS]
        if not non_skip:
            raise RestoreError(
                "nothing to restore — tracked state already matches this checkpoint"
            )

        token = secrets.token_hex(16)
        now = datetime.now(timezone.utc)
        plan = {
            "token": token,
            "profile": profile,
            "checkpoint_id": checkpoint_id,
            "scope": "whole" if selection is None else "selection",
            "selection": sorted(selected_rels),
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=PLAN_TTL_S)).isoformat(),
            # Binding: full tracked-state digest + per-target content hashes
            # AS OF THE PREVIEW (live state), so apply can detect a file that
            # changed between preview and apply even when size+mtime fool the
            # digest. None = file did not exist at preview time.
            "state_digest": catalog.fingerprint_digest(root),
            "target_hashes": {
                i["path"]: (current.get(i["path"]) or {}).get("sha256")
                for i in items
                if i["action"] in ("create", "replace")
            },
            "items": items,
        }
        store.write_plan(plan)
        return plan


def _revalidate_dest(catalog: Catalog, profile: str, rel: str) -> Path:
    """Fresh containment + symlink-escape check immediately before mutation."""
    return catalog.resolve(profile, rel)


def _restore_file_from_manifest(
    store: Store, catalog: Catalog, profile: str, manifest: dict, rel: str
) -> None:
    """Set a live file to the state recorded in a manifest (rollback helper)."""
    dest = _revalidate_dest(catalog, profile, rel)
    meta = manifest.get("files", {}).get(rel)
    if meta is None:
        # The file did not exist in this manifest's state — remove it.
        if dest.exists() or dest.is_symlink():
            dest.unlink()
        return
    data = store.read_object(meta["sha256"])
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.tm-rollback")
    tmp.write_bytes(data)
    os.replace(tmp, dest)


def apply_plan(store: Store, catalog: Catalog, token: str) -> dict:
    """Apply a restore plan with staging, validation, and rollback-on-failure.

    Returns a result dict with the applied operations, verification results,
    and the pre-restore safety checkpoint id. Raises StalePlanError when the
    tracked state changed since the plan was built, RestoreFailed when a
    mid-restore failure occurred (after rollback), and PlanError for missing
    or expired plans.
    """
    plan = store.load_plan(token)  # raises PlanNotFound/PlanExpired
    profile = plan["profile"]
    root = catalog.profile_root(profile)
    if root is None or not root.exists():
        raise ProfileNotFoundError(profile)

    with store.lock():
        # 1) Freshness: the plan is bound to the state at preview time.
        digest = catalog.fingerprint_digest(root)
        if digest != plan["state_digest"]:
            raise StalePlanError(
                "tracked state changed since the restore preview — re-preview and try again"
            )
        # Belt-and-suspenders: re-hash the target files (catches same-size
        # same-second edits that mtime-based fingerprints can miss). The
        # expected values are the LIVE hashes at preview time.
        current = catalog.current_files(root)
        for rel, expected in plan.get("target_hashes", {}).items():
            cur = current.get(rel)
            if expected is None:
                # File did not exist at preview; any creation since is caught
                # by the digest check above.
                continue
            if cur is None or cur["sha256"] != expected:
                raise StalePlanError(
                    f"tracked state changed for {rel} since the preview — re-preview"
                )

        # 2) Auto-checkpoint the current state so the restore is undoable.
        safety = _create_checkpoint_locked(
            store,
            catalog,
            profile,
            root,
            trigger="pre-restore",
            label=f"Pre-restore safety (before {plan['checkpoint_id']})",
        )
        if safety is None:  # pragma: no cover - pre-restore always creates
            safety = store.latest_checkpoint(profile)
        assert safety is not None

        # 3) Stage + validate everything before mutating anything.
        staged: list[tuple[str, Path, Path | None, str]] = []
        for item in plan["items"]:
            action = item["action"]
            if action in SKIP_ACTIONS:
                continue
            dest = _revalidate_dest(catalog, profile, item["path"])
            if action in ("create", "replace"):
                data = store.read_object(item["sha256"])  # corrupt → fail closed
                stage = dest.with_name(f".{dest.name}.tm-stage")
                stage.parent.mkdir(parents=True, exist_ok=True)
                stage.write_bytes(data)
                ensure_private_file(stage)
                if hashlib.sha256(data).hexdigest() != item["sha256"]:
                    raise RestoreError(f"staged content mismatch for {item['path']}")
                staged.append((item["path"], dest, stage, action))
            else:  # delete
                staged.append((item["path"], dest, None, "delete"))

        # 4) Journaled apply (same-filesystem atomic replacement).
        applied: list[dict] = []
        try:
            try:
                for rel, dest, stage, action in staged:
                    dest = _revalidate_dest(
                        catalog, profile, rel
                    )  # re-check pre-mutation
                    if action == "delete":
                        if dest.exists() or dest.is_symlink():
                            dest.unlink()
                        applied.append({"path": rel, "action": "delete"})
                    else:
                        assert stage is not None
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(stage, dest)  # atomic, same filesystem
                        applied.append({"path": rel, "action": action})
            finally:
                # Best-effort cleanup of stage files that were not applied.
                for _, _dest, stage, _action in staged:
                    if stage is not None and stage.exists():
                        try:
                            stage.unlink()
                        except OSError:
                            pass
        except Exception as exc:
            rollback_errors: list[dict] = []
            for entry in reversed(applied):
                try:
                    _restore_file_from_manifest(
                        store, catalog, profile, safety, entry["path"]
                    )
                except Exception as re_:  # noqa: BLE001 — surface every rollback failure
                    rollback_errors.append({"path": entry["path"], "error": str(re_)})
            log.error(
                "restore %s failed after %d mutations; rollback errors: %s",
                token,
                len(applied),
                rollback_errors,
            )
            raise RestoreFailed(
                applied=[a["path"] for a in applied],
                rollback_errors=rollback_errors,
                cause=str(exc),
            ) from exc

        # 5) Verify every selected tracked file matches the checkpoint exactly.
        verification = _verify_restore(store, catalog, profile, plan)

        # 6) Baseline the new live state so the watcher does not immediately
        #    create an automatic checkpoint for the restore we just performed.
        store.save_fingerprint(profile, catalog.fingerprint_digest(root))
        store.delete_plan(token)

        return {
            "profile": profile,
            "checkpoint_id": plan["checkpoint_id"],
            "safety_checkpoint": safety["id"],
            "applied": applied,
            "verification": verification,
            "reload_hints": reload_hints([a["path"] for a in applied]),
        }


def _verify_restore(store: Store, catalog: Catalog, profile: str, plan: dict) -> dict:
    """Re-hash every target file and confirm it matches the checkpoint."""
    current = catalog.current_files(catalog.require_profile(profile))
    failed: list[dict] = []
    checked = 0
    for item in plan["items"]:
        if item["action"] in SKIP_ACTIONS:
            continue
        checked += 1
        rel = item["path"]
        cur = current.get(rel)
        if item["action"] == "delete":
            if cur is not None:
                failed.append({"path": rel, "error": "file still present after delete"})
        elif cur is None:
            failed.append({"path": rel, "error": "file missing after restore"})
        elif cur["sha256"] != item["sha256"]:
            failed.append({"path": rel, "error": "content hash mismatch after restore"})
    return {
        "checked": checked,
        "failed": failed,
        "ok": not failed,
        "verified": checked - len(failed),
    }


def reload_hints(paths: list[str]) -> list[str]:
    """Tell the operator what needs a reload — never auto-restart anything."""
    hints: set[str] = set()
    for p in paths:
        if p == "config.yaml":
            hints.add(
                "Hermes config (config.yaml) changed — restart the gateway/serve process for it to take effect."
            )
        elif p.startswith("cron/"):
            hints.add(
                "Cron definitions changed — cron reads jobs.json on its next tick; restart the gateway to be safe."
            )
        elif p.startswith("desktop-plugins/") or p == "desktop.json":
            hints.add(
                "Desktop plugin sources changed — reload desktop plugins (⌘K → Reload desktop plugins) or restart the desktop."
            )
        elif p.startswith("skills/"):
            hints.add(
                "Skills changed — reload the active session to pick up skill changes."
            )
        elif p.startswith("plugins/"):
            hints.add(
                "Plugin sources changed — restart the backend (hermes serve) for plugin API changes to take effect."
            )
        elif p in ("SOUL.md", "MEMORY.md", "USER.md", "system_prompt.md"):
            hints.add(
                "Profile persona files changed — a new session picks these up; restart the gateway to be safe."
            )
        elif p.startswith(("memories/", "knowledge/", "preferences/")):
            hints.add(
                "Profile memory/knowledge changed — reload memory and knowledge in the active session."
            )
    return sorted(hints)
