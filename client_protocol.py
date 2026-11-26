"""Read the pinned client's protocol key; never write, log, or persist it."""
import struct

from client_profiles import NOVEMBER_26
from passive_map import MapReadError

PROTOCOL_VTABLE = 0x8BE5FC

# Reviewed Lua-bound Game::getProtocolGame, Protocol::setXteaKey and
# Protocol::enableXteaEncryption in the af433830 executable.
SIGNATURES = {
    0xBBD67: bytes.fromhex('8b 4a 24 89 08 8b 4a 28'),
    0x3ECC40: bytes.fromhex('55 8b ec 8b 45 08 89 41 18 8b 45 0c 89 41 1c 8b 45 10 89 41 20 8b 45 14 89 41 24 5d c2 10 00'),
    0x1C9A10: bytes.fromhex('c6 41 3e 01 c3'),
}


def read_protocol_key(source, expected_pid):
    if source.profile != NOVEMBER_26 or source.pid != expected_pid:
        raise MapReadError('Protocol key source does not match the selected client')
    for rva, code in SIGNATURES.items():
        if source.read(source.base+rva, len(code)) != code:
            raise MapReadError('Protocol accessor signature mismatch')
    address = source.base + source.profile.player_layout.game_singleton + 0x24
    link = source.read(address, 8)
    ptr, control = struct.unpack('<II', link)
    if not ptr or not control:
        raise MapReadError('No active client protocol')
    identity = source.read(ptr, 4)
    if struct.unpack('<I', identity)[0] != source.base + PROTOCOL_VTABLE:
        raise MapReadError('Unexpected client protocol type')
    if source.read(ptr+0x3E, 1) != b'\x01':
        raise MapReadError('Client protocol encryption is not enabled')
    secret = source.read(ptr+0x18, 16)
    if (not any(secret) or source.read(ptr+0x18, 16) != secret
            or source.read(ptr+0x3E, 1) != b'\x01'
            or source.read(ptr, 4) != identity or source.read(address, 8) != link):
        raise MapReadError('Client protocol changed during key read')
    return struct.unpack('<4I', secret)


def read_live_protocol_key(expected_pid):
    from passive_map_win32 import ReadOnlyClient
    source = ReadOnlyClient()
    try:
        return read_protocol_key(source, expected_pid)
    finally:
        source.close()
