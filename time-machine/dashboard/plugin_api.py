"""Time Machine dashboard plugin — backend API routes.

Mounted at /api/plugins/time-machine/ by the Hermes dashboard plugin system
(manifest.json declares api=plugin_api.py; the plugin must be in
plugins.enabled in config.yaml for the serve process to import it).

Routes
------
    GET  /health                       Liveness probe + store status.
    GET  /profiles                     Profiles known to the catalog.
    GET  /catalog?profile=<id>         Tracked files for a profile.
    GET  /checkpoints?profile=<id>&limit=N     Timeline, newest first.
    POST /checkpoints                  {profile, label} manual checkpoint.
    GET  /checkpoints/{profile}/{cid}  Checkpoint summary.
    GET  /checkpoints/{profile}/{cid}/diff?against=current|adjacent
    POST /checkpoints/{profile}/{cid}/pin   {pinned: bool}
    POST /checkpoints/prune             {profile?} retention policy.
    POST /restore/preview               {profile, checkpoint_id, selection?} -> plan
    POST /restore/apply                 {plan_token} -> applied + verified

Security / design contract
--------------------------
* The checkpoint engine lives in sibling modules under dashboard/tm/
  (catalog, store, snapshot, diff, restore, retention, security); this file
  stays a thin validation + routing layer.
* Mutation routes validate every input. Restore destinations resolve ONLY
  through the catalog (traversal + symlink guards) — the frontend can never
  name an arbitrary filesystem path.
* Secret-shaped files are excluded at capture time (never stored/diffed);
  diff text is redacted for secret-shaped values before it leaves the API.
* The store lives under HERMES_HOME/time-machine with restrictive
  permissions and a cross-process lock; the auto-checkpoint watcher is
  started lazily (first request), never at import, so tests and the loader
  contract stay side-effect-free.
* No snapshot/diff content is ever sent to a model or external service —
  the frontend reads only this API.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

# The Hermes web_server mounts plugin APIs with
# ``importlib.util.spec_from_file_location`` and does NOT add this plugin's
# dashboard/ directory to sys.path. Without this self-registration, sibling
# imports (tm/* modules) fail at serve time. It is a no-op when the dir is
# already importable (e.g. under pytest).
_PLUGIN_DIR = str(Path(__file__).resolve().parent)
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)

from tm import (
    AutoWatcher,
    Catalog,
    PathTraversalError,
    PlanError,
    PlanExpiredError,
    PlanNotFoundError,
    ProfileNotFoundError,
    RestoreError,
    RestoreFailed,
    StalePlanError,
    Store,
    StoreIntegrityError,
    StoreLockTimeout,
    apply_plan,
    build_plan,
    create_checkpoint,
    diff_checkpoint,
    prune,
)

log = logging.getLogger("tm.api")
router = APIRouter()


# ---------------------------------------------------------------------------
# Lazy engine (never constructed at import time)
# ---------------------------------------------------------------------------

_engine_lock = threading.Lock()
_engine = None


def _default_hermes_home() -> str:
    """HERMES_HOME as the gateway process resolves it (importable there)."""
    try:
        from hermes_constants import get_hermes_home

        return str(get_hermes_home())
    except Exception:  # noqa: BLE001 - fallback when not running under the gateway
        return str(Path.home() / ".hermes")


def _env_config() -> dict:
    def _int(name: str, default: int) -> int:
        try:
            return int(os.environ.get(name, str(default)))
        except ValueError:
            return default

    return {
        "hermes_home": os.environ.get("TIME_MACHINE_HERMES_HOME")
        or _default_hermes_home(),
        "poll_s": _int("TIME_MACHINE_POLL_S", 30),
        "debounce_s": _int("TIME_MACHINE_DEBOUNCE_S", 60),
        "keep_auto": _int("TIME_MACHINE_KEEP_AUTO", 20),
        "keep_manual": _int("TIME_MACHINE_KEEP_MANUAL", 50),
    }


def _get_engine() -> tuple[Store, Catalog, dict]:
    """Build (once) the store, catalog, and watcher; start the watcher."""
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                cfg = _env_config()
                store = Store(cfg["hermes_home"])
                catalog = Catalog(cfg["hermes_home"], store_root=store.root)
                watcher = AutoWatcher(
                    store,
                    catalog,
                    poll_s=cfg["poll_s"],
                    debounce_s=cfg["debounce_s"],
                    prune_cb=lambda profile: prune(store, catalog, profile=profile),
                )
                watcher.start()
                _engine = (store, catalog, cfg)
    return _engine


def _ensure_backend():
    """Route handlers call this first; starts the watcher on first use."""
    return _get_engine()


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


def _http(exc: Exception) -> HTTPException:
    if isinstance(exc, ProfileNotFoundError):
        return HTTPException(status_code=404, detail=f"profile not found: {exc}")
    if isinstance(exc, StoreIntegrityError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, PathTraversalError):
        return HTTPException(status_code=400, detail=f"invalid path: {exc}")
    if isinstance(exc, StalePlanError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, PlanExpiredError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, PlanNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, PlanError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, RestoreFailed):
        return HTTPException(
            status_code=500,
            detail={
                "error": str(exc),
                "applied": exc.applied,
                "rollback_errors": exc.rollback_errors,
            },
        )
    if isinstance(exc, RestoreError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, StoreLockTimeout):
        return HTTPException(status_code=503, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))


def _valid_profile(profile: str, catalog: Catalog) -> Path:
    root = catalog.profile_root(profile)
    if root is None:
        raise HTTPException(status_code=404, detail=f"unknown profile: {profile!r}")
    return root


def _valid_cid(cid: str) -> None:
    if not cid or "/" in cid or "\\" in cid or ".." in cid or "\x00" in cid:
        raise HTTPException(status_code=400, detail=f"invalid checkpoint id: {cid!r}")


def _checkpoint_summary(manifest: dict) -> dict:
    """Smaller wire form of a manifest for timelines."""
    if manifest.get("degraded"):
        return {"id": manifest["id"], "degraded": True, "error": manifest.get("error")}
    summary = manifest.get("summary") or {}
    return {
        "id": manifest["id"],
        "timestamp": manifest.get("timestamp"),
        "label": manifest.get("label"),
        "trigger": manifest.get("trigger"),
        "pinned": bool(manifest.get("pinned")),
        "profile": manifest.get("profile"),
        "file_count": manifest.get("file_count", len(manifest.get("files", {}))),
        "total_size": manifest.get("total_size", 0),
        "prev_id": manifest.get("prev_id"),
        "summary": {
            "created": len(summary.get("created", [])),
            "modified": len(summary.get("modified", [])),
            "deleted": len(summary.get("deleted", [])),
        },
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/health")
async def health() -> dict:
    store, catalog, cfg = _ensure_backend()
    checkpoints = store.list_manifests()
    healthy = sum(1 for m in checkpoints if not m.get("degraded"))
    return {
        "ok": True,
        "plugin": "time-machine",
        "status": "ready",
        "store_root": str(store.root),
        "hermes_home": str(store.hermes_home),
        "profiles": len(catalog.profile_ids()),
        "checkpoints": healthy,
        "degraded": len(checkpoints) - healthy,
        "retention": {"keep_auto": cfg["keep_auto"], "keep_manual": cfg["keep_manual"]},
        "watch": {"poll_s": cfg["poll_s"], "debounce_s": cfg["debounce_s"]},
    }


@router.get("/profiles")
async def profiles() -> dict:
    store, catalog, _ = _ensure_backend()
    items = []
    for pid in catalog.profile_ids():
        root = catalog.profile_root(pid)
        checkpoints = store.list_manifests(pid)
        items.append(
            {
                "id": pid,
                "root": str(root),
                "exists": bool(root and root.exists()),
                "checkpoint_count": sum(
                    1 for m in checkpoints if not m.get("degraded")
                ),
            }
        )
    return {"profiles": items}


@router.get("/catalog")
async def catalog_files(profile: str = "default") -> dict:
    _, catalog, _ = _ensure_backend()
    root = _valid_profile(profile, catalog)
    entries = ["/".join(rel) for rel, _ in catalog.walk(root)]
    return {
        "profile": profile,
        "tracked_files": len(entries),
        "entries": sorted(entries),
    }


@router.get("/checkpoints")
async def checkpoints(
    profile: str = "default",
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    store, catalog, _ = _ensure_backend()
    _valid_profile(profile, catalog)
    manifests = store.list_manifests(profile)
    items = [_checkpoint_summary(m) for m in manifests[:limit]]
    return {"profile": profile, "count": len(items), "items": items}


@router.post("/checkpoints")
async def create_manual_checkpoint(body: dict) -> dict:
    store, catalog, _ = _ensure_backend()
    profile = str(body.get("profile") or "default")
    _valid_profile(profile, catalog)
    label = str(body.get("label") or "").strip()
    if len(label) > 200:
        raise HTTPException(status_code=400, detail="label too long (max 200 chars)")
    try:
        manifest = create_checkpoint(
            store, catalog, profile, trigger="manual", label=label
        )
    except ProfileNotFoundError as exc:
        raise _http(exc) from exc
    except StoreLockTimeout as exc:
        raise _http(exc) from exc
    if manifest is None:  # pragma: no cover - manual checkpoints always create
        raise HTTPException(status_code=409, detail="no tracked content to checkpoint")
    return {"checkpoint": _checkpoint_summary(manifest)}


@router.get("/checkpoints/{profile}/{cid}")
async def checkpoint_detail(profile: str, cid: str) -> dict:
    store, catalog, _ = _ensure_backend()
    _valid_profile(profile, catalog)
    _valid_cid(cid)
    try:
        manifest = store.get_checkpoint(profile, cid)
    except StoreIntegrityError as exc:
        raise _http(exc) from exc
    return {"checkpoint": manifest}


@router.get("/checkpoints/{profile}/{cid}/diff")
async def checkpoint_diff(profile: str, cid: str, against: str = "current") -> dict:
    store, catalog, _ = _ensure_backend()
    _valid_profile(profile, catalog)
    _valid_cid(cid)
    try:
        return diff_checkpoint(store, catalog, profile, cid, against=against)
    except StoreIntegrityError as exc:
        raise _http(exc) from exc


@router.post("/checkpoints/{profile}/{cid}/pin")
async def pin_checkpoint(profile: str, cid: str, body: dict) -> dict:
    store, catalog, _ = _ensure_backend()
    _valid_profile(profile, catalog)
    _valid_cid(cid)
    pinned = bool(body.get("pinned", False))
    try:
        with store.lock():
            manifest = store.set_pinned(profile, cid, pinned)
    except StoreIntegrityError as exc:
        raise _http(exc) from exc
    return {"checkpoint": _checkpoint_summary(manifest)}


@router.post("/checkpoints/prune")
async def prune_checkpoints(body: dict) -> dict:
    store, catalog, cfg = _ensure_backend()
    profile = body.get("profile")
    if profile is not None:
        _valid_profile(str(profile), catalog)
    try:
        result = prune(
            store,
            catalog,
            profile=str(profile) if profile else None,
            keep_auto=cfg["keep_auto"],
            keep_manual=cfg["keep_manual"],
        )
    except StoreLockTimeout as exc:
        raise _http(exc) from exc
    return result


@router.post("/restore/preview")
async def restore_preview(body: dict) -> dict:
    store, catalog, _ = _ensure_backend()
    profile = str(body.get("profile") or "default")
    _valid_profile(profile, catalog)
    cid = str(body.get("checkpoint_id") or "")
    _valid_cid(cid)
    selection = body.get("selection")  # None | list[str] | list[{category}]
    if selection is not None and not isinstance(selection, list):
        raise HTTPException(status_code=400, detail="selection must be a list")
    try:
        plan = build_plan(store, catalog, profile, cid, selection=selection)
    except (
        RestoreError,
        PathTraversalError,
        StoreIntegrityError,
        ProfileNotFoundError,
    ) as exc:
        raise _http(exc) from exc
    except StoreLockTimeout as exc:
        raise _http(exc) from exc

    counts = {"create": 0, "replace": 0, "delete": 0, "skip": 0}
    for item in plan["items"]:
        counts[item["action"]] = counts.get(item["action"], 0) + 1
    return {
        "token": plan["token"],
        "profile": plan["profile"],
        "checkpoint_id": plan["checkpoint_id"],
        "scope": plan["scope"],
        "expires_at": plan["expires_at"],
        "summary": counts,
        "items": plan["items"],
    }


@router.post("/restore/apply")
async def restore_apply(body: dict) -> dict:
    store, catalog, _ = _ensure_backend()
    token = str(body.get("plan_token") or "")
    try:
        return apply_plan(store, catalog, token)
    except (PlanNotFoundError, PlanExpiredError, PlanError) as exc:
        raise _http(exc) from exc
    except (StalePlanError, RestoreFailed, RestoreError) as exc:
        raise _http(exc) from exc
    except (ProfileNotFoundError, StoreIntegrityError, PathTraversalError) as exc:
        raise _http(exc) from exc
    except StoreLockTimeout as exc:
        raise _http(exc) from exc
