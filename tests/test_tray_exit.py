"""Exercise both shutdown paths with open stdin and no game connection."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest


ROOT = Path(__file__).resolve().parents[1]


def wait_for_text(file, token, process):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if file.exists() and token in file.read_text(encoding="utf-8"):
            return
        assert process.poll() is None, f"process exited before {token!r}"
        time.sleep(0.05)
    pytest.fail(f"timed out waiting for {token!r}")


@pytest.mark.parametrize("standalone", [True, False], ids=["launcher", "direct-mcp"])
def test_tray_ends_session_with_host_input_open(tmp_path, standalone):
    env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONUTF8": "1"}
    env.pop("DBV_TRAY_EXIT_FILE", None)
    if standalone:
        # A local MCP fixture replaces the game engine behind the real launcher.
        (tmp_path / "run_mcp.py").write_text("""
import atexit, os
from pathlib import Path
from mcp.server.fastmcp import FastMCP
server = FastMCP("offline")
@server.tool()
def ready() -> str:
    return os.environ["DBV_TRAY_EXIT_FILE"]
atexit.register(lambda: Path("child-stopped").touch())
server.run()
""", encoding="utf-8")
        script = """
import os, sys
from pathlib import Path
import run_bot as launcher
async def check(session, settings):
    result = await session.call_tool("ready", {})
    Path("owner-file").write_text(result.content[0].text, encoding="utf-8")
launcher.start_checked = check
launcher.occupied_ports = lambda ports: []
launcher.prepare_environment = lambda port: dict(os.environ)
launcher.ROOT = Path.cwd()
sys.argv = ["run_bot", "--start"]
sys.exit(launcher.main())
"""
        if os.name != "nt":
            pytest.skip("standalone launcher is Windows-only")
    else:
        script = """
import asyncio
from contextlib import contextmanager
from pathlib import Path
import mcp_server as server
original = server.tray_input
@contextmanager
def report_file(encoding):
    with original(encoding) as pair:
        Path("owner-file").write_text(str(pair[0]), encoding="utf-8")
        yield pair
server.tray_input = report_file
asyncio.run(server.serve_with_tray_exit())
"""
    output = tmp_path / "stdout"
    with output.open("wb") as log, subprocess.Popen(
        [sys.executable, "-B", "-c", script], cwd=tmp_path, env=env,
        stdin=subprocess.PIPE, stdout=log, stderr=subprocess.PIPE,
    ) as process:
        try:
            if not standalone:
                initialize = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                    "protocolVersion": "2025-03-26", "capabilities": {},
                    "clientInfo": {"name": "offline-test", "version": "1"}}}
                process.stdin.write((json.dumps(initialize) + "\n").encode())
                process.stdin.flush()
                wait_for_text(output, "serverInfo", process)
            owner_file = tmp_path / "owner-file"
            wait_for_text(owner_file, "dbv-session-", process)
            marker = Path(owner_file.read_text(encoding="utf-8"))
            marker.touch()
            assert process.wait(timeout=12) == 0, process.stderr.read().decode(errors="replace")
            assert not marker.parent.exists()
            if standalone:
                assert (tmp_path / "child-stopped").exists()
                from run_bot import launcher_lock
                with launcher_lock(tmp_path / ".codex" / "standalone.lock"):
                    pass
        finally:
            if process.poll() is None:
                process.kill()
