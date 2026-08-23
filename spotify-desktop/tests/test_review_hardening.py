"""R1 review-hardening regressions: projection boundary, strict playback
validation, stateless capability probe, and honest /status semantics."""

from __future__ import annotations

import json

from test_projection_contract import FakeSpotify, install, load_api


def _get(api, fake, path):
    return install(api, fake).get(path)


# --- F1: explicit renderer-safe projections --------------------------------


def test_search_projection_strips_raw_catalog_fields():
    api = load_api()
    raw_page = {
        "href": "https://api.spotify.com/v1/search?x=secret",
        "items": [
            {
                "id": "t1",
                "name": "Song",
                "uri": "spotify:track:t1",
                "duration_ms": 1000,
                "explicit": False,
                # Raw-only fields that must never reach the renderer:
                "popularity": 88,
                "available_markets": ["US", "CA"],
                "disc_number": 1,
                "artists": [{"id": "a1", "name": "Artist", "followers": {"total": 5}}],
                "album": {
                    "id": "al1",
                    "name": "Album",
                    "images": [{"url": "https://img", "width": 64}],
                },
            }
        ],
    }
    fake = FakeSpotify({"tracks": raw_page})
    response = install(api, fake).get("/search", params={"q": "song"})
    assert response.status_code == 200
    payload = response.json()["results"]
    track = payload["tracks"]["items"][0]
    serialized = json.dumps(response.json())
    for forbidden in ("popularity", "available_markets", "disc_number", "followers"):
        assert forbidden not in serialized
    assert track["id"] == "t1" and track["name"] == "Song"
    assert track["album"]["images"][0]["url"] == "https://img"
    assert set(payload) == {"tracks", "artists", "albums", "playlists"}
    page = payload["tracks"]
    assert set(page) == {"items", "next", "total", "limit", "offset"}


def test_album_detail_projection_keeps_only_safe_fields():
    api = load_api()
    fake = FakeSpotify(
        {
            "id": "al1",
            "name": "Album",
            "uri": "spotify:album:al1",
            "album_type": "album",
            "release_date": "2024-01-01",
            "popularity": 91,
            "copyrights": [{"text": "(C) someone"}],
            "external_urls": {"spotify": "https://open.spotify.com/album/al1"},
            "images": [{"url": "https://img"}],
            "artists": [{"id": "a1", "name": "Artist", "genres": ["pop"]}],
            "tracks": {
                "items": [{"id": "t9", "name": "Ninth", "uri": "spotify:track:t9"}],
                "next": None,
            },
        }
    )
    response = _get(api, fake, "/albums/al1")
    album = response.json()["album"]
    serialized = response.text
    assert "copyrights" not in serialized and "genres" not in serialized
    assert album["tracks"]["items"][0]["id"] == "t9"
    assert album["external_urls"] == {
        "spotify": "https://open.spotify.com/album/al1"
    }


def test_library_pages_project_entries_flat_and_safe():
    api = load_api()

    class LibraryFake(FakeSpotify):
        def get_saved_tracks(self, **kwargs):
            self.calls.append(("get_saved_tracks", kwargs))
            return {
                "items": [
                    {
                        "added_at": "2026-08-22T00:00:00Z",
                        "track": {"id": "t1", "name": "S", "popularity": 70},
                    }
                ]
            }

        def get_saved_albums(self, **kwargs):
            self.calls.append(("get_saved_albums", kwargs))
            return {
                "items": [
                    {
                        "added_at": "2026-08-22T00:00:00Z",
                        "album": {
                            "id": "al1",
                            "name": "A",
                            "release_date_precision": "day",
                        },
                    }
                ]
            }

        def request(self, method, path, **kwargs):
            self.calls.append(("request", {"method": method, "path": path}))
            return {"artists": {"items": [{"id": "ar1", "name": "R"}]}}

    fake = LibraryFake()
    http = install(api, fake)

    tracks = http.get("/library/tracks").json()["page"]["items"][0]
    assert tracks["id"] == "t1" and tracks["added_at"].startswith("2026")
    albums = http.get("/library/albums").json()["page"]["items"][0]
    assert albums["id"] == "al1" and "added_at" in albums
    artists = http.get("/library/artists").json()["page"]["items"][0]
    assert artists["id"] == "ar1" and set(artists) == {"id", "name", "uri"}


def test_playlist_surfaces_project_summary_and_detail_shapes():
    api = load_api()

    class PlaylistFake(FakeSpotify):
        def get_my_playlists(self, **kwargs):
            self.calls.append(("get_my_playlists", kwargs))
            return {
                "items": [
                    {
                        "id": "p1",
                        "name": "Mine",
                        "owner": {"id": "me", "display_name": "Me"},
                        "fields": "raw-only",
                    }
                ]
            }

        def get_playlist(self, **kwargs):
            self.calls.append(("get_playlist", kwargs))
            return {
                "id": "p1",
                "name": "Mine",
                "owner": {"id": "me"},
                "snapshot_id": "snap-2",
                "collaborative": False,
                "tracks": {"items": [{"added_at": "x", "track": {"id": "t1"}}]},
            }

        def create_playlist(self, **kwargs):
            self.calls.append(("create_playlist", kwargs))
            return {"id": "new", "name": "Created", "owner": {"id": "me"}}

        def request(self, method, path, **kwargs):
            self.calls.append(("request", {"method": method, "path": path}))
            return {"id": "me"}

    fake = PlaylistFake()
    http = install(api, fake)

    listed = http.get("/playlists").json()["page"]["items"][0]
    serialized = http.get("/playlists").text
    assert listed["id"] == "p1" and listed["owner"]["display_name"] == "Me"
    assert "raw-only" not in serialized

    detail = http.get("/playlists/p1").json()["playlist"]
    assert detail["is_owned"] is True and detail["snapshot_id"] == "snap-2"
    assert detail["tracks"]["items"][0]["track"]["id"] == "t1"

    created = http.post("/playlists", json={"name": "Created"}).json()["playlist"]
    assert created["id"] == "new"


# --- F2: strict playback request validation ---------------------------------


def test_playback_validation_rejects_wrong_types_before_client_calls():
    api = load_api()
    fake = FakeSpotify({})
    http = install(api, fake)

    bad_bodies = [
        ("/playback/shuffle", {"state": "true"}),
        ("/playback/shuffle", {"state": 1}),
        ("/playback/shuffle", {}),
        ("/playback/repeat", {"state": "always"}),
        ("/playback/repeat", {"state": True}),
        ("/playback/repeat", {}),
        ("/playback/seek", {}),
        ("/playback/seek", {"position_ms": True}),
        ("/playback/seek", {"position_ms": -1}),
        ("/playback/volume", {"volume_percent": 101}),
        ("/playback/volume", {"volume_percent": "50"}),
        ("/playback/play", {"context_uri": "spotify:album:x", "uris": ["spotify:track:y"]}),
        ("/playback/play", {"uris": []}),
        ("/playback/play", {"uris": ["https://not-a-uri"]}),
    ]
    for path, body in bad_bodies:
        calls_before = list(fake.calls)
        assert http.post(path, json=body).status_code == 422, (path, body)
        assert fake.calls == calls_before, f"{path} reached Spotify: {body}"


def test_playback_validation_accepts_the_documented_happy_paths():
    api = load_api()
    fake = FakeSpotify({})
    http = install(api, fake)
    good = [
        ("/playback/shuffle", {"state": False}),
        ("/playback/shuffle", {"state": True}),
        ("/playback/repeat", {"state": "off"}),
        ("/playback/repeat", {"state": "context"}),
        ("/playback/repeat", {"state": "track"}),
        ("/playback/seek", {"position_ms": 0}),
        ("/playback/seek", {"position_ms": 90_000}),
        ("/playback/volume", {"volume_percent": 0}),
        ("/playback/volume", {"volume_percent": 100}),
        ("/playback/play", {"context_uri": "spotify:album:x"}),
        ("/playback/play", {"uris": ["spotify:track:y"]}),
    ]
    for path, body in good:
        assert http.post(path, json=body).status_code == 200, (path, body)


# --- F3: stateless capability probe -----------------------------------------


def test_capability_probe_never_mutates_shared_capability_state():
    api = load_api()
    before = dict(api._CAPABILITIES)
    fake = FakeSpotify({"preview_url": "https://p", "id": "t"})
    response = install(api, fake).post(
        "/capabilities/probe", json={"feature": "preview_url", "track_id": "t"}
    )
    assert response.json() == {
        "ok": True,
        "feature": "preview_url",
        "available": True,
    }
    assert api._CAPABILITIES == before
    assert client_status_caps(api) == before


def test_status_capabilities_are_static_after_probe_flips():
    api = load_api()
    fake = FakeSpotify(error=None)
    http = install(api, fake)

    class ProbeAvailable(FakeSpotify):
        def request(self, *args, **kwargs):
            return {"preview_url": "https://p"}

    probe_http = install(api, ProbeAvailable())
    assert (
        probe_http.post(
            "/capabilities/probe", json={"feature": "preview_url", "track_id": "t"}
        ).json()["available"]
        is True
    )
    assert http.get("/status").json()["capabilities"]["preview_url"] is False


# --- F4: honest /status semantics -------------------------------------------


def test_status_reports_credentials_available_not_verified_connected():
    api = load_api()
    # Construction succeeds = credentials resolved locally (no network I/O).
    api._client_factory = lambda: object()
    response = install(api, FakeSpotify()).get("/status")
    payload = response.json()
    assert payload["auth"]["state"] == "credentials_available"
    assert payload["auth"]["verified"] is False


def client_status_caps(api):
    return install(api, FakeSpotify()).get("/status").json()["capabilities"]
