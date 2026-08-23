"""Startup-gate contract tests for the standalone spotify-desktop package."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _active_dashboard_plugins(home: Path) -> set[str]:
    config = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    plugins = config.get("plugins", {})
    enabled = set(plugins.get("enabled", []))
    disabled = set(plugins.get("disabled", []))
    manifests = [
        json.loads(path.read_text(encoding="utf-8"))["name"]
        for path in (home / "plugins").glob("*/dashboard/manifest.json")
    ]
    return {name for name in manifests if name in enabled and name not in disabled}


def test_enabled_and_disabled_gate_contract(tmp_path):
    home = tmp_path / "home"
    package = home / "plugins" / "spotify-desktop" / "dashboard"
    package.mkdir(parents=True)
    (package / "manifest.json").write_text(
        (PLUGIN_ROOT / "dashboard" / "manifest.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    (home / "config.yaml").write_text(
        yaml.safe_dump({"plugins": {"enabled": ["spotify-desktop"]}}), encoding="utf-8"
    )
    assert _active_dashboard_plugins(home) == {"spotify-desktop"}

    (home / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "plugins": {
                    "enabled": ["spotify-desktop"],
                    "disabled": ["spotify-desktop"],
                }
            }
        ),
        encoding="utf-8",
    )
    assert _active_dashboard_plugins(home) == set()
