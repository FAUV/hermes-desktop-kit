/**
 * Time Machine — visual history of the human-maintained Hermes configuration
 * layer.
 *
 * Gives Hermes Desktop users a chronological timeline of checkpoints over
 * their config (config.yaml, SOUL.md, memories, skills, cron, scripts,
 * plugins, desktop plugins, ...), a file-level diff against the adjacent or
 * current state, and a safety-gated restore flow (preview plan → confirm →
 * apply; the backend auto-checkpoints current state first, rejects stale
 * plans, stages/validates, and rolls back on mid-restore failure).
 *
 * Backend: /api/plugins/time-machine/ (FastAPI router in
 * dashboard/plugin_api.py; requires the plugin in plugins.enabled). All
 * content-bearing data comes from this plugin's own REST namespace — never
 * through chat/tool methods — and the backend redacts secret-shaped values
 * before they reach this page.
 *
 * Plain ESM loaded uncompiled: jsx() calls, NOT JSX syntax. Only
 * @hermes/plugin-sdk, react, react/jsx-runtime resolve.
 */

import {
  Badge,
  Button,
  cn,
  Codicon,
  EmptyState,
  ErrorState,
  haptic,
  host,
  Input,
  Loader,
  PALETTE_AREA,
  queryClient,
  relativeTime,
  ROUTES_AREA,
  ScrollArea,
  SegmentedControl,
  SIDEBAR_NAV_AREA,
  STATUSBAR_AREAS,
  Tip,
  useQuery
} from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'
import { useState } from 'react'

const ID = 'time-machine'
const REFETCH_MS = 30_000
const TIMELINE_LIMIT = 100
const MAX_FILE_CHECKS = 200

let rest = null

// ---------------------------------------------------------------------------
// CSS — theme vars only (content-compared so hot-reloads replace stale rules)
// ---------------------------------------------------------------------------

function ensurePluginStyles() {
  if (typeof document === 'undefined') return
  const css = [
    '.tm-dot { display: inline-block; width: 8px; height: 8px; border-radius: 9999px; background: var(--ui-text-quaternary); }',
    '.tm-dot-ok { background: var(--ui-green); }',
    '.tm-dot-down { background: var(--ui-red); }',
    '.tm-split { display: grid; grid-template-columns: minmax(18rem, 34%) minmax(0, 1fr); }',
    '.tm-row[aria-selected="true"] { background: var(--ui-bg-quaternary); }',
    '.tm-diff-pre { white-space: pre-wrap; overflow-wrap: anywhere; font-family: var(--ui-monospace, monospace); font-size: 0.6875rem; line-height: 1.45; color: var(--ui-text-secondary); }',
    '.tm-modal-backdrop { position: fixed; inset: 0; z-index: 60; display: flex; align-items: flex-start; justify-content: center; padding: 3rem 1rem 1rem; background: color-mix(in srgb, var(--ui-bg-editor) 55%, transparent); }',
    '.tm-modal { width: min(44rem, 100%); max-height: 80vh; display: flex; flex-direction: column; border: 1px solid var(--ui-stroke-secondary); border-radius: 8px; background: var(--ui-bg-elevated); box-shadow: 0 12px 40px rgba(0,0,0,0.45); }',
    '.tm-modal-body { overflow-y: auto; padding: 0.875rem 1rem; }',
    '.tm-action-create { color: var(--ui-green); }',
    '.tm-action-modified { color: var(--ui-orange); }',
    '.tm-action-deleted { color: var(--ui-red); }',
    '.tm-filecheck { display: flex; align-items: center; gap: 0.5rem; padding: 0.1875rem 0.25rem; font-size: 0.75rem; color: var(--ui-text-secondary); }'
  ].join('\n')
  let style = document.getElementById('tm-styles')
  if (!style) {
    style = document.createElement('style')
    style.id = 'tm-styles'
    document.head.appendChild(style)
  }
  if (style.textContent !== css) { style.textContent = css }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmtAge(iso) {
  if (!iso) return '—'
  const ms = new Date(iso).getTime()
  return Number.isFinite(ms) ? relativeTime(ms) : '—'
}

function fmtWhen(iso) {
  if (!iso) return ''
  try { return new Date(iso).toLocaleString() } catch { return iso }
}

function fmtBytes(n) {
  if (!Number.isFinite(n) || n <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  let i = 0
  let v = n
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1 }
  return `${v >= 100 ? Math.round(v) : v.toFixed(1)} ${units[i]}`
}

function triggerLabel(trigger) {
  return trigger === 'manual' ? 'Manual' : trigger === 'pre-restore' ? 'Safety' : 'Auto'
}

function triggerVariant(trigger) {
  return trigger === 'manual' ? 'outline' : trigger === 'pre-restore' ? 'warn' : 'muted'
}

function actionClass(action) {
  return action === 'created' || action === 'create'
    ? 'tm-action-create'
    : action === 'deleted' || action === 'delete'
      ? 'tm-action-deleted'
      : 'tm-action-modified'
}

function actionIcon(action) {
  return action === 'created' || action === 'create'
    ? 'diff-added'
    : action === 'deleted' || action === 'delete'
      ? 'diff-removed'
      : 'diff-modified'
}

function invalidate() {
  void queryClient.invalidateQueries({ queryKey: [ID] })
}

// ---------------------------------------------------------------------------
// Data hooks
// ---------------------------------------------------------------------------

function useBackendHealth() {
  return useQuery({
    queryKey: [ID, 'health'],
    queryFn: () => rest('/health'),
    refetchInterval: REFETCH_MS,
    staleTime: 10_000
  })
}

function useProfiles() {
  return useQuery({
    queryKey: [ID, 'profiles'],
    queryFn: () => rest('/profiles'),
    staleTime: 30_000,
    retry: false
  })
}

function useCheckpoints(profile) {
  return useQuery({
    queryKey: [ID, 'checkpoints', profile],
    queryFn: () => rest(`/checkpoints?profile=${encodeURIComponent(profile)}&limit=${TIMELINE_LIMIT}`),
    refetchInterval: REFETCH_MS,
    staleTime: 5_000,
    retry: false
  })
}

function useCheckpointDetail(profile, cid) {
  return useQuery({
    queryKey: [ID, 'checkpoint', profile, cid],
    queryFn: () => rest(`/checkpoints/${encodeURIComponent(profile)}/${encodeURIComponent(cid)}`),
    enabled: Boolean(profile && cid),
    staleTime: 30_000,
    retry: false
  })
}

function useDiff(profile, cid, against) {
  return useQuery({
    queryKey: [ID, 'diff', profile, cid, against],
    queryFn: () => rest(`/checkpoints/${encodeURIComponent(profile)}/${encodeURIComponent(cid)}/diff?against=${against}`),
    enabled: Boolean(profile && cid),
    staleTime: 10_000,
    retry: false
  })
}

// ---------------------------------------------------------------------------
// Timeline row
// ---------------------------------------------------------------------------

function CheckpointRow({ item, selected, onSelect, onTogglePin, pinBusy }) {
  const summary = item.summary || {}
  const changed = (summary.created || 0) + (summary.modified || 0) + (summary.deleted || 0)
  return jsxs('div', {
    className: cn(
      'tm-row group flex w-full min-w-0 cursor-pointer items-start gap-2 px-3 py-2.5 text-left',
      'hover:bg-(--chrome-action-hover)'
    ),
    'aria-selected': selected ? 'true' : 'false',
    role: 'option',
    tabIndex: 0,
    onClick: () => onSelect(item),
    onKeyDown: e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(item) } },
    children: [
      jsx(Codicon, { className: 'mt-0.5 shrink-0 text-(--ui-text-tertiary)', name: 'history' }),
      jsxs('span', { className: 'min-w-0 flex-1', children: [
        jsxs('span', { className: 'flex min-w-0 items-center gap-1.5 text-xs', children: [
          jsx(Badge, { variant: triggerVariant(item.trigger), children: triggerLabel(item.trigger) }),
          item.label ? jsx('span', { className: 'truncate text-(--ui-text-primary)', children: item.label }) : null,
          item.pinned ? jsx(Codicon, { name: 'pin', className: 'shrink-0 text-(--ui-accent)' }) : null
        ] }),
        jsxs('span', { className: 'mt-0.5 flex items-center gap-2 text-[0.6875rem] text-(--ui-text-tertiary)', children: [
          jsx('span', { children: fmtAge(item.timestamp) }),
          jsx('span', { children: `${item.file_count} files · ${fmtBytes(item.total_size)}` }),
          changed > 0
            ? jsx('span', { children: `${summary.created || 0}▲ ${summary.modified || 0}~ ${summary.deleted || 0}▼` })
            : null
        ] })
      ] }),
      jsx(Button, {
        'aria-label': item.pinned ? 'Unpin checkpoint' : 'Pin checkpoint',
        disabled: pinBusy,
        onClick: e => { e.stopPropagation(); onTogglePin(item) },
        size: 'icon-xs',
        variant: 'ghost',
        children: jsx(Codicon, { name: item.pinned ? 'pin' : 'pin', className: item.pinned ? 'text-(--ui-accent)' : 'text-(--ui-text-quaternary)' })
      })
    ]
  })
}

// ---------------------------------------------------------------------------
// Diff / change detail
// ---------------------------------------------------------------------------

function ChangeItem({ entry }) {
  const [open, setOpen] = useState(false)
  const kind = entry.kind === 'binary' ? 'binary' : 'text'
  const hasText = kind === 'text' && !!entry.text_diff
  return jsxs('div', { className: 'border-b border-(--ui-stroke-tertiary)', children: [
    jsxs('button', {
      type: 'button',
      className: 'flex w-full min-w-0 items-center gap-2 px-3 py-2 text-left text-xs hover:bg-(--chrome-action-hover)',
      onClick: () => { haptic('tap'); setOpen(o => !o) },
      children: [
        jsx(Codicon, { name: actionIcon(entry.action), className: cn('shrink-0', actionClass(entry.action)) }),
        jsx('span', { className: 'min-w-0 flex-1 truncate text-(--ui-text-primary)', children: entry.path }),
        jsx('span', { className: 'shrink-0 uppercase text-[0.625rem] text-(--ui-text-quaternary)', children: entry.action }),
        kind === 'binary'
          ? jsx(Badge, { variant: 'muted', children: 'binary' })
          : hasText
            ? jsx(Codicon, { name: open ? 'chevron-down' : 'chevron-right', className: 'text-(--ui-text-quaternary)' })
            : null
      ]
    }),
    open
      ? jsx('div', { className: 'px-4 pb-2', children: hasText
          ? jsx('pre', { className: 'tm-diff-pre', children: entry.text_diff })
          : jsxs('div', { className: 'flex flex-wrap items-center gap-x-3 gap-y-1 text-[0.6875rem] text-(--ui-text-tertiary)', children: [
              jsx('span', { children: `type: ${entry.kind}` }),
              jsx('span', { children: `size: ${fmtBytes(entry.old ? entry.old.size : entry.size)} → ${fmtBytes(entry.new ? entry.new.size : entry.size)}` }),
              jsx('span', { children: `sha256: ${entry.new ? entry.new.sha256.slice(0, 12) : entry.sha256.slice(0, 12)}…` })
            ] })
      })
      : null
  ] })
}

function DiffList({ diff, against }) {
  if (!diff || !diff.items) return null
  const s = diff.summary || {}
  if (diff.items.length === 0) {
    return jsx('div', { className: 'px-3 py-4 text-xs text-(--ui-text-tertiary)', children: against === 'current'
      ? 'No changes since this checkpoint — tracked state matches it.'
      : 'This is the first checkpoint — nothing to compare with the previous state.' })
  }
  return jsxs('div', { children: [
    jsxs('div', { className: 'flex items-center gap-3 px-3 py-1.5 text-[0.6875rem] text-(--ui-text-secondary)', children: [
      jsx('span', { className: 'tm-action-create', children: `${s.created || 0} created` }),
      jsx('span', { className: 'tm-action-modified', children: `${s.modified || 0} modified` }),
      jsx('span', { className: 'tm-action-deleted', children: `${s.deleted || 0} deleted` }),
      diff.truncated ? jsx('span', { className: 'text-(--ui-text-quaternary)', children: '(list truncated)' }) : null
    ] }),
    jsx('div', { children: diff.items.map(entry => jsx(ChangeItem, { key: entry.path, entry })) })
  ] })
}

// ---------------------------------------------------------------------------
// Restore wizard
// ---------------------------------------------------------------------------

const RESTORE_SCOPE_OPTIONS = [
  { id: 'whole', label: 'Whole checkpoint' },
  { id: 'category', label: 'Category' },
  { id: 'files', label: 'Files' }
]

function RestoreModal({ profile, checkpoint, onClose, onDone }) {
  const [scope, setScope] = useState('whole')
  const [category, setCategory] = useState('')
  const [selectedFiles, setSelectedFiles] = useState(new Set())
  const [busy, setBusy] = useState(false)
  const [plan, setPlan] = useState(null)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)

  const files = checkpoint && checkpoint.files ? Object.keys(checkpoint.files) : []
  const categories = [...new Set(files.map(f => f.split('/')[0]))].sort()
  const visibleFiles = files.slice(0, MAX_FILE_CHECKS)
  const filesTruncated = files.length > MAX_FILE_CHECKS

  const close = () => { if (!busy) onClose() }

  const buildSelection = () => {
    if (scope === 'whole') return null
    if (scope === 'category') return category ? [{ category }] : null
    return [...selectedFiles]
  }

  const preview = async () => {
    const selection = buildSelection()
    if (scope === 'category' && !category) { setError('Choose a category first.'); return }
    if (scope === 'files' && selectedFiles.size === 0) { setError('Choose at least one file.'); return }
    setBusy(true); setError(null)
    try {
      const body = { profile, checkpoint_id: checkpoint.id }
      if (selection) body.selection = selection
      const res = await rest('/restore/preview', { method: 'POST', body })
      setPlan(res)
    } catch (e) {
      setError(e && e.message ? e.message : String(e))
    } finally { setBusy(false) }
  }

  const apply = async () => {
    if (!plan) return
    setBusy(true); setError(null)
    try {
      const res = await rest('/restore/apply', { method: 'POST', body: { plan_token: plan.token } })
      setResult(res)
      onDone()
    } catch (e) {
      let msg = e && e.message ? e.message : String(e)
      let detail = null
      if (e && e.detail && typeof e.detail === 'object') detail = e.detail
      setError({ message: msg, detail })
    } finally { setBusy(false) }
  }

  const planCounts = plan ? plan.summary : null
  const ver = result && result.verification
  const reloadHints = result && result.reload_hints

  return jsxs('div', {
    className: 'tm-modal-backdrop',
    onClick: close,
    children: [
      jsxs('div', {
        className: 'tm-modal',
        onClick: e => e.stopPropagation(),
        children: [
          jsxs('div', { className: 'flex items-center gap-2 border-b border-(--ui-stroke-secondary) px-4 py-3', children: [
            jsx(Codicon, { name: 'history' }),
            jsx('div', { className: 'text-sm font-semibold', children: 'Restore checkpoint' }),
            jsx('div', { className: 'text-xs text-(--ui-text-tertiary)', children: checkpoint.id }),
            jsx('div', { className: 'ml-auto' }),
            jsx(Button, { size: 'icon-xs', variant: 'ghost', disabled: busy, onClick: close, children: jsx(Codicon, { name: 'close' }) })
          ] }),
          jsx('div', { className: 'tm-modal-body', children: [
            // Result panel
            result ? jsxs('div', { children: [
              jsxs('div', { className: 'flex items-center gap-2 text-sm font-medium', children: [
                jsx(Codicon, { name: ver && ver.ok ? 'check' : 'warning', className: ver && ver.ok ? 'text-(--ui-green)' : 'text-(--ui-orange)' }),
                jsx('span', { children: ver && ver.ok ? 'Restore applied and verified' : 'Restore applied with verification issues' })
              ] }),
              jsx('div', { className: 'mt-2 text-xs text-(--ui-text-secondary)', children: `${result.applied.length} mutation(s) applied · safety checkpoint ${result.safety_checkpoint} created before restore` }),
              ver ? jsxs('div', { className: 'mt-2 text-xs text-(--ui-text-tertiary)', children: [
                jsx('span', { children: `verified ${ver.verified}/${ver.checked}` }),
                ver.failed && ver.failed.length > 0
                  ? jsx('ul', { className: 'mt-1 list-disc pl-4', children: ver.failed.map(f => jsx('li', { key: f.path, children: `${f.path}: ${f.error}` })) })
                  : null
              ] }) : null,
              reloadHints && reloadHints.length > 0
                ? jsxs('div', { className: 'mt-3 border-t border-(--ui-stroke-tertiary) pt-2 text-xs', children: [
                    jsx('div', { className: 'font-medium text-(--ui-text-secondary)', children: 'Needs a reload (no auto-restart):' }),
                    jsx('ul', { className: 'mt-1 list-disc pl-4 text-(--ui-text-tertiary)', children: reloadHints.map(h => jsx('li', { key: h, children: h })) })
                  ] })
                : null,
              jsx('div', { className: 'mt-4 flex justify-end gap-2', children: jsx(Button, { variant: 'secondary', onClick: close, children: 'Close' }) })
            ] }) : null,

            // Error panel
            error ? jsxs('div', { className: 'mb-3 border border-(--ui-red) bg-(--ui-bg-tertiary) p-3 text-xs text-(--ui-text-secondary)', children: [
              jsxs('div', { className: 'flex items-start gap-2', children: [
                jsx(Codicon, { name: 'error', className: 'mt-0.5 text-(--ui-red)' }),
                jsx('div', { className: 'min-w-0', children: [
                  jsx('div', { children: typeof error === 'string' ? error : error.message }),
                  error && typeof error === 'object' && error.detail
                    ? jsxs('div', { className: 'mt-1 text-(--ui-text-tertiary)', children: [
                        jsx('div', { children: `applied before failure: ${(error.detail.applied || []).length}` }),
                        error.detail.rollback_errors && error.detail.rollback_errors.length > 0
                          ? jsx('ul', { className: 'mt-1 list-disc pl-4', children: error.detail.rollback_errors.map(r => jsx('li', { key: r.path, children: `rollback failed for ${r.path}: ${r.error}` })) })
                          : null
                      ] })
                    : null
                ] })
              ] }),
              !busy ? jsx('div', { className: 'mt-2 flex gap-2', children: [
                jsx(Button, { size: 'sm', variant: 'secondary', onClick: () => { setError(null); setPlan(null) }, children: 'Back to preview' }),
                jsx(Button, { size: 'sm', variant: 'ghost', onClick: close, children: 'Close' })
              ] }) : null
            ] }) : null,

            // Preview / scope
            !result && !error ? (plan
              ? jsxs('div', { children: [
                  jsxs('div', { className: 'text-xs text-(--ui-text-secondary)', children: [
                    jsx('span', { children: 'Restoring ' }),
                    jsx('span', { className: 'font-medium', children: `${plan.checkpoint_id} (${plan.scope === 'whole' ? 'whole checkpoint' : 'selected files'})` }),
                    jsx('span', { children: ' — plan bound to current state; expires ' }),
                    jsx('span', { className: 'font-medium', children: fmtWhen(plan.expires_at) })
                  ] }),
                  planCounts
                    ? jsxs('div', { className: 'mt-2 flex gap-3 text-xs', children: [
                        jsx('span', { className: 'tm-action-create', children: `${planCounts.create || 0} create` }),
                        jsx('span', { className: 'tm-action-modified', children: `${planCounts.replace || 0} replace` }),
                        jsx('span', { className: 'tm-action-deleted', children: `${planCounts.delete || 0} delete` }),
                        planCounts.skip ? jsx('span', { className: 'text-(--ui-text-quaternary)', children: `${planCounts.skip} unchanged` }) : null
                      ] })
                    : null,
                  jsx('div', { className: 'mt-2 max-h-48 overflow-y-auto border border-(--ui-stroke-tertiary)', children: plan.items.map(item => jsxs('div', {
                    key: item.path,
                    className: 'flex items-center gap-2 border-b border-(--ui-stroke-tertiary) px-2 py-1 text-[0.6875rem]',
                    children: [
                      jsx(Codicon, { name: actionIcon(item.action), className: cn('shrink-0', actionClass(item.action)) }),
                      jsx('span', { className: 'min-w-0 flex-1 truncate', children: item.path }),
                      jsx('span', { className: cn('shrink-0 uppercase', actionClass(item.action)), children: item.action })
                    ]
                  })) }),
                  jsxs('div', { className: 'mt-4 flex items-center justify-between gap-2', children: [
                    jsx('div', { className: 'text-[0.6875rem] text-(--ui-text-tertiary)', children: 'A safety checkpoint is created automatically before restore, so this is undoable.' }),
                    jsxs('div', { className: 'flex gap-2', children: [
                      jsx(Button, { size: 'sm', variant: 'ghost', disabled: busy, onClick: () => setPlan(null), children: 'Back' }),
                      jsx(Button, { size: 'sm', disabled: busy, onClick: apply, children: busy ? 'Restoring…' : 'Restore now' })
                    ] })
                  ] })
                ] })
              : jsxs('div', { children: [
                  jsx('div', { className: 'text-xs text-(--ui-text-secondary)', children: `Pick what to restore from checkpoint ${checkpoint.id} (${files.length} tracked files).` }),
                  jsx('div', { className: 'mt-3', children: jsx(SegmentedControl, { value: scope, onChange: setScope, options: RESTORE_SCOPE_OPTIONS }) }),
                  scope === 'whole'
                    ? jsx('div', { className: 'mt-3 text-xs text-(--ui-text-tertiary)', children: 'Restores the whole checkpoint: files will be created/replaced to match it, and files added since the checkpoint will be deleted. No files outside the tracked catalog are touched.' })
                    : null,
                  scope === 'category'
                    ? jsx('select', {
                        className: 'mt-3 w-full border border-(--ui-stroke-secondary) bg-(--ui-bg-secondary) px-2 py-1.5 text-xs',
                        value: category,
                        onChange: e => setCategory(e.target.value),
                        children: [
                          jsx('option', { value: '', children: 'Choose a category…' }),
                          ...categories.map(c => jsx('option', { key: c, value: c, children: c }))
                        ]
                      })
                    : null,
                  scope === 'files'
                    ? jsxs('div', { className: 'mt-3', children: [
                        jsx('div', { className: 'max-h-56 overflow-y-auto border border-(--ui-stroke-tertiary)', children: visibleFiles.map(f => jsx('label', {
                          key: f,
                          className: 'tm-filecheck',
                          children: [
                            jsx('input', {
                              type: 'checkbox',
                              checked: selectedFiles.has(f),
                              onChange: e => {
                                const next = new Set(selectedFiles)
                                if (e.target.checked) next.add(f); else next.delete(f)
                                setSelectedFiles(next)
                              }
                            }),
                            jsx('span', { className: 'min-w-0 flex-1 truncate', children: f })
                          ]
                        })) }),
                        filesTruncated ? jsx('div', { className: 'mt-1 text-[0.6875rem] text-(--ui-text-quaternary)', children: `Showing first ${MAX_FILE_CHECKS} files — for larger checkpoints use Category or Whole scope.` }) : null
                      ] })
                    : null,
                  jsx('div', { className: 'mt-4 flex justify-end gap-2', children: jsx(Button, { size: 'sm', disabled: busy, onClick: preview, children: busy ? 'Preparing…' : 'Preview restore' }) })
                ] })
            ) : null
          ] })
        ]
      })
    ]
  })
}

function NewCheckpointModal({ profile, onClose, onCreated }) {
  const [label, setLabel] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const create = async () => {
    setBusy(true); setError(null)
    try {
      await rest('/checkpoints', { method: 'POST', body: { profile, label } })
      onCreated()
    } catch (e) {
      setError(e && e.message ? e.message : String(e))
      setBusy(false)
    }
  }

  return jsxs('div', {
    className: 'tm-modal-backdrop',
    onClick: () => { if (!busy) onClose() },
    children: [
      jsxs('div', {
        className: 'tm-modal',
        onClick: e => e.stopPropagation(),
        children: [
          jsxs('div', { className: 'flex items-center gap-2 border-b border-(--ui-stroke-secondary) px-4 py-3', children: [
            jsx(Codicon, { name: 'checkpoint' }),
            jsx('div', { className: 'text-sm font-semibold', children: 'New checkpoint' }),
            jsx('div', { className: 'text-xs text-(--ui-text-tertiary)', children: profile }),
            jsx('div', { className: 'ml-auto' }),
            jsx(Button, { size: 'icon-xs', variant: 'ghost', disabled: busy, onClick: () => onClose(), children: jsx(Codicon, { name: 'close' }) })
          ] }),
          jsx('div', { className: 'tm-modal-body', children: [
            jsx('div', { className: 'text-xs text-(--ui-text-secondary)', children: 'Capture the current tracked configuration as a named recovery point. Manual checkpoints are never deduplicated away.' }),
            error ? jsx('div', { className: 'mt-2 text-xs text-(--ui-red)', children: error }) : null,
            jsx('div', { className: 'mt-3', children: jsx(Input, {
              value: label,
              onChange: e => setLabel(e.target.value),
              placeholder: 'Optional label — e.g. "Before rebuilding Outreach profile"',
              onKeyDown: e => { if (e.key === 'Enter') void create() }
            }) }),
            jsx('div', { className: 'mt-4 flex justify-end gap-2', children: [
              jsx(Button, { size: 'sm', variant: 'ghost', disabled: busy, onClick: () => onClose(), children: 'Cancel' }),
              jsx(Button, { size: 'sm', disabled: busy, onClick: create, children: busy ? 'Creating…' : 'Create checkpoint' })
            ] })
          ] })
        ]
      })
    ]
  })
}

// ---------------------------------------------------------------------------
// Detail panel
// ---------------------------------------------------------------------------

function DetailPanel({ profile, cid, against, setAgainst, onRestore }) {
  const detail = useCheckpointDetail(profile, cid)
  const diff = useDiff(profile, cid, against)
  const manifest = detail.data ? detail.data.checkpoint : null

  if (!detail.data && detail.isLoading) {
    return jsx('div', { className: 'grid h-full place-items-center', children: jsx(Loader, { label: 'Loading checkpoint…' }) })
  }
  if (detail.isError || !manifest) {
    return jsx(EmptyState, { title: 'Checkpoint unavailable', description: 'Could not load this checkpoint — it may be corrupt or missing.' })
  }

  const files = Object.keys(manifest.files || {})

  return jsxs('div', { className: 'flex h-full min-h-0 flex-col', children: [
    jsxs('div', { className: 'flex flex-wrap items-center gap-2 border-b border-(--ui-stroke-tertiary) px-4 py-2.5', children: [
      jsx('div', { className: 'text-xs font-medium text-(--ui-text-primary)', children: manifest.label || manifest.id }),
      jsx(Badge, { variant: triggerVariant(manifest.trigger), children: triggerLabel(manifest.trigger) }),
      manifest.pinned ? jsx(Badge, { variant: 'outline', children: 'pinned' }) : null,
      jsx('div', { className: 'ml-auto' }),
      jsx(SegmentedControl, {
        value: against,
        onChange: setAgainst,
        options: [
          { id: 'current', label: 'vs now' },
          { id: 'adjacent', label: 'vs previous' }
        ]
      }),
      jsx(Button, { size: 'sm', variant: 'secondary', onClick: onRestore, children: [jsx(Codicon, { name: 'history' }), ' Restore…'] })
    ] }),
    jsxs('div', { className: 'border-b border-(--ui-stroke-tertiary) px-4 py-2 text-[0.6875rem] text-(--ui-text-tertiary)', children: [
      jsx('span', { children: `${manifest.file_count} files · ${fmtBytes(manifest.total_size)} · ${fmtWhen(manifest.timestamp)}` }),
      manifest.prev_id ? jsx('span', { className: 'ml-3', children: `previous: ${manifest.prev_id}` }) : null
    ] }),
    jsx('div', { className: 'min-h-0 flex-1 overflow-y-auto', children: jsx(DiffList, { diff: diff.data, against }) })
  ] })
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

function TimeMachinePage() {
  const health = useBackendHealth()
  const profilesQ = useProfiles()
  const [profile, setProfile] = useState('default')
  const [selectedCid, setSelectedCid] = useState(null)
  const [against, setAgainst] = useState('current')
  const [newOpen, setNewOpen] = useState(false)
  const [restoreCid, setRestoreCid] = useState(null)
  const [pinBusy, setPinBusy] = useState(false)
  const [pruneBusy, setPruneBusy] = useState(false)

  const profiles = (profilesQ.data && profilesQ.data.profiles) || []
  const checkpointsQ = useCheckpoints(profile)
  const checkpoints = (checkpointsQ.data && checkpointsQ.data.items) || []
  const selectedDetail = useCheckpointDetail(profile, selectedCid)
  const restoreManifest = restoreCid ? (selectedDetail.data && selectedDetail.data.checkpoint) || null : null

  // Keep the selection valid when the profile changes.
  const switchProfile = next => {
    setProfile(next)
    setSelectedCid(null)
    setRestoreCid(null)
  }

  const backendOk = !!(health.data && health.data.ok)
  const statusText = backendOk
    ? 'Backend connected'
    : (health.isLoading ? 'Connecting to backend…' : 'Backend unavailable')

  const togglePin = async item => {
    setPinBusy(true)
    try {
      await rest(`/checkpoints/${encodeURIComponent(profile)}/${encodeURIComponent(item.id)}/pin`, {
        method: 'POST',
        body: { pinned: !item.pinned }
      })
      invalidate()
    } finally { setPinBusy(false) }
  }

  const prune = async () => {
    setPruneBusy(true)
    try {
      const res = await rest('/checkpoints/prune', { method: 'POST', body: { profile } })
      host.notify({ kind: 'success', message: `Pruned ${res.removed_checkpoints} checkpoint(s), ${res.freed_objects} object(s) freed` })
      invalidate()
    } catch (e) {
      host.notify({ kind: 'error', message: e && e.message ? e.message : 'Prune failed' })
    } finally { setPruneBusy(false) }
  }

  if (health.isError) {
    return jsx(ErrorState, {
      title: 'Time Machine backend unavailable',
      description: 'The plugin backend could not be reached. Add time-machine to plugins.enabled in ~/.hermes/config.yaml and restart the backend.',
      children: jsx(Button, { size: 'sm', onClick: () => { haptic('tap'); invalidate() }, children: 'Retry' })
    })
  }

  const profileOptions = profiles.length
    ? profiles.map(p => ({ id: p.id, label: p.id }))
    : [{ id: 'default', label: 'default' }]
  if (profileOptions.every(o => o.id !== profile)) setProfile(profileOptions[0].id)

  return jsxs('div', { className: 'flex h-full flex-col', children: [
    // Header
    jsxs('div', { className: 'flex items-center gap-2 border-b border-(--ui-stroke-tertiary) px-4 py-3', children: [
      jsx(Codicon, { name: 'history', size: '1.1rem' }),
      jsx('div', { className: 'text-sm font-semibold', children: 'Time Machine' }),
      jsx('div', { className: 'text-xs text-(--ui-text-tertiary)', children: 'configuration history & safe rollback' }),
      jsx('div', { className: 'ml-auto' }),
      jsx(SegmentedControl, { value: profile, onChange: switchProfile, options: profileOptions }),
      jsx(Button, { size: 'sm', variant: 'ghost', disabled: pruneBusy, onClick: prune, children: [jsx(Codicon, { name: 'trash' }), ' Prune'] }),
      jsx(Button, { size: 'sm', variant: 'secondary', onClick: () => { haptic('tap'); setNewOpen(true) }, children: [jsx(Codicon, { name: 'checkpoint' }), ' New checkpoint'] }),
      jsx(Button, { size: 'sm', variant: 'ghost', onClick: () => { haptic('tap'); invalidate() }, children: 'Refresh' })
    ] }),
    // Body
    jsxs('div', { className: 'tm-split min-h-0 flex-1', children: [
      // Timeline
      jsx('div', { className: 'min-w-0 overflow-hidden border-r border-(--ui-stroke-tertiary)', children: jsx(ScrollArea, {
        className: 'h-full',
        children: checkpointsQ.isLoading && !checkpoints.length
          ? jsx('div', { className: 'grid h-full place-items-center', children: jsx(Loader, { label: 'Loading checkpoints…' }) })
          : checkpoints.length
            ? jsxs('div', { className: 'py-1', children: [
                jsxs('div', { className: 'px-3 pb-1 pt-2 text-[0.625rem] font-medium uppercase tracking-wide text-(--ui-text-quaternary)', children: [
                  jsx('span', { children: `${checkpoints.length} checkpoints` }),
                  jsx('span', { className: 'ml-2 text-(--ui-text-tertiary)', children: statusText })
                ] }),
                checkpoints.map(item => jsx(CheckpointRow, {
                  key: item.id,
                  item,
                  selected: selectedCid === item.id,
                  onSelect: c => { haptic('tap'); setSelectedCid(c.id); setRestoreCid(null) },
                  onTogglePin: togglePin,
                  pinBusy
                }))
              ] })
            : jsxs('div', { className: 'p-4', children: [
                jsxs('div', { className: 'mb-2 flex items-center gap-2 text-xs text-(--ui-text-secondary)', children: [
                  jsx('span', { className: cn('tm-dot', backendOk ? 'tm-dot-ok' : (health.isLoading ? '' : 'tm-dot-down')) }),
                  jsx('span', { children: statusText })
                ] }),
                jsx(EmptyState, {
                  title: 'No checkpoints yet',
                  description: 'Create a checkpoint manually, or edit tracked config (config.yaml, SOUL.md, skills, cron, …) — Time Machine records it automatically while the Desktop runs.'
                })
              ] })
      }) }),
      // Detail
      jsx('div', { className: 'min-w-0 overflow-hidden', children: selectedCid
        ? jsx(DetailPanel, {
            profile,
            cid: selectedCid,
            against,
            setAgainst: setAgainst,
            onRestore: () => { haptic('tap'); setRestoreCid(selectedCid) }
          })
        : jsx('div', { className: 'grid h-full place-items-center', children: jsx(EmptyState, {
            title: 'Select a checkpoint',
            description: 'Pick a checkpoint from the timeline to see what changed and restore it safely.'
          }) })
      })
    ] }),
    newOpen ? jsx(NewCheckpointModal, {
      profile,
      onClose: () => setNewOpen(false),
      onCreated: () => { setNewOpen(false); invalidate() }
    }) : null,
    restoreCid && restoreManifest ? jsx(RestoreModal, {
      profile,
      checkpoint: restoreManifest,
      onClose: () => setRestoreCid(null),
      onDone: () => invalidate()
    }) : null
  ] })
}

// ---------------------------------------------------------------------------
// Statusbar chip — shown only when the backend is reachable
// ---------------------------------------------------------------------------

function TimeMachineChip() {
  const q = useBackendHealth()
  if (!q.data || !q.data.ok) return null
  return jsx(Tip, {
    label: 'Time Machine — backend connected',
    children: jsx('button', {
      className: cn(
        'inline-flex h-full items-center gap-1 px-1.5 text-[0.6875rem] transition-colors',
        'text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover) hover:text-foreground'
      ),
      type: 'button',
      onClick: () => { haptic('tap'); host.navigate('/time-machine') },
      children: [
        jsx('span', { className: 'tm-dot tm-dot-ok' }),
        jsx('span', { children: 'Time Machine' })
      ]
    })
  })
}

// ---------------------------------------------------------------------------
// Registration
// ---------------------------------------------------------------------------

export default {
  id: ID,
  name: 'Time Machine',
  description: 'Visual history of the human-maintained Hermes configuration layer — inspect what changed and safely put it back.',
  defaultEnabled: true,
  register(ctx) {
    rest = ctx.rest
    ensurePluginStyles()

    const openTimeMachine = () => {
      haptic('tap')
      host.navigate('/time-machine')
    }

    ctx.registerMany([
      {
        id: 'page',
        area: ROUTES_AREA,
        data: { path: '/time-machine' },
        title: 'Time Machine',
        render: () => jsx(TimeMachinePage, {})
      },
      {
        id: 'nav',
        area: SIDEBAR_NAV_AREA,
        order: 60,
        data: { path: '/time-machine', label: 'Time Machine', codicon: 'history' }
      },
      {
        id: 'chip',
        area: STATUSBAR_AREAS.right,
        order: 120,
        render: () => jsx(TimeMachineChip, {})
      },
      {
        id: 'open',
        area: PALETTE_AREA,
        data: {
          id: 'time-machine.open',
          label: 'Open Time Machine',
          keywords: ['time', 'machine', 'history', 'rollback', 'snapshot', 'checkpoint', 'restore', 'diff', 'undo'],
          run: openTimeMachine
        }
      }
    ])
  }
}
