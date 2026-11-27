"""Open Tibia game protocol as DBVictory speaks it: opcodes, a reader and a
writer for packet bodies, and builders for every packet the bot sends.

All numbers are little-endian. A position is x:u16, y:u16, z:u8 and a string
is a u16 length followed by Latin-1 bytes. Opcodes are listed by value.
"""
import struct
from enum import IntEnum


class ClientOpcode(IntEnum):
    """Client → server."""
    LOGIN_SERVER = 0x01          # login server: account login
    GAME_LOGIN = 0x0A            # game server: character login
    LOGOUT = 0x14
    PING = 0x1E
    PONG = 0x1F
    NEW_WALK = 0x45              # OTCv8 "new walking" step (DBVClient_v5)
    AUTO_WALK = 0x64             # path of directions
    WALK_NORTH = 0x65
    WALK_EAST = 0x66
    WALK_SOUTH = 0x67
    WALK_WEST = 0x68
    STOP_WALK = 0x69
    WALK_NORTHEAST = 0x6A
    WALK_SOUTHEAST = 0x6B
    WALK_SOUTHWEST = 0x6C
    WALK_NORTHWEST = 0x6D
    TURN_NORTH = 0x6F
    TURN_EAST = 0x70
    TURN_SOUTH = 0x71
    TURN_WEST = 0x72
    MOVE_THING = 0x78            # drag an item: from, item, stack, to, count
    BUY_ITEM = 0x7A              # NPC trade
    SELL_ITEM = 0x7B
    CLOSE_NPC_TRADE = 0x7C
    REQUEST_TRADE = 0x7D         # player trade
    ACCEPT_TRADE = 0x7F
    REJECT_TRADE = 0x80
    USE_ITEM = 0x82              # position, item, stack, container index
    USE_ITEM_EX = 0x83           # use an item on a tile or another item
    USE_ON_CREATURE = 0x84       # use an item (rune) on a creature
    CLOSE_CONTAINER = 0x87
    UP_CONTAINER = 0x88          # open the parent container
    LOOK = 0x8C
    LOOK_IN_TRADE = 0x8E
    SAY = 0x96                   # speak mode, text
    OPEN_CHANNEL = 0x98
    CLOSE_CHANNEL = 0x99
    OPEN_PRIVATE_CHANNEL = 0x9A
    SET_FIGHT_MODES = 0xA0       # fight, chase, safe
    ATTACK = 0xA1                # creature id, 0 cancels
    FOLLOW = 0xA2
    CANCEL_ATTACK = 0xBE
    BROWSE_FIELD = 0xCB          # list the items on a tile
    SEEK_IN_CONTAINER = 0xCC
    SET_OUTFIT = 0xD3
    ADD_VIP = 0xDC
    REMOVE_VIP = 0xDD
    REQUEST_QUEST_LOG = 0xF0
    REQUEST_QUEST_LINE = 0xF1


class ServerOpcode(IntEnum):
    """Server → client."""
    LOGIN_OR_PENDING = 0x0A      # player id, then the first map description
    GM_ACTIONS = 0x0B
    UPDATE_NEEDED = 0x0C
    LOGIN_TOKEN = 0x0D
    LOGIN_ERROR = 0x14
    LOGIN_ADVISE = 0x15
    LOGIN_WAIT = 0x16
    LOGIN_SUCCESS = 0x17
    PING = 0x1D
    PONG = 0x1E
    CHALLENGE = 0x1F
    MAP_DESCRIPTION = 0x64       # full view around a position
    MAP_SLICE_NORTH = 0x65       # one new row or column after a step
    MAP_SLICE_EAST = 0x66
    MAP_SLICE_SOUTH = 0x67
    MAP_SLICE_WEST = 0x68
    TILE_ADD_THING = 0x6A
    TILE_TRANSFORM_THING = 0x6B
    TILE_REMOVE_THING = 0x6C
    CREATURE_MOVE = 0x6D
    OPEN_CONTAINER = 0x6E        # in this protocol 0x6E is a container, not a turn
    CLOSE_CONTAINER = 0x6F
    CREATE_IN_CONTAINER = 0x70
    CHANGE_IN_CONTAINER = 0x71
    REMOVE_FROM_CONTAINER = 0x72
    MAGIC_EFFECT = 0x83
    ANIMATED_TEXT = 0x84
    SHOOT_EFFECT = 0x85
    CREATURE_HEALTH = 0x8C
    CREATURE_LIGHT = 0x8D
    CREATURE_OUTFIT = 0x8E
    CREATURE_SPEED = 0x8F
    CREATURE_SKULL = 0x90
    CREATURE_PARTY = 0x91
    PLAYER_STATS = 0xA0
    PLAYER_SKILLS = 0xA1
    PLAYER_ICONS = 0xA2
    PLAYER_CANCEL_ATTACK = 0xA3
    TALK = 0xAA
    CHANNELS = 0xAB
    OPEN_CHANNEL = 0xAC
    PRIVATE_CHANNEL = 0xAD
    CLOSE_CHANNEL = 0xB2
    TEXT_MESSAGE = 0xB4
    PLAYER_CANCEL_WALK = 0xB5
    FLOOR_CHANGE_UP = 0xBE
    FLOOR_CHANGE_DOWN = 0xBF
    VIP_ADD = 0xD2
    VIP_STATE = 0xD3
    VIP_LOGOUT = 0xD4


class Direction(IntEnum):
    """The client's own direction numbers."""
    NORTH, EAST, SOUTH, WEST = 0, 1, 2, 3
    NORTHEAST, SOUTHEAST, SOUTHWEST, NORTHWEST = 4, 5, 6, 7


# One step in a direction, by opcode. Diagonals sit after STOP_WALK (0x69).
WALK_OPCODES = {d: ClientOpcode(0x65 + d if d < 4 else 0x66 + d) for d in Direction}
TURN_OPCODES = {d: ClientOpcode(0x6F + d) for d in Direction if d < 4}

_U16, _U32 = struct.Struct("<H"), struct.Struct("<I")
_POSITION = struct.Struct("<HHB")


class PacketReader:
    """Reads a packet body front to back.

    Running past the end raises IndexError (bytes, strings, u8) or
    struct.error (u16, u32), the same errors callers already handle.
    """

    def __init__(self, data: bytes):
        self._data = data
        self._pos = 0

    @property
    def position(self) -> int:
        return self._pos

    @property
    def remaining(self) -> int:
        return len(self._data) - self._pos

    def _unpack(self, layout):
        values = layout.unpack_from(self._data, self._pos)
        self._pos += layout.size
        return values

    def read_u8(self) -> int:
        value = self._data[self._pos]
        self._pos += 1
        return value

    def read_u16(self) -> int:
        return self._unpack(_U16)[0]

    def read_u32(self) -> int:
        return self._unpack(_U32)[0]

    def read_position(self) -> tuple[int, int, int]:
        return self._unpack(_POSITION)

    def read_bytes(self, count: int) -> bytes:
        if count > self.remaining:
            raise IndexError(f"need {count} bytes at offset {self._pos}, only {self.remaining} left")
        chunk = self._data[self._pos:self._pos + count]
        self._pos += count
        return chunk

    def read_string(self) -> str:
        size = self.read_u16()
        if size > self.remaining:
            raise IndexError(f"string of {size} bytes, only {self.remaining} left")
        return self.read_bytes(size).decode("latin-1")

    def peek_remaining(self) -> bytes:
        """The unread rest, without consuming it."""
        return self._data[self._pos:]


class PacketWriter:
    """Builds a packet body. Integers are truncated to their width."""

    def __init__(self):
        self._buf = bytearray()

    @property
    def data(self) -> bytes:
        return bytes(self._buf)

    @property
    def size(self) -> int:
        return len(self._buf)

    def write_bytes(self, data: bytes):
        self._buf += data

    def write_u8(self, value: int):
        self._buf.append(value & 0xFF)

    def write_u16(self, value: int):
        self._buf += _U16.pack(value & 0xFFFF)

    def write_u32(self, value: int):
        self._buf += _U32.pack(value & 0xFFFFFFFF)

    def write_position(self, x: int, y: int, z: int):
        self._buf += _POSITION.pack(x & 0xFFFF, y & 0xFFFF, z & 0xFF)

    def write_string(self, text: str):
        raw = text.encode("latin-1")
        self.write_u16(len(raw))
        self._buf += raw


_WIDTH = {"B": 0xFF, "H": 0xFFFF, "I": 0xFFFFFFFF}


def _packet(opcode, layout="", *values) -> bytes:
    """Opcode byte, then ``values`` packed by a struct ``layout`` and cut to their width."""
    return struct.pack("<B" + layout, opcode, *(v & _WIDTH[c] for c, v in zip(layout, values)))


# ── packets the bot sends ─────────────────────────────────────────

def build_walk_packet(direction: Direction) -> bytes:
    return _packet(WALK_OPCODES[direction])


def build_turn_packet(direction: Direction) -> bytes:
    """Face north, east, south or west without moving."""
    return _packet(TURN_OPCODES[direction])


def build_stop_walk_packet() -> bytes:
    return _packet(ClientOpcode.STOP_WALK)


def build_ping_packet() -> bytes:
    return _packet(ClientOpcode.PING)


def build_attack_packet(creature_id: int) -> bytes:
    """Attack a creature; 0 stops attacking."""
    return _packet(ClientOpcode.ATTACK, "I", creature_id)


def build_follow_packet(creature_id: int) -> bytes:
    return _packet(ClientOpcode.FOLLOW, "I", creature_id)


def build_say_packet(text: str, mode: int = 1) -> bytes:
    """Speak ``text``; mode 1 is normal speech (spells are spoken too)."""
    writer = PacketWriter()
    writer.write_u8(ClientOpcode.SAY)
    writer.write_u8(mode)
    writer.write_string(text)
    return writer.data


def build_set_fight_modes_packet(fight_mode: int = 1, chase_mode: int = 0, safe_mode: int = 1) -> bytes:
    """fight: 1 offensive, 2 balanced, 3 defensive; chase: 0 stand, 1 chase; safe: 0 PvP, 1 safe."""
    return _packet(ClientOpcode.SET_FIGHT_MODES, "BBB", fight_mode, chase_mode, safe_mode)


def build_use_item_packet(x: int, y: int, z: int, item_id: int, stack_pos: int, index: int) -> bytes:
    """Use an item. Position (0xFFFF, 0, 0) is the hotkey form: the server finds the item."""
    return _packet(ClientOpcode.USE_ITEM, "HHBHBB", x, y, z, item_id, stack_pos, index)


def build_use_on_creature_packet(x: int, y: int, z: int, item_id: int, stack_pos: int, creature_id: int) -> bytes:
    """Use an item on a creature, e.g. an attack rune on a monster."""
    return _packet(ClientOpcode.USE_ON_CREATURE, "HHBHBI", x, y, z, item_id, stack_pos, creature_id)


def build_use_item_ex_packet(
    from_x: int, from_y: int, from_z: int,
    item_id: int, stack_pos: int,
    to_x: int, to_y: int, to_z: int, to_stack_pos: int, *, target_item_id: int | None = None,
) -> bytes:
    """Use an item on a tile or a thing, e.g. a rope on a hole or a shovel on the ground.

    ``target_item_id`` adds the target thing's item id, which this server
    expects between the target position and its stack position.
    """
    head = _packet(ClientOpcode.USE_ITEM_EX, "HHBHBHHB", from_x, from_y, from_z, item_id, stack_pos,
                   to_x, to_y, to_z)
    middle = b"" if target_item_id is None else _U16.pack(target_item_id & 0xFFFF)
    return head + middle + bytes([to_stack_pos & 0xFF])


def build_move_item_packet(from_pos: tuple, item_id: int, from_stack: int, to_pos: tuple, count: int,
                           *, count_bytes=1) -> bytes:
    """Move ``count`` of an item; ``count_bytes`` is 1 or 2 depending on the item kind."""
    if count_bytes not in (1, 2):
        raise ValueError("Nieobsługiwany format liczby sztuk.")
    return _packet(ClientOpcode.MOVE_THING, "HHBHBHHB" + ("B" if count_bytes == 1 else "H"),
                   *from_pos, item_id, from_stack, *to_pos, count)


def build_look_packet(x: int, y: int, z: int, item_id: int, stack_pos: int) -> bytes:
    return _packet(ClientOpcode.LOOK, "HHBHB", x, y, z, item_id, stack_pos)
