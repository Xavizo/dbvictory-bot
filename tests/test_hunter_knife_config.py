import pytest

from dbv.brain.combat import hunter_knife as hk
from dbv.brain.combat.hunter_knife import (DEFAULT_CORPSES, DEFAULT_SPECIES, FRESH, SPENT, apply_config,
                                           body_counts, exposed_body_action, loot_species, skinned,
                                           validate_config)


@pytest.fixture(autouse=True)
def restore_defaults():
    yield
    apply_config({})


def items(*ids):
    return [dict(id=i, stack=s, flags=["container"]) for s, i in enumerate(ids, 1)]


def test_defaults_match_the_previous_hardcoded_lists():
    cfg = validate_config({})
    assert {e["id"] for e in cfg["corpses"]} == {9068, 8548, 910, 8550}
    assert all(e["skinned"] == SPENT for e in cfg["corpses"])
    assert cfg["species"] == list(DEFAULT_SPECIES)


def test_configured_corpse_and_monster_are_recognised():
    apply_config({"corpses": [{"id": 1234, "skinned": 4321}], "species": ["  Big   Ape "]})
    assert hk.FRESH_BODIES == {1234} and hk.SKINNABLE_SPECIES == {"big ape"}
    assert exposed_body_action(items(1234))[0] == "skin"
    assert exposed_body_action(items(FRESH)) is None
    assert exposed_body_action(items(1234, 4321))[0] == "uncover"
    before, after = body_counts(items(1234)), body_counts(items(4321))
    assert skinned(before, after, 1234)
    assert not skinned(body_counts(items(1234)), body_counts(items(SPENT)), 1234)
    assert loot_species("12:00 [Big Ape]: 3 gold coins") == "big ape"
    assert loot_species("12:00 [Roco]: 3 gold coins") is None


def test_ids_from_text_and_missing_skinned_id_default_to_spent():
    cfg = validate_config({"corpses": ["777", {"id": 778}], "species": ["Roco"]})
    assert cfg["corpses"] == [{"id": 777, "skinned": SPENT}, {"id": 778, "skinned": SPENT}]


@pytest.mark.parametrize("cfg", [
    {"corpses": []},
    {"corpses": [{"id": 0}]},
    {"corpses": [{"id": 70000}]},
    {"corpses": [{"id": 5908}]},
    {"corpses": [{"id": 10, "skinned": 10}]},
    {"corpses": [{"id": 10}, {"id": 10}]},
    {"corpses": [{"id": 10, "skinned": 11}, {"id": 11}]},
    {"species": []},
    {"species": ["[Roco]"]},
    {"species": [""]},
])
def test_invalid_lists_are_refused(cfg):
    with pytest.raises(ValueError):
        validate_config(cfg)


def test_broken_saved_settings_fall_back_to_defaults():
    apply_config({"corpses": "nonsense"})
    assert hk.FRESH_BODIES == {e["id"] for e in DEFAULT_CORPSES}
