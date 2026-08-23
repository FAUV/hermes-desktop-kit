/* Durable real-ESM loader contract for spotify-desktop/plugin.js. */
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { register } from 'node:module'

const root = dirname(dirname(fileURLToPath(import.meta.url)))
const pluginPath = join(root, 'desktop', 'plugin.js')
const stubs = mkdtempSync(join(tmpdir(), 'spotify-desktop-esm-'))
const stubUrl = name => pathToFileURL(join(stubs, name)).href

writeFileSync(join(stubs, 'sdk.mjs'), `
export const ROUTES_AREA = 'routes'
export const SIDEBAR_NAV_AREA = 'sidebar.nav'
export const PALETTE_AREA = 'palette'
export const host = { navigations: [], navigate(path) { this.navigations.push(path) } }
// SDK contract: usePluginI18n(id) IS the translator (plugin-i18n.ts).
export const usePluginI18n = () => key => key
`)
writeFileSync(join(stubs, 'react.mjs'), `
export const useState = initial => [initial, () => {}]
export const useEffect = effect => { const cleanup = effect(); if (typeof cleanup === 'function') cleanup() }
`)
writeFileSync(join(stubs, 'jsx.mjs'), `
export const jsx = (type, props) => ({ type, props: props || {} })
export const jsxs = (type, props) => ({ type, props: props || {} })
`)
writeFileSync(join(stubs, 'loader.mjs'), `
const stubs = ${JSON.stringify({
  '@hermes/plugin-sdk': stubUrl('sdk.mjs'),
  react: stubUrl('react.mjs'),
  'react/jsx-runtime': stubUrl('jsx.mjs')
})}
const plugin = ${JSON.stringify(pathToFileURL(pluginPath).href)}
export function resolve(specifier, context, nextResolve) {
  if (stubs[specifier]) return { url: stubs[specifier], shortCircuit: true }
  return nextResolve(specifier, context)
}
export async function load(url, context, nextLoad) {
  const result = await nextLoad(url, context)
  if (url === plugin) return { format: 'module', source: result.source, shortCircuit: true }
  return result
}
`)

let failures = 0
const check = (condition, message) => {
  if (!condition) { failures += 1; console.error('FAIL: ' + message) }
}

try {
  const source = readFileSync(pluginPath, 'utf8')
  const imports = source.match(/(?:from|import)\s*['"][^'"]+['"]/g) || []
  const allowed = new Set(["from '@hermes/plugin-sdk'", "from 'react'", "from 'react/jsx-runtime'"])
  check(imports.every(value => allowed.has(value)), 'plugin has a runtime-loader-unsafe import')
  // Uncompiled JSX, not every '<' (loop guards like `i < n` are legal JS).
  check(!/<[A-Za-z]/.test(source), 'plugin contains uncompiled JSX')

  register(pathToFileURL(join(stubs, 'loader.mjs',)).href)
  const plugin = (await import(pathToFileURL(pluginPath).href + '?t=' + Date.now())).default
  check(plugin.id === 'spotify-desktop', 'plugin id mismatch')
  check(typeof plugin.register === 'function', 'missing register function')

  const contributions = []
  const disposers = []
  plugin.register({
    rest: path => Promise.resolve(path === '/status' ? { ok: true } : {}),
    registerMany: entries => contributions.push(...entries),
    onDispose: dispose => disposers.push(dispose)
  })
  check(contributions.length === 3, 'expected exactly route/nav/palette contributions')
  const page = contributions.find(item => item.area === 'routes')
  const nav = contributions.find(item => item.area === 'sidebar.nav')
  const palette = contributions.find(item => item.area === 'palette')
  check(page?.data?.path === '/spotify' && typeof page.render === 'function', 'missing /spotify route')
  const renderedPage = page.render()
  const pageTree = renderedPage.type()
  check(pageTree.type === 'main' && pageTree.props.className === 'spotify-desktop-page', 'route renders Hermes-authored music page shell')
  const kids = pageTree.props.children
  const kidTypes = (Array.isArray(kids) ? kids : [kids]).map(k => k?.type).map(t => (typeof t === 'function' ? t.name : t))
  // Structure contract: header + connection state + content + now-playing bar
  // + privacy notice + deletion notice, with section-level navigation living
  // inside the content workspace.
  check(kidTypes.includes('header') && kidTypes.includes('ConnectionNotice') && kidTypes.includes('section') && kidTypes.includes('NowPlayingBar') && kidTypes.includes('PrivacyNotice'), 'page includes header, connection state, content, now-playing bar, privacy notice, and deletion notice')
  check(nav?.data?.path === '/spotify', 'missing /spotify navigation item')
  check(palette?.data?.id === 'spotify-desktop.open', 'missing palette contribution')
  check(disposers.length === 1, 'register must provide one disposer')
  disposers.forEach(dispose => dispose())
  check(true, 'disposer executed')
} finally {
  rmSync(stubs, { recursive: true, force: true })
}

console.log(failures === 0 ? 'PASS: spotify-desktop runtime ESM contract' : 'FAILURES: ' + failures)
process.exit(failures ? 1 : 0)
