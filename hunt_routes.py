"""Suggest and simulate a circuit on the saved hunting-ground map."""
from collections import Counter, defaultdict, deque
from functools import lru_cache
import heapq

from hunt_areas import SIDE, catalogue, sector
from map_atlas import MAX_ANCHORS, MAX_EXPANSIONS, navigation_edges
from navigation_tools import supported_transition
from transition_learning import position


STEPS = ((0,-1), (1,0), (0,1), (-1,0), (1,-1), (1,1), (-1,1), (-1,-1))


class GroundMap:
    def __init__(self, atlas, observations):
        tiles = atlas._paged_tiles()
        records = navigation_edges(tiles, observations)
        entries = {tuple(row['entry']) for edges in records.values() for _, row in edges
                   if row['action'] in {'step','use_with_then_step'}}
        self.links = defaultdict(dict)
        for p, edges in records.items():
            for q, row in sorted(edges, key=lambda e: e[1].get('id') or ''):
                if (row.get('id') and supported_transition(row) and q in tiles and
                        (row.get('evidence') == 'observed' or row.get('automatic_door'))):
                    self.links[p].setdefault(q, row['id'])

        @lru_cache(maxsize=16000)
        def clear(p):
            tile = tiles.get(p)
            return bool(p not in entries and tile and tile['kind'] == 'clear' and
                        all(f['kind'] in {'rope_spot','ladder'} for f in tile.get('navigation', ())))
        self.clear = clear

    def neighbors(self, p):
        for dx, dy in STEPS:
            q = p[0]+dx, p[1]+dy, p[2]
            if self.clear(q):
                yield q, 14 if dx and dy else 10, None
        for q, identity in self.links.get(p, {}).items():
            # A floor change interrupts a sweep of the room. Prefer finishing
            # nearby sectors before using the same stairs again.
            yield q, 50 if p[2] != q[2] else 20, identity


def shortest(start, goals, neighbors, priority=None, budget=None):
    """Nearest goal by walking cost. First sightings break equal-cost ties."""
    costs, parents, queue = {start:0}, {}, [(0,start)]
    best_cost, choices = None, []
    while queue:
        cost, p = heapq.heappop(queue)
        if cost != costs[p]:
            continue
        if best_cost is not None and cost > best_cost:
            break
        if p in goals:
            best_cost = cost
            choices.append(p)
            continue
        if best_cost is not None:
            continue
        if budget is not None and len(parents) >= budget:
            raise ValueError('Dojście wykracza poza zakres pojedynczego wyszukiwania. Wybierz bliższe pole.')
        for q, weight, link in neighbors(p):
            total = cost + weight
            if total < costs.get(q, float('inf')):
                costs[q] = total
                parents[q] = p, link
                heapq.heappush(queue, (total,q))
    if not choices:
        return None
    p = min(choices, key=lambda q: (-(priority or {}).get(q,0), q))
    walk = []
    while p != start:
        origin, link = parents[p]
        walk.append((p,link))
        p = origin
    return walk[::-1]


def simulated(atlas, observations, walk):
    """Keep every turn and the two endpoints of each explicit action."""
    retained = [0]
    for i in range(1,len(walk)-1):
        a, b, c = (walk[j][0] for j in (i-1,i,i+1))
        if walk[i][1] or walk[i+1][1] or any(b[k]-a[k] != c[k]-b[k] for k in range(3)):
            retained.append(i)
    retained.append(len(walk)-1)
    points = [list(walk[i][0]) for i in retained]
    links = [dict(from_index=j-1,id=walk[i][1]) for j,i in enumerate(retained) if walk[i][1]]
    if len(points) > MAX_ANCHORS:
        raise ValueError('Propozycja przekracza rozmiar szkicu. Podziel expowisko na mniejsze obszary.')
    plan = atlas.plan(points, observations, links=links, loop=points[0]==points[-1])
    if not plan['complete']:
        raise ValueError('Symulacja: '+plan['gaps'][0]['reason'])
    return dict(points=points, links=links, path=plan['path'], transitions=len(plan['transitions']))


def propose(atlas, observations, body):
    if not isinstance(body,dict) or body.get('scope') != atlas.scope:
        raise ValueError('Zmieniono atlas. Wczytaj obszary ponownie.')
    info = catalogue(atlas)
    key = body.get('ground')
    if not isinstance(key,str):
        raise ValueError('Wybierz expowisko.')
    if key.startswith('draft:'):
        source = next((d for d in info['drafts'] if d['id']==key[6:] and not d['archived']), None)
        if not source or not source['latest']:
            raise ValueError('Do propozycji ze szkicu potrzebny jest pełny obieg.')
        area = dict(cells=source['latest']['cells'], drafts=[source['id']])
    else:
        area = next((a for a in info['areas'] if a['id']==key), None)
        if not area:
            raise ValueError('Nie znaleziono expowiska.')
    cells = set(map(tuple,area['cells']))
    observations = atlas.with_recorded_ropes(observations)
    ground = GroundMap(atlas,observations)
    if 'approach' in body:
        a, b = body['approach'], body.get('entrance')
        if not position(a) or not position(b) or sector(b) not in cells:
            raise ValueError('Wybierz początek dojścia i wejście na expowisko.')
        a, b = tuple(a), tuple(b)
        if not ground.clear(a) or not ground.clear(b):
            raise ValueError('Dojście wymaga znanego, przechodniego pola na obu końcach.')
        route = shortest(a, {b}, ground.neighbors, budget=MAX_EXPANSIONS)
        if route is None:
            raise ValueError('Atlas nie zna ciągłego dojścia z wybranego pola.')
        if not route:
            return dict(path=[a], transitions=0)
        return simulated(atlas, observations, [(a,None), *route])

    first_seen = Counter()
    for d in info['drafts']:
        if d['id'] in area['drafts']:
            for mark in d['sightings']:
                if tuple(mark['observer_cell']) in cells and sector(mark['point']) in cells:
                    first_seen[tuple(mark['point'])] += mark['count']
    seeds = [tuple(p) for d in atlas.drafts() if d['id'] in area['drafts'] for p in d['points']]
    return circuit(atlas, observations, ground, cells, first_seen, seeds)


def circuit(atlas, observations, ground, cells, first_seen, seeds):
    # Keep the approach from town outside the circuit, even if it is shorter.
    ordinary = {(x,y,z) for sx,sy,z in sorted(cells) for x in range(sx,sx+SIDE)
                for y in range(sy,sy+SIDE) if ground.clear((x,y,z))}
    if not ordinary:
        raise ValueError('W granicach expowiska brakuje zapisanych przechodnich pól.')
    start = next((p for p in seeds if p in ordinary), min(ordinary))
    # An explicit action may land on the gate itself. Only its directed edge
    # permits entering it. The shared simulator checks it again before saving.
    # Use the two player positions for the boundary. A ladder or rope tile
    # clicked from inside the sector can be just beyond its border.
    nodes = ordinary | {q for p,links in ground.links.items() for q in links
                         if sector(p) in cells and sector(q) in cells}
    graph, reverse = {}, defaultdict(list)
    for p in sorted(nodes):
        graph[p] = [(q,w,link) for q,w,link in ground.neighbors(p) if q in nodes]
        for q,_,_ in graph[p]:
            reverse[q].append(p)
    def reachable(next_points):
        visited, pending = {start}, deque([start])
        while pending:
            for q in next_points(pending.popleft()):
                if q not in visited:
                    visited.add(q)
                    pending.append(q)
        return visited
    accessible = reachable(lambda p: (q for q,_,_ in graph[p])) & reachable(lambda p: reverse[p])
    by_sector = defaultdict(list)
    for p in sorted(ordinary & accessible):
        by_sector[sector(p)].append(p)
    # Aim inside each reachable sector, near first sightings where available.
    # A creature seen across a wall is never treated as a walkable waypoint.
    moments = defaultdict(lambda: [0,0,0])
    for (x,y,z),count in first_seen.items():
        accum = moments[sector((x,y,z))]
        accum[0] += count
        accum[1] += x*count
        accum[2] += y*count
    goals = {}
    for (sx,sy,z), candidates in by_sector.items():
        count, xs, ys = moments[sx,sy,z]
        x,y = (xs/count,ys/count) if count else (sx+(SIDE-1)/2,sy+(SIDE-1)/2)
        goal = min(candidates,key=lambda p: (p[0]-x)**2+(p[1]-y)**2)
        goals[goal] = count
    targets = set(goals)-{start}
    if not targets and len(by_sector[sector(start)]) > 1:
        targets.add(max(by_sector[sector(start)],key=lambda p: abs(p[0]-start[0])+abs(p[1]-start[1])))
    if not targets:
        raise ValueError('Brak drugiego dostępnego pola z potwierdzonym powrotem.')
    here, walk = start, [(start,None)]
    while targets or here != start:
        leg = shortest(here, targets or {start}, graph.__getitem__, goals)
        walk.extend(leg)
        here = walk[-1][0]
        targets.difference_update(p for p,_ in leg)
    result = simulated(atlas,observations,walk)
    visited = {sector(p) for p in result['path']}
    if visited-cells or set(by_sector)-visited:
        raise ValueError('Symulacja zmieniła pokrycie obszaru. Sprawdź granice expowiska.')
    return dict(**result, entrance=start, covered=len(visited), total=len(cells),
                missing=sorted(cells-visited), sightings=sum(first_seen.values()))
