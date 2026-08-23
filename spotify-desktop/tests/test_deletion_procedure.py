"""T2 deletion-procedure boundary tests."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "delete_spotify_personal_data.py"


def load_script():
    spec = importlib.util.spec_from_file_location("spotify_desktop_delete", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_deletion_procedure_removes_only_plugin_owned_cache_and_is_idempotent(tmp_path):
    module = load_script()
    home = tmp_path / "hermes-home"
    plugin_cache = home / "cache" / "spotify-desktop"
    plugin_cache.mkdir(parents=True)
    (plugin_cache / "recent-metadata.json").write_text(
        '{"artist":"private"}', encoding="utf-8"
    )
    auth = home / "auth.json"
    auth.write_text('{"spotify":{"refresh_token":"must-remain"}}', encoding="utf-8")

    first = module.delete_plugin_data(home)
    second = module.delete_plugin_data(home)

    assert first == {"removed": ["cache/spotify-desktop"]}
    assert second == {"removed": []}
    assert not plugin_cache.exists()
    assert (
        auth.read_text(encoding="utf-8")
        == '{"spotify":{"refresh_token":"must-remain"}}'
    )


def test_deletion_helper_rejects_home_that_cannot_resolve():
    module = load_script()
    bad = Path("\0")
    try:
        module.delete_plugin_data(bad)
    except (ValueError, OSError):
        pass
    else:  # pragma: no cover
        raise AssertionError("expected invalid path to fail closed")
