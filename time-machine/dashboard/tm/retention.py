"""Retention / prune with pin semantics (SCAFFOLD).

Prunes automatic checkpoints oldest-first while exempting pinned manual
checkpoints, and guards against destroying the only safe recovery point.
Implementation milestone.
"""
