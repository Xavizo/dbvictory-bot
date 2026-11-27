"""
DBVictory Bot — Centralized game constants.

Single source of truth for game-specific RVAs, network config, item IDs,
player icon bits and creature ID ranges.

Server packet layouts are loaded from offsets.json at import time.
"""

import json
import os
from pathlib import Path
from dbv.paths import INSTALL_ROOT

_PROJECT_ROOT = INSTALL_ROOT

# ── Network ────────────────────────────────────────────────────────
SERVER_HOST = os.environ.get("DBV_SERVER_HOST", "87.98.220.215")
def _configured_port(name: str, default: int) -> int:
    value = int(os.environ.get(name, str(default)))
    if not 1 <= value <= 65535:
        raise ValueError(f"{name} must be a TCP port from 1 to 65535")
    return value


LOGIN_PORT = _configured_port("DBV_LOGIN_PORT", 7171)
GAME_PORT = _configured_port("DBV_GAME_PORT", 7172)

# Packed binary forms used by proxy IP replacement
SERVER_IP_BYTES = bytes(int(b) for b in SERVER_HOST.split("."))  # e.g. b'\x57\x62\xdc\xd7'
SERVER_IP_STR = SERVER_HOST.encode("ascii")                      # e.g. b'87.98.220.215'

# ── Item IDs ───────────────────────────────────────────────────────
ITEM_RED_HAM = 3583
ITEM_RUNE_3165 = 3165

# ── Creature ID ranges ─────────────────────────────────────────────
MONSTER_ID_MIN = 0x40000000  # OT creature ID range: monsters start here

# ── Offsets from offsets.json ──────────────────────────────────────
# Loaded at import time so the rest of the codebase can use them directly.
_offsets_path = _PROJECT_ROOT / "offsets.json"
try:
    with open(_offsets_path, encoding="utf-8") as _f:
        OFFSETS = json.load(_f)
except (FileNotFoundError, json.JSONDecodeError):
    OFFSETS = {}

# Packet format sizes and field offsets — used by game_state.py for parsing.
# Externalised so server packet format changes only need a JSON edit.
PACKET_FORMATS = OFFSETS.get("packet_formats", {})
