/**
 * SSR render test for time-machine — page/chip skeleton.
 *
 * Mounts TimeMachinePage with react-dom/server, stubs the SDK, feeds a
 * healthy /health fixture, and asserts: page title, backend status line,
 * and the "no checkpoints yet" placeholder. Also renders the statusbar chip
 * and asserts it shows when the backend is reachable.
 *
 * Run: HERMES_AGENT_NODE_MODULES=<dir with react+react-dom> node tests/esm-render.mjs
 */

import { mkdirSync, writeFileSync } from 'node:fs'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { dirname, join } from 'node:path'

const __dirname = dirname(fileURLToPath(import.meta.url))
const STUBS_DIR = join(__dirname, '.stubs')
// Resolve the plugin relative to this repo layout (tests/ sits next to plugin.js).
const PLUGIN_PATH = join(__dirname, '..', 'plugin.js')
// React/react-dom live in the Hermes desktop app's node_modules. Point
// HERMES_AGENT_NODE_MODULES at them, or default to a sibling node_modules.
const REPO = process.env.HERMES_AGENT_NODE_MODULES || join(__dirname, '..', '..', 'node_modules')

// ---------------------------------------------------------------------------
// Healthy backend fixture
// ---------------------------------------------------------------------------

const FIXTURE_HEALTH = { ok: true, plugin: 'time-machine', status: 'skeleton' }

// ---------------------------------------------------------------------------
// SDK stub — useQuery returns the fixture for /health
// ---------------------------------------------------------------------------

const FIXTURE_HEALTH_JSON = JSON.stringify(FIXTURE_HEALTH)

const USE_QUERY_SRC = `opts => {
  const path = opts && opts.queryKey ? opts.queryKey[1] || '' : ''
  const FIXTURE = ${FIXTURE_HEALTH_JSON}
  if (path === 'health') return { data: FIXTURE, isLoading: false, isError: false, error: null }
  return { data: null, isLoading: false, isError: false, error: null }
}`

const sdkStub = {
  // String stubs act as host element names: React renders <Button>..children..</Button>,
  // so children (labels, nested content) survive renderToString. Only components
  // that turn props into text (EmptyState/ErrorState) are function stubs.
  Button: 'Button',
  cn: (...args) => args.filter(Boolean).join(' '),
  Codicon: 'Codicon',
  EmptyState: ({ title, description }) => 'EmptyState|' + (title || '') + '|' + (description || ''),
  ErrorState: ({ title, description, children }) => 'ErrorState|' + (title || '') + '|' + (description || '') + '|' + (children || ''),
  haptic: () => {},
  host: { navigate: () => {}, notify: () => {}, state: {} },
  PALETTE_AREA: 'palette',
  queryClient: { invalidateQueries: () => {}, getQueryCache: () => ({ findAll: () => [] }) },
  ROUTES_AREA: 'routes',
  ScrollArea: 'ScrollArea',
  SIDEBAR_NAV_AREA: 'sidebar.nav',
  STATUSBAR_AREAS: { left: 'statusBar.left', right: 'statusBar.right' },
  Tip: 'Tip',
  useQuery: USE_QUERY_SRC
}

// ---------------------------------------------------------------------------
// Write stubs
// ---------------------------------------------------------------------------

mkdirSync(STUBS_DIR, { recursive: true })

function serializeStub(obj) {
  return Object.entries(obj)
    .map(([k, v]) => {
      if (typeof v === 'function') return `export const ${k} = ${v.toString()}`
      if (typeof v === 'string' && /^(opts|\()/.test(v.trim())) return `export const ${k} = ${v}`
      return `export const ${k} = ${JSON.stringify(v)}`
    })
    .join('\n')
}

const sdkSrc = serializeStub(sdkStub) + '\n'
writeFileSync(join(STUBS_DIR, 'render-sdk.mjs'), sdkSrc)

writeFileSync(
  join(STUBS_DIR, 'render-react.mjs'),
  `export * from ${JSON.stringify(pathToFileURL(join(REPO, 'react', 'index.js')).href)}\n`
)
writeFileSync(
  join(STUBS_DIR, 'render-jsx-runtime.mjs'),
  `export * from ${JSON.stringify(pathToFileURL(join(REPO, 'react', 'jsx-runtime.js')).href)}\n`
)

const stubUrls = {
  '@hermes/plugin-sdk': pathToFileURL(join(STUBS_DIR, 'render-sdk.mjs')).href,
  react: pathToFileURL(join(STUBS_DIR, 'render-react.mjs')).href,
  'react/jsx-runtime': pathToFileURL(join(STUBS_DIR, 'render-jsx-runtime.mjs')).href
}

writeFileSync(
  join(__dirname, 'render-loader-hooks.mjs'),
  `const STUB_URLS = ${JSON.stringify(stubUrls, null, 2)}\n\n` +
    'export function resolve(specifier, context, nextResolve) {\n' +
    '  if (STUB_URLS[specifier]) {\n' +
    '    return { url: STUB_URLS[specifier], shortCircuit: true }\n' +
    '  }\n' +
    '  return nextResolve(specifier, context)\n' +
    '}\n'
)

import { register } from 'node:module'
register(new URL('./render-loader-hooks.mjs', import.meta.url))

// ---------------------------------------------------------------------------
// Render
// ---------------------------------------------------------------------------

let failures = 0
const check = (cond, msg) => {
  if (!cond) { failures += 1; console.error('FAIL: ' + msg) }
}

let plugin
try {
  plugin = (await import(pathToFileURL(PLUGIN_PATH).href + '?t=' + Date.now())).default
} catch (err) {
  check(false, 'plugin import failed: ' + (err && err.stack ? err.stack : err))
  process.exit(1)
}

const contributions = []
const ctx = {
  rest: path => {
    if (path === 'health') return Promise.resolve(FIXTURE_HEALTH)
    return Promise.resolve({ ok: false })
  },
  socket: () => () => {},
  storage: { get: () => undefined, set: () => {} },
  i18n: { register: () => {} },
  onDispose: () => {},
  register: c => contributions.push(c),
  registerMany: cs => contributions.push(...cs)
}
plugin.register(ctx)

const page = contributions.find(c => c.area === 'routes')
const chip = contributions.find(c => c.area === 'statusBar.right')
check(!!page, 'page contribution exists')
check(!!chip, 'chip contribution exists')

const React = (await import(pathToFileURL(join(REPO, 'react', 'index.js')).href)).default
const { renderToString } = await import(pathToFileURL(join(REPO, 'react-dom', 'server.browser.js')).href)

let html = ''
try {
  html = renderToString(React.createElement(page.render))
  check(html.length > 100, 'rendered html is non-trivial (len=' + html.length + ')')
} catch (err) {
  check(false, 'renderToString threw: ' + (err && err.stack ? err.stack : err))
}

const expectIn = (needle, label) => check(html.includes(needle), 'html contains ' + label + ' (' + needle + ')')

// Page structure
expectIn('Time Machine', 'page title')
expectIn('configuration history', 'subtitle')
expectIn('Backend connected', 'backend status line')
expectIn('No checkpoints yet', 'empty-state title')

// Chip renders when backend is healthy
let chipHtml = ''
try {
  chipHtml = renderToString(React.createElement(chip.render))
  check(chipHtml.includes('Time Machine'), 'chip html contains label')
} catch (err) {
  check(false, 'chip renderToString threw: ' + (err && err.stack ? err.stack : err))
}

console.log(failures === 0 ? 'PASS: time-machine SSR render (' + html.length + ' chars)' : 'FAILURES: ' + failures)
process.exit(failures === 0 ? 0 : 1)
