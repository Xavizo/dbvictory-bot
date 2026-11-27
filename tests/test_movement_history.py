import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from dbv.brain.cavebot.adaptive_cavebot import AdaptiveCavebot
from dbv.brain.cavebot.cavebot_walking import WalkOutcome
from dbv.mcp_server.dashboard_api import _Handler
from dbv.brain.cavebot.movement_history import MovementHistory
from dbv.client_bridge.packet_observer import log_walk_cancels
from dbv.brain.navigation.passive_map import PassiveMapMonitor
from dbv.client_bridge.protocol import ClientOpcode, PacketReader


A, B = (100,100,7), (101,100,7)


def frame(center=A, kind="clear"):
    return dict(status="ready",center=center,captured_at=time.time(),
                cells=[dict(x=B[0],y=B[1],z=B[2],kind=kind,name="private",
                            occupants=1, items=[{"id":1234}])])


@pytest.fixture
def session(tmp_path):
    now = [0.]
    return now, MovementHistory(tmp_path, clock=lambda:now[0])


def test_manual_walk_cancel_groups_five_minutes_and_correct_tail(session):
    now,h = session
    h.frame(frame())
    now[0] = 1
    h.frame(frame(B))
    now[0] = 300.5
    h.frame(frame())
    gs = NS(movement_history=h, client_walk_requests=[])
    monitor = PassiveMapMonitor(NS(connected=True,game_state=gs))
    monitor._result = frame()
    monitor.record_client_packet(ClientOpcode.WALK_EAST,PacketReader(b""))
    now[0] = 301
    scan = NS(state=NS(server_events=[(0,"cancel_walk",{"pos":A,"direction":1})]),
              received_perf=time.perf_counter(),received_at=now[0])
    log_walk_cancels(gs,scan)
    now[0] = 329
    h.cancelled(A,1,329)
    now[0] = 332
    h.request("client",A,B,frame())
    h.player_position(B)
    h.frame(frame(B))
    now[0] = 358.9
    assert h.poll() is None
    now[0] = 360
    report = h.poll()
    assert report["trigger_ms"] == 301000 and report["end_ms"] == 359000
    assert report["events"][0]["ms"] == 1000
    events = [row["event"] for row in report["events"]]
    assert events.count("server_cancel")==2 and "position_after_request" in events and "map_after_request" in events
    request = next(r for r in report["events"] if r["event"]=="walk_request")
    assert request["source"]=="client" and request["origin"]==A and request["target"]==B and request["terrain"]=="clear"
    assert "private" not in json.dumps(report) and "1234" not in json.dumps(report)
    assert h.poll() is None


def test_web_report_compares_manual_ground_click_with_planned_web_and_observed_change(session):
    from dbv.client_bridge.protocol import build_use_item_ex_packet
    now,h=session
    before=frame()
    tile=before['cells'][0]
    tile['items']=[{'id':486,'stack':0},{'id':183,'stack':1}]
    monitor=PassiveMapMonitor(NS(connected=True,game_state=NS(movement_history=h)))
    monitor._result=before
    h.web_tool('cavebot',B,8900,183,1,tile)
    use=build_use_item_ex_packet(65535,10,0,8900,0,*B,0,target_item_id=486)
    monitor.record_client_packet(ClientOpcode.USE_ITEM_EX,PacketReader(use[1:]))
    tile['items'][-1]['id']=189
    before['captured_at'] = h.web_watch[2]  # Same clock tick cannot prove the order.
    h.frame(before)
    assert h.web_watch  # Snapshot captured before the tool selection.
    h.frame({**before,'captured_at':time.time()+.1})
    assert h.web_watch is None
    now[0]=31
    report=h.poll()
    uses=[e for e in report['events'] if e['event']=='web_tool_use']
    assert [(e['source'],e['target_item'],e['target_layer']) for e in uses]==[('cavebot',183,1),('client',486,0)]
    assert sum(e['event']=='web_cut_observed' for e in report['events'])==1


def test_stalled_web_report_records_layers_once_without_a_walk_request(session):
    now,h=session
    tile={'items':[dict(id=486,stack=0,flags=['ground']),dict(id=183,stack=1,flags=['not_walkable'])]}
    h.web_blocked(A,B,tile,'przeszkoda terenowa')
    h.web_blocked(A,B,tile,'przeszkoda terenowa')
    now[0]=31
    data=h.poll()
    assert data['trigger']=='web_obstruction'
    assert [r['event'] for r in data['events']]==['web_obstruction']
    assert data['events'][0]['items'][1]==(183,1,('not_walkable',))


def test_timeout_with_repeated_requests_and_delayed_manual_save(session):
    now,h = session
    h.frame(frame())
    h.request("client",A,B,frame())
    now[0] = 2
    h.request("client",A,B,frame(kind="blocked"))
    now[0] = 3
    h.poll()
    assert h.status()["capturing"]
    now[0] = 240
    saved = Path(h.manual_save())
    report = json.loads(saved.read_text())
    assert report["trigger"]=="manual"
    event = next(r for r in report["events"] if r["event"]=="position_unconfirmed")
    assert event["elapsed_ms"]==3000 and event["terrain"]=="blocked"
    assert h.poll()["end_reason"]=="quiet"
    now[0] = 304
    with pytest.raises(ValueError):h.manual_save()


def test_transient_terrain_and_first_position_source_can_arrive_in_either_order(session):
    now,h = session
    counts = NS(dropped=2,partial=4,failures=0)
    h.request("client",A,B,frame())
    h.frame(frame(kind="blocked"),counts)
    now[0] = .2
    h.frame(frame(B),counts)
    now[0] = .3
    h.player_position(B)
    h.request("client",B,A)
    h.stop_request()
    now[0] += 5
    assert h.poll() is None and not h.status()["capturing"]
    events = json.loads(Path(h.manual_save()).read_text())["events"]
    assert [r["elapsed_ms"] for r in events if r["event"].endswith("after_request")]==[200,300]
    assert any(r["event"]=="target_terrain" and r["terrain"]=="blocked" for r in events)
    assert sum(r["event"]=="observer_totals" for r in events)==1


def test_cavebot_timer_and_worker_finalize_on_disconnect(session):
    now,h = session
    controller = AdaptiveCavebot(NS(game_state=NS(movement_history=h),passive_map_monitor=NS(_result=frame())))
    async def timeout(*args):
        return WalkOutcome.TIMEOUT
    controller.wait_position = timeout
    assert not asyncio.run(controller._confirm_walk(B,A))
    h.close()
    h.start_worker()
    deadline = time.monotonic()+3
    while not h.status()["latest"] and time.monotonic()<deadline:time.sleep(.01)
    report = json.loads(Path(h.status()["latest"]).read_text())
    assert report["end_reason"]=="logout" and report["trigger"]=="position_unconfirmed"
    assert [(r["event"],r.get("source")) for r in report["events"]]==[("walk_request","cavebot"),("route_position",None)]


def test_manual_endpoint_and_stale_cancel_do_not_erase_new_movement(session,monkeypatch):
    from dbv.mcp_server import dashboard_api
    now,h = session
    handler = NS(headers={},_json_response=lambda data,status=200:(status,data))
    monkeypatch.setattr(dashboard_api,"_state",NS(game_state=NS(movement_history=h)))
    assert _Handler._movement_history(handler,save=True)[0]==400
    now[0] = 5
    h.request("client",A,B)
    h.cancelled(A,1,4.9)
    h.player_position(B)
    assert _Handler._movement_history(handler,save=True)[0]==200
    latest = Path(_Handler._movement_history(handler)[1]["latest"])
    assert latest.is_file() and "position_after_request" in latest.read_text()
    now[0] = 40
    h.cancelled(B,0,40)
    assert h.poll()["end_ms"]==35000
    h.close()
    assert h.poll()["trigger_ms"]==40000


def test_old_map_and_observation_loss_do_not_confirm_a_new_step(session):
    now,h = session
    h.request("client",A,B)
    h.observation_gap("overload")
    h.observation_gap("overload")
    h.frame({**frame(B),"captured_at":time.time()-2})
    now[0] = 3
    h.poll()
    h.close()
    events = h.poll()["events"]
    assert sum(r["event"]=="observation_gap" for r in events)==1
    assert not any(r["event"]=="map_after_request" for r in events)
    assert events[-1]["event"]=="position_unconfirmed"


@pytest.mark.parametrize("mode", ["confirmed", "next_step", "only_position", "cancel", "interaction", "stale", "automation"])
def test_single_manual_stair_step_needs_a_separate_landing_confirmation(session, mode):
    clock, history = session
    landing = (101,100,8)
    history.request("bot" if mode=="automation" else "client", A, B)
    clock[0] = .8 if mode=="stale" else .10
    history.player_position(landing)
    if mode=="cancel":
        history.cancelled(A, 1, clock[0])
    if mode=="interaction":
        history.interrupt_floor_step()
    if mode=="next_step":
        history.request("client",landing,(102,100,8))
    elif mode!="only_position":
        history.frame(frame(landing))
    expected = [(A,B,landing)] if mode in ("confirmed","next_step") else []
    assert history.take_floor_steps(active=True) == expected
    assert history.take_floor_steps(active=True) == []
