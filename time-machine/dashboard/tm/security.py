"""Security helpers for Time Machine.

The exclusion catalog is the single source of truth for what Time Machine
may capture: anything excluded here is never read, hashed, stored, diffed,
or restored. It is assembled from the research brief section 11 (upstream
``hermes_cli/backup.py`` ``_EXCLUDED_*`` sets and ``hermes_cli/profiles.py``
``_DEFAULT_EXPORT_EXCLUDE_ROOT``, mapped to the parent contract) and follows
upstream's philosophy of an allow-list plus a defensive name/depth exclusion
catalog rather than a blacklist that must anticipate everything.

Also provides path resolution/containment (traversal + symlink-escape
guards), secret-shaped name detection, restrictive store permissions, and
light secret-value redaction for the diff/API layer (the parent contract
forbids exposing secret values through diffs or API responses even if a
secret-shaped value lands inside an otherwise tracked file).
"""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Exclusion catalog
# ---------------------------------------------------------------------------

# Names excluded at ANY depth (files and directories).
EXCLUDED_NAMES = frozenset(
    {
        # Secrets / credentials
        ".env",
        "auth.json",
        "auth.lock",
        # Runtime DBs (sqlite + sidecars; generic *.db also excluded below)
        "state.db",
        "hermes_state.db",
        "sessions.db",
        "response_store.db",
        "memory_store.db",
        "verification_evidence.db",
        "projects.db",
        "kanban.db",
        "executions.db",
        "discord_message_recovery.db",
        "channel_directory.json",
        "channel_aliases.json",
        "feishu_comment_pairing.json",
        # Session / transcript artifacts
        ".hermes_history",
        # Logs
        "logs",
        "errors.log",
        "log.txt",
        # Process / runtime state
        "gateway.pid",
        "cron.pid",
        "gateway.lock",
        "gateway_state.json",
        "processes.json",
        "active_profile",
        ".update_check",
        # Caches / regenerable state
        "cache",
        "image_cache",
        "audio_cache",
        "document_cache",
        "browser_screenshots",
        "checkpoints",
        "sandboxes",
        ".cache",
        ".tox",
        ".nox",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "__pycache__",
        "bootstrap-cache",
        "chrome-debug",
        "node_modules",
        ".venv",
        "venv",
        "site-packages",
        "bin",
        ".git",
        ".worktrees",
        # Profile infrastructure
        "profile.yaml",
        # Upstream lock files
        ".backup.lock",
    }
)

# Filename prefixes excluded anywhere (case-insensitive). ".env" covers
# .env, .env.local, .env.production, ... — the canonical Hermes secret file.
EXCLUDED_NAME_PREFIXES = (".env",)

# Suffixes excluded anywhere.
EXCLUDED_SUFFIXES = frozenset(
    {
        ".pyc",
        ".pyo",
        ".db",
        ".db-wal",
        ".db-shm",
        ".db-journal",
        ".sock",
        ".tmp",
        ".partial",
        # Time Machine's own staging/rollback temp names (leftovers must
        # never be captured by a later checkpoint).
        ".tm-stage",
        ".tm-rollback",
    }
)

# fnmatch patterns on the basename for secret-shaped files (OAuth/API
# credentials and machine keys). Deliberately scoped to secret-y extensions
# so a skill or plugin *about* credentials ("credentials.md", "token-speed")
# is not silently dropped from the tracked layer.
EXCLUDED_FILE_PATTERNS = (
    "token*.json",
    "token*.yaml",
    "token*.yml",
    "tokens*.json",
    "tokens*.yaml",
    "tokens*.yml",
    "oauth*.json",
    "oauth*.yaml",
    "oauth*.yml",
    "*_credentials*.json",
    "*_credentials*.yaml",
    "*_credentials*.yml",
    "*.secret",
    "*.secret.json",
    "*.secret.yaml",
    "*.secret.yml",
    "id_rsa*",
    "id_ed25519*",
    "*.pem",
    "*.key",
)

# Root-depth-only exclusions: the core checkout and other profiles. Nested
# copies (e.g. a skill directory named "hermes-agent") must be preserved —
# upstream makes the same distinction.
EXCLUDED_ROOT_ONLY = frozenset({"hermes-agent", "profiles"})


def should_exclude(rel_parts: tuple[str, ...], is_dir: bool = False) -> bool:
    """Return True when a catalog entry must never be tracked.

    ``rel_parts`` is the path tuple relative to the profile root. Depth-aware:
    ``EXCLUDED_ROOT_ONLY`` names only match at depth 0.
    """
    name = rel_parts[-1]
    if name in EXCLUDED_NAMES:
        return True
    lower = name.lower()
    if any(lower.startswith(pfx) for pfx in EXCLUDED_NAME_PREFIXES):
        return True
    if any(name.endswith(sfx) for sfx in EXCLUDED_SUFFIXES):
        return True
    if any(fnmatch.fnmatch(name, pat) for pat in EXCLUDED_FILE_PATTERNS):
        return True
    return len(rel_parts) == 1 and name in EXCLUDED_ROOT_ONLY


# ---------------------------------------------------------------------------
# Path resolution / containment
# ---------------------------------------------------------------------------


class PathTraversalError(ValueError):
    """Raised when a path escapes its root (traversal or symlink escape)."""


def resolve_contained(root: Path, rel: str | Path) -> Path:
    """Resolve ``rel`` against ``root``, rejecting traversal and symlink escapes.

    Returns an absolute ``Path`` guaranteed to live under ``root`` both
    lexically and physically (existing symlinks are resolved). Used for
    catalog walks, diff reads, and restore destinations — never trust a
    frontend-supplied filesystem path.
    """
    p = Path(rel)
    if p.is_absolute():
        raise PathTraversalError(f"absolute path not allowed: {rel}")
    raw = str(rel)
    if ".." in p.parts or "\x00" in raw:
        raise PathTraversalError(f"path traversal not allowed: {rel!r}")
    root_resolved = root.resolve()
    candidate = (root_resolved / p).resolve(strict=False)
    try:
        candidate.relative_to(root_resolved)
    except ValueError:
        raise PathTraversalError(f"path escapes root: {rel!r}")

    # Symlink-escape belt-and-suspenders: every existing ancestor must stay
    # inside the root (resolve() above already catches most, but re-check the
    # chain so a symlink swapped in between resolve and use is caught by the
    # caller's fresh re-validation before mutation).
    cur = root_resolved
    for part in candidate.parts[len(root_resolved.parts) :]:
        cur = cur / part
        if cur.is_symlink():
            try:
                cur.resolve(strict=False).relative_to(root_resolved)
            except ValueError:
                raise PathTraversalError(f"symlink escape: {rel!r}")
    return candidate


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------


def ensure_private_dir(path: Path, mode: int = 0o700) -> None:
    """Create a directory with restrictive permissions (best-effort)."""
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, mode)
    except OSError:
        pass  # Windows / unsupported filesystems: best-effort


def ensure_private_file(path: Path, mode: int = 0o600) -> None:
    """Restrict a file's permissions (best-effort)."""
    try:
        os.chmod(path, mode)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Secret-value redaction (diff/API layer only — objects stay pristine)
# ---------------------------------------------------------------------------

# Well-known secret value prefixes: OpenAI sk-..., GitHub ghp_/github_pat_,
# Slack xoxb-..., Anthropic sk-ant-..., Stripe sk_live_/rk_live_, etc.
_SECRET_VALUE_PREFIX = re.compile(
    r"(?i)(\bsk-[A-Za-z0-9_\-]{8,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"xox[baprs]-[A-Za-z0-9\-]{10,}|sk-ant-[A-Za-z0-9_\-]{20,}|"
    r"(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16})"
)

# key: value / key = value assignments for common secret-shaped keys.
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(\b(?:api[_-]?key|secret|token|password|passwd|authorization|access[_-]?key|"
    r"private[_-]?key|client[_-]?secret|refresh[_-]?token|bearer)\b\s*[:=]\s*"
    r"['\"]?)([^\s'\"\],}]{4,})"
)


def redact_secret_values(text: str) -> str:
    """Redact obvious secret-shaped values from diff/API text output.

    Applied only when emitting text to the frontend; stored objects and
    restore payloads keep pristine content. Conservative: redacts well-known
    secret prefixes and key=value assignments for secret-shaped keys.
    """
    out = _SECRET_VALUE_PREFIX.sub(r"\1[REDACTED]", text)
    out = _SECRET_ASSIGNMENT.sub(r"\1[REDACTED]", out)
    return out
