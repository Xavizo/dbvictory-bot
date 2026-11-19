"""Known tool functions for explicit learned edges; no process or transport I/O."""
DIG_TOOLS = frozenset({3457, 8899, 8900})
ROPE_TOOLS = frozenset({3003, 8899, 8900})


WEB_ITEM_ID = 183
AIO_TOOLS = (8899, 8900)


def cuttable_web(tile):
    """Return the exact layer only when removing it would expose ordinary ground."""
    if not tile or tile.get('kind') not in {'clear', 'blocked', 'avoid'} or tile.get('transitions'):
        return None
    features = tile.get('navigation', ())
    if not features or any(f.get('kind') != 'web' or f.get('item_id') != WEB_ITEM_ID
                           or f.get('source') != 'confirmed_id' for f in features):
        return None
    items = tile.get('items', ())
    webs = [i for i in items if i['id'] == WEB_ITEM_ID]
    if len(webs) != 1 or webs[0].get('stack') != features[0].get('stack'):
        return None
    other_flags = {f for i in items if i['id'] != WEB_ITEM_ID for f in i.get('flags', ())}
    if 'ground' not in other_flags or other_flags & {'not_walkable', 'not_pathable', 'floor_change'}:
        return None
    return webs[0]


def web_edge(origin, entry):
    return dict(origin=list(origin), entry=list(entry), exit=list(entry), item_id=WEB_ITEM_ID,
                action='clear_web', tool_id=AIO_TOOLS[0], tool_ids=list(AIO_TOOLS))


def known_item(value):
    return type(value) is int and 100 <= value <= 65535


def tool_kind(edge):
    points = [edge.get(k) for k in ('origin', 'entry', 'exit')]
    if any(not isinstance(p, (list, tuple)) or len(p) != 3 or
           any(type(v) is not int for v in p) or not 0 <= p[2] <= 15 for p in points):
        return None
    origin, entry, dest = points
    if origin[2] != entry[2] or not known_item(edge.get('item_id')):
        return None
    distance = max(abs(origin[0]-entry[0]), abs(origin[1]-entry[1]))
    if (edge.get('action') == 'clear_web' and edge['item_id'] == WEB_ITEM_ID
            and edge.get('tool_id') in AIO_TOOLS and tuple(entry) == tuple(dest) and distance == 1):
        return 'web'
    if (edge.get('action') == 'use_with' and edge.get('tool_id') in ROPE_TOOLS
            and dest[2] == origin[2]-1 and distance <= 1):
        return 'rope'
    if (edge.get('action') == 'use_with_then_step' and edge.get('tool_id') in DIG_TOOLS
            and known_item(edge.get('prepared_item_id')) and edge['prepared_item_id'] != edge['item_id']
            and dest[2] == origin[2]+1 and distance == 1):
        return 'dig'
    return None


def supported_transition(edge):
    return edge.get('action') in {'step', 'use', 'use_then_step'} or tool_kind(edge) is not None


def transition_tools(edge):
    kind = tool_kind(edge)
    if kind == "web":
        return list(AIO_TOOLS)
    family = (3003, 8899, 8900) if kind == 'rope' else (3457, 8899, 8900) if kind == 'dig' else ()
    # AiO substitutes the tool function; learned objects and exits stay fixed.
    return [edge['tool_id']] + [iid for iid in family if iid != edge['tool_id']] if family else []
