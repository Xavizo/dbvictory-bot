"""Synthetic navigation observations; no client process or game commands."""
import json
import struct
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from protocol import ClientOpcode as Op, PacketReader
import transition_learning as tl
from passive_map import PassiveMapMonitor

ORIGIN = [100,100,7]
ENTRY = [100,99,7]
EXIT = [100,99,6]


def tile(pos=ENTRY, item=14607, kind="clear", nav=None):
    return dict(zip(("x","y","z"),pos),kind=kind,items=[{"id":item,"stack":0}],
                navigation=[{"kind":nav}] if nav else [])


def sample(center=ORIGIN, cells=None):
    return {"status":"ready","center":list(center),"cells":cells if cells is not None else [tile()]}


def step(origin=ORIGIN, entry=ENTRY):
    return {"kind":"step","origin":list(origin),"entry":list(entry)}


def use(kind="use", item=14607, entry=ENTRY, **extra):
    return {"kind":kind,"entry":list(entry),"item_id":item,"stack":0,**extra}


def transition(learner, command=None, start=0, destination=EXIT, cells=None):
    learner.observe(sample(cells=cells),now=start,wall=100+start)
    if command: learner.enqueue(command,now=start+.1)
    learner.observe(sample(destination),now=start+.25,wall=100+start+.25)
    assert not learner.snapshot()["records"] or start>0
    learner.observe(sample(destination),now=start+.5,wall=100+start+.5)
    return learner.snapshot()["records"][0]


@pytest.fixture
def learner(tmp_path):
    return tl.TransitionLearner(tmp_path,"a"*64)


@pytest.mark.parametrize("direction,offset",list(tl.OFFSETS.items()))
def test_decode_single_new_walk(direction,offset):
    packet = struct.pack("<IIHHBBHB",10,20,*ORIGIN,0,1,direction)
    result = tl.decode_command(Op.NEW_WALK,PacketReader(packet),None)
    assert result == step(ORIGIN,[100+offset[0],100+offset[1],7])


@pytest.mark.parametrize("packet",[b"",struct.pack("<IIHHBBHBB",1,2,*ORIGIN,0,2,1,3)])
def test_ambiguous_routes(packet):
    assert tl.decode_command(Op.NEW_WALK,PacketReader(packet),ORIGIN)=={"kind":"ambiguous"}


@pytest.mark.parametrize("legacy",[False,True])
def test_use_with_layouts_and_tool(legacy):
    packet=struct.pack("<HHBHBHHB",65535,1,0,3003,0,*ENTRY)
    packet += b"\0" if legacy else struct.pack("<HB",14607,0)
    assert tl.decode_command(Op.USE_ITEM_EX,PacketReader(packet),ORIGIN)==use("use_with",None if legacy else 14607,tool_id=3003)


def test_inventory_and_chat_ignored():
    packet=struct.pack("<HHBHBB",65535,1,0,10161,0,0)
    assert tl.decode_command(Op.USE_ITEM,PacketReader(packet),ORIGIN) is None
    assert tl.decode_command(Op.SAY,PacketReader(b"private message"),ORIGIN) is None


@pytest.mark.parametrize("command,action",[(step(),"step"),(use(),"use"),(use("use_with",tool_id=3003),"use_with")])
def test_floor_transition_attribution_persistence(learner,command,action):
    row=transition(learner,command)
    assert (row["origin"],row["entry"],row["exit"],row["action"],row["direction"],row["evidence"]) == (ORIGIN,ENTRY,EXIT,action,"up","observed")
    loaded=tl.TransitionLearner(learner.path.parent,learner.scope)
    assert loaded.snapshot()["total"]==1
    assert loaded.snapshot()["records"][0]["id"]==row["id"]
    assert learner.annotate([tile()])[0]["transitions"][0]["exit"]==EXIT


@pytest.mark.parametrize("command",[None,{"kind":"ambiguous"},use(item=999),step(origin=[99,100,7])])
def test_uncertain_never_annotated_as_learned(learner,command):
    row=transition(learner,command)
    assert row["evidence"]=="review" and row["entry"] is None
    assert learner.annotate([tile()])[0]["transitions"]==[]


def test_duplicate_count_and_variable_destination(learner):
    transition(learner,step())
    learner.reset()
    transition(learner,step(),start=2)
    assert learner.snapshot()["records"][0]["count"]==2
    learner.reset()
    transition(learner,step(),start=4,destination=[200,200,6])
    assert learner.snapshot()["total"]==2
    assert all(r["variable_exit"] for r in learner.snapshot()["records"])


@pytest.mark.parametrize('command', [step(),use(),use("use_with",tool_id=3003)])
@pytest.mark.parametrize('confirmed', [True,False])
def test_continue_walking_immediately_after_floor_change(learner,command,confirmed):
    learner.observe(sample(),now=0,wall=100)
    learner.enqueue(command,now=.1)
    learner.observe(sample(EXIT),now=.2,wall=100.2)
    next_tile=[EXIT[0]+1,EXIT[1],EXIT[2]]
    if confirmed:learner.enqueue(step(EXIT,next_tile),now=.25)
    learner.observe(sample(next_tile),now=.35,wall=100.35)
    record=next(iter(learner.records.values()))
    assert record["exit"]==EXIT and record["evidence"]==("observed" if confirmed else "review")


@pytest.mark.parametrize("mode",["reset","gap","suspended","unavailable","overflow"])
def test_session_or_sample_loss_does_not_become_transition(learner,mode):
    learner.observe(sample(),now=0)
    learner.enqueue(step(),now=.1)
    if mode=="reset": learner.reset()
    if mode=="suspended": learner.observe(sample(),now=.15,active=False)
    if mode=="unavailable": learner.observe(None,now=.15)
    if mode=="overflow":
        for _ in range(65): learner.enqueue(step(),now=.1)
    now=2 if mode=="gap" else .25
    learner.observe(sample(EXIT),now=now)
    learner.observe(sample(EXIT),now=now+.25)
    assert learner.snapshot()["total"]==0


def test_two_commands_and_unstable_landing_review(learner):
    learner.observe(sample(),now=0)
    learner.enqueue(step(),now=.05)
    learner.enqueue(use(),now=.1)
    learner.observe(sample(EXIT),now=.25)
    learner.observe(sample(EXIT),now=.5)
    assert learner.snapshot()["records"][0]["reason"]=="multiple_commands"
    learner.reset()
    learner.observe(sample(),now=2)
    learner.enqueue(step(),now=2.1)
    learner.observe(sample(EXIT),now=2.25)
    learner.observe(sample([101,99,6]),now=2.5)
    assert all(r["evidence"]=="review" for r in learner.snapshot()["records"])


def test_short_identified_teleport(learner):
    row=transition(learner,step(),destination=[104,100,7],cells=[tile(nav="teleport")])
    assert row["action"]=="step" and row["direction"]=="same_floor"


def test_ordinary_walk_not_a_transition(learner):
    learner.observe(sample(),now=0)
    learner.enqueue(step(),now=.1)
    learner.observe(sample(ENTRY),now=.25)
    learner.observe(sample(ENTRY),now=.5)
    assert learner.snapshot()["total"]==0


@pytest.mark.parametrize("opened_by_use",[False,True])
def test_door_crossing(learner,opened_by_use):
    closed=tile(item=1200,kind="blocked",nav="door")
    opened=tile(item=1201,nav="door")
    learner.observe(sample(cells=[closed if opened_by_use else opened]),now=0)
    learner.enqueue(use(item=1200) if opened_by_use else step(),now=.1)
    learner.observe(sample(ENTRY,[opened]),now=.25)
    learner.enqueue(step(ENTRY,[100,98,7]),now=.3)
    learner.observe(sample([100,98,7],[opened]),now=.5,wall=100)
    row=learner.snapshot()["records"][0]
    assert row["entry"]==ENTRY and row["exit"]==[100,98,7]
    assert row["action"]==("use_then_step" if opened_by_use else "step")


def test_failed_door_use_does_not_record(learner):
    closed=tile(item=1200,kind="blocked",nav="door")
    learner.observe(sample(cells=[closed]),now=0)
    learner.enqueue(use(item=1200),now=.1)
    learner.observe(sample(cells=[closed]),now=.25)
    learner.observe(sample(cells=[closed]),now=.5)
    assert learner.snapshot()["total"]==0


@pytest.mark.parametrize("delay",[0,1])
def test_shovel_then_step_records_preparation(learner,delay):
    learner.observe(sample(cells=[tile(item=6000)]),now=0)
    learner.enqueue(use("use_with",item=6000,tool_id=3001),now=.1)
    learner.observe(sample(cells=[tile(item=6001)]),now=.25)
    # Also cover an immediate step while both commands remain in the queue.
    if delay:
        for now in (.5,.75,1,1.25): learner.observe(sample(cells=[tile(item=6001)]),now=now)
    learner.enqueue(step(),now=.3+delay)
    learner.observe(sample([100,99,8]),now=.5+delay)
    learner.observe(sample([100,99,8]),now=.75+delay,wall=100)
    row=learner.snapshot()["records"][0]
    assert row["action"]=="use_with_then_step" and row["tool_id"]==3001
    assert row["item_id"]==6000 and row["entry_items"]==[6000]


def test_external_edits_and_corrupt_storage_preserved(learner):
    transition(learner,step())
    learner.path.write_text("broken")
    learner.reset()
    transition(learner,step(),start=2)
    assert learner.path.read_text()=="broken" and learner.snapshot()["storage_error"]
    loaded=tl.TransitionLearner(learner.path.parent,learner.scope)
    transition(loaded,step())
    assert loaded.path.read_text()=="broken" and loaded.snapshot()["storage_error"]


def test_full_legacy_store_accepts_new_passages_and_reopens(tmp_path):
    scope = 'a' * 64
    legacy = tmp_path / f'map_transitions-{scope[:16]}.json'
    rows = []
    for x in range(2000):
        row = dict(origin=[x,100,7], entry=None, exit=[x,100,6], action='unknown',
                   direction='up', evidence='review', reason='sample_gap', item_id=None,
                   tool_id=None, entry_items=[], count=1, first_seen=1, last_seen=1)
        row['id'] = tl.TransitionLearner._key(row)
        rows.append(row)
    original = json.dumps(dict(version=1,scope=scope,records=rows))
    legacy.write_text(original)
    learner = tl.TransitionLearner(tmp_path,scope)
    transition(learner,step(),start=1)
    learner.reset()
    transition(learner,step(),start=2)
    reopened = tl.TransitionLearner(tmp_path,scope)
    assert not reopened.storage_error and len(reopened.records) == 2001
    assert all(reopened.records[r['id']] == r for r in rows)
    assert sum(r['count'] for r in reopened.records.values() if r['evidence']=='observed') == 2
    assert legacy.read_text() == original


def test_recovered_stairs_preserve_live_observations_and_ignore_ambiguous_entries(tmp_path):
    scope = "a" * 64
    def record(x, dx=0, count=2):
        row = dict(origin=[x,100,7], entry=[x,99,7], exit=[x+dx,99,6], action="step",
                   direction="up", evidence="observed", reason="matched_command", item_id=None,
                   tool_id=None, entry_items=[469], count=count, first_seen=1, last_seen=2)
        return dict(row, id=tl.TransitionLearner._key(row))
    live, recovered = record(100), record(101)
    learner = tl.TransitionLearner(tmp_path, scope)
    learner._legacy.write_text(json.dumps(dict(version=1, scope=scope, records=[live])))
    bundle = learner.path.with_suffix(".recovered.json")
    bundle.write_text(json.dumps(dict(version=1, scope=scope, records=[
        record(100,1), recovered, record(102,0), record(102,1), record(103,count=1)])))
    loaded = tl.TransitionLearner(tmp_path, scope)
    expected = {r["id"]:r for r in (live,recovered)}
    assert loaded.records == expected
    loaded._save(recovered)
    assert tl.TransitionLearner(tmp_path, scope).records == expected
    bundle.write_text('{"invalid":true}')
    reopened = tl.TransitionLearner(tmp_path, scope)
    assert not reopened.storage_error and reopened.records == expected


def test_overlapping_learning_sessions_preserve_other_writers(learner):
    transition(learner,step())
    other = tl.TransitionLearner(learner.path.parent,learner.scope)
    learner.reset()
    transition(learner,step(),start=2)
    transition(other,step(),start=4)
    assert other.storage_error
    reopened = tl.TransitionLearner(learner.path.parent,learner.scope)
    assert next(iter(reopened.records.values()))['count'] == 2


def test_nearest_search_includes_old_entries_outside_recent_twenty(learner):
    for i in range(25):
        learner._record({"origin":[100,100,7],"entry":[100+i,100,7],"exit":[100+i,100,6],
                         "action":"step","direction":"up","evidence":"observed","reason":"matched_command"},100+i)
    learner.observe(sample(),now=0)
    view=learner.snapshot()
    assert view["records"][0]["entry"]==[124,100,7]
    assert view["nearby_records"][0]["entry"]==[100,100,7]
    assert len(view["nearby_records"])==20


def test_nearest_prioritizes_current_floor_and_leaves_unknown_entrance_last(learner):
    for entry in ([100,100,6],None,[110,100,7]):
        learner._record({"origin":ORIGIN,"entry":entry,"exit":EXIT,"action":"unknown" if entry is None else "step",
                         "direction":"up","evidence":"review" if entry is None else "observed","reason":"matched_command"},100)
    learner.observe(sample(),now=0)
    assert [r["entry"] for r in learner.snapshot()["nearby_records"]]==[[110,100,7],[100,100,6],None]


@pytest.mark.parametrize("mode",["manual","cavebot","walk","dead"])
def test_monitor_suspends_while_automated_or_dead(tmp_path,monkeypatch,mode):
    actual=tl.TransitionLearner(tmp_path,"unused")
    monkeypatch.setattr(tl,"TransitionLearner",lambda directory,scope: actual)
    task=Mock(); task.done.return_value=False
    state=SimpleNamespace(game_proxy=SimpleNamespace(server_host="test",server_port=1),
                          _action_tasks={"adaptive_cavebot":task} if mode=="cavebot" else {},
                          _auto_task=task if mode=="walk" else None,
                          game_state=SimpleNamespace(hp=0 if mode=="dead" else 100,stats_updated_at=1))
    monitor=PassiveMapMonitor(state)
    monitor._learn(sample(),{"sha256":"a"})
    assert actual.snapshot()["status"]==("learning" if mode=="manual" else "suspended")
    assert not actual.path.exists()


def test_one_confirmed_step_persists_and_never_creates_an_inputless_reverse(tmp_path):
    scope = "a"*64
    learner = tl.TransitionLearner(tmp_path,scope)
    a,b,c=tuple(ORIGIN),tuple(ENTRY),tuple(EXIT)
    cell=dict(items=[dict(id=469)],navigation=[dict(kind="passage",item_id=469)])
    learner.learn_floor_steps([(a,b,c),(a,b,c)],{b:cell})
    assert len(learner.records)==1
    obs=next(iter(learner.records.values()))
    assert obs["reason"]=="confirmed_step" and obs["count"]==1 and obs["entry"]==ENTRY and obs["exit"]==EXIT
    assert tl.TransitionLearner(tmp_path,scope).records==learner.records
    for kind in ("ladder","teleport","rope_spot"):
        other=tl.TransitionLearner(tmp_path/kind,scope)
        other.learn_floor_steps([(a,b,c)],{b:dict(items=cell["items"],navigation=[dict(kind=kind,item_id=469)])})
        assert not other.records

def test_archive_with_one_corroborated_traversal_survives_reopen(tmp_path):
    scope = "a"*64
    learner = tl.TransitionLearner(tmp_path,scope)
    row = dict(origin=ORIGIN,entry=ENTRY,exit=EXIT,action="step",direction="up",
               evidence="observed",reason="confirmed_step",item_id=None,tool_id=None,
               entry_items=[469],count=1,first_seen=1,last_seen=1)
    row["id"]=learner._key(row)
    learner.path.with_suffix(".steps.json").write_text(json.dumps(dict(version=1,scope=scope,records=[row])))
    assert tl.TransitionLearner(tmp_path,scope).records=={row["id"]:row}
