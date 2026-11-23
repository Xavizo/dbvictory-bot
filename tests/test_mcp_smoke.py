"""Real stdio handshake and read-only tool calls; never attach to the game."""

import asyncio
import os
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


def test_stdio_catalog_and_disconnected_status(tmp_path):
    async def exercise():
        params = StdioServerParameters(
            command=sys.executable, args=[str(ROOT / "run_mcp.py")],
            cwd=str(tmp_path), env={**os.environ, "PYTHONUTF8": "1"},
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()
                names = {tool.name for tool in result.tools}
                assert {"start_bot", "open_dashboard", "get_status", "list_actions", "toggle_action"} <= names
                assert {"emergency_stop", "unlock_automation", "configure_potions", "potion_diagnostics"} <= names
                catalog = await session.list_resources()
                resources = {str(resource.uri): resource for resource in catalog.resources}
                for name in ("architecture", "modules", "decisions"):
                    uri = f"dbvictory://knowledge/vbot/{name}"
                    assert resources[uri].mimeType == "text/markdown"
                    result = await session.read_resource(resources[uri].uri)
                    expected = (ROOT / "docs" / "vbot" / f"{name}.md").read_text(encoding="utf-8")
                    assert result.contents[0].text == expected
                    assert expected.startswith("# ") and "09d6816" in expected
                status = await session.call_tool("get_status", {})
                assert not status.isError
                assert "MCP Server: running" in status.content[0].text
                assert "Proxy: not started" in status.content[0].text
                diagnostic = await session.call_tool("potion_diagnostics", {})
                assert not diagnostic.isError
    asyncio.run(asyncio.wait_for(exercise(), timeout=30))
