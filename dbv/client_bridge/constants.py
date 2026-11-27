"""Game constants: the server address and ports, a few item ids, and the
server packet layouts from ``offsets.json``.

The address and ports can be overridden with DBV_SERVER_HOST, DBV_LOGIN_PORT
and DBV_GAME_PORT, for example to point the proxy at a test server.
"""
import json
import os

from dbv.paths import INSTALL_ROOT


def _port(variable: str, default: int) -> int:
    port = int(os.environ.get(variable) or default)
    if not 0 < port < 65536:
        raise ValueError(f"{variable} must be a TCP port from 1 to 65535")
    return port


SERVER_HOST = os.environ.get("DBV_SERVER_HOST", "87.98.220.215")
LOGIN_PORT = _port("DBV_LOGIN_PORT", 7171)
GAME_PORT = _port("DBV_GAME_PORT", 7172)
# The server address as the login reply carries it: 4 raw bytes and as text.
SERVER_IP_BYTES = bytes(map(int, SERVER_HOST.split(".")))
SERVER_IP_STR = SERVER_HOST.encode("ascii")

ITEM_RED_HAM = 3583
ITEM_RUNE_3165 = 3165
MONSTER_ID_MIN = 0x40000000  # creature ids from here up are monsters

try:
    PACKET_FORMATS = json.loads((INSTALL_ROOT / "offsets.json").read_text(encoding="utf-8"))["packet_formats"]
except (OSError, ValueError, KeyError):
    PACKET_FORMATS = {}
