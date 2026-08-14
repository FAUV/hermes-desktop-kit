"""Time Machine backend package (SCAFFOLD).

Owns the checkpoint engine: catalog (tracked sources + exclusions), store
(content-addressed objects + manifests), snapshot (capture orchestration),
diff (file-level comparisons), restore (planning + transactional apply),
retention (prune/pin), and security (path/traversal/symlink guards).

Modules are docstring-only skeletons at this milestone; the implementation
card fills them in. plugin_api.py imports from this package.
"""
