"""On-target regression with synthetic transport; never connects to the client."""
import asyncio
import time
import pytest
import combat_wave as wave
from cavebot_combat import validate_spell
from combat_spell_conditions import evaluate_spell
from protocol import ClientOpcode
from test_v2_combat import armed, monster
from test_automation_safety import state


def configured(state, tmp_path, **condition):
    c, cells = armed(state, tmp_path, spells=[dict(words='Shinretsuha', aim='target',
        range=3, range_op='<=', count=1, ki=10, cooldown=1, **condition)])
    c.combat_only = True
    c.status = 'running'
    state.game_state.mana = state.game_state.max_mana = 100
    # No facing sample: targeted casting must not depend on orientation.
    state.game_state.client_snapshot = None
    return c, cells


@pytest.mark.parametrize('x,y', [(11, 10), (11, 11), (12, 11), (13, 12)])
def test_targeted_casts_at_axial_diagonal_and_ranged_targets_without_turn_or_walk(state, tmp_path, monkeypatch, x, y):
    c, cells = configured(state, tmp_path)
    monkeypatch.setattr(wave, 'catalogue_waves', lambda: {'shinretsuha'})
    monster(state, cells, x=x, y=y)
    asyncio.run(c.combat.tick(state.passive_map_monitor.snapshot()))
    packets = []
    while not state.game_proxy._inject_queue.empty():
        packets.append(state.game_proxy._inject_queue.get_nowait()[1])
    assert [p[0] for p in packets] == [ClientOpcode.ATTACK, ClientOpcode.SAY]
    assert b'Shinretsuha' in packets[-1]
    assert c.combat.wave_turn is None and state.game_state.position == (10, 10, 7)


@pytest.mark.parametrize('failure', ['range', 'ki', 'hp', 'count', 'no_target', 'cooldown'])
def test_targeted_override_preserves_user_conditions(state, tmp_path, failure):
    c, cells = configured(state, tmp_path)
    monster(state, cells, x=12, y=11)
    target = c.combat.candidates()[0]
    spell = c.combat.config['spells'][0]
    kwargs = {}
    if failure == 'range': spell.update(range=1, range_op='=')
    if failure == 'ki': state.game_state.mana = 9
    if failure == 'hp': spell.update(hp=50, hp_op='<=')
    if failure == 'count': spell.update(count=2)
    if failure == 'no_target': target = None
    if failure == 'cooldown': kwargs['cast_after'] = 2
    check = evaluate_spell(spell, state.game_state, c.combat.candidates(), target,
                           now=1, sample=state.passive_map_monitor.snapshot(), **kwargs)
    assert not check['ready'] and not check['turn_required'] and check['wave_note'] is None


@pytest.mark.parametrize('two_mobs', [False, True])
def test_shunkan_priority_and_shinretsuha_fallback_on_diagonal(state, tmp_path, two_mobs):
    c, cells = configured(state, tmp_path)
    c.combat.config['spells'].insert(0, validate_spell(dict(words='Shunkan', aim='self', range=1, count=2)))
    monster(state, cells, x=11, y=11)
    if two_mobs: monster(state, cells, x=11, y=10, cid=0x40000002)
    asyncio.run(c.combat.tick(state.passive_map_monitor.snapshot()))
    assert c.combat.last_spell_attempt['words'] == ('Shunkan' if two_mobs else 'Shinretsuha')


@pytest.mark.parametrize('change', ['out_of_range', 'dead', 'manual_target', 'pause', 'disconnect'])
def test_targeted_queued_cast_rechecks_target_and_session(state, tmp_path, change):
    c, cells = configured(state, tmp_path)
    cid, raw, _ = monster(state, cells, x=11, y=11)
    async def scenario():
        await c.combat.tick(state.passive_map_monitor.snapshot())
        queued = []
        while not state.game_proxy._inject_queue.empty(): queued.append(state.game_proxy._inject_queue.get_nowait())
        cast = next(row for row in queued if row[1][0] == ClientOpcode.SAY)
        assert cast[2]()
        if change == 'out_of_range': raw['x'] = 20
        if change == 'dead': raw['health'] = 0
        if change == 'manual_target': c.combat.accept_manual_target(cid + 1)
        if change == 'pause': c.pause('manual')
        if change == 'disconnect': state.game_proxy.logged_in = False
        assert not cast[2]()
    asyncio.run(scenario())
