import asyncio
import copy
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
import pytest
import mcp_server as server
from bot_profiles import ProfileStore,bot_config,applied_bot_settings,require_stopped
from cavebot_combat import validate_combat
from game_alerts import default_config
from pause_hotkey import PauseHotkey
from test_automation_safety import state
from test_adaptive_cavebot import setup as route_setup


def test_profiles_round_trip_without_runtime_flags_or_route_copies(tmp_path):
    store=ProfileStore(tmp_path/'profiles.json')
    fight=store.save('combat','Luruj 3',{'mode':'lure','lure_count':3})
    assert store.get(fight['id'],'combat')['config']['lure_count']==3
    current={'actions':{'speed_up':{'enabled':True},'dll_bridge':{'enabled':True},'adaptive_cavebot':{'enabled':True}},'combat_profile':fight['config'],'alerts':default_config(),'paused_actions':['speed_up']}
    profile=store.save('bot','Exp',bot_config(current))
    assert 'enabled' not in profile['config']['actions']['speed_up']
    assert 'dll_bridge' not in profile['config']['actions']
    applied=applied_bot_settings(current,profile['config'])
    assert applied['actions']['dll_bridge']['enabled']
    assert not applied['actions']['speed_up']['enabled'] and not applied['actions']['adaptive_cavebot']['enabled']
    assert 'paused_actions' not in applied and 'points' not in json.dumps(profile)
    assert current['actions']['speed_up']['enabled']  # pure application candidate
    store.delete(fight['id'],'combat');assert [r['kind'] for r in store.list()]==['bot']


def test_profile_name_is_a_label_not_a_path_and_bad_payloads_are_rejected(tmp_path):
    store=ProfileStore(tmp_path/'profiles.json')
    row=store.save('combat','../Example',{})
    assert len(row['id'])==32 and store.path.exists()
    with pytest.raises(ValueError):store.save('combat','bad\nname',{})
    with pytest.raises(ValueError):store.save('unknown','name',{})
    with pytest.raises(ValueError):store.get('../Example','combat')
    cfg=bot_config({'actions':{'speed_up':{'enabled':True}}});cfg['actions']['speed_up']['enabled']=True
    with pytest.raises(ValueError):store.save('bot','unsafe',cfg)


def test_loading_combat_allows_healing_but_not_running_v2_and_bot_requires_stop():
    st=NS(_adaptive_prepare_token=None,_action_tasks={'auto_potions':NS(done=lambda:False)})
    require_stopped(st,'combat')
    with pytest.raises(ValueError):require_stopped(st,'bot')
    st._action_tasks['adaptive_cavebot']=NS(done=lambda:False)
    with pytest.raises(ValueError):require_stopped(st,'combat')


def test_bind_route_select_other_combat_and_delete_binding_without_editing_waypoints(monkeypatch,tmp_path):
    monkeypatch.setattr(server,'load_settings',lambda:{'actions':{}})
    st=server.BotState();monkeypatch.setattr(server,'state',st)
    monkeypatch.setattr(server,'SETTINGS_FILE',tmp_path/'settings.json')
    st.profile_store=ProfileStore(tmp_path/'profiles.json')
    draft={'id':'route','points':[[100,100,7],[101,100,7]],'name':'My route'}
    original=copy.deepcopy(draft)
    st.passive_map_monitor=NS(atlas=NS(scope='scope',drafts=lambda:[draft]))
    first=st.profile_store.save('combat','Group 3',{'mode':'lure','lure_count':3})
    second=st.profile_store.save('combat','Group 4',{'mode':'lure','lure_count':4})
    asyncio.run(server._async_profiles({'command':'bind','kind':'combat','scope':'scope','draft_id':'route','id':first['id']}))
    assert st.settings['route_combat_profiles']=={'scope:route':first['id']}
    asyncio.run(server._async_profiles({'command':'apply','kind':'combat','id':second['id']}))
    assert st.settings['combat_profile']['lure_count']==4
    assert st.settings['route_combat_profiles']['scope:route']==first['id']
    assert not st._action_tasks and draft==original
    asyncio.run(server._async_profiles({'command':'delete','kind':'combat','id':first['id']}))
    assert st.settings['route_combat_profiles']=={} and draft==original


def test_pause_key_debounces_repeats_serializes_and_reports_errors(monkeypatch):
    import pause_hotkey
    now=[1.0];monkeypatch.setattr(pause_hotkey.time,'monotonic',lambda:now[0])
    async def run():
        callback=AsyncMock();hotkey=PauseHotkey(asyncio.get_running_loop(),callback)
        hotkey.press();hotkey.press();await asyncio.sleep(0);await asyncio.sleep(0)
        assert callback.await_count==1
        now[0]+=.1;hotkey.press();assert callback.await_count==1
        now[0]+=1;hotkey.press();await asyncio.sleep(0);await asyncio.sleep(0)
        assert callback.await_count==2
        callback.side_effect=ValueError('critical pause cannot resume');now[0]+=1
        hotkey.press();await asyncio.sleep(0);await asyncio.sleep(0)
        assert hotkey.error=='critical pause cannot resume' and not hotkey.busy
    asyncio.run(run())


def test_global_toggle_uses_manual_resume_and_does_not_unlock_emergency(monkeypatch):
    st=NS(_emergency_stopping=False,automation_paused=False)
    monkeypatch.setattr(server,'state',st)
    pause=AsyncMock();resume=AsyncMock(side_effect=ValueError('Emergency STOP'))
    monkeypatch.setattr(server,'_async_pause_automation',pause);monkeypatch.setattr(server,'_async_resume_automation',resume)
    asyncio.run(server._async_toggle_all());pause.assert_awaited_once()
    st.automation_paused=True
    with pytest.raises(ValueError):asyncio.run(server._async_toggle_all())
    resume.assert_awaited_once()


def test_named_combat_changes_preserve_stopped_route_and_spell_cooldowns(state,tmp_path):
    import time
    c,_,draft=route_setup(state,tmp_path)
    c.index=1;state.game_state.position=tuple(c.path[1]);c.stop(preserve_progress=True)
    old_draft=copy.deepcopy(state.passive_map_monitor.atlas.drafts())
    c.combat.cast_after=time.monotonic()+1
    c.combat.deadlines={'shinretsuha':time.monotonic()+2}
    state.profile_store=ProfileStore(tmp_path/'profiles.json')
    row=state.profile_store.save('combat','Group 3',{'enabled':True,'mode':'lure','lure_count':3,'rules':[{'pattern':'*'}]})
    asyncio.run(server._async_profiles({'command':'apply','kind':'combat','id':row['id']}))
    resumed=asyncio.run(c.resume_candidate())
    assert resumed.path==c.path and resumed.index==1
    assert resumed.combat.config['lure_count']==3 and resumed.combat.config['enabled']
    assert resumed.combat.deadlines==c.combat.deadlines and resumed.combat.cast_after==c.combat.cast_after
    assert state.passive_map_monitor.atlas.drafts()==old_draft and not state._action_tasks


def test_pause_break_toggles_selected_actions_and_rejoins_current_waypoint(state,tmp_path,monkeypatch):
    from unittest.mock import Mock
    async def run():
        c,_,_=route_setup(state,tmp_path)
        c.index=1;state.game_state.position=tuple(c.path[1])
        c.combat.deadlines={'shinretsuha':1234}
        state.settings['actions'].update(adaptive_cavebot={'enabled':True},eat_food={'enabled':True},speed_up={'enabled':False})
        state._action_tasks['adaptive_cavebot']=asyncio.create_task(asyncio.sleep(100))
        state._action_tasks['eat_food']=asyncio.create_task(asyncio.sleep(100))
        state._action_tasks['dll_bridge']=asyncio.create_task(asyncio.sleep(100))
        internal=state._action_tasks['dll_bridge']
        await server._async_toggle_all()
        assert state.automation_paused and not internal.done()
        assert set(state.settings['paused_actions'])=={'adaptive_cavebot','eat_food'}
        before=c.waypoint_progress()['current']
        calls=Mock(return_value=None);monkeypatch.setattr(server,'_start_action',calls)
        await server._async_toggle_all()
        resumed=state.adaptive_cavebot
        assert not state.automation_paused and resumed.index==1
        assert resumed.waypoint_progress()['current']==before and resumed.combat.deadlines==c.combat.deadlines
        assert {c.args[0] for c in calls.call_args_list}=={'adaptive_cavebot','eat_food'}
        internal.cancel();await asyncio.gather(internal,return_exceptions=True)
    asyncio.run(run())
