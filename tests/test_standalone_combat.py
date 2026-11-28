"""Walka (actions/auto_combat.py): standalone combat that gives way to the cavebot."""
import asyncio
import pytest
from dbv.mcp_server import server
from test_adaptive_cavebot import setup
from test_automation_safety import state, proxy  # noqa: F401 (fixtures)

WOLVES = dict(rules=[dict(pattern='Wolf*')], hold_target=True)


def _quiet(state):
    state.game_state.player_id = 0x10000001
    async def inject(payload, guard=None):
        pass
    state.game_proxy.inject_to_server = inject


async def _until(check, seconds=2.0):
    for _ in range(int(seconds / .02)):
        if check():
            return
        await asyncio.sleep(.02)
    raise AssertionError(f"condition not reached {server.state.standalone_combat} {server.state._action_tasks} {dict(server.state.action_logs)}")


def test_standalone_needs_a_monster_before_it_can_be_switched_on(state, tmp_path):
    setup(state, tmp_path)
    message = asyncio.run(server._async_toggle_action('auto_combat', True))
    assert message.startswith('Error') and 'potwora' in message
    assert not state.settings['actions'].get('auto_combat', {}).get('enabled')


def test_settings_are_its_own_and_full_screen_with_8_sqm_saves(state, tmp_path):
    setup(state, tmp_path)
    combat = {**WOLVES, 'engage': 'near', 'radius': 8,
              'spells': [dict(words='Spell', trigger='visible', range=8)]}
    saved = asyncio.run(server._async_update_action_config('auto_combat', {'combat': combat}))['combat']
    assert saved['radius'] == 8 and saved['spells'][0]['range'] == 8 and saved['hold_target']
    assert 'combat_profile' not in state.settings  # the cavebot's own combat is untouched


def test_fights_alone_then_the_cavebot_takes_over_and_gives_it_back(state, tmp_path):
    route, _, draft = setup(state, tmp_path)
    _quiet(state)
    state.settings['actions']['auto_combat'] = {'combat': WOLVES}

    async def scenario():
        assert 'started' in await server._async_toggle_action('auto_combat', True)
        await _until(server._standalone_running)
        fighting = state.adaptive_cavebot
        assert fighting.combat_only and fighting.combat.config['hold_target']
        assert state._parked_route is route        # the prepared route waits aside
        assert state.standalone_combat['status'] == 'fighting'
        assert not state.settings['actions'].get('adaptive_cavebot', {}).get('enabled')

        # Cavebot Start from the panel: standalone combat stops first.
        result = await server._async_adaptive_control('start_combat', {'combat': WOLVES})
        assert result['mode'] == 'combat' and not getattr(state.adaptive_cavebot, 'standalone', False)
        await asyncio.sleep(.6)
        assert state.standalone_combat['status'] == 'yielding'
        assert not server._standalone_running()

        # When the cavebot stops, standalone combat takes over again.
        await server._async_adaptive_control('stop')
        await _until(server._standalone_running)

        assert 'disabled' in await server._async_toggle_action('auto_combat', False)
        await _until(lambda: not server._live(state._action_tasks.get('adaptive_cavebot')))
        assert not getattr(state.adaptive_cavebot, 'standalone', False)
    asyncio.run(scenario())


def test_cavebot_view_sees_the_parked_route_not_standalone_combat(state, tmp_path):
    route, _, draft = setup(state, tmp_path)
    _quiet(state)
    state.settings['actions']['auto_combat'] = {'combat': WOLVES}

    async def scenario():
        await server._async_toggle_action('auto_combat', True)
        await _until(server._standalone_running)
        assert (await server._async_adaptive_control('pause'))['draft_id'] == draft
        assert server._standalone_running()        # Pause in the cavebot view does not touch it
        # Cavebot Start of the route: standalone stops and the route is back in place.
        await server._async_adaptive_control('start', {'scope': route.scope, 'draft_id': draft, 'loop': False})
        assert state.adaptive_cavebot.draft_id == draft and state._parked_route is None
        await server._async_adaptive_control('stop')
        await server._async_toggle_action('auto_combat', False)
        await asyncio.sleep(.1)
    asyncio.run(scenario())
