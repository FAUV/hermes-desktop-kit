"""Delete only plugin-owned Spotify-derived cache data.

This helper deliberately never reads or changes Hermes' core auth.json. It is
safe to run repeatedly with an explicit HERMES_HOME path after the in-app
Disconnect action has cleared renderer-scoped preferences.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def delete_plugin_data(home: Path) -> dict[str, list[str]]:
    """Remove the declared Spotify Desktop cache directory beneath *home*."""
    root = home.expanduser().resolve(strict=False)
    cache = root / "cache" / "spotify-desktop"
    if cache.exists():
        shutil.rmtree(cache)
        return {"removed": ["cache/spotify-desktop"]}
    return {"removed": []}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Delete Spotify Desktop plugin cache data"
    )
    parser.add_argument("--home", type=Path, required=True, help="Target HERMES_HOME")
    args = parser.parse_args()
    print(delete_plugin_data(args.home))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
