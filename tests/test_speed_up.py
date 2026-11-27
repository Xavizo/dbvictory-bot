import pytest
from dbv.brain.combat.speed_up import SpeedScheduler,validate_config,fresh


def sample(t,active=False):return {'monotonic':t,'conditions':2 if active else 0,'hp':100}
def config(**kw):return validate_config({'duration':20,'before':10,'after':25,**kw})


def test_no_spam_and_exactly_one_random_draw_per_confirmed_cycle():
    draws=[];s=SpeedScheduler(lambda a,b:draws.append((a,b)) or -7);cfg=config()
    assert s.inspect(sample(10),cfg,now=10);s.sent(10)
    assert not s.inspect(sample(10.2),cfg,now=10.2)
    assert not s.inspect(sample(10.3,True),cfg,ack=10.25,now=10.3)
    assert s.due==pytest.approx(23.25) and draws==[(-10,25)]
    for t in range(11,23):assert not s.inspect(sample(t,True),cfg,ack=10.25,now=t)
    assert len(draws)==1
    assert s.inspect(sample(23.3,True),cfg,ack=10.25,now=23.3)
    s.sent(23.3);assert not s.inspect(sample(23.4,True),cfg,ack=23.35,now=23.4)
    assert len(draws)==2 and s.due==pytest.approx(36.35)


def test_unknown_active_effect_waits_until_expiry_and_then_delays():
    s=SpeedScheduler(lambda a,b:25);cfg=config()
    assert not s.inspect(sample(1,True),cfg,now=1)
    assert not s.inspect(sample(200,True),cfg,now=200)
    assert not s.inspect(sample(201),cfg,now=201)
    assert s.due==226
    assert not s.inspect(sample(225),cfg,now=225)
    assert s.inspect(sample(226),cfg,now=226)


def test_manual_cast_and_observed_early_expiry_reset_without_reroll():
    calls=[];s=SpeedScheduler(lambda a,b:calls.append(1) or 5);cfg=config()
    s.inspect(sample(1),cfg,now=1)
    s.inspect(sample(2,True),cfg,now=2);assert s.due==27
    s.inspect(sample(12),cfg,now=12);assert s.due==17 and len(calls)==1
    # No redraw when editing settings or reading status.
    s.inspect(sample(13),config(after=9),now=13);assert s.due==17 and len(calls)==1


def test_lost_confirmation_is_bounded_and_never_recasts_each_second():
    s=SpeedScheduler();cfg=config()
    for t in (1,14,27):
        assert s.inspect(sample(t),cfg,now=t);s.sent(t)
        for dt in (.2,1,2):assert not s.inspect(sample(t+dt),cfg,now=t+dt)
        assert not s.inspect(sample(t+3),cfg,now=t+3)
    assert not s.inspect(sample(50),cfg,now=50)
    assert '3 próbach' in s.status


def test_unconfirmed_early_refresh_waits_for_actual_effect_end():
    s=SpeedScheduler(lambda a,b:-10);cfg=config()
    s.inspect(sample(1),cfg,now=1);s.sent(1);s.inspect(sample(2,True),cfg,now=2)
    assert s.inspect(sample(12,True),cfg,now=12);s.sent(12)
    assert not s.inspect(sample(15,True),cfg,now=15)
    assert not s.inspect(sample(100,True),cfg,now=100)
    assert s.inspect(sample(101),cfg,now=101)


def test_missing_stale_or_dead_client_sample_never_authorizes_cast():
    s=SpeedScheduler();cfg=config()
    for value in [None,{'monotonic':1,'hp':100},sample(0),{**sample(1),'hp':0}]:
        assert not s.inspect(value,cfg,now=1)
    assert s.inspect(sample(1),cfg,now=1)


@pytest.mark.parametrize('bad',[{'duration':0},{'before':20,'duration':20},{'after':-1},{'after':float('nan')},{'before':True},{'other':2}])
def test_validation_rejects_invalid_windows(bad):
    with pytest.raises(ValueError):validate_config(bad)


def test_cooldown_confirmation_is_only_read_at_known_boundaries(monkeypatch):
    import struct; from dbv.brain.state import game_state
    gs=game_state.GameState();monkeypatch.setattr(game_state.time,'monotonic',lambda:123)
    packet=b'\xa4'+bytes([195])+struct.pack('<I',1000)
    game_state.scan_packet(b'\xff'+packet,gs);assert not hasattr(gs,'haste_cast_at')
    game_state.scan_packet(packet,gs);assert gs.haste_cast_at==123


def test_unknown_delivery_waits_for_new_effect_evidence_instead_of_retrying_on_time():
    from types import SimpleNamespace

    scheduler = SpeedScheduler(lambda a,b: 0)
    cfg = config()
    assert scheduler.inspect(sample(10), cfg, now=10)
    scheduler.sent(10)
    scheduler.receipt = SimpleNamespace(status="unknown")
    for t in (11, 15, 25, 100):
        assert not scheduler.inspect(sample(t), cfg, now=t)
        assert scheduler.attempts == 1
    # A later manual effect establishes a new cycle, without confirming the old request.
    assert not scheduler.inspect(sample(101, True), cfg, now=101)
    assert scheduler.confirmed and scheduler.attempts == 0 and scheduler.due == 121


@pytest.mark.parametrize("ack,expected", [(10.1, "confirmed"), (9.9, "pending"), (14., "pending")])
def test_speed_effect_only_confirms_a_current_attempt(ack, expected):
    import asyncio
    from dbv.client_bridge.command_delivery import CommandReceipt

    async def run():
        scheduler = SpeedScheduler()
        cfg = config()
        scheduler.inspect(sample(10), cfg, now=10)
        scheduler.sent(10.)
        receipt = CommandReceipt(1, 1, "server", 0)
        receipt.finish("unknown")
        scheduler.receipt = receipt
        observed = max(10.2, ack + .1)
        scheduler.inspect(sample(observed, True), cfg, ack=ack, now=observed)
        assert scheduler.confirmed and receipt.effect == expected
    asyncio.run(run())
