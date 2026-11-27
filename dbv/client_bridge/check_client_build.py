"""Offline executable/DAT/DLL verification. Never opens a process or a socket.

Unknown hashes fail; this tool does not discover or approve new profiles.
The November 26 checks preserve the static evidence behind its layout.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct

from dbv.client_bridge.client_profiles import NOVEMBER_26, select_profile


class Image:
    def __init__(self, data):
        self.data = data
        if data[:2] != b'MZ':
            raise ValueError('Missing PE header')
        pe = struct.unpack_from('<I', data, 0x3c)[0]
        if data[pe:pe+4] != b'PE\0\0':
            raise ValueError('Missing PE signature')
        machine, count = struct.unpack_from('<HH', data, pe+4)
        opt_size = struct.unpack_from('<H', data, pe+20)[0]
        if machine != 0x14c or struct.unpack_from('<H', data, pe+24)[0] != 0x10b:
            raise ValueError('Expected a 32-bit x86 PE')
        self.base = struct.unpack_from('<I', data, pe+24+28)[0]
        self.sections = []
        for i in range(count):
            offset = pe+24+opt_size+i*40
            _, rva, size, raw = struct.unpack_from('<4I', data, offset+8)
            if raw+size > len(data):
                raise ValueError('Truncated PE section')
            self.sections.append((rva, size, raw))

    def read(self, rva, size):
        for start, length, raw in self.sections:
            if start <= rva and rva+size <= start+length:
                return self.data[raw+rva-start:raw+rva-start+size]
        raise ValueError(f'Unbacked RVA {rva:x}')

    def pointer(self, rva):
        return struct.unpack('<I', self.read(rva,4))[0] - self.base


def verify(path, dll=None):
    path = Path(path)
    if path.stat().st_size > 64*1024*1024:
        raise ValueError('Executable exceeds the supported size')
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    profile = select_profile(digest)
    image = Image(data)
    checks = 0

    def expect(rva, expected):
        nonlocal checks
        if image.read(rva,len(expected)) != expected:
            raise ValueError(f'Static signature mismatch at RVA {rva:x}')
        checks += 1

    if profile == NOVEMBER_26:
        from dbv.client_bridge.client_protocol import SIGNATURES, PROTOCOL_VTABLE
        signatures = {
            0xBAEC0: '66 8b 81 d4 00 00 00 c3',  # speed: U16 +D4
            0xBB310: '8b 81 48 05 00 00 c3',     # conditions: U32 +548
            0xBB3C0: 'dd 81 58 05 00 00 c3',     # health: double +558
            0xBB3D0: 'dd 81 60 05 00 00 c3',     # max health: double +560
            0x73B40: 'dd 81 90 05 00 00 c3',     # mana: double +590
            0xBB410: 'dd 81 80 05 00 00 c3',     # level: double +580
            0xBB430: 'dd 81 a0 05 00 00 c3',     # magic: double +5A0
            0x737D0: '8b 41 58 c3',             # direction: U32 +58
            0x6D3E0: '8b 41 38 c3',             # creature ID: U32 +38
            0xBAE30: '8a 41 54 c3',             # creature health: BYTE +54
            0xADB60: '0f b7 41 35 c3',          # item ID: U16 +35
            0x6D6C0: 'b0 01 c3',                # isCreature constant true
            0xBB4E0: '8b 8a 88 04 00 00',       # inventory control +488
            0xBB4EE: '8b 8a 84 04 00 00',       # inventory object +484
            0xBB94C: '8b 46 2c',                # Game container map +2C
            0xBCA16: 'f3 0f 7e 81 ac 01 00 00', # Map center +1AC
            0xBF2B8: '68 60 9e f7 00',          # g_map binding
            0xBDD9E: '68 b0 a4 f7 00',          # g_things binding
            0xC3D54: '68 50 9b f7 00',          # g_game binding
            0xBA920: '55 8b ec 8b 55 08 c6 81 5d 01 00 00 01 8b 02 89 81 3c 01 00 00 8b 42 04 89 81 40 01 00 00 8b 42 08 89 81 44 01 00 00 8b 42 0c 89 81 48 01 00 00 5d c2 04 00',
            0xBA960: 'c6 81 5d 01 00 00 00 c3',
            0x405455: 'ff 15 a4 e3 ba 00',
            0x405480: 'ff 15 a4 e3 ba 00',
        }
        for rva, expected in signatures.items():
            expect(rva,bytes.fromhex(expected))
        for rva, expected in SIGNATURES.items():
            expect(rva, expected)
        # Follow PE RTTI from vtable[-1] -> COL -> TypeDescriptor, not guessed shifts.
        types = [(profile.tile_vtable,'Tile'),(profile.item_vtable,'Item'),
                 (profile.type_vtable,'ThingType'),(PROTOCOL_VTABLE,'ProtocolGame')]
        names = {'self':'LocalPlayer','player':'Player','monster':'Monster','npc':'Npc'}
        types += [(rva,names[kind]) for rva,kind in profile.creature_vtables]
        for vtable, name in types:
            col = image.pointer(vtable-4)
            descriptor = image.pointer(col+12)
            expect(descriptor+8, f'.?AV{name}@@\0'.encode('ascii'))
        col = image.pointer(profile.u16_attribute_vtable-4)
        expect(image.pointer(col+12)+8,b'.?AU?$holder@G@any@stdext@@\0')

    asset = path.parent / 'bytes' / 'things' / 'file.dat'
    if asset.stat().st_size > 32*1024*1024:
        raise ValueError('DAT exceeds the supported size')
    if hashlib.sha256(asset.read_bytes()).hexdigest() != profile.dat_sha256:
        raise ValueError('DAT does not match the selected profile')
    if dll:
        native = Path(dll).read_bytes()
        Image(native)
        if not profile.observer_pipe or profile.observer_pipe+b'\0' not in native:
            raise ValueError('DLL does not contain the selected profile pipe')
    return {'sha256':digest,'architecture':'x86','static_checks':checks,
            'dat_matches':True,'dll_checked':bool(dll),'live_tested':False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('client', type=Path)
    parser.add_argument('--dll', type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(verify(args.client,args.dll),indent=2))
    except (ValueError,OSError,struct.error) as exc:
        parser.exit(1,f'Client check failed: {exc}\n')
