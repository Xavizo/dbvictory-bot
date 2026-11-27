"""Concurrent workers exercised against synthetic terrain and packet transport."""
import asyncio
import time
from types import SimpleNamespace

import pytest
from dbv.mcp_server import server
from dbv.client_bridge.command_delivery import CommandReceipt
from dbv.brain.combat.potions import run_potions
from engine_timing_probe import measure_blockage
from test_adaptive_cavebot import setup,connect_fake_walk
from test_automation_safety import state
from test_v2_combat import configure_rules,monster
from dbv.client_bridge.protocol import ClientOpcode


async def until(predicate,timeout=1.5):
    async with asyncio.timeout(timeout):
        while not predicate():await asyncio.sleep(.01)


def configure(state,c,cells,mode='lure'):
    configure_rules(c,[dict(pattern='*')],enabled=True,mode=mode,lure_count=4,
                    spells=[dict(words='Test spell',cooldown=.2)])
    state.game_state.hp=40;state.game_state.mana=state.game_state.max_mana=100
    _,row,_=monster(state,cells,x=11,y=11)
    state.settings['actions']['auto_potions']=dict(enabled=True,shared_cooldown=.1,rules=[dict(
        id='heal',enabled=True,resource='hp',item_id=10161,threshold=60,jitter=0,
        interval=.1,cooldown=.1,mode='use')])
    return row


async def freshen(state,row):
    while True:
        row['t']=time.time();state.game_state.stats_updated_at=time.time()
        await asyncio.sleep(.03)


def test_blocked_terrain_keeps_combat_and_healing_active_and_route_resumes(state, tmp_path):
    stats = asyncio.run(measure_blockage(state, tmp_path, pulses=6))
    assert stats["heals_while_blocked"] == 6 and stats["casts_while_blocked"] >= 2


def test_healing_and_casting_continue_during_unacknowledged_walk(state,tmp_path):
    c,cells,_=setup(state,tmp_path);row=configure(state,c,cells);sent=[]
    async def inject(payload,guard=None):
        assert guard and guard()
        sent.append(payload[0])
        r=CommandReceipt(len(sent),0,'server',payload[0]);r.finish('dispatched');return r
    state.game_proxy.inject_to_server=inject
    async def exercise():
        workers=[asyncio.create_task(freshen(state,row)),asyncio.create_task(run_potions(server.BotContext('auto_potions')))]
        run=asyncio.create_task(c.run())
        try:
            await until(lambda:0x66 in sent or 0x6b in sent)
            await until(lambda:sent.count(ClientOpcode.SAY)>=3)
            assert sent.count(0x82)>=2 and state.game_state.position==(10,10,7)
            assert c.status=='running' and c.combat.sent_target
            # No second movement may overtake the unacknowledged first one.
            assert sum(op in {0x65,0x66,0x67,0x68,0x6a,0x6b,0x6c,0x6d} for op in sent)==1
        finally:
            c.pause('test');run.cancel()
            for task in workers:task.cancel()
            await asyncio.gather(run,*workers,return_exceptions=True)
        assert all(t.done() for t in c.domains.tasks)
        assert not state.game_proxy._client_packet_callbacks
    asyncio.run(exercise())


@pytest.mark.parametrize('error',[ValueError('Niepotwierdzone wyjście'),RuntimeError('synthetic navigation fault')])
def test_navigation_fault_does_not_cancel_combat_or_healing(state,tmp_path,error):
    c,cells,_=setup(state,tmp_path);row=configure(state,c,cells,mode='stop');sent=[]
    async def inject(payload,guard=None):
        if guard and guard():sent.append(payload[0])
    state.game_proxy.inject_to_server=inject
    async def fail():raise error
    c._navigation_loop=fail
    async def exercise():
        refresh=asyncio.create_task(freshen(state,row))
        heal=asyncio.create_task(run_potions(server.BotContext('auto_potions')))
        run=asyncio.create_task(c.run())
        try:
            await until(lambda:sent.count(ClientOpcode.SAY)>=2 and sent.count(0x82)>=2)
            assert c.status=='running' and c.navigation_hold
            assert c.domains.combat_state=='running' and not heal.done()
        finally:
            c.pause('test');run.cancel();refresh.cancel();heal.cancel()
            await asyncio.gather(run,refresh,heal,return_exceptions=True)
    asyncio.run(exercise())


def test_critical_session_change_cancels_both_workers(state,tmp_path):
    c,cells,_=setup(state,tmp_path);configure(state,c,cells,mode='stop')
    async def inject(payload,guard=None):return None
    state.game_proxy.inject_to_server=inject
    state.game_proxy.session_generation=1
    async def exercise():
        run=asyncio.create_task(c.run())
        await until(lambda:c.domains is not None and c.domains.combat_state=='running')
        state.game_proxy.session_generation+=1
        await asyncio.wait_for(run,1)
        assert c.status=='paused' and all(t.done() for t in c.domains.tasks)
    asyncio.run(exercise())


def test_combat_fault_holds_route_without_stopping_healing(state,tmp_path):
    c,cells,_=setup(state,tmp_path);row=configure(state,c,cells);sent=[]
    async def fail(*args,**kwargs):raise RuntimeError('synthetic combat fault')
    c.combat.tick=fail
    async def inject(payload,guard=None):
        assert guard();sent.append(payload[0])
    state.game_proxy.inject_to_server=inject
    async def exercise():
        run=asyncio.create_task(c.run());heal=asyncio.create_task(run_potions(server.BotContext('auto_potions')))
        refresh=asyncio.create_task(freshen(state,row))
        try:
            await until(lambda:sent.count(0x82)>=2)
            assert c.status=='running' and c.domains.combat_state=='fault'
            assert state.game_state.position==(10,10,7) and all(op==0x82 for op in sent)
        finally:
            c.pause('test');run.cancel();heal.cancel();refresh.cancel()
            await asyncio.gather(run,heal,refresh,return_exceptions=True)
    asyncio.run(exercise())


def test_route_completion_keeps_enabled_combat_worker_alive(state,tmp_path):
    c,_,_=setup(state,tmp_path)
    configure_rules(c,[dict(pattern='*')],enabled=True)
    sent=connect_fake_walk(state,c)
    async def exercise():
        run=asyncio.create_task(c.run())
        try:
            await until(lambda:c.domains is not None and c.domains.movement_state=='complete')
            assert c.status=='running' and len(sent)==3 and c.laps==1
            await asyncio.sleep(.1)
            assert c.laps==1 and not c.domains.tasks[0].done()
        finally:
            c.pause('test');await asyncio.wait_for(run,1)
    asyncio.run(exercise())


def test_route_and_combat_command_scopes_do_not_leak_across_tasks(state,tmp_path):
    c,_,_=setup(state,tmp_path)
    async def exercise():
        marked=asyncio.Event();release=asyncio.Event()
        async def movement():
            c._route_command=True;c._route_boundary=True
            c._route_attempt={'sent':1};marked.set();await release.wait()
            assert c._route_command and c._route_attempt=={'sent':1}
        task=asyncio.create_task(movement());await marked.wait()
        assert not c._route_command and not c._route_boundary and c._route_attempt is None
        release.set();await task
    asyncio.run(exercise())


def test_independent_combat_stops_movement_at_three_adjacent_monsters(state,tmp_path):
    c,cells,_=setup(state,tmp_path);row=configure(state,c,cells)
    c.combat.config['lure_count']=3
    monster(state,cells,x=11,y=10,cid=0x40000002)
    monster(state,cells,x=10,y=11,cid=0x40000003)
    sent=[]
    async def inject(payload,guard=None):
        assert guard();sent.append(payload[0])
    state.game_proxy.inject_to_server=inject
    async def exercise():
        run=asyncio.create_task(c.run())
        try:
            await until(lambda:ClientOpcode.SAY in sent)
            assert c.combat.engaged_count==3 and c.combat.lure_stop_reason=='group'
            assert state.game_state.position==(10,10,7)
            assert set(sent)<={ClientOpcode.ATTACK,ClientOpcode.SAY}
            assert c.status=='running'
        finally:c.pause('test');await asyncio.wait_for(run,1)
    asyncio.run(exercise())


def test_chase_and_return_share_one_movement_executor(state,tmp_path):
    c,cells,_=setup(state,tmp_path)
    configure_rules(c,[dict(pattern='*')],enabled=True,mode='chase')
    cid,row,tile=monster(state,cells,x=13,y=11)
    walk=connect_fake_walk(state,c);original=state.game_proxy.inject_to_server;walkers=0;peak=0
    async def inject(payload,guard=None):
        nonlocal walkers,peak
        if payload[0] in {ClientOpcode.ATTACK,ClientOpcode.SAY}:return None
        walkers+=1;peak=max(peak,walkers)
        try:
            await asyncio.sleep(.02)
            await original(payload,guard)
        finally:walkers-=1
    state.game_proxy.inject_to_server=inject
    async def exercise():
        run=asyncio.create_task(c.run());refresh=asyncio.create_task(freshen(state,row))
        try:
            await until(lambda:max(abs(state.game_state.position[0]-13),abs(state.game_state.position[1]-11))<=1)
            row['health']=0;tile.update(occupants=0,occupant_types={})
            await until(lambda:c.domains.movement_state=='complete')
            assert peak==1 and walk and c.combat.anchor is None and c.status=='running'
        finally:
            c.pause('test');refresh.cancel();run.cancel()
            await asyncio.gather(run,refresh,return_exceptions=True)
    asyncio.run(exercise())


@pytest.mark.parametrize('occupied',[False,True])
def test_ready_map_wakes_combat_review_before_its_periodic_tick(state,tmp_path,monkeypatch,occupied):
    from dbv.brain.state import automation_domains as domains

    c,cells,_=setup(state,tmp_path)
    configure_rules(c,[dict(pattern='*')],enabled=True,mode='stop')
    c.status='running'
    workers=domains.CavebotDomains(c)
    monkeypatch.setattr(domains,'COMBAT_INTERVAL',4.0)
    visited=[]
    async def evaluate(sample,boundary,allow_movement):
        visited.append(tuple(sample['center']))
        return bool(c.combat.candidates(sample))
    c.combat.tick=evaluate

    async def scenario():
        task=asyncio.create_task(workers.combat_loop())
        try:
            await until(lambda: workers.decision is not None)
            state.game_state.position=tuple(c.path[1])
            c.index=1
            if occupied:
                monster(state,cells,x=12,y=10)
            sample=c.map_now()
            # First pass discards permission for the old square. The signal
            # wakes the sleeping reviewer even with a four-second timer.
            assert await asyncio.wait_for(workers.consume_movement(sample,False),.8)
            assert workers.decision['signature']==workers.signature()
            assert await workers.consume_movement(sample,False) is occupied
            assert visited==[tuple(c.path[0]),tuple(c.path[1])]
        finally:
            task.cancel()
            await asyncio.gather(task,return_exceptions=True)
    asyncio.run(scenario())


def test_request_during_same_review_uses_that_result_once(state,tmp_path,monkeypatch):
    from dbv.brain.state import automation_domains as domains

    c,cells,_=setup(state,tmp_path)
    configure_rules(c,[dict(pattern='*')],enabled=True,mode='stop')
    c.status='running'
    workers=domains.CavebotDomains(c)
    monkeypatch.setattr(domains,'COMBAT_INTERVAL',4.0)

    async def scenario():
        entered,finish=asyncio.Event(),asyncio.Event()
        visits=[]
        async def evaluate(sample,boundary,allow_movement):
            visits.append(tuple(sample['center']))
            entered.set()
            await finish.wait()
            return False
        c.combat.tick=evaluate
        reviewer=asyncio.create_task(workers.combat_loop())
        try:
            await asyncio.wait_for(entered.wait(),.8)
            walking=asyncio.create_task(workers.consume_movement(c.map_now(),False))
            await until(workers._review_requested.is_set)
            finish.set()
            assert await asyncio.wait_for(walking,.8)
            assert not await workers.consume_movement(c.map_now(),False)
            assert not workers._review_requested.is_set() and len(visits)==1
        finally:
            reviewer.cancel()
            await asyncio.gather(reviewer,return_exceptions=True)
    asyncio.run(scenario())
