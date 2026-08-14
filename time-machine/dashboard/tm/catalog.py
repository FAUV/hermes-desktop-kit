"""Tracked-source catalog: which files Time Machine watches per profile.

The catalog is the backend's answer to "what is the human-maintained Hermes
configuration layer". Per profile root it allow-lists exactly upstream's
``_DEFAULT_EXPORT_INCLUDE_ROOT`` (hermes_cli/profiles.py) plus the desktop
plugin surface the parent contract explicitly wants tracked, minus
``sessions`` (session transcripts are explicitly excluded by the parent).
Exclusions from ``security.py`` apply at every depth, so the allow-list stays
current as upstream adds artifacts while never tracking secrets, runtime
DBs, caches, dependency trees, or Time Machine's own store.

Profiles: the default profile root is HERMES_HOME itself; named profiles are
``<HERMES_HOME>/profiles/<name>``. Each profile is its own catalog root —
``profiles/`` is never recursed into from another root.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .security import resolve_contained, should_exclude

# Upstream hermes_cli/profiles.py::_DEFAULT_EXPORT_INCLUDE_ROOT mapped to the
# parent contract: everything there except "sessions" (transcripts excluded).
TRACKED_TOP = frozenset(
    {
        "config.yaml",
        "SOUL.md",
        "MEMORY.md",
        "USER.md",
        "todo.json",
        "system_prompt.md",
        "AGENTS.md",
        "CLAUDE.md",
        ".cursorrules",
        "desktop.json",
        "skills",
        "cron",
        "scripts",
        "plugins",
        "memories",
        "knowledge",
        "preferences",
        # Desktop plugin source (parent explicitly wants it tracked);
        # desktop.json (its config sibling) is already in the allow-list.
        "desktop-plugins",
    }
)

_PROFILE_ID_RE = __import__("re").compile(r"^[A-Za-z0-9_-]+$")


class CatalogError(RuntimeError):
    pass


class Catalog:
    """Resolves profiles and walks their tracked files."""

    def __init__(self, hermes_home: str | Path, store_root: str | Path | None = None):
        self.hermes_home = Path(hermes_home).resolve()
        self.store_root = (
            Path(store_root).resolve()
            if store_root is not None
            else (self.hermes_home / "time-machine").resolve()
        )
        # Absolute paths Time Machine must never capture: its own store and
        # its own plugin implementation (frontend + backend copies) at every
        # profile root. Prevents accidental self-restoration.
        self._self_excludes = frozenset(self._default_self_excludes())

    def _default_self_excludes(self) -> set[Path]:
        excludes = {self.store_root}
        for pid in self.profile_ids():
            root = self.profile_root(pid)
            if root is None:
                continue
            excludes.add(root / "time-machine")
            excludes.add(root / "plugins" / "time-machine")
            excludes.add(root / "desktop-plugins" / "time-machine")
        return excludes

    # -- profiles -----------------------------------------------------------

    def valid_profile_id(self, profile: str) -> bool:
        return bool(_PROFILE_ID_RE.match(profile or ""))

    def profile_ids(self) -> list[str]:
        """All catalog profiles: "default" plus named profiles on disk."""
        ids = ["default"]
        profiles_dir = self.hermes_home / "profiles"
        if profiles_dir.is_dir():
            for child in sorted(profiles_dir.iterdir()):
                if (
                    child.is_dir()
                    and not child.name.startswith(".")
                    and child.name != "default"
                    and self.valid_profile_id(child.name)
                ):
                    ids.append(child.name)
        return ids

    def profile_root(self, profile: str) -> Path | None:
        """Absolute root for a profile, or None when the profile is unknown."""
        if not self.valid_profile_id(profile):
            return None
        if profile == "default":
            return self.hermes_home
        root = self.hermes_home / "profiles" / profile
        return root if root.is_dir() else None

    def require_profile(self, profile: str) -> Path:
        root = self.profile_root(profile)
        if root is None:
            raise CatalogError(f"unknown profile: {profile!r}")
        return root

    # -- walking ------------------------------------------------------------

    def walk(self, root: Path):
        """Yield ``(rel_parts, abs_path)`` for every tracked file.

        Applies the TRACKED_TOP allow-list at depth 0, the exclusion catalog
        at every depth, and never follows symlinks.
        """
        root = root.resolve()
        for entry_name in sorted(TRACKED_TOP):
            entry = root / entry_name
            if entry.is_symlink():
                continue
            if entry.resolve() in self._self_excludes:
                continue
            if entry.is_file():
                yield (entry_name,), entry
            elif entry.is_dir():
                yield from self._walk_dir(entry, (entry_name,))

    def _walk_dir(self, dir_path: Path, rel_parts: tuple[str, ...]):
        try:
            children = sorted(dir_path.iterdir(), key=lambda p: p.name)
        except OSError:
            return
        for child in children:
            child_rel = rel_parts + (child.name,)
            if should_exclude(child_rel, child.is_dir()):
                continue
            if child.is_symlink():
                continue
            if child.resolve() in self._self_excludes:
                continue
            if child.is_dir():
                yield from self._walk_dir(child, child_rel)
            elif child.is_file():
                yield child_rel, child

    # -- state snapshots ----------------------------------------------------

    def current_files(self, root: Path) -> dict:
        """``{rel: {"size", "sha256", "kind"}}`` for every tracked file now."""
        out: dict = {}
        for rel_parts, abs_path in self.walk(root):
            try:
                data = abs_path.read_bytes()
            except OSError:
                continue  # transient lock/read error — skip the file
            from .diff import detect_kind  # local import avoids cycle

            out["/".join(rel_parts)] = {
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "kind": detect_kind(abs_path, data),
            }
        return out

    def fingerprint(self, root: Path) -> dict:
        """``{rel: [size, mtime_ns]}`` — cheap change detection for watcher
        and restore-plan binding. Content hashing happens only when a file's
        size/mtime actually differs."""
        out: dict = {}
        for rel_parts, abs_path in self.walk(root):
            try:
                st = abs_path.stat()
            except OSError:
                continue
            out["/".join(rel_parts)] = [st.st_size, st.st_mtime_ns]
        return out

    def fingerprint_digest(self, root: Path) -> str:
        """Stable sha256 over the current tracked-state fingerprint."""
        canonical = json.dumps(
            self.fingerprint(root), sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def resolve(self, profile: str, rel: str) -> Path:
        """Resolve a catalog-relative path to an absolute path for a profile.

        Raises ``PathTraversalError`` for traversal / symlink escapes and
        ``CatalogError`` for unknown profiles. The ONLY way backend code
        derives a live filesystem path from a frontend-supplied relative
        path — never accept arbitrary destinations.
        """
        root = self.require_profile(profile)
        return resolve_contained(root, rel)
