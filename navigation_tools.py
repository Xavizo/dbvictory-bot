"""Known tool functions for explicit learned edges; no process or transport I/O."""
DIG_TOOLS = frozenset({3457, 8899, 8900})
ROPE_TOOLS = frozenset({3003, 8899, 8900})


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
    family = (3003, 8899, 8900) if kind == 'rope' else (3457, 8899, 8900) if kind == 'dig' else ()
    # AiO substitutes the tool function; learned objects and exits stay fixed.
    return [edge['tool_id']] + [iid for iid in family if iid != edge['tool_id']] if family else []
