import struct
from types import SimpleNamespace as NS
import pytest
from game_alerts import AlertCenter, default_config, validate_config
from game_state import GameState, scan_packet
from chat_messages import parse_talk


def setup(*, resume=True):
    now=[100.0]
    gs=GameState(); gs.player_id=0x10000001;gs.player_name='Me';gs.stats_updated_at=100;gs.position=(100,100,7)
    state=NS(game_state=gs,connected=True,game_proxy=object(),automation_paused=False,
             adaptive_cavebot=NS(status='running'),_action_tasks={'adaptive_cavebot':NS(done=lambda:False)},
             passive_map_monitor=NS(_result={'status':'ready','captured_at':100,'center':[100,100,7],'cells':[]}))
    center=AlertCenter(clock=lambda:now[0],spells={'shunkan','shinretsuha'})
    if resume:
        center.set_paused(False)
    def advance(seconds):
        now[0]+=seconds;gs.stats_updated_at=now[0];state.passive_map_monitor._result['captured_at']=now[0]
    return center,state,advance


def player(state, cid=0x10000002, name='Visitor'):
    state.game_state.creatures[cid]={'name':name}
    return {'x':101,'y':100,'z':7,'alert_players':[{'id':cid}]}


def message(kind='private',sender='Visitor',text='Hello',statement=1):
    return dict(kind=kind,sender=sender,text=text,statement=statement)


def test_new_session_starts_paused_and_resumes_without_replaying_history(monkeypatch):
    import asyncio
    import mcp_server as server

    config = default_config()
    config['default']['enabled'] = False
    c, s, _ = setup(resume=False)
    c.configure(config)
    monkeypatch.setattr(server, 'state', NS(alert_center=c))
    def pause(value):
        asyncio.run(server._async_alerts('set_paused', {'paused': value}))

    assert c.snapshot()['paused']
    c.observe_chat(message(), s, s.game_state)
    snap = c.snapshot()
    assert snap['latest_id'] == snap['silenced_through'] == 1 and snap['unread'] == 0
    assert not snap['events'][0]['sound']
    for invalid in (None, 0, 'false'):
        with pytest.raises(ValueError):
            pause(invalid)

    pause(False)
    assert c.snapshot()['unread'] == 0
    c.observe_chat(message(statement=2), s, s.game_state)
    pause(False)
    snap = c.snapshot()
    assert not snap['paused'] and snap['unread'] == 1 and snap['events'][-1]['sound']
    assert snap['config'] == config
    pause(True)
    assert c.snapshot()['silenced_through'] == 2 and c.snapshot()['unread'] == 0
    assert AlertCenter(config).snapshot()['paused']


def test_scopes_are_independent_and_never_change_actions():
    c,s,advance=setup();config=default_config();config['private']['scope']='cavebot'
    c.configure(config);s.adaptive_cavebot.status='paused'
    c.observe_chat(message(),s,s.game_state);assert not c.snapshot()['events']
    s.passive_map_monitor._result['cells']=[player(s)]
    c.observe_players(s);assert c.snapshot()['events'][0]['kind']=='player'
    s.adaptive_cavebot.status='running';advance(3)
    c.observe_chat(message(statement=2),s,s.game_state);assert c.snapshot()['events'][-1]['kind']=='private'
    assert s.adaptive_cavebot.status=='running' and not s.automation_paused
    s.automation_paused=True;c.observe_chat(message(statement=3),s,s.game_state)
    assert len(c.snapshot()['events'])==2


def test_waiting_for_blocker_is_running_but_finished_task_and_idle_are_not():
    c,s,_=setup();cfg=default_config();cfg['private']['scope']='cavebot';c.configure(cfg)
    s.adaptive_cavebot.navigation_hold='blocker';c.observe_chat(message(),s,s.game_state)
    assert len(c.events)==1
    s._action_tasks['adaptive_cavebot']=NS(done=lambda:True)
    c.observe_chat(message(statement=2),s,s.game_state);assert len(c.events)==1


def test_own_npc_spell_and_unknown_speaker_are_filtered():
    c,s,_=setup();gs=s.game_state
    for msg in [message(sender='ME'),message('default'),message('default',sender='Guard')]:
        c.observe_chat(msg,s,gs)
    assert not c.events
    s.passive_map_monitor._result['cells']=[player(s)];c.observe_players(s)
    c.observe_chat(message('default',text='Shunkan!'),s,gs);assert len(c.events)==1
    c.observe_chat(message('default'),s,gs);assert len(c.events)==2
    assert c.events[-1]['text']=='Hello'


def test_player_jitter_reentry_cooldown_self_and_other_floor():
    c,s,advance=setup();result=s.passive_map_monitor._result
    result['cells']=[player(s),player(s,cid=s.game_state.player_id),{**player(s,cid=0x10000003),'z':6}]
    c.observe_players(s);assert len(c.events)==1
    advance(.5);c.observe_players(s);assert len(c.events)==1
    result['cells']=[];advance(3);c.observe_players(s)
    result['cells']=[player(s)];c.observe_players(s);assert len(c.events)==1
    result['status']='waiting';advance(20);c.observe_players(s);assert len(c.events)==1
    result['status']='ready';c.observe_players(s);assert len(c.events)==2


def test_disconnected_and_stale_input_never_emit():
    c,s,_=setup();s.connected=False
    s.passive_map_monitor._result['cells']=[player(s)];c.observe_players(s)
    c.observe_chat(message(),s,s.game_state);assert not c.events
    s.connected=True;s.game_state.stats_updated_at=1
    c.observe_chat(message(),s,s.game_state);assert not c.events
    s.game_state.stats_updated_at=100;s.passive_map_monitor._result['captured_at']=1
    c.observe_players(s);assert not c.events
    c.observe_chat(message(),s,GameState());assert not c.events


def test_disabled_events_are_not_replayed_on_enable():
    c,s,_=setup();cfg=default_config();cfg['player']['enabled']=False;c.configure(cfg)
    s.passive_map_monitor._result['cells']=[player(s)];c.observe_players(s)
    cfg['player']['enabled']=True;c.configure(cfg);c.observe_players(s);assert not c.events


def test_transient_stale_stats_do_not_repeat_player_arrival():
    c,s,advance=setup()
    s.passive_map_monitor._result['cells']=[player(s)]
    c.observe_players(s)
    s.game_state.stats_updated_at=1
    c.observe_players(s)
    advance(.5);c.observe_players(s)
    assert len(c.events)==1 and c.map_fresh


def test_burst_sounds_coalesce_but_history_and_unread_survive():
    c,s,advance=setup()
    for i in range(4):c.observe_chat(message(statement=i),s,s.game_state)
    assert [e['sound'] for e in c.events]==[True,False,False,False]
    c.observe_chat(message(statement=3),s,s.game_state);assert len(c.events)==4
    c.acknowledge(3);assert c.snapshot()['unread']==1
    advance(3);c.observe_chat(message(statement=5),s,s.game_state);assert c.events[-1]['sound']
    cfg=default_config();cfg['private']['sound']=False;c.configure(cfg)
    advance(3);c.observe_chat(message(statement=6),s,s.game_state);assert not c.events[-1]['sound']
    for i in range(300):c.observe_chat(message(statement=100+i),s,s.game_state)
    assert len(c.events)==100 and len(c.seen_messages)<=256
    with pytest.raises(ValueError):c.acknowledge(c.latest_id+1)
    snap=c.snapshot();snap['events'][0]['text']='tampered';assert c.events[0]['text']=='Hello'


@pytest.mark.parametrize('bad',[None,{},[],{'private':{}}, {**default_config(),'player':{'enabled':1,'sound':True,'scope':'always'}},
    {**default_config(),'default':{'enabled':True,'sound':True,'scope':'idle'}}])
def test_config_rejects_bad_types(bad):
    with pytest.raises(ValueError):validate_config(bad)


def talk(mode=6,name='Visitor',text='Hello',statement=7):
    def string(value):
        raw=value.encode();return struct.pack('<H',len(raw))+raw
    extra=(struct.pack('<HHB',100,100,7) if mode in (1,2,3,4,5,19,20,28) else
           struct.pack('<H',7) if mode in (7,8,13,15) else struct.pack('<I',9) if mode==9 else b'')
    return b'\xaa'+struct.pack('<I',statement)+string(name)+struct.pack('<HB',320,mode)+extra+string(text)


@pytest.mark.parametrize('mode',[1,2,3,6,14])
def test_talk_parsed_only_at_sequential_boundary(mode):
    data=talk(mode);end,parsed=parse_talk(data,1)
    assert end==len(data) and parsed['kind']==('private' if mode in (6,14) else 'default')
    gs=GameState();events=[];gs.alert_chat_callback=lambda m,g:events.append(m)
    scan_packet(data+talk(mode,statement=8),gs);assert len(events)==2
    events.clear();scan_packet(b'\xff'+data,gs);assert not events
    for i in range(len(data)):
        assert parse_talk(data[:i],1) is None


def test_talk_rejects_unknown_modes_and_bounds_strings():
    for mode in [0,16,255]:assert parse_talk(talk(mode),1) is None
    assert parse_talk(talk(name='x'*65),1) is None
    assert parse_talk(talk(text='x'*4097),1) is None
    assert parse_talk(talk(text='a\x00b'),1) is None
    assert len(parse_talk(talk(text='x'*800),1)[1]['text'])==512


@pytest.mark.parametrize('mode',[4,5,7,8,9,10,11,12,13,15,19,20,28])
def test_dbv_nonalert_speech_does_not_hide_a_bundled_private_message(mode):
    ignored=talk(mode)
    end,message=parse_talk(ignored,1)
    assert end==len(ignored) and message['kind'] is None
    gs=GameState();events=[];gs.alert_chat_callback=lambda m,g:events.append(m)
    scan_packet(ignored+talk(6,statement=8),gs)
    assert len(events)==1 and events[0]['kind']=='private' and events[0]['statement']==8
    for length in range(len(ignored)):
        assert parse_talk(ignored[:length],1) is None


def test_dbv_wire_private_reaches_alert_center_without_player_being_visible():
    center,state,_=setup();gs=state.game_state
    gs.alert_chat_callback=lambda message,source:center.observe_chat(message,state,source)
    scan_packet(talk(6)+talk(14,statement=8),gs)
    assert center.chat_received==2
    assert [event['kind'] for event in center.events]==['private','private']
    assert not center.known_players


def test_settings_persist_only_alert_preferences_and_history_stays_in_memory(monkeypatch,tmp_path):
    import asyncio,json
    import mcp_server as server
    settings={'actions':{'auto_potions':{'enabled':True}},'combat_profile':{'enabled':False}}
    monkeypatch.setattr(server,'load_settings',lambda:settings)
    state=server.BotState();monkeypatch.setattr(server,'state',state)
    monkeypatch.setattr(server,'SETTINGS_FILE',tmp_path/'settings.json')
    config=default_config();config['private']['scope']='cavebot';config['player']['sound']=False
    asyncio.run(server._async_alerts('configure',{'config':config}))
    saved=json.loads(server.SETTINGS_FILE.read_text())
    assert saved=={**settings,'alerts':config}
    assert state.alert_center.snapshot()['config']==config and not state._action_tasks
    assert 'events' not in saved and 'session' not in saved
    reloaded=server.BotState();assert reloaded.alert_center.config==config
    previous=state.alert_center.snapshot()
    monkeypatch.setattr(server,'save_settings',lambda value:(_ for _ in ()).throw(OSError('synthetic disk error')))
    with pytest.raises(OSError):asyncio.run(server._async_alerts('configure',{'config':default_config()}))
    assert state.alert_center.snapshot()==previous


def test_vitals_outage_during_hunt_warns_once_before_disconnect_and_rearms(monkeypatch):
    from client_player_state import FRESH_SECONDS
    from potions import PotionScheduler
    from test_potions import config, rule

    center, state, _ = setup()
    clock = [100.]
    center.monotonic = lambda:clock[0]
    monkeypatch.setattr('time.monotonic', center.monotonic)
    gs = state.game_state
    gs.client_state_required = True
    scheduler = PotionScheduler()
    cfg = config(rules=[rule('hp',8465)])
    def sample(hp):
        gs.client_snapshot = dict(hp=hp, max_hp=100, mana=64, max_mana=100,
                                  monotonic=clock[0], stats_updated_at=100.,position=(100,100,7))
    sample(100)
    center.observe_players(state)
    clock[0] += FRESH_SECONDS + .01
    center.observe_players(state)
    assert not center.events and gs.max_hp == 0
    for elapsed in (1.5, 3.923, 9.916, 40.732):
        clock[0] = 100 + elapsed
        center.observe_players(state)
        assert scheduler.next_rule(cfg, gs, clock[0],100) is None
        assert center.snapshot()['vitals_alarm_id'] == 1
    assert len(center.events) == 1 and '100/100' in center.events[0]['text']
    state.connected = False
    center.observe_players(state)
    assert center.snapshot()['vitals_alarm_id'] is None
    state.connected = True
    sample(40)
    center.observe_players(state)
    assert scheduler.next_rule(cfg,gs,clock[0],100)['id'] == 'hp'
    clock[0] += 2
    center.observe_players(state)
    assert center.snapshot()['vitals_alarm_id'] == 2


def test_vitals_alarm_only_after_valid_data_with_active_automation():
    center,state,_ = setup()
    center.monotonic = lambda:100.
    gs = state.game_state
    gs.client_state_required = True
    center.observe_players(state)
    assert not center.events
    state.adaptive_cavebot.status = 'stopped'
    gs.client_snapshot = dict(hp=80,max_hp=100,monotonic=100.,stats_updated_at=100.,position=(100,100,7))
    center.observe_players(state)
    center.monotonic = lambda:102.
    center.observe_players(state)
    assert not center.events
    state._action_tasks['auto_potions'] = NS(done=lambda:False)
    center.monotonic = lambda:100.
    center.observe_players(state)
    center.monotonic = lambda:102.
    center.observe_players(state)
    assert len(center.events) == 1
    state.automation_paused = True
    center.observe_players(state)
    assert center.snapshot()['vitals_alarm_id'] is None


def ki_scene():
    center, state, advance = setup()
    gs = state.game_state
    gs.hp = gs.max_hp = gs.max_mana = 100
    center.monotonic = center.clock
    frame = state.passive_map_monitor._result
    frame.update(width=18,height=14,origin=[92,94])
    frame['cells'] = [
        dict(x=x, y=y, z=7, kind='clear', occupant_types={}, occupants=0)
        for x in range(92, 110) for y in range(94, 108)
    ]
    def sample(ki, elapsed=0):
        advance(elapsed)
        gs.mana = ki
        center.observe_ki(state)
    return center, state, frame, sample


def test_ki_drop_uses_the_recent_high_and_sounds_for_ten_seconds_once():
    c, state, frame, sample = ki_scene()
    state.adaptive_cavebot.status = 'stopped'
    # A classified pet in range or monsters on another floor / outside the radius are fine.
    for x,y,kind in [(105,105,'pet'), (110,100,'monster')]:
        cell = dict(x=x,y=y,z=7,kind='clear')
        if x == 110: frame['cells'].append(cell)
        else: cell = next(v for v in frame['cells'] if (v['x'],v['y'])==(x,y))
        cell.update(occupants=1,occupant_types={kind:1})
    frame['cells'].append(dict(x=100,y=100,z=6,occupants=1,occupant_types={'monster':1}))
    sample(90)
    sample(45,.5)
    sample(5,1)
    assert not c.events
    sample(5,1)
    event, = c.events
    assert event['kind'] == 'ki_loss' and event['position'] == [100,100,7]
    assert c.snapshot()['ki_alarm_id'] == event['id']
    sample(0,9.9)
    assert c.snapshot()['ki_alarm_id'] == event['id'] and len(c.events) == 1
    sample(0,.11)
    assert c.snapshot()['ki_alarm_id'] is None and len(c.events) == 1
    sample(100)
    sample(0,.1)
    sample(0,1)
    assert c.snapshot()['ki_alarm_id'] == 2


@pytest.mark.parametrize('problem', ['monster','unknown_type','missing_tile','unreadable_tile',
                                      'stale_map','future_map','wrong_center','waiting','empty_map'])
def test_ki_alarm_requires_confirmed_empty_nearby_map(problem):
    c, state, frame, sample = ki_scene()
    sample(100)
    corner = next(v for v in frame['cells'] if (v['x'],v['y'])==(109,107))
    if problem == 'monster': corner.update(occupants=1,occupant_types={'monster':1})
    if problem == 'unknown_type': corner.update(occupants=1,occupant_types={'unknown':1})
    if problem == 'missing_tile': frame['cells'].remove(corner)
    if problem == 'unreadable_tile': corner['kind']='unknown'
    if problem == 'stale_map': frame['captured_at']-=2
    if problem == 'future_map': frame['captured_at']+=1
    if problem == 'wrong_center': frame['center']=[100,101,7]
    if problem == 'waiting': frame['status']='refreshing'
    if problem == 'empty_map': frame['cells']=[]
    state.game_state.mana=0
    c.observe_ki(state)
    sample(0,1.1)
    assert not c.events


@pytest.mark.parametrize('problem', ['slow','small','max_change','new_session','teleport','dead','disabled','scope'])
def test_unrelated_ki_samples_do_not_create_alarm(problem):
    c,state,frame,sample=ki_scene()
    cfg = default_config()
    if problem == 'disabled': cfg['ki_loss']['enabled']=False
    if problem == 'scope': cfg['ki_loss']['scope']='cavebot'
    c.configure(cfg)
    state.adaptive_cavebot.status='stopped'
    sample(100)
    if problem == 'max_change': state.game_state.max_mana=200
    if problem == 'new_session': state.game_proxy=object()
    if problem == 'teleport':
        state.game_state.position = (120,100,7)
        frame['center']=[120,100,7]
        frame['origin']=[112,94]
        for tile in frame['cells']: tile['x']+=20
    if problem == 'dead': state.game_state.hp=0
    sample(20 if problem=='small' else 0,1.51 if problem=='slow' else .1)
    sample(20 if problem=='small' else 0,1.1)
    assert not c.events


def test_previous_alert_preferences_survive_adding_ki_alarm():
    old=default_config()
    del old['ki_loss']
    old['private']['sound']=False
    assert AlertCenter(old).config == {**old, 'ki_loss':default_config()['ki_loss']}
    assert 'ki_loss' not in old


@pytest.mark.parametrize('hit_offset, expected', [(-2, True), (-.5, False), (.7, False)])
def test_offscreen_attack_text_suppresses_alarm_even_after_ki_sample(hit_offset, expected):
    import struct
    from unittest.mock import Mock
    from packet_observer import apply_scan, detach_state, publish_server_feedback
    c,state,_,sample=ki_scene()
    gs=state.game_state
    sample(100)
    sample(0,.5)
    scan=detach_state(gs,0)
    scan.received_at=100.5+hit_offset
    text=b'You lose 17095 ki points blocking an attack by a garlic.'
    scan_packet(b'\xb4\x19'+struct.pack('<H',len(text))+text,scan.state)
    apply_scan(gs,scan)
    publish_server_feedback(gs,scan,Mock())
    assert gs.last_incoming_hit == scan.received_at
    sample(100,1)
    assert bool(c.events) == expected
