"""Live F1 verification: real isolated hermes serve, logged-out Spotify.

Reproduces QA's repro path end-to-end: install the repaired package into a
clean HERMES_HOME, boot `hermes serve --isolated`, and assert every data
endpoint now returns 401 not_authenticated (previously HTTP 500).
"""

from __future__ import annotations

import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

PLUGIN_ROOT = Path(
    os.environ.get("SPOTIFY_DESKTOP_PLUGIN_ROOT", "~/.hermes/plugins/spotify-desktop")
).expanduser()
READY = re.compile(r"HERMES_BACKEND_READY port=(\d+)")
TOKEN = "spotify-f1-repair-token"

ENDPOINTS = [
    ("GET", "/playback", None),
    ("GET", "/devices", None),
    ("GET", "/queue", None),
    ("GET", "/search?q=x", None),
    ("GET", "/library/tracks", None),
    ("GET", "/library/contains?kind=track&ids=t1", None),
    ("GET", "/playlists", None),
    ("GET", "/home/recently-played", None),
    ("POST", "/queue", {"uri": "spotify:track:x"}),
    ("POST", "/transfer", {"device_id": "d"}),
    ("POST", "/playback/pause", {"device_id": None}),
    ("POST", "/playlists", {"name": "n"}),
    ("POST", "/library/items", {"kind": "track", "ids": ["t1"], "saved": True}),
    ("POST", "/capabilities/probe", {"feature": "artist_albums", "artist_id": "a"}),
]


def main() -> int:
    home = Path(tempfile.mkdtemp(prefix="f1-repair-"))
    shutil.copytree(PLUGIN_ROOT, home / "plugins" / "spotify-desktop")
    (home / "config.yaml").write_text(
        "plugins:\n  enabled:\n    - spotify-desktop\n", encoding="utf-8"
    )
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["HERMES_HOME"] = str(home)
    env["HERMES_DASHBOARD_SESSION_TOKEN"] = TOKEN
    proc = subprocess.Popen(
        ["hermes", "serve", "--port", "0", "--skip-build", "--isolated"],
        cwd=str(home), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )
    assert proc.stdout is not None
    lines: queue.Queue[str] = queue.Queue()
    threading.Thread(
        target=lambda: [lines.put(l) for l in proc.stdout], daemon=True
    ).start()
    port, seen = None, []
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            line = lines.get(timeout=0.25)
        except queue.Empty:
            continue
        seen.append(line)
        m = READY.search(line)
        if m:
            port = int(m.group(1))
            break
    if port is None:
        print("SERVE FAILED TO START:\n" + "".join(seen))
        proc.terminate()
        return 2
    failures = 0
    base = f"http://127.0.0.1:{port}/api/plugins/spotify-desktop"
    time.sleep(1.0)
    for method, path, body in ENDPOINTS:
        req = urllib.request.Request(
            base + path,
            data=body and __import__("json").dumps(body).encode() or None,
            headers={
                "Authorization": f"Bearer {TOKEN}",
                "Content-Type": "application/json",
            },
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                code, text = r.status, r.read().decode()
        except urllib.error.HTTPError as exc:
            code, text = exc.code, exc.read().decode()
        ok = (
            code == 401
            and '"category":"not_authenticated"' in text.replace(" ", "")
            and "secret" not in text.lower()
            and "refresh_token" not in text.lower()
        )
        print(f"{'PASS' if ok else 'FAIL'} {method} {path} -> {code} {text[:120]}")
        failures += 0 if ok else 1
    # /status must stay 200 not_authenticated
    with urllib.request.urlopen(
        urllib.request.Request(
            base + "/status", headers={"Authorization": f"Bearer {TOKEN}"}
        ),
        timeout=10,
    ) as r:
        body = r.read().decode()
        ok = r.status == 200 and '"state":"not_authenticated"' in body.replace(" ", "")
        print(f"{'PASS' if ok else 'FAIL'} GET /status -> {r.status}")
        failures += 0 if ok else 1
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    shutil.rmtree(home, ignore_errors=True)
    print("RESULT:", "ALL PASS" if failures == 0 else f"{failures} FAILURES")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
