"""Restore planning + transactional apply (SCAFFOLD).

Restore safety contract: preview builds a plan (create/replace/delete)
bound to a state fingerprint; apply auto-checkpoints current state first,
rejects stale plans, stages + validates before mutating, uses safe
same-filesystem replacement, and on mid-failure rolls back via the
pre-restore checkpoint, failing closed. All paths resolve through the
catalog — no arbitrary fs destinations from the frontend. Implementation
milestone.
"""
