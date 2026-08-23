"""T6 library and playlist-management contracts over a mocked Spotify client."""

from __future__ import annotations

from test_projection_contract import install, load_api


class LibrarySpotify:
    def __init__(self):
        self.calls = []

    def _call(self, operation, **kwargs):
        self.calls.append((operation, kwargs))
        return {"snapshot_id": "next-snapshot"}

    def get_saved_tracks(self, **kwargs):
        return self._call("get_saved_tracks", **kwargs)

    def get_saved_albums(self, **kwargs):
        return self._call("get_saved_albums", **kwargs)

    def save_library_items(self, **kwargs):
        return self._call("save_library_items", **kwargs)

    def library_contains(self, **kwargs):
        self.calls.append(("library_contains", kwargs))
        return [True, False]

    def create_playlist(self, **kwargs):
        return self._call("create_playlist", **kwargs)

    def get_playlist(self, **kwargs):
        self.calls.append(("get_playlist", kwargs))
        return {
            "id": kwargs["playlist_id"],
            "owner": {"id": "me"},
            "snapshot_id": "old",
        }

    def update_playlist_details(self, **kwargs):
        return self._call("update_playlist_details", **kwargs)

    def add_playlist_items(self, **kwargs):
        return self._call("add_playlist_items", **kwargs)

    def remove_playlist_items(self, **kwargs):
        return self._call("remove_playlist_items", **kwargs)

    def request(self, method, path, **kwargs):
        self.calls.append(("request", {"method": method, "path": path, **kwargs}))
        if method == "GET" and path == "/me":
            return {"id": "me"}
        if method == "GET" and path == "/me/following":
            return {"artists": {"items": [{"id": "artist-1", "name": "Artist"}]}}
        if method == "GET" and path == "/me/library/contains":
            return [True, False]
        return {"snapshot_id": "next-snapshot"}


def test_library_reads_artists_and_contains_checks_drive_heart_state():
    api = load_api()
    fake = LibrarySpotify()
    http = install(api, fake)

    artists = http.get("/library/artists", params={"limit": 10, "offset": 0})
    assert artists.status_code == 200
    assert artists.json()["page"]["items"][0]["id"] == "artist-1"
    assert fake.calls[-1] == (
        "request",
        {
            "method": "GET",
            "path": "/me/following",
            "params": {"type": "artist", "limit": 10, "offset": 0},
        },
    )

    contains = http.get(
        "/library/contains", params=[("kind", "track"), ("ids", "one"), ("ids", "two")]
    )
    assert contains.status_code == 200
    assert contains.json()["contains"] == [True, False]
    assert fake.calls[-1] == (
        "library_contains",
        {"uris": ["spotify:track:one", "spotify:track:two"]},
    )


def test_generic_library_save_unsave_uses_only_me_library():
    api = load_api()
    fake = LibrarySpotify()
    http = install(api, fake)

    assert http.post(
        "/library/items", json={"kind": "artist", "ids": ["a"], "saved": True}
    ).json() == {"ok": True, "saved": True}
    assert fake.calls[-1] == ("save_library_items", {"uris": ["spotify:artist:a"]})
    assert http.post(
        "/library/items", json={"kind": "album", "ids": ["b"], "saved": False}
    ).json() == {"ok": True, "saved": False}
    assert fake.calls[-1] == (
        "request",
        {
            "method": "DELETE",
            "path": "/me/library",
            "params": {"uris": "spotify:album:b"},
        },
    )


def test_owned_playlist_crud_and_item_mutations_reuse_snapshot_id():
    api = load_api()
    fake = LibrarySpotify()
    http = install(api, fake)

    created = http.post(
        "/playlists", json={"name": "Library edits", "description": "local"}
    )
    assert created.status_code == 200
    assert fake.calls[-1] == (
        "create_playlist",
        {
            "name": "Library edits",
            "public": False,
            "collaborative": False,
            "description": "local",
        },
    )

    edited = http.patch("/playlists/p", json={"name": "Renamed"})
    assert edited.json() == {"ok": True, "snapshot_id": "next-snapshot"}
    assert fake.calls[-1] == (
        "update_playlist_details",
        {
            "playlist_id": "p",
            "name": "Renamed",
            "public": None,
            "collaborative": None,
            "description": None,
        },
    )

    removed = http.post(
        "/playlists/p/items",
        json={"action": "remove", "uris": ["spotify:track:t"], "snapshot_id": "old"},
    )
    assert removed.json()["snapshot_id"] == "next-snapshot"
    assert fake.calls[-1] == (
        "remove_playlist_items",
        {"playlist_id": "p", "uris": ["spotify:track:t"], "snapshot_id": "old"},
    )

    reordered = http.post(
        "/playlists/p/items",
        json={
            "action": "reorder",
            "range_start": 4,
            "insert_before": 1,
            "snapshot_id": "next-snapshot",
        },
    )
    assert reordered.json()["snapshot_id"] == "next-snapshot"
    assert fake.calls[-1] == (
        "request",
        {
            "method": "PUT",
            "path": "/playlists/p/items",
            "json_body": {
                "range_start": 4,
                "insert_before": 1,
                "snapshot_id": "next-snapshot",
            },
        },
    )


def test_other_users_playlists_are_rejected_for_mutations():
    api = load_api()
    fake = LibrarySpotify()
    fake.get_playlist = lambda **kwargs: {
        "id": kwargs["playlist_id"],
        "owner": {"id": "other"},
    }
    http = install(api, fake)
    projected = http.get("/playlists/not-mine")
    assert projected.status_code == 200
    assert projected.json()["playlist"]["is_owned"] is False
    response = http.post(
        "/playlists/not-mine/items", json={"action": "add", "uris": ["spotify:track:t"]}
    )
    assert response.status_code == 403
    assert response.json()["detail"]["category"] == "unavailable"
