"""File-level diff engine for Time Machine checkpoints.

Diff semantics follow the parent contract:

* ``created`` / ``modified`` / ``deleted`` per file, computed either against
  the adjacent checkpoint (``against=adjacent``) or against the current live
  tracked state (``against=current``).
* Text files get readable unified diffs (stdlib difflib), line-capped.
* Binary/unsupported files get honest metadata-only treatment (size +
  hashes) — never a fake diff.
* Secret values are redacted in the emitted text (``security.redact_secret_values``)
  so a key pasted into an otherwise tracked file is not echoed back through
  the API. Stored objects keep pristine content.
"""

from __future__ import annotations

import difflib
from pathlib import Path

from .security import redact_secret_values

# Caps so a large change never floods the desktop payload.
TEXT_DIFF_MAX_LINES = 200
TEXT_DIFF_MAX_BYTES = 64 * 1024
MAX_CHANGED_FILES = 500

MAX_TEXT_DIFF_ENTRY_BYTES = 128 * 1024


def detect_kind(path: Path, data: bytes) -> str:
    """Return ``"text"`` or ``"binary"`` (NUL sniff + UTF-8 probe)."""
    head = data[:8192]
    if b"\x00" in head:
        return "binary"
    try:
        head.decode("utf-8")
        return "text"
    except UnicodeDecodeError:
        return "binary"


def decode_text(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def classify(old_files: dict, new_files: dict) -> dict:
    """created/modified/deleted rel lists between two file maps."""
    created: list[str] = []
    modified: list[str] = []
    deleted: list[str] = []
    for rel, meta in new_files.items():
        old = old_files.get(rel)
        if old is None:
            created.append(rel)
        elif old.get("sha256") != meta.get("sha256"):
            modified.append(rel)
    for rel in old_files:
        if rel not in new_files:
            deleted.append(rel)
    created.sort()
    modified.sort()
    deleted.sort()
    return {"created": created, "modified": modified, "deleted": deleted}


def _unified_diff(old_text: str, new_text: str, path: str, max_lines: int) -> str:
    lines = list(
        difflib.unified_diff(
            old_text.splitlines(),
            new_text.splitlines(),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm="",
        )
    )
    if len(lines) > max_lines:
        overflow = len(lines) - max_lines
        lines = lines[:max_lines] + [f"… {overflow} more lines truncated"]
    return "\n".join(lines)


def _text_diff(old_data: bytes, new_data: bytes, path: str) -> str | None:
    """Unified diff for two text blobs, or None when beyond caps."""
    if len(old_data) > TEXT_DIFF_MAX_BYTES or len(new_data) > TEXT_DIFF_MAX_BYTES:
        return None  # metadata-only: file too large to diff usefully
    diff = _unified_diff(
        decode_text(old_data), decode_text(new_data), path, TEXT_DIFF_MAX_LINES
    )
    return redact_secret_values(diff)


def _live_text(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError:
        return b""


def diff_checkpoint(
    store, catalog, profile: str, cid: str, against: str = "current"
) -> dict:
    """Compute the diff for one checkpoint.

    ``against``:
      * ``current``  — changes between this checkpoint and the live tracked
                       state (old = checkpoint objects, new = live files).
      * ``adjacent`` — changes between the previous checkpoint and this one
                       (both sides read from stored objects).
    """
    if against not in ("current", "adjacent"):
        against = "current"

    manifest = store.get_checkpoint(profile, cid)

    if against == "adjacent":
        old_files: dict = {}
        if manifest.get("prev_id"):
            prev = store.get_checkpoint(profile, manifest["prev_id"])
            old_files = prev.get("files", {})
        new_files = manifest.get("files", {})
    else:
        root = catalog.profile_root(profile)
        new_files = catalog.current_files(root) if root else {}
        old_files = manifest.get("files", {})

    classification = classify(old_files, new_files)

    # Build detail entries (capped), reading content from the right side.
    items: list[dict] = []
    truncated = False

    def _append(entry: dict) -> None:
        nonlocal truncated
        if len(items) >= MAX_CHANGED_FILES:
            truncated = True
            return
        items.append(entry)

    def _read_side(meta: dict, side: str) -> bytes:
        """Read content for a file map entry from either side."""
        if side == "old":
            return store.read_object(meta["sha256"])
        # new side: for 'current' it is live; for 'adjacent' it is an object
        if against == "adjacent":
            return store.read_object(meta["sha256"])
        try:
            return catalog.resolve(profile, meta["_rel"]).read_bytes()
        except Exception:  # noqa: BLE001 - live file vanished / unreadable → empty
            return b""

    # Created (present in new, absent in old)
    for rel in classification["created"]:
        meta = dict(new_files[rel])
        meta["_rel"] = rel
        entry: dict = {
            "path": rel,
            "action": "created",
            "kind": meta.get("kind", "text"),
            "size": meta.get("size", 0),
            "sha256": meta.get("sha256", ""),
        }
        if entry["kind"] == "text":
            diff = _text_diff(b"", _read_side(meta, "new"), rel)
            if diff:
                entry["text_diff"] = diff
        _append(entry)

    # Deleted (present in old, absent in new)
    for rel in classification["deleted"]:
        meta = old_files[rel]
        entry = {
            "path": rel,
            "action": "deleted",
            "kind": meta.get("kind", "text"),
            "size": meta.get("size", 0),
            "sha256": meta.get("sha256", ""),
        }
        if entry["kind"] == "text":
            diff = _text_diff(_read_side(meta, "old"), b"", rel)
            if diff:
                entry["text_diff"] = diff
        _append(entry)

    # Modified (present in both, hash differs)
    for rel in classification["modified"]:
        old_meta = old_files[rel]
        new_meta = dict(new_files[rel])
        new_meta["_rel"] = rel
        entry = {
            "path": rel,
            "action": "modified",
            "kind": old_meta.get("kind", "text"),
            "old": {
                "size": old_meta.get("size", 0),
                "sha256": old_meta.get("sha256", ""),
            },
            "new": {
                "size": new_meta.get("size", 0),
                "sha256": new_meta.get("sha256", ""),
            },
        }
        old_kind = old_meta.get("kind", "text")
        new_kind = new_meta.get("kind", "text")
        if old_kind == "text" and new_kind == "text":
            diff = _text_diff(
                _read_side(old_meta, "old"), _read_side(new_meta, "new"), rel
            )
            if diff:
                entry["text_diff"] = diff
        _append(entry)

    return {
        "profile": profile,
        "checkpoint_id": cid,
        "against": against,
        "summary": {
            "created": len(classification["created"]),
            "modified": len(classification["modified"]),
            "deleted": len(classification["deleted"]),
        },
        "items": items,
        "truncated": truncated,
    }
