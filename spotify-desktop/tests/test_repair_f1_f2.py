"""F1/F2 repair regression tests (QA HOLD on t_4815a03b).

F1: a client factory that raises SpotifyAuthRequiredError (the real
SpotifyClient.__init__ behavior when credentials cannot resolve) must produce
401 {"ok":false,"category":"not_authenticated"} on EVERY data endpoint, with
no secret leakage. F2: the attribution logo URL must be a currently-valid
official Spotify asset, verified live, not by string match alone.
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

import pytest
from test_projection_contract import client, load_api

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DEAD_LOGO_URL = (
    "https://developer.spotify.com/assets/branding-guidelines/spotify-logo.png"
)

# (method, path, valid request body or query params) for every data endpoint.
DATA_ENDPOINTS = [
    ("get", "/playback", None),
    ("get", "/devices", None),
    ("get", "/queue", None),
    ("post", "/queue", {"uri": "spotify:track:x"}),
    ("post", "/transfer", {"device_id": "d"}),
    ("post", "/playback/pause", {}),
    ("get", "/search", {"q": "x"}),
    ("get", "/home/recently-played", None),
    ("get", "/albums/a1", None),
    ("get", "/library/contains", {"kind": "track", "ids": ["t1"]}),
    ("get", "/library/tracks", None),
    ("get", "/library/albums", None),
    ("get", "/library/artists", None),
    ("post", "/library/items", {"kind": "track", "ids": ["t1"], "saved": True}),
    ("get", "/playlists", None),
    ("get", "/playlists/p1", None),
    ("post", "/playlists", {"name": "n"}),
    ("patch", "/playlists/p1", {"name": "n"}),
    ("post", "/playlists/p1/items", {"action": "add", "uris": ["spotify:track:x"]}),
    ("post", "/capabilities/probe", {"feature": "artist_albums", "artist_id": "a"}),
]


def _auth_raising_factory(api):
    def factory():
        raise api.SpotifyAuthRequiredError("auth.json refresh_token=do-not-leak-secret")

    return factory


@pytest.mark.parametrize("method,path,body", DATA_ENDPOINTS)
def test_unauthenticated_data_endpoints_return_not_authenticated(method, path, body):
    api = load_api()
    api._client_factory = _auth_raising_factory(api)
    http = client(api)

    if method == "get":
        response = http.get(path, params=body or {})
    else:
        response = getattr(http, method)(path, json=body)

    assert response.status_code == 401, (
        f"{method} {path} returned {response.status_code}"
    )
    detail = response.json()["detail"]
    assert detail["ok"] is False
    assert detail["category"] == "not_authenticated"
    low = response.text.lower()
    assert "secret" not in low and "refresh_token" not in low and "auth.json" not in low


def test_status_still_reports_not_authenticated_without_downgrade():
    api = load_api()
    api._client_factory = _auth_raising_factory(api)
    response = client(api).get("/status")
    assert response.status_code == 200
    assert response.json()["auth"]["state"] == "not_authenticated"


ATTRIBUTION_URL = json.loads(
    (PLUGIN_ROOT / "desktop" / "attribution-source.json").read_text(encoding="utf-8")
)["attributionLogo"]


def test_attribution_logo_resolves_to_official_spotify_asset():
    plugin_js = (PLUGIN_ROOT / "desktop" / "plugin.js").read_text(encoding="utf-8")
    assert f"attributionLogo: '{ATTRIBUTION_URL}'" in plugin_js, (
        "COMPLIANCE.attributionLogo must stay in sync with attribution-source.json"
    )
    assert ATTRIBUTION_URL != DEAD_LOGO_URL
    request = urllib.request.Request(
        ATTRIBUTION_URL,
        method="HEAD",
        headers={"User-Agent": "spotify-desktop-plugin-qa/1.0"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        assert response.status in (200, 204, 206), response.status
        assert (response.headers.get("Content-Type") or "").startswith("image/")


def test_dead_developer_branding_url_is_not_referenced():
    for name in ("plugin.js", "tests/attribution.mjs"):
        text = (
            (PLUGIN_ROOT / name).read_text(encoding="utf-8")
            if name != "plugin.js"
            else (PLUGIN_ROOT / "desktop" / name).read_text(encoding="utf-8")
        )
        assert DEAD_LOGO_URL not in text, f"{name} still references the 404 asset"
