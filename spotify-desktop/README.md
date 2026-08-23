# Hermes for Spotify (spotify-desktop)

A personal, non-commercial Spotify companion for the Hermes Desktop app. It
renders a `/spotify` page inside the desktop shell and drives your existing
Spotify account through the Hermes Spotify integration — no second OAuth flow,
no credentials owned by this plugin.

## What it does

- **Connection status** — polls the plugin backend for the current Spotify
  auth state; links to `hermes auth spotify` when credentials are missing or
  expired.
- **Now Playing** — live track, cover art, progress, shuffle/repeat state,
  seek, volume, and transport (play/pause/next/previous).
- **Devices** — list available Spotify Connect devices, transfer playback, and
  adjust volume where the device exposes it.
- **Queue** — read-only view of the playback queue plus add-to-queue from any
  track row.
- **Home** — recently played shelf and your playlists.
- **Search** — tracks, artists, albums, and playlists (bounded to 10 per page).
- **Library** — saved tracks/albums and followed artists; save/unsave hearts.
- **Playlists** — list, open, rename, create, and reorder/remove items on
  playlists you own. Other users' playlists are read-only.

## Architecture

```
spotify-desktop/
├── dashboard/
│   ├── manifest.json        # Hermes plugin manifest (name, label, api entry)
│   └── plugin_api.py        # FastAPI router: typed projection + stable errors
├── desktop/
│   ├── plugin.js            # Hermes-authored React renderer (no build step)
│   └── attribution-source.json  # provenance for the Spotify logo asset
├── tests/                   # pytest (backend) + Node ESM (renderer) contracts
├── scripts/                 # Deletion + live-verification utilities (see below)
└── *.md                     # Privacy / deletion / compliance documentation
```

The backend owns **OAuth nor tokens**. It imports `SpotifyClient` from the
existing Hermes Spotify plugin (`plugins/spotify/client.py`), which resolves and
refreshes credentials itself. This plugin only projects a **renderer-safe
surface**:

- `_track`, `_artist`, `_device`, `_playback`, `_album_summary`,
  `_playlist_summary`, and `_page` strip every response down to the fields the
  renderer needs — raw catalog fields (`popularity`, `available_markets`,
  `followers`, `copyrights`, etc.) never reach the renderer.
- All upstream errors are normalized into a `Failure` shape
  (`ok: false`, `category`, optional `retry_after_seconds`) with secret-free
  detail text.

## Dependency on Hermes Spotify auth

This plugin has **no credentials of its own**. It requires the existing Hermes
Spotify integration to be authorized:

```bash
hermes auth spotify
```

Until that completes, `/status` reports `not_authenticated` and every data
endpoint returns a `not_authenticated` 401. The plugin never prompts for or
stores Spotify credentials.

## `/status` semantics (honest, not verified)

`SpotifyClient()` resolves and refreshes **stored credentials locally** — it
performs **no Spotify API call**. So `/status` does not claim a verified
"connected" state. It reports:

- `auth.state = "credentials_available"` + `auth.verified = false` — credentials
  resolved locally, not yet verified against the API.
- `auth.state = "not_authenticated"` — no credentials to resolve.

The renderer treats `credentials_available` as "usable" and lets the first real
data call (e.g. `/playback`) surface any genuine 401/expiry. This avoids burning
Spotify API quota just to decorate a status light.

## Spotify Premium limitations

Playback **control** (play/pause/seek/volume/queue mutations) requires a
Spotify **Premium** account with an active device. The backend surfaces this
honestly via the `premium_required` failure category; the renderer renders
read-only/disabled states. Read-only browsing (search, library, playlist
metadata) works without Premium. Playback state endpoints return
`no_active_device` when nothing is playing.

## Supported features

| Area | Endpoints |
| --- | --- |
| Status | `GET /status` |
| Playback | `GET /playback`, `POST /playback/{play,pause,next,previous,seek,volume,shuffle,repeat}` |
| Devices | `GET /devices`, `POST /transfer` |
| Queue | `GET /queue`, `POST /queue` |
| Search | `GET /search` |
| Home | `GET /home/recently-played` |
| Album | `GET /albums/{id}` |
| Library | `GET /library/{tracks,albums,artists}`, `GET /library/contains`, `POST /library/items` |
| Playlists | `GET /playlists`, `POST /playlists`, `GET /playlists/{id}`, `PATCH /playlists/{id}`, `POST /playlists/{id}/items` |
| Capabilities | `POST /capabilities/probe` (stateless, per-request) |

Playback request validation is strict and fails **before** reaching Spotify:
`shuffle` accepts only a boolean, `repeat` only `off`/`context`/`track`, `seek`
requires a non-negative integer `position_ms`, `volume` an integer `0..100`,
and a `play` cannot combine `context_uri` with `uris`.

## Development & test

Use the **Hermes runtime toolchain** (its venv Python and Node), not arbitrary
system Python — the plugin imports from the Hermes tree.

```bash
cd ~/.hermes/plugins/spotify-desktop/tests

# Backend contract tests (FastAPI + pydantic, mocked SpotifyClient)
python3 -m pytest -q

# Renderer / Node ESM contract tests
for f in *.mjs; do node "$f"; done

# Static checks
python3 -m ruff check ../dashboard/plugin_api.py
python3 -m py_compile ../dashboard/plugin_api.py
```

`tests/test_projection_contract.py` is the shared fixture for the backend tests
(`FakeSpotify`, `install`, `load_api`).

### Scripts (utilities, not loaded by the plugin)

- `scripts/delete_spotify_personal_data.py` — the documented five-day deletion
  procedure (see `DATA_DELETION.md`).
- `scripts/probe_playback_control_live.py`, `scripts/verify_f1_live.py` —
  live verification utilities used during QA. They hit the real API only when
  you run them explicitly.

## Privacy & local storage

- The plugin sends **only your explicit, text-initiated actions** to Spotify,
  through Hermes. No analytics, no advertising, no payments, no voice control,
  no in-app audio, no AI/ML ingestion of Spotify content.
- Local persistence is limited to two keys (`selected_device_id`, `layout`)
  stored through the Hermes plugin storage API. Disconnect clears them
  immediately; see `DATA_DELETION.md` for the five-day deletion procedure and
  `PRIVACY_NOTICE.md` for the full notice.

## Disconnect behavior

The connection notice exposes a "Disconnect and delete plugin data" action. It
removes all plugin-owned local state immediately and flips the surface to the
disconnected state. It does **not** revoke the Hermes Spotify authorization —
that remains owned by `hermes auth spotify`.

## Compliance constraints

- **Personal, non-commercial** use only.
- **Spotify attribution** is required and rendered on every content surface
  (logo + "Open in Spotify"); see `desktop/attribution-source.json` for the
  official asset provenance and `COMPLIANCE_CHECKLIST.md` for the full review.
- **No mimicry** — this is a companion, not a Spotify client replica.
- **No scope/credential changes** — this plugin does not register a Spotify app
  or alter OAuth scopes.

## What gets committed

Real source (`dashboard/`, `desktop/`), tests (`tests/`), the manifest,
`README.md`, and the privacy/compliance docs (`PRIVACY_NOTICE.md`,
`DATA_DELETION.md`, `COMPLIANCE_CHECKLIST.md`), plus `scripts/`. Excluded by
`.gitignore`: caches (`__pycache__`, `.pytest_cache`, `.ruff_cache`), transient
evidence (`evidence/`, `*EVIDENCE.md`, `TEST_PLAN.md`, `IDEA.md`), editor/OS
noise, and logs.
