/* P4-R1 F1/F2 regression contract: bridge-faithful plain-object bodies (the
 * real Electron bridge serializes opts.body exactly once via JSON.stringify —
 * apps/desktop/electron/main.ts fetchJson), plus reachable album/playlist
 * context play. Named for the incident (QA HOLD F1 422 double-stringify). */
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { register } from 'node:module'

const root = dirname(dirname(fileURLToPath(import.meta.url)))
const pluginPath = join(root, 'desktop', 'plugin.js')
const stubs = mkdtempSync(join(tmpdir(), 'spotify-rest-body-esm-'))
const stubUrl = name => pathToFileURL(join(stubs, name)).href
let failures = 0
const check = (ok, message) => { if (!ok) { failures += 1; console.error('FAIL: ' + message) } }

writeFileSync(join(stubs, 'sdk.mjs'), `export const ROUTES_AREA='routes'; export const SIDEBAR_NAV_AREA='sidebar.nav'; export const PALETTE_AREA='palette'; export const host={navigate(){}}; export const usePluginI18n=()=>({t:key=>key})`)
writeFileSync(join(stubs, 'react.mjs'), `export const useState=x=>[x,()=>{}]; export const useEffect=()=>undefined`)
writeFileSync(join(stubs, 'jsx.mjs'), `export const jsx=(type,props)=>({type,props:props||{}}); export const jsxs=jsx`)
writeFileSync(join(stubs, 'loader.mjs'), `const stubs=${JSON.stringify({'@hermes/plugin-sdk':stubUrl('sdk.mjs'),react:stubUrl('react.mjs'),'react/jsx-runtime':stubUrl('jsx.mjs')})}; const plugin=${JSON.stringify(pathToFileURL(pluginPath).href)}; export function resolve(s,c,n){return stubs[s]?{url:stubs[s],shortCircuit:true}:n(s,c)}; export async function load(u,c,n){const r=await n(u,c);return u===plugin?{format:'module',source:r.source,shortCircuit:true}:r}`)

try {
  const source = readFileSync(pluginPath, 'utf8')
  register(pathToFileURL(join(stubs, 'loader.mjs')).href)
  const mod = await import(pathToFileURL(pluginPath).href + '?t=' + Date.now())
  const { contextUri, mutateRest } = mod

  // L1: no pre-stringified mutation bodies remain anywhere in the renderer.
  check(!/body:\s*JSON\.stringify/.test(source), 'renderer never pre-stringifies ctx.rest bodies (422 double-stringify regression)')

  // L2/L4: bridge-faithful stub — serialize like the real bridge does, then
  // assert the wire payload parses back to an object with the typed fields.
  const calls = []
  const bridge = (path, opts) => new Promise((resolve, reject) => {
    const wire = opts && opts.body !== undefined ? JSON.stringify(opts.body) : undefined
    calls.push({ path, method: opts && opts.method, wire })
    let parsed
    try { parsed = wire === undefined ? undefined : JSON.parse(wire) } catch { parsed = 'UNPARSEABLE' }
    if (typeof parsed === 'string' || parsed === 'UNPARSEABLE') return reject(new Error('endpoint received string body'))
    resolve({ ok: true, parsed })
  })
  const originalRest = bridge

  // Route the plugin's module-level restRef through the bridge by registering.
  mod.default.register({ rest: originalRest, registerMany: () => {}, onDispose: () => {} })
  await mutateRest('/playback/play', 'POST', { uris: ['spotify:track:x'] })
  await mutateRest('/playback/seek', 'POST', { position_ms: 30000 })
  await mutateRest('/transfer', 'POST', { device_id: 'd1', play: true })
  const play = calls.find(c => c.path === '/playback/play')
  const seek = calls.find(c => c.path === '/playback/seek')
  const transfer = calls.find(c => c.path === '/transfer')
  check(play && JSON.parse(play.wire).uris[0] === 'spotify:track:x', 'play body survives single bridge serialization as object')
  check(seek && JSON.parse(seek.wire).position_ms === 30000, 'seek body is a typed object after bridge serialization')
  check(transfer && JSON.parse(transfer.wire).device_id === 'd1', 'transfer body is a typed object after bridge serialization')

  // F2: context_uri construction and wired UI paths.
  check(contextUri('album', 'abc') === 'spotify:album:abc' && contextUri('playlist', 'p1') === 'spotify:playlist:p1' && contextUri('album', null) === null, 'contextUri builds spotify:kind:id uris')
  check(source.includes("mutateRest('/playback/play', 'POST', { context_uri: contextUri(props.kind, props.id) })"), 'context play sends context_uri to the typed play action')
  check(source.includes("function openAlbum(id)") && source.includes("function openPlaylist(id)"), 'content workspace routes selections into album/playlist views')
  check(source.includes("view === 'album' ? jsx(AlbumView") && source.includes("view === 'playlist' ? jsx(PlaylistView"), 'album and playlist views are reachable from view state')
  check(source.includes("onOpenAlbum: openAlbum") && source.includes('onOpenPlaylist: openPlaylist'), 'library and search surfaces receive open handlers')
  check(source.includes("'Play album'") && source.includes("'Play playlist'"), 'album and playlist surfaces expose context play labels')
  check(source.includes("filter === 'albums' ? 'album' : 'playlist'"), 'library cards play the correct context kind')
} finally { rmSync(stubs, { recursive: true, force: true }) }

console.log(failures === 0 ? 'PASS: spotify rest body + context play contract' : 'FAILURES: ' + failures)
process.exit(failures ? 1 : 0)
