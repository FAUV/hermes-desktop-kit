"""Time Machine backend package.

Owns the checkpoint engine: catalog (tracked sources + exclusions), store
(content-addressed objects + manifests + cross-process lock), snapshot
(capture orchestration + auto watcher), diff (file-level comparisons),
restore (planning + transactional apply with rollback), retention
(prune/pin), and security (path/traversal/symlink guards + exclusions).

``plugin_api.py`` imports from this package via sys.path self-registration.
"""

from .catalog import TRACKED_TOP, Catalog, CatalogError
from .diff import diff_checkpoint
from .restore import (
    RestoreError,
    RestoreFailed,
    StalePlanError,
    apply_plan,
    build_plan,
    reload_hints,
)
from .retention import prune
from .security import (
    PathTraversalError,
    redact_secret_values,
    resolve_contained,
    should_exclude,
)
from .snapshot import AutoWatcher, ProfileNotFoundError, create_checkpoint
from .store import (
    PlanError,
    PlanExpiredError,
    PlanNotFoundError,
    Store,
    StoreIntegrityError,
    StoreLockTimeout,
)

__all__ = [
    "TRACKED_TOP",
    "AutoWatcher",
    "Catalog",
    "CatalogError",
    "PathTraversalError",
    "PlanError",
    "PlanExpiredError",
    "PlanNotFoundError",
    "ProfileNotFoundError",
    "RestoreError",
    "RestoreFailed",
    "StalePlanError",
    "Store",
    "StoreIntegrityError",
    "StoreLockTimeout",
    "apply_plan",
    "build_plan",
    "create_checkpoint",
    "diff_checkpoint",
    "prune",
    "redact_secret_values",
    "reload_hints",
    "resolve_contained",
    "should_exclude",
]
