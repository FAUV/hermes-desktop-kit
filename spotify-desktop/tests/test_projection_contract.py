"""L2/L3 regression coverage for T1's safe Spotify projection."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "dashboard" / "plugin_api.py"


def load_api():
    spec = importlib.util.spec_from_file_location("spotify_projection_test", PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def client(api):
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)


class FakeSpotify:
    def __init__(self, payload=None, error=None):
        self.payload, self.error, self.calls = payload or {}, error, []

    def _call(self, name, **kwargs):
        self.calls.append((name, kwargs))
        if self.error:
            raise self.error
        return self.payload

    def get_playback_state(self):
        return self._call("get_playback_state")

    def get_devices(self):
        return self._call("get_devices")

    def transfer_playback(self, **kwargs):
        return self._call("transfer_playback", **kwargs)

    def get_queue(self):
        return self._call("get_queue")

    def add_to_queue(self, **kwargs):
        return self._call("add_to_queue", **kwargs)

    def start_playback(self, **kwargs):
        return self._call("start_playback", **kwargs)

    def pause_playback(self, **kwargs):
        return self._call("pause_playback", **kwargs)

    def skip_next(self, **kwargs):
        return self._call("skip_next", **kwargs)

    def skip_previous(self, **kwargs):
        return self._call("skip_previous", **kwargs)

    def seek(self, **kwargs):
        return self._call("seek", **kwargs)

    def set_volume(self, **kwargs):
        return self._call("set_volume", **kwargs)

    def set_shuffle(self, **kwargs):
        return self._call("set_shuffle", **kwargs)

    def set_repeat(self, **kwargs):
        return self._call("set_repeat", **kwargs)

    def search(self, **kwargs):
        return self._call("search", **kwargs)

    def get_album(self, **kwargs):
        return self._call("get_album", **kwargs)

    def get_saved_tracks(self, **kwargs):
        return self._call("get_saved_tracks", **kwargs)

    def get_saved_albums(self, **kwargs):
        return self._call("get_saved_albums", **kwargs)

    def get_my_playlists(self, **kwargs):
        return self._call("get_my_playlists", **kwargs)

    def get_playlist(self, **kwargs):
        return self._call("get_playlist", **kwargs)

    def get_recently_played(self, **kwargs):
        return self._call("get_recently_played", **kwargs)

    def request(self, *_args):
        return self._call("request")


def install(api, fake):
    api._client_factory = lambda: fake
    return client(api)


def api_error(api, status, text):
    error = api.SpotifyAPIError("upstream", status_code=status, response_body=text)
    error.path = "/me/player"
    return error


def test_missing_or_revoked_auth_is_a_secret_free_connect_state():
    api = load_api()
    api._client_factory = lambda: (_ for _ in ()).throw(
        api.SpotifyAuthRequiredError("refresh_token=secret")
    )
    response = client(api).get("/status")
    assert response.status_code == 200
    assert response.json()["auth"]["state"] == "not_authenticated"
    assert "secret" not in response.text and "refresh_token" not in response.text


def test_idle_204_projection_and_sparse_track_do_not_require_catalog_fields():
    api = load_api()
    response = install(api, FakeSpotify({"empty": True, "status_code": 204})).get(
        "/playback"
    )
    assert response.json() == {"ok": True, "idle": True, "item": None, "device": None}
    sparse = api._track({"id": "t", "name": "x", "artists": [{}], "album": {}})
    assert sparse == {
        "id": "t",
        "uri": None,
        "name": "x",
        "duration_ms": None,
        "explicit": None,
        "artists": [{"id": None, "name": None, "uri": None}],
        "album": {"id": None, "name": None, "images": []},
    }


def test_error_categories_retry_after_and_no_leaks():
    cases = [
        (403, "Premium required", "premium_required"),
        (403, "restricted device", "restricted_device"),
        (403, "No active device", "no_active_device"),
        (404, "Player command failed", "no_active_device"),
        (429, "QUOTA_EXCEEDED Retry after 17 seconds", "quota_exceeded"),
        (429, "Retry after 3 seconds", "rate_limited"),
    ]
    for status, detail, category in cases:
        api = load_api()
        response = install(
            api,
            FakeSpotify(
                error=api_error(api, status, detail + " access_token=do-not-leak")
            ),
        ).post("/playback/pause", json={})
        assert response.json()["detail"]["category"] == category
        assert (
            "do-not-leak" not in response.text and "access_token" not in response.text
        )
    api = load_api()
    response = install(
        api, FakeSpotify(error=api_error(api, 503, "auth.json refresh_token=bad"))
    ).get("/devices")
    assert response.json()["detail"]["category"] == "unavailable"
    assert "auth.json" not in response.text and "bad" not in response.text


def test_bounded_search_and_mutations_are_typed_and_delegate_to_existing_client():
    api = load_api()
    fake = FakeSpotify({"tracks": {"items": []}})
    http = install(api, fake)
    assert http.get("/search", params={"q": "abc", "limit": 11}).status_code == 422
    assert http.get("/search", params={"q": "abc", "limit": 10}).status_code == 200
    assert fake.calls[-1] == (
        "search",
        {
            "query": "abc",
            "search_types": ["track", "artist", "album", "playlist"],
            "limit": 10,
            "offset": 0,
        },
    )
    assert (
        http.post("/transfer", json={"device_id": "one", "play": True}).status_code
        == 200
    )
    assert http.post("/queue", json={"uri": "spotify:track:a"}).status_code == 200
    assert (
        http.post("/playback/volume", json={"volume_percent": 101}).status_code == 422
    )


def test_capability_404_403_degrades_only_the_optional_feature():
    for status in (403, 404):
        api = load_api()
        fake = FakeSpotify(error=api_error(api, status, "gone"))
        http = install(api, fake)
        response = http.post(
            "/capabilities/probe", json={"feature": "artist_albums", "artist_id": "a"}
        )
        assert response.json() == {
            "ok": True,
            "feature": "artist_albums",
            "available": False,
        }
        assert http.get("/status").json()["capabilities"]["artist_albums"] is False
