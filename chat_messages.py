"""Read TALK at a known protocol boundary, never search opaque map bytes.

DBV header: statement U32, name string, level U16, wire mode U8.
The client's MessageModes enum is NOT its wire encoding. In the pinned client
8c15e00a...a54f5e8, parseTalk RVA 1A99xx and converter RVA 19ABD0 use a mapping
table: PrivateFrom=6, GM private=14, NPC=4/5. Verified read-only on 2026-11-17.
Known non-alert speech is consumed so it cannot hide following private speech.
Unsupported custom modes stop parsing; no byte searching through opaque data.
"""
import struct


def parse_talk(data, pos):
    def text(offset, limit):
        length = struct.unpack_from('<H', data, offset)[0]
        start, end = offset + 2, offset + 2 + length
        if not 0 < length <= limit or end > len(data):
            raise ValueError()
        raw = data[start:end]
        if any(c < 32 or c == 127 for c in raw):
            raise ValueError()
        try:
            value = raw.decode('utf-8')
        except UnicodeDecodeError:
            value = raw.decode('cp1250', errors='replace')
        return value, end
    try:
        statement = struct.unpack_from('<I', data, pos)[0]
        sender, pos = text(pos+4, 64)
        level, mode = struct.unpack_from('<HB', data, pos)
        pos += 3
        kind = None
        if mode in (1, 2, 3, 4, 5, 19, 20, 28):
            x, y, z = struct.unpack_from('<HHB', data, pos)
            if not (0 < x < 65535 and 0 < y < 65535 and z < 16):
                return None
            pos += 5
            if mode in (1, 2, 3):
                kind = 'default'
        elif mode in (6, 14):
            kind = 'private'
        elif mode in (7, 8, 13, 15):
            struct.unpack_from('<H', data, pos)  # channel ID
            pos += 2
        elif mode == 9:
            struct.unpack_from('<I', data, pos)  # RVR channel timestamp
            pos += 4
        elif mode in (10, 11, 12):
            pass  # RVR replies and broadcast; no alert category
        else:
            return None
        message, end = text(pos, 4096)
        return end, {'statement': statement, 'sender': sender, 'kind': kind, 'text': message[:512]}
    except (struct.error, IndexError, ValueError):
        return None
