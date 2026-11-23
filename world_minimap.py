"""Render saved OTClient terrain and colors observed during exploration."""
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
TERRAIN_FLAGS = frozenset(("ground", "ground_border", "on_bottom", "on_top"))
# PNG index zero is transparent. OTClient reserves byte 255 for transparency
# and renders other out-of-palette values as black (PNG index one).
PIXEL_INDEX = bytes(range(1, 217)) + bytes([1]) * 39 + bytes([0])
PALETTE = bytes([0, 0, 0] + [v * 51 for color in range(216) for v in
                             (color // 36, color // 6 % 6, color % 6)])


def client_minimap():
    return Path(os.environ['APPDATA']) / 'otclient/otclient/DBVictory.eu/minimap.otmm'


def tile_color(items):
    color = None
    for item in items:
        if TERRAIN_FLAGS.isdisjoint(item.get("flags", ())):
            break
        value = item.get("minimap_color")
        if type(value) is int and 0 < value < 216:
            color = value
    return color


def save_colors(db, cells):
    by_floor = {}
    for cell in cells:
        if "minimap_color" in cell:
            z = cell["z"]
            by_floor.setdefault(z, []).append((z, cell["x"], cell["y"], cell["minimap_color"], cell["seen_at"]))
    for z, rows in by_floor.items():
        revision = db.execute("SELECT COALESCE(MAX(revision),0)+1 FROM world_pixels WHERE z=?", (z,)).fetchone()[0]
        db.executemany("""INSERT INTO world_pixels(z,x,y,color,observed,revision) VALUES(?,?,?,?,?,?)
            ON CONFLICT(z,x,y) DO UPDATE SET color=excluded.color, observed=excluded.observed, revision=excluded.revision
            WHERE color<>excluded.color""", ((*row, revision) for row in rows))


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
            pixels = data[1::3].translate(PIXEL_INDEX)
            if any(pixels):
                blocks[x, y] = pixels
    raise ValueError('Niepełny zapis minimapy. Spróbuj ponownie.')


@lru_cache(maxsize=512)
def terrain_png(pixels):
    return image(pixels, PALETTE, 8)


@lru_cache(maxsize=32)
def _floor_count(atlas_path, floor, tile_count):
    # The global count invalidates the cache when exploration adds tiles.
    with closing(sqlite3.connect(atlas_path.resolve().as_uri()+'?mode=ro', uri=True)) as db:
        return db.execute('SELECT COUNT(*) FROM tiles WHERE z=?', (floor,)).fetchone()[0]


def _atlas_masks(atlas_path, floor, count):
    masks = {}
    with closing(sqlite3.connect(atlas_path.resolve().as_uri()+'?mode=ro', uri=True)) as db:
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
    return {key: image(mask, bytes((0, 0, 0, 57, 230, 201)), 1) for key, mask in masks.items()}


_cached_masks = lru_cache(maxsize=8)(_atlas_masks)


def _color_revision(atlas_path, floor):
    with closing(sqlite3.connect(atlas_path.resolve().as_uri()+'?mode=ro', uri=True)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='world_pixels'").fetchone():
            return 0
        return db.execute("SELECT COALESCE(MAX(revision),0) FROM world_pixels WHERE z=?", (floor,)).fetchone()[0]


def _observed_terrain(atlas_path, floor, terrain, saved_at):
    changed = {}
    with closing(sqlite3.connect(atlas_path.resolve().as_uri()+'?mode=ro', uri=True)) as db:
        after = (-1, -1)
        while rows := db.execute('SELECT x,y,color,observed FROM world_pixels WHERE z=? AND (x,y)>(?,?) '
                                 'ORDER BY x,y LIMIT 4096', (floor, *after)).fetchall():
            for x, y, color, observed in rows:
                origin = (x // SIDE * SIDE, y // SIDE * SIDE)
                index = y % SIDE * SIDE + x % SIDE
                saved = terrain.get(origin)
                if saved and saved[index] and (observed <= saved_at or saved[index] == color + 1):
                    continue
                if origin not in changed:
                    changed[origin] = bytearray(saved) if saved else bytearray(SIDE * SIDE)
                changed[origin][index] = color + 1
            after = rows[-1][:2]
    return {**terrain, **{origin: bytes(pixels) for origin, pixels in changed.items()}}


def world_floor(path, atlas_path, floor, *, since=None, tile_count=None):
    if not 0 <= floor <= 15:
        raise ValueError('Piętro musi być w zakresie 0–15.')
    try:
        info = path.stat()
    except FileNotFoundError:
        if not atlas_path:
            raise
        info = None
    count = _floor_count(atlas_path, floor, tile_count) if atlas_path and tile_count is not None else tile_count
    colors = _color_revision(atlas_path, floor) if atlas_path else 0
    fingerprint = (str(path), str(atlas_path), floor, count, colors, (info.st_mtime_ns, info.st_size) if info else None)
    revision = hashlib.sha256(repr(fingerprint).encode()).hexdigest()[:24]
    if tile_count is not None and since == revision:
        return {'floor': floor, 'revision': revision}
    floors, terrain = _terrain(path, info.st_mtime_ns, info.st_size, floor) if info else ([], {})
    masks = {}
    if atlas_path:
        masks = (_cached_masks if count is not None else _atlas_masks)(atlas_path, floor, count)
        if colors:
            terrain = _observed_terrain(atlas_path, floor, terrain, info.st_mtime if info else 0)
        if masks and floor not in floors:
            floors = sorted([*floors, floor])
    sectors = []
    for x, y in sorted(terrain.keys() | masks.keys()):
        row = {'x': x, 'y': y}
        if (x, y) in terrain:
            row['terrain'] = terrain_png(terrain[x, y])
        if (x, y) in masks:
            row['atlas'] = masks[x, y]
        sectors.append(row)
    return {'floor': floor, 'floors': floors, 'sectors': sectors, 'revision': revision}
