"""Real map Look / server reply / Cavebot paths with synthetic occupants."""
import struct
import time
from types import SimpleNamespace

import pytest

from cavebot_combat import CombatRuntime, validate_combat, validate_spell
from combat_spell_conditions import evaluate_spell
from game_state import GameState, parse_server_packet, scan_packet
from passive_map import PassiveMapMonitor
from protocol import ClientOpcode, PacketReader, ServerOpcode, build_look_packet

PET, WILD = 0x40000021, 0x40000022
START = (51, 50, 7)
NEXT = (51, 51, 7)


def sample(gs, placements, age=0):
    cells, slots = {}, {}
    for pos, stack, identity in placements:
        cell = cells.setdefault(pos, dict(x=pos[0], y=pos[1], z=pos[2], kind="clear",
                                          occupants=0, occupant_types={"monster": 0}, items=[]))
        cell["occupants"] += 1
        cell["occupant_types"]["monster"] += 1
        slots[(*pos, stack)] = identity
    cells = list(cells.values())
    captured = time.time()-age
    generation = gs.pets.observe_map(cells, captured, slots)
    return dict(cells=cells, status="ready", center=gs.position, age_seconds=age, captured_at=captured,
                pet_map_generation=generation)


def look(gs, position=START, stack=1):
    monitor = PassiveMapMonitor(SimpleNamespace(connected=True, game_state=gs))
    payload = build_look_packet(*position, 99, stack)
    monitor.record_client_packet(ClientOpcode.LOOK, PacketReader(payload[1:]))


def reply(gs, text, legacy=False, message_type=25):
    data = text.encode("latin-1")
    data = struct.pack("<BH", message_type, len(data)) + data
    if legacy:
        parse_server_packet(ServerOpcode.TEXT_MESSAGE, PacketReader(data), gs)
    else:
        scan_packet(bytes([ServerOpcode.TEXT_MESSAGE]) + data, gs)


@pytest.mark.parametrize("battle_pet", [True, False])
def test_pet_moves_as_occupied_tile_but_is_never_a_target_group_member_or_spell_count(battle_pet, monkeypatch):
    # Consecutive Windows wall-clock readings can coincide as a creature moves.
    now = time.time()
    monkeypatch.setattr(time, "time", lambda: now)
    gs = GameState()
    gs.position = (50, 50, 7)
    gs.hp = gs.mana = gs.max_hp = gs.max_mana = 100
    identity = PET if battle_pet else ("visible", 123)
    prior_map = sample(gs, [(START, 1, identity), (NEXT, 1, WILD)])
    look(gs)
    reply(gs, "You see Bills\r\nMaster of Dupa Biskupa")
    config = validate_combat(dict(enabled=True, mode="lure", lure_count=2,
                                  rules=[{"pattern":"*", "action":"attack"}]))
    runtime = CombatRuntime(SimpleNamespace(state=SimpleNamespace(game_state=gs)), config)
    assert runtime.visible_group(prior_map) == [(NEXT, 1)]
    live = sample(gs, [(NEXT, 2, identity), (START, 1, WILD)])
    assert live["cells"][0]["occupant_types"] == {"pet": 1}
    assert live["cells"][0]["occupants"] == 1 and live["cells"][0]["pet_names"] == ["Bills"]
    assert live["cells"][1]["occupant_types"] == {"monster": 1}
    for cid, pos in [(WILD, START)] + ([(PET, NEXT)] if battle_pet else []):
        gs.creatures[cid] = dict(x=pos[0], y=pos[1], z=pos[2], name="Bills", source="dll",
                                 health=100, t=time.time())
    targets = runtime.candidates(live)
    assert [c["id"] for c in targets] == [WILD]
    runtime.refresh_lure_limit(targets, runtime.visible_group(live), False)
    assert runtime.engaged_count == 1 and runtime.lure_stop_reason is None
    spell = validate_spell(dict(words="Shunkan", aim="self", count=2, range=1))
    assert evaluate_spell(spell, gs, targets, targets[0])["matched_count"] == 1
    alone = sample(gs, [(NEXT, 1, identity)])
    assert runtime.candidates(alone) == [] and runtime.visible_group(alone) == []
    runtime.refresh_lure_limit([], [], True)
    assert runtime.lure_stop_reason is None


def test_only_clicked_stack_is_retyped_and_local_identity_expires_when_gone():
    gs = GameState()
    key = ("visible", 12)
    placed = [(START, 1, WILD), (START, 2, key)]
    sample(gs, placed)
    look(gs, stack=2)
    reply(gs, "You see Bills.", legacy=True)
    reply(gs, "Master of Trainer.", legacy=True)
    tile = sample(gs, placed)["cells"][0]
    assert tile["occupants"] == 2 and tile["occupant_types"] == {"monster":1, "pet":1}
    sample(gs, [])
    assert sample(gs, placed)["cells"][0]["occupant_types"] == {"monster":2}


@pytest.mark.parametrize("case", ["no-look", "wrong-stack", "stale", "two-looks", "moved",
                                 "ordinary", "chat-like", "wrong-type", "name-mismatch", "new-login", "duplicate-id"])
def test_ambiguous_look_does_not_label_an_unrelated_monster(case):
    gs = GameState()
    placements = [(START, 1, PET)]
    if case == "duplicate-id":
        placements.append((NEXT, 1, PET))
    sample(gs, placements, age=1 if case=="stale" else 0)
    if case != "no-look":
        look(gs, stack=2 if case=="wrong-stack" else 1)
    if case=="two-looks":
        look(gs)
    if case=="moved":
        sample(gs, [(START, 1, WILD), (NEXT, 1, PET)])
    if case=="new-login":
        gs = GameState()
    if case=="name-mismatch":
        gs.creatures[PET] = {"name": "Wolf", "t": time.time()}
    text = {"ordinary":"You see Bills.", "chat-like":"Trainer says: You see Bills\nMaster of Trainer"}
    reply(gs, text.get(case, "You see Bills.\nMaster of Trainer."), message_type=17 if case=="wrong-type" else 25)
    assert not gs.pets.is_pet(PET) and not gs.pets.is_pet(WILD)


@pytest.mark.parametrize("new_tile", [START, NEXT])
def test_look_stays_with_identifiable_pet_on_stack_reorder_or_step(new_tile):
    gs = GameState()
    sample(gs, [(START, 1, PET)])
    look(gs)
    cells = [dict(x=new_tile[0], y=new_tile[1], z=new_tile[2], occupants=1, occupant_types={"monster": 1})]
    if new_tile != START:
        cells.append(dict(x=START[0], y=START[1], z=START[2], occupants=0, occupant_types={}))
    gs.pets.observe_map(cells, time.time(), {(*new_tile, 2): PET})
    reply(gs, "You see Bills\nMaster of Trainer")
    assert sample(gs, [(new_tile, 2, PET)])["cells"][0]["pet_names"] == ["Bills"]


@pytest.mark.parametrize("identity", [PET, ("visible", 15)])
def test_manual_label_follows_one_visible_creature_and_expires(identity, caplog):
    gs = GameState()
    first = sample(gs, [(START, 1, identity)])
    ref = first["cells"][0]["pet_ref"]
    with caplog.at_level("INFO", logger="pet_tracking"):
        gs.pets.label_visible(START, ref, "Bills")
    expected_identity = f"game ID {PET}" if identity == PET else "visible on map only; no game ID"
    assert caplog.messages == [f"Pet confirmed: 'Bills', {expected_identity}"]
    assert gs.pets.confirmed_after_map(START, first["pet_map_generation"]) == 1
    live = sample(gs, [(NEXT, 2, identity), (START, 1, WILD)])
    pet, monster = live["cells"]
    assert pet["occupant_types"] == {"pet": 1} and pet["pet_names"] == ["Bills"]
    assert pet["occupants"] == 1 and pet["pet_manual"] and pet["pet_ref"] == ref
    assert monster["occupant_types"] == {"monster": 1}
    assert gs.pets.monster_slots_for_sample(live["pet_map_generation"]) == {START: [WILD]}
    with pytest.raises(ValueError):
        gs.pets.label_visible(START, ref, None)
    gs.pets.label_visible(NEXT, ref, None)
    assert not gs.pets.is_pet(identity)
    gs.pets.label_visible(NEXT, ref, "Bills")
    sample(gs, [])
    later = sample(gs, [(START, 1, identity)])["cells"][0]
    assert later["pet_ref"] != ref and later["occupant_types"] == {"monster": 1}
    with pytest.raises(ValueError):
        gs.pets.label_visible(START, ref, "Bills")


def test_manual_label_rejects_ambiguous_stale_or_replaced_creatures():
    gs = GameState()
    ref = sample(gs, [(START, 1, PET)])["cells"][0]["pet_ref"]
    double = sample(gs, [(START, 1, PET), (START, 2, WILD)])["cells"][0]
    assert "pet_ref" not in double
    with pytest.raises(ValueError):
        gs.pets.label_visible(START, ref, "Bills")
    sample(gs, [(START, 1, WILD)])
    with pytest.raises(ValueError):
        gs.pets.label_visible(START, ref, "Bills")
    old = sample(gs, [(START, 1, PET)], age=3)["cells"][0]
    with pytest.raises(ValueError):
        gs.pets.label_visible(START, old["pet_ref"], "Bills")
    assert not gs.pets.is_pet(PET) and not gs.pets.is_pet(WILD)
