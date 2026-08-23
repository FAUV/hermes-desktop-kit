/* T7 compliance regression guard: policy-facing source and documentation boundaries. */
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = dirname(dirname(fileURLToPath(import.meta.url)))
const source = readFileSync(join(root, 'desktop', 'plugin.js'), 'utf8')
const manifest = readFileSync(join(root, 'dashboard', 'manifest.json'), 'utf8')
const privacy = readFileSync(join(root, 'PRIVACY_NOTICE.md'), 'utf8')
const deletion = readFileSync(join(root, 'DATA_DELETION.md'), 'utf8')
const checklist = readFileSync(join(root, 'COMPLIANCE_CHECKLIST.md'), 'utf8')
let failures = 0
const check = (ok, message) => { if (!ok) { failures += 1; console.error('FAIL: ' + message) } }

check(source.includes("voicePlaybackEnabled: false"), 'voice playback must be explicitly default-off')
check(!/voicePlaybackEnabled\s*:\s*true|id:\s*['"][^'"]*voice|path:\s*['"][^'"]*voice|label:\s*['"][^'"]*[^'"]*voice/i.test(source), 'renderer must not add a voice-initiated Spotify route or command')
check(!/Web Playback|audio\.play|SpeechRecognition|webkitSpeechRecognition/i.test(source), 'renderer must not add in-app playback/audio/speech APIs')
check(!/ctx\.(rest|socket)\([^)]*(ai|analytics|profile|ads|payments|register)|host\.request\([^)]*(ai|analytics|profile|ads|payments|register)/i.test(source), 'renderer must not add AI/ML, analytics, ads, payments, or registration request paths')
check(!source.includes('#1ed760'), 'Spotify signature green must not be a plugin token')
check(!/SpotifyMixUI|Circular|<svg|<path/i.test(source), 'renderer must not copy Spotify font or SVG/logo-path identity')
check(source.includes("fontFamily: 'Inter, sans-serif'") && source.includes("gridTemplateColumns: 'minmax(0, 1fr) minmax(260px, 340px)"), 'page retains Hermes-authored typography and non-Spotify-shell layout')
check(source.includes('function Attribution') && source.includes('attributionLogo') && source.includes("width: '70px'") && source.includes("padding: '11px'"), 'official attribution component provides logo sizing and exclusion spacing')
check(source.includes('function CoverArt') && source.includes("borderRadius: props.size === 'large' ? '8px' : '4px'") && source.includes("objectFit: 'contain'") && !/filter\s*:|animation\s*:|objectFit:\s*'cover'/.test(source), 'cover art stays original-form with 4px/8px corners and no crop/filter/animation')
check(source.includes('function ExplicitBadge') && source.includes('track.explicit && jsx(ExplicitBadge'), 'typed explicit metadata renders an explicit badge')
check(source.includes('function PrivacyNotice') && source.includes('PRIVACY_NOTICE.md') && source.includes('DATA_DELETION.md'), 'page visibly links privacy and deletion notices')
check(manifest.includes('"label": "Hermes for Spotify"') && !/"label":\s*"Spot/i.test(manifest), 'plugin label uses approved Hermes-for-Spotify naming')
check(privacy.includes('voice-initiated Spotify playback commands') && privacy.includes('AI/ML') && privacy.includes('five calendar days'), 'privacy notice documents no-voice, no-AI, and deletion boundary')
check(deletion.includes('five calendar days') && deletion.includes('auth.json'), 'deletion procedure retains five-day and auth-ownership boundary')
check(checklist.includes('Independent legal judgment still required'), 'checklist preserves unresolved legal judgment')

console.log(failures ? 'FAILURES: ' + failures : 'PASS: spotify compliance contract')
process.exit(failures ? 1 : 0)
