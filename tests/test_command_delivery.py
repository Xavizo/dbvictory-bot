"""Actual receipt-aware dispatcher; no sockets, client process or game commands."""
import asyncio
import time
from collections import deque
from unittest.mock import AsyncMock,Mock

import pytest
from command_delivery import CommandReceipt,delivery
from session_lifetime import SessionLease
from test_automation_safety import proxy,state


def transport():
    p=proxy();p.command_delivery=True;p.session_generation=1;p.command_sequence=0
    p.command_history=deque(maxlen=100);p.client_writer=None
    p._inject_queue=asyncio.Queue(maxsize=3)
    return p


async def dispatched(p,receipt):
    task=asyncio.create_task(p._process_inject_queue())
    try:return await asyncio.wait_for(delivery(receipt),2)
    finally:
        task.cancel();await asyncio.gather(task,return_exceptions=True)


def test_written_is_not_executed_and_history_has_no_payload():
    async def exercise():
        p=transport();r=await p.inject_to_server(b'\x96private spell text')
        assert r.status=='queued'
        await dispatched(p,r)
        assert r.status=='dispatched' and r.effect=='pending'
        assert p.server_writer.write.call_count==1
        r.confirm('effect_observed')
        assert r.snapshot()['effect']=='confirmed'
        assert 'private' not in str(p.command_snapshot())
    asyncio.run(exercise())


@pytest.mark.parametrize('change,expected',[
    ('guard',('rejected','conditions_changed')),('session',('cancelled','session_changed')),
    ('expiry',('expired','deadline')),('pause',('cancelled','automation_paused')),
    ('missing_writer',('rejected','writer_unavailable'))])
def test_conditions_rechecked_at_dispatch(change,expected):
    async def exercise():
        p=transport();valid=True;r=await p.inject_to_server(b'\x66',guard=lambda:valid)
        writer=p.server_writer
        if change=='guard':valid=False
        elif change=='session':p.session_generation+=1
        elif change=='expiry':r.expires=time.monotonic()-1
        elif change=='pause':p.pause_injections()
        elif change=='missing_writer':p.server_writer=None
        await dispatched(p,r)
        assert (r.status,r.reason)==expected and not r.attempted
        assert not writer.write.called and r.effect=='not_sent'
    asyncio.run(exercise())


def test_full_queue_is_bounded_and_reports_rejection():
    async def exercise():
        p=transport()
        for _ in range(3):await p.inject_to_server(b'\x66')
        r=await p.inject_to_server(b'\x66')
        assert r.status=='rejected' and r.reason=='queue_full' and p._inject_queue.qsize()==3
        p.discard_commands('disconnected')
        assert all(r.status!='queued' for r in p.command_history)
    asyncio.run(exercise())


@pytest.mark.parametrize('phase',['encrypt','write','drain'])
def test_failure_reports_whether_delivery_is_uncertain(phase):
    async def exercise():
        p=transport()
        if phase=='encrypt':p._encrypt_game_packet=Mock(side_effect=ValueError('secret details'))
        elif phase=='write':p.server_writer.write.side_effect=OSError('secret details')
        else:p.server_writer.drain.side_effect=OSError('secret details')
        r=await dispatched(p,await p.inject_to_server(b'\x66'))
        assert r.status==('failed' if phase=='encrypt' else 'unknown')
        assert 'secret' not in str(r.snapshot())
    asyncio.run(exercise())


def test_cancelling_owner_before_dispatch_invalidates_queued_work():
    async def exercise():
        p=transport();r=await p.inject_to_server(b'\x66')
        owner=asyncio.create_task(r.wait());await asyncio.sleep(0)
        owner.cancel();await asyncio.gather(owner,return_exceptions=True)
        await dispatched(p,r)
        assert r.status=='cancelled' and not p.server_writer.write.called
    asyncio.run(exercise())


def test_cancelling_after_write_is_uncertain_never_retried_by_dispatcher():
    async def exercise():
        p=transport();started=asyncio.Event()
        async def drain():started.set();await asyncio.sleep(100)
        p.server_writer.drain=drain
        r=await p.inject_to_server(b'\x66');worker=asyncio.create_task(p._process_inject_queue())
        await started.wait();worker.cancel();await asyncio.gather(worker,return_exceptions=True)
        assert r.status=='unknown' and r.effect=='pending' and p.server_writer.write.call_count==1
    asyncio.run(exercise())


@pytest.mark.parametrize('change',['game','proxy','generation','player'])
def test_session_lease_expires_without_relying_on_proxy_object_replacement(state,change):
    state.game_proxy=transport();state.ready=True;state.game_state.player_id=123
    lease=SessionLease(state);assert lease.valid()
    if change=='game':state.game_state=type(state.game_state)()
    elif change=='proxy':state.game_proxy=transport()
    elif change=='generation':state.game_proxy.session_generation+=1
    else:state.game_state.player_id=124
    assert not lease.valid()


def test_context_old_action_cannot_send_in_new_session(state):
    import mcp_server as server
    async def exercise():
        state.game_proxy=transport();state.ready=True
        ctx=server.BotContext('auto_potions')
        state.game_proxy.session_generation+=1
        with pytest.raises(asyncio.CancelledError):await ctx.inject_to_server(b'\x82')
        assert state.game_proxy._inject_queue.empty()
    asyncio.run(exercise())


def test_overlapping_connections_retire_old_owners_before_new_login(monkeypatch):
    async def exercise():
        p=transport();p._connection_task=None;p.server_host='synthetic';p.server_port=1
        p.is_login_proxy=False;p.on_game_disconnected=Mock()
        peers=[];entered=asyncio.Queue()
        async def connect(*args):
            writer=Mock(wait_closed=AsyncMock());peers.append(writer)
            return Mock(),writer
        monkeypatch.setattr(asyncio,'open_connection',connect)
        async def relay():
            await entered.put(p.session_generation)
            await asyncio.sleep(100)
        p._run_relay_loop=relay
        clients=[Mock(wait_closed=AsyncMock()) for _ in range(3)]
        tasks=[]
        for client in clients:
            tasks.append(asyncio.create_task(p._handle_client_connection(Mock(),client)))
            await asyncio.wait_for(entered.get(),1)
        assert all(t.done() for t in tasks[:-1]) and p.client_writer is clients[-1]
        assert all(w.close.called for w in peers[:-1]) and not peers[-1].close.called
        assert p.on_game_disconnected.call_count==2
        tasks[-1].cancel();await asyncio.gather(*tasks,return_exceptions=True)
        assert p.on_game_disconnected.call_count==3 and p.client_writer is None
    asyncio.run(exercise())


def test_failed_connection_retires_state_without_leaving_a_writer(monkeypatch):
    async def exercise():
        p=transport();p._connection_task=None;p.server_host='synthetic';p.server_port=1
        p.is_login_proxy=False;p.on_game_disconnected=Mock()
        monkeypatch.setattr(asyncio,'open_connection',AsyncMock(side_effect=OSError('synthetic')))
        writer=Mock(wait_closed=AsyncMock())
        await p._handle_client_connection(Mock(),writer)
        assert writer.close.called and p.client_writer is None and p.server_writer is None
        assert not p.logged_in and p.xtea_keys is None and p.on_game_disconnected.call_count==1
    asyncio.run(exercise())


@pytest.mark.parametrize('outcome',['rejected','unknown'])
def test_uncertain_target_selection_never_casts_at_the_previous_target(state,tmp_path,outcome):
    from test_v2_combat import armed,monster
    c,cells=armed(state,tmp_path,spells=[dict(words='Test spell',cooldown=1)])
    monster(state,cells);c.status='running';state.game_state.mana=state.game_state.max_mana=100
    sent=[]
    async def inject(payload,guard=None):
        sent.append(payload[0]);r=CommandReceipt(1,0,'server',payload[0]);r.finish(outcome);return r
    state.game_proxy.inject_to_server=inject
    asyncio.run(c.combat.tick(c.map_now()))
    assert sent==[0xa1] and c.combat.sent_target==0 and not c.combat.deadlines


def test_rejected_spell_does_not_reserve_cooldown(state,tmp_path):
    from test_v2_combat import armed,monster
    c,cells=armed(state,tmp_path,spells=[dict(words='Test spell',cooldown=1)])
    monster(state,cells);c.status='running';state.game_state.mana=state.game_state.max_mana=100
    sent=[]
    async def inject(payload,guard=None):
        sent.append(payload[0]);r=CommandReceipt(len(sent),0,'server',payload[0])
        r.finish('dispatched' if payload[0]==0xa1 else 'rejected');return r
    state.game_proxy.inject_to_server=inject
    asyncio.run(c.combat.tick(c.map_now()))
    assert sent==[0xa1,0x96] and c.combat.sent_target and not c.combat.deadlines


@pytest.mark.parametrize('status,consumed',[('rejected',False),('expired',False),('unknown',True),('dispatched',True)])
def test_healing_reserves_cooldown_only_for_possible_delivery(state,status,consumed):
    import mcp_server as server
    from potions import run_potions
    state.ready=True;state.game_proxy=transport()
    gs=state.game_state;gs.hp=30;gs.max_hp=100;gs.stats_updated_at=time.time()
    state.settings['actions']['auto_potions']=dict(enabled=True,health_cooldown=2,ki_cooldown=2)
    async def inject(payload,guard=None):
        assert guard();r=CommandReceipt(1,0,'server',payload[0]);r.finish(status,'test');return r
    state.game_proxy.inject_to_server=inject
    async def done(seconds):raise asyncio.CancelledError()
    async def exercise():
        bot=server.BotContext('auto_potions');bot.sleep=done
        with pytest.raises(asyncio.CancelledError):await run_potions(bot)
        assert bool(state.potion_scheduler.last_use)==consumed
    asyncio.run(exercise())


def test_late_hp_recovery_invalidates_queued_healing(state):
    import mcp_server as server
    from potions import run_potions
    state.ready=True;state.game_proxy=transport()
    gs=state.game_state;gs.hp=30;gs.max_hp=100;gs.stats_updated_at=time.time()
    state.settings['actions']['auto_potions']=dict(enabled=True,health_cooldown=2,ki_cooldown=2)
    original=state.game_proxy.inject_to_server
    async def inject(payload,guard=None):
        receipt=await original(payload,guard=guard)
        gs.hp=100;gs.stats_updated_at=time.time()
        return await dispatched(state.game_proxy,receipt)
    state.game_proxy.inject_to_server=inject
    async def done(seconds):raise asyncio.CancelledError()
    async def exercise():
        bot=server.BotContext('auto_potions');bot.sleep=done
        with pytest.raises(asyncio.CancelledError):await run_potions(bot)
        assert not state.game_proxy.server_writer.write.called and not state.potion_scheduler.last_use
    asyncio.run(exercise())


@pytest.mark.parametrize('written',[False,True])
def test_cancelled_healing_keeps_cooldown_only_if_write_started(state,written):
    import mcp_server as server
    from potions import run_potions
    state.ready=True;state.game_proxy=transport()
    gs=state.game_state;gs.hp=30;gs.max_hp=100;gs.stats_updated_at=time.time()
    state.settings['actions']['auto_potions']=dict(enabled=True,health_cooldown=2,ki_cooldown=2)
    async def exercise():
        queued=asyncio.Event()
        async def inject(payload,guard=None):
            r=CommandReceipt(1,0,'server',payload[0]);r.write_started=written
            queued.set();return r
        state.game_proxy.inject_to_server=inject
        task=asyncio.create_task(run_potions(server.BotContext('auto_potions')))
        await queued.wait();await asyncio.sleep(0)
        task.cancel();await asyncio.gather(task,return_exceptions=True)
        assert bool(state.potion_scheduler.last_use)==written
    asyncio.run(exercise())


@pytest.mark.parametrize('written',[False,True])
def test_cancelled_spell_does_not_forget_possible_cast(state,tmp_path,written):
    from test_v2_combat import armed,monster
    c,cells=armed(state,tmp_path,spells=[dict(words='Test spell',cooldown=1)])
    monster(state,cells);c.status='running';state.game_state.mana=state.game_state.max_mana=100
    async def exercise():
        queued=asyncio.Event()
        async def inject(payload,guard=None):
            r=CommandReceipt(1,0,'server',payload[0])
            if payload[0]==0xa1:r.finish('dispatched')
            else:r.write_started=written;queued.set()
            return r
        state.game_proxy.inject_to_server=inject
        task=asyncio.create_task(c.combat.tick(c.map_now()))
        await asyncio.wait_for(queued.wait(),1);await asyncio.sleep(0)
        task.cancel();await asyncio.gather(task,return_exceptions=True)
        assert bool(c.combat.deadlines)==written
    asyncio.run(exercise())
