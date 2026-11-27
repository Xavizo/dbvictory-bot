"""Example routes shipped with their map (config/starter-map.json.gz)."""
import gzip
import json
import sqlite3

from dbv.brain.navigation import starter_map
from dbv.brain.navigation.map_atlas import MapAtlas
from dbv.brain.navigation.transition_learning import TransitionLearner

SCOPE = "a" * 64


def shipped():
    return starter_map.load()


def test_the_shipped_pack_holds_the_routes_and_everything_they_link_to():
    pack = shipped()
    names = {d["name"] for d in pack["drafts"]}
    assert len(names) == 11
    assert {"Monars - Garlic castle", "ice island shenlong castle", "Saiyan - Vegeta East"} <= names
    known = {o[0] for o in pack["observations"]} | {r[0] for r in pack["recorded_ropes"]}
    for draft in pack["drafts"]:
        for link in json.loads(draft["links"] or "[]"):
            # Door IDs are derived from the shipped tiles; everything else is a shipped record.
            assert link["id"].startswith("door-") or link["id"] in known, (draft["name"], link["id"])
    for _, raw in pack["observations"]:
        assert set(json.loads(raw)) <= {"item_id", "tool_id", "entry_items", "origin", "entry", "exit", "action",
                                        "evidence", "reason", "direction", "id", "count", "first_seen",
                                        "last_seen", "object_change", "prepared_item_id"}
    for tile in pack["tiles"][:5000]:
        assert set(json.loads(tile[3])) <= {"x", "y", "z", "kind", "items", "navigation", "transitions",
                                            "minimap_color", "web_cut"}


def small_pack(tmp_path):
    pack = shipped()
    pack["tiles"] = pack["tiles"][:50]
    path = tmp_path / "pack.json.gz"
    path.write_bytes(gzip.compress(json.dumps(pack).encode()))
    return pack, path


def test_a_fresh_scope_on_the_same_server_gets_runnable_examples(tmp_path):
    pack, path = small_pack(tmp_path)
    data = tmp_path / "local_data"
    assert starter_map.apply(data, SCOPE, pack["server"], path) is True
    atlas = MapAtlas(data, SCOPE)
    assert {d["name"] for d in atlas.drafts()} == {d["name"] for d in pack["drafts"]}
    assert atlas.total == 50
    learner = TransitionLearner(data, SCOPE)
    assert not learner.storage_error and len(learner.records) == len(pack["observations"])
    assert starter_map.apply(data, SCOPE, pack["server"], path) is False  # once per scope


def test_another_server_gets_nothing(tmp_path):
    _, path = small_pack(tmp_path)
    data = tmp_path / "local_data"
    assert starter_map.apply(data, SCOPE, "127.0.0.1:7171", path) is False
    assert not (data / f"map_atlas-{SCOPE[:16]}.sqlite3").exists()


def test_the_players_own_data_wins_and_a_deleted_example_stays_deleted(tmp_path):
    pack, path = small_pack(tmp_path)
    data = tmp_path / "local_data"
    atlas = MapAtlas(data, SCOPE)
    own = atlas.save_draft("Własna", [[1, 1, 7], [2, 2, 7]])
    x, y, z, raw, _ = pack["tiles"][0]
    mine = json.dumps({**json.loads(raw), "kind": "avoid"})
    with sqlite3.connect(atlas.path) as db:
        db.execute("INSERT INTO tiles VALUES (?,?,?,?,?)", (x, y, z, mine, 1.0))
        example = pack["drafts"][0]
        db.execute("INSERT INTO drafts (id,name,points,updated,deleted) VALUES (?,?,?,?,1)",
                   (example["id"], example["name"], example["points"], 1.0))
    assert starter_map.apply(data, SCOPE, pack["server"], path) is True
    with sqlite3.connect(atlas.path) as db:
        assert db.execute("SELECT data FROM tiles WHERE x=? AND y=? AND z=?", (x, y, z)).fetchone()[0] == mine
        assert db.execute("SELECT deleted FROM drafts WHERE id=?", (example["id"],)).fetchone()[0] == 1
        assert db.execute("SELECT name FROM drafts WHERE id=?", (own,)).fetchone()[0] == "Własna"


def test_a_broken_pack_never_blocks_the_map(tmp_path):
    broken = tmp_path / "broken.gz"
    broken.write_bytes(b"not gzip")
    assert starter_map.apply(tmp_path / "local_data", SCOPE, "x:1", broken) is False


def test_every_example_plans_completely_on_a_fresh_installation(tmp_path):
    pack = shipped()
    data = tmp_path / "local_data"
    assert starter_map.apply(data, SCOPE, pack["server"])
    atlas = MapAtlas(data, SCOPE)
    observations = list(TransitionLearner(data, SCOPE).records.values())
    for draft in atlas.drafts():
        assert "terrain" not in draft or draft["terrain"]["ground"] == "world"  # no author-only hunt grounds
        plan = atlas.plan(draft["points"], observations, loop=draft["loop"], links=draft["links"])
        assert plan["complete"] and not plan["gaps"], draft["name"]
