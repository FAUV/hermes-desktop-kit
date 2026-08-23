"""T0 contract tests for the standalone spotify-desktop backend."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
API_PATH = PLUGIN_ROOT / "dashboard" / "plugin_api.py"
MANIFEST_PATH = PLUGIN_ROOT / "dashboard" / "manifest.json"


def _load_api():
    spec = importlib.util.spec_from_file_location(
        "spotify_desktop_plugin_api_test", API_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _client(api):
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)


def test_manifest_identity_and_backend_entry_are_stable():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert manifest["name"] == "spotify-desktop"
    assert manifest["api"] == "plugin_api.py"
    assert manifest["version"] == "0.1.0"


def test_status_is_typed_capability_projection_without_secrets():
    api = _load_api()
    api._client_factory = lambda: (_ for _ in ()).throw(
        api.SpotifyAuthRequiredError("refresh_token=secret")
    )
    client = _client(api)

    response = client.get("/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["plugin"] == "spotify-desktop"
    assert payload["auth"] == {"state": "not_authenticated"}
    assert payload["capabilities"]["status"] is True
    assert all(
        not enabled
        for name, enabled in payload["capabilities"].items()
        if name != "status"
    )
    serialized = response.text.lower()
    for forbidden in ("access_token", "refresh_token", "auth.json", "bearer "):
        assert forbidden not in serialized


def test_status_is_get_only():
    api = _load_api()
    client = _client(api)

    assert client.post("/status").status_code == 405
