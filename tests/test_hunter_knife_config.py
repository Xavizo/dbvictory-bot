from types import SimpleNamespace

import pytest

from dbv.brain.combat import hunter_knife as hk
from dbv.brain.combat.hunter_knife import (FRESH, GUNSO, MUTANT_WOLF, ROCO, SPENT, LearnedCorpses, apply_config,
                                           body_counts, exposed_body_action, skinned, validate_config,
                                           with_learned)
from dbv.brain.combat.hunter_knife_action import NAME, learn


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
    cfg = with_learned({}, {1234: 4321, MUTANT_WOLF: SPENT})
    assert cfg["corpses"][-1] == {"id": 1234, "skinned": 4321} and len(cfg["corpses"]) == 5
    assert with_learned(cfg, {1234: 4321}) is None
    assert with_learned({}, {SPENT: 1}) is None  # a skinned corpse is never a fresh one


def test_worker_saves_learned_corpses_to_settings():
    learned = LearnedCorpses()
    learned.record(1234, 4321)
    saved = []
    settings = {"actions": {NAME: {"enabled": True}}}
    bot = SimpleNamespace(state=SimpleNamespace(knife_learned=learned, settings=settings),
                          save_settings=lambda: saved.append(True))
    learn(bot)
    assert {"id": 1234, "skinned": 4321} in settings["actions"][NAME]["corpses"]
    assert settings["actions"][NAME]["enabled"] is True and saved == [True] and learned.snapshot() == {}
    learn(bot)
    assert saved == [True]
