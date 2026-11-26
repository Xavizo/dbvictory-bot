"""Offline relay/worker isolation; synthetic packets only, no game connection."""
import asyncio
from copy import deepcopy
import json
import struct
import threading
import time
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest
from crypto import xtea_encrypt, adler32_checksum
from game_state import GameState, scan_packet
from packet_observer import PacketObserver, apply_scan, decrypt_game_packet, detach_state, parse_detached, publish_server_feedback
from proxy import OTProxy
from test_game_alerts import talk
from test_player_updates import stats, login, creature_move, floors

KEYS=(1,2,3,4)


def frame(payload, checksum=True):
    encrypted=xtea_encrypt(struct.pack('<H',len(payload))+payload,KEYS)
    return (struct.pack('<I',adler32_checksum(encrypted)) if checksum else b'')+encrypted


@pytest.mark.parametrize('checksum',[False,True])
@pytest.mark.parametrize('payload',[b'',b'\x1d',stats(),bytes(range(256))*8])
def test_crypto_framing_is_identical_for_the_passive_worker(checksum,payload):
    assert decrypt_game_packet(frame(payload,checksum),KEYS)==payload


@pytest.mark.parametrize('raw',[b'',b'x',b'x'*65536,b'\0'*8])
def test_invalid_frames_are_not_parsed(raw):
    if raw==b'\0'*8:
        raw=xtea_encrypt(struct.pack('<H',99)+b'abcdef',KEYS)
    assert decrypt_game_packet(raw,KEYS) is None


@pytest.mark.parametrize('payload',[stats(),login(),creature_move()+b'\x66opaque',
    floors((80,95,8),range(8,11)),b'\xa2\x02\x00',b'\x82\x06\x07',talk(6)])
def test_detached_parser_preserves_existing_observed_fields(payload,monkeypatch):
    monkeypatch.setattr('game_state._dump_stats_debug',lambda *a:None)
    actual=GameState();actual.packet_position=actual.position=(80,95,7)
    expected=GameState();expected.packet_position=expected.position=(80,95,7)
    scan_packet(payload,expected)
    result=parse_detached(frame(payload),KEYS,detach_state(actual,0))
    apply_scan(actual,result)
    for key in ('hp','max_hp','mana','max_mana','experience','level','packet_position','position','player_icons','player_id','world_light_level'):
        assert getattr(actual,key)==getattr(expected,key),key


def test_worker_cannot_mutate_live_state_or_publish_callbacks_and_main_applies_once():
    gs=GameState();notifications=[]
    gs.alert_chat_callback=lambda *a:notifications.append(a)
    detached=detach_state(gs,0)
    result=parse_detached(frame(stats(hp=37)+talk(6)),KEYS,detached)
    assert gs.hp==0 and not notifications
    assert result.state.hp==37 and len(result.messages)==1
    apply_scan(gs,result)
    assert gs.hp==37 and not notifications


def test_newer_health_position_creatures_and_manual_selection_are_preserved():
    gs=GameState();gs.client_state_required=True;gs.client_player_id=gs.player_id=0x10000001
    gs.client_snapshot={'monotonic':time.monotonic(),'hp':90,'max_hp':100,'position':(80,95,7)}
    first=0x40000001;second=0x40000002;gs.attack_target_id=first
    gs.creatures[first]={'health':90,'source':'dll','t':1}
    detached=detach_state(gs,first)
    message=b'Creature is not reachable.'
    payload=stats(hp=10)+b'\x8c'+struct.pack('<IB',first,20)+b'\xb4\x19'+struct.pack('<H',len(message))+message
    result=parse_detached(frame(payload),KEYS,detached)
    gs.client_snapshot={'monotonic':time.monotonic(),'hp':100,'max_hp':100,'position':(81,95,7)}
    gs.creatures[first]['health']=80;gs.attack_target_id=second
    apply_scan(gs,result)
    assert gs.hp==100 and gs.position==(81,95,7)
    assert gs.creatures[first]['health']==80 and gs.attack_target_id==second
    assert first in gs.unreachable_creatures and second not in gs.unreachable_creatures


def test_detached_client_snapshot_does_not_expire_mid_parse(monkeypatch):
    gs=GameState();gs.client_state_required=True
    gs.client_snapshot={'monotonic':1,'hp':55,'position':(50,60,7)}
    monkeypatch.setattr('client_player_state.time.monotonic',lambda:1.1)
    detached=detach_state(gs,0)
    monkeypatch.setattr('client_player_state.time.monotonic',lambda:100)
    assert gs.hp==0 and detached.state.hp==55
    assert detach_state(gs,0).state.hp == 0
    assert detached.state.client_snapshot is not gs.client_snapshot


def test_kill_dedup_and_xp_attribution_publish_without_worker_telemetry(monkeypatch):
    import sys
    telemetry=Mock();monkeypatch.setattr(sys.modules['__main__'],'state',NS(telemetry=telemetry),raising=False)
    gs=GameState();cid=0x40000001;gs.creatures[cid]={'health':100,'x':20,'y':30,'z':7,'source':'dll'}
    payload=b'\x8c'+struct.pack('<IB',cid,0)
    scan=parse_detached(frame(payload),KEYS,detach_state(gs,0))
    assert gs.session_kills==0 and not telemetry.record_kill.called
    kills=apply_scan(gs,scan)
    assert gs.session_kills==1 and len(kills)==1
    assert cid in gs._recent_kills  # First packet after a cleanup must stay deduped.
    scan=parse_detached(frame(payload),KEYS,detach_state(gs,0));apply_scan(gs,scan)
    assert gs.session_kills==1


def test_packet_potion_messages_are_delivered_only_after_worker_returns():
    gs=GameState()
    text=b'You cannot use this object.'
    scan=parse_detached(frame(b'\xb4\x19'+struct.pack('<H',len(text))+text),KEYS,detach_state(gs,0))
    assert not gs.messages
    assert scan.potion_messages==[(25,text.decode(),{'batch_id':1})]
    apply_scan(gs,scan)
    message, = gs.messages
    assert (message['type'], message['text']) == (25, text.decode())
    assert abs(time.time() - message['at']) < 2


def test_message_history_keeps_the_latest_100000_entries_across_batches():
    live = GameState()
    capacity = 100_000
    live.messages.extend({"type": 25, "text": str(i)} for i in range(capacity))
    assert len(live.messages) == capacity

    scan = detach_state(live, 0)
    assert not scan.state.messages
    scan.state.messages.extend({"type": 25, "text": str(i)} for i in range(capacity, capacity + 60))
    apply_scan(live, scan)

    assert len(live.messages) == capacity
    assert all(row["text"] == str(i) for i, row in enumerate(live.messages, start=60))


def test_walk_cancel_retains_its_ingress_time_after_worker_publication():
    gs = GameState()
    gs.position = (697, 394, 9)
    scan = detach_state(gs, 0)
    scan.received_at = 20.
    scan.received_perf = 41.
    result = parse_detached(frame(bytes([0xb5, 3])), KEYS, scan)
    assert gs.last_walk_cancel is None
    apply_scan(gs, result)
    assert gs.last_walk_cancel == (41., (697, 394, 9))


def test_senzu_and_pet_feedback_keep_the_identity_and_time_of_the_live_run():
    from potions import PotionDiagnostics, SenzuUsage
    from test_pet_tracking import START, PET, look, sample

    gs = GameState()
    usage = gs.senzu_usage = SenzuUsage()
    diagnostics = PotionDiagnostics()
    slots = [(START, 1, PET)]
    sample(gs, slots)
    before_look = time.monotonic() - .01
    look(gs)

    def receive(text, at):
        data = text.encode("latin1")
        scan = detach_state(gs, 0)
        scan.received_at = at
        result = parse_detached(frame(b'\xb4\x19' + struct.pack('<H',len(data)) + data), KEYS, scan)
        apply_scan(gs, result)
        publish_server_feedback(gs, result, diagnostics)

    description = "You see Bills\r\nMaster of Trainer"
    receive(description, before_look)
    assert sample(gs, slots)["cells"][0]["occupant_types"] == {"monster": 1}
    receive(description, time.monotonic())
    assert sample(gs, slots)["cells"][0]["occupant_types"] == {"pet": 1}
    receive("Using one of 10 Senzu...", time.monotonic())
    receive("Using one of 9 Senzu...", time.monotonic())
    assert usage.total == 1
    previous_run = time.monotonic()
    gs.senzu_usage = SenzuUsage()
    receive("Using one of 8 Senzu...", previous_run - .01)
    receive("Using one of 7 Senzu...", time.monotonic())
    assert gs.senzu_usage.total == 0


def test_delayed_potion_response_cannot_confirm_a_newer_use():
    from potions import PotionDiagnostics
    gs=GameState();diagnostics=PotionDiagnostics();diagnostics.until=1000
    diagnostics.record_use(10161,'use',gs,now=11)
    scan=detach_state(gs,0);scan.received_at=10
    text=b'Using one of 30 Special Health Potion...'
    result=parse_detached(frame(b'\xb4\x19'+struct.pack('<H',len(text))+text),KEYS,scan)
    for kind,text,kwargs in result.potion_messages:
        diagnostics.observe_message(kind,text,now=result.received_at,**kwargs)
    assert diagnostics.rows[-1]['status']=='pending'
    assert not diagnostics.last_reports


def test_packet_gap_preserves_native_map_but_invalidates_learning_correlations():
    from passive_map import PassiveMapMonitor
    monitor=PassiveMapMonitor(NS())
    result={'status':'ready','cells':[{'x':10,'y':20,'z':7}],'captured_at':time.time()}
    monitor._result=result;monitor.learner=Mock()
    monitor.invalidate_packet_evidence()
    assert monitor._result is result
    monitor.learner.reset.assert_called_once()
    monitor.reset_learning_session()
    assert monitor._result['status']=='waiting' and monitor._result['cells']==[]


def test_xp_attribution_merges_existing_kill_without_counting_it_twice():
    gs=GameState();gs._prev_experience=75543800
    gs.kill_log.append({'cid':0x40000001,'t':time.time()});gs.session_kills=1
    scan=parse_detached(frame(stats()),KEYS,detach_state(gs,0))
    assert 'xp' not in gs.kill_log[-1]
    assert apply_scan(gs,scan)==[]
    assert gs.session_kills==1 and gs.kill_log[-1]['xp']==71


def test_outsized_result_is_rejected_before_main_loop_publication():
    gs=GameState();payload=b''.join(talk(6,statement=n) for n in range(257))
    with pytest.raises(ValueError,match='observation_result_limit'):
        parse_detached(frame(payload),KEYS,detach_state(gs,0))
    assert not gs.messages


def test_lost_packet_position_waits_for_absolute_resynchronization():
    gs=GameState();gs.packet_position=gs.position=(100,100,7);gs.packet_position_trusted=False
    result=parse_detached(frame(b'\x66opaque'),KEYS,detach_state(gs,0));apply_scan(gs,result)
    assert gs.packet_position==(100,100,7) and not gs.packet_position_trusted
    assert not gs.server_events
    result=parse_detached(frame(login(x=105,y=102,z=8)),KEYS,detach_state(gs,0));apply_scan(gs,result)
    assert gs.packet_position==(105,102,8) and gs.packet_position_trusted


def harness(**options):
    st=NS(gs=GameState(),session=1,connected=True)
    applied=[];gaps=[];owner=threading.get_ident()
    def apply(gs,result,kills):
        assert threading.get_ident()==owner
        applied.append((gs.hp,len(result.messages)))
    def gap(reason):
        assert threading.get_ident()==owner
        st.gs.packet_position_trusted=False;gaps.append(reason)
    observer=PacketObserver(lambda:st.gs,lambda:st.session,lambda:st.connected,apply,gap,**options)
    return st,observer,applied,gaps


async def wait_until(predicate):
    for _ in range(200):
        if predicate():return
        await asyncio.sleep(.005)
    raise AssertionError('Condition not met within bounded wait')


def test_ordered_publication_and_no_secret_metadata():
    async def exercise():
        st,obs,applied,gaps=harness()
        try:
            for hp in (80,60,40):obs.offer(frame(stats(hp=hp)+talk(6,text='private sentinel')),KEYS,1)
            await asyncio.wait_for(obs.queue.join(),2)
            assert applied==[(80,1),(60,1),(40,1)] and st.gs.hp==40 and not gaps
            text=json.dumps(obs.snapshot())
            assert 'sentinel' not in text and 'keys' not in text and 'payload' not in text
        finally:await obs.close()
    asyncio.run(exercise())


def test_walk_log_uses_only_client_inputs_before_reply_without_a_running_route(caplog, monkeypatch):
    from passive_map import PassiveMapMonitor
    from protocol import ClientOpcode as Op, PacketReader
    from packet_observer import log_walk_cancels

    caplog.set_level('INFO', logger='packet_observer')
    gs = GameState()
    gs.position = (550,310,7)
    monitor = PassiveMapMonitor(NS(connected=True, game_state=gs))
    monitor._result = dict(center=list(gs.position), captured_at=time.time())
    now = 30.
    monkeypatch.setattr('passive_map.time', NS(time=time.time, perf_counter=lambda: now))
    monitor.record_client_packet(Op.WALK_EAST, PacketReader(b''))
    now += .2
    monitor.record_client_packet(Op.WALK_NORTH, PacketReader(b''))
    scan = detach_state(gs, 0)
    scan.received_perf = 30.1
    parse_detached(frame(b'\xb5\x03'), KEYS, scan)
    log_walk_cancels(gs, scan)
    assert 'last_client_move=WALK_EAST requested_from=[550, 310, 7] requested_to=[551, 310, 7] request_age_ms=100' in caplog.messages[-1]
    assert 'observed_pos=[550, 310, 7]' in caplog.messages[-1]
    now = 31.
    monitor.record_client_packet(Op.AUTO_WALK, PacketReader(bytes([2,1,1])))
    scan.received_perf = now+.1
    log_walk_cancels(gs, scan)
    assert 'last_client_move=AUTO_WALK requested_from=None requested_to=None' in caplog.messages[-1]
    scan.received_perf += 3
    log_walk_cancels(gs, scan)
    assert 'last_client_move=None' in caplog.messages[-1]


def test_log_distinguishes_skipped_copy_from_partly_parsed_frame(caplog):
    caplog.set_level('INFO', logger='packet_observer')
    async def exercise():
        st, obs, applied, gaps = harness()
        try:
            obs.offer(b'xx', KEYS, st.session)
            await obs.queue.join()
            obs.offer(frame(b'\xb5\x02'), KEYS, st.session)
            obs.offer(frame(b'\xb5\x02\xff'), KEYS, st.session)  # Unknown final message.
            await obs.queue.join()
            assert obs.snapshot()['partial_among_applied'] == 1
            assert len(applied) == 2 and gaps == ['decode_failed']
        finally:
            await obs.close()
        assert 'Server observation gap: decode_failed' in caplog.messages
        assert any('offered=3 accepted=3 applied=2 partial_among_applied=1 dropped=1' in m for m in caplog.messages)
    asyncio.run(exercise())


def test_optional_subscriber_failure_does_not_discard_following_packets():
    async def exercise():
        st,obs,applied,gaps=harness()
        obs.on_result=Mock(side_effect=RuntimeError('private subscriber details'))
        try:
            for hp in (80,60,40):obs.offer(frame(stats(hp=hp)),KEYS,1)
            await asyncio.wait_for(obs.queue.join(),2)
            assert st.gs.hp==40 and obs.applied==3 and obs.failures==3
            assert not gaps and obs.dropped==0
            assert obs.snapshot()['last_problem']=='notification_failed'
            assert 'private' not in json.dumps(obs.snapshot())
        finally:await obs.close()
    asyncio.run(exercise())


def test_slow_worker_does_not_block_relay_or_other_automation_tasks():
    async def exercise():
        entered=threading.Event();release=threading.Event()
        def slow(*args):
            entered.set();assert release.wait(2);return parse_detached(*args)
        st,obs,applied,gaps=harness(worker=slow,max_age=2)
        writer=NS(write=Mock(),drain=AsyncMock())
        p=OTProxy.__new__(OTProxy)
        p.logged_in=True;p.xtea_keys=KEYS;p.session_generation=1;p.packets_from_server=0
        p.server_reader=object();p.client_writer=writer;p.on_encrypted_server_data=obs.offer
        packets=[frame(stats(hp=hp)) for hp in (80,70,60)]
        p._read_packet=AsyncMock(side_effect=packets+[None])
        p._decrypt_game_packet=Mock(side_effect=AssertionError('Relay must not decrypt'))
        try:
            await p._relay_server_to_client()
            await wait_until(entered.is_set)
            heartbeat=[]
            for _ in range(5):
                heartbeat.append(1);await asyncio.sleep(.005)
            assert len(heartbeat)==5 and st.gs.hp==0 and not applied
            assert [call.args[0] for call in writer.write.call_args_list]==[p._wrap_packet(raw) for raw in packets]
            assert writer.drain.await_count==3 and not p._decrypt_game_packet.called
            release.set();await asyncio.wait_for(obs.queue.join(),2)
            assert st.gs.hp==60
        finally:release.set();await obs.close()
    asyncio.run(exercise())


def test_overload_is_bounded_and_discards_old_inflight_result():
    async def exercise():
        entered=threading.Event();release=threading.Event();calls=[]
        def slow(*args):
            calls.append(1)
            if len(calls)==1:entered.set();assert release.wait(2)
            return parse_detached(*args)
        st,obs,applied,gaps=harness(worker=slow,max_packets=2,max_bytes=1000,max_age=2)
        try:
            obs.offer(frame(stats(hp=10)),KEYS,1);await wait_until(entered.is_set)
            for hp in (20,30,40):obs.offer(frame(stats(hp=hp)),KEYS,1)
            assert obs.queue.qsize()==1 and obs.queued_bytes<=1000 and gaps==['overload']
            release.set();await asyncio.wait_for(obs.queue.join(),2)
            assert applied==[(40,0)] and obs.dropped==3
        finally:release.set();await obs.close()
    asyncio.run(exercise())


@pytest.mark.parametrize('change',['reconnect','invalidate','disconnect','close'])
def test_session_or_lifetime_change_prevents_late_publication(change):
    async def exercise():
        entered=threading.Event();release=threading.Event()
        def slow(*args):
            entered.set();assert release.wait(2);return parse_detached(*args)
        st,obs,applied,gaps=harness(worker=slow,max_age=2);old=st.gs
        try:
            obs.offer(frame(stats(hp=10)+talk(6)),KEYS,1);await wait_until(entered.is_set)
            if change=='reconnect':st.gs=GameState();st.session=2
            elif change=='invalidate':obs.invalidate()
            elif change=='disconnect':st.connected=False
            else:await obs.close()
            release.set();await asyncio.wait_for(obs.queue.join(),2)
            assert not applied and old.hp==0 and st.gs.hp==0
            assert obs.offered == obs.dropped == 1
        finally:release.set();await obs.close()
    asyncio.run(exercise())


def test_stale_or_failed_analysis_never_sets_live_health_and_next_packet_recovers():
    async def exercise():
        entered=threading.Event();release=threading.Event();calls=[]
        def worker(*args):
            calls.append(1)
            if len(calls)==1:
                entered.set();assert release.wait(2)
            if len(calls)==2:raise RuntimeError('secret error payload')
            return parse_detached(*args)
        st,obs,applied,gaps=harness(worker=worker,max_age=.04)
        try:
            obs.offer(frame(stats(hp=10)),KEYS,1);await wait_until(entered.is_set)
            await asyncio.sleep(.06);release.set();await asyncio.wait_for(obs.queue.join(),2)
            assert st.gs.hp==0 and gaps==['stale']
            obs.offer(frame(stats(hp=20)),KEYS,1);await asyncio.wait_for(obs.queue.join(),2)
            assert st.gs.hp==0 and gaps[-1]=='analysis_failed'
            obs.max_age=1;obs.offer(frame(stats(hp=30)),KEYS,1);await asyncio.wait_for(obs.queue.join(),2)
            assert st.gs.hp==30 and applied==[(30,0)]
            assert 'secret' not in json.dumps(obs.snapshot())
        finally:release.set();await obs.close()
    asyncio.run(exercise())


def test_relay_keeps_forwarding_when_observer_tap_fails():
    async def exercise():
        p=OTProxy.__new__(OTProxy)
        p.logged_in=True;p.xtea_keys=KEYS;p.session_generation=1;p.packets_from_server=0;p.server_reader=object()
        p.client_writer=NS(write=Mock(),drain=AsyncMock())
        p.on_encrypted_server_data=Mock(side_effect=RuntimeError('private details'))
        packets=[frame(b'\x1d'),frame(b'\x1e')]
        p._read_packet=AsyncMock(side_effect=packets+[None])
        await p._relay_server_to_client()
        assert p.client_writer.write.call_count==2 and p.on_encrypted_server_data.call_count==2
    asyncio.run(exercise())


def test_byte_limit_and_malformed_frames_invalidate_only_observation():
    async def exercise():
        st,obs,applied,gaps=harness(max_bytes=50,max_age=1)
        try:
            assert not obs.offer(b'x'*51,KEYS,1)
            assert gaps==['invalid_frame'] and st.connected
            assert obs.offer(b'xx',KEYS,1)
            await asyncio.wait_for(obs.queue.join(),2)
            assert gaps[-1]=='decode_failed' and obs.queue.qsize()==0
            assert not obs.offer(frame(b'\x1d'),KEYS,99)
            obs.offer(frame(b'\x1d'),KEYS,1)
            await asyncio.wait_for(obs.queue.join(),2)
            assert obs.applied==1
            assert obs.offered == obs.applied + obs.dropped == 3
        finally:await obs.close()
    asyncio.run(exercise())


@pytest.mark.parametrize('wire,error,stage,reason', [
    (b'',None,'header','eof'),(b'x',None,'header','truncated'),
    (bytes([4,0,42]),None,'body','truncated'),(bytes(2),None,'header','empty_frame'),
    (b'',TimeoutError(10060,'timeout'),'header','TimeoutError'),
    (b'',ConnectionResetError(10054,'reset'),'header','ConnectionResetError')])
def test_stream_end_records_side_stage_and_freshness_without_payload(wire,error,stage,reason,caplog):
    async def run():
        p=OTProxy.__new__(OTProxy)
        reader=p.server_reader=asyncio.StreamReader()
        p.last_server_read=time.monotonic()-2
        p.last_client_forward=time.monotonic()-1
        if error:
            reader.set_exception(error)
        else:
            reader.feed_data(wire)
            reader.feed_eof()
        assert await p._read_packet(reader) is None
    with caplog.at_level('INFO'):
        asyncio.run(run())
    assert f'side=server stage={stage} reason={reason}' in caplog.text
    assert 'last_server_read_ms=2' in caplog.text and 'last_client_forward_ms=1' in caplog.text
    if error:
        assert f'errno={error.errno}' in caplog.text

def test_failure_forwarding_to_client_is_observed_and_other_workers_stop(caplog):
    async def run():
        p = OTProxy.__new__(OTProxy)
        reader = p.server_reader = asyncio.StreamReader()
        reader.feed_data(bytes([1,0,42]))
        p.logged_in = True
        p.packets_from_server = 0
        p.last_server_read = p.last_client_forward = None
        p.client_writer = Mock(drain=AsyncMock(side_effect=ConnectionResetError(10054,'reset')))
        completed = []
        async def wait():
            try:
                await asyncio.Future()
            finally:
                completed.append(True)
        p._relay_client_to_server = p._process_inject_queue = wait
        await asyncio.wait_for(p._run_relay_loop(),1)
        assert completed == [True,True]
        assert p.last_server_read is not None and p.last_client_forward is None
    with caplog.at_level('INFO'):
        asyncio.run(run())
    assert 'side=towards_client stage=relay reason=ConnectionResetError errno=10054' in caplog.text
