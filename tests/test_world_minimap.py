import base64
import os
import sqlite3
import struct
import zlib

import pytest

from dbv.brain.navigation.world_minimap import world_floor
from dbv.brain.navigation.map_atlas import MapAtlas


def write_map(path, flags=1):
    header = struct.pack('<4sHHI', b'OTMM', 12, 1, 0)
    blocks = []
    for floor in (7, 8):
        raw = bytearray((0, 255, 0)) * (64 * 64)
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


def test_world_floor_poll_detects_new_atlas_pixels_and_skips_unchanged_image(tmp_path):
    path, atlas = tmp_path/'map.otmm', tmp_path/'atlas.sqlite3'
    write_map(path)
    with sqlite3.connect(atlas) as db:
        db.execute('CREATE TABLE tiles(x,y,z)')
        db.execute('INSERT INTO tiles VALUES(643,388,7)')
    first = world_floor(path, atlas, 7, tile_count=1)
    assert world_floor(path, atlas, 7, since=first['revision'], tile_count=1) == {
        'floor':7, 'revision':first['revision']}
    with sqlite3.connect(atlas) as db:
        db.execute('INSERT INTO tiles VALUES(707,388,7)')
    updated = world_floor(path, atlas, 7, since=first['revision'], tile_count=2)
    with sqlite3.connect(atlas) as db:
        db.execute('INSERT INTO tiles VALUES(707,388,8)')
    assert 'sectors' not in world_floor(path, atlas, 7, since=updated['revision'], tile_count=3)
    assert updated['revision'] != first['revision']
    assert [(s['x'],s['y']) for s in updated['sectors'] if s.get('atlas')] == [(640,384),(704,384)]


def test_partial_minimap_can_be_retried(tmp_path):
    path = tmp_path/'minimap.otmm'
    write_map(path)
    path.write_bytes(path.read_bytes()[:-3])
    with pytest.raises(ValueError):
        world_floor(path, None, 7)
    write_map(path, flags=0)
    sector, = world_floor(path, None, 7)['sectors']
    assert png_rows(sector['terrain'])[4*65+1+3] == 8


def test_live_colors_refresh_on_same_floor_without_file_save_or_tile_count_change(tmp_path):
    path = tmp_path/'minimap.otmm'
    write_map(path)
    os.utime(path, (1, 1))
    atlas = MapAtlas(tmp_path, "colors")

    def observe(x, z, items, **source):
        atlas.observe(dict(status="ready", cells=[dict(x=x, y=388, z=z, kind="clear", items=[
            dict(id=300+n, stack=n, flags=[flag], minimap_color=color)
            for n, (flag, color) in enumerate(items)])], **source))

    # The same coordinates were known before the client supplied their colors.
    observe(643, 7, [])
    observe(644, 7, [])
    def read(since=None):
        return world_floor(path, atlas.path, 7, since=since, tile_count=atlas.total)
    first = read()
    observe(643, 7, [('ground', 129), ('ground_border', 24), ('on_bottom', 210), ('container', 45)])
    observe(644, 7, [('ground', 129), ('on_top', 0)])
    newer = read(first['revision'])
    assert atlas.total == 2
    assert png_rows(newer['sectors'][0]['terrain'])[4*65+4:4*65+6] == bytes((211, 130))
    observe(644, 7, [('ground', 129)])
    observe(644, 7, [('ground', 80)], stale=True)
    observe(644, 7, [('ground', 81)], preview=True)
    observe(644, 8, [('ground', 88)])
    assert read(newer['revision']) == dict(floor=7, revision=newer['revision'])
    # The next on-disk save is authoritative at colored pixels, while
    # observations still fill genuinely blank pixels on that floor.
    os.utime(path, (4000000000, 4000000000))
    saved = read(newer['revision'])
    assert png_rows(saved['sectors'][0]['terrain'])[4*65+4:4*65+6] == bytes((8, 130))
    path.unlink()
    reopened = MapAtlas(tmp_path, "colors")
    offline = world_floor(path, reopened.path, 7, tile_count=reopened.total)
    assert png_rows(offline['sectors'][0]['terrain'])[4*65+4:4*65+6] == bytes((211, 130))


def test_populated_floor_spans_multiple_database_chunks(tmp_path):
    path, atlas = tmp_path/'minimap.otmm', tmp_path/'atlas.sqlite3'
    write_map(path)
    with sqlite3.connect(atlas) as db:
        db.execute('CREATE TABLE tiles(x,y,z,PRIMARY KEY(z,x,y))')
        db.executemany('INSERT INTO tiles VALUES(?,?,7)', ((x,y) for x in range(65) for y in range(64)))
    masks = {(s['x'],s['y']):png_rows(s['atlas']) for s in world_floor(path,atlas,7)['sectors'] if 'atlas' in s}
    assert set(masks) == {(0,0),(64,0)}
    assert sum(int(v).bit_count() for mask in masks.values() for v in mask) == 65*64
