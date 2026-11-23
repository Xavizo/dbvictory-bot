"""Correlate an observed manual step with a separately seen landing on the next floor."""
from transition_learning import distance, position


class FloorStepWitness:
    def __init__(self):
        self.request = self.landing = self.map_position = self.coverage = None

    def clear(self):
        self.request = self.landing = self.map_position = None

    def feed(self, event):
        kind, ms = event['event'], event['ms']
        if kind == 'observer_totals':
            coverage = event.get('dropped'), event.get('failures')
            if self.coverage is not None and self.coverage != coverage:
                self.clear()
            self.coverage = coverage
        if kind in {'server_cancel', 'walk_stopped', 'observation_gap', 'position_unconfirmed', 'interaction'}:
            self.clear()
            return None

        if self.request and not 0 <= ms - self.request[2] <= 1400:
            self.clear()
        if kind == 'walk_request':
            origin, target = event.get('origin'), event.get('target')
            manual = event.get('source') == 'client' and position(origin) and position(target) and distance(origin, target) == 1
            witnessed = None
            if manual and self.landing and tuple(origin) == self.landing[0] and ms - self.landing[1] <= 700:
                witnessed = (*self.request[:2], self.landing[0])
            self.clear()
            if manual:
                self.request = tuple(origin), tuple(target), ms
            return witnessed
        if self.request is None:
            return None
        origin, entry, started = self.request
        pos = event.get('position')
        if kind not in {'position_after_request','map_after_request','map_position'} or not position(pos):
            return None
        pos = tuple(pos)
        if kind != 'map_position' and tuple(event.get('target') or ()) != entry:
            self.clear()
            return None
        if kind == 'position_after_request':
            if (ms - started > 700 or abs(ms - started - event.get('elapsed_ms', -10000)) > 2
                    or abs(pos[2] - entry[2]) != 1 or max(abs(pos[k] - entry[k]) for k in (0,1)) > 1):
                self.clear()
                return None
            self.landing = pos, ms
        elif pos != origin:
            if self.landing and (pos != self.landing[0] or ms - self.landing[1] > 700):
                self.clear()
                return None
            self.map_position = pos
        if self.landing and self.map_position == self.landing[0]:
            self.clear()
            return origin, entry, pos
        return None


def observed_stair(origin, entry, dest, tile):
    """Only a step-operated object can explain the floor change. Never infer a reverse edge."""
    if not tile:
        return None
    navigation = tile.get('navigation', ())
    if not navigation or any(f.get('kind') not in {'stairs','passage','hole','descent'}
                             or f.get('activation_hint') not in (None, 'step')
                             for f in navigation):
        return None
    ids = {f.get('item_id') for f in navigation}
    if len(ids) != 1 or None in ids or not ids <= {i['id'] for i in tile.get('items', ())}:
        return None
    direction = 'up' if dest[2] < origin[2] else 'down'
    if any(f.get('direction_hint') not in (None, direction) for f in navigation):
        return None
    return dict(origin=list(origin), entry=list(entry), exit=list(dest), action='step',
                item_id=None, tool_id=None, entry_items=sorted(ids), direction=direction,
                evidence='observed', reason='confirmed_step')
