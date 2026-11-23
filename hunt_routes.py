"""Suggest and simulate a circuit on the saved hunting-ground map."""
from collections import Counter, defaultdict
from functools import lru_cache
import heapq

from hunt_corridors import crossings, patch, repeat_count, separate_corridors
from hunt_areas import SIDE, catalogue, checked_sectors, checked_connector, connector_zone, sector
from map_atlas import MAX_ANCHORS, MAX_EXPANSIONS, navigation_edges
from navigation_tools import cuttable_web, supported_transition
from transition_learning import position


STEPS = ((0,-1), (1,0), (0,1), (-1,0), (1,-1), (1,1), (-1,1), (-1,-1))


class GroundMap:
    def __init__(self, atlas, observations, excluded=frozenset(), allowed=None):
        tiles = atlas._paged_tiles()
        records = navigation_edges(tiles, observations)
        entries = {tuple(row['entry']) for edges in records.values() for _, row in edges
                   if row['action'] in {'step','use_with_then_step'}}
        self.links = defaultdict(dict)
        self.excluded = excluded
        self.allowed = allowed

        def in_bounds(p):
            if allowed is None and not excluded:
                return True
            cell = sector(p)
            return (allowed is None or cell in allowed) and cell not in excluded

        for p, edges in records.items():
            for q, row in sorted(edges, key=lambda e: e[1].get('id') or ''):
                if not all(map(in_bounds, (p,row['entry'],q))):
                    continue
                if (row.get('id') and supported_transition(row) and q in tiles and
                        (row.get('evidence') == 'observed' or row.get('automatic_door'))):
                    self.links[p].setdefault(q, row['id'])

        @lru_cache(maxsize=16000)
        def clear(p):
            if not in_bounds(p):
                return False
            tile = tiles.get(p)
            return bool(p not in entries and tile and tile['kind'] == 'clear' and
                        all(f['kind'] in {'rope_spot','ladder'} for f in tile.get('navigation', ())))
        self.clear = clear
        @lru_cache(maxsize=16000)
        def web(p):
            if p in entries or not in_bounds(p):
                return False
            return cuttable_web(tiles.get(p)) is not None
        self.web = web

    def neighbors(self, p):
        for dx, dy in STEPS:
            q = p[0]+dx, p[1]+dy, p[2]
            if self.clear(q):
                yield q, 14 if dx and dy else 10, None
            elif self.web(q):
                # Pin both endpoints for the shared simulator's conditional AiO action.
                yield q, (14 if dx and dy else 10)+35, True
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


def shorten_sweeps(path, clear, *, anchors=None, protected=()):
    """Cut local backtracks only when all previously visited 4×4 areas remain covered."""
    def patch(p):
        return p[0]//4,p[1]//4,p[2]

    path = list(map(tuple,path))
    counts = Counter(map(patch,path))
    stops = set(anchors) if anchors is not None else None
    protected = set(protected)
    shortened, kept = [], {}
    i = 0
    while i < len(path):
        kept[i] = len(shortened)
        a = path[i]
        shortened.append(a)
        if stops is not None and i not in stops:
            i += 1
            continue
        candidates = []
        for j in range(i+1,min(i+49,len(path))):
            b = path[j]
            if b[2]!=a[2] or j in protected:
                break
            if stops is not None and j not in stops:
                continue
            steps = max(abs(a[0]-b[0]),abs(a[1]-b[1]))
            gain = j-i-steps
            if steps<=12 and gain>=2 and (i or j<len(path)-1):
                candidates.append((gain,j,steps))
        for _,j,steps in sorted(candidates,reverse=True):
            b = path[j]
            if not clear(a) or not clear(b):
                continue
            middle = [(a[0]+round((b[0]-a[0])*k/steps),a[1]+round((b[1]-a[1])*k/steps),a[2])
                      for k in range(1,steps)]
            if any(not clear(p) or patch(p) not in counts for p in middle):
                continue
            delta = Counter(map(patch,path[i+1:j] if steps else path[i+1:j+1]))
            delta.subtract(map(patch,middle))
            if any(counts[key]<=lost for key,lost in delta.items()):
                continue
            counts.subtract(delta)
            shortened.extend(middle)
            if not steps:
                kept[j]=kept[i]
            i=j if steps else j+1
            break
        else:
            i+=1
    return shortened,kept


def sparse_waypoints(atlas, observations, plan, protected=()):
    """Remove editable anchors while keeping the walked corridor, coverage and actions."""
    points, legs = plan['points'], plan['legs']
    if not plan['complete'] or len(points) < 4 or len(legs) != len(points)-1:
        return plan
    offsets = [0, *(leg['end'] for leg in legs)]
    pins = {0, len(points)-1, *protected}
    for link in plan['links']:
        pins.update((link['from_index'], link['from_index']+1))
    # Limit the distance to the next editable anchor, even on a straight road.
    last = 0
    for i in range(1,len(points)):
        if i in pins or offsets[i]-offsets[last] >= 14:
            pins.add(i)
            last = i
    if len(pins) == 2 and points[0] == points[-1]:
        pins.add(len(points)//2)
    def same_corridor(original, replay):
        if original == replay:
            return True
        return (len(original)==len(replay) and set(map(patch,original))==set(map(patch,replay))
                and all(p[2]==q[2] and max(abs(p[0]-q[0]),abs(p[1]-q[1]))<=1
                        for p,q in zip(original,replay)))

    while len(pins) < len(points):
        keep = sorted(pins)
        order = {old:new for new,old in enumerate(keep)}
        links = [dict(link, from_index=order[link['from_index']]) for link in plan['links']]
        trial = atlas.plan([points[i] for i in keep], observations, links=links, loop=plan['loop'])
        split = set()
        for k,(a,b) in enumerate(zip(keep,keep[1:])):
            if b <= a+1:
                continue
            leg = trial['legs'][k] if k < len(trial['legs']) else None
            if leg is None or not same_corridor(plan['path'][offsets[a]:offsets[b]+1],
                                                trial['path'][leg['start']:leg['end']+1]):
                split.add((a+b)//2)
        if not split:
            if (trial['complete'] and trial['return_mode']==plan['return_mode']
                    and trial['transitions']==plan['transitions'] and trial['avoid_tiles']==plan['avoid_tiles']
                    and len(trial['path'])==len(plan['path']) and set(map(patch,trial['path']))==set(map(patch,plan['path']))):
                return trial
            return plan
        pins.update(split)
    return plan


def shorter_draft(atlas, observations, plan, labels):
    result = _shorter_draft(atlas, observations, plan, labels)
    labelled = {tuple(p) for p,label in zip(plan['points'],labels) if label}
    def simplify(candidate):
        protected = [i for i,p in enumerate(candidate['points']) if tuple(p) in labelled]
        return sparse_waypoints(atlas, observations, candidate, protected)
    sparse = simplify(result)
    if result is not plan and len(sparse['path']) > len(plan['path']):
        original = simplify(plan)
        if len(original['points']) < len(plan['points']) and len(original['points']) <= len(sparse['points']):
            return original
    return sparse


def _shorter_draft(atlas,observations,plan,labels):
    base = _compact_draft(atlas,observations,plan,labels)
    if not base['complete'] or not base['loop_closed'] or base['return_mode'] != 'closed':
        return base
    path = [tuple(p) for p in base['path']]
    repeated = repeat_count(path)
    if not repeated or not base['legs']:
        return base
    sectors = set(map(sector,path))
    ground = GroundMap(atlas,atlas.with_recorded_ropes(observations),allowed=sectors)
    starts = {leg['start'] for leg in base['legs']} | {leg['end'] for leg in base['legs']}
    linked = {link['from_index'] for link in base['links']}
    for leg in base['legs']:
        if leg['from_index'] in linked:
            starts.update(range(leg['start'],leg['end']+1))
    walk = [(p,None) for p in path]
    alt, remap = separate_corridors(walk,ground.clear,pinned=starts,coverage=patch)
    if alt == walk:
        return base
    # One via point can steer a whole leg. Encoding every diagonal turn
    # as an editable waypoint would make a long sketch hard to maintain.
    alternatives = []
    usage = crossings(path)
    for leg in base['legs']:
        a,b = leg['start'],leg['end']
        original = path[a:b+1]
        segment = [p for p,_ in alt[remap[a]:remap[b]+1]]
        if segment == original or len(segment) < 3:
            continue
        prior = crossings(original)
        after = crossings(segment)
        gain = sum(n-(usage[e]==n) for e,n in prior.items())
        gain -= sum(n-(usage[e]<=prior[e]) for e,n in after.items())
        if gain <= 0:
            continue
        def deviation(i):
            p = segment[i]
            distance = min(max(abs(p[0]-q[0]),abs(p[1]-q[1])) for q in original)
            return distance, -abs(2*i-len(segment)+1)
        i = max(range(1,len(segment)-1),key=deviation)
        if deviation(i)[0]:
            alternatives.append((gain,a,segment[i]))
    budget = max(2,len(plan['points'])//10)
    via = {a:p for _,a,p in sorted(alternatives,reverse=True)[:budget]}
    fields = set(map(patch,path))
    while via:
        points = [base['points'][0]]
        order = {0:0}
        for leg in base['legs']:
            if leg['start'] in via:
                points.append(list(via[leg['start']]))
            if not leg.get('closing'):
                order[leg['to_index']] = len(points)
                points.append(base['points'][leg['to_index']])
        if len(points)>MAX_ANCHORS:
            return base
        links = [dict(from_index=order[l['from_index']],id=l['id']) for l in base['links']]
        trial = atlas.plan(points,observations,loop=True,links=links)
        if not trial['complete'] or not trial['loop_closed']:
            return base
        missing = fields-set(map(patch,trial['path']))
        if missing:
            # The ordinary sketch simulator may take a different diagonal on
            # either side of a via. Restore any leg that provided lost coverage.
            reject = [leg['start'] for leg in base['legs'] if leg['start'] in via and
                      any(patch(p) in missing for p in path[leg['start']:leg['end']+1])]
            if not reject:
                return base
            for index in reject:
                del via[index]
            continue
        after = repeat_count(trial['path'])
        if (after<repeated and trial['transitions']==base['transitions']
                and set(map(sector,trial['path']))==sectors
                and set(map(tuple,trial['avoid_tiles']))<=set(map(tuple,base['avoid_tiles']))
                and len(trial['path'])<=len(path)+(len(path)-1)//25):
            trial['corridor_repeats'] = [repeat_count(plan['path']),after]
            return trial
        break
    return base


def _compact_draft(atlas,observations,plan,labels):
    """Preview a shorter authored loop with the same entrances, exits and coverage."""
    if not plan['complete'] or not plan['loop_closed'] or plan['return_mode']!='closed':
        return plan
    points, path = plan['points'],plan['path']
    indices = [0]+[leg['end'] for leg in plan['legs']]
    if len(indices)!=len(points) or len(set(indices))!=len(indices):
        return plan
    protected = {indices[i] for i,label in enumerate(labels) if label}
    for link in plan['links']:
        k=link['from_index']
        protected.update(indices[k:k+2])
    ground = GroundMap(atlas,atlas.with_recorded_ropes(observations))
    _, kept = shorten_sweeps(path,ground.clear,anchors=indices,protected=protected)
    remaining = [i for i,at in enumerate(indices) if at in kept]
    if len(remaining)==len(points) or len(remaining)<2:
        return plan
    def replay(selected):
        order={old:new for new,old in enumerate(selected)}
        links=[dict(from_index=order[l['from_index']],id=l['id']) for l in plan['links']]
        return atlas.plan([points[i] for i in selected],observations,loop=True,links=links)
    candidate=replay(remaining)
    def coverage(result):
        return {(x//4,y//4,z) for x,y,z in result['path']}
    # The simulator can choose a different equal-cost diagonal. Restore the
    # original anchors of the affected shortcuts and verify the entire loop.
    missing=coverage(plan)-coverage(candidate)
    if missing and candidate['complete']:
        restore=set(remaining)
        for i,j in zip(remaining,remaining[1:]):
            if j>i+1 and any((x//4,y//4,z) in missing for x,y,z in path[indices[i]:indices[j]+1]):
                restore.update(range(i+1,j))
        if len(remaining)<len(restore)<len(points):
            candidate=replay(sorted(restore))
    if (candidate['complete'] and candidate['loop_closed'] and len(candidate['path'])<len(path)
            and candidate['transitions']==plan['transitions'] and coverage(plan)<=coverage(candidate)
            and set(map(sector,candidate['path']))==set(map(sector,path))
            and set(map(tuple,candidate['avoid_tiles']))<=set(map(tuple,plan['avoid_tiles']))):
        return candidate
    return plan


def simulated(atlas, observations, walk, excluded=frozenset(), allowed=None):
    """Keep every turn and the two endpoints of each explicit action."""
    retained = [0]
    for i in range(1,len(walk)-1):
        a, b, c = (walk[j][0] for j in (i-1,i,i+1))
        if walk[i][1] or walk[i+1][1] or any(b[k]-a[k] != c[k]-b[k] for k in range(3)):
            retained.append(i)
    retained.append(len(walk)-1)
    points = [list(walk[i][0]) for i in retained]
    links = [dict(from_index=j-1,id=walk[i][1]) for j,i in enumerate(retained) if isinstance(walk[i][1],str)]
    if len(points) > MAX_ANCHORS:
        raise ValueError('Propozycja przekracza rozmiar szkicu. Podziel expowisko na mniejsze obszary.')
    plan = atlas.plan(points, observations, links=links, loop=points[0]==points[-1])
    if not plan['complete']:
        raise ValueError('Symulacja: '+plan['gaps'][0]['reason'])
    if excluded and (any(sector(p) in excluded for p in plan['path']) or
                     any(sector(t[k]) in excluded for t in plan['transitions'] for k in ('origin','entry','exit'))):
        raise ValueError('Symulacja przecięła pominięty sektor. Zmień wybór sektorów.')
    if allowed is not None and (any(sector(p) not in allowed for p in plan['path']) or
            any(sector(t[k]) not in allowed for t in plan['transitions'] for k in ('origin','entry','exit'))):
        raise ValueError('Odcinek lub przejście wykracza poza zaznaczony teren.')
    passages = [{k:t[k] for k in ('id','origin','entry','exit','action')} for t in plan['transitions']]
    return dict(points=points, links=links, path=plan['path'], transitions=len(passages), passages=passages)


def varied_route(atlas, observations, ground, walk, stops, result):
    path = result['path']
    repeats = repeat_count(path)
    if not repeats:
        return result
    selected = set(map(sector,path))
    def clear(p):
        return sector(p) in (ground.allowed if ground.allowed is not None else selected) and ground.clear(p)
    pinned = {i for i,(p,_) in enumerate(walk) if p in stops}
    other, _ = separate_corridors(walk,clear,pinned=pinned,coverage=sector)
    if other == walk:
        return result
    try:
        trial = simulated(atlas,observations,other,ground.excluded,ground.allowed)
    except ValueError:
        return result
    if (trial['passages']==result['passages'] and selected<=set(map(sector,trial['path']))
            and len(trial['path'])<=len(path)+(len(path)-1)//25 and repeat_count(trial['path'])<repeats):
        return trial
    return result


def compact_proposal(atlas, observations, result, model):
    variants = result.get('comparison',{}).get('variants',())
    geometries = [result, *(v['geometry'] for v in variants)]
    compact = {}
    for route in geometries:
        points = route.get('points', [])
        if len(points) < 4:
            continue
        key = tuple(map(tuple,points)), tuple((l['from_index'],l['id']) for l in route['links'])
        if key not in compact:
            plan = atlas.plan(points,observations,links=route['links'],loop=points[0]==points[-1])
            if plan['path'] != route['path']:
                continue
            sparse = sparse_waypoints(atlas,observations,plan)
            compact[key] = sparse['points'], sparse['links'], sparse['path']
        route['points'], route['links'], route['path'] = compact[key]
    for variant in variants:
        route = variant['geometry']
        actions = {(tuple(t['origin']),tuple(t['exit'])) for t in route['passages']}
        variant['metrics'] = dict(model.estimate(route['path'],actions),repeated_segments=repeat_count(route['path']))
    return result


def propose(atlas, observations, body):
    if not isinstance(body,dict) or body.get('scope') != atlas.scope:
        raise ValueError('Zmieniono atlas. Wczytaj obszary ponownie.')
    key = body.get('ground')
    if not isinstance(key,str):
        raise ValueError('Wybierz expowisko.')
    info = catalogue(atlas) if not key.startswith('manual:') else None
    manual_sources = []
    if key == 'world':
        from hunt_world import world_zones
        from manual_hunts import discoveries
        chosen = checked_sectors(body.get('selected'))
        known = world_zones(atlas)['cells']
        manual_sources = [g for g in discoveries(atlas) if any(tuple(c) in chosen for c in g['cells'])]
        related = [d['id'] for d in info['drafts'] if
                   any(tuple(m['observer_cell']) in chosen for m in d['sightings'])
                   or any(tuple(c) in chosen for c in (d.get('terrain') or {}).get('sectors', ()))
                   or any(tuple(c) in chosen for c in (d.get('latest') or {}).get('cells', ()))]
        area = dict(cells=known, drafts=related)
    elif key.startswith('draft:'):
        source = next((d for d in info['drafts'] if d['id']==key[6:] and not d['archived']), None)
        if not source or not (source['latest'] or source.get('terrain')):
            raise ValueError('Do propozycji ze szkicu potrzebny jest pełny obieg.')
        cells = set(map(tuple, (source['latest'] or {}).get('cells', [])))
        cells.update(map(tuple, (source.get('terrain') or {}).get('sectors', [])))
        area = dict(cells=cells, drafts=[source['id']])
    elif key.startswith('manual:'):
        from manual_hunts import discoveries, remember_check
        area = next(iter(discoveries(atlas,key)),None)
        if not area:
            raise ValueError('Wykryte expowisko zmieniło się. Wczytaj obszary ponownie.')
    else:
        area = next((a for a in info['areas'] if a['id']==key), None)
        if not area:
            raise ValueError('Nie znaleziono expowiska.')
    cells = set(map(tuple,area['cells']))
    skip = body.get('excluded',[])
    if not isinstance(skip,list) or not all(position(p) and tuple(p) in cells for p in skip):
        raise ValueError('Można pomijać tylko sektory wybranego expowiska.')
    excluded = frozenset(map(tuple,skip))
    allowed = None
    via = frozenset()
    if body.get('via', []) != [] and 'selected' not in body:
        raise ValueError('Łącznik wymaga zaznaczonego terenu.')
    if 'selected' in body:
        allowed = checked_sectors(body['selected'])
        if not allowed <= cells or excluded:
            raise ValueError('Wybór musi zawierać wyłącznie sektory tego expowiska.')
        cells = allowed
        via = checked_connector(body.get('via', []), cells)
        allowed = cells | via
    else:
        cells -= excluded
    if not cells:
        raise ValueError('Pozostaw co najmniej jeden sektor w pętli.')
    compare = body.get('compare', False)
    if type(compare) is not bool or compare and (allowed is None or 'approach' in body):
        raise ValueError('Do porównania pętli wybierz sektory expowiska.')
    model = None
    if compare:
        from hunt_comparison import comparison_model
        sources = [d for d in info['drafts'] if d['id'] in area['drafts']] if info else []
        model = comparison_model(atlas, area, sources, cells, observations, manual_sources=manual_sources)
    anchor = body.get('loop_start')
    if anchor is not None and (not position(anchor) or sector(anchor) not in cells|excluded):
        raise ValueError('Początek pętli leży poza wybranym expowiskiem.')
    if key.startswith('manual:') and 'approach' not in body:
        result = manual_circuit(atlas,observations,area,excluded=excluded,anchor=anchor,allowed=allowed,via=via,model=model)
        if allowed is None and not excluded and anchor is None:
            remember_check(atlas,area,result)
        return compact_proposal(atlas, observations, result, model)
    observations = atlas.with_recorded_ropes(observations)
    ground = GroundMap(atlas,observations,excluded,allowed=None if 'approach' in body else allowed)
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
        return simulated(atlas, observations, [(a,None), *route],excluded)

    first_seen = Counter()
    evidence = [d for d in info['drafts'] if d['id'] in area['drafts']] + manual_sources
    for source in evidence:
        for mark in source['sightings']:
            if tuple(mark['observer_cell']) in cells and sector(mark['point']) in cells:
                first_seen[tuple(mark['point'])] += mark['count']
    seeds = [tuple(p) for d in atlas.drafts() if d['id'] in area['drafts'] for p in d['points']]
    result = proposed_circuit(atlas, observations, ground, cells, first_seen, seeds, choose_region=allowed is not None, anchor=anchor, model=model)
    return compact_proposal(atlas, observations, result, model)


def manual_circuit(atlas, observations, area, *, excluded=frozenset(),anchor=None,allowed=None,via=frozenset(),model=None):
    cells=allowed-via if allowed is not None else set(map(tuple,area['cells']))-excluded
    counts=Counter()
    for mark in area['sightings']:
        if tuple(mark['observer_cell']) in cells and sector(mark['point']) in cells:
            counts[tuple(mark['point'])]+=mark['count']
    rows=atlas.with_recorded_ropes(observations)
    return proposed_circuit(atlas,rows,GroundMap(atlas,rows,excluded,allowed),
                   cells,counts,[tuple(area['seed'])],choose_region=True,anchor=anchor,model=model)


def unconfirmed_floor_crossings(observations, ground, cells, result):
    """Locate recorded floor changes worth revisiting, without making them navigable."""
    missing_floors = {p[2] for p in cells} - {p[2] for p in result['path']}
    if not missing_floors:
        return []
    known = {(p[2], q[2]) for p, links in ground.links.items() for q in links}
    candidates = {}
    for row in observations:
        a, b = row.get('origin'), row.get('exit')
        if row.get('evidence') != 'review' or not position(a) or not position(b):
            continue
        pair = (a[2], b[2])
        if abs(a[2]-b[2]) != 1 or pair in known or not missing_floors.intersection(pair):
            continue
        spot = row.get('entry') or a
        if not all(sector(p) in ground.allowed for p in (a, spot, b)):
            continue
        rank = (row.get('entry') is not None, row.get('count', 1), row.get('last_seen', 0))
        if pair not in candidates or rank > candidates[pair][0]:
            candidates[pair] = (rank, dict(point=spot, to_floor=b[2], exact=row.get('entry') is not None))
    return [candidates[pair][1] for pair in sorted(candidates)]


def proposed_circuit(atlas, observations, ground, cells, sightings, seeds, *, choose_region, anchor, model=None):
    current = circuit(atlas, observations, ground, cells, sightings, seeds,
                      choose_region=choose_region, anchor=anchor, model=model)
    if ground.allowed is None or not current['missing']:
        return current

    current['floor_checks'] = unconfirmed_floor_crossings(observations, ground, cells, current)

    def preview(allowed):
        terrain = GroundMap(atlas, observations, allowed=allowed)
        return circuit(atlas, observations, terrain, cells, sightings, seeds,
                       anchor=current['entrance'])

    # An optional preview must never invalidate the usable part of a route.
    zone = connector_zone(cells)
    try:
        broad = preview(zone)
    except ValueError:
        return current
    missing = set(current['missing'])
    if not set(broad['missing']) < missing:
        return current
    footprint = {sector(p) for p in broad['path']}
    for passage in broad['passages']:
        footprint.update(sector(passage[key]) for key in ('origin','entry','exit'))
    extra = footprint - ground.allowed
    if not extra:
        return current
    try:
        candidate = preview(ground.allowed | extra)
    except ValueError:
        return current
    uncovered = set(candidate['missing'])
    if not uncovered < missing:
        return current
    # A broad search can also take an incidental shortcut through an unselected
    # sector. Offer only the terrain needed for the demonstrated coverage.
    stair_sectors = {sector(t['entry']) for t in candidate['passages']}
    for cell in sorted(extra,key=lambda p:(p in stair_sectors,p))[:8]:
        if len(extra) == 1:
            break
        reduced = extra-{cell}
        try:
            trial = preview(ground.allowed | reduced)
        except ValueError:
            continue
        if set(trial['missing']) <= uncovered:
            extra, candidate, uncovered = reduced, trial, set(trial['missing'])
    current['connector'] = dict(sectors=sorted(extra), covered=candidate['covered'],
                                passages=[t for t in candidate['passages'] if t['origin'][2] != t['exit'][2]
                                          or sector(t['entry']) in extra])
    return current


def round_trip_regions(graph, reverse):
    """Strongly connected components of the directed, in-area navigation graph."""
    finished, seen = [], set()
    for root in graph:
        if root in seen:
            continue
        seen.add(root)
        stack = [(root, iter(graph[root]))]
        while stack:
            p, edges = stack[-1]
            edge = next(edges, None)
            if edge is None:
                finished.append(p)
                stack.pop()
            elif edge[0] not in seen:
                q = edge[0]
                seen.add(q)
                stack.append((q,iter(graph[q])))
    seen.clear()
    for root in reversed(finished):
        if root in seen:
            continue
        region, pending = {root}, [root]
        seen.add(root)
        while pending:
            for q in reverse.get(pending.pop(), ()):
                if q not in seen:
                    seen.add(q)
                    region.add(q)
                    pending.append(q)
        yield region


def circuit(atlas, observations, ground, cells, first_seen, seeds, *, choose_region=False,anchor=None,model=None):
    travel = ground.allowed if ground.allowed is not None else cells
    ordinary = {(x,y,z) for sx,sy,z in sorted(travel) for x in range(sx,sx+SIDE)
                for y in range(sy,sy+SIDE) if ground.clear((x,y,z))}
    hunting_tiles = {p for p in ordinary if sector(p) in cells}
    if not hunting_tiles:
        raise ValueError('W granicach expowiska brakuje zapisanych przechodnich pól.')
    start = next((p for p in seeds if p in hunting_tiles), min(hunting_tiles))
    if anchor is not None:
        nearby=(p for p in hunting_tiles if p[2]==anchor[2])
        kept=min(nearby,key=lambda p:((p[0]-anchor[0])**2+(p[1]-anchor[1])**2,p),default=None)
        if kept is not None:
            start,choose_region=kept,False
        else:
            choose_region=True
    # An explicit action may land on the gate itself. Only its directed edge
    # permits entering it. The shared simulator checks it again before saving.
    # Use the two player positions for the boundary. A ladder or rope tile
    # clicked from inside the sector can be just beyond its border.
    webs = {(x,y,z) for sx,sy,z in travel for x in range(sx,sx+SIDE) for y in range(sy,sy+SIDE)
            if (x,y,z) not in ordinary and ground.web((x,y,z))}
    nodes = ordinary | webs | {q for p,links in ground.links.items() for q in links
                         if sector(p) in travel and sector(q) in travel}
    graph, reverse = {}, defaultdict(list)
    for p in sorted(nodes):
        graph[p] = [(q, w if sector(q) in cells else w+60, link)
                    for q,w,link in ground.neighbors(p) if q in nodes]
        for q,_,_ in graph[p]:
            reverse[q].append(p)
    regions = round_trip_regions(graph, reverse)
    if choose_region:
        # An XP sample can fall on the approach or in an isolated room.
        # Compare whole circuits once, without running a path search per seed.
        def score(region):
            fields = region & hunting_tiles
            return (len({p[2] for p in fields}), len({sector(p) for p in fields}), sum(first_seen[p] for p in fields),
                    start in fields, len(fields), min(fields))
        candidates = (region for region in regions if region & hunting_tiles and len(region)>=2)
        accessible = max(candidates,key=score,default=None)
        if accessible is None:
            raise ValueError('Brak drugiego dostępnego pola z potwierdzonym powrotem.')
        if start not in accessible:
            start = min(hunting_tiles & accessible,key=lambda p:(-first_seen[p],p))
    else:
        # Authored grounds keep their explicitly chosen entrance.
        accessible = next(region for region in regions if start in region)
    by_sector = defaultdict(list)
    for p in sorted(hunting_tiles & accessible):
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
    protected={i for i,(p,_) in enumerate(walk) if goals.get(p,0)>0}
    for i,(_,link) in enumerate(walk):
        if link:protected.update((i-1,i))
    path,kept=shorten_sweeps([p for p,_ in walk],ground.clear,protected=protected)
    result=None
    selected_walk=walk
    if len(path)<len(walk) and len(set(path))>1:
        actions={kept[i]:link for i,(_,link) in enumerate(walk) if link}
        try:
            compact_walk=[(p,actions.get(i)) for i,p in enumerate(path)]
            trial=simulated(atlas,observations,compact_walk,ground.excluded,ground.allowed)
            visited={sector(p) for p in trial['path']}
            if set(by_sector)<=visited<=travel:
                result,selected_walk=trial,compact_walk
        except ValueError:
            pass  # An ambiguous shortcut leaves the original verified walk available.
    if result is None:
        result=simulated(atlas,observations,walk,ground.excluded,ground.allowed)
    result=varied_route(atlas,observations,ground,selected_walk,{p for p in goals if goals[p]},result)
    visited = {sector(p) for p in result['path']}
    if visited-travel or set(by_sector)-visited:
        raise ValueError('Symulacja zmieniła pokrycie obszaru. Sprawdź granice expowiska.')
    full = dict(**result, entrance=start, covered=len(visited & cells), total=len(cells), via=sorted(travel-cells),excluded=sorted(ground.excluded),
                selected=sorted(cells), missing=sorted(cells-visited), sightings=sum(first_seen.values()))
    if model is not None:
        from hunt_comparison import compare_routes
        full['comparison'] = compare_routes(atlas, observations, ground, graph, goals, walk, full, model)
    return full
