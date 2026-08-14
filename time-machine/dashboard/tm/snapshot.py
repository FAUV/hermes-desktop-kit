"""Checkpoint capture orchestration (SCAFFOLD).

Walks the tracked catalog per profile, hashes content, stores new objects,
writes the manifest, and computes created/modified/deleted vs adjacent
checkpoints. Automatic checkpoints (debounced/deduped while Desktop runs)
and manual named checkpoints. Implementation milestone.
"""
