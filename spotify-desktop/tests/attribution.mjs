/* F2 regression guard: attribution logo must be a live official Spotify asset. */
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = dirname(dirname(fileURLToPath(import.meta.url)))
const source = readFileSync(join(root, 'desktop', 'plugin.js'), 'utf8')
const meta = JSON.parse(readFileSync(join(root, 'desktop', 'attribution-source.json'), 'utf8'))
let failures = 0
const check = (ok, message) => { if (!ok) { failures += 1; console.error('FAIL: ' + message) } }

check(source.includes("attributionLogo: '" + meta.attributionLogo + "'"), 'COMPLIANCE.attributionLogo matches attribution-source.json manifest')
check(meta.source.startsWith('https://newsroom.spotify.com/') || meta.source.startsWith('https://developer.spotify.com/'), 'attribution source is an official Spotify page')
check(!source.includes('developer.spotify.com/assets/branding-guidelines/'), 'dead developer branding asset path is not referenced')

console.log(failures ? 'FAILURES: ' + failures : 'PASS: attribution logo contract')
process.exit(failures ? 1 : 0)
