"""T4 device and queue API contracts over the typed Spotify projection."""

from __future__ import annotations

from test_projection_contract import FakeSpotify, api_error, install, load_api


def test_device_projection_marks_current_transfer_and_volume_capabilities():
    api = load_api()
    fake = FakeSpotify(
        {
            "devices": [
                {
                    "id": "desktop",
                    "name": "Desktop",
                    "type": "Computer",
                    "is_active": True,
                    "is_restricted": False,
                    "volume_percent": 42,
                },
                {
                    "id": "speaker",
                    "name": "Speaker",
                    "type": "Speaker",
                    "is_active": False,
                    "is_restricted": True,
                    "volume_percent": 50,
                },
            ]
        }
    )

    response = install(api, fake).get("/devices")

    assert response.status_code == 200
    desktop, speaker = response.json()["devices"]
    assert desktop["is_active"] is True
    assert desktop["can_transfer"] is True
    assert desktop["can_adjust_volume"] is True
    assert speaker["is_restricted"] is True
    assert speaker["can_transfer"] is False
    assert speaker["can_adjust_volume"] is False


def test_transfer_is_single_device_and_volume_is_typed_per_device():
    api = load_api()
    fake = FakeSpotify({})
    http = install(api, fake)

    assert (
        http.post("/transfer", json={"device_id": "phone", "play": True}).status_code
        == 200
    )
    assert fake.calls[-1] == ("transfer_playback", {"device_id": "phone", "play": True})
    assert (
        http.post("/transfer", json={"device_ids": ["one", "two"]}).status_code == 422
    )
    assert (
        http.post(
            "/playback/volume", json={"device_id": "phone", "volume_percent": 33}
        ).status_code
        == 200
    )
    assert fake.calls[-1] == (
        "set_volume",
        {"device_id": "phone", "volume_percent": 33},
    )


def test_playback_actions_preserve_optional_play_fields_and_require_only_action_inputs():
    api = load_api()
    fake = FakeSpotify({})
    http = install(api, fake)

    response = http.post("/playback/play", json={})
    assert response.status_code == 200
    assert fake.calls[-1] == (
        "start_playback",
        {"device_id": None, "context_uri": None, "uris": None, "position_ms": None},
    )

    expected = [
        (
            "play",
            {"device_id": "desktop"},
            "start_playback",
            {
                "device_id": "desktop",
                "context_uri": None,
                "uris": None,
                "position_ms": None,
            },
        ),
        ("pause", {"device_id": "desktop"}, "pause_playback", {"device_id": "desktop"}),
        ("next", {"device_id": "desktop"}, "skip_next", {"device_id": "desktop"}),
        (
            "previous",
            {"device_id": "desktop"},
            "skip_previous",
            {"device_id": "desktop"},
        ),
        (
            "seek",
            {"device_id": "desktop", "position_ms": 123},
            "seek",
            {"device_id": "desktop", "position_ms": 123},
        ),
        (
            "volume",
            {"device_id": "desktop", "volume_percent": 33},
            "set_volume",
            {"device_id": "desktop", "volume_percent": 33},
        ),
        (
            "shuffle",
            {"device_id": "desktop", "state": True},
            "set_shuffle",
            {"device_id": "desktop", "state": True},
        ),
        (
            "repeat",
            {"device_id": "desktop", "state": "context"},
            "set_repeat",
            {"device_id": "desktop", "state": "context"},
        ),
    ]
    for action, body, method, kwargs in expected:
        response = http.post(f"/playback/{action}", json=body)
        assert response.status_code == 200
        assert fake.calls[-1] == (method, kwargs)

    for action in ("seek", "volume", "shuffle", "repeat"):
        calls_before = list(fake.calls)
        response = http.post(f"/playback/{action}", json={"device_id": "desktop"})
        assert response.status_code == 422
        assert response.json()["detail"] == {
            "ok": False,
            "category": "unavailable",
            "retry_after_seconds": None,
        }
        assert fake.calls == calls_before


def test_queue_projection_is_read_only_and_add_delegates_to_end_only_client_method():
    api = load_api()
    fake = FakeSpotify(
        {
            "currently_playing": {
                "id": "now",
                "uri": "spotify:track:now",
                "name": "Now",
            },
            "queue": [{"id": "next", "uri": "spotify:track:next", "name": "Next"}],
        }
    )
    http = install(api, fake)

    response = http.get("/queue")
    assert response.status_code == 200
    assert response.json()["currently_playing"]["name"] == "Now"
    assert response.json()["queue"][0]["name"] == "Next"
    assert (
        http.post(
            "/queue", json={"uri": "spotify:track:add", "device_id": "phone"}
        ).status_code
        == 200
    )
    assert fake.calls[-1] == (
        "add_to_queue",
        {"uri": "spotify:track:add", "device_id": "phone"},
    )
    assert http.delete("/queue").status_code == 405
    assert http.post("/queue/reorder", json={}).status_code == 404


def test_device_queue_rate_limit_preserves_retry_after_and_secret_boundary():
    api = load_api()
    fake = FakeSpotify(
        error=api_error(
            api, 429, "QUOTA_EXCEEDED Retry after 23 seconds access_token=hidden"
        )
    )

    response = install(api, fake).get("/queue")

    assert response.status_code == 429
    assert response.json()["detail"] == {
        "ok": False,
        "category": "quota_exceeded",
        "retry_after_seconds": 23,
    }
    assert "hidden" not in response.text and "access_token" not in response.text
