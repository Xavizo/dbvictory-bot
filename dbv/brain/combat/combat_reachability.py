"""Same-floor, live-terrain connectivity; no game commands or historical shortcuts."""
from collections import deque

def ordinary_terrain(cell):
    if not cell or cell.get('kind')!='clear':return False
    for feature in cell.get('navigation',[]):
        kind=feature['kind']
        if kind in {'rope_spot','ladder'}:continue
        if kind in {'door','level_gate'} and feature.get('door_state_hint')=='open':continue
        if kind=='passage' and feature.get('behavior_hint') in {'open_then_step','use_enters'} and feature.get('door_state_hint')=='open':continue
        return False
    return not cell.get('stairs')

def reachable_tiles(cells,origin):
    """Occupancy does not seal terrain: a queue of monsters remains reachable.

    Closed doors, unknown terrain and implicit floor changes do seal it. Doors
    can become reachable after an explicit route action opens them. Diagonal
    steps match the observed client's destination-only movement rule.
    """
    start=tuple(origin);tiles={(c['x'],c['y'],c['z']):c for c in cells}
    seen={start};queue=deque([start])
    while queue:
        x,y,z=queue.popleft()
        for dx,dy in ((0,1),(1,0),(0,-1),(-1,0),(1,1),(-1,1),(1,-1),(-1,-1)):
            p=(x+dx,y+dy,z)
            if p in seen or not ordinary_terrain(tiles.get(p)):continue
            seen.add(p);queue.append(p)
    return seen
