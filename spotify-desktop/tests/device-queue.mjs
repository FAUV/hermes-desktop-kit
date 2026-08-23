/* T4 device/queue renderer behavior and API-boundary contracts. */
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { register } from 'node:module'

const root = dirname(dirname(fileURLToPath(import.meta.url)))
const pluginPath = join(root, 'desktop', 'plugin.js')
const stubs = mkdtempSync(join(tmpdir(), 'spotify-device-queue-esm-'))
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
  const { canAdjustDeviceVolume, canTransferDevice, deviceUiState, retryAfterDelay } = mod
  const capable = { id: 'desktop', can_transfer: true, can_adjust_volume: true }
  const restricted = { id: 'speaker', can_transfer: false, can_adjust_volume: false }

  check(deviceUiState({ category: 'premium_required' }) === 'free_read_only', 'Premium failures create a read-only device state')
  check(deviceUiState({ category: 'no_active_device' }) === 'no_device', 'no-device failures retain guidance state')
  check(deviceUiState({ category: 'restricted_device' }) === 'restricted', 'restricted-device failures disable controls')
  check(deviceUiState({ category: 'quota_exceeded' }) === 'rate_limited', 'quota errors become throttled controls')
  check(canTransferDevice(capable, null) && !canTransferDevice(restricted, null), 'transfer honors explicit device capabilities')
  check(canAdjustDeviceVolume(capable, null) && !canAdjustDeviceVolume(restricted, null), 'volume only renders for allowed devices')
  check(!canTransferDevice(capable, { category: 'premium_required' }) && !canAdjustDeviceVolume(capable, { category: 'restricted_device' }), 'mutation failures disable device controls')
  check(retryAfterDelay({ retry_after_seconds: 17 }) === 17000 && retryAfterDelay({ retry_after_seconds: 999 }) === 60000, '429 retry delay is honored and bounded')

  check(source.includes("'/devices'") && source.includes("'/transfer'") && source.includes("'/playback/volume'"), 'device picker consumes only typed device APIs')
  check(source.includes("mutate('/transfer', { device_id: device.id, play: true })"), 'transfer sends exactly one device ID')
  check(source.includes("'/queue'") && source.includes("mutateRest('/queue', 'POST', { uri: track.uri })"), 'queue reads typed projection and track rows add by URI')
  check(source.includes("readOnly: true") && source.includes('The queue is read-only here'), 'queue rows are explicitly rendered read-only')
  check(!source.includes('/queue/reorder') && !source.includes('Remove from queue') && !source.includes('Clear queue') && !source.includes('draggable'), 'queue exposes no unsupported mutation or drag affordance')
  check(source.includes('Spotify attribution') && source.includes('open.spotify.com') && source.includes('function CoverArt') && source.includes("borderRadius: props.size === 'large' ? '8px' : '4px'"), 'device/queue additions preserve attribution, linkback, and centralized original-art boundary')
} finally { rmSync(stubs, { recursive: true, force: true }) }

console.log(failures === 0 ? 'PASS: spotify device and queue contract' : 'FAILURES: ' + failures)
process.exit(failures ? 1 : 0)
