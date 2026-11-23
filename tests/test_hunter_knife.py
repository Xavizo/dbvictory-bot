from hunter_knife import FRESH, SPENT, SkinningQueue, body_counts, skinned

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


def observations(deaths, credits, cells):
    q = SkinningQueue()
    q.observe([tile(A), tile(B)], 10)
    for i, p in enumerate(deaths):
        q.death(dict(name=NAME, cid=i+1, x=p[0], y=p[1], z=p[2]), 10.3)
    for _ in range(credits):
        q.personal_loot("[Mutant Fire Elemental]: nothing.", 10.4)
    q.observe(cells, 10.5)
    q.observe(cells, 11.6)
    return q


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
    from game_state import GameState, scan_packet
    from packet_observer import apply_scan, detach_state
    from protocol import ServerOpcode
    from hunter_knife_action import KnifeAutomation

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
    from hunter_knife import loot_species
    from hunter_knife_action import KnifeAutomation, WAITING_FOR_BODY

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
    from hunter_knife_action import KnifeAutomation

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
    assert runtime.queue.possible_work(A, 100.4) == targeted
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


def test_without_loot_ambiguous_or_unrelated_corpse_is_left_alone():
    for kwargs in (dict(xp=False), dict(foreign=True), dict(other_birth=True),
                   dict(still_alive=True), dict(existing=1), dict(other_death=True), dict(targeted=False)):
        runtime, attempt = combat_frames(**kwargs)
        assert attempt is None and not runtime.queue.stacks, kwargs


def test_two_successive_targets_at_same_tile_without_loot_or_cavebot_target_clicks():
    from types import SimpleNamespace as NS
    from hunter_knife_action import KnifeAutomation

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


def test_status_line_denial_from_manual_and_automatic_uses():
    from types import SimpleNamespace as NS
    from game_state import GameState
    from hunter_knife import ownership_denied, publish_feedback
    from hunter_knife_action import KnifeAutomation

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
    runtime.pending = NS(source=A,drop=None,sent=runtime.started+.2)
    publish_feedback(game,msg,[],runtime.started+.5)
    assert game.knife_denials == 2 and not runtime.queue.stacks and not runtime.pending
    assert runtime.done == 0


def test_worker_waits_after_combat_confirms_two_layers_and_resumes_route(monkeypatch):
    from types import SimpleNamespace
    from hunter_knife_action import KnifeAutomation
    clock = [11.6]
    monkeypatch.setattr("hunter_knife_action.time.monotonic",lambda:clock[0])
    game = SimpleNamespace(position=A,creatures={},attack_target_id=None)
    runtime = KnifeAutomation(game)
    runtime.tool_ready = True
    runtime.queue = observations([A,A],2,[tile(A,FRESH,FRESH)])
    def observe(*items):
        return runtime.next(dict(cells=[tile(A,*items)],captured_at=clock[0]),clock[0])
    import time
    game.creatures[1] = dict(health=20,t=time.time())
    game.attack_target_id = 1
    assert observe(FRESH,FRESH) is None and not runtime.hold()
    game.creatures[1]["health"] = 0
    for before,after in (((FRESH,FRESH),(SPENT,FRESH)),((SPENT,FRESH),(SPENT,SPENT))):
        attempt = observe(*before)
        assert runtime.hold() and attempt.item["id"] == FRESH
        runtime.pending = attempt
        assert observe(*before) is None
        clock[0] += .2
        observe(*after)
    assert runtime.done == 2 and not runtime.hold()


def test_worker_changes_top_layer_only_with_usable_destination():
    from types import SimpleNamespace
    from hunter_knife_action import KnifeAutomation
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
    from protocol import build_use_item_ex_packet

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
    monkeypatch.setattr("passive_map_win32.ReadOnlyClient",lambda:source)
    monkeypatch.setattr("client_player_state.ClientPlayerReader",lambda *args:object())
    monkeypatch.setattr("client_equipment.EquipmentReader",lambda *args:reader)
    wall = time.time()
    def wall_time():
        return wall+clock[0]-100
    monkeypatch.setattr("hunter_knife_action.time",NS(time=wall_time,monotonic=lambda:clock[0]))
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
