"""Render a saved OTClient v1 minimap only when the world atlas is opened."""
import base64
import hashlib
from contextlib import closing
from functools import lru_cache
import os
from pathlib import Path
import sqlite3
import struct
import zlib


SIDE = 64
PALETTE = bytes([0, 0, 0] + [v * 51 for color in range(216) for v in
                             (color // 36, color // 6 % 6, color % 6)])


def client_minimap():
    return Path(os.environ['APPDATA']) / 'otclient/otclient/DBVictory.eu/minimap.otmm'


def chunk(kind, payload):
    return (struct.pack('>I', len(payload)) + kind + payload
            + struct.pack('>I', zlib.crc32(kind + payload)))


def image(pixels, palette, depth):
    stride = SIDE * depth // 8
    rows = b''.join(b'\0' + pixels[start:start+stride] for start in range(0, len(pixels), stride))
    header = struct.pack('>IIBBBBB', SIDE, SIDE, depth, 3, 0, 0, 0)
    png = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header) + chunk(b'PLTE', palette)
           + chunk(b'tRNS', b'\0') + chunk(b'IDAT', zlib.compress(rows, 1)) + chunk(b'IEND', b''))
    return base64.b64encode(png).decode('ascii')


@lru_cache(maxsize=2)
def _terrain(path, modified, size, floor):
    floors, blocks = set(), {}
    with path.open('rb') as stream:
        header = stream.read(12)
        if len(header) != 12:
            raise ValueError('Niepełny nagłówek minimapy.')
        magic, offset, version, _ = struct.unpack('<4sHHI', header)
        if magic != b'OTMM' or version != 1 or not 12 <= offset < size:
            raise ValueError('Nieobsługiwany format zapisanej minimapy.')
        stream.seek(offset)
        while len(position := stream.read(5)) == 5:
            x, y, z = struct.unpack('<HHB', position)
            if (x, y, z) == (65535, 65535, 255):
                return sorted(floors), blocks
            size_bytes = stream.read(2)
            if len(size_bytes) != 2 or x % SIDE or y % SIDE or z > 15:
                break
            length, = struct.unpack('<H', size_bytes)
            if stream.tell() + length > size:
                break
            floors.add(z)
            if z != floor:
                stream.seek(length, 1)
                continue
            decoder = zlib.decompressobj()
            data = decoder.decompress(stream.read(length), SIDE * SIDE * 3 + 1)
            if len(data) != SIDE * SIDE * 3 or not decoder.eof:
                break
            pixels = bytes((color + 1 if color < 216 else 1) if flags & 1 else 0
                           for flags, color in zip(data[::3], data[1::3]))
            if any(pixels):
                blocks[x, y] = image(pixels, PALETTE, 8)
    raise ValueError('Niepełny zapis minimapy. Spróbuj ponownie.')


@lru_cache(maxsize=32)
def _floor_count(atlas_path, floor, tile_count):
    # The global count invalidates the cache when exploration adds tiles.
    with closing(sqlite3.connect(atlas_path.resolve().as_uri()+'?mode=ro', uri=True)) as db:
        return db.execute('SELECT COUNT(*) FROM tiles WHERE z=?', (floor,)).fetchone()[0]


def world_floor(path, atlas_path, floor, *, since=None, tile_count=None):
    if not 0 <= floor <= 15:
        raise ValueError('Piętro musi być w zakresie 0–15.')
    info = path.stat()
    # The atlas layer is a mask of discovered coordinates. Revisiting a tile
    # updates its timestamp in SQLite without changing the pixels of this map.
    count = _floor_count(atlas_path, floor, tile_count) if atlas_path and tile_count is not None else tile_count
    fingerprint = (str(path), str(atlas_path), floor, count, info.st_mtime_ns, info.st_size)
    revision = hashlib.sha256(repr(fingerprint).encode()).hexdigest()[:24]
    if tile_count is not None and since == revision:
        return {'floor': floor, 'revision': revision}
    floors, terrain = _terrain(path, info.st_mtime_ns, info.st_size, floor)
    masks = {}
    if atlas_path:
        with closing(sqlite3.connect(atlas_path.resolve().as_uri()+'?mode=ro', uri=True)) as db:
            # Release the read lock before rasterizing each chunk of the floor.
            after = (-1, -1)
            while rows := db.execute('SELECT x,y FROM tiles WHERE z=? AND (x,y)>(?,?) '
                                     'ORDER BY x,y LIMIT 4096', (floor, *after)).fetchall():
                for x, y in rows:
                    origin = (x // SIDE * SIDE, y // SIDE * SIDE)
                    if origin not in masks:
                        masks[origin] = bytearray(512)
                    index = y % SIDE * SIDE + x % SIDE
                    masks[origin][index // 8] |= 128 >> (index % 8)
                after = rows[-1]
    sectors = []
    for x, y in sorted(terrain.keys() | masks.keys()):
        row = {'x': x, 'y': y}
        if (x, y) in terrain:
            row['terrain'] = terrain[x, y]
        if (x, y) in masks:
            row['atlas'] = image(masks[x, y], bytes((0, 0, 0, 57, 230, 201)), 1)
        sectors.append(row)
    return {'floor': floor, 'floors': floors, 'sectors': sectors, 'revision': revision}
