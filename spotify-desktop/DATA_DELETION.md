# Spotify Desktop Plugin — Data Deletion Procedure

Owner: the Spotify Desktop plugin maintainer. This procedure applies to the plugin package at `~/.hermes/plugins/spotify-desktop/` and must be completed within five calendar days after a user selects **Disconnect and delete plugin data** or otherwise requests deletion.

## Data boundary

| Data class | Location | Owner | Deletion action |
|---|---|---|---|
| Selected Spotify Connect device identifier; layout preference | Plugin-scoped renderer storage keys `hermes.plugin.spotify-desktop.selected_device_id` and `.layout` | Plugin | The in-page Disconnect action deletes both keys immediately. |
| Spotify-derived temporary metadata cache, if present in a later content feature | `$HERMES_HOME/cache/spotify-desktop/` | Plugin | Run the executable helper below; it recursively removes only this directory. |
| Spotify OAuth credentials and provider configuration | `$HERMES_HOME/auth.json` | Existing Hermes Spotify auth subsystem, not this plugin | Not read, changed, copied, or deleted by this procedure. Users who want to revoke core Hermes Spotify auth must use its own established auth/logout flow. |

The current T2 plugin writes no Spotify content cache. The cache location is reserved so a future feature has one explicit, deletable home rather than adding ad-hoc retention.

## User-facing Disconnect

1. In Music, select **Disconnect and delete plugin data**.
2. The renderer immediately removes both plugin-owned storage keys and stops status polling for that page session.
3. Confirm the page displays **Spotify plugin data was cleared on this device**.
4. The plugin does not inspect or expose credentials, and it does not terminate the existing Hermes-wide Spotify credential because that credential is outside its ownership boundary.

## Offline five-day completion procedure

Within five calendar days, run this idempotent command with the same Hermes home that hosted the plugin:

```sh
python3 ~/.hermes/plugins/spotify-desktop/scripts/delete_spotify_personal_data.py --home "$HERMES_HOME"
```

If `HERMES_HOME` is not set, use the explicit home path, for example `--home ~/.hermes`. Expected output is either `{'removed': ['cache/spotify-desktop']}` or `{'removed': []}`. The latter is successful: no plugin cache remained.

## Verification record

Record the request date, completion date, target home, and helper output in the private support/maintenance record. Do not record Spotify metadata, device IDs, URLs, tokens, or auth-file content. Escalate a cache path outside `$HERMES_HOME/cache/spotify-desktop/` as an incident; do not broaden this deletion script without review.

## Scope finding

The existing `hermes auth spotify` implementation is CLI-only in the current Hermes runtime. The plugin safely opens the existing Hermes tool-settings route (`hermes://open/settings/tools`) and then observes `GET /status`; it neither launches a subprocess nor creates a renderer OAuth/PKCE implementation. A future one-click login requires an approved shared core/session-backed auth-launch primitive. Until then, the UI gives a re-auth CTA plus the explicit existing command: `hermes auth spotify`.
