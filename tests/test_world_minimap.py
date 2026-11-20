import base64
import sqlite3
import struct
import zlib

import pytest

from world_minimap import world_floor


def write_map(path, flags=1):
    header = struct.pack('<4sHHI', b'OTMM', 12, 1, 0)
    blocks = []
    for floor in (7, 8):
        raw = bytearray(64 * 64 * 3)
        start = (4 * 64 + 3) * 3
        raw[start:start+3] = bytes((flags, floor, 10))
        packed = zlib.compress(raw)
        blocks.append(struct.pack('<HHBH', 640, 384, floor, len(packed)) + packed)
    path.write_bytes(header + b''.join(blocks) + b'\xff' * 5)


def png_rows(encoded):
    data = base64.b64decode(encoded)
    pos = 8
    assert data[:8] == b'\x89PNG\r\n\x1a\n'
    while pos < len(data):
        length, name = struct.unpack_from('>I4s', data, pos)
        if name == b'IDAT':
            return zlib.decompress(data[pos+8:pos+8+length])
        pos += length + 12
    raise AssertionError('missing image data')


def test_overlays_share_game_coordinates_and_floor(tmp_path):
    path, atlas = tmp_path/'minimap.otmm', tmp_path/'atlas.sqlite3'
    write_map(path)
    with sqlite3.connect(atlas) as db:
        db.execute('CREATE TABLE tiles(x,y,z)')
        db.executemany('INSERT INTO tiles VALUES(?,?,?)', [(643,388,7), (707,388,7)])
    result = world_floor(path, atlas, 7)
    assert result['floors'] == [7, 8]
    land, extra = result['sectors']
    assert (land['x'], land['y']) == (640, 384)
    pixels = png_rows(land['terrain'])
    assert pixels[4*65+1+3] == 8 and sum(pixels) == 8
    mask = png_rows(land['atlas'])
    assert mask[4*9+1] == 0x10 and sum(mask) == 0x10
    assert (extra['x'],extra['y']) == (704,384) and 'terrain' not in extra
    other, = world_floor(path, atlas, 8)['sectors']
    assert 'atlas' not in other and png_rows(other['terrain'])[4*65+1+3] == 9
    assert world_floor(path, atlas, 0)['sectors'] == []


def test_partial_minimap_can_be_retried(tmp_path):
    path = tmp_path/'minimap.otmm'
    write_map(path)
    path.write_bytes(path.read_bytes()[:-3])
    with pytest.raises(ValueError):
        world_floor(path, None, 7)
    write_map(path, flags=0)
    assert world_floor(path, None, 7)['sectors'] == []


def test_populated_floor_spans_multiple_database_chunks(tmp_path):
    path, atlas = tmp_path/'minimap.otmm', tmp_path/'atlas.sqlite3'
    write_map(path)
    with sqlite3.connect(atlas) as db:
        db.execute('CREATE TABLE tiles(x,y,z,PRIMARY KEY(z,x,y))')
        db.executemany('INSERT INTO tiles VALUES(?,?,7)', ((x,y) for x in range(65) for y in range(64)))
    masks = {(s['x'],s['y']):png_rows(s['atlas']) for s in world_floor(path,atlas,7)['sectors'] if 'atlas' in s}
    assert set(masks) == {(0,0),(64,0)}
    assert sum(int(v).bit_count() for mask in masks.values() for v in mask) == 65*64
