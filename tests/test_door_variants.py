"""Offline regression for two neighboring doors and an existing sketch link."""
import asyncio

import pytest

from map_atlas import MapAtlas
from protocol import build_use_item_packet
from test_adaptive_cavebot import cell, setup, connect_fake_walk
from test_automation_safety import state
from transition_learning import TransitionLearner


START = [1035, 1299, 7]
DOOR = [1036, 1299, 7]
NEXT_DOOR = [1036, 1300, 7]
EXIT = [1037, 1299, 7]
LINK = 'door-2883ff6d9196b994919b'


def opening(closed, entry=DOOR, opened=5282, kind='door'):
    return dict(id=f'{entry}-{closed}-{opened}', origin=START, entry=entry, exit=START,
                action='use', evidence='observed', item_id=closed, count=1,
                object_change=dict(kind=kind, closed_id=closed, open_id=opened, behavior='open_then_step'))


def door_tile(entry, item):
    opened = item == 5282
    return cell(*entry, item=item, kind='clear' if opened else 'blocked',
                navigation=[dict(kind='door', item_id=item, door_state_hint='open' if opened else 'closed')])


def room(item):
    return [cell(*START), door_tile(DOOR, item), door_tile(NEXT_DOOR, 5280), cell(*EXIT)]


def old_link_plan(atlas, observations):
    return atlas.plan([START, EXIT], observations, links=[dict(from_index=0, id=LINK)])


@pytest.mark.parametrize('live', [5280, 5281, 5282])
def test_original_link_opens_current_variant_at_exact_door_once(state, tmp_path, live):
    cells = room(5282)
    observations = [opening(5280, NEXT_DOOR), opening(5281), opening(5280)]
    controller, _, _ = setup(state, tmp_path, cells, [START, EXIT], observations)
    atlas = state.passive_map_monitor.atlas
    assert old_link_plan(atlas, [opening(5281)])['transitions'][0]['id'] == LINK
    plan = old_link_plan(atlas, observations)
    assert plan['complete'] and len(plan['transitions']) == 1
    edge = plan['transitions'][0]
    assert edge['id'] == LINK and edge['entry'] == DOOR and edge['door_closed_ids'] == [5280, 5281]
    # Exercise the normal route runner after a map change since planning.
    draft = atlas.save_draft('saved door', [START, EXIT], loop=False, links=[dict(from_index=0, id=LINK)])
    controller.prepare(atlas.scope, draft, loop=False)
    assert controller.edges[0]['id'] == LINK
    cells[1].update(door_tile(DOOR, live))
    cells[1]['items'][0]['stack'] = 3
    walked = connect_fake_walk(state, controller)
    walk = state.game_proxy.inject_to_server
    used = []

    async def simulate(payload, guard=None):
        if payload == build_use_item_packet(*DOOR, live, 3, 0):
            assert guard() and live != 5282
            used.append(live)
            cells[1].update(door_tile(DOOR, 5282))
            controller.last_command = 0
        else:
            await walk(payload, guard)

    state.game_proxy.inject_to_server = simulate
    asyncio.run(controller.run())
    assert controller.status == 'complete' and state.game_state.position == tuple(EXIT)
    assert len(walked) == 2 and used == ([] if live == 5282 else [live])
    assert cells[2]['items'][0]['id'] == 5280


@pytest.mark.parametrize('case', ['other_tile', 'different_open', 'gate', 'mixed_stack'])
def test_incompatible_observation_does_not_authorize_saved_door(tmp_path, case):
    atlas = MapAtlas(tmp_path, 'd' * 64)
    cells = room(5280)
    observations = [opening(5281)]
    if case == 'other_tile':
        observations.append(opening(5280, NEXT_DOOR))
    elif case == 'different_open':
        observations.append(opening(5280, opened=5283))
    elif case == 'gate':
        observations.append(opening(5280, kind='level_gate'))
    else:
        observations.append(opening(5280))
        cells[1]['items'].append(dict(id=5281, flags=[], stack=1))
    atlas.observe(dict(status='ready', cells=cells))
    assert not old_link_plan(atlas, observations)['complete']


def test_live_unknown_door_does_not_use_the_neighbor(state, tmp_path):
    cells = room(5282)
    controller, _, _ = setup(state, tmp_path, cells, [START, EXIT],
                             [opening(5281), opening(5280, NEXT_DOOR)])
    cells[1].update(door_tile(DOOR, 5280))
    sent = connect_fake_walk(state, controller)
    asyncio.run(controller.run())
    assert controller.status == 'paused' and not sent
    assert state.game_state.position == tuple(START)


def test_observed_open_appearance_keeps_its_map_annotation(tmp_path):
    learner = TransitionLearner(tmp_path, 'c' * 64)
    learner.records = {r['id']:r for r in [opening(5280), opening(5281)]}
    cells = learner.annotate([door_tile(DOOR, 5282), door_tile(NEXT_DOOR, 5282)])
    assert cells[0]['navigation'][0]['source'] == 'observed'
    assert cells[0]['navigation'][0]['door_state_hint'] == 'open'
    assert cells[1]['navigation'][0].get('source') != 'observed'
