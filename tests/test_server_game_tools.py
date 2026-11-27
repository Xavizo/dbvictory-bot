"""MCP tools that send packets refuse until a character is in game, and a reset forgets the session."""
import asyncio

import pytest

from dbv.mcp_server import server

GAME_TOOLS = {
    "walk": {"direction": "n"}, "turn": {"direction": "n"}, "say": {"text": "hi"},
    "attack": {"creature_id": 1}, "follow": {"creature_id": 1}, "stop": {},
    "use_item": {"x": 1, "y": 2, "z": 7, "item_id": 3}, "look_at": {"x": 1, "y": 2, "z": 7, "item_id": 3},
    "move_item": {"from_x": 1, "from_y": 2, "from_z": 7, "item_id": 3, "from_stack": 0,
                  "to_x": 2, "to_y": 2, "to_z": 7},
    "set_fight_modes": {}, "send_ping": {}, "logout": {}, "start_recording": {"name": "route"},
}


@pytest.mark.parametrize("name,arguments", sorted(GAME_TOOLS.items()))
def test_game_tools_refuse_without_a_character(monkeypatch, name, arguments):
    monkeypatch.setattr(server.state, "ready", False)
    sent = []
    monkeypatch.setattr(server, "_send", lambda packet: sent.append(packet))
    _, result = asyncio.run(server.mcp.call_tool(name, arguments))
    assert result["result"] == server.NOT_CONNECTED
    assert sent == []


def test_game_tools_keep_their_descriptions_and_parameters():
    tools = {tool.name: tool for tool in asyncio.run(server.mcp.list_tools())}
    assert tools["walk"].description.startswith("Take one or more steps")
    assert set(tools["move_item"].inputSchema["required"]) == {
        "from_x", "from_y", "from_z", "item_id", "from_stack", "to_x", "to_y", "to_z"}


def test_a_new_session_forgets_the_previous_one():
    bot = server.BotState()
    bot.ready, bot.recording_active, bot.recording_waypoints = True, True, [{"type": "walk"}]
    bot.game_proxy = object()
    old_state = bot.game_state
    bot.begin_session(type(old_state), {"automation_paused": True})
    assert not bot.connected and bot.game_proxy is None
    assert bot.game_state is not old_state
    assert (bot.recording_active, bot.recording_waypoints) == (False, [])
    assert bot.automation_paused and bot.automation_pause_kind == "emergency"
