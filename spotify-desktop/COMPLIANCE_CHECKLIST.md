# Spotify Desktop Plugin — T7 Compliance Checklist

This is an engineering implementation checklist, not legal clearance. Binding legal source: `spotify-legal-boundaries.md` (T7 task handoff); product constraint source: `SPOTIFY-PREBUILD-SPEC.md` §§5–8, 10–12, 15.

## Implemented, covered by `tests/compliance.mjs`

- [x] Product name is `Hermes for Spotify`; it does not begin with `Spot`.
- [x] A shared `Attribution` component renders an official Spotify-hosted logo, 70px minimum rendered width, 11px exclusion spacing, descriptive alt text, and an `Open in Spotify` link.
- [x] The shell and every track row use the attribution component; album and playlist views retain an attribution/linkback; content-level attribution is rendered before the content workspace.
- [x] `ExplicitBadge` renders when typed track data declares `explicit`.
- [x] `CoverArt` is the only cover-art renderer: 4px small/medium and 8px large radii, `objectFit: contain`, and no overlay/crop/blur/filter/animation styles.
- [x] `COMPLIANCE.voicePlaybackEnabled` is a hard default `false`; no voice UI, palette command, REST route, Web Playback, audio, AI/ML, analytics, ads, payment, account-registration, or publishing flow was added.
- [x] Plugin uses the Hermes-authored `Inter` scale, neutral Hermes theme variables, and `#7ab8e8` rather than the Spotify signature `#1ed760`; it has no copied font, SVG path, circle/wave app identity, or full three-pane shell.
- [x] User-visible `PrivacyNotice` identifies personal/non-commercial use, text/UI initiation, prohibited uses, and the deletion documentation.
- [x] `PRIVACY_NOTICE.md` and `DATA_DELETION.md` document immediate disconnect cleanup and the five-calendar-day cache-deletion process.

## Independent legal judgment still required

- Whether the final packaged visual composition clears Spotify Policy §III.11 and trademark/trade-dress risk.
- Whether any distribution beyond Tony’s personal machines is private personal/non-commercial use and requires Spotify/counsel approval.
- The final end-user agreement’s required Spotify terms and any incident-specific notification duty.
- Any later request to enable voice interaction, add Web Playback, change scopes, add data retention, commercialize, or distribute this plugin.
