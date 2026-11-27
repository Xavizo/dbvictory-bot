"""DBVictory bot package. User data stays at the install root (see dbv.paths).

client_bridge  talks to the game client
brain          decides what the bot does
mcp_server     how Codex, Claude and the panel reach the engine
"""
from dbv import _legacy

_legacy.install()
