import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from potions import PotionScheduler, PotionDiagnostics, validate_config, run_potions
from potions import rules_from_config


def config(**updates):
    return validate_config({"health_cooldown": 2, "ki_cooldown": 3,
                            "health_jitter": 0, "ki_jitter": 0, **updates}, require_cooldowns=True)


def stats(**updates):
    return SimpleNamespace(**{"hp": 40, "max_hp": 100, "mana": 20, "max_mana": 100,
                              "stats_updated_at": 100, "player_id": 123, **updates})


def rule(rid="hp", item=10161, **updates):
    return {"id": rid, "enabled": True, "resource": "hp", "threshold": 60,
            "jitter": 0, "item_id": item, "cooldown": 2, "interval": .2,
            "mode": "use", **updates}


def test_rules_priority_fallback_reorder_and_strict_threshold():
    cfg = validate_config({"rules": [rule("emergency", 8465, threshold=30), rule()]}, require_cooldowns=True)
    timer = PotionScheduler(); gs = stats(hp=30)
    assert timer.next_rule(cfg, gs, 10, 100)["id"] == "hp"
    gs.hp = 20
    assert timer.next_rule(cfg, gs, 10, 100)["id"] == "emergency"
    timer.record_use("emergency", cfg, 10, 100)
    assert timer.next_rule(cfg, gs, 10.2, 100)["id"] == "hp"
    cfg["rules"].reverse()
    assert timer.next_rule(cfg, gs, 13, 101)["id"] == "hp"


def test_same_item_across_conditions_and_new_ids_cannot_bypass_cooldown():
    cfg = validate_config({"rules": [rule("hp", 8465, cooldown=8), rule("ki", 8465, resource="ki")]})
    timer = PotionScheduler(); gs = stats()
    timer.record_use("hp", cfg, 10, 99)
    assert timer.next_rule(cfg, gs, 13, 100) is None
    cfg["rules"] = [rule("new-id", 8465, cooldown=1)]
    assert timer.next_rule(cfg, gs, 17.9, 100) is None
    assert timer.next_rule(cfg, gs, 18, 100)["id"] == "new-id"


def test_manual_senzu_use_and_lowered_settings_preserve_deadline():
    timer = PotionScheduler(); gs = stats()
    cfg = validate_config({"rules": [rule("hp", 8465, cooldown=10)]})
    timer.record_item_use(8465, cfg, 10, 99)
    cfg["rules"][0]["cooldown"] = 1
    timer.record_item_use(8465, cfg, 12, 99)
    assert timer.next_rule(cfg, gs, 19.9, 100) is None
    assert timer.next_rule(cfg, gs, 20, 100)
    timer.record_item_use(8465, cfg, 20, 100)
    assert timer.next_rule(cfg, gs, 25, 101) is None  # must receive new stats


def test_legacy_settings_convert_without_mutation_and_disabled_senzu_is_safe():
    cfg = config(health_threshold=80, shared_cooldown=.3)
    original = dict(cfg)
    migrated = rules_from_config(cfg)
    assert "rules" not in cfg and cfg == original
    assert [r["item_id"] for r in migrated] == [10161, 10160]
    assert migrated[0]["threshold"] == 80
    migrated.append(rule("senzu", 8465, enabled=False, cooldown=0))
    validate_config({"rules": migrated}, require_cooldowns=True)
    migrated[-1]["enabled"] = True
    with pytest.raises(ValueError, match="cooldown"):
        validate_config({"rules": migrated}, require_cooldowns=True)


@pytest.mark.parametrize("rules", [None, {}, [rule()] * 2, [rule(item=4560)],
    [rule(resource="xp")], [rule(threshold=float("nan"))], [rule(mode="attack")],
    [rule(id="bad id")], [rule(enabled=1)], [rule(extra=1)],
    [rule(str(n)) for n in range(33)]])
def test_invalid_rules_are_rejected(rules):
    with pytest.raises(ValueError): validate_config({"rules": rules})


def test_empty_rules_bonus_ki_and_missing_self_target():
    timer = PotionScheduler()
    assert timer.next_rule(validate_config({"rules": []}), stats(), 10, 100) is None
    cfg = validate_config({"rules": [rule("ki", resource="ki"), rule("self", 8465, mode="self")]})
    assert timer.next_rule(cfg, stats(mana=108, player_id=0), 10, 100) is None


def test_senzu_is_in_passive_diagnostics():
    diag = PotionDiagnostics(); diag.until = 1000
    diag.record_use(8465, "use", stats(), now=10)
    assert diag.snapshot()["rows"][0]["item_id"] == 8465


def test_priority_and_independent_cooldowns():
    timer = PotionScheduler(); cfg = config(); gs = stats()
    assert timer.next_potion(cfg, gs, 10, 100) == "health"
    timer.record_use("health", cfg, 10, 100)
    assert timer.next_potion(cfg, gs, 10, 100) == "ki"
    timer.record_use("ki", cfg, 10, 100)
    gs.stats_updated_at = 101
    assert timer.next_potion(cfg, gs, 11.9, 101) is None
    assert timer.next_potion(cfg, gs, 12, 101) == "health"


def test_interval_and_shared_cooldown_are_both_respected():
    timer = PotionScheduler(); cfg = config(health_interval=5, shared_cooldown=4); gs = stats()
    timer.record_use("health", cfg, 10, 99)
    assert timer.next_potion(cfg, gs, 13.9, 100) is None
    assert timer.next_potion(cfg, gs, 14, 100) == "ki"
    cfg["ki_enabled"] = False
    assert timer.next_potion(cfg, gs, 14, 100) is None
    assert timer.next_potion(cfg, gs, 15, 100) == "health"


def test_lowering_config_does_not_shorten_pending_cooldown():
    timer = PotionScheduler(); gs = stats()
    timer.record_use("health", config(health_cooldown=10), 0, 99)
    assert timer.next_potion(config(ki_enabled=False), gs, 3, 100) is None


def test_threshold_stays_stable_and_is_clamped():
    calls = []
    timer = PotionScheduler(lambda low, high: calls.append((low, high)) or high)
    cfg = config(health_threshold=99, health_jitter=5, ki_enabled=False)
    gs = stats(hp=100)
    for _ in range(4):
        assert timer.next_potion(cfg, gs, 10, 100) is None
    assert calls == [(94, 100)]
    gs.hp = 99
    assert timer.next_potion(cfg, gs, 10, 100) == "health"


@pytest.mark.parametrize("updates,wall", [({"hp": 0}, 100), ({"max_hp": 0}, 100), ({"hp": 101}, 100),
                                            ({"stats_updated_at": 0}, 100), ({}, 106)])
def test_invalid_dead_or_stale_stats_never_use_items(updates, wall):
    assert PotionScheduler().next_potion(config(), stats(**updates), 10, wall) is None


def test_same_stats_cannot_trigger_repeat_attempt():
    timer = PotionScheduler(); cfg = config(ki_enabled=False)
    timer.record_use("health", cfg, 0, 100)
    assert timer.next_potion(cfg, stats(), 10, 101) is None


@pytest.mark.parametrize("updates", [{"health_cooldown": 0}, {"ki_interval": -1}, {"shared_cooldown": float("nan")},
                                      {"health_threshold": True}, {"health_jitter": 31}, {"health_mode": "bad"}])
def test_bad_config_cannot_enable(updates):
    with pytest.raises(ValueError): config(**updates)


@pytest.mark.parametrize("mode,opcode", [("use", 0x82), ("self", 0x84)])
@pytest.mark.parametrize("item", [10161, 10160, 8465])
def test_actual_action_requests_correct_item_only_once(mode, opcode, item):
    async def exercise():
        import time
        gs = stats(stats_updated_at=time.time())
        cfg = config(health_mode=mode, ki_enabled=False) if item == 10161 else validate_config({"rules": [rule(item=item, mode=mode)]})
        st = SimpleNamespace(settings={"actions": {"auto_potions": cfg}}, game_state=gs,
                             potion_scheduler=PotionScheduler(), potion_diagnostics=PotionDiagnostics())
        async def end(_): raise asyncio.CancelledError
        bot = SimpleNamespace(state=st, is_connected=True, inject_to_server=AsyncMock(), sleep=end, log=lambda _: None)
        with pytest.raises(asyncio.CancelledError): await run_potions(bot)
        packet = bot.inject_to_server.call_args.args[0]
        assert packet[0] == opcode
        assert int.from_bytes(packet[6:8], "little") == item
        bot.inject_to_server.assert_awaited_once()
    asyncio.run(exercise())


def test_diagnostics_are_bounded_passive_and_do_not_claim_success():
    diag = PotionDiagnostics(); diag.until = 1000; gs = stats()
    diag.record_use(10161, "self", gs, now=10)
    gs.hp = 65
    diag.observe_stats(gs, now=10.5)
    diag.record_use(10161, "self", gs, now=12)
    rows = diag.snapshot()["rows"]
    assert rows[0]["hp_delta"] == 25
    assert rows[1]["gap_seconds"] == 2
    assert "success" not in rows[0] and "_hp" not in rows[0]
    for n in range(530): diag.record_use(10160, "use", gs, now=20 + n)
    assert len(diag.snapshot()["rows"]) == 500
    diag.set_active(False)
    diag.record_use(10161, "use", gs)
    assert diag.snapshot()["rows"][-1]["item_id"] == 10160


def test_inherited_cooldown_and_optional_longer_override():
    cfg=validate_config({'shared_cooldown':.7,'rules':[rule(cooldown=None)]},require_cooldowns=True)
    timer=PotionScheduler();gs=stats()
    timer.record_use('hp',cfg,10,99)
    assert timer.next_rule(cfg,gs,10.69,100) is None
    assert timer.next_rule(cfg,gs,10.71,100)
    cfg['rules'][0]['cooldown']=2
    timer.record_use('hp',cfg,11,99)
    cfg['rules'][0]['cooldown']=None
    assert timer.next_rule(cfg,gs,12.9,100) is None
    assert timer.next_rule(cfg,gs,13,100)
    with pytest.raises(ValueError):
        validate_config({'shared_cooldown':0,'rules':[rule(cooldown=None)]},require_cooldowns=True)


def test_using_message_is_not_success_and_rejection_wins(monkeypatch):
    monkeypatch.setattr('potions.time.monotonic',lambda:10)
    diag=PotionDiagnostics();diag.until=1000;gs=stats(hp=100,mana=100)
    diag.record_use(8465,'use',gs,now=10)
    diag.observe_message(25,'Using one of 99 Senzu...',batch_id=1,now=10.04)
    assert diag.snapshot()['rows'][0]['status']=='server_reported'
    diag.observe_message(26,'[Senzu] You are exhausted.',batch_id=1,now=10.04)
    assert diag.snapshot()['rows'][0]['status']=='rejected'
    diag.record_use(8465,'use',gs,now=11)
    diag.observe_message(25,'Using one of 99 Senzu...',batch_id=2,now=11.04)
    assert diag.snapshot()['rows'][0]['status']=='rejected'
    diag.record_use(8465,'use',gs,now=12)
    diag.observe_message(25,'Using one of 98 Senzu...',batch_id=3,now=12.04)
    rows=diag.snapshot()['rows']
    assert rows[1]['status']=='consumption_observed'
    assert rows[1]['hp_delta']==rows[1]['ki_delta']==0  # full resources are supported
    assert rows[2]['status']=='server_reported' and rows[2]['stock_delta']==-1


def test_chat_other_batch_and_stock_changes_cannot_falsely_confirm(monkeypatch):
    monkeypatch.setattr('potions.time.monotonic',lambda:10)
    diag=PotionDiagnostics();diag.until=1000;gs=stats()
    diag.record_use(10160,'use',gs,now=10)
    diag.observe_message(1,'Using one of 50 Special Ki Potion...',batch_id=1,now=10.01)
    assert diag.snapshot()['rows'][0]['status']=='pending'
    diag.observe_message(25,'Using one of 50 Special Ki Potion...',batch_id=1,now=10.02)
    diag.observe_message(26,'You are exhausted.',batch_id=2,now=10.03)
    assert diag.snapshot()['rows'][0]['status']=='server_reported'
    diag.invalidate_inventory()
    diag.record_use(10160,'use',gs,now=11)
    diag.observe_message(25,'Using one of 49 Special Ki Potion...',batch_id=3,now=11.01)
    assert diag.snapshot()['rows'][0]['status']=='server_reported'


def test_multiple_pending_attempts_are_ambiguous(monkeypatch):
    monkeypatch.setattr('potions.time.monotonic',lambda:10)
    diag=PotionDiagnostics();diag.until=1000
    for at in (10,10.01):diag.record_use(8465,'use',stats(),now=at)
    diag.observe_message(25,'Using one of 10 Senzu...',batch_id=1,now=10.04)
    assert all(r['status']=='ambiguous' for r in diag.snapshot()['rows'])


def test_active_observation_does_not_pause_automatic_healing():
    async def exercise():
        import time
        gs=stats(stats_updated_at=time.time());diag=PotionDiagnostics();diag.set_active(True)
        st=SimpleNamespace(settings={'actions':{'auto_potions':config(ki_enabled=False)}},game_state=gs,
                           potion_scheduler=PotionScheduler(),potion_diagnostics=diag)
        async def end(seconds):
            assert seconds==.05
            raise asyncio.CancelledError
        bot=SimpleNamespace(state=st,is_connected=True,inject_to_server=AsyncMock(),sleep=end,log=lambda _:None)
        with pytest.raises(asyncio.CancelledError):await run_potions(bot)
        bot.inject_to_server.assert_awaited_once()
        assert diag.snapshot()['rows'][0]['source']=='auto'
    asyncio.run(exercise())
