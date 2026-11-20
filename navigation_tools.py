"""Known tool functions for explicit learned edges; no process or transport I/O."""
DIG_TOOLS = frozenset({3457, 8899, 8900})
ROPE_TOOLS = frozenset({3003, 8899, 8900})


WEB_ITEM_IDS = frozenset({182, 183})
# A manual cut of web 183 was confirmed with 8900 after a failed use of 8899.
AIO_TOOLS = (8900, 8899)


def covering_body(tile):
    # Observed on DBVictory. Ordinary containers share these flags.
    return next((item for item in (tile or {}).get('items', ())
                 if item['id'] == 8506 and 'container' in item['flags']
                 and 'not_moveable' not in item['flags'] and 0 <= item['stack'] < 255), None)


def has_cut_web(items, definitions):
    # Only the verified 183 -> 189 pair is known for the current client assets.
    cut_id = (definitions or {}).get(183, {}).get('cut_id')
    return bool(cut_id and any(i['id']==cut_id for i in items)
                and not any(i['id'] in WEB_ITEM_IDS for i in items))


def cuttable_web(tile):
    """Return the exact layer only when removing it would expose ordinary ground."""
    if not tile or tile.get('kind') not in {'clear', 'blocked', 'avoid'} or tile.get('transitions'):
        return None
    features = tile.get('navigation') or ()
    if len(features) != 1 or features[0].get('kind') != 'web' or features[0].get('source') != 'confirmed_id':
        return None
    items = tile.get('items', ())
    webs = [i for i in items if i['id'] in WEB_ITEM_IDS]
    if (len(webs) != 1 or webs[0]['id'] != features[0].get('item_id')
            or webs[0].get('stack') != features[0].get('stack')):
        return None
    other_flags = {f for i in items if i['id'] not in WEB_ITEM_IDS for f in i.get('flags', ())}
    if 'ground' not in other_flags or other_flags & {'not_walkable', 'not_pathable', 'floor_change'}:
        return None
    return webs[0]


def web_edge(origin, entry, item_id=183):
    return dict(origin=list(origin), entry=list(entry), exit=list(entry), item_id=item_id,
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
    if (edge.get('action') == 'clear_web' and edge['item_id'] in WEB_ITEM_IDS
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
