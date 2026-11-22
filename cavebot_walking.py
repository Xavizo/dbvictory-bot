"""Local Cavebot paths over observed tiles. All decisions here are free of session state."""
from collections import deque
import random

from protocol import Direction


OFFSETS={(0,-1):Direction.NORTH,(1,0):Direction.EAST,(0,1):Direction.SOUTH,(-1,0):Direction.WEST,
         (1,-1):Direction.NORTHEAST,(1,1):Direction.SOUTHEAST,(-1,1):Direction.SOUTHWEST,(-1,-1):Direction.NORTHWEST}


def distance(a,b):return max(abs(a[0]-b[0]),abs(a[1]-b[1])) if a[2]==b[2] else 100000
def walkable(cell):
    return bool(cell and cell.get("kind")=="clear" and
                cell.get("occupants",0)<=cell.get("occupant_types",{}).get("self",0) and
                all(f["kind"] in {"rope_spot","ladder"} for f in cell.get("navigation",[])))

def step_clear(cells,origin,target,blocked=(),door=False):
    tiles={(c['x'],c['y'],c['z']):c for c in cells}
    def allowed(p):
        c=tiles.get(tuple(p))
        if tuple(p) in blocked or not c:return False
        if door and list(p)==target:
            return (c.get('kind')=='clear' and c.get('occupants',0)<=c.get('occupant_types',{}).get('self',0) and
                    any((f['kind'] in {'door','level_gate'} or (f['kind']=='passage' and f.get('behavior_hint') in {'use_enters','open_then_step'}))
                        and f.get('door_state_hint')=='open' for f in c.get('navigation',[])))
        return walkable(c)
    # The client enters only the diagonal destination, not the two side tiles.
    return distance(origin,target)==1 and allowed(target)

def detour(cells,start,end,blocked=()):
    """Bounded local BFS: only fresh visible terrain, no unobserved transitions."""
    start,end=tuple(start),tuple(end);tiles={(c['x'],c['y'],c['z']):c for c in cells};blocked=set(blocked)
    allowed=lambda p:p not in blocked and walkable(tiles.get(p))
    queue=deque([start]);previous={start:None}
    while queue and len(previous)<=512:
        p=queue.popleft()
        if p==end:
            path=[]
            while p is not None:path.append(list(p));p=previous[p]
            return path[::-1]
        for dx,dy in OFFSETS:
            q=(p[0]+dx,p[1]+dy,p[2])
            if q in previous or not allowed(q):continue
            previous[q]=p;queue.append(q)
    return None

def varied_detour(cells,start,end,reference,blocked=(),rng=None):
    """One bounded bend per leg; only known walkable cells, at most two extra steps."""
    # Stay within 2 SQM of this ordinary leg, without revisiting cells.
    corridor=[c for c in cells if any(distance((c['x'],c['y'],c['z']),p)<=2 for p in reference)]
    shortest=detour(corridor,start,end,blocked)
    if not shortest or len(shortest)<7:return shortest
    rng=rng or random.SystemRandom()
    mid=shortest[len(shortest)//2];options=[shortest]
    for c in corridor:
        anchor=[c['x'],c['y'],c['z']]
        if anchor==mid or distance(anchor,mid)>2 or not walkable(c):continue
        a=detour(corridor,start,anchor,blocked);b=detour(corridor,anchor,end,blocked)
        if not a or not b:continue
        path=a[:-1]+b
        if len(path)>len(shortest)+2 or len({tuple(v) for v in path})!=len(path):continue
        # Prefer broad, simple bends; avoid a random zigzag at every step.
        dirs=[(q[0]-p[0],q[1]-p[1]) for p,q in zip(path,path[1:])]
        if sum(a!=b for a,b in zip(dirs,dirs[1:]))>3:continue
        if path not in options:options.append(path)
    # Compare alternatives against the whole visible room, including the
    # free space just outside the permitted band around the route.
    terrain = {(c['x'],c['y'],c['z']) for c in cells if walkable(c)} - set(blocked)
    def clearance_cost(path):
        cost = len(path) - len(shortest)
        for x,y,z in path[1:-1]:
            free = sum((x+dx,y+dy,z) in terrain for dx,dy in OFFSETS)
            cost += 8 - free
        return cost
    costs = [clearance_cost(path) for path in options]
    best = min(costs)
    return rng.choice([path for path,cost in zip(options,costs) if cost == best])


def waypoint_tile(cells, excluded, origin, exact, onward, pos, *, displaced, preferred):
    """Choose a reachable spot in the marker's room and verify the continuation."""
    floor = {(t['x'],t['y'],t['z']) for t in cells
             if walkable(t) and not t.get('navigation') and not t.get('transitions') and not t.get('stairs')} - excluded
    def open_between(a, b):
        # Require the intervening rectangle to be observed, even diagonally.
        # Nearby coordinates across a wall or doorway are separate rooms.
        return a[2] == b[2] and all((x,y,a[2]) in floor
            for x in range(min(a[0],b[0]),max(a[0],b[0])+1)
            for y in range(min(a[1],b[1]),max(a[1],b[1])+1))
    def room(p):
        return sum((p[0]+dx,p[1]+dy,p[2]) in floor for dx,dy in OFFSETS)
    can_pass = (room(exact) >= 5 and open_between(origin, exact) and open_between(exact, onward))
    if (can_pass and 0 < distance(pos, exact) <= 2 and distance(pos, onward) == 1
            and open_between(pos, exact) and open_between(pos, onward)):
        return pos
    if displaced:
        return None
    if can_pass:
        # Both hops must be ordinary steps. Prefer room to gather monsters
        # and keep the direction of travel when equally open.
        choices = []
        for dx, dy in OFFSETS:
            mid = [origin[0]+dx, origin[1]+dy, origin[2]]
            if (distance(mid,onward) == 1 and open_between(origin,mid)
                    and open_between(mid,onward) and open_between(mid,exact)):
                bend = (onward[0]-mid[0]-dx)**2 + (onward[1]-mid[1]-dy)**2
                choices.append(((room(mid), -bend), mid))
        if choices:
            best = max(score for score, _ in choices)
            mids = [mid for score, mid in choices if score == best]
            # Keep the same choice across retries while the terrain permits it.
            return preferred if preferred in mids else random.choice(mids)
    return exact
