/* T6 renderer contracts: Library filters, contains-driven hearts, and snapshots. */
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { register } from 'node:module'

const root = dirname(dirname(fileURLToPath(import.meta.url)))
const pluginPath = join(root, 'desktop', 'plugin.js')
const stubs = mkdtempSync(join(tmpdir(), 'spotify-library-esm-'))
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
  check(mod.libraryContainsPath('album', ['a', 'b']) === '/library/contains?kind=album&ids=a&ids=b', 'contains checks preserve type and id order')
  check(mod.containsSaved({contains:[true, false]}, 0) && !mod.containsSaved({contains:[true, false]}, 1), 'heart state is driven only by contains checks')
  check(mod.snapshotAfter('old', {snapshot_id:'new'}) === 'new' && mod.snapshotAfter('old', {}) === 'old', 'playlist snapshot responses are reused without a full refresh token')
  check(source.includes("['playlists', 'Playlists']") && source.includes("['albums', 'Albums']") && source.includes("['artists', 'Artists']"), 'Library exposes required filter chips')
  check(source.includes("storageRef.set('layout', next)") && source.includes("'Grid'") && source.includes("'List'"), 'grid/list preference is non-sensitive local storage only')
  check(source.includes("'/library/items'") && source.includes("'/library/contains?kind='"), 'heart mutations and contains checks use typed library APIs')
  check(source.includes("'/playlists/' + encodeURIComponent(props.playlist.id) + '/items'") && source.includes('snapshot_id: snapshot'), 'playlist item management propagates snapshot_id')
  check(source.includes('This playlist belongs to another user, so this companion shows metadata only.'), 'other users playlists remain metadata-only')
  check(!source.includes('ugc-image-upload') && !source.includes('cover upload'), 'no deferred cover-upload scope or surface was introduced')
} finally { rmSync(stubs, {recursive:true, force:true}) }
console.log(failures ? 'FAILURES: ' + failures : 'PASS: spotify library and playlist contract')
process.exit(failures ? 1 : 0)
