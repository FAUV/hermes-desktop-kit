/* T2 state-machine and no-second-OAuth regression contract. */
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { register } from 'node:module'

const root = dirname(dirname(fileURLToPath(import.meta.url)))
const pluginPath = join(root, 'desktop', 'plugin.js')
const stubs = mkdtempSync(join(tmpdir(), 'spotify-connection-esm-'))
const stubUrl = name => pathToFileURL(join(stubs, name)).href
let failures = 0
const check = (condition, message) => {
  if (!condition) { failures += 1; console.error('FAIL: ' + message) }
}

writeFileSync(join(stubs, 'sdk.mjs'), `
export const ROUTES_AREA = 'routes'
export const SIDEBAR_NAV_AREA = 'sidebar.nav'
export const PALETTE_AREA = 'palette'
export const host = { navigations: [], navigate(path) { this.navigations.push(path) } }
export const usePluginI18n = () => ({ t: key => key })
`)
writeFileSync(join(stubs, 'react.mjs'), `
export const useState = initial => [initial, () => {}]
export const useEffect = () => undefined
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

try {
  const source = readFileSync(pluginPath, 'utf8')
  check(!/access_token|refresh_token|auth\.json|Authorization:\s*Bearer/i.test(source), 'renderer must not serialize credential material')
  check(!/\b(child_process|subprocess|spawn|execFile|execSync)\b|\bpkce\b|\bcode_verifier\b/i.test(source), 'renderer must not implement or shell out to OAuth')
  check(source.includes('hermes auth spotify'), 'login and reauth states document the existing manual Hermes command')
  check(source.includes("'/status'"), 'renderer must poll the typed status endpoint')
  register(pathToFileURL(join(stubs, 'loader.mjs')).href)
  const mod = await import(pathToFileURL(pluginPath).href + '?t=' + Date.now())
  const { connectionState, transitionConnection, clearPluginData, AUTH_SETTINGS_DEEP_LINK } = mod

  check(connectionState({ auth: { state: 'not_authenticated' } }, false) === 'login_required', 'missing auth maps to login_required')
  check(connectionState({ auth: { state: 'connected' } }, false) === 'connected', 'connected maps to connected')
  check(connectionState({ auth: { state: 'credentials_available' } }, false) === 'connected', 'credentials_available maps to a usable connected state (R1/F4)')
  check(connectionState({ auth: { state: 'expired' } }, false) === 'expired', 'expired maps to reauth state')
  check(connectionState({ auth: { state: 'revoked' } }, false) === 'expired', 'revoked maps to reauth state')
  check(connectionState({ auth: { state: 'not_authenticated' } }, true) === 'disconnected', 'explicit disconnect wins over backend missing auth')
  check(connectionState(null, false) === 'error', 'malformed status maps to safe error state')
  check(transitionConnection('login_required', 'connect_opened') === 'connecting', 'connect CTA enters connecting')
  check(transitionConnection('connecting', 'status_connected') === 'connected', 'successful poll completes connection')
  check(transitionConnection('connecting', 'status_not_authenticated') === 'login_required', 'missing auth exits connecting without relaunch')
  check(transitionConnection('connecting', 'status_expired') === 'expired', 'expired poll exits connecting to reauth')
  check(transitionConnection('connected', 'disconnect') === 'disconnected', 'disconnect exits connection')
  check(transitionConnection('expired', 'connect_opened') === 'connecting', 'reauth CTA enters connecting')
  check(AUTH_SETTINGS_DEEP_LINK === 'hermes://open/settings/tools', 'auth CTA only opens the existing Hermes tool settings route')

  const removed = []
  clearPluginData({ remove: key => removed.push(key) })
  check(JSON.stringify(removed.sort()) === JSON.stringify(['layout', 'selected_device_id'].sort()), 'disconnect clears every persisted plugin-owned key')
} finally {
  rmSync(stubs, { recursive: true, force: true })
}

console.log(failures === 0 ? 'PASS: spotify connection state contract' : 'FAILURES: ' + failures)
process.exit(failures ? 1 : 0)
