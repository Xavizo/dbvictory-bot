import pytest

from dbv.brain.combat import hunter_knife as hk
from dbv.brain.combat.hunter_knife import (FRESH, GUNSO, MUTANT_WOLF, ROCO, SPENT, CorpseSuggestions,
                                           apply_config, body_counts, exposed_body_action, killed_on,
                                           loot_species, skinned, validate_config)


@pytest.fixture(autouse=True)
def restore_defaults():
    yield
    apply_config({})


def items(*ids):
    return [dict(id=i, stack=s, flags=["container"]) for s, i in enumerate(ids, 1)]


def test_defaults_match_the_previous_hardcoded_lists():
    monsters = validate_config({})["monsters"]
    assert {c["id"] for m in monsters for c in m["corpses"]} == {FRESH, ROCO, GUNSO, MUTANT_WOLF}
    assert all(c["skinned"] == SPENT for m in monsters for c in m["corpses"])
    assert {m["name"] for m in monsters} == {
        "fire elemental", "fire elementalis", "mutant fire elemental", "fury mutant fire",
        "fury mutant fire elemental", "roco", "chibi roco", "fury roco", "gunso", "chibi gunso",
        "fury gunso", "mutant wolf"}


def test_configured_monster_and_corpse_are_recognised():
    apply_config({"monsters": [{"name": "  Big   Ape ", "corpses": [{"id": 1234, "skinned": 4321}]}]})
    assert hk.FRESH_BODIES == {1234} and hk.SKINNABLE_SPECIES == {"big ape"}
    assert exposed_body_action(items(1234))[0] == "skin"
    assert exposed_body_action(items(FRESH)) is None
    assert exposed_body_action(items(1234, 4321))[0] == "uncover"
    assert skinned(body_counts(items(1234)), body_counts(items(4321)), 1234)
    assert not skinned(body_counts(items(1234)), body_counts(items(SPENT)), 1234)
    assert loot_species("12:00 [Big Ape]: 3 gold coins") == "big ape"
    assert loot_species("12:00 [Roco]: 3 gold coins") is None


def test_removing_a_monster_removes_its_corpses():
    monsters = [m for m in validate_config({})["monsters"] if m["name"] != "mutant wolf"]
    apply_config({"monsters": monsters})
    assert MUTANT_WOLF not in hk.FRESH_BODIES and exposed_body_action(items(MUTANT_WOLF)) is None
    assert ROCO in hk.FRESH_BODIES


def test_monster_without_corpse_id_is_kept_but_skins_nothing():
    apply_config({"monsters": [{"name": "Big Ape", "corpses": []}]})
    assert hk.FRESH_BODIES == set() and hk.SKINNABLE_SPECIES == {"big ape"}
    assert exposed_body_action(items(FRESH)) is None


def test_ids_from_text_and_missing_skinned_id_default_to_spent():
    cfg = validate_config({"monsters": [{"name": "a", "corpses": ["777", {"id": 778}]}]})
    assert cfg["monsters"][0]["corpses"] == [{"id": 777, "skinned": SPENT}, {"id": 778, "skinned": SPENT}]


@pytest.mark.parametrize("monsters", [
    [],
    [{"name": "a", "corpses": [{"id": 0}]}],
    [{"name": "a", "corpses": [{"id": 70000}]}],
    [{"name": "a", "corpses": [{"id": 5908}]}],
    [{"name": "a", "corpses": [{"id": 10, "skinned": 10}]}],
    [{"name": "a"}, {"name": "A "}],
    [{"name": "a", "corpses": [{"id": 10, "skinned": 11}]}, {"name": "b", "corpses": [{"id": 11}]}],
    [{"name": "a", "corpses": [{"id": 10, "skinned": 11}]}, {"name": "b", "corpses": [{"id": 10, "skinned": 12}]}],
    [{"name": "[Roco]"}],
    [{"name": ""}],
])
def test_invalid_lists_are_refused(monsters):
    with pytest.raises(ValueError):
        validate_config({"monsters": monsters})


def test_broken_saved_settings_fall_back_to_defaults():
    apply_config({"monsters": "nonsense"})
    assert hk.FRESH_BODIES == {FRESH, ROCO, GUNSO, MUTANT_WOLF}


def test_old_flat_lists_are_replaced_by_the_monster_list():
    cfg = validate_config({"corpses": [{"id": 1}], "species": ["x"], "enabled": True})
    assert "corpses" not in cfg and "species" not in cfg and cfg["enabled"] is True


def test_manual_skin_of_an_unknown_corpse_is_suggested_until_added_or_ignored():
    rows = CorpseSuggestions()
    rows.record(FRESH, SPENT, "Fire Elemental", 1.)  # already on the default list
    rows.record(1234, 4321, "Big  Ape", 2.)
    rows.record(1234, 4321, None, 3.)
    rows.record(555, 556, None, 4.)
    pending = rows.pending({})
    assert [(r["id"], r["skinned"], r["name"], r["seen"]) for r in pending] == [(555, 556, None, 1), (1234, 4321, "big ape", 2)]
    added = {"monsters": [*validate_config({})["monsters"], {"name": "big ape", "corpses": [{"id": 1234, "skinned": 4321}]}],
             "ignored_suggestions": [555]}
    assert rows.pending(added) == []


def test_known_corpse_skinned_from_an_unlisted_monster_is_suggested_for_that_monster():
    rows = CorpseSuggestions()
    rows.record(ROCO, SPENT, "Giant Roco", 1.)
    assert [r["name"] for r in rows.pending({})] == ["giant roco"]


def test_suggestions_are_bounded():
    rows = CorpseSuggestions(limit=3)
    for i in range(10):
        rows.record(100 + i, 50, None, i)
    assert [r["id"] for r in rows.pending({})] == [109, 108, 107]


class Game:
    kill_log = [dict(t=100., name="Old", x=1, y=2, z=7), dict(t=900., name="Big Ape", x=1, y=2, z=7),
                dict(t=950., name="Other", x=5, y=5, z=7)]


def test_killed_on_names_the_latest_recent_kill_on_the_tile():
    assert killed_on(Game, (1, 2, 7), 1000.) == "Big Ape"
    assert killed_on(Game, (1, 2, 7), 5000.) is None
    assert killed_on(Game, (9, 9, 7), 1000.) is None
