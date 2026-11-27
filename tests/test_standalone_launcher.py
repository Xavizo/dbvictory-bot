"""No real game, process launch or action enabling in launcher tests."""
import asyncio
from types import SimpleNamespace
import pytest
import mcp_server as server
import run_bot as launcher


def test_reopen_dashboard_does_not_restart_connected_game_or_actions(monkeypatch):
    proxy=object();tasks={'auto_potions':object()};calls=[]
    st=SimpleNamespace(game_proxy=proxy,connected=True,_action_tasks=tasks)
    monkeypatch.setattr(server,'state',st)
    monkeypatch.setattr(server,'_launch_dashboard',lambda:calls.append('panel'))
    monkeypatch.setattr(server,'_dashboard_running',lambda name:True)
    assert 'unchanged' in asyncio.run(server.open_dashboard())
    assert calls==['panel'] and st.game_proxy is proxy and st._action_tasks is tasks


def test_reopen_without_session_cannot_start_or_attach(monkeypatch):
    monkeypatch.setattr(server,'state',SimpleNamespace(game_proxy=None))
    def unexpected():raise AssertionError('Must not launch')
    monkeypatch.setattr(server,'_launch_dashboard',unexpected)
    assert asyncio.run(server.open_dashboard()).startswith('ERROR:')


@pytest.mark.parametrize('available',[True,False])
def test_standalone_panel_before_login_only_needs_local_api(monkeypatch,available):
    import dashboard_api
    st=SimpleNamespace(game_proxy=None)
    monkeypatch.setattr(server,'state',st)
    calls=[]
    def start_api(loop,state,*,panel_only):
        assert state is st and panel_only
        return available
    monkeypatch.setattr(dashboard_api,'start_api',start_api)
    monkeypatch.setattr(server,'_launch_dashboard',lambda:calls.append('panel'))
    monkeypatch.setattr(server,'_dashboard_running',lambda name:True)
    result=asyncio.run(server.open_dashboard(standalone=True))
    assert result.startswith('ERROR:') is not available
    assert calls==(['panel'] if available else [])


def test_start_reviews_actions_disables_combat_and_preserves_potions(monkeypatch):
    calls=[]
    async def fake_call(session,name,args=None):calls.append((name,args));return 'OK'
    monkeypatch.setattr(launcher,'call',fake_call)
    settings={'actions':{'auto_potions':{'enabled':True},'adaptive_cavebot':{'enabled':True},'auto_combat':{'enabled':True},'cavebot2':{'enabled':False}}}
    asyncio.run(launcher.start_checked(None,settings))
    assert calls[:2]==[('get_status',None),('list_actions',None)]
    assert calls[2:]==[('toggle_action',{'name':'adaptive_cavebot','enabled':False}),('toggle_action',{'name':'auto_combat','enabled':False}),('start_bot',None)]
    assert settings['actions']['auto_potions']=={'enabled':True}


def test_failed_combat_disable_prevents_attachment(monkeypatch):
    calls=[]
    async def fake_call(session,name,args=None):
        calls.append(name)
        if name=='toggle_action':raise RuntimeError('failed')
        return 'OK'
    monkeypatch.setattr(launcher,'call',fake_call)
    with pytest.raises(RuntimeError):asyncio.run(launcher.start_checked(None,{'actions':{'auto_combat':{'enabled':True}}}))
    assert 'start_bot' not in calls


def test_second_launcher_does_not_start_mcp(monkeypatch):
    monkeypatch.setattr(launcher,'occupied_ports',lambda ports:[4747])
    with pytest.raises(RuntimeError,match='drugiej'):
        asyncio.run(launcher.run(SimpleNamespace(game_port=7475,start=True),{}))


def test_windows_launcher_lock_rejects_duplicate_and_releases(tmp_path):
    import os
    if os.name!='nt':pytest.skip('Windows lock')
    path=tmp_path/'launcher.lock'
    with launcher.launcher_lock(path):
        with pytest.raises(RuntimeError):
            with launcher.launcher_lock(path):pass
    with launcher.launcher_lock(path):pass
