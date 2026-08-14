/**
 * Time Machine — visual history of the human-maintained Hermes configuration
 * layer.
 *
 * Gives Hermes Desktop users a chronological timeline of checkpoints over
 * their config (config.yaml, SOUL.md, memories, skills, cron, scripts,
 * plugins, desktop plugins, ...), a file-level diff against adjacent/current
 * state, and a safety-gated restore flow (plan → bind → auto-checkpoint →
 * stage → apply → rollback-on-failure).
 *
 * This file is the SCAFFOLD milestone: the page, sidebar nav, palette
 * command, and statusbar chip are wired and load in the app; deep feature
 * logic (checkpoint engine, diff, restore) lands in the implementation
 * milestone. The backend lives at /api/plugins/time-machine/ (FastAPI router
 * in dashboard/plugin_api.py; requires the plugin in plugins.enabled).
 *
 * Plain ESM loaded uncompiled: jsx() calls, NOT JSX syntax. Only
 * @hermes/plugin-sdk, react, react/jsx-runtime resolve.
 */

import {
  Button,
  cn,
  Codicon,
  EmptyState,
  ErrorState,
  haptic,
  host,
  PALETTE_AREA,
  queryClient,
  ROUTES_AREA,
  ScrollArea,
  SIDEBAR_NAV_AREA,
  STATUSBAR_AREAS,
  Tip,
  useQuery
} from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'time-machine'
const REFETCH_MS = 30_000

let rest = null

// ---------------------------------------------------------------------------
// CSS — theme vars only
// ---------------------------------------------------------------------------

function ensurePluginStyles() {
  if (typeof document === 'undefined') return
  const css = [
    '.tm-dot { display: inline-block; width: 8px; height: 8px; border-radius: 9999px; background: var(--ui-text-quaternary); }',
    '.tm-dot-ok { background: var(--ui-green); }',
    '.tm-dot-down { background: var(--ui-red); }'
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
// Data hook
// ---------------------------------------------------------------------------

function useBackendHealth() {
  return useQuery({
    queryKey: [ID, 'health'],
    queryFn: () => rest('/health'),
    refetchInterval: REFETCH_MS,
    staleTime: 10_000
  })
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

function TimeMachinePage() {
  const q = useBackendHealth()

  if (q.isError) {
    return jsx(ErrorState, {
      title: 'Time Machine backend unavailable',
      description: 'The plugin backend could not be reached. Add time-machine to plugins.enabled in ~/.hermes/config.yaml and restart the backend.',
      children: jsx(Button, { size: 'sm', onClick: () => { haptic('tap'); void queryClient.invalidateQueries({ queryKey: [ID] }) }, children: 'Retry' })
    })
  }

  const backendOk = !!(q.data && q.data.ok)
  const statusText = backendOk
    ? 'Backend connected'
    : (q.isLoading ? 'Connecting to backend…' : 'Backend unavailable')

  return jsxs('div', { className: 'flex h-full flex-col', children: [
    // Header
    jsxs('div', { className: 'flex items-center gap-2 border-b border-(--ui-stroke-tertiary) px-4 py-3', children: [
      jsx(Codicon, { name: 'history', size: '1.1rem' }),
      jsx('div', { className: 'text-sm font-semibold', children: 'Time Machine' }),
      jsx('div', { className: 'text-xs text-(--ui-text-tertiary)', children: 'configuration history & safe rollback' }),
      jsx('div', { className: 'ml-auto' }),
      jsx(Button, { size: 'sm', variant: 'ghost', onClick: () => { haptic('tap'); void queryClient.invalidateQueries({ queryKey: [ID] }) }, children: 'Refresh' })
    ] }),
    // Body
    jsx(ScrollArea, { className: 'flex-1', children: jsxs('div', { className: 'pb-6', children: [
      jsxs('div', { className: 'flex items-center gap-2 px-4 py-2 text-xs text-(--ui-text-secondary)', children: [
        jsx('span', { className: cn('tm-dot', backendOk ? 'tm-dot-ok' : (q.isLoading ? '' : 'tm-dot-down')) }),
        jsx('span', { children: statusText })
      ] }),
      jsx(EmptyState, {
        title: 'No checkpoints yet',
        description: 'Time Machine is scaffolded. The checkpoint engine lands in the next milestone — check back soon.'
      })
    ] }) })
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
