"""Time Machine dashboard plugin — backend API routes (SCAFFOLD).

Mounted at /api/plugins/time-machine/ by the Hermes dashboard plugin
system (manifest.json declares api=plugin_api.py; the plugin must be in
plugins.enabled in config.yaml for the serve process to import it).

This is the SCAFFOLD milestone: a liveness probe only, so the frontend
page/chip can confirm the backend is mounted. The implementation milestone
adds the checkpoint engine in sibling modules under dashboard/tm/ (catalog,
store, snapshot, diff, restore, retention, security) and the real routes:

    GET  /health                      Liveness probe.
    GET  /checkpoints?profile=<id>&limit=N     Timeline of checkpoints.
    POST /checkpoints                 Manual checkpoint {label, profile}.
    GET  /checkpoints/<id>            Summary: files present, hashes, size.
    GET  /checkpoints/<id>/diff?against=current|adjacent
    GET  /checkpoints/<id>/file?path=...
    POST /restore/preview             {checkpoint_id, profile, selection}
    POST /restore/apply               {plan_token}
    POST /checkpoints/<id>/pin
    POST /checkpoints/prune

Security / design contract (carried into the implementation milestone)
---------------------------------------------------------------------
* Read-mostly; mutation routes (restore preview/apply, pin, prune) must
  validate every input and resolve all paths through the tracked-source
  catalog — never accept arbitrary filesystem destinations from the
  frontend.
* Never emit secret values (.env, auth.json, credentials) or runtime DB
  contents through diffs or API responses; secret-shaped files get
  metadata-only treatment.
* Store lives under HERMES_HOME with restrictive permissions; the
  cross-process lock guards multi-profile concurrency.
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import APIRouter

# The Hermes web_server mounts plugin APIs with
# ``importlib.util.spec_from_file_location`` and does NOT add this
# plugin's dashboard/ directory to sys.path. Without this self-registration,
# sibling imports (tm/* modules) fail at serve time with
# ``No module named 'tm'`` even though tests (which insert the path
# themselves) pass. Registering our own directory keeps the plugin
# importable under the real loader contract. This is a no-op when the dir is
# already on sys.path (e.g. under pytest).
_PLUGIN_DIR = str(Path(__file__).resolve().parent)
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    """Liveness probe for the frontend page/chip."""
    return {"ok": True, "plugin": "time-machine", "status": "skeleton"}
