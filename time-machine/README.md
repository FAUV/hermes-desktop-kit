# Time Machine

Visual history of the **human-maintained Hermes configuration layer** — a chronological timeline of checkpoints over your config, file-level diffs against adjacent/current state, and a **safety-gated restore flow** ("Hermes changed something and now my setup is broken. Show me what changed and let me safely put it back.").

This is NOT a replacement for `hermes backup`, `hermes import`, `/snapshot`, `hermes backup --quick`, or pre-update backups — those cover disaster recovery, databases, credentials, and runtime state. Time Machine complements them with fine-grained configuration history, diffs, and selective rollback.

## Install

Two parts (frontend + backend):

```bash
# 1. Frontend
mkdir -p ~/.hermes/desktop-plugins
cp -r time-machine ~/.hermes/desktop-plugins/

# 2. Backend
mkdir -p ~/.hermes/plugins/time-machine
cp -r time-machine/dashboard ~/.hermes/plugins/time-machine/
```

Then add to `plugins.enabled` in `~/.hermes/config.yaml` (proper YAML list, not a string):

```yaml
plugins:
  enabled:
    - time-machine
```

Backend installs require a serve-process restart (the desktop respawns `hermes serve`). Verify the mount with `Mounted plugin API routes: /api/plugins/time-machine/` in `~/.hermes/logs/agent.log`. The frontend hot-reloads within seconds; if it doesn't appear, hit `⌘K` → **Reload desktop plugins**.

## What it tracks

Per Hermes profile (default = `~/.hermes`, named = `~/.hermes/profiles/<name>/`), the allow-list inherited from upstream profile/export semantics (`_DEFAULT_EXPORT_INCLUDE_ROOT`):

`config.yaml`, `SOUL.md`, `MEMORY.md`, `USER.md`, `todo.json`, `system_prompt.md`, `AGENTS.md`, `CLAUDE.md`, `.cursorrules`, `desktop.json`, `skills/`, `cron/` (jobs.json), `scripts/`, `plugins/`, `memories/`, `knowledge/`, `preferences/`, `desktop-plugins/`.

**Never captured, diffed, or restored** (exclusion catalog, applied at every depth): `.env`, `auth.json` and other secret-shaped files, SQLite/session DBs (`state.db`, `sessions.db`, `memory_store.db`, …), transcripts (`sessions/`), logs, PID/lock/process state, gateway runtime, caches, venvs/`node_modules`, the Hermes core checkout (`hermes-agent` at root depth only), `profiles/` (each profile is its own root), and Time Machine's own store + plugin implementation (self-restoration guard).

## How it works

- **Snapshot model** — lightweight, local, **content-addressed**: identical content is stored once under `<HERMES_HOME>/time-machine/objects/`, durable manifests under `checkpoints/<profile>/` carry timestamp, label, trigger/source, profile, files present, content hashes, sizes, and created/modified/deleted summaries vs the adjacent checkpoint. Store permissions are restrictive (0o700 dirs / 0o600 files, best-effort on Windows); the cross-process lock (`tm.lock`, fcntl/msvcrt) serializes snapshots, restores, and pruning across multiple Hermes profiles/backends sharing one store.
- **Checkpointing** — manual named checkpoints (`POST /checkpoints`) always capture; automatic checkpoints (backend polling watcher, stdlib-only: 30s poll, 60s debounce) coalesce bursts of saves and never store byte-identical state twice. Manual checkpoints can be **pinned** so pruning never removes them.
- **Diffs** — created/modified/deleted vs **current** or the **adjacent** checkpoint. Text files get readable unified diffs (stdlib `difflib`, line-capped); binary/unsupported files get honest metadata-only treatment. Secret-shaped values in diff text are redacted (`[REDACTED]`) before leaving the API.
- **Restore safety** — restore always produces a **plan** first (create/replace/delete, whole-checkpoint/category/selected-file scope); the plan is **bound** to the current state fingerprint and per-target content hashes (stale plans rejected — a file edited after preview invalidates the plan); applying **auto-checkpoints current state first** (pre-restore safety checkpoint) so the restore is undoable; data is staged and hash-validated before any mutation; mutations use same-filesystem atomic replacement; a mid-restore failure **rolls back** applied mutations from the safety checkpoint and fails closed, surfacing exactly what happened. All destinations resolve through the backend catalog — the frontend can never name an arbitrary filesystem path (traversal + symlink-escape guards). No Hermes/gateway/profile is auto-restarted; the result lists what needs a reload.

## Backend routes

All under `/api/plugins/time-machine/` (FastAPI; requires the plugin in `plugins.enabled`).

```
GET  /health                       Liveness probe + store status
GET  /profiles                     Profiles known to the catalog
GET  /catalog?profile=<id>         Tracked files for a profile
GET  /checkpoints?profile=<id>&limit=N     Timeline, newest first
POST /checkpoints                  {profile, label} manual checkpoint
GET  /checkpoints/{profile}/{cid}  Checkpoint summary (full manifest)
GET  /checkpoints/{profile}/{cid}/diff?against=current|adjacent
POST /checkpoints/{profile}/{cid}/pin   {pinned: bool}
POST /checkpoints/prune             {profile?} retention policy
POST /restore/preview               {profile, checkpoint_id, selection?} -> plan
POST /restore/apply                 {plan_token} -> applied + verified
```

Env overrides (for tests / unusual layouts): `TIME_MACHINE_HERMES_HOME`, `TIME_MACHINE_POLL_S` (30), `TIME_MACHINE_DEBOUNCE_S` (60), `TIME_MACHINE_KEEP_AUTO` (20), `TIME_MACHINE_KEEP_MANUAL` (50).

Retention: keeps the newest checkpoint per profile forever (never destroys the only safe recovery point), exempts pinned checkpoints, keeps the N most recent auto checkpoints and M most recent manual/pre-restore checkpoints, and garbage-collects orphaned objects after pruning. Degraded (corrupt) manifests are preserved unless a healthy recovery point remains.

## Tests

```bash
env -u PYTHONPATH python -m pytest tests -q     # backend suite (55 tests: catalog, store,
                                                # snapshot/watcher, diff, restore, retention, API)
node tests/esm-registration.mjs                 # frontend registration (loader import-scan)
HERMES_AGENT_NODE_MODULES=<dir with react+react-dom> node tests/esm-render.mjs  # SSR render
```

The restore suite includes the parent's 10-step end-to-end acceptance scenario (fixture → checkpoint → modify/create/delete → diff → preview → apply → verify → excluded-never-touched → injected mid-restore failure rollback) plus stale-plan rejection, traversal/symlink defenses, corrupt-object fail-closed, concurrent snapshots, self-restoration guards (restore never touches the store or plugin files), and retention pin semantics.

## Design decisions

- **Store location**: `<HERMES_HOME>/time-machine/` — shared across profiles (multi-profile concurrency serialized by the store lock), discoverable, and excluded from Time Machine's own catalog.
- **Watcher ownership**: backend polling thread (stdlib-only; no new dependencies), started lazily on first API call, never at import (tests and the loader contract stay side-effect-free). Debounce coalesces saves; identical content is never re-stored.
- **Per-profile checkpoints**: one checkpoint = one profile; the timeline is filtered by profile.
- **desktop.json / desktop-plugins/** are tracked (parent explicitly wants Desktop plugin source tracked).
- **Secrets**: secret-shaped files are excluded at capture time (never stored/diffed/restored). For secret-shaped values that appear inside otherwise tracked files, diff/API output is redacted; stored objects keep pristine content so restore is exact.
