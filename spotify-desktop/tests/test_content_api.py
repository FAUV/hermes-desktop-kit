"""T5 backend contracts: bounded Home data and capability-scoped degradation."""

from __future__ import annotations

from test_projection_contract import FakeSpotify, install, load_api


def test_recently_played_home_shelf_is_bounded_and_typed():
    api = load_api()
    fake = FakeSpotify(
        {
            "items": [
                {"track": {"id": "t", "name": "Sparse", "artists": [{}], "album": {}}}
            ]
        }
    )
    response = install(api, fake).get("/home/recently-played", params={"limit": 11})
    assert response.status_code == 422
    response = install(api, fake).get("/home/recently-played", params={"limit": 10})
    assert response.status_code == 200
    assert fake.calls[-1] == ("get_recently_played", {"limit": 10})
    assert response.json()["items"][0]["track"]["name"] == "Sparse"


def test_removed_recently_played_hides_only_that_home_shelf():
    api = load_api()
    error = api.SpotifyAPIError("gone", status_code=404, response_body="removed")
    fake = FakeSpotify(error=error)
    response = install(api, fake).get("/home/recently-played")
    assert response.status_code == 404
    assert response.json()["detail"]["category"] == "unavailable"
    assert api._CAPABILITIES["search"] is True
    assert api._CAPABILITIES["playlists"] is True
