import struct
from types import SimpleNamespace as NS
import pytest
from game_alerts import AlertCenter, default_config, validate_config
from game_state import GameState, scan_packet
from chat_messages import parse_talk


def setup():
    now=[100.0]
    gs=GameState(); gs.player_id=0x10000001;gs.player_name='Me';gs.stats_updated_at=100;gs.position=(100,100,7)
    state=NS(game_state=gs,connected=True,game_proxy=object(),automation_paused=False,
             adaptive_cavebot=NS(status='running'),_action_tasks={'adaptive_cavebot':NS(done=lambda:False)},
             passive_map_monitor=NS(_result={'status':'ready','captured_at':100,'center':[100,100,7],'cells':[]}))
    center=AlertCenter(clock=lambda:now[0],spells={'shunkan','shinretsuha'})
    def advance(seconds):
        now[0]+=seconds;gs.stats_updated_at=now[0];state.passive_map_monitor._result['captured_at']=now[0]
    return center,state,advance


def player(state, cid=0x10000002, name='Visitor'):
    state.game_state.creatures[cid]={'name':name}
    return {'x':101,'y':100,'z':7,'alert_players':[{'id':cid}]}


def message(kind='private',sender='Visitor',text='Hello',statement=1):
    return dict(kind=kind,sender=sender,text=text,statement=statement)


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


def talk(mode=4,name='Visitor',text='Hello',statement=7):
    def string(value):
        raw=value.encode();return struct.pack('<H',len(raw))+raw
    return b'\xaa'+struct.pack('<I',statement)+string(name)+struct.pack('<HB',320,mode)+(struct.pack('<HHB',100,100,7) if mode in (1,2,3) else b'')+string(text)


@pytest.mark.parametrize('mode',[1,2,3,4])
def test_talk_parsed_only_at_sequential_boundary(mode):
    data=talk(mode);end,parsed=parse_talk(data,1)
    assert end==len(data) and parsed['kind']==('private' if mode==4 else 'default')
    gs=GameState();events=[];gs.alert_chat_callback=lambda m,g:events.append(m)
    scan_packet(data+talk(mode,statement=8),gs);assert len(events)==2
    events.clear();scan_packet(b'\xff'+data,gs);assert not events
    for i in range(len(data)):
        assert parse_talk(data[:i],1) is None


def test_talk_rejects_unknown_spell_and_npc_modes_and_bounds_strings():
    for mode in [5,9,10,11,12,255]:assert parse_talk(talk(mode),1) is None
    assert parse_talk(talk(name='x'*65),1) is None
    assert parse_talk(talk(text='x'*4097),1) is None
    assert parse_talk(talk(text='a\x00b'),1) is None
    assert len(parse_talk(talk(text='x'*800),1)[1]['text'])==512


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
