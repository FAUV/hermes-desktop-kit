# Time Machine

Visual history of the **human-maintained Hermes configuration layer** — a chronological timeline of checkpoints over your config, file-level diffs against adjacent/current state, and a **safety-gated restore flow** ("Hermes changed something and now my setup is broken. Show me what changed and let me safely put it back.").

This is NOT a replacement for `hermes backup`, `hermes import`, `/snapshot`, `hermes backup --quick`, or pre-update backups — those cover disaster recovery, databases, credentials, and runtime state. Time Machine complements them with fine-grained configuration history, diffs, and selective rollback.

**Milestone status: SCAFFOLD.** The page, sidebar nav, palette command, and statusbar chip are wired and load in the app; the backend serves a liveness probe. The checkpoint engine (catalog, store, snapshot, diff, restore, retention, security) lands in the implementation milestone.

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

## How it works (planned V1)

- **Tracked sources** — per Hermes profile (default = `~/.hermes`, named = `~/.hermes/profiles/<name>/`): `config.yaml`, `SOUL.md`, `MEMORY.md`, `USER.md`, `todo.json`, `system_prompt.md`, `AGENTS.md`, `CLAUDE.md`, `.cursorrules`, `desktop.json`, `skills/`, `cron/`, `scripts/`, `plugins/`, `memories/`, `knowledge/`, `preferences/`, `desktop-plugins/` — the allow-list inherited from upstream profile/export semantics.
- **Exclusions** — never captured or exposed: `.env`, `auth.json`, OAuth/API credentials, SQLite/session DBs, transcripts, logs, PID/lock/process state, gateway runtime, caches, venvs, `node_modules`, the Hermes core checkout/worktrees, and Time Machine's own store.
- **Snapshot model** — lightweight, local, content-addressed: identical content stored once, durable manifests (timestamp, label, trigger/source, profile, files present, content hashes, size), created/modified/deleted vs adjacent checkpoints.
- **Checkpointing** — automatic (debounced/deduped while Desktop runs) + manual named checkpoints that are pinnable so pruning can't remove them.
- **Restore safety** — restore always produces a plan first (create/replace/delete), auto-checkpoints current state immediately before applying so the restore is undoable, binds the plan to the current state fingerprint (stale plans are rejected), stages + validates before mutating, uses same-filesystem safe replacement, and on mid-restore failure rolls back via the pre-restore checkpoint, failing closed. No arbitrary filesystem destinations from the frontend — all paths resolve through the backend tracked-source catalog. No automatic Hermes/gateway/profile restarts as a side effect.
- **UX** — timeline of checkpoints → select → change summary + file-level diff (readable diffs for text, honest metadata-only for binary/unsupported/secret-shaped files) → restore wizard.

## Tests

```bash
env -u PYTHONPATH python -m pytest tests -q                  # backend (loader contract)
node tests/esm-registration.mjs                              # frontend registration
HERMES_AGENT_NODE_MODULES=<dir with react+react-dom> node tests/esm-render.mjs  # SSR render
```
