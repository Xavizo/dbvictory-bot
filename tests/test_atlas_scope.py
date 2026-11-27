import json
import sqlite3

from dbv.brain.navigation.atlas_scope import LINEAGE, inherit
from dbv.brain.navigation.map_annotations import MapAnnotations
from dbv.brain.navigation.map_atlas import MapAtlas
from dbv.brain.navigation.transition_learning import TransitionLearner

OLD = "a" * 64
NEW = "b" * 64
SERVER = "game.example:7171"
NOTE = {"activation_hint": "step", "kind": "teleport", "item_id": 11946, "position": [75, 104, 7],
        "note": "", "required_level": None, "updated_at": 1794835724.3}
STAIR = {"item_id": None, "tool_id": None, "entry_items": [], "origin": [82, 106, 8], "entry": None,
         "exit": [82, 103, 7], "action": "unknown", "evidence": "review", "reason": "sample_gap",
         "direction": "up", "id": "4716ec272163e970fac8", "count": 1,
         "first_seen": 1794835763.2, "last_seen": 1794835763.2}


def _seed_old(tmp_path):
    atlas = MapAtlas(tmp_path, OLD)
    atlas.save_draft("Monars - Garlic castle", [[1, 2, 7], [3, 4, 7]])
    db = sqlite3.connect(tmp_path / f"map_transitions-{OLD[:16]}.sqlite3")
    with db:
        db.execute("CREATE TABLE store (scope TEXT PRIMARY KEY, revision INTEGER NOT NULL)")
        db.execute("INSERT INTO store VALUES (?,3)", (OLD,))
        db.execute("CREATE TABLE observations (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
        db.execute("INSERT INTO observations VALUES (?,?)", (STAIR["id"], json.dumps(STAIR)))
    db.close()
    (tmp_path / f"map_notes-{OLD[:16]}.json").write_text(
        json.dumps({"version": 1, "scope": OLD, "records": [NOTE]}), encoding="utf-8")


def test_client_update_carries_routes_stairs_and_notes_to_the_new_scope(tmp_path):
    assert inherit(tmp_path, OLD, SERVER) == []
    _seed_old(tmp_path)

    copied = inherit(tmp_path, NEW, SERVER)

    assert copied[-1] == f"map_atlas-{NEW[:16]}.sqlite3"
    assert [d["name"] for d in MapAtlas(tmp_path, NEW).drafts()] == ["Monars - Garlic castle"]
    learner = TransitionLearner(tmp_path, NEW)
    assert learner.storage_error is None and STAIR["id"] in learner.records
    notes = MapAnnotations(tmp_path, NEW)
    assert notes.error is None and len(notes.records) == 1
    # The old scope's files are left as they were.
    assert MapAtlas(tmp_path, OLD).drafts()[0]["name"] == "Monars - Garlic castle"
    assert json.loads((tmp_path / LINEAGE).read_text())["servers"][SERVER] == NEW


def test_existing_new_scope_is_never_overwritten(tmp_path):
    inherit(tmp_path, OLD, SERVER)
    _seed_old(tmp_path)
    MapAtlas(tmp_path, NEW).save_draft("today", [[5, 5, 7], [6, 6, 7]])

    assert inherit(tmp_path, NEW, SERVER) == []
    assert [d["name"] for d in MapAtlas(tmp_path, NEW).drafts()] == ["today"]


def test_other_server_or_unknown_server_starts_empty(tmp_path):
    inherit(tmp_path, OLD, SERVER)
    _seed_old(tmp_path)

    assert inherit(tmp_path, NEW, "other.example:7171") == []
    assert inherit(tmp_path, NEW, ":0") == []
    assert MapAtlas(tmp_path, NEW).drafts() == []


def test_unreadable_lineage_does_not_break_the_map(tmp_path):
    (tmp_path / LINEAGE).write_text("{not json", encoding="utf-8")
    assert inherit(tmp_path, NEW, SERVER) == []
    assert json.loads((tmp_path / LINEAGE).read_text())["servers"][SERVER] == NEW


def test_corrupted_optional_file_does_not_block_the_atlas(tmp_path):
    inherit(tmp_path, OLD, SERVER)
    _seed_old(tmp_path)
    (tmp_path / f"map_transitions-{OLD[:16]}.recovered.json").write_text("{broken", encoding="utf-8")

    copied = inherit(tmp_path, NEW, SERVER)

    assert copied[-1] == f"map_atlas-{NEW[:16]}.sqlite3"
    assert f"map_transitions-{NEW[:16]}.recovered.json" not in copied
    assert not list(tmp_path.glob("*.partial"))
    assert [d["name"] for d in MapAtlas(tmp_path, NEW).drafts()] == ["Monars - Garlic castle"]
    assert STAIR["id"] in TransitionLearner(tmp_path, NEW).records
    assert json.loads((tmp_path / LINEAGE).read_text())["servers"][SERVER] == NEW
