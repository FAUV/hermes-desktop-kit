"""L4: real isolated Hermes serve smoke for spotify-desktop."""

from __future__ import annotations

import os
import queue
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
READY = re.compile(r"HERMES_BACKEND_READY port=(\d+)")


def _launch(home: Path) -> tuple[subprocess.Popen[str], queue.Queue[str]]:
    executable = shutil.which("hermes")
    if not executable:
        pytest.skip("hermes executable is unavailable")
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["HERMES_HOME"] = str(home)
    env["HERMES_DASHBOARD_SESSION_TOKEN"] = "spotify-t0-isolated-token"
    process = subprocess.Popen(
        [executable, "serve", "--port", "0", "--skip-build", "--isolated"],
        cwd=str(home),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert process.stdout is not None
    lines: queue.Queue[str] = queue.Queue()
    threading.Thread(
        target=lambda: [lines.put(line) for line in process.stdout], daemon=True
    ).start()
    return process, lines


def _wait_for_port(
    process: subprocess.Popen[str], lines: queue.Queue[str]
) -> tuple[int, list[str]]:
    seen: list[str] = []
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            pytest.fail("isolated Hermes serve exited early:\n" + "".join(seen))
        try:
            line = lines.get(timeout=0.25)
        except queue.Empty:
            continue
        seen.append(line)
        match = READY.search(line)
        if match:
            return int(match.group(1)), seen
    pytest.fail("timed out waiting for isolated Hermes serve:\n" + "".join(seen))


def _request(port: int, path: str) -> tuple[int, str]:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        headers={"Authorization": "Bearer spotify-t0-isolated-token"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def _stop(process: subprocess.Popen[str]) -> None:
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def _install(home: Path, *, enabled: bool, disabled: bool = False) -> None:
    shutil.copytree(PLUGIN_ROOT, home / "plugins" / "spotify-desktop")
    config = "plugins:\n  enabled:\n"
    config += "    - spotify-desktop\n" if enabled else "    []\n"
    if disabled:
        config += "  disabled:\n    - spotify-desktop\n"
    (home / "config.yaml").write_text(config, encoding="utf-8")


def _mounted(home: Path, lines: list[str]) -> bool:
    signature = "Mounted plugin API routes: /api/plugins/spotify-desktop/"
    log_path = home / "logs" / "agent.log"
    log = (
        log_path.read_text(encoding="utf-8", errors="replace")
        if log_path.exists()
        else ""
    )
    return any(signature in line for line in lines) or signature in log


def test_isolated_enable_mounts_status_route_and_disabled_does_not(tmp_path):
    enabled_home = tmp_path / "enabled"
    _install(enabled_home, enabled=True)
    process, lines = _launch(enabled_home)
    try:
        port, seen = _wait_for_port(process, lines)
        status, body = _request(port, "/api/plugins/spotify-desktop/status")
        assert status == 200
        assert '"plugin":"spotify-desktop"' in body.replace(" ", "")
        # The server logging handler may emit after READY; collect briefly.
        until = time.monotonic() + 2
        while time.monotonic() < until:
            try:
                seen.append(lines.get(timeout=0.1))
            except queue.Empty:
                pass
        assert _mounted(enabled_home, seen)
    finally:
        _stop(process)

    # A desktop serve child reads enabled plugins only at startup. Reusing the
    # same clean home with a second fresh child proves the package still mounts
    # after an isolated restart, without touching the user's Desktop runtime.
    process, lines = _launch(enabled_home)
    try:
        port, seen = _wait_for_port(process, lines)
        status, body = _request(port, "/api/plugins/spotify-desktop/status")
        assert status == 200
        assert '"plugin":"spotify-desktop"' in body.replace(" ", "")
        assert _mounted(enabled_home, seen)
    finally:
        _stop(process)

    disabled_home = tmp_path / "disabled"
    _install(disabled_home, enabled=True, disabled=True)
    process, lines = _launch(disabled_home)
    try:
        port, seen = _wait_for_port(process, lines)
        status, _ = _request(port, "/api/plugins/spotify-desktop/status")
        assert status == 404
        assert not _mounted(disabled_home, seen)
    finally:
        _stop(process)
