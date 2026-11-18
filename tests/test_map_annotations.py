import json
import time
from types import SimpleNamespace

import pytest
from map_annotations import MapAnnotations, validate_note
from passive_map import PassiveMapMonitor


def note(**changes):
    return {"position":[986,609,7],"item_id":6264,"kind":"level_gate","required_level":320,
            "activation_hint":"use","note":"Brama z wymaganym poziomem",**changes}


def cell(**changes):
    return {"x":986,"y":609,"z":7,"kind":"blocked","items":[{"id":6264,"stack":1,"flags":["not_walkable"]}],
            "navigation":[{"kind":"door","item_id":6264,"stack":1,"source":"confirmed_id"}],**changes}


@pytest.fixture
def monitor(tmp_path):
    state=SimpleNamespace(connected=True,settings={"actions":{"cavebot":{"enabled":False}}})
    m=PassiveMapMonitor(state)
    m.annotations=MapAnnotations(tmp_path,"a"*64)
    m._result={"status":"ready","center":[986,608,7],"captured_at":time.time(),"cells":[cell()]}
    return m


@pytest.mark.parametrize("changes",[{"position":[0,0,16]},{"item_id":True},{"kind":"execute"},{"kind":[]},
    {"required_level":True},{"required_level":-1},{"kind":"door"},{"activation_hint":[]},{"note":"x"*241},{"note":"line\nbreak"}])
def test_invalid_descriptions_rejected(changes):
    with pytest.raises(ValueError):validate_note(note(**changes))


def test_local_description_persists_without_relabeling_other_objects(monitor):
    m=monitor
    m.save_annotation({**note(),"scope":m.annotations.scope,"operation":"save"})
    reopened=MapAnnotations(m.annotations.path.parent,m.annotations.scope)
    cells=reopened.annotate([cell(),cell(x=987),cell(items=[{"id":6265,"stack":1,"flags":[]}])])
    feature=cells[0]["navigation"][0]
    assert feature["kind"]=="level_gate" and feature["required_level"]==320
    assert feature["source"]=="user_annotation" and feature["note"]==note()["note"]
    assert cells[0]["kind"]=="blocked"
    assert cells[1]["navigation"][0]["source"]=="confirmed_id"
    assert cells[2]["navigation"][0]["source"]=="confirmed_id"
    assert m.state.settings=={"actions":{"cavebot":{"enabled":False}}}
    assert m.learner is None  # Description is not a successful transition.
    m.save_annotation({**note(),"scope":m.annotations.scope,"operation":"remove"})
    assert m.annotations.annotate([cell()])[0]["navigation"][0]["kind"]=="door"


@pytest.mark.parametrize("mode",["disconnected","stale","wrong_scope","missing_item","unknown_tile"])
def test_stale_or_mismatched_map_never_written(monitor,mode):
    body={**note(),"scope":monitor.annotations.scope,"operation":"save"}
    if mode=="disconnected":monitor.state.connected=False
    if mode=="stale":monitor._result["captured_at"]-=5
    if mode=="wrong_scope":body["scope"]="b"*64
    if mode=="missing_item":monitor._result["cells"][0]["items"]=[]
    if mode=="unknown_tile":monitor._result["cells"][0]["kind"]="unknown"
    with pytest.raises(ValueError):monitor.save_annotation(body)
    assert not monitor.annotations.path.exists()


def test_corrupt_and_concurrently_edited_files_preserved(tmp_path):
    store=MapAnnotations(tmp_path,"a"*64)
    store.save(note())
    store.path.write_text("broken")
    with pytest.raises(ValueError):store.save(note(note="changed"))
    assert store.path.read_text()=="broken"
    loaded=MapAnnotations(tmp_path,"a"*64)
    with pytest.raises(ValueError):loaded.save(note())
    assert store.path.read_text()=="broken"


def test_annotations_separated_by_server_profile(tmp_path):
    a,b=MapAnnotations(tmp_path,"a"*64),MapAnnotations(tmp_path,"b"*64)
    a.save(note())
    assert b.annotate([cell()])[0]["navigation"][0]["kind"]=="door"
