"""Synthetic combat transport; no game, process access or real packets."""
import asyncio
import json
import time
from types import SimpleNamespace
import pytest
from cavebot_combat import validate_combat,matches,eligible_monsters,choose_target
from map_atlas import MapAtlas
from protocol import ClientOpcode,PacketReader
from test_adaptive_cavebot import setup,cell,connect_fake_walk
from test_automation_safety import state,proxy
from test_dashboard_security import http_api


def configure_rules(controller,rules,**kwargs):
    controller.combat.config=validate_combat({**controller.combat.config,**kwargs,'rules':rules})


def test_legacy_rules_default_to_attack_and_inherit_and_roundtrip(tmp_path):
    config=validate_combat(dict(rules=[dict(pattern='Wolf')]))
    rule=config['rules'][0]
    assert rule['action']=='attack' and rule['stance']=='inherit' and rule['spell_mode']=='inherit'
    config=validate_combat(dict(rules=[dict(pattern='Dragon*',stance='distance',distance=3,spell_mode='custom',spell=dict(words='Spell',range=4))]))
    atlas=MapAtlas(tmp_path,'c'*64)
    draft=atlas.save_draft('profile',[[10,10,7],[11,10,7]])
    atlas.change_draft(draft,'configure',config,False)
    reopened=MapAtlas(tmp_path,'c'*64).drafts()[0]
    assert reopened['combat']==config


@pytest.mark.parametrize('rule',[
    dict(stance='teleport'),dict(stance='distance',distance=1),dict(distance=True),dict(spell_mode='any'),
    dict(spell_mode='custom'),dict(spell_mode='custom',spell=dict(words='hi\nbye')),
    dict(stance='distance',distance=4,spell_mode='custom',spell=dict(words='Spell',range=3)),
])
def test_invalid_monster_overrides_are_rejected(rule):
    with pytest.raises(ValueError):validate_combat(dict(rules=[dict(pattern='*',**rule)]))


def test_per_monster_spells_replace_shared_spells_and_keep_cooldown_on_target_change(state,tmp_path):
    c,cells=armed(state,tmp_path,spells=[dict(words='Shared Spell',cooldown=5)])
    configure_rules(c,[dict(pattern='Wolf Alpha',priority=90,spell_mode='custom',spell=dict(words='Only Spell',cooldown=5)),
                       dict(pattern='Wolf Beta',priority=80,spell_mode='custom',spell=dict(words='only spell',cooldown=.2)),
                       dict(pattern='Wolf Silent',priority=70,spell_mode='none'),dict(pattern='*',priority=10)])
    monsters=[monster(state,cells,name=name,x=x,y=y,cid=0x40000001+i) for i,(name,x,y) in enumerate([
        ('Wolf Alpha',11,10),('Wolf Beta',12,11),('Wolf Silent',13,11),('Wolf',12,12)])]
    state.game_state.mana=state.game_state.max_mana=100;c.status='running';sent=[]
    async def inject(payload,guard=None):assert guard();sent.append(payload);c.last_command=0
    state.game_proxy.inject_to_server=inject
    async def scenario():
        for cid,row,tile in monsters:
            for _,r,_ in monsters:r['t']=time.time()
            c.combat.cast_after=0
            await c.combat.tick(state.passive_map_monitor.snapshot())
            assert c.combat.sent_target==cid
            row['health']=0;tile.update(occupants=0,occupant_types={})
    asyncio.run(scenario())
    speech=[p for p in sent if p[0]==ClientOpcode.SAY]
    assert len(speech)==2 and b'Only Spell' in speech[0] and b'Shared Spell' in speech[1]


def test_specific_stand_rule_overrides_global_chase(state,tmp_path):
    c,cells=armed(state,tmp_path,mode='chase');configure_rules(c,[dict(pattern='*',stance='stop')])
    monster(state,cells,x=13);c.status='running'
    asyncio.run(c.combat.tick(state.passive_map_monitor.snapshot()))
    assert state.game_state.position==(10,10,7) and c.combat.phase=='fight'
    assert state.game_proxy._inject_queue.qsize()==1


def test_ranged_rule_retreats_then_attacks_and_returns_to_anchor(state,tmp_path):
    cells=[cell(x,y) for x in range(7,16) for y in range(7,16)]
    c,cells,_=setup(state,tmp_path,cells=cells)
    configure_rules(c,[dict(pattern='*',stance='distance',distance=3)],enabled=True)
    cid,row,tile=monster(state,cells,x=11,y=11);c.status='running'
    walk=connect_fake_walk(state,c);walk_inject=state.game_proxy.inject_to_server;attacks=[]
    async def inject(payload,guard=None):
        if payload[0]==ClientOpcode.ATTACK:assert guard();attacks.append(payload);c.last_command=0
        else:await walk_inject(payload,guard)
    state.game_proxy.inject_to_server=inject
    async def scenario():
        for _ in range(5):
            row['t']=time.time();await c.combat.tick(state.passive_map_monitor.snapshot())
            if attacks:break
        assert walk and attacks and c.index==0
        assert max(abs(n-m) for n,m in zip(state.game_state.position[:2],(11,11)))>=3
        state.game_state.creatures.clear();tile.update(occupants=0,occupant_types={})
        for _ in range(8):
            if not await c.combat.tick(state.passive_map_monitor.snapshot()):break
        assert state.game_state.position==(10,10,7) and c.combat.anchor is None
    asyncio.run(scenario())


def test_ranged_rule_does_not_escape_through_stairs_or_unknown_tiles(state,tmp_path):
    c,cells=armed(state,tmp_path)
    configure_rules(c,[dict(pattern='*',stance='distance',distance=3)])
    monster(state,cells,x=11,y=11);c.status='running'
    # Only a staircase and an unknown cell would provide the requested gap.
    cells.extend([cell(8,10,navigation=[dict(kind='stairs')]),cell(9,10,kind='unknown')])
    assert asyncio.run(c.combat.tick(state.passive_map_monitor.snapshot()))
    assert c.status=='running' and 'czekam' in c.reason
    assert state.game_proxy._inject_queue.empty() and state.game_state.position==(10,10,7)


@pytest.mark.parametrize('stance',['chase','lure','distance'])
def test_per_monster_movement_override_cannot_move_combat_only(state,tmp_path,stance):
    c,cells,_=setup(state,tmp_path)
    c.prepare_combat_only(dict(rules=[dict(pattern='*',stance=stance,distance=3)],spells=[dict(words='Spell')]))
    monster(state,cells,x=11,y=11);c.status='running'
    state.game_state.mana=state.game_state.max_mana=100;sent=[]
    async def inject(payload,guard=None):assert guard();sent.append(payload);c.last_command=0
    state.game_proxy.inject_to_server=inject
    asyncio.run(c.combat.tick(state.passive_map_monitor.snapshot()))
    assert state.game_state.position==(10,10,7) and c.combat.anchor is None
    if stance=='distance':assert not sent and 'ręczny' in c.reason
    else:assert [p[0] for p in sent]==[ClientOpcode.ATTACK,ClientOpcode.SAY]


def test_ranged_attack_guard_rechecks_distance_and_cancels_when_monster_approaches(state,tmp_path):
    c,cells=armed(state,tmp_path);c.combat_only=True
    configure_rules(c,[dict(pattern='*',stance='distance',distance=3)])
    cid,row,tile=monster(state,cells,x=13,y=10);c.status='running'
    async def scenario():
        await c.combat.tick(state.passive_map_monitor.snapshot())
        _,_,guard=state.game_proxy._inject_queue.get_nowait();assert guard()
        tile.update(occupants=0,occupant_types={});row.update(x=11,t=time.time())
        next(t for t in cells if (t['x'],t['y'])==(11,10)).update(occupants=1,occupant_types={'monster':1})
        assert not guard()
        await c.combat.tick(state.passive_map_monitor.snapshot())
        assert c.combat.sent_target==0
        _,payload,_=state.game_proxy._inject_queue.get_nowait()
        assert payload==b'\xa1\0\0\0\0' and state.game_proxy._inject_queue.empty()
    asyncio.run(scenario())


def monster(state,cells,name='Wolf',x=12,y=11,cid=0x40000001):
    c=dict(name=name,x=x,y=y,z=7,health=100,t=time.time(),source='dll')
    state.game_state.creatures[cid]=c
    tile=next(c for c in cells if (c['x'],c['y'])==(x,y));tile.update(occupants=1,occupant_types={'monster':1})
    return cid,c,tile


def armed(state,tmp_path,mode='stop',**extra):
    c,cells,draft=setup(state,tmp_path)
    cfg=validate_combat(dict(enabled=True,mode=mode,rules=[dict(pattern='Wolf*',action='attack')],**extra))
    state.passive_map_monitor.atlas.change_draft(draft,'configure',cfg,False)
    c.prepare(c.scope,draft,False)
    return c,cells


def test_patterns_are_case_insensitive_and_only_star_is_special():
    assert matches('wOlF*','Wolf Alpha') and matches('*','Dragon')
    assert not matches('Wolf?','WolfA') and not matches('[W]olf','Wolf')
    assert matches('a.b*','a.b c') and not matches('a.b*','axb')


@pytest.mark.parametrize('change',[dict(mode='follow'),dict(radius=99),dict(enabled=1),dict(spells=[dict(words='hi\nbye')]),dict(spells=[dict(words='Spell',cooldown=float('nan'))]),dict(rules=[dict(pattern='')])])
def test_invalid_combat_configs_rejected(change):
    with pytest.raises(ValueError):validate_combat(change)


def test_monster_filter_rejects_players_npcs_stale_and_ambiguous_identity(state,tmp_path):
    c,cells=armed(state,tmp_path);cid,row,tile=monster(state,cells)
    sample=state.passive_map_monitor.snapshot();cfg=c.combat.config
    assert len(eligible_monsters(state.game_state,sample,cfg))==1
    for kind in ('npc','player','unknown','self'):
        tile['occupant_types']={kind:1}
        assert not eligible_monsters(state.game_state,sample,cfg)
    tile['occupant_types']={'monster':1};row['t']-=2
    assert not eligible_monsters(state.game_state,sample,cfg)
    row['t']=time.time();state.game_state.creatures[cid+1]=dict(row)
    assert not eligible_monsters(state.game_state,sample,cfg)
    state.game_state.creatures.pop(cid+1)
    assert not eligible_monsters(state.game_state,{**sample,'stale':True},cfg)


@pytest.mark.parametrize('old_hp,age', [(0,0), (85,2)])
def test_dead_or_expired_creature_on_same_tile_does_not_hide_live_target(state,tmp_path,old_hp,age):
    c,cells=armed(state,tmp_path)
    cid,row,_=monster(state,cells)
    now=time.time()
    state.game_state.creatures[cid+1]={**row,'health':old_hp,'t':now-age}
    targets=eligible_monsters(state.game_state,state.passive_map_monitor.snapshot(),c.combat.config,now)
    assert [t['id'] for t in targets]==[cid]


def test_ordered_exclusions_priority_and_strategy(state,tmp_path):
    c,cells=armed(state,tmp_path);a,_,_=monster(state,cells,name='Wolf Alpha',x=12);b,_,_=monster(state,cells,name='Wolf',x=11,cid=0x40000002)
    cfg=validate_combat(dict(rules=[dict(pattern='Wolf Alpha',action='ignore'),dict(pattern='*',action='attack')]))
    targets=eligible_monsters(state.game_state,state.passive_map_monitor.snapshot(),cfg)
    assert [t['id'] for t in targets]==[b]
    cfg['rules']=[dict(pattern='Wolf Alpha',action='attack'),dict(pattern='*',action='attack')]
    targets=eligible_monsters(state.game_state,state.passive_map_monitor.snapshot(),cfg)
    assert choose_target(targets,cfg)['id']==a
    for t in targets:t['priority']=0
    targets[0]['health']=10;cfg['strategy']='lowest_hp'
    assert choose_target(targets,cfg)['id']==a
    cfg['strategy']='finish';assert choose_target(targets,cfg,b)['id']==b


def test_stand_fight_cast_and_return_to_route_without_repeated_attack(state,tmp_path):
    c,cells=armed(state,tmp_path,spells=[dict(words='Darkness Blaster',cooldown=2,ki=10,range=5,count=1)])
    cid,row,tile=monster(state,cells);state.game_state.mana=state.game_state.max_mana=100
    sent=[]
    async def inject(payload,guard=None):
        assert guard and guard();sent.append(payload);c.last_command=0
    state.game_proxy.inject_to_server=inject;c.status='running'
    async def scenario():
        assert await c.combat.tick(state.passive_map_monitor.snapshot())
        assert await c.combat.tick(state.passive_map_monitor.snapshot())
        assert c.index==0 and state.game_state.position==(10,10,7)
        state.game_state.creatures.clear();tile.update(occupants=0,occupant_types={})
        assert not await c.combat.tick(state.passive_map_monitor.snapshot())
    asyncio.run(scenario())
    assert [p[0] for p in sent]==[ClientOpcode.ATTACK,ClientOpcode.SAY,ClientOpcode.ATTACK]
    assert sent[-1]==b'\xa1\0\0\0\0'


def test_chase_has_one_movement_owner_and_returns_to_anchor(state,tmp_path):
    c,cells=armed(state,tmp_path,mode='chase');cid,row,tile=monster(state,cells,x=13)
    walked=connect_fake_walk(state,c);walk=state.game_proxy.inject_to_server;sent=[]
    async def inject(payload,guard=None):
        if payload[0]==ClientOpcode.ATTACK:assert guard();sent.append(payload)
        else:await walk(payload,guard)
    state.game_proxy.inject_to_server=inject;c.status='running'
    async def scenario():
        assert await c.combat.tick(state.passive_map_monitor.snapshot())
        assert state.game_state.position!=(10,10,7) and c.index==0
        state.game_state.creatures.clear();tile.update(occupants=0,occupant_types={})
        for _ in range(5):
            if not await c.combat.tick(state.passive_map_monitor.snapshot()):break
        assert state.game_state.position==(10,10,7) and c.combat.anchor is None
    asyncio.run(scenario());assert len(walked)>=2


def test_lure_walks_route_then_stops_at_limit_or_transition(state,tmp_path):
    c,cells=armed(state,tmp_path,mode='lure');monster(state,cells);c.status='running'
    sent=[]
    async def inject(payload,guard=None):assert guard();sent.append(payload)
    state.game_proxy.inject_to_server=inject
    async def scenario():
        assert not await c.combat.tick(state.passive_map_monitor.snapshot())
        assert len(sent)==1 and sent[0][0]==ClientOpcode.ATTACK
        assert c.combat.phase=='lure'
        assert await c.combat.tick(state.passive_map_monitor.snapshot(),at_transition=True)
        assert c.combat.phase=='fight'
    asyncio.run(scenario());assert len(sent)==1 and sent[0][0]==ClientOpcode.ATTACK


def test_numeric_danger_overrides_list_order_and_exclusion_still_wins(state,tmp_path):
    c,cells=armed(state,tmp_path)
    low,_,_=monster(state,cells,name='Wolf',x=11)
    high,_,_=monster(state,cells,name='Danger Wolf',x=13,cid=0x40000002)
    cfg=validate_combat(dict(rules=[dict(pattern='Wolf',priority=10),dict(pattern='Danger*',priority=90)]))
    targets=eligible_monsters(state.game_state,state.passive_map_monitor.snapshot(),cfg)
    assert choose_target(targets,cfg)['id']==high
    cfg['rules'].insert(0,dict(pattern='Danger*',action='ignore',priority=0))
    assert choose_target(eligible_monsters(state.game_state,state.passive_map_monitor.snapshot(),cfg),cfg)['id']==low
    for value in (-1,101,True,'50'):
        with pytest.raises(ValueError):validate_combat(dict(rules=[dict(pattern='*',priority=value)]))


def test_lure_attacks_and_casts_while_moving_waits_at_gap_and_stops_at_four(state,tmp_path):
    c,cells=armed(state,tmp_path,mode='lure',lure_count=4,lure_distance=3,
                  spells=[dict(words='Spell',cooldown=2,ki=1,range=5,count=1)])
    c.status='running';state.game_state.mana=state.game_state.max_mana=100
    cells[:]=[cell(x,y) for x in range(6,18) for y in range(7,14)]
    rows=[monster(state,cells,x=x,y=y,cid=0x40000001+i)[1]
          for i,(x,y) in enumerate(((9,9),(9,10),(9,11),(8,10)))]
    sent=[]
    async def inject(payload,guard=None):assert guard();sent.append(payload);c.last_command=0
    state.game_proxy.inject_to_server=inject
    def frame(player_x,advance=0):
        state.game_state.position=(player_x,10,7)
        for tile in cells:tile.update(occupants=0,occupant_types={})
        for row in rows:
            row.update(x=row['x']+advance,t=time.time())
            next(t for t in cells if (t['x'],t['y'])==(row['x'],row['y'])).update(occupants=1,occupant_types={'monster':1})
        return state.passive_map_monitor.snapshot()
    async def scenario():
        assert not await c.combat.tick(frame(10))
        assert [p[0] for p in sent]==[ClientOpcode.ATTACK,ClientOpcode.SAY]
        assert c.combat.snapshot()['lure']['following']==0  # four visible != four followers
        assert not await c.combat.tick(frame(11,1))
        assert c.combat.snapshot()['lure']['following']==0  # first pursuit response
        # Force a spell due at the instant the fourth follower is confirmed.
        c.combat.cast_after=0;c.combat.deadlines.clear()
        assert await c.combat.tick(frame(12,1))
        assert c.combat.phase=='fight' and c.combat.battle_started is not None
        assert c.combat.snapshot()['lure']==dict(following=4,goal=4,waiting=False,stop_reason='group',engaged=4,nearby=3)
        assert sent[-1][0]==ClientOpcode.SAY and c.combat.sent_target
        # Remain stopped until all eligible monsters have gone.
        state.game_state.creatures={0x40000001:rows[0]}
        assert await c.combat.tick(state.passive_map_monitor.snapshot())
        assert c.combat.phase=='fight'
        state.game_state.creatures.clear()
        for tile in cells:tile.update(occupants=0,occupant_types={})
        assert await c.combat.tick(state.passive_map_monitor.snapshot())
        c.combat.group_clear_since=time.monotonic()-.5
        assert not await c.combat.tick(state.passive_map_monitor.snapshot())
        assert c.combat.phase=='route'
    asyncio.run(scenario())
    assert sent[-1]==b'\xa1\0\0\0\0'


def test_lure_waits_for_farthest_confirmed_follower_and_clears_on_pause(state,tmp_path):
    c,cells=armed(state,tmp_path,mode='lure');c.status='running'
    # The group is east of us; the route heads west, away from it.
    c.path=[[10,10,7],[9,10,7],[8,10,7]];c.index=0
    _,near,_=monster(state,cells,x=11,y=11)
    _,far,_=monster(state,cells,x=13,y=11,cid=0x40000002)
    candidates=eligible_monsters(state.game_state,state.passive_map_monitor.snapshot(),c.combat.config)
    # Exact movement traces are tested separately; this checks route pacing.
    c.combat.pursuit.update=lambda *args:candidates
    async def inject(payload,guard=None):assert guard()
    state.game_proxy.inject_to_server=inject
    assert asyncio.run(c.combat.tick(state.passive_map_monitor.snapshot()))
    assert c.combat.phase=='lure' and c.combat.lure_waiting
    c.combat.pursuit.following={c['id'] for c in candidates}
    c.pause('test pause')
    assert not c.combat.pursuit.following and c.combat.lure_started is None
    c.combat.pursuit.following={1};c.stop()
    assert not c.combat.pursuit.following


def test_lure_timeout_is_not_reported_as_a_complete_group(state,tmp_path):
    c,cells=armed(state,tmp_path,mode='lure');c.status='running';monster(state,cells,x=10,y=11)
    async def inject(payload,guard=None):assert guard()
    state.game_proxy.inject_to_server=inject
    async def scenario():
        await c.combat.tick(state.passive_map_monitor.snapshot())
        c.combat.lure_started=time.monotonic()-9
        assert await c.combat.tick(state.passive_map_monitor.snapshot())
        assert c.combat.snapshot()['lure']['stop_reason']=='timeout'
        assert c.combat.snapshot()['lure']['following']==0 and c.combat.phase=='fight'
    asyncio.run(scenario())


def test_target_preview_http_contract_is_wrapped_and_never_attacks(http_api,state,tmp_path,monkeypatch):
    import threading
    import sys
    import mcp_server as server
    import dashboard_api as api
    from dashboard_security import DASHBOARD_ORIGIN,SESSION_TOKEN
    c,cells=armed(state,tmp_path);monster(state,cells)
    loop=asyncio.new_event_loop();thread=threading.Thread(target=loop.run_forever,daemon=True);thread.start()
    monkeypatch.setattr(api,'_state',state);monkeypatch.setattr(api,'_main_loop',loop)
    monkeypatch.setattr(sys.modules['__main__'],'_async_adaptive_preview',server._async_adaptive_preview,raising=False)
    request,_=http_api
    try:
        status,_,body=request('POST','/api/adaptive/preview',json.dumps({'combat':c.combat.config}),Origin=DASHBOARD_ORIGIN,Authorization=f'Bearer {SESSION_TOKEN}')
        data=json.loads(body)
        assert status==200 and data['ok'] is True
        assert isinstance(data['message']['message'],str) and len(data['message']['targets'])==1
        assert data['message']['selected']['name']=='Wolf'
        assert not state._action_tasks and state.game_proxy._inject_queue.empty()
    finally:loop.call_soon_threadsafe(loop.stop);thread.join(timeout=2);loop.close()


def test_visible_and_near_target_conditions_and_spell_ranges(state,tmp_path):
    from cavebot_combat import spell_reaches
    c,cells=armed(state,tmp_path);cid,row,_=monster(state,cells,x=13,y=11)
    cfg=validate_combat(dict(rules=[dict(pattern='*')],engage='near',radius=1))
    sample=state.passive_map_monitor.snapshot()
    assert not eligible_monsters(state.game_state,sample,cfg)
    cfg['engage']='visible';assert eligible_monsters(state.game_state,sample,cfg)[0]['id']==cid
    target=dict(distance=3)
    assert spell_reaches(dict(trigger='visible',range=1),target)
    assert not spell_reaches(dict(trigger='distance',range=1),target)
    target['distance']=1
    assert spell_reaches(dict(trigger='distance',range=1),target)
    cfg['engage']='near';state.game_state.position=(12,10,7);sample=state.passive_map_monitor.snapshot()
    assert eligible_monsters(state.game_state,sample,cfg)[0]['distance']==1  # diagonal neighbor
    for invalid in [dict(engage='any'),dict(spells=[dict(words='Spell',trigger='invalid')])]:
        with pytest.raises(ValueError):validate_combat(invalid)


def test_combat_only_waits_for_mobs_allows_manual_walk_and_never_sends_movement(state,tmp_path):
    c,cells,_=setup(state,tmp_path)
    cfg=dict(mode='lure',engage='visible',rules=[dict(pattern='Wolf*',priority=80)],
             spells=[dict(words='Spell',trigger='visible',range=1,cooldown=2)])
    c.prepare_combat_only(cfg);assert c.combat.config['mode']=='stop' and c.draft_id is None
    state.game_state.mana=state.game_state.max_mana=100
    sent=[]
    async def inject(payload,guard=None):
        assert guard();sent.append(payload);c.last_command=0
    state.game_proxy.inject_to_server=inject
    async def scenario():
        task=asyncio.create_task(c.run())
        try:
            await asyncio.sleep(.07)
            assert not task.done() and not sent
            # The user moves manually; combat must not take over or move back.
            for callback in list(state.game_proxy._client_packet_callbacks):callback(ClientOpcode.WALK_EAST,PacketReader(b''))
            state.game_state.position=(11,10,7)
            cid,row,tile=monster(state,cells,x=13,y=11)
            for _ in range(20):
                if any(p[0]==ClientOpcode.SAY for p in sent):break
                await asyncio.sleep(.02)
            assert any(p[0]==ClientOpcode.ATTACK for p in sent) and any(p[0]==ClientOpcode.SAY for p in sent)
            state.game_state.creatures.clear();tile.update(occupants=0,occupant_types={})
            await asyncio.sleep(.07)
            assert c.status=='running' and state.game_state.position==(11,10,7) and c.combat.anchor is None
            with pytest.raises(ValueError,match='nie może'):
                await c.send(b'\x66')
        finally:
            c.pause('test done');await asyncio.wait_for(task,1)
    asyncio.run(scenario())
    assert set(p[0] for p in sent)<={ClientOpcode.ATTACK,ClientOpcode.SAY}
    assert not state._action_tasks


def test_combat_profile_save_is_passive_and_start_stop_never_stops_manual_walking(state,tmp_path):
    import mcp_server as server
    c,cells,_=setup(state,tmp_path);sent=[]
    async def inject(payload,guard=None):
        assert guard and guard();sent.append(payload)
    state.game_proxy.inject_to_server=inject
    async def scenario():
        cfg=dict(rules=[dict(pattern='Wolf*')],engage='visible')
        await server._async_adaptive_control('configure_combat',{'combat':cfg})
        assert not state._action_tasks and not sent
        result=await server._async_adaptive_control('start_combat',{})
        assert result['mode']=='combat'
        await asyncio.sleep(.06)
        await server._async_adaptive_control('stop')
        assert state.adaptive_cavebot.status=='idle'
        assert not state.settings['actions']['adaptive_cavebot']['enabled']
    asyncio.run(scenario())
    assert not sent  # in particular no STOP_WALK, even when stopping combat


def test_pause_invalidates_pending_combat_and_manual_attack_is_not_cancelled(state,tmp_path):
    c,cells=armed(state,tmp_path);monster(state,cells);c.status='running'
    asyncio.run(c.combat.tick(state.passive_map_monitor.snapshot()))
    _,_,guard=state.game_proxy._inject_queue.get_nowait();assert guard()
    c.pause('user');assert not guard()
    c.combat.manual_override=True;asyncio.run(c.combat.cancel_attack())
    assert state.game_proxy._inject_queue.empty()


def test_low_hp_no_longer_stops_v2_but_death_does(state,tmp_path):
    c,_,draft=setup(state,tmp_path);state.game_state.hp=5;c.prepare(c.scope,draft,False)
    state.game_state.hp=0
    with pytest.raises(ValueError):c.guard()


def test_manual_movement_keeps_owned_attack(state,tmp_path):
    c,cells=armed(state,tmp_path);monster(state,cells);sent=[]
    async def inject(payload,guard=None):
        if not guard():return
        sent.append(payload)
    state.game_proxy.inject_to_server=inject
    async def scenario():
        task=asyncio.create_task(c.run())
        try:
            await asyncio.sleep(.04)
            for cb in list(state.game_proxy._client_packet_callbacks):cb(ClientOpcode.WALK_EAST,PacketReader(b''))
            await asyncio.sleep(.06)
            assert c.status=='running' and c.combat.sent_target
            assert not any(p==b'\xa1\0\0\0\0' for p in sent)
        finally:c.pause('test done');await asyncio.wait_for(task,1)
    asyncio.run(scenario())


def test_drafts_delete_restore_keep_combat_and_do_not_overwrite_from_editor(tmp_path):
    a=MapAtlas(tmp_path,'a'*64);points=[[10,10,7],[11,10,7]];draft=a.save_draft('test',points)
    combat=validate_combat(dict(rules=[dict(pattern='Wolf*',action='attack')]))
    a.change_draft(draft,'configure',combat,False);a.save_draft('renamed',points,draft,True)
    assert a.drafts()[0]['combat']==combat
    a.change_draft(draft,'delete');assert a.drafts()==[] and a.drafts(True)[0]['points']==points
    with pytest.raises(ValueError):a.save_draft('bad',points,draft)
    a=MapAtlas(tmp_path,'a'*64);a.change_draft(draft,'restore')
    assert a.drafts()[0]['combat']==combat
    with pytest.raises(ValueError):MapAtlas(tmp_path,'b'*64).change_draft(draft,'delete')


def test_draft_api_requires_scope_origin_and_stopped_route(http_api,monkeypatch,tmp_path):
    import dashboard_api as api
    from dashboard_security import DASHBOARD_ORIGIN,SESSION_TOKEN
    a=MapAtlas(tmp_path,'a'*64);draft=a.save_draft('test',[[10,10,7],[11,10,7]])
    controller=SimpleNamespace(status='running',draft_id=draft)
    monkeypatch.setattr(api,'_state',SimpleNamespace(passive_map_monitor=SimpleNamespace(atlas=a),adaptive_cavebot=controller))
    request,_=http_api;headers={'Origin':DASHBOARD_ORIGIN,'Authorization':f'Bearer {SESSION_TOKEN}'}
    payload=dict(scope=a.scope,id=draft,operation='delete')
    assert request('POST','/api/map/drafts',body=json.dumps(payload),**headers)[0]==400
    controller.status='idle'
    assert request('POST','/api/map/drafts',body=json.dumps(payload),**{**headers,'Origin':'https://evil.example'})[0]==403
    assert request('POST','/api/map/drafts',body=json.dumps({**payload,'scope':'wrong'}),**headers)[0]==400
    assert len(a.drafts())==1
    assert request('POST','/api/map/drafts',body=json.dumps(payload),**headers)[0]==200
    assert not a.drafts()
    assert request('POST','/api/map/drafts',body=json.dumps({**payload,'operation':'restore'}),**headers)[0]==200
    assert len(a.drafts())==1
