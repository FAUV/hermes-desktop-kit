/* T5 search/content view behavior, policy, and visual-boundary contracts. */
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { register } from 'node:module'

const root = dirname(dirname(fileURLToPath(import.meta.url)))
const pluginPath = join(root, 'desktop', 'plugin.js')
const stubs = mkdtempSync(join(tmpdir(), 'spotify-content-esm-'))
const stubUrl = name => pathToFileURL(join(stubs, name)).href
let failures = 0
const check = (condition, message) => { if (!condition) { failures += 1; console.error('FAIL: ' + message) } }
writeFileSync(join(stubs, 'sdk.mjs'), `export const ROUTES_AREA='routes'; export const SIDEBAR_NAV_AREA='sidebar.nav'; export const PALETTE_AREA='palette'; export const host={navigate(){}}; export const usePluginI18n=()=>({t:key=>key})`)
writeFileSync(join(stubs, 'react.mjs'), `export const useState=initial=>[initial,()=>{}]; export const useEffect=()=>undefined`)
writeFileSync(join(stubs, 'jsx.mjs'), `export const jsx=(type,props)=>({type,props:props||{}}); export const jsxs=jsx`)
writeFileSync(join(stubs, 'loader.mjs'), `const stubs=${JSON.stringify({'@hermes/plugin-sdk':stubUrl('sdk.mjs'),react:stubUrl('react.mjs'),'react/jsx-runtime':stubUrl('jsx.mjs')})}; const plugin=${JSON.stringify(pathToFileURL(pluginPath).href)}; export function resolve(s,c,n){return stubs[s]?{url:stubs[s],shortCircuit:true}:n(s,c)}; export async function load(u,c,n){const r=await n(u,c);return u===plugin?{format:'module',source:r.source,shortCircuit:true}:r}`)
try {
  const source = readFileSync(pluginPath, 'utf8')
  register(pathToFileURL(join(stubs, 'loader.mjs')).href)
  const mod = await import(pathToFileURL(pluginPath).href + '?t=' + Date.now())
  const { boundedOffset, contentState, isOwnedPlaylist, normalizeSearchResults, pageLimit, shouldLoadMore } = mod
  check(pageLimit(99) === 10 && pageLimit(0) === 1, 'all paginated requests are bounded to 1..10')
  check(boundedOffset(-2) === 0 && boundedOffset(12) === 12, 'pagination offsets cannot become negative')
  check(contentState(null, null) === 'skeleton', 'unloaded content renders skeleton state')
  check(contentState({ items: [] }, null) === 'empty', 'empty collection renders empty state')
  check(contentState(null, { category: 'rate_limited' }) === 'error', 'typed failure renders retryable error state')
  const normalized = normalizeSearchResults({ tracks: { items: [{ id: 't', name: 'Track', popularity: 99, artists: [{}], album: {} }] }, artists: { items: [{ id: 'a', name: 'Artist', followers: { total: 12 } }] }, albums: { items: [] }, playlists: { items: [] } })
  check(normalized.tracks[0].name === 'Track' && normalized.tracks[0].artists[0].name === '', 'sparse tracks do not depend on popularity or complete artist fields')
  check(normalized.artists[0].name === 'Artist', 'sparse artists do not depend on followers')
  check(isOwnedPlaylist({ owner: { id: 'me' } }, 'me') && !isOwnedPlaylist({ owner: { id: 'other' } }, 'me'), 'only owned playlists receive content rows')
  check(shouldLoadMore({ next: 'cursor' }, 0) && !shouldLoadMore({ next: null }, 0) && !shouldLoadMore({ next: 'cursor' }, 30), 'load more follows upstream page availability and quota backoff')
  check(source.includes("'/search?q='") && source.includes("&limit=10"), 'search is typed and requests only ten items per page')
  check(source.includes('Open in Spotify') && source.includes('Spotify attribution'), 'content surfaces carry official attribution and linkback')
  check(source.includes('function CoverArt') && source.includes("borderRadius: props.size === 'large' ? '8px' : '4px'") && source.includes("objectFit: 'contain'") && !source.includes('#1ed760'), 'content preserves centralized original-form art and Hermes-authored accent')
  check(!/Made for You|recommendations|related artists|audio features/i.test(source), 'home excludes prohibited recommendation and audio surfaces')
} finally { rmSync(stubs, { recursive: true, force: true }) }
console.log(failures === 0 ? 'PASS: spotify content view contract' : 'FAILURES: ' + failures)
process.exit(failures ? 1 : 0)
