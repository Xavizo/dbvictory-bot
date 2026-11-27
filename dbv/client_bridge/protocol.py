"""Open Tibia game protocol as DBVictory speaks it: opcodes, a reader and a
writer for packet bodies, and builders for every packet the bot sends.

All numbers are little-endian. A position is x:u16, y:u16, z:u8 and a string
is a u16 length followed by Latin-1 bytes. Opcodes are listed by value.
"""
import struct
import enum


class ClientOpcode(enum.IntEnum):
    """Client → server, grouped by what they do."""
    # session
    LOGIN_SERVER, GAME_LOGIN, LOGOUT = 0x01, 0x0A, 0x14   # account login, character login
    PING, PONG = 0x1E, 0x1F
    # movement: 0x65..0x68 are N/E/S/W, the diagonals follow STOP_WALK
    NEW_WALK, AUTO_WALK = 0x45, 0x64      # OTCv8 "new walking" step; a path of directions
    WALK_NORTH, WALK_EAST, WALK_SOUTH, WALK_WEST, STOP_WALK = 0x65, 0x66, 0x67, 0x68, 0x69
    WALK_NORTHEAST, WALK_SOUTHEAST, WALK_SOUTHWEST, WALK_NORTHWEST = 0x6A, 0x6B, 0x6C, 0x6D
    TURN_NORTH, TURN_EAST, TURN_SOUTH, TURN_WEST = 0x6F, 0x70, 0x71, 0x72
    # items and trade
    MOVE_THING = 0x78                     # drag an item: from, item, stack, to, count
    BUY_ITEM, SELL_ITEM, CLOSE_NPC_TRADE = 0x7A, 0x7B, 0x7C          # NPC trade
    REQUEST_TRADE, ACCEPT_TRADE, REJECT_TRADE = 0x7D, 0x7F, 0x80     # player trade
    USE_ITEM, USE_ITEM_EX, USE_ON_CREATURE = 0x82, 0x83, 0x84        # on itself, on a thing, on a creature
    CLOSE_CONTAINER, UP_CONTAINER = 0x87, 0x88                       # UP opens the parent container
    LOOK, LOOK_IN_TRADE = 0x8C, 0x8E
    BROWSE_FIELD, SEEK_IN_CONTAINER = 0xCB, 0xCC                     # BROWSE lists a tile's items
    # talking
    SAY = 0x96                            # speak mode, text
    OPEN_CHANNEL, CLOSE_CHANNEL, OPEN_PRIVATE_CHANNEL = 0x98, 0x99, 0x9A
    # combat
    SET_FIGHT_MODES, ATTACK, FOLLOW = 0xA0, 0xA1, 0xA2   # ATTACK takes a creature id, 0 cancels
    CANCEL_ATTACK = 0xBE
    # character and social
    SET_OUTFIT = 0xD3                     # change appearance
    ADD_VIP, REMOVE_VIP = 0xDC, 0xDD
    REQUEST_QUEST_LOG, REQUEST_QUEST_LINE = 0xF0, 0xF1


class ServerOpcode(enum.IntEnum):
    """Server → client, grouped by what they carry."""
    # login and session
    LOGIN_OR_PENDING = 0x0A               # player id, then the first map description
    GM_ACTIONS, UPDATE_NEEDED, LOGIN_TOKEN = 0x0B, 0x0C, 0x0D
    LOGIN_ERROR, LOGIN_ADVISE, LOGIN_WAIT, LOGIN_SUCCESS = 0x14, 0x15, 0x16, 0x17
    PING, PONG, CHALLENGE = 0x1D, 0x1E, 0x1F
    # map: a full view, then one new row or column after each step
    MAP_DESCRIPTION = 0x64
    MAP_SLICE_NORTH, MAP_SLICE_EAST, MAP_SLICE_SOUTH, MAP_SLICE_WEST = 0x65, 0x66, 0x67, 0x68
    TILE_ADD_THING, TILE_TRANSFORM_THING, TILE_REMOVE_THING, CREATURE_MOVE = 0x6A, 0x6B, 0x6C, 0x6D
    FLOOR_CHANGE_UP, FLOOR_CHANGE_DOWN = 0xBE, 0xBF
    # containers (in this protocol 0x6E opens a container; it is not a turn)
    OPEN_CONTAINER, CLOSE_CONTAINER = 0x6E, 0x6F
    CREATE_IN_CONTAINER, CHANGE_IN_CONTAINER, REMOVE_FROM_CONTAINER = 0x70, 0x71, 0x72
    # effects and creatures
    MAGIC_EFFECT, ANIMATED_TEXT, SHOOT_EFFECT = 0x83, 0x84, 0x85
    CREATURE_HEALTH, CREATURE_LIGHT, CREATURE_OUTFIT = 0x8C, 0x8D, 0x8E
    CREATURE_SPEED, CREATURE_SKULL, CREATURE_PARTY = 0x8F, 0x90, 0x91
    # the player
    PLAYER_STATS, PLAYER_SKILLS, PLAYER_ICONS, PLAYER_CANCEL_ATTACK = 0xA0, 0xA1, 0xA2, 0xA3
    PLAYER_CANCEL_WALK = 0xB5             # the server refused a step
    # chat
    TALK, CHANNELS, OPEN_CHANNEL, PRIVATE_CHANNEL = 0xAA, 0xAB, 0xAC, 0xAD
    CLOSE_CHANNEL, TEXT_MESSAGE = 0xB2, 0xB4
    VIP_ADD, VIP_STATE, VIP_LOGOUT = 0xD2, 0xD3, 0xD4


class Direction(enum.IntEnum):
    """The client's own direction numbers."""
    NORTH, EAST, SOUTH, WEST = 0, 1, 2, 3
    NORTHEAST, SOUTHEAST, SOUTHWEST, NORTHWEST = 4, 5, 6, 7


# One step in a direction, by opcode. Diagonals sit after STOP_WALK (0x69).
WALK_OPCODES = {d: ClientOpcode(0x65 + d if d < 4 else 0x66 + d) for d in Direction}
TURN_OPCODES = {d: ClientOpcode(0x6F + d) for d in Direction if d < 4}

_U16, _U32 = struct.Struct("<H"), struct.Struct("<I")
_POSITION = struct.Struct("<HHB")


def _fixed(layout, first_only=True):
    """A reader method for one struct layout; the cursor moves past it."""
    def read(self):
        values = layout.unpack_from(self._data, self._pos)
        self._pos += layout.size
        return values[0] if first_only else values
    return read


class PacketReader:  # a cursor over one packet body
    """Reads a packet body front to back.

    Running past the end raises IndexError (bytes, strings, u8) or
    struct.error (u16, u32, positions), the same errors callers already handle.
    """

    def __init__(self, data: bytes, start: int = 0):
        self._data, self._pos = data, start

    position = property(lambda self: self._pos, doc="Offset of the next unread byte.")
    remaining = property(lambda self: len(self._data) - self._pos, doc="Bytes not read yet.")
    read_u16 = _fixed(_U16)
    read_u32 = _fixed(_U32)
    read_position = _fixed(_POSITION, first_only=False)   # (x, y, z)

    def read_u8(self):
        byte = self._data[self._pos]          # IndexError at the end, like bytes
        self._pos = self._pos + 1
        return byte

    def read_bytes(self, count: int):
        if count > self.remaining:
            raise IndexError(f"need {count} bytes at offset {self._pos}, only {self.remaining} left")
        start, self._pos = self._pos, self._pos + count
        return self._data[start:self._pos]

    def read_string(self):
        """A u16 length, then that many Latin-1 bytes."""
        length = self.read_u16()
        if length > self.remaining:
            raise IndexError(f"string of {length} bytes, only {self.remaining} left")
        return self.read_bytes(length).decode("latin-1")

    def peek_remaining(self):
        """The unread rest, without consuming it."""
        return self._data[self.position:]


class PacketWriter:  # appends to one packet body
    """Builds a packet body. Integers are truncated to their width."""

    def __init__(self) -> None:
        self._out = bytearray()

    data = property(lambda self: bytes(self._out), doc="Everything written so far.")
    size = property(lambda self: len(self._out), doc="Bytes written so far.")

    def write_bytes(self, raw: bytes) -> None:
        self._out.extend(raw)

    def write_u8(self, number: int) -> None:
        self._out.append(number % 0x100)

    def write_u16(self, number: int) -> None:
        self._out.extend(_U16.pack(number % 0x10000))

    def write_u32(self, number: int) -> None:
        self._out.extend(_U32.pack(number % 0x100000000))

    def write_position(self, x: int, y: int, z: int) -> None:
        self._out.extend(_POSITION.pack(x % 0x10000, y % 0x10000, z % 0x100))

    def write_string(self, text: str) -> None:
        encoded = text.encode("latin-1")
        self.write_u16(len(encoded))
        self.write_bytes(encoded)


Packet = bytes   # an encoded packet: the opcode byte, then its fields

_WIDTH = {"B": 0xFF, "H": 0xFFFF, "I": 0xFFFFFFFF}


def _packet(opcode, layout="", *values) -> bytes:
    """Opcode byte, then ``values`` packed by a struct ``layout`` and cut to their width."""
    return struct.pack("<B" + layout, opcode, *(v & _WIDTH[c] for c, v in zip(layout, values)))


# ── packets the bot sends ─────────────────────────────────────────

def build_walk_packet(direction: Direction) -> Packet:
    return _packet(WALK_OPCODES[direction])


def build_turn_packet(direction: Direction) -> Packet:
    """Face north, east, south or west without moving."""
    return _packet(TURN_OPCODES[direction])


def build_stop_walk_packet() -> Packet:
    return _packet(ClientOpcode.STOP_WALK)


def build_ping_packet() -> Packet:
    return _packet(ClientOpcode.PING)


def build_logout_packet() -> Packet:
    return _packet(ClientOpcode.LOGOUT)


def build_attack_packet(creature_id: int) -> Packet:
    """Attack a creature; 0 stops attacking."""
    return _packet(ClientOpcode.ATTACK, "I", creature_id)


def build_follow_packet(creature_id: int) -> Packet:
    return _packet(ClientOpcode.FOLLOW, "I", creature_id)


def build_say_packet(text: str, mode: int = 1) -> Packet:
    """Speak ``text``; mode 1 is normal speech (spells are spoken too)."""
    writer = PacketWriter()
    writer.write_u8(ClientOpcode.SAY)
    writer.write_u8(mode)
    writer.write_string(text)
    return writer.data


def build_set_fight_modes_packet(fight_mode: int = 1, chase_mode: int = 0, safe_mode: int = 1) -> Packet:
    """fight: 1 offensive, 2 balanced, 3 defensive; chase: 0 stand, 1 chase; safe: 0 PvP, 1 safe."""
    return _packet(ClientOpcode.SET_FIGHT_MODES, "BBB", fight_mode, chase_mode, safe_mode)


def build_use_item_packet(x: int, y: int, z: int, item_id: int, stack_pos: int, index: int) -> Packet:
    """Use an item. Position (0xFFFF, 0, 0) is the hotkey form: the server finds the item."""
    return _packet(ClientOpcode.USE_ITEM, "HHBHBB", x, y, z, item_id, stack_pos, index)


def build_use_on_creature_packet(x: int, y: int, z: int, item_id: int, stack_pos: int, creature_id: int) -> Packet:
    """Use an item on a creature, e.g. an attack rune on a monster."""
    return _packet(ClientOpcode.USE_ON_CREATURE, "HHBHBI", x, y, z, item_id, stack_pos, creature_id)


def build_use_item_ex_packet(from_x: int, from_y: int, from_z: int, item_id: int, stack_pos: int,
                             to_x: int, to_y: int, to_z: int, to_stack_pos: int,
                             *, target_item_id: int | None = None) -> Packet:
    """Use an item on a tile or a thing, e.g. a rope on a hole or a shovel on the ground.

    ``target_item_id`` adds the target thing's item id, which this server
    expects between the target position and its stack position.
    """
    head = _packet(ClientOpcode.USE_ITEM_EX, "HHBHBHHB", from_x, from_y, from_z, item_id, stack_pos,
                   to_x, to_y, to_z)
    middle = b"" if target_item_id is None else _U16.pack(target_item_id & 0xFFFF)
    return head + middle + bytes([to_stack_pos & 0xFF])


def build_move_item_packet(from_pos: tuple, item_id: int, from_stack: int, to_pos: tuple, count: int,
                           *, count_bytes=1) -> Packet:
    """Move ``count`` of an item; ``count_bytes`` is 1 or 2 depending on the item kind."""
    if count_bytes not in (1, 2):
        raise ValueError("Nieobsługiwany format liczby sztuk.")
    return _packet(ClientOpcode.MOVE_THING, "HHBHBHHB" + ("B" if count_bytes == 1 else "H"),
                   *from_pos, item_id, from_stack, *to_pos, count)


def build_look_packet(x: int, y: int, z: int, item_id: int, stack_pos: int) -> Packet:
    return _packet(ClientOpcode.LOOK, "HHBHB", x, y, z, item_id, stack_pos)
