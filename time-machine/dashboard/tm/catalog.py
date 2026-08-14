"""Tracked-source catalog + exclusion engine (SCAFFOLD).

Defines which Hermes profile surfaces are tracked (config.yaml, SOUL.md,
MEMORY.md, USER.md, skills/, cron/, scripts/, plugins/, memories/,
knowledge/, preferences/, desktop.json, desktop-plugins/ — per profile
root, based on upstream ``_DEFAULT_EXPORT_INCLUDE_ROOT``) and the exclusion
catalog (secrets, runtime DBs, transcripts, logs, PID/lock/process state,
caches, venvs/node_modules, core checkout/worktrees, Time Machine's own
store). Implementation milestone: see research brief sections 10-11.
"""
