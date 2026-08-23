# Hermes for Spotify — Privacy Notice

Effective: 2026-08-19. Scope: the personal, non-commercial `spotify-desktop` Hermes runtime plugin.

## What this plugin does

The Music page makes only user-initiated, text/UI requests to the existing Hermes Spotify integration so it can display Spotify metadata and send allowed remote-control requests to the user’s existing Spotify Connect device. It is a companion page, not Spotify playback inside Hermes.

The plugin does not:

- accept or route voice-initiated Spotify playback commands (`voicePlaybackEnabled` is hard-defaulted to `false`);
- stream or cache audio, use Web Playback, run advertising/payments/account registration, or publish content;
- add analytics, profiling, or derived listening metrics;
- send Spotify content into AI/ML training, prompts, or model ingestion;
- read, copy, log, or expose OAuth access/refresh tokens or `auth.json`.

## Data use and retention

The renderer may retain only its non-sensitive local presentation preferences: selected device id and layout. Spotify-derived metadata is fetched from the existing Hermes Spotify backend for display; this version intentionally writes no content cache. If a future plugin-owned cache exists, its sole allowed location is `$HERMES_HOME/cache/spotify-desktop/` and it is covered by the deletion procedure below.

Spotify OAuth credentials are owned by Hermes’ existing Spotify auth subsystem and are not accessed by this plugin. Revocation of that separate auth integration follows its established Hermes auth/logout process.

## Disconnect and deletion

Choose **Disconnect and delete plugin data** on the Music page. The page immediately clears plugin-owned renderer keys. The plugin maintainer must complete any plugin-cache deletion within five calendar days using the documented, idempotent helper in `DATA_DELETION.md`. The procedure intentionally never deletes `auth.json`.

## Personal-use and support boundary

This is intended for Tony’s personal, non-commercial use until any distribution posture receives the required review. Do not use it for children-targeted, business/retail, advertising, or monetized use.

For a security incident involving plugin-held Spotify Personal Data, preserve the minimum necessary evidence and escalate promptly; Spotify’s Developer Policy requires notice to `security@spotify.com` within 24 hours where applicable. This notice does not replace the required end-user agreement or independent legal review for distribution.
