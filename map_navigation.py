"""Navigation labels are separate from collision and never authorize movement.

LensHelp meanings follow the DAT help codes documented in the author's reader:
https://blackdtools.com/tibiadat86.php (0x044C..0x0456).
DBV may reuse metadata, so these are client hints, not server behavior proofs.
"""

import json
from pathlib import Path

CATALOGUE_PATH = Path(__file__).with_name("map_navigation_types.json")
ASSET_SHA256 = "26e6342c67378183dd422d3a044a7ef159e27ebfe2af1594fae8e0a35f200c91"
SUPPORTED_KINDS = {"stairs", "ladder", "hole", "descent", "covered_hole", "rope_spot", "door", "level_gate", "teleport", "web"}
_catalogue_key = None
_catalogue_value = {}
_location_value = {}


def load_confirmed_types(path=CATALOGUE_PATH, asset_sha256=ASSET_SHA256):
    """Reload bounded data only when changed; never execute code or send commands."""
    global _catalogue_key, _catalogue_value, _location_value
    try:
        path = Path(path)
        stat = path.stat()
        key = (str(path.resolve()), stat.st_mtime_ns, stat.st_size, asset_sha256)
        if key == _catalogue_key:
            return _catalogue_value
        if stat.st_size > 65536:
            raise ValueError("catalogue too large")
        with path.open("rb") as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise ValueError("catalogue too large")
        document = json.loads(raw)
        if document.get("version") != 1:
            raise ValueError("wrong asset or schema")
        if document.get("asset_sha256") != asset_sha256:
            document = document.get("profiles", {}).get(asset_sha256, {})
        if document.get("asset_sha256") != asset_sha256:
            raise ValueError("wrong asset")
        entries = document.get("types")
        if not isinstance(entries, dict) or len(entries) > 512:
            raise ValueError("invalid type collection")
        result = {}
        for item_id, entry in entries.items():
            if not item_id.isdecimal() or not 100 <= int(item_id) <= 65535:
                raise ValueError("invalid item ID")
            if not isinstance(entry, dict) or entry.get("kind") not in SUPPORTED_KINDS:
                raise ValueError("invalid navigation type")
            # Only validated type names cross into the API. Notes are provenance, not instructions.
            result[int(item_id)] = {"kind": entry["kind"]}
            if entry["kind"] == "rope_spot":
                result[int(item_id)].update(activation_hint="rope_or_fly_up", direction_hint="up")
            if entry["kind"] == "web":
                result[int(item_id)]["activation_hint"] = "aio"
            if entry["kind"] == "covered_hole":
                result[int(item_id)].update(activation_hint="shovel", direction_hint="down")
        locations = document.get("locations",{})
        if not isinstance(locations,dict) or len(locations)>512:
            raise ValueError("invalid location notes")
        notes = {}
        for key_pos, entry in locations.items():
            components = key_pos.split(",")
            if len(components)!=3 or not all(v.isdecimal() for v in components):
                raise ValueError("invalid location")
            xyz = tuple(int(v) for v in components)
            if not (0<=xyz[0]<65535 and 0<=xyz[1]<65535 and 0<=xyz[2]<16):
                raise ValueError("invalid coordinates")
            if (not isinstance(entry,dict) or entry.get("kind")!="level_gate" or
                    type(entry.get("item_id")) is not int or not 100<=entry["item_id"]<=65535 or
                    type(entry.get("required_level")) is not int or not 1<=entry["required_level"]<=100000):
                raise ValueError("invalid gate note")
            notes[xyz] = {k:entry[k] for k in ("kind","item_id","required_level")}
            notes[xyz]["requirement_source"] = "user_report"
        _catalogue_key, _catalogue_value = key, result
        _location_value = notes
        return result
    except (OSError, ValueError, TypeError, AttributeError):
        # Do not reuse possibly wrong labels after an invalid or incompatible edit.
        _catalogue_key, _catalogue_value, _location_value = None, {}, {}
        return {}


def load_location_notes(path=CATALOGUE_PATH, asset_sha256=ASSET_SHA256):
    load_confirmed_types(path, asset_sha256)
    return _location_value

LENS_HELP_TYPES = {
    1100: {"kind": "ladder", "activation_hint": "use", "direction_hint": "up"},
    1101: {"kind": "descent", "activation_hint": "use"},
    1102: {"kind": "rope_spot", "activation_hint": "rope", "direction_hint": "up"},
    1104: {"kind": "door"},
    1105: {"kind": "door", "lock_hint": True},
    1106: {"kind": "stairs", "activation_hint": "step", "direction_hint": "up"},
    1110: {"kind": "covered_hole", "activation_hint": "shovel", "direction_hint": "down"},
}


def navigation_features(items, confirmed_types=None):
    """Return per-object annotations, keeping ground/decorative layers distinct."""
    features = []
    known = load_confirmed_types() if confirmed_types is None else confirmed_types
    for item in items:
        definition = known.get(item["id"])
        source = "confirmed_id"
        if definition is None:
            definition = LENS_HELP_TYPES.get(item.get("lens_help"))
            source = "client_hint"
        if definition is None and "floor_change" in item["flags"]:
            definition, source = {"kind": "floor_change"}, "client_hint"
        if (definition is None and item.get("minimap_color") == 210
                and {"not_moveable", "not_pathable"}.issubset(item["flags"])
                and "not_walkable" not in item["flags"]):
            # Observed on DBV stairs/hole: an uncertain candidate, never a traversability rule.
            definition, source = {"kind": "passage"}, "client_hint"
        if definition is None:
            continue
        feature = {**definition, "item_id": item["id"], "stack": item.get("stack", 0), "source": source}
        if feature["kind"] in {"door", "level_gate"}:
            # Static type flags do not prove the server's opening/closing rules.
            feature["door_state_hint"] = "closed" if "not_walkable" in item["flags"] else "open"
            feature.setdefault("closing_policy", "unknown")
        features.append(feature)
    return features
