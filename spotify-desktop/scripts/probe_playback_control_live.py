"""Authorized live diagnosis of spotify-desktop control routes; leaves playback paused."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
API_PATH = ROOT / "dashboard" / "plugin_api.py"
OUT = ROOT / "evidence" / "playback-control" / "live-http-trace.json"


def load_api():
    spec = importlib.util.spec_from_file_location(
        "spotify_desktop_live_probe", API_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def redact_payload(value):
    if isinstance(value, dict):
        return {
            key: redact_payload(item)
            for key, item in value.items()
            if key not in {"url", "external_urls"}
        }
    if isinstance(value, list):
        return [redact_payload(item) for item in value]
    return value


def record(http: TestClient, method: str, path: str, body: dict | None = None):
    request_kwargs = {"json": body} if body is not None else {}
    response = getattr(http, method)(path, **request_kwargs)
    try:
        payload = redact_payload(response.json())
    except ValueError:
        payload = {"non_json_body": response.text[:500]}
    return {
        "request": {"method": method.upper(), "path": path, "json": body or {}},
        "status": response.status_code,
        "body": payload,
    }


def main() -> None:
    api = load_api()
    app = FastAPI()
    app.include_router(api.router)
    results = []
    with TestClient(app) as http:
        results.append(record(http, "get", "/status"))
        devices = record(http, "get", "/devices")
        results.append(devices)
        initial_playback = record(http, "get", "/playback")
        results.append(initial_playback)

        payload = initial_playback["body"] if initial_playback["status"] == 200 else {}
        raw_device = payload.get("device") or {}
        device_id = raw_device.get("id")
        if not device_id:
            device_list = (
                devices["body"].get("devices", []) if devices["status"] == 200 else []
            )
            target = next(
                (
                    device
                    for device in device_list
                    if device.get("id") and not device.get("is_restricted")
                ),
                None,
            )
            if target:
                device_id = target["id"]
                results.append(
                    record(
                        http,
                        "post",
                        "/transfer",
                        {"device_id": device_id, "play": False},
                    )
                )
                initial_playback = record(http, "get", "/playback")
                results.append(initial_playback)
                payload = (
                    initial_playback["body"]
                    if initial_playback["status"] == 200
                    else {}
                )
                raw_device = payload.get("device") or target
        action_body = {"device_id": device_id} if device_id else {}

        # Exercise each API action against the active target. Restore the two
        # preference toggles and leave the account paused even on an API error.
        try:
            results.append(record(http, "post", "/playback/play", action_body))
            results.append(record(http, "post", "/playback/next", action_body))
            results.append(record(http, "post", "/playback/previous", action_body))
            current_playback = record(http, "get", "/playback")
            results.append(current_playback)
            current_payload = (
                current_playback["body"] if current_playback["status"] == 200 else {}
            )
            current_device = current_payload.get("device") or raw_device
            initial_volume = current_device.get("volume_percent")
            initial_shuffle = current_payload.get("shuffle_state")
            initial_repeat = current_payload.get("repeat_state")
            progress = current_payload.get("progress_ms")
            results.append(
                record(
                    http,
                    "post",
                    "/playback/seek",
                    {
                        **action_body,
                        "position_ms": int(progress)
                        if isinstance(progress, int)
                        else 0,
                    },
                )
            )
            if isinstance(initial_volume, int):
                results.append(
                    record(
                        http,
                        "post",
                        "/playback/volume",
                        {**action_body, "volume_percent": initial_volume},
                    )
                )
            if isinstance(initial_shuffle, bool):
                results.append(
                    record(
                        http,
                        "post",
                        "/playback/shuffle",
                        {**action_body, "state": not initial_shuffle},
                    )
                )
                results.append(
                    record(
                        http,
                        "post",
                        "/playback/shuffle",
                        {**action_body, "state": initial_shuffle},
                    )
                )
            if initial_repeat in {"off", "context", "track"}:
                temporary_repeat = "context" if initial_repeat == "off" else "off"
                results.append(
                    record(
                        http,
                        "post",
                        "/playback/repeat",
                        {**action_body, "state": temporary_repeat},
                    )
                )
                results.append(
                    record(
                        http,
                        "post",
                        "/playback/repeat",
                        {**action_body, "state": initial_repeat},
                    )
                )
        finally:
            results.append(record(http, "post", "/playback/pause", action_body))
            results.append(record(http, "get", "/playback"))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(OUT)
    for entry in results:
        print(
            entry["request"]["method"],
            entry["request"]["path"],
            entry["status"],
            entry["body"],
        )


if __name__ == "__main__":
    main()
