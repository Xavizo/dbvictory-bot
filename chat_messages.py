"""Read TALK at a known protocol boundary, never search opaque map bytes.

Common OTClient header: statement U32, name string, level U16, mode U8.
Modes 1/2/3 are local speech, 4 is incoming private speech. Those values are
shared by the 8.6 and modern OT protocols. No assumption about other modes.
DBV live chat still needs a manual acceptance test; unsupported payloads stop
the sequential scan rather than consuming an invented message length.
Reference: edubart/otclient src/client/protocolgameparse.cpp parseTalk.
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
        if mode in (1, 2, 3):
            x, y, z = struct.unpack_from('<HHB', data, pos)
            if not (0 < x < 65535 and 0 < y < 65535 and z < 16):
                return None
            pos += 5
            kind = 'default'
        elif mode == 4:
            kind = 'private'
        else:
            return None
        message, end = text(pos, 4096)
        return end, {'statement': statement, 'sender': sender, 'kind': kind, 'text': message[:512]}
    except (struct.error, IndexError, ValueError):
        return None
