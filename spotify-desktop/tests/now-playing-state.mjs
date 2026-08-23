/* T3 playback-state, boundary, i18n, and visual identity contract. */
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { register } from 'node:module'

const root = dirname(dirname(fileURLToPath(import.meta.url)))
const pluginPath = join(root, 'desktop', 'plugin.js')
const stubs = mkdtempSync(join(tmpdir(), 'spotify-now-playing-esm-'))
const stubUrl = name => pathToFileURL(join(stubs, name)).href
let failures = 0
const check = (condition, message) => {
  if (!condition) { failures += 1; console.error('FAIL: ' + message) }
}

writeFileSync(join(stubs, 'sdk.mjs'), `
export const ROUTES_AREA = 'routes'
export const SIDEBAR_NAV_AREA = 'sidebar.nav'
export const PALETTE_AREA = 'palette'
export const host = { navigate() {} }
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
  register(pathToFileURL(join(stubs, 'loader.mjs')).href)
  const mod = await import(pathToFileURL(pluginPath).href + '?t=' + Date.now())
  const { actionAllowed, clampSeek, interpolateProgress, nextPollDelay, playbackUiState, translations } = mod

  check(interpolateProgress({ progress_ms: 1000, timestamp: 1000, is_playing: true, item: { duration_ms: 5000 } }, 3500) === 3500, 'progress interpolates from server timestamp')
  check(interpolateProgress({ progress_ms: 4900, timestamp: 1000, is_playing: true, item: { duration_ms: 5000 } }, 2000) === 5000, 'progress is clamped at track duration')
  check(interpolateProgress({ progress_ms: 1000, timestamp: 1000, is_playing: false, item: { duration_ms: 5000 } }, 3500) === 1000, 'paused progress does not interpolate')
  check(clampSeek(-1, 5000) === 0 && clampSeek(9000, 5000) === 5000 && clampSeek(1234, 5000) === 1234, 'seek clamps to typed duration bounds')

  check(playbackUiState({ category: 'premium_required' }) === 'free_read_only', 'premium failure selects Free read-only state')
  check(playbackUiState({ category: 'no_active_device' }) === 'no_device', 'typed no-device failure selects guidance state')
  check(playbackUiState({ category: 'restricted_device' }) === 'restricted', 'restricted device selects disabled state')
  check(playbackUiState({ category: 'rate_limited', retry_after_seconds: 12 }) === 'rate_limited', 'rate-limit state remains non-blocking')
  check(playbackUiState({ category: 'unavailable' }) === 'offline', 'transport failure selects offline state')
  check(playbackUiState({ idle: true }) === 'idle', '204/idle playback selects empty state')
  check(playbackUiState({ item: { name: 'Track' }, device: { is_restricted: false } }) === 'ready', 'normal playback selects ready state')

  check(actionAllowed('ready', 'play') && !actionAllowed('free_read_only', 'play'), 'Free state never permits transport mutation')
  check(!actionAllowed('no_device', 'next') && !actionAllowed('restricted', 'seek'), 'no-device and restricted states never fake mutations')
  check(source.includes("transport('volume'") && source.includes("transport('shuffle'") && source.includes("transport('repeat'"), 'now-playing bar exposes volume, shuffle, and repeat through typed transport actions')
  check(source.includes("mutateRest('/playback/play', 'POST', { uris: [track.uri] })") && source.includes("kind === 'track' ? jsx(TrackRow"), 'search, album, and playlist track rows start the selected track through the typed play action')
  check(source.includes("return getPlayback()"), 'playback mutations refresh now-playing state after success')
  check(source.includes("role: 'alert'"), 'playback action failures remain visible to the user')
  check(nextPollDelay({ visible: true, focused: true }) === 5000, 'focused page uses normal polling cadence')
  check(nextPollDelay({ visible: false, focused: true }) === 15000, 'hidden page backs off polling')
  check(nextPollDelay({ visible: true, focused: true, category: 'rate_limited', retry_after_seconds: 12 }) === 12000, 'rate-limit category honors retry-after')
  check(nextPollDelay({ visible: true, focused: true, category: 'quota_exceeded' }) === 30000, 'quota category uses conservative backoff')

  // SDK contract (i18n/runtime.ts resolvePath): keys resolve by dot-path walk
  // through nested trees, so 'music.title' must live at .music.title.
  const resolvePath = (tree, key) => key.split('.').reduce((node, part) => (node && typeof node === 'object' ? node[part] : undefined), tree)
  for (const locale of ['en', 'ja', 'zh', 'zh-hant']) {
    check(typeof resolvePath(translations?.[locale], 'music.title') === 'string', 'translation bundle contains ' + locale)
  }
  check(source.includes('useI18n()'), 'renderer uses i18n hook for visible strings')
  check(source.includes('Spotify attribution') && source.includes('open.spotify.com'), 'page visibly includes official attribution and Open in Spotify linkback')
  check(!source.includes('#1ed760'), 'renderer does not use Spotify signature green')
  check(!/Web Playback|audio\.play|speechrecognition|machine learning/i.test(source), 'renderer does not add forbidden Web Playback, audio, speech-recognition, or AI paths')
} finally {
  rmSync(stubs, { recursive: true, force: true })
}

console.log(failures === 0 ? 'PASS: spotify now-playing state contract' : 'FAILURES: ' + failures)
process.exit(failures ? 1 : 0)
