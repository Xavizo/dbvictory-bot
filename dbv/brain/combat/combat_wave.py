"""Directional spell checks. Geometry is deliberately separate from cast range.

The catalogue has categories, NOT reliable casting semantics or server hit masks.
Shinretsuha is on-target (user-confirmed 2026-11-17), despite its Wave label.
Other Wave spells remain blocked until a reviewed profile exists; never invent
a cone from range or apply a directional restriction to an on-target spell.
"""
import json
import time
from functools import lru_cache
from pathlib import Path
from dbv import paths

TURN_OPCODES = frozenset((0x6F, 0x70, 0x71, 0x72))
CARDINALS = ((0, -1), (1, 0), (0, 1), (-1, 0))
# Overrides describe delivery, not range, server cooldown or damage. Preserve
# catalogue assets and saved conditions. Keep the picker label in sync.
TARGETED_SPELLS = frozenset({'shinretsuha'})
PROFILES = {}


@lru_cache(maxsize=1)
def catalogue_waves():
    try:
        rows = json.loads(paths.SPELL_CATALOG.read_text(encoding='utf-8'))['spells']
        return frozenset(r['words'].strip().casefold() for r in rows if r.get('category') == 'Wave')
    except (OSError, ValueError, KeyError, TypeError):
        return frozenset()


def is_wave(spell):
    word = spell['words'].strip().casefold()
    if word in TARGETED_SPELLS:
        return False
    return word in PROFILES or word in catalogue_waves()


def facing(gs, after=0):
    snapshot = getattr(gs, 'client_snapshot', None)
    if not snapshot or tuple(snapshot.get('position', ())) != tuple(gs.position):
        return None
    stamp = snapshot.get('monotonic', 0)
    direction = snapshot.get('direction')
    if not after <= stamp <= time.monotonic() or time.monotonic()-stamp > .5:
        return None
    return direction if type(direction) is int and 0 <= direction <= 7 else None


def rotated(offset, direction):
    x, y = offset
    for _ in range(direction):
        x, y = -y, x
    return x, y


def clear_ray(cells, origin, destination):
    """Conservative supercover: an unknown cell/corner or projectile wall blocks."""
    x, y, z = origin
    dx, dy = destination[0]-x, destination[1]-y
    nx, ny = abs(dx), abs(dy)
    sx, sy = (1 if dx > 0 else -1), (1 if dy > 0 else -1)
    def clear(px, py):
        cell = cells.get((px, py, z))
        return bool(cell and cell.get('kind') != 'unknown' and cell.get('items') and
                    all('flags' in item and 'blocks_projectiles' not in item['flags'] for item in cell['items']))
    ix = iy = 0
    while ix < nx or iy < ny:
        cmp = (1+2*ix)*ny-(1+2*iy)*nx
        if cmp == 0:
            if not clear(x+sx, y) or not clear(x, y+sy):
                return False
            x += sx; y += sy; ix += 1; iy += 1
        elif cmp < 0:
            x += sx; ix += 1
        else:
            y += sy; iy += 1
        if not clear(x, y):
            return False
    return True


def plan_wave(spell, gs, candidates, target, sample):
    if not is_wave(spell):
        return None
    result = dict(direction=None, hit_ids=[], reason=None,
                  note='Wave: kontrola kierunku, obszaru i przeszkód')
    profile = PROFILES.get(spell['words'].strip().casefold())
    if profile is None:
        return {**result, 'reason': 'Wave: brak sprawdzonego profilu obszaru'}
    origin = tuple(gs.position)
    if (not sample or sample.get('status') != 'ready' or sample.get('stale') or
            sample.get('age_seconds', 99) > .6 or tuple(sample.get('center', ())) != origin):
        return {**result, 'reason': 'Wave: brak świeżej mapy do sprawdzenia trafienia'}
    if facing(gs) is None:
        return {**result, 'reason': 'Wave: brak świeżego odczytu kierunku postaci'}
    if target is None or tuple(target.get('position', ())) == origin:
        return {**result, 'reason': 'Wave: brak celu przed postacią'}
    cells = {(c['x'], c['y'], c['z']): c for c in sample['cells']}
    options = []
    for direction in range(4):
        area = {(origin[0]+dx, origin[1]+dy, origin[2]) for dx, dy in (rotated(o, direction) for o in profile)}
        hits = [c['id'] for c in candidates if tuple(c.get('position', ())) in area and
                clear_ray(cells, origin, c['position'])]
        if target['id'] in hits:
            options.append((len(hits), direction == facing(gs), -direction, hits))
    if not options:
        return {**result, 'reason': 'Wave: cel poza sprawdzonym obszarem lub zasłonięty'}
    _, _, negative, hits = max(options)
    return {**result, 'direction': -negative, 'hit_ids': hits}
