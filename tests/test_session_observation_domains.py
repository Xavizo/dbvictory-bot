"""Offline replays across the session journal, creature identities and Atlas."""
import asyncio
import time
from types import SimpleNamespace as NS

from game_state import GameState
from hunt_areas import save_area
from hunt_encounters import HuntEncounters
from hunt_sightings import FirstSightings
from hunt_world import world_zone_update
from map_atlas import MapAtlas
from session_observations import (
    note_observation_gap, observation_family_status, publish_semantic_result, session_observations,
)


def test_session_gap_relog_and_partial_player_samples():
    gs = GameState()
    gs.session_id = 4
    journal = session_observations(gs)
    start = time.monotonic()
    gs.client_snapshot = dict(monotonic=start, hp=42, max_hp=100, mana=80, max_mana=100, position=[12,18,7])
    note_observation_gap(gs, "overload")
    view = journal.snapshot(4, now=start+.1)["families"]
    assert view["health"]["state"] == view["position"]["state"] == "fresh"
    assert view["walk_cancel"]["state"] == "gap"
    health_revision = view["health"]["revision"]
    gs.client_snapshot = dict(monotonic=start+.2, position=[13,18,7])
    view = journal.snapshot(4, now=start+.3)["families"]
    assert view["position"]["state"] == "fresh"
    assert view["health"]["state"] == "gap" and view["health"]["reason"] == "incomplete"
    journal.publish("server", ("health",), start-.1)
    assert journal.revision_for(("health",)) == view["health"]["revision"] > health_revision
    assert journal.snapshot(4, now=start+1)["families"]["position"]["state"] == "stale"
    new = GameState()
    new.session_id = 5
    st = NS(game_state=new, connected=True)
    session_observations(new)
    journal.publish("server", ("death",))
    current = observation_family_status(st)
    assert current["session_id"] == 5 and current["revision"] == 0
    assert all(row["state"] == "waiting" for row in current["families"].values())


def test_wait_is_race_free_scoped_and_released_after_timeout_or_cancellation():
    journal = session_observations(GameState())

    async def replay():
        rev = journal.revision_for(("map",))
        ready = asyncio.create_task(journal.wait(("map",), rev, 1))
        await asyncio.sleep(0)
        journal.publish("server", ("messages",))
        await asyncio.sleep(0)
        assert not ready.done()
        await asyncio.to_thread(journal.publish, "map_snapshot", ("map",))
        assert await ready == journal.revision_for(("map",))
        assert await journal.wait(("map",), rev, 0) > rev
        assert await journal.wait(("death",), 0, .001) == 0
        cancelled = asyncio.create_task(journal.wait(("loot",), 0, 5))
        await asyncio.sleep(0)
        cancelled.cancel()
        await asyncio.gather(cancelled, return_exceptions=True)
        assert not journal._waiters

    asyncio.run(replay())


def test_confirmed_death_and_later_positive_hp_start_a_new_map_lifetime(monkeypatch):
    gs = GameState()
    gs.client_state_required = True
    clock = [time.time()]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    cid, pet = 55, 56
    a, b = (22,24,7), (25,24,7)
    sightings = FirstSightings(gs)
    sightings.begin_lap(clock[0]-.1)

    def observe(point, named, hp=100):
        clock[0] += .1
        now = clock[0]
        gs.creatures[cid] = dict(name=named, health=hp, source="dll", t=now, x=point[0], y=point[1], z=7)
        tiles = [dict(x=point[0], y=point[1], z=7, occupants=1, occupant_types={"monster":1})]
        generation = gs.pets.observe_map(tiles, now, {(*point, 1):cid}, gs.creatures)
        frame = dict(captured_at=now, center=(20,24,7), pet_map_generation=generation)
        sightings.observe(frame)
        sightings.observe(frame)
        return tiles[0]

    tile = observe(a, "Bills")
    gs.pets.label_visible(a, tile["pet_ref"], "Bills")
    assert gs.pets.is_pet(cid) and not gs.pets.is_pet(pet)
    death = dict(cid=cid, t=clock[0]+.05, name="Bills")
    gs.kill_log.append(death)
    gs.session_kills = 1
    result = NS(state=GameState(), scalars={}, received_at=time.monotonic(), messages=[], potion_messages=[])
    publish_semantic_result(gs, result, [death])
    assert not gs.pets.is_pet(cid) and death["creature_kind"] == "pet"
    observe(a, "Bills", hp=0)
    fresh = observe(b, "Bills")
    assert fresh["occupant_types"] == {"monster":1}
    assert sightings.end_lap() == [dict(point=b, observer_cell=(16,24,7), name="Bills", count=1)]


def test_adaptive_loss_reappearance_and_out_of_order_death_keep_correct_lifetime(monkeypatch):
    game = NS(position=(12,20,7), creatures={7:{}}, session_kills=0, kill_log=[], attack_target_id=0)
    recorder = HuntEncounters()
    monitor = NS(snapshot=lambda:{"status":"ready"})
    visible = []
    monkeypatch.setattr("hunt_encounters.eligible_monsters", lambda *args: list(visible))
    visible.append(dict(id=7, name="Wolf", position=[13,20,7]))
    recorder.sample(game,monitor,0,10,0,False)
    visible.clear()
    recorder.sample(game,monitor,1,14,0,False)
    assert recorder.events[0]["contact"] == "lost"
    visible.append(dict(id=7, name="Wolf", position=[15,20,7]))
    recorder.sample(game,monitor,2,15,0,False)
    assert len(recorder.events) == 1 and recorder.events[0]["reappearances"] == 1
    assert recorder.events[0]["first_pos"] == [13,20,7]
    visible.clear()
    game.session_kills = 1
    game.kill_log.append(dict(cid=7,t=15.5,name="Wolf",x=15,y=20,z=7))
    recorder.sample(game,monitor,3,16,100,False)
    assert recorder.events[0]["death_pos"] == [15,20,7]
    visible.append(dict(id=7, name="Wolf", position=[16,20,7]))
    game.creatures[7] = dict(t=17,health=100)
    recorder.sample(game,monitor,4,17,0,False)
    game.session_kills = 2
    game.kill_log.append(dict(cid=7,t=16.5,name="Wolf",x=15,y=20,z=7))
    recorder.sample(game,monitor,5,17.5,0,False)
    assert recorder.events[1]["outcome"] == "seen" and 7 in recorder.active


def test_late_death_of_previous_lifetime_does_not_inherit_new_pet_marker(monkeypatch):
    game = GameState()
    now = time.time()
    monkeypatch.setattr(time, "time", lambda: now)
    cid, pos = 123, (30,40,7)
    cell = dict(x=30, y=40, z=7, occupants=1, occupant_types={"monster": 1})
    game.pets.observe_map([cell], now, {(*pos,1):cid})
    game.pets.label_visible(pos, cell["pet_ref"], "Bills")
    old = dict(cid=cid, t=now-.2, name="Bills")
    result = NS(state=GameState(),scalars={},received_at=time.monotonic(),messages=[],potion_messages=[])
    publish_semantic_result(game,result,[old])
    assert old.get("creature_kind") != "pet" and game.pets.is_pet(cid)


def test_recording_excludes_confirmed_pet_after_label_expires(monkeypatch):
    game = GameState()
    game.position = (12,20,7)
    game.creatures[7] = dict(health=100,t=10)
    game.attack_target_id = 7
    visible = [dict(id=7,name="Bills",position=[13,20,7])]
    monkeypatch.setattr("hunt_encounters.eligible_monsters", lambda *args: list(visible))
    monitor = NS(snapshot=lambda:{"status":"ready"})
    recorder = HuntEncounters()
    recorder.sample(game,monitor,0,10,0,False)
    visible.clear()
    game.session_kills=1
    game.kill_log.append(dict(cid=7,t=10.5,name="Bills",creature_kind="pet",x=13,y=20,z=7))
    assert not game.pets.is_pet(7)
    recorder.sample(game,monitor,1,11,100,False)
    assert recorder.events[0]["outcome"] == "pet" and "death_pos" not in recorder.events[0]
    assert not recorder.pending and not recorder.active


def test_xp_on_previous_floor_is_not_assigned_to_death_after_stairs(monkeypatch):
    game = NS(position=(12,20,7), creatures={7:{}}, session_kills=0, kill_log=[], attack_target_id=7)
    recorder = HuntEncounters()
    monitor = NS(snapshot=lambda:{"status":"ready"})
    visible = [dict(id=7, name="Wolf", position=[13,20,7])]
    monkeypatch.setattr("hunt_encounters.eligible_monsters", lambda *args: list(visible))
    recorder.sample(game,monitor,0,10,100,False)
    game.position=(12,20,6)
    game.session_kills=1
    game.kill_log.append(dict(cid=7,t=10.8,name="Wolf",x=13,y=20,z=6))
    visible.clear()
    recorder.sample(game,monitor,1,11,0,False)
    assert recorder.events[0]["death_pos"] == [13,20,6] and recorder.events[0]["outcome"] == "death"


def test_world_changes_only_touched_sectors_and_unknown_baseline_recovers(tmp_path):
    atlas = MapAtlas(tmp_path, "a"*64)
    previous = [[24,32,7],[24,32,6]]
    sketch = atlas.save_draft("Room route", [[24,32,7],[25,32,7]])
    ident = save_area(atlas, dict(scope=atlas.scope,name="Room",cells=previous,drafts=[sketch]))
    first = world_zone_update(atlas,"delta:")
    now = [[24,32,6],[32,32,7]]
    save_area(atlas, dict(scope=atlas.scope,name="Room",cells=now,drafts=[sketch],id=ident,version=1))
    delta = world_zone_update(atlas,"delta:"+first["revision"])
    assert delta["base_revision"] == first["revision"]
    assert set(delta["changed"]) == {(24,32,7),(32,32,7)} and delta["cells"] == [(32,32,7)]
    unchanged = world_zone_update(atlas,"delta:"+delta["revision"])
    assert set(unchanged) == {"scope","revision","side"}
    recovery = world_zone_update(atlas,"delta:unknown")
    assert "changed" not in recovery and set(recovery["cells"]) == set(map(tuple,now))
