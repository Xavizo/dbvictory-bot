"""The injector finds LoadLibraryW in a 32-bit kernel32 image without running it."""
import struct

import pytest

from dbv.client_bridge.injector import InjectionError, export_rva


def pe_image(exports, *, magic=0x10B, section_rva=0x1000, raw=0x400):
    """A minimal PE file with one section holding an export table."""
    names = sorted(exports)
    table = bytearray(40)
    strings = bytearray()
    name_offsets = []
    for name in names:
        name_offsets.append(len(strings))
        strings += name.encode() + b"\0"
    functions_at, names_at = 40, 40 + 4 * len(names)
    ordinals_at = names_at + 4 * len(names)
    strings_at = ordinals_at + 2 * len(names)
    body = bytearray(strings_at) + strings
    struct.pack_into("<IIII", body, 24, len(names), section_rva + functions_at,
                     section_rva + names_at, section_rva + ordinals_at)
    for i, name in enumerate(names):
        struct.pack_into("<I", body, functions_at + 4 * i, exports[name])
        struct.pack_into("<I", body, names_at + 4 * i, section_rva + strings_at + name_offsets[i])
        struct.pack_into("<H", body, ordinals_at + 2 * i, i)
    optional_size = 224 if magic == 0x10B else 240
    pe = 0x80
    image = bytearray(raw) + body
    image[:2] = b"MZ"
    struct.pack_into("<I", image, 0x3C, pe)
    image[pe:pe + 4] = b"PE\0\0"
    struct.pack_into("<HH", image, pe + 4, 0x14C, 1)
    struct.pack_into("<H", image, pe + 20, optional_size)
    optional = pe + 24
    struct.pack_into("<H", image, optional, magic)
    directories = optional + (96 if magic == 0x10B else 112)
    struct.pack_into("<II", image, directories, section_rva, len(body))
    section = optional + optional_size
    image[section:section + 8] = b".edata\0\0"
    struct.pack_into("<IIII", image, section + 8, len(body), section_rva, len(body), raw)
    return bytes(image)


@pytest.mark.parametrize("magic", [0x10B, 0x20B])
def test_export_offset_is_read_from_the_table(magic):
    image = pe_image({"LoadLibraryA": 0x1F00, "LoadLibraryW": 0x2A40, "Sleep": 0x77}, magic=magic)
    assert export_rva(image, "LoadLibraryW") == 0x2A40
    assert export_rva(image, "Sleep") == 0x77


def test_missing_export_or_damaged_file_is_an_error():
    image = pe_image({"LoadLibraryA": 0x1F00})
    with pytest.raises(InjectionError, match="not exported"):
        export_rva(image, "LoadLibraryW")
    with pytest.raises(InjectionError):
        export_rva(b"not a pe", "LoadLibraryW")
    with pytest.raises(InjectionError):
        export_rva(image[:0x90], "LoadLibraryW")


def test_injection_refuses_a_missing_dll(tmp_path):
    from dbv.client_bridge import injector
    with pytest.raises(InjectionError, match="not found"):
        injector.inject(tmp_path / "missing.dll")
