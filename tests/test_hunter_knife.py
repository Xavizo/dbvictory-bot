import pytest

from dbv.brain.combat.hunter_knife import FRESH, SPENT, SkinningQueue, body_counts, skinned


def test_manual_unknown_corpse_requires_single_replacement_on_same_tile(caplog):
    import logging
    import time
    from types import SimpleNamespace
    from dbv.brain.navigation.passive_map import PassiveMapMonitor
    from dbv.client_bridge.protocol import ClientOpcode, PacketReader, build_use_item_ex_packet

    from dbv.brain.combat.hunter_knife import CorpseSuggestions
    game = SimpleNamespace(kill_log=[dict(t=time.time() - 5, name="Big Ape", x=22, y=44, z=7)])
    suggestions = CorpseSuggestions()
    monitor = PassiveMapMonitor(SimpleNamespace(connected=True, game_state=game, knife_suggestions=suggestions))
    pos = [22,44,7]
    def frame(*ids):
        cell = dict(x=22,y=44,z=7,kind="clear",items=[
            dict(id=item,stack=i+1,flags=["container"]) for i,item in enumerate(ids)])
        return dict(center=pos, status="ready", captured_at=time.time(), cells=[cell])
    def click(*ids):
        monitor._result = frame(*ids)
        payload = build_use_item_ex_packet(65535,4,0,5908,0,*pos,1,target_item_id=7100)
        monitor.record_client_packet(ClientOpcode.USE_ITEM_EX, PacketReader(payload[1:]))
    with caplog.at_level(logging.INFO):
        click(7100,7101)
        assert monitor._knife_witness is None
        click(7100)
        monitor._check_manual_knife(game, frame(7100))
        assert not caplog.records
        monitor._check_manual_knife(game, {**frame(7101), 'captured_at': time.time() + .01})
        assert "corpse_before=7100 corpse_after=7101" in caplog.text
        assert monitor._knife_witness is None
    assert [(r["id"], r["skinned"], r["name"]) for r in suggestions.pending({})] == [(7100, 7101, "big ape")]

A = (1925, 1288, 4)
B = (1927, 1289, 4)
NAME = "Mutant Fire Elemental"


def tile(point, *bodies):
    base, decoration = (4405 if point == A else 4408), 2889
    return dict(x=point[0], y=point[1], z=point[2], items=[
        dict(id=base, stack=0, flags=["ground", "not_moveable"]),
        dict(id=decoration, stack=1, flags=["not_moveable"]),
        *(dict(id=i, stack=s, flags=["container", "force_use"] if i == FRESH else ["container"])
          for s, i in enumerate(bodies, 2))
    ])


def observations(deaths, credits, cells, name=NAME):
    q = SkinningQueue()
    q.observe([tile(A), tile(B)], 10)
    for i, p in enumerate(deaths):
        q.death(dict(name=name, cid=i+1, x=p[0], y=p[1], z=p[2]), 10.3)
    for _ in range(credits):
        q.personal_loot(f"[{name}]: nothing.", 10.4)
    q.observe(cells, 10.5)
    q.observe(cells, 11.6)
    return q


@pytest.mark.parametrize(("name", "body_id"), [
    ("Gunso", 910), ("Chibi Gunso", 910), ("Fury Gunso", 910), ("Mutant Wolf", 8550),
])
def test_personal_loot_and_death_identify_skinnable_body(name, body_id):
    corpse = tile(A, body_id)
    queue = observations([A], 1, [corpse], name=name)
    assert queue.next_body(A, corpse["items"]) == ("skin", corpse["items"][-1])


def test_distinct_own_deaths_and_identical_layers():
    for n in (1, 2, 4):
        t = tile(A, *[FRESH]*n)
        q = observations([A]*n, n, [t])
        for left in reversed(range(1, n+1)):
            assert q.next_body(A, t["items"])[0] == "skin"
            changed = tile(A, *[SPENT]*(n-left+1), *[FRESH]*(left-1))
            assert skinned(body_counts(t["items"]), body_counts(changed["items"]))
            q.consumed(A)
            q.observe([changed], 11.7)
            t = changed
        assert not q.stacks


def test_spent_above_fresh_is_uncovered_but_not_when_below_it():
    q = observations([A], 1, [tile(A, FRESH, SPENT)])
    assert q.next_body(A, tile(A, FRESH, SPENT)["items"])[0] == "uncover"
    assert q.next_body(A, tile(A, SPENT, FRESH)["items"])[0] == "skin"


def test_ambiguous_ownership_at_same_tile_or_two_places():
    for points, cells in (([A, A], [tile(A,FRESH,FRESH)]), ([A,B], [tile(A,FRESH),tile(B,FRESH)])):
        assert not observations(points, 1, cells).stacks
    assert not observations([A], 1, [tile(A,FRESH,FRESH)]).stacks
    assert not observations([A], 0, [tile(A,FRESH)]).stacks


def test_changing_stacks_and_no_drop_are_distinct_from_confirmation():
    q = observations([A], 1, [tile(A, FRESH)])
    before = body_counts(tile(A, FRESH)["items"])
    assert not skinned(before, before)
    assert not skinned(before, body_counts(tile(A)["items"]))
    assert q.next_body(A, tile(A, FRESH, 7777)["items"]) is None
    q.observe([tile(A)], 12)
    assert not q.stacks


def test_loot_channel_provenance_and_ownership_rejection():
    import struct
    from dbv.brain.state.game_state import GameState, scan_packet
    from dbv.client_bridge.packet_observer import apply_scan, detach_state
    from dbv.client_bridge.protocol import ServerOpcode
    from dbv.brain.combat.hunter_knife_action import KnifeAutomation

    def string(value):
        return struct.pack("<H",len(value))+value

    game = GameState()
    runtime = game.hunter_knife = KnifeAutomation(game)
    def receive(payload):
        batch = detach_state(game,0)
        batch.received_at = runtime.started+1
        scan_packet(payload,batch.state)
        apply_scan(game,batch)

    def message(channel,sender=b""):
        return (bytes([ServerOpcode.TALK])+struct.pack("<I",1)+string(sender)
                +struct.pack("<HBH",0,7,channel)+string(b"13:51 [Fury Mutant Fire Elemental]: nothing."))

    receive(bytes([ServerOpcode.OPEN_CHANNEL])+struct.pack("<H",41)+string(b"Loot"))
    receive(message(41,b"Someone"))
    receive(message(40))
    assert game.knife_loot_count == 0
    receive(message(41))
    assert game.knife_loot_count == 1 and len(runtime.queue.loot) == 1
    receive(bytes([ServerOpcode.CLOSE_CHANNEL])+struct.pack("<H",41))
    receive(message(41))
    receive(bytes([ServerOpcode.TEXT_MESSAGE,25])+string(b"[Mutant Fire Elemental]: nothing."))
    assert game.knife_candidate_count == 1 and game.knife_loot_count == 1
    assert not runtime.queue.stacks


def test_fury_loot_with_display_time_and_channel_state():
    from types import SimpleNamespace
    from dbv.brain.combat.hunter_knife import loot_species
    from dbv.brain.combat.hunter_knife_action import KnifeAutomation, WAITING_FOR_BODY

    line = "[Fury Mutant Fire Elemental]: nothing."
    assert loot_species(line) == loot_species("13:51 " + line) == "fury mutant fire elemental"
    assert loot_species("24:51 " + line) is None
    assert loot_species("Gracz: 13:51 " + line) is None
    assert loot_species("13:51 [Something Else]: nothing.") is None
    game = SimpleNamespace(personal_loot_channel=None)
    runtime = KnifeAutomation(game)
    runtime.status = WAITING_FOR_BODY
    assert runtime.panel_status() == WAITING_FOR_BODY
    game.personal_loot_channel = 0
    assert runtime.panel_status() == WAITING_FOR_BODY
    runtime.status = "Brak noża"
    assert runtime.panel_status() == "Brak noża"


def combat_frames(*, xp=True, foreign=False, other_birth=False, still_alive=False,
                  existing=0, other_death=False, targeted=True):
    from types import SimpleNamespace as NS
    from dbv.brain.combat.hunter_knife_action import KnifeAutomation

    cid = 0x40000001
    game = NS(position=A, attack_target_id=cid if targeted else 0, experience=120000, creatures={})
    runtime = KnifeAutomation(game)
    runtime.tool_ready = True
    def frame(t, bodies, monster=False, elsewhere=False):
        # B has an identical pre-existing foreign corpse throughout.
        cells = [dict(tile(A, *bodies), occupant_types={"monster": int(monster)}),
                 dict(tile(B, FRESH, *([FRESH] if elsewhere else [])), occupant_types={"player": int(foreign)})]
        return runtime.next(dict(captured_at=t, cells=cells), t)
    game.creatures[cid] = dict(x=A[0], y=A[1], z=A[2], name="Fury Mutant Fire Elemental",
                               health=10, t=100)
    before = [FRESH]*existing
    frame(100, before, monster=True)
    if not still_alive:
        game.creatures.clear()
    if other_death:
        runtime.queue.death(dict(cid=cid+1, name="Rat", x=B[0], y=B[1], z=B[2]), 100.2)
    frame(100.25, before + [FRESH], elsewhere=other_birth)
    assert runtime.queue.possible_work(A, 100.4, include_owned=False) == targeted
    if xp:
        game.experience += 12345
    frame(100.7, before + [FRESH], elsewhere=other_birth)
    if still_alive:
        game.creatures[cid].update(x=B[0], t=101.3)
    attempt = frame(101.3, before + [FRESH], elsewhere=other_birth)
    return runtime, attempt


def test_solo_fury_without_loot_uses_knife_only_on_new_target_body():
    runtime, attempt = combat_frames()
    assert runtime.queue.from_combat == 1 and not runtime.queue.loot
    assert attempt.source == A and attempt.item["id"] == FRESH and attempt.item["stack"] == 2
    assert B not in runtime.queue.stacks
    assert runtime.queue.next_body(B, tile(B, FRESH)["items"]) is None


def test_exposed_body_without_reliable_personal_evidence_can_be_tried_once():
    for kwargs in (dict(xp=False), dict(foreign=True), dict(other_birth=True),
                   dict(still_alive=True), dict(existing=1), dict(other_death=True), dict(targeted=False)):
        runtime, attempt = combat_frames(**kwargs)
        assert runtime.queue.from_combat == 0 and not runtime.queue.stacks, kwargs
        assert attempt and attempt.source == A and attempt.item["id"] == FRESH, kwargs
        runtime.pending = attempt
        runtime.rejected(101.31)
        unchanged = [FRESH, FRESH] if kwargs.get("existing") else [FRESH]
        assert runtime.next(dict(captured_at=101.4, cells=[tile(A, *unchanged)]), 101.4) is None, kwargs


def test_two_successive_targets_at_same_tile_without_loot_or_cavebot_target_clicks():
    from types import SimpleNamespace as NS
    from dbv.brain.combat.hunter_knife_action import KnifeAutomation

    game = NS(position=A, attack_target_id=0, experience=20000, creatures={})
    combat = NS(config={"enabled": True}, sent_target=0, target=None)
    worker = KnifeAutomation(game, NS(status="running", combat=combat))
    def frame(now, count, monster=False):
        cell = dict(tile(A, *[FRESH]*count), occupant_types={"monster": int(monster)})
        worker.next(dict(captured_at=now, cells=[cell]), now)
    for i in range(2):
        t = 100 + i*2
        cid = 0x40000001 + i
        combat.sent_target = cid
        game.creatures[cid] = dict(name=NAME, x=A[0], y=A[1], z=A[2], health=10, t=t)
        frame(t, i, monster=True)
        del game.creatures[cid]
        combat.sent_target = 0
        game.experience += 1500
        frame(t+.25, i+1)
        frame(t+.9, i+1)
        frame(t+1.4, i+1)
    assert worker.queue.stacks[A].fresh == worker.queue.from_combat == 2
    assert worker.queue.next_body(A, tile(A, FRESH, FRESH)["items"])[1]["stack"] == 3


def test_running_cavebot_skins_first_exposed_body_with_next_target_active(monkeypatch):
    from types import SimpleNamespace as NS
    from dbv.brain.combat.hunter_knife_action import KnifeAutomation
    clock = [100.]
    monkeypatch.setattr("dbv.brain.combat.hunter_knife_action.time", NS(time=lambda: clock[0], monotonic=lambda: clock[0]))
    cid = 0x40000001
    game = NS(position=A, attack_target_id=0, experience=10000, creatures={})
    combat = NS(config={"enabled": True}, sent_target=cid, target=None)
    worker = KnifeAutomation(game, NS(status="running", combat=combat))
    worker.tool_ready = True
    def frame(t, items, occupants):
        clock[0] = t
        c = dict(tile(A, *items), occupant_types=occupants)
        return worker.next(dict(captured_at=t, cells=[c]), t)
    game.creatures[cid] = dict(name=NAME, x=A[0], y=A[1], z=A[2], health=35, t=100)
    assert frame(100, [], {"monster": 1}) is None
    del game.creatures[cid]
    game.experience += 500
    combat.target = dict(id=cid+1, health=80)
    combat.sent_target = cid+1
    game.creatures[cid+1] = dict(name=NAME, x=B[0], y=B[1], z=B[2], health=80, t=100.2)
    worker.pending = frame(100.2, [FRESH], {})
    assert worker.pending and worker.pending.item["id"] == FRESH and not worker.hold()
    assert frame(100.3, [FRESH], {}) is None
    assert frame(100.4, [FRESH], {}) is None
    assert frame(100.5, [SPENT], {}) is None
    assert worker.done == 1
    assert worker.last_timing_ms == dict(recognition=0, wait_for_use=0, confirmation=300)
    worker.queue.credit(NAME.casefold(), 100.65)
    worker.queue.death(dict(name=NAME, cid=cid, x=A[0], y=A[1], z=A[2]), 100.65)
    assert frame(100.9, [SPENT], {}) is None
    assert frame(101.8, [SPENT], {}) is None
    assert worker.done == 1 and not worker.queue.stacks


def test_fully_matched_personal_loot_can_release_two_bodies_early():
    q = SkinningQueue()
    q.observe([tile(A)], 10)
    for cid in (1, 2):
        q.death(dict(name=NAME, cid=cid, x=A[0], y=A[1], z=A[2]), 10.1)
        q.credit(NAME.casefold(), 10.14)
    q.observe([tile(A,FRESH,FRESH)], 10.15)
    q.observe([tile(A,FRESH,FRESH)], 10.36)
    assert q.stacks[A].fresh == 2


def test_status_line_denial_from_manual_and_automatic_uses():
    from types import SimpleNamespace as NS
    from dbv.brain.state.game_state import GameState
    from dbv.brain.combat.hunter_knife import ownership_denied, publish_feedback
    from dbv.brain.combat.hunter_knife_action import KnifeAutomation

    game = GameState()
    msg = NS(messages=[{"text":"Sorry, you are not owner."}],knife_feedback=[])
    game.knife_manual_use_at = 10
    publish_feedback(game,msg,[],10.7)
    publish_feedback(game,msg,[],13.1)
    assert game.knife_denials == 1
    assert ownership_denied("You are not the owner of this corpse!")
    assert not ownership_denied("Using the last Hunter Knife [+1]...")
    runtime = game.hunter_knife = KnifeAutomation(game)
    runtime.queue = observations([A],1,[tile(A,FRESH)])
    game.position = A
    runtime.tool_ready = True
    stamp = runtime.started + .2
    runtime.pending = runtime.next(dict(cells=[tile(A,FRESH)], captured_at=stamp), stamp)
    assert runtime.pending and runtime.pending.source == A
    publish_feedback(game,msg,[],runtime.started+.5)
    assert game.knife_denials == 2 and not runtime.queue.stacks and not runtime.pending
    assert runtime.done == 0


def test_worker_confirms_two_layers_across_end_of_manual_combat(monkeypatch):
    from types import SimpleNamespace
    from dbv.brain.combat.hunter_knife_action import KnifeAutomation
    clock = [11.6]
    monkeypatch.setattr("dbv.brain.combat.hunter_knife_action.time.monotonic",lambda:clock[0])
    game = SimpleNamespace(position=A,creatures={},attack_target_id=None)
    runtime = KnifeAutomation(game)
    runtime.tool_ready = True
    runtime.queue = observations([A,A],2,[tile(A,FRESH,FRESH)])
    def observe(*items):
        return runtime.next(dict(cells=[tile(A,*items)],captured_at=clock[0]),clock[0])
    import time
    game.creatures[1] = dict(health=20,t=time.time())
    game.attack_target_id = 1
    for before,after in (((FRESH,FRESH),(SPENT,FRESH)),((SPENT,FRESH),(SPENT,SPENT))):
        attempt = observe(*before)
        assert attempt is not None and attempt.item["id"] == FRESH
        assert runtime.hold() is (not runtime.fighting())
        runtime.pending = attempt
        assert observe(*before) is None
        clock[0] += .2
        observe(*after)
        game.creatures[1]["health"] = 0
    assert runtime.done == 2 and not runtime.hold()


def test_worker_changes_top_layer_only_with_usable_destination():
    from types import SimpleNamespace
    from dbv.brain.combat.hunter_knife_action import KnifeAutomation
    game = SimpleNamespace(position=(A[0]-1,A[1],A[2]),creatures={},attack_target_id=0)
    runtime = KnifeAutomation(game)
    runtime.tool_ready = True
    runtime.queue = observations([A],1,[tile(A,FRESH,SPENT)])
    spot = (A[0],A[1]-1,A[2])
    drop = dict(tile(spot),kind="clear",occupants=0)
    def observe(stamp,source,dest):
        return runtime.next(dict(cells=[tile(A,*source),dest],captured_at=stamp),stamp)
    assert observe(11.6,(FRESH,SPENT),dict(drop,occupants=1)) is None
    a = runtime.pending = observe(11.7,(FRESH,SPENT),drop)
    assert a.drop == spot and a.item["id"] == SPENT
    assert observe(11.8,(FRESH,SPENT),drop) is None
    knife = observe(11.9,(FRESH,),dict(tile(spot,SPENT),kind="clear"))
    assert knife.item["id"] == FRESH and knife.drop is None and runtime.done == 0
    runtime.pending = knife
    runtime.rejected(12)
    assert observe(12.1,(FRESH,),drop) is None and not runtime.queue.stacks


def test_standalone_action_uses_equipped_base_knife_and_observed_effect(monkeypatch):
    import asyncio
    import time
    from types import SimpleNamespace as NS
    from unittest.mock import Mock
    from actions.auto_hunter_knife import run
    from dbv.client_bridge.protocol import build_use_item_ex_packet

    clock = [100.0]
    gs = NS(hp=90,position=A,attack_target_id=None,creatures={})
    cfg = {"enabled":True}
    monitor = NS(_result={})
    state = NS(game_state=gs,game_proxy=object(),passive_map_monitor=monitor,
               automation_paused=False,settings={"actions":{"auto_hunter_knife":cfg}})
    bot = NS(state=state,player_id=100,is_connected=True,position=A)
    reader = Mock()
    reader.snapshot.return_value = {"slots":{6:5908},"sources":[]}
    source = Mock()
    monkeypatch.setattr("dbv.client_bridge.passive_map_win32.ReadOnlyClient",lambda:source)
    monkeypatch.setattr("dbv.client_bridge.client_player_state.ClientPlayerReader",lambda *args:object())
    monkeypatch.setattr("dbv.client_bridge.client_equipment.EquipmentReader",lambda *args:reader)
    wall = time.time()
    def wall_time():
        return wall+clock[0]-100
    monkeypatch.setattr("dbv.brain.combat.hunter_knife_action.time",NS(time=wall_time,monotonic=lambda:clock[0]))
    sent = []
    def sample(*items):
        monitor._result = dict(status="ready",cells=[dict(tile(A,*items),kind="clear")],center=A,captured_at=wall_time())
    async def send(packet,guard):
        assert guard()
        sent.append(packet)
        bot.last_receipt=NS(attempted=True)
        sample(SPENT)
    async def tick(_):
        clock[0] += .25
        assert clock[0] < 102
        if clock[0] == 100.25:
            gs.hunter_knife.queue.death(dict(name=NAME,x=A[0],y=A[1],z=A[2]),clock[0])
            gs.hunter_knife.queue.personal_loot("[Mutant Fire Elemental]: nothing.",clock[0])
        if gs.hunter_knife.done:
            cfg["enabled"]=False
        else:
            sample(SPENT if sent else FRESH)
    bot.sleep, bot.inject_to_server = tick,send
    sample()
    asyncio.run(run(bot))
    assert sent == [build_use_item_ex_packet(65535,6,0,5908,0,*A,2,target_item_id=FRESH)]
    assert source.close.call_count == 1 and gs.hunter_knife is None
