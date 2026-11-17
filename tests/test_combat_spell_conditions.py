"""Spell policy regression fixtures. No game access or real automation."""
import asyncio
import time
from types import SimpleNamespace
import pytest
from cavebot_combat import validate_spell,validate_combat
from combat_spell_conditions import evaluate_spell,evaluate_spells,spell_reaches,catalogue_aims
from test_v2_combat import armed,monster
from test_automation_safety import state,proxy
from protocol import ClientOpcode,PacketReader


def stats(**kwargs):
    return SimpleNamespace(**dict(dict(hp=80000,max_hp=100000,mana=20000,max_mana=100000),**kwargs))


def row(**kwargs):
    return validate_spell(dict(words='Spell',**kwargs))


@pytest.mark.parametrize('op,expected',[('>',False),('>=',True),('=',True),('<=',True),('<',False)])
def test_all_comparators_at_boundary_for_hp_ki_count_and_distance(op,expected):
    target=dict(id=1,distance=1)
    for patch in (dict(ki=20,ki_op=op),dict(hp=80,hp_op=op),dict(count=2,count_op=op)):
        assert evaluate_spell(row(**patch),stats(),[target,target],target)['ready'] is expected
    assert spell_reaches(row(range=1,range_op=op),target) is expected


def test_absolute_resources_and_percentage_mode_cannot_be_confused():
    target=dict(distance=1)
    s=row(ki=13500,ki_unit='value',hp=85000,hp_unit='value')
    assert evaluate_spell(s,stats(),[target],target)['ready']
    assert not evaluate_spell(s,stats(mana=13000),[target],target)['ready']
    result=evaluate_spell(s,stats(mana=90,max_mana=100),[target],target)
    assert not result['ready'] and 'procenty' in result['reason']
    assert evaluate_spell(row(ki=110),stats(mana=120,max_mana=100),[target],target)['ready']
    assert not evaluate_spell(row(),stats(max_mana=0),[target],target)['ready']


@pytest.mark.parametrize('patch',[
    dict(ki_op='!='),dict(count_op=None),dict(range_op=[]),dict(hp_op={}),dict(ki_unit='raw'),
    dict(hp_unit=[]),dict(hp=True),dict(ki=10.5),dict(hp=-1),dict(hp=101),
    dict(ki=2**31,ki_unit='value'),dict(aim=[]),dict(aim='somewhere'),dict(range=0),dict(count=13),
])
def test_invalid_conditions_are_rejected(patch):
    with pytest.raises(ValueError):row(**patch)


def test_legacy_area_migrates_by_local_catalogue_and_explicit_override_wins(monkeypatch):
    monkeypatch.setattr('combat_spell_conditions.catalogue_aims',lambda:{'shunkan':'self'})
    assert validate_spell(dict(words='Shunkan'))['aim']=='self'
    assert validate_spell(dict(words='Shunkan',aim='target'))['aim']=='target'
    assert validate_spell(dict(words='Unknown'))['aim']=='target'


def test_area_priority_uses_nearby_monsters_even_with_distant_selected_target():
    near=[dict(id=1,distance=1),dict(id=2,distance=1)]
    far=dict(id=3,distance=5)
    aoe=row(aim='self',range=1,count=2,cooldown=1.5)
    wave=validate_spell(dict(words='Wave',range=6,cooldown=1))
    checks=evaluate_spells([aoe,wave],stats(),near+[far],far)
    assert checks[0]['selected'] and checks[0]['matched_count']==2 and not checks[1]['selected']
    checks=evaluate_spells([aoe,wave],stats(),near[:1]+[far],far)
    assert not checks[0]['selected'] and checks[1]['selected'] and 'Moby: 1' in checks[0]['reason']
    checks=evaluate_spells([aoe,wave],stats(),near+[far],far,now=10,deadlines={'spell':11})
    assert checks[1]['selected'] and 'Odstęp' in checks[0]['reason']


def test_no_candidate_or_incorrect_target_distance_is_explained():
    s=row(range=1,count=1)
    assert 'Brak' in evaluate_spell(s,stats(),[],None)['reason']
    assert 'Cel:' in evaluate_spell(s,stats(),[dict(distance=1)],dict(distance=4))['reason']


def test_cast_condition_change_skips_attempt_without_pausing_or_reserving_cooldown(state,tmp_path):
    c,cells=armed(state,tmp_path,spells=[dict(words='Shunkan',aim='self',range=1,count=2)])
    state.game_state.mana=state.game_state.max_mana=100;c.status='running'
    monster(state,cells,x=11,y=10);monster(state,cells,x=11,y=11,cid=0x40000002)
    sent=[]
    async def inject(payload,guard=None):
        assert guard();sent.append(payload);c.last_command=0
        if payload[0]==ClientOpcode.ATTACK:state.game_state.creatures.clear()
    state.game_proxy.inject_to_server=inject
    asyncio.run(c.combat.tick(state.passive_map_monitor.snapshot()))
    assert c.status=='running' and [p[0] for p in sent]==[ClientOpcode.ATTACK]
    assert not c.combat.deadlines and c.combat.last_spell_attempt is None
    assert 'ponowię' in c.combat.spell_checks[0]['reason']


def test_shared_interval_prevents_second_spell_from_starving_first_and_queue_guard_survives_reservation(state,tmp_path):
    c,cells=armed(state,tmp_path,spells=[dict(words='Shunkan',aim='self',range=1,count=2,cooldown=1.5),dict(words='Wave',range=6,cooldown=1)])
    state.game_state.mana=state.game_state.max_mana=100;c.status='running'
    monster(state,cells,x=11,y=10);monster(state,cells,x=11,y=11,cid=0x40000002)
    async def scenario():
        await c.combat.tick(state.passive_map_monitor.snapshot())
        packets=[]
        while not state.game_proxy._inject_queue.empty():
            _,payload,guard=state.game_proxy._inject_queue.get_nowait();assert guard();packets.append(payload)
        assert len(packets)==2 and packets[-1][0]==ClientOpcode.SAY
        assert b'Shunkan' in packets[-1] and c.combat.cast_after>time.monotonic()+1
        await c.combat.tick(state.passive_map_monitor.snapshot())
        assert state.game_proxy._inject_queue.empty()
    asyncio.run(scenario())


def test_skippable_combat_does_not_bypass_death_or_navigation_failure(state,tmp_path):
    c,cells=armed(state,tmp_path);c.status='running'
    async def scenario():
        assert await c.send(b'\xa1',lambda:False,skip_if_changed=True) is False
        with pytest.raises(ValueError):await c.send(b'\x66',lambda:False,skip_if_changed=True)
        state.game_state.hp=0
        with pytest.raises(ValueError):await c.send(b'\xa1',lambda:False,skip_if_changed=True)
    asyncio.run(scenario())
