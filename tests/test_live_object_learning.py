"""Manual object observations on synthetic maps. No game connection."""
import pytest
from protocol import ClientOpcode as Op, PacketReader, build_say_packet
from client_profiles import LEGACY, UPDATED
from map_annotations import MapAnnotations
from map_navigation import load_confirmed_types, navigation_features
from transition_learning import TransitionLearner, decode_command
from test_transition_learning import ORIGIN, ENTRY, EXIT, sample, step, use, tile, transition


def door_tile(opened=False, kind="door"):
    item=1201 if opened else 1200
    t=tile(item=item, kind="clear" if opened else "blocked")
    t["items"][0]["flags"]=[] if opened else ["not_walkable"]
    if kind:
        t["navigation"]=[{"kind":kind,"item_id":item,"stack":0,"source":"client_hint"}]
    return t


@pytest.fixture
def learner(tmp_path):
    return TransitionLearner(tmp_path,"b"*64)


def open_door(learner, entered=False, kind="door"):
    learner.observe(sample(cells=[door_tile(kind=kind)]),now=0,wall=100)
    learner.enqueue(use(item=1200),now=.1)
    learner.observe(sample(ENTRY if entered else ORIGIN,[door_tile(True,kind)]),now=.25,wall=101)
    return learner.snapshot()["records"][0]


def test_one_open_updates_map_and_reloads_without_second_visit(learner):
    row=open_door(learner)
    assert row["object_change"]["behavior"]=="open_then_step"
    assert row["count"]==1
    opened=learner.annotate([door_tile(True)])[0]
    assert opened["navigation"][0]["door_state_hint"]=="open"
    assert opened["navigation"][0]["source"]=="observed"
    assert opened["transitions"]==[]  # Opening alone is not a traversal.
    loaded=TransitionLearner(learner.path.parent,learner.scope)
    closed=loaded.annotate([door_tile()])[0]
    assert closed["navigation"][0]["door_state_hint"]=="closed"
    assert closed["kind"]=="blocked"


def test_level_gate_use_enters_is_distinct(learner):
    row=open_door(learner,entered=True,kind="level_gate")
    assert row["exit"]==ENTRY and row["object_change"]["behavior"]=="use_enters"
    assert learner.annotate([door_tile(True,"level_gate")])[0]["navigation"][0]["behavior_hint"]=="use_enters"


@pytest.mark.parametrize("closes",[False,True])
def test_observe_exit_closure_or_still_open(learner,closes):
    open_door(learner,entered=True)
    destination=[100,98,7]
    learner.enqueue(step(ENTRY,destination),now=.3)
    current=sample(destination,[door_tile(not closes)])
    learner.observe(current,now=.5,wall=102)
    for now in (.75,1,1.25,1.5,1.75,2,2.25,2.5):
        learner.observe(current,now=now,wall=102+now)
    behaviors={r["object_change"]["behavior"] for r in learner.snapshot()["records"] if r.get("object_change")}
    assert ("closes_after_exit" if closes else "stays_open") in behaviors
    f=learner.annotate([door_tile(not closes)])[0]["navigation"][0]
    assert f["closing_policy"]==("after_exit" if closes else "observed_open")


@pytest.mark.parametrize("mode",["two_clicks","no_change","different_stack","moving_elsewhere"])
def test_ambiguous_opening_does_not_confirm(learner,mode):
    learner.observe(sample(cells=[door_tile()]),now=0)
    learner.enqueue(use(item=1200),now=.1)
    if mode=="two_clicks":learner.enqueue(use(item=1200),now=.15)
    current=door_tile(mode!="no_change")
    if mode=="different_stack":current["items"][0]["stack"]=1
    learner.observe(sample([101,100,7] if mode=="moving_elsewhere" else ORIGIN,[current]),now=.25)
    assert learner.snapshot()["total"]==0


def test_unknown_toggle_does_not_invent_gate_or_level(learner):
    open_door(learner,kind=None)
    f=learner.annotate([door_tile(True,None)])[0]["navigation"][0]
    assert f["kind"]=="passage" and "required_level" not in f


def test_first_traversal_marks_matching_hint_only(learner):
    transition(learner,step())
    known=tile()
    known["navigation"]=[{"kind":"ladder","item_id":14607,"stack":0,"source":"client_hint"}]
    f=learner.annotate([known])[0]["navigation"][0]
    assert f["source"]=="observed" and f["observation_count"]==1
    replaced=tile(item=999)
    replaced["navigation"]=[{"kind":"stairs","item_id":999,"stack":0,"source":"client_hint"}]
    result=learner.annotate([replaced])[0]
    assert result["transitions"]==[] and result["navigation"][0]["source"]=="client_hint"


def test_new_rope_and_shovel_ids_are_asset_scoped():
    known=load_confirmed_types(asset_sha256=UPDATED.dat_sha256)
    assert set(known)=={182,183,9410,606,607,476,1948}
    assert known[476]["kind"]=="hole" and known[1948]["kind"]=="ladder"
    assert known[9410]["kind"]=="rope_spot" and known[606]["kind"]=="covered_hole" and known[607]["kind"]=="hole"
    assert 9410 not in load_confirmed_types(asset_sha256=LEGACY.dat_sha256)
    assert load_confirmed_types(asset_sha256="f"*64)=={}


@pytest.mark.parametrize("activation",["rope","fly_up","rope_or_fly_up","aio"])
def test_rope_annotation_persists_without_command(tmp_path,activation):
    store=MapAnnotations(tmp_path,"a"*64)
    store.save({"position":ENTRY,"item_id":9410,"kind":"rope_spot","activation_hint":activation,"required_level":None,"note":""})
    cell=tile(item=9410);cell["items"][0]["flags"]=[]
    assert store.annotate([cell])[0]["navigation"][0]["activation_hint"]==activation


def test_fly_up_whitelist_ignores_chat_and_channels(learner):
    command=decode_command(Op.SAY,PacketReader(build_say_packet("fly up")[1:]),ORIGIN)
    assert command=={"kind":"fly_up","entry":ORIGIN}
    for text,mode in [("private message",1),("fly up",7),("fly up please",1)]:
        assert decode_command(Op.SAY,PacketReader(build_say_packet(text,mode)[1:]),ORIGIN) is None
    spot=tile(ORIGIN,9410);spot["items"][0]["flags"]=[]
    spot["navigation"]=navigation_features(spot["items"],load_confirmed_types(asset_sha256=UPDATED.dat_sha256))
    row=transition(learner,command,cells=[spot])
    assert row["action"]=="fly_up" and row["item_id"]==9410 and row["evidence"]=="observed"


@pytest.mark.parametrize("tool_id",[3457,8899])
def test_shovel_then_step_keeps_closed_and_open_id(learner,tool_id):
    learner.observe(sample(cells=[tile(item=606)]),now=0)
    learner.enqueue(use("use_with",item=606,tool_id=tool_id),now=.1)
    learner.observe(sample(cells=[tile(item=607)]),now=.25)
    learner.enqueue(step(),now=.3)
    learner.observe(sample([100,99,8]),now=.5)
    learner.observe(sample([100,99,8]),now=.75,wall=100)
    row=learner.snapshot()["records"][0]
    assert (row["action"],row["tool_id"],row["item_id"],row["prepared_item_id"])==("use_with_then_step",tool_id,606,607)
    assert learner.annotate([tile(item=607)])[0]["transitions"][0]["direction"]=="down"
    loaded=TransitionLearner(learner.path.parent,learner.scope)
    assert loaded.snapshot()["records"][0]["prepared_item_id"]==607


@pytest.mark.parametrize("tool_id",[3003,8899])
def test_rope_and_aio_record_actual_tool(learner,tool_id):
    row=transition(learner,use("use_with",item=9410,tool_id=tool_id),cells=[tile(item=9410)])
    assert row["tool_id"]==tool_id and row["item_id"]==9410 and row["direction"]=="up"
