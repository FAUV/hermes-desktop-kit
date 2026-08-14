"""API layer tests: routes validate input, map errors to HTTP semantics, and
exercise the full manual → diff → preview → apply flow through plugin_api.

The router functions are called directly (they take plain args, no
Request/Response dependencies) so no TestClient/httpx dependency is needed.
The engine is built lazily from TIME_MACHINE_HERMES_HOME at first route
call, so a module-scoped fixture pins it to a scratch home before any test
runs a route.
"""

from __future__ import annotations

import asyncio
import os

import pytest
from fastapi import HTTPException

os.environ["TIME_MACHINE_POLL_S"] = "3600"  # keep the watcher quiet in tests
os.environ["TIME_MACHINE_DEBOUNCE_S"] = "3600"

import plugin_api


@pytest.fixture(scope="module", autouse=True)
def api_home(tmp_path_factory):
    home = tmp_path_factory.mktemp("api-home")
    os.environ["TIME_MACHINE_HERMES_HOME"] = str(home)
    (home / "config.yaml").write_text("model: default\n")
    (home / "SOUL.md").write_text("# SOUL\nv1\n")
    (home / "skills" / "s").mkdir(parents=True)
    (home / "skills" / "s" / "SKILL.md").write_text("skill v1\n")
    (home / ".env").write_text("SECRET=sk-leak\n")
    return home


def _run(coro):
    return asyncio.run(coro)


def _newest(profile="default"):
    store, _, _ = plugin_api._get_engine()
    return store.latest_checkpoint(profile)["id"]


def test_health(api_home):
    res = _run(plugin_api.health())
    assert res["ok"] is True
    assert res["status"] == "ready"
    assert res["checkpoints"] == 0


def test_profiles(api_home):
    res = _run(plugin_api.profiles())
    ids = [p["id"] for p in res["profiles"]]
    assert "default" in ids


def test_manual_checkpoint_and_timeline(api_home):
    res = _run(
        plugin_api.create_manual_checkpoint({"profile": "default", "label": "api-test"})
    )
    assert res["checkpoint"]["trigger"] == "manual"
    assert res["checkpoint"]["label"] == "api-test"
    cid = res["checkpoint"]["id"]

    tl = _run(plugin_api.checkpoints(profile="default", limit=10))
    assert tl["count"] == 1
    assert tl["items"][0]["id"] == cid

    detail = _run(plugin_api.checkpoint_detail("default", cid))
    files = detail["checkpoint"]["files"]
    assert "config.yaml" in files
    assert ".env" not in files  # secrets never captured through the API


def test_diff_route(api_home):
    cid = _newest()
    (api_home / "config.yaml").write_text("model: changed\n")
    diff = _run(plugin_api.checkpoint_diff("default", cid, against="current"))
    assert diff["summary"]["modified"] == 1
    entry = next(i for i in diff["items"] if i["path"] == "config.yaml")
    assert entry["text_diff"]


def test_pin_route(api_home):
    cid = _newest()
    res = _run(plugin_api.pin_checkpoint("default", cid, {"pinned": True}))
    assert res["checkpoint"]["pinned"] is True


def test_restore_preview_apply_flow(api_home):
    store, catalog, _ = plugin_api._get_engine()
    from tm.snapshot import create_checkpoint

    # Deterministic: reset live state, checkpoint it, then diverge.
    (api_home / "config.yaml").write_text("model: default\n")
    (api_home / "SOUL.md").write_text("# SOUL\nv1\n")
    create_checkpoint(store, catalog, "default", "manual", label="base")
    base_id = _newest()
    (api_home / "config.yaml").write_text("model: changed\n")

    preview = _run(
        plugin_api.restore_preview({"profile": "default", "checkpoint_id": base_id})
    )
    assert preview["summary"]["replace"] == 1
    cfg = next(i for i in preview["items"] if i["path"] == "config.yaml")
    assert cfg["action"] == "replace"

    applied = _run(plugin_api.restore_apply({"plan_token": preview["token"]}))
    assert applied["verification"]["ok"] is True
    assert (api_home / "config.yaml").read_text() == "model: default\n"
    # The pre-restore safety checkpoint is recorded in the store.
    assert applied["safety_checkpoint"]
    # Plan token is consumed (cannot be re-applied).
    with pytest.raises(HTTPException) as ei:
        _run(plugin_api.restore_apply({"plan_token": preview["token"]}))
    assert ei.value.status_code == 404


def test_invalid_inputs(api_home):
    with pytest.raises(HTTPException) as ei:
        _run(plugin_api.checkpoint_detail("default", "../evil"))
    assert ei.value.status_code == 400

    with pytest.raises(HTTPException) as ei2:
        _run(plugin_api.create_manual_checkpoint({"profile": "ghost"}))
    assert ei2.value.status_code == 404

    with pytest.raises(HTTPException) as ei3:
        _run(plugin_api.restore_apply({"plan_token": "0" * 32}))
    assert ei3.value.status_code == 404


def test_catalog_route(api_home):
    res = _run(plugin_api.catalog_files(profile="default"))
    assert "config.yaml" in res["entries"]
    assert ".env" not in res["entries"]
