"""Content-addressed object store + durable manifests for Time Machine.

Layout under the store root (default ``<HERMES_HOME>/time-machine``):

    tm.lock                          cross-process lock file (fcntl/msvcrt)
    objects/<aa>/<bb>/<sha256>       content-addressed blobs (dedup)
    checkpoints/<profile>/<id>.json  durable manifests
    state/<profile>.fingerprint.json last-known live-state digest (watcher)
    state/<profile>.pending.json     debounce bookkeeping for auto checkpoints
    state/plans/<token>.json         restore plans bound to a state digest
    state/plans/<token>.journal.json applied-operation journal (diagnostics)

Security: the whole tree gets restrictive permissions (0o700 dirs / 0o600
files, best-effort on Windows). All manifest/state writes are atomic
(partial sibling + ``os.replace``). Reads verify content hashes and fail
closed on corrupt/missing objects. The cross-process lock guards
simultaneous snapshots and multi-profile concurrent access to the same
store; the same lock serializes prune/GC.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .security import ensure_private_dir, ensure_private_file

MANIFEST_VERSION = 1

# Plans expire so a stale plan token can't be applied after a long idle.
PLAN_TTL_S = 600


class StoreError(RuntimeError):
    pass


class StoreIntegrityError(StoreError):
    """Corrupt or missing snapshot object / manifest."""


class StoreLockTimeout(StoreError):
    pass


class PlanError(StoreError):
    pass


class PlanNotFoundError(PlanError):
    pass


class PlanExpiredError(PlanError):
    pass


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

try:  # POSIX
    import fcntl

    _HAVE_FCNTL = True
except ImportError:  # pragma: no cover - Windows
    fcntl = None
    _HAVE_FCNTL = False

try:  # Windows
    import msvcrt

    _HAVE_MSVCRT = True
except ImportError:
    msvcrt = None
    _HAVE_MSVCRT = False


class Store:
    def __init__(self, hermes_home: str | Path, store_root: str | Path | None = None):
        self.hermes_home = Path(hermes_home).resolve()
        self.root = (
            Path(store_root).resolve()
            if store_root is not None
            else self.hermes_home / "time-machine"
        )
        self.objects_dir = self.root / "objects"
        self.checkpoints_dir = self.root / "checkpoints"
        self.state_dir = self.root / "state"
        self.plans_dir = self.state_dir / "plans"
        self.lock_path = self.root / "tm.lock"
        self._rlock = threading.RLock()
        self._lock_holder: int | None = None
        self._lock_depth = 0
        self._ensure_layout()

    def _ensure_layout(self) -> None:
        for d in (
            self.root,
            self.objects_dir,
            self.checkpoints_dir,
            self.state_dir,
            self.plans_dir,
        ):
            ensure_private_dir(d)
        if not self.lock_path.exists():
            try:
                with open(self.lock_path, "wb") as fh:
                    fh.write(b"\x00")  # msvcrt.locking needs >= 1 byte
            except OSError:
                pass
            ensure_private_file(self.lock_path)

    # -- locking ------------------------------------------------------------

    @contextmanager
    def lock(self, timeout: float = 10.0) -> Iterator[None]:
        """Cross-process exclusive lock over the store.

        Reentrant within a thread (flock is per-file-description, so a second
        fd would deadlock against our own lock); serializes snapshot creation,
        restore planning/apply, prune, and object GC across processes so
        multiple Hermes profiles/backends sharing one store stay safe.
        """
        tid = threading.get_ident()
        if self._lock_holder == tid:
            # Same thread already holds the flock — reentrant entry.
            self._lock_depth += 1
            try:
                yield
            finally:
                self._lock_depth -= 1
            return

        with self._rlock:
            fd = None
            try:
                fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
                if _HAVE_FCNTL:
                    assert fcntl is not None
                    deadline = time.monotonic() + timeout
                    while True:
                        try:
                            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                            break
                        except OSError:
                            if time.monotonic() >= deadline:
                                raise StoreLockTimeout(
                                    f"could not acquire store lock within {timeout:.0f}s"
                                )
                            time.sleep(0.05)
                elif _HAVE_MSVCRT:  # pragma: no cover - Windows
                    assert msvcrt is not None
                    deadline = time.monotonic() + timeout
                    while True:
                        try:
                            os.lseek(fd, 0, os.SEEK_SET)
                            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                            break
                        except OSError:
                            if time.monotonic() >= deadline:
                                raise StoreLockTimeout(
                                    f"could not acquire store lock within {timeout:.0f}s"
                                )
                            time.sleep(0.05)
                self._lock_holder = tid
                self._lock_depth = 1
                try:
                    yield
                finally:
                    self._lock_holder = None
                    self._lock_depth = 0
            finally:
                if fd is not None:
                    if _HAVE_FCNTL:
                        assert fcntl is not None
                        try:
                            fcntl.flock(fd, fcntl.LOCK_UN)
                        except OSError:
                            pass
                    elif _HAVE_MSVCRT:  # pragma: no cover - Windows
                        assert msvcrt is not None
                        try:
                            os.lseek(fd, 0, os.SEEK_SET)
                            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                        except OSError:
                            pass
                    os.close(fd)

    # -- objects ------------------------------------------------------------

    @staticmethod
    def _valid_sha(sha: str) -> bool:
        return bool(_SHA256_RE.match(sha or ""))

    def object_path(self, sha: str) -> Path:
        if not self._valid_sha(sha):
            raise StoreIntegrityError(f"invalid object hash: {sha!r}")
        return self.objects_dir / sha[:2] / sha[2:4] / sha

    def put_object(self, data: bytes) -> str:
        """Store content, returning its sha256. Identical content is stored
        exactly once (content-addressing is the dedup)."""
        sha = hashlib.sha256(data).hexdigest()
        target = self.object_path(sha)
        if target.exists():
            return sha
        target.parent.mkdir(parents=True, exist_ok=True)
        ensure_private_dir(target.parent)
        partial = target.with_name(target.name + ".partial")
        partial.write_bytes(data)
        ensure_private_file(partial)
        os.replace(partial, target)
        ensure_private_file(target)
        return sha

    def object_exists(self, sha: str) -> bool:
        if not self._valid_sha(sha):
            return False
        return self.object_path(sha).is_file()

    def read_object(self, sha: str) -> bytes:
        """Read + verify an object. Raises StoreIntegrityError on missing or
        corrupt content — restore refuses to proceed on such objects."""
        if not self._valid_sha(sha):
            raise StoreIntegrityError(f"invalid object hash: {sha!r}")
        path = self.object_path(sha)
        if not path.is_file():
            raise StoreIntegrityError(f"missing snapshot object {sha[:12]}…")
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise StoreIntegrityError(
                f"unreadable snapshot object {sha[:12]}…: {exc}"
            ) from exc
        if hashlib.sha256(data).hexdigest() != sha:
            raise StoreIntegrityError(
                f"corrupt snapshot object {sha[:12]}… (hash mismatch)"
            )
        return data

    # -- manifests ----------------------------------------------------------

    def make_checkpoint_id(self, profile: str) -> str:
        base = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        cid = base
        n = 2
        while (self.checkpoints_dir / profile / f"{cid}.json").exists():
            cid = f"{base}-{n}"
            n += 1
        return cid

    def checkpoint_path(self, profile: str, cid: str) -> Path:
        if "/" in cid or "\\" in cid or ".." in cid or "\x00" in cid:
            raise StoreIntegrityError(f"invalid checkpoint id: {cid!r}")
        return self.checkpoints_dir / profile / f"{cid}.json"

    def write_manifest(self, profile: str, manifest: dict) -> None:
        cid = manifest["id"]
        path = self.checkpoint_path(profile, cid)
        path.parent.mkdir(parents=True, exist_ok=True)
        ensure_private_dir(path.parent)
        partial = path.with_suffix(".json.partial")
        partial.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        ensure_private_file(partial)
        os.replace(partial, path)
        ensure_private_file(path)

    def delete_manifest(self, profile: str, cid: str) -> None:
        path = self.checkpoint_path(profile, cid)
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    def list_manifests(self, profile: str | None = None) -> list[dict]:
        """All manifests, newest first. Corrupt manifests are surfaced as
        ``{"id": <file stem>, "degraded": true, "error": ...}`` rather than
        silently dropped."""
        if profile is not None:
            dirs = [self.checkpoints_dir / profile]
        else:
            base = self.checkpoints_dir
            dirs = (
                [base / p for p in sorted(base.iterdir()) if (base / p).is_dir()]
                if base.is_dir()
                else []
            )
        out: list[dict] = []
        for d in dirs:
            if not d.is_dir():
                continue
            for path in sorted(d.glob("*.json"), reverse=True):
                cid = path.stem
                try:
                    manifest = json.loads(path.read_text())
                    if not isinstance(manifest, dict) or "id" not in manifest:
                        raise ValueError("manifest missing id")
                    out.append(manifest)
                except (OSError, ValueError, json.JSONDecodeError) as exc:
                    out.append({"id": cid, "degraded": True, "error": str(exc)})
        out.sort(key=lambda m: m.get("timestamp", m.get("id", "")), reverse=True)
        return out

    def get_checkpoint(self, profile: str, cid: str) -> dict:
        """Load a manifest; raise StoreIntegrityError for missing/corrupt."""
        path = self.checkpoint_path(profile, cid)
        if not path.is_file():
            raise StoreIntegrityError(f"checkpoint not found: {profile}/{cid}")
        try:
            manifest = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise StoreIntegrityError(
                f"corrupt checkpoint manifest {profile}/{cid}: {exc}"
            ) from exc
        if not isinstance(manifest, dict) or manifest.get("id") != cid:
            raise StoreIntegrityError(f"corrupt checkpoint manifest {profile}/{cid}")
        return manifest

    def latest_checkpoint(self, profile: str) -> dict | None:
        for manifest in self.list_manifests(profile):
            if not manifest.get("degraded"):
                return manifest
        return None

    def set_pinned(self, profile: str, cid: str, pinned: bool) -> dict:
        manifest = self.get_checkpoint(profile, cid)
        manifest["pinned"] = bool(pinned)
        self.write_manifest(profile, manifest)
        return manifest

    # -- state files (fingerprint / pending) --------------------------------

    def _state_path(self, profile: str, kind: str) -> Path:
        return self.state_dir / f"{profile}.{kind}.json"

    def load_state_json(self, profile: str, kind: str) -> dict:
        path = self._state_path(profile, kind)
        try:
            data = json.loads(path.read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def save_state_json(self, profile: str, kind: str, data: dict) -> None:
        path = self._state_path(profile, kind)
        partial = path.with_suffix(".json.partial")
        partial.write_text(json.dumps(data, sort_keys=True) + "\n")
        ensure_private_file(partial)
        os.replace(partial, path)
        ensure_private_file(path)

    def load_fingerprint(self, profile: str) -> str | None:
        return self.load_state_json(profile, "fingerprint").get("digest")

    def save_fingerprint(self, profile: str, digest: str) -> None:
        self.save_state_json(profile, "fingerprint", {"digest": digest})

    def load_pending(self, profile: str) -> float | None:
        ts = self.load_state_json(profile, "pending").get("since")
        return float(ts) if isinstance(ts, (int, float)) else None

    def save_pending(self, profile: str, ts: float) -> None:
        self.save_state_json(profile, "pending", {"since": ts})

    def clear_pending(self, profile: str) -> None:
        path = self._state_path(profile, "pending")
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    # -- restore plans ------------------------------------------------------

    def write_plan(self, plan: dict) -> None:
        path = self.plans_dir / f"{plan['token']}.json"
        partial = path.with_suffix(".json.partial")
        partial.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n")
        ensure_private_file(partial)
        os.replace(partial, path)
        ensure_private_file(path)

    def load_plan(self, token: str) -> dict:
        if not re.fullmatch(r"[0-9a-f]{32}", token or ""):
            raise PlanNotFoundError("invalid plan token")
        path = self.plans_dir / f"{token}.json"
        if not path.is_file():
            raise PlanNotFoundError(
                "restore plan not found (expired or already applied)"
            )
        try:
            plan = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise PlanError(f"corrupt restore plan: {exc}") from exc
        expires_at = plan.get("expires_at")
        if expires_at:
            try:
                if datetime.fromisoformat(expires_at) < datetime.now(timezone.utc):
                    raise PlanExpiredError(
                        "restore plan expired — tracked state may have changed; re-preview"
                    )
            except (TypeError, ValueError):
                raise PlanError("corrupt restore plan (bad expiry)") from None
        return plan

    def delete_plan(self, token: str) -> None:
        for suffix in (".json", ".journal.json"):
            path = self.plans_dir / f"{token}{suffix}"
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    # -- object GC ----------------------------------------------------------

    def referenced_objects(self) -> set[str]:
        refs: set[str] = set()
        for manifest in self.list_manifests():
            if manifest.get("degraded"):
                continue
            for meta in manifest.get("files", {}).values():
                sha = meta.get("sha256")
                if self._valid_sha(sha):
                    refs.add(sha)
        return refs

    def gc_objects(self) -> int:
        """Delete objects no longer referenced by any manifest. Returns the
        number of freed objects. Call under the store lock."""
        referenced = self.referenced_objects()
        freed = 0
        if not self.objects_dir.is_dir():
            return 0
        for shard1 in sorted(self.objects_dir.iterdir()):
            if not shard1.is_dir():
                continue
            for shard2 in sorted(shard1.iterdir()):
                if not shard2.is_dir():
                    continue
                for obj in shard2.iterdir():
                    if obj.name not in referenced:
                        try:
                            obj.unlink()
                            freed += 1
                        except OSError:
                            pass
        return freed
