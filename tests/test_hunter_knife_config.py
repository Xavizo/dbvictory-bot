import pytest

from dbv.brain.combat import hunter_knife as hk
from dbv.brain.combat.hunter_knife import (FRESH, GUNSO, MUTANT_WOLF, ROCO, SPENT, LearnedCorpses, apply_config,
                                           body_counts, exposed_body_action, learn_into, loot_species, skinned,
                                           validate_config, with_learned)


@pytest.fixture(autouse=True)
def restore_defaults():
    yield
    apply_config({})


def items(*ids):
    return [dict(id=i, stack=s, flags=["container"]) for s, i in enumerate(ids, 1)]


def test_defaults_are_the_four_known_corpses():
    assert validate_config({})["corpses"] == [{"id": i, "skinned": SPENT} for i in (FRESH, ROCO, GUNSO, MUTANT_WOLF)]


def test_configured_corpse_is_recognised_and_removed_one_is_not():
    apply_config({"corpses": [{"id": 1234, "skinned": 4321}]})
    assert hk.FRESH_BODIES == {1234}
    assert exposed_body_action(items(1234))[0] == "skin"
    assert exposed_body_action(items(MUTANT_WOLF)) is None
    assert exposed_body_action(items(1234, 4321))[0] == "uncover"
    assert skinned(body_counts(items(1234)), body_counts(items(4321)), 1234)


def test_ids_from_text_and_missing_skinned_id_default_to_spent():
    assert validate_config({"corpses": ["777", {"id": 778}]})["corpses"] == [
        {"id": 777, "skinned": SPENT}, {"id": 778, "skinned": SPENT}]


def test_settings_from_the_monster_window_fall_back_to_the_corpse_list():
    cfg = validate_config({"enabled": True, "monsters": [{"name": "x", "corpses": []}], "ignored_suggestions": []})
    assert cfg == {"enabled": True, "corpses": validate_config({})["corpses"]}


@pytest.mark.parametrize("corpses", [
    [{"id": 0}], [{"id": 70000}], [{"id": 5908}], [{"id": 10, "skinned": 10}],
    [{"id": 10, "skinned": 11}, {"id": 11}], "nonsense",
])
def test_invalid_lists_are_refused(corpses):
    with pytest.raises(ValueError):
        validate_config({"corpses": corpses})


def test_broken_saved_settings_fall_back_to_defaults():
    apply_config({"corpses": "nonsense"})
    assert hk.FRESH_BODIES == {FRESH, ROCO, GUNSO, MUTANT_WOLF}


def test_hand_skinned_corpses_are_added_once():
    cfg = with_learned({}, {1234: (4321, "Big  Ape"), MUTANT_WOLF: (SPENT, "Mutant Wolf")})
    assert cfg["corpses"][-1] == {"id": 1234, "skinned": 4321, "monster": "big ape"} and len(cfg["corpses"]) == 5
    assert with_learned(cfg, {1234: (4321, None)}) is None
    assert with_learned({}, {SPENT: (1, None)}) is None  # a skinned corpse is never a fresh one
    assert with_learned({}, {55: (56, None)})["corpses"][-1] == {"id": 55, "skinned": 56}


def test_learned_monster_counts_as_kill_evidence():
    apply_config(with_learned({}, {1234: (4321, "Big Ape")}))
    assert "big ape" in hk.SKINNABLE_SPECIES and "mutant wolf" in hk.SKINNABLE_SPECIES
    assert loot_species("[Big Ape]: 2 gold coins") == "big ape"
    apply_config({})
    assert "big ape" not in hk.SKINNABLE_SPECIES


def test_learning_is_saved_into_settings_once():
    learned = LearnedCorpses()
    calls = []
    learned.on_record = lambda: calls.append(True)
    learned.record(1234, 4321, "Big Ape")
    settings = {"actions": {"auto_hunter_knife": {"enabled": False}}}
    assert calls == [True] and learn_into(settings, learned)
    cfg = settings["actions"]["auto_hunter_knife"]
    assert {"id": 1234, "skinned": 4321, "monster": "big ape"} in cfg["corpses"] and cfg["enabled"] is False
    assert learned.snapshot() == {} and not learn_into(settings, learned)


def test_server_saves_hand_skins_while_automation_is_off(monkeypatch):
    from dbv.mcp_server import server
    saved = []
    monkeypatch.setattr(server, "save_settings", lambda settings: saved.append(settings))
    monkeypatch.setattr(server.state, "settings", {"actions": {"auto_hunter_knife": {"enabled": False}}})
    monkeypatch.setattr(server.state, "knife_learned", LearnedCorpses())
    server.state.knife_learned.record(1234, 4321, None)
    server._save_learned_knife_corpses()
    assert {"id": 1234, "skinned": 4321} in saved[0]["actions"]["auto_hunter_knife"]["corpses"]
