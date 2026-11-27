"""Sharing a route with its map as a .dbvroute file (dbv/brain/navigation/route_pack.py)."""
import json
import sqlite3

import pytest

from test_dashboard_security import http_api  # noqa: F401  (fixture)

from dbv.brain.navigation import route_pack, starter_map
from dbv.brain.navigation.map_atlas import MapAtlas
from dbv.brain.navigation.transition_learning import TransitionLearner

AUTHOR, PLAYER = "a" * 64, "b" * 64


@pytest.fixture(scope="module")
def author(tmp_path_factory):
    data = tmp_path_factory.mktemp("author")
    server = starter_map.load()["server"]
    assert starter_map.apply(data, AUTHOR, server)
    atlas = MapAtlas(data, AUTHOR)
    with sqlite3.connect(atlas.path) as db:
        db.execute("UPDATE recorded_ropes SET recording='Mateusz hunt 3'")
    atlas = MapAtlas(data, AUTHOR)
    return atlas, TransitionLearner(data, AUTHOR), server


def exported(author, name):
    atlas, learner, server = author
    draft = next(d for d in atlas.drafts() if d["name"] == name)
    if not any(draft["labels"]):
        labels = ["Notatka gracza"] + draft["labels"][1:]
        atlas.save_draft(draft["name"], draft["points"], draft["id"], draft["loop"], draft["updated"], labels, draft["links"])
    return route_pack.export(atlas, learner.records.values(), draft["id"], server)


def player(tmp_path):
    return MapAtlas(tmp_path, PLAYER), TransitionLearner(tmp_path, PLAYER)


@pytest.mark.parametrize("name", ["Fury cell chibi island", "Monars - Garlic castle"])
def test_an_imported_route_plans_completely_on_a_fresh_installation(author, tmp_path, name):
    pack = json.loads(json.dumps(exported(author, name)))  # as written to and read from the file
    atlas, learner = player(tmp_path)
    [draft_id] = route_pack.import_pack(atlas, learner, pack, author[2])
    draft = next(d for d in atlas.drafts() if d["id"] == draft_id)
    assert draft["name"] == name
    plan = atlas.plan(draft["points"], list(learner.records.values()), loop=draft["loop"], links=draft["links"])
    assert plan["complete"] and not plan["gaps"]
    # Restarting the bot reads the same data back.
    again = TransitionLearner(tmp_path, PLAYER)
    assert not again.storage_error and again.records.keys() == learner.records.keys()


def test_export_leaves_out_the_authors_notes_and_names(author):
    pack = exported(author, "Fury cell chibi island")
    assert pack["format"] == "dbvictory-route" and pack["complete"]
    [draft] = pack["drafts"]
    assert set(json.loads(draft["labels"])) == {""}
    assert pack["recorded_ropes"] and {r[1] for r in pack["recorded_ropes"]} == {"shared"}
    assert "Mateusz" not in json.dumps(pack)
    assert draft["id"] != next(d["id"] for d in author[0].drafts() if d["name"] == draft["name"])
    for tile in pack["tiles"]:
        assert set(json.loads(tile[3])) <= route_pack.TILE_KEYS


def test_import_never_overwrites_the_players_data(author, tmp_path):
    pack = exported(author, "Saiyan - Vegeta East")
    atlas, learner = player(tmp_path)
    own = atlas.save_draft("Saiyan - Vegeta East", [[1, 1, 7], [2, 2, 7]])
    x, y, z, raw, _ = pack["tiles"][0]
    mine = json.dumps({**json.loads(raw), "kind": "avoid"})
    with sqlite3.connect(atlas.path) as db:
        db.execute("INSERT INTO tiles VALUES (?,?,?,?,?)", (x, y, z, mine, 1.0))
    first = route_pack.import_pack(atlas, learner, pack, author[2])
    second = route_pack.import_pack(atlas, learner, pack, author[2])
    names = {d["id"]: d["name"] for d in atlas.drafts()}
    assert names[own] == "Saiyan - Vegeta East" and next(d for d in atlas.drafts() if d["id"] == own)["points"] == [[1, 1, 7], [2, 2, 7]]
    assert names[first[0]] == "Saiyan - Vegeta East (2)" and names[second[0]] == "Saiyan - Vegeta East (3)"
    with sqlite3.connect(atlas.path) as db:
        assert db.execute("SELECT data FROM tiles WHERE x=? AND y=? AND z=?", (x, y, z)).fetchone()[0] == mine
    # The learner still owns its store: later observations save normally.
    row = dict(next(iter(learner.records.values())))
    learner._save(row)
    assert learner.storage_error is None


def test_another_server_and_broken_files_change_nothing(author, tmp_path):
    pack = exported(author, "Fury cell chibi island")
    atlas, learner = player(tmp_path)
    with pytest.raises(ValueError, match="innego serwera"):
        route_pack.import_pack(atlas, learner, pack, "127.0.0.1:7171")
    broken = json.loads(json.dumps(pack))
    broken["observations"][0][1] = json.dumps({"id": "x"})
    for bad in (broken, {"version": 2}, {**pack, "tiles": [[1, 2, 3, "{}", 0]]}, None):
        with pytest.raises(ValueError, match="prawidłowy plik"):
            route_pack.import_pack(atlas, learner, bad, author[2])
    assert atlas.drafts() == [] and learner.records == {}
    with sqlite3.connect(atlas.path) as db:
        assert db.execute("SELECT COUNT(*) FROM tiles").fetchone()[0] == 0


def test_file_names_are_safe():
    assert route_pack.file_name("Saiyan - Vegeta East") == "Saiyan - Vegeta East.dbvroute"
    assert route_pack.file_name("..\\x/y:z") == "_x_y_z.dbvroute"
    assert route_pack.file_name("Łowisko · szybka (2)") == "Łowisko _ szybka _2_.dbvroute"


def test_the_panel_exports_and_imports_through_the_dashboard_api(author, http_api, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from dbv.mcp_server import dashboard_api as api
    from dbv.mcp_server.dashboard_security import DASHBOARD_ORIGIN, SESSION_TOKEN
    atlas, learner, server = author
    request, _ = http_api
    headers = dict(Origin=DASHBOARD_ORIGIN, Authorization=f"Bearer {SESSION_TOKEN}")
    monkeypatch.setattr(api, "_state", SimpleNamespace(passive_map_monitor=SimpleNamespace(atlas=atlas, learner=learner, server=server)))
    draft = next(d for d in atlas.drafts() if d["name"] == "Fury cell chibi island")
    status, _, data = request("GET", f"/api/map/drafts/export?id={draft['id']}", None, **headers)
    shared = json.loads(data)
    assert status == 200 and shared["file_name"] == "Fury cell chibi island.dbvroute"
    mine, own_learner = player(tmp_path)
    monkeypatch.setattr(api, "_state", SimpleNamespace(passive_map_monitor=SimpleNamespace(atlas=mine, learner=own_learner, server=server)))
    status, _, data = request("POST", "/api/map/drafts/import", json.dumps({"scope": PLAYER, "pack": shared["pack"]}), **headers)
    result = json.loads(data)
    assert status == 200, result
    assert [d["name"] for d in result["drafts"] if d["id"] in result["imported"]] == ["Fury cell chibi island"]
    status, _, data = request("POST", "/api/map/drafts/import", json.dumps({"scope": "c" * 64, "pack": shared["pack"]}), **headers)
    assert status == 400
