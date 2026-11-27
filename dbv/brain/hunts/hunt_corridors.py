"""Prefer a different corridor on a return leg when the mapped terrain permits it."""
from collections import Counter
import heapq


def patch(p):
    return (p[0] // 4, p[1] // 4, p[2])


def boundary(a, b):
    return (a, b) if a < b else (b, a)


def crossings(path):
    cells = list(map(patch, path))
    return Counter(boundary(a, b) for a, b in zip(cells, cells[1:]) if a != b and a[2] == b[2])


def repeat_count(path):
    counts = crossings(path)
    return sum(counts.values()) - len(counts)


def _alternative(start, end, clear, used, limit):
    def distance(p):
        return max(abs(p[0]-end[0]), abs(p[1]-end[1]))

    queue = [(distance(start)*10, 0, 0, start)]
    costs = {start: 0}
    parents = {}
    while queue:
        _, cost, length, p = heapq.heappop(queue)
        if cost != costs[p]:
            continue
        if p == end:
            leg = [p]
            while p != start:
                p = parents[p]
                leg.append(p)
            return leg[::-1]
        cell = patch(p)
        for dx, dy in ((0,-1),(1,0),(0,1),(-1,0),(1,1),(1,-1),(-1,1),(-1,-1)):
            q = (p[0]+dx, p[1]+dy, p[2])
            remaining = distance(q)
            if length+1+remaining > limit or not clear(q):
                continue
            other = patch(q)
            penalty = 35 if cell != other and used[boundary(cell,other)] > 0 else 0
            total = cost + (14 if dx and dy else 10) + penalty
            if total < costs.get(q, float('inf')):
                costs[q], parents[q] = total, p
                heapq.heappush(queue,(total+remaining*10,total,length+1,q))
    return None


def separate_corridors(walk, clear, *, pinned, coverage):
    """Keep action legs, designated stops and all visited coverage cells."""
    path = [p for p,_ in walk]
    used = crossings(path)
    visits = Counter(map(coverage,path))
    stops = {0, len(walk)-1, *pinned}
    for i, (_, action) in enumerate(walk):
        if action:
            stops.update((i-1,i))
    # Limit the search around a return leg, even for very long straight paths.
    ordered = sorted(stops)
    stops.update(i for a,b in zip(ordered,ordered[1:]) for i in range(a+20,b,20))
    ordered = sorted(stops)
    out = [walk[0]]
    indices = {0:0}
    extra = 0
    for a,b in zip(ordered,ordered[1:]):
        old = path[a:b+1]
        chosen = None
        if b-a >= 2 and old[0] != old[-1] and old[0][2] == old[-1][2] and clear(old[0]) and clear(old[-1]):
            before = crossings(old)
            if any(used[e] > 1 for e in before):
                used.subtract(before)
                allowance = min(3, max(0,(len(path)-1)//25-extra))
                alt = _alternative(old[0],old[-1],clear,used,b-a+allowance)
                if alt and len(alt)-1 <= b-a+allowance:
                    after = crossings(alt)
                    # With the rest of the loop fixed, the first traversal of
                    # an unused boundary is free; all further crossings repeat.
                    old_cost = sum(n - (used[e]==0) for e,n in before.items())
                    new_cost = sum(n - (used[e]==0) for e,n in after.items())
                    removed = Counter(map(coverage,old[1:-1]))
                    removed.subtract(map(coverage,alt[1:-1]))
                    if new_cost < old_cost and all(visits[k]>n for k,n in removed.items() if n>0):
                        chosen = alt
                        visits.subtract(removed)
                        used.update(after)
                        extra += len(alt)-len(old)
                if chosen is None:
                    used.update(before)
        out.extend(((p,None) for p in chosen[1:]) if chosen else walk[a+1:b+1])
        indices[b] = len(out)-1
    return out, indices
