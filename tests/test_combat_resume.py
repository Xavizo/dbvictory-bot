"""Combat-only edits must not invalidate a stopped navigation cursor."""
import asyncio
import json
import time
from copy import deepcopy
from unittest.mock import Mock, patch
from types import SimpleNamespace
import pytest
from dbv.mcp_server import server
from dbv.brain.cavebot.route_identity import same_route,navigation_revision
from test_automation_safety import state
from test_adaptive_cavebot import setup,connect_fake_walk


def test_combat_save_preserves_path_cursor_and_spell_cooldowns_without_starting(state,tmp_path):
    c,_,draft=setup(state,tmp_path);atlas=state.passive_map_monitor.atlas
    c.index=1;state.game_state.position=tuple(c.path[1]);c.stop(preserve_progress=True)
    old=deepcopy(c.route_draft);path=deepcopy(c.path);cursor=c.waypoint_progress()
    c.combat.cast_after=time.monotonic()+1;c.combat.deadlines={'shinretsuha':time.monotonic()+2}
    cfg=dict(enabled=True,mode='lure',lure_count=3,rules=[dict(pattern='*',action='attack',priority=50)],spells=[])
    with patch('dbv.brain.navigation.map_atlas.time', SimpleNamespace(time=lambda: old['updated'] + 1)):
        atlas.change_draft(draft,'configure',cfg,False)
    saved=atlas.drafts()[0]
    assert saved['updated']!=old['updated'] and same_route(saved,old)
    assert saved['navigation_revision']==cursor['navigation_revision']
    resumed=asyncio.run(c.resume_candidate())
    assert resumed.path==path and resumed.index==1 and resumed.waypoint_progress()['current']==cursor['current']
    assert resumed.combat.config['enabled'] and resumed.combat.config['lure_count']==3
    assert resumed.combat.cast_after==c.combat.cast_after and resumed.combat.deadlines==c.combat.deadlines
    assert resumed.combat.deadlines is not c.combat.deadlines
    assert c.combat.config!=resumed.combat.config and c.route_draft==old
    assert not state._action_tasks and state.game_proxy._inject_queue.empty()


def test_start_after_combat_edit_resumes_same_cursor_using_saved_new_profile(state,tmp_path,monkeypatch):
    c,_,draft=setup(state,tmp_path);c.index=1;state.game_state.position=tuple(c.path[1]);c.stop(preserve_progress=True)
    atlas=state.passive_map_monitor.atlas
    atlas.change_draft(draft,'configure',dict(enabled=True,rules=[dict(pattern='Wolf*')]),False)
    start=Mock(return_value=None);monkeypatch.setattr(server,'_start_action',start)
    result=asyncio.run(server._async_adaptive_control('start',dict(scope=c.scope,draft_id=draft,loop=False)))
    assert result['index']==1 and state.adaptive_cavebot.combat.config['enabled']
    assert state.adaptive_cavebot.combat.config['rules'][0]['pattern']=='Wolf*'
    start.assert_called_once_with('adaptive_cavebot')
    assert state.game_proxy._inject_queue.empty()


@pytest.mark.parametrize('field',['name','labels','combat','updated'])
def test_metadata_does_not_change_navigation_identity(field):
    old=dict(id='a',points=[[1,1,7],[2,1,7]],links=[],loop=True,name='before',labels=['',''],combat={},updated=1)
    new=deepcopy(old);new[field]={'name':'after','labels':['Start','Finish'],'combat':{'enabled':True},'updated':2}[field]
    assert navigation_revision(old)==navigation_revision(new) and same_route(old,new)


@pytest.mark.parametrize('field',['id','points','links','loop'])
def test_changed_geometry_does_invalidate_progress(field):
    old=dict(id='a',points=[[1,1,7],[2,1,7]],links=[],loop=True)
    new=deepcopy(old);new[field]={'id':'b','points':[[1,1,7],[2,2,7]],'links':[dict(from_index=0,id='stairs')],'loop':False}[field]
    assert not same_route(old,new)
    assert not same_route(None,old)


def test_resume_rechecks_conflicting_attack_actions_when_combat_newly_enabled(state,tmp_path):
    c,_,draft=setup(state,tmp_path);c.stop(preserve_progress=True)
    state.passive_map_monitor.atlas.change_draft(draft,'configure',dict(enabled=True,rules=[dict(pattern='*')]),False)
    state._action_tasks['auto_targeting']=Mock(done=lambda:False)
    with pytest.raises(ValueError,match='Inna akcja walki'):asyncio.run(c.resume_candidate())
    assert state.game_proxy._inject_queue.empty()


@pytest.mark.parametrize('status',['idle','paused','ready','complete'])
def test_discard_progress_clears_only_runtime_never_draft_or_combat(state,tmp_path,status):
    c,_,draft=setup(state,tmp_path);c.status=status
    before=deepcopy(state.passive_map_monitor.atlas.drafts());state._adaptive_prepare_token=object()
    result=asyncio.run(server._async_adaptive_control('discard_progress'))
    assert result['status']=='idle' and result['total']==0 and not result['resumable']
    assert not state._action_tasks and state._adaptive_prepare_token is None
    assert not state.settings['actions']['adaptive_cavebot']['enabled']
    assert state.passive_map_monitor.atlas.drafts()==before and state.game_proxy._inject_queue.empty()


def test_discard_rejected_while_moving(state,tmp_path):
    c,_,_=setup(state,tmp_path);c.status='running';before=deepcopy(c.path)
    with pytest.raises(ValueError,match='Najpierw zatrzymaj'):asyncio.run(server._async_adaptive_control('discard_progress'))
    assert c.path==before and c.status=='running'


def test_duplicate_start_end_chooses_first_waypoint_and_explains_completion(state,tmp_path):
    c,_,_=setup(state,tmp_path,points=[[10,10,7],[13,10,7],[10,10,7]],loop=False)
    assert c.reason=='Trasa przygotowana od waypointu 1.' and len(c.path)==7
    sent=connect_fake_walk(state,c);asyncio.run(c.run())
    assert len(sent)==6 and c.status=='complete' and 'Powtarzanie wyłączone' in c.reason
    assert c.waypoint_progress()['last_reached']==2
