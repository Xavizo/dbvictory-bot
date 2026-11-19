"""Exercise the actual HTTP and WebSocket boundaries without a game client."""

import asyncio
import http.client
import json
import threading
from http.server import HTTPServer

import pytest
import websockets
from websockets.exceptions import ConnectionClosed, InvalidStatus

import dashboard_api as api
from dashboard_security import DASHBOARD_ORIGIN, SESSION_TOKEN


@pytest.fixture
def http_api(monkeypatch):
    mutations = []
    monkeypatch.setattr(api, "_build_state_json", lambda: '{"connected":false}')
    def toggle(handler, name):
        mutations.append(name)
        handler._json_response({"ok": True})
    monkeypatch.setattr(api._Handler, "_handle_toggle", toggle)
    server = HTTPServer(("127.0.0.1", 0), api._Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def request(method="GET", path="/api/state", body=None, **headers):
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
        try:
            conn.request(method, path, body=body, headers=headers)
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            conn.close()
    yield request, mutations
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/profiles"), ("POST", "/api/profiles"),
    ("GET", "/api/alerts"), ("POST", "/api/alerts"),
    ("GET", "/api/state"), ("POST", "/api/actions/power_up/toggle"),
    ("DELETE", "/api/actions/power_up"),
    ("POST", "/api/emergency-stop"), ("POST", "/api/automation/unlock"),
    ("POST", "/api/potions/diagnostics"),
    ("POST", "/api/map/annotation"), ("POST", "/api/map/pet"),
    ("POST", "/api/map/drafts"), ("POST", "/api/adaptive/preview"),
])
def test_unauthenticated_requests_are_rejected(http_api, method, path):
    request, mutations = http_api
    assert request(method, path)[0] == 401
    assert not mutations


def test_authentication_and_origin_enforced_before_mutation(http_api):
    request, mutations = http_api
    headers = {"Origin": DASHBOARD_ORIGIN, "Authorization": f"Bearer {SESSION_TOKEN}"}
    path = "/api/actions/power_up/toggle"
    for origin in ("https://evil.example", "null", DASHBOARD_ORIGIN + ".evil.example"):
        assert request("POST", path, **{**headers, "Origin": origin})[0] == 403
    assert request("POST", path, **{**headers, "Authorization": "Bearer wrong"})[0] == 401
    assert request("POST", path, **{**headers, "Host": "evil.example:8089"})[0] == 403
    assert not mutations
    status, response_headers, _ = request("POST", path, **headers)
    assert status == 200
    assert response_headers["Access-Control-Allow-Origin"] == DASHBOARD_ORIGIN
    assert response_headers["Cache-Control"] == "no-store"
    assert mutations == ["power_up"]


def test_lap_reports_endpoint_is_read_only_and_requires_session(http_api, monkeypatch):
    from types import SimpleNamespace
    request, mutations = http_api
    monkeypatch.setattr(api, "_state", SimpleNamespace())
    path = "/api/cavebot/reports"
    headers = {"Origin": DASHBOARD_ORIGIN, "Authorization": f"Bearer {SESSION_TOKEN}"}
    assert request("GET",path)[0] == 401
    status, _, body = request("GET",path,**headers)
    assert status == 200 and json.loads(body) == {"active":None,"reports":[],"more":False}
    assert request("GET",path+"?id=missing",**headers)[0] == 404
    assert request("GET",path+"?before=nan",**headers)[0] == 400
    assert not mutations


def test_annotation_endpoint_requires_origin_and_passes_only_valid_request(http_api,monkeypatch):
    from types import SimpleNamespace
    calls=[]
    monitor=SimpleNamespace(save_annotation=lambda body:calls.append(body) or "saved")
    monkeypatch.setattr(api,"_state",SimpleNamespace(passive_map_monitor=monitor))
    request,_=http_api
    headers={"Origin":DASHBOARD_ORIGIN,"Authorization":f"Bearer {SESSION_TOKEN}"}
    path="/api/map/annotation"
    assert request("POST",path,body='{}',**{**headers,"Origin":"https://evil.example"})[0]==403
    assert not calls
    assert request("POST",path,body='x'*4097,**headers)[0]==400
    assert not calls
    assert request("POST",path,body='{"operation":"save"}',**headers)[0]==200
    assert calls==[{"operation":"save"}]


def test_manual_pet_label_applies_only_to_the_current_map_reference(http_api, monkeypatch):
    import time
    from types import SimpleNamespace
    from game_state import GameState
    state = SimpleNamespace(connected=True, game_state=GameState())
    monkeypatch.setattr(api, "_state", state)
    tile = dict(x=10, y=20, z=7, occupants=1, occupant_types={"monster": 1})
    state.game_state.pets.observe_map([tile], time.time(), {(10, 20, 7, 1): 70})
    request, _ = http_api
    headers = {"Origin": DASHBOARD_ORIGIN, "Authorization": f"Bearer {SESSION_TOKEN}"}
    def post(**changes):
        data = {"position": [10, 20, 7], "ref": tile["pet_ref"], "name": "Bills", **changes}
        return request("POST", "/api/map/pet", body=json.dumps(data), **headers)[0]
    assert post(ref="old") == 400
    assert post(position=[12, 20, 7]) == 400
    assert post(name=1) == 400
    assert not state.game_state.pets.is_pet(70)
    assert post() == 200 and state.game_state.pets.is_pet(70)
    assert post(name=None) == 200 and not state.game_state.pets.is_pet(70)
    state.connected = False
    assert post() == 400


def test_adaptive_record_start_stop_plan_and_raw_recovery(http_api, tmp_path, monkeypatch):
    import time
    from collections import deque
    from types import SimpleNamespace
    from unittest.mock import Mock
    import cavebot
    from map_atlas import MapAtlas

    atlas = MapAtlas(tmp_path / "atlas", "scope")
    atlas.observe({"cells": [{"x": x, "y": 101, "z": 7, "items": [], "kind": "clear"}
                             for x in range(70, 115)], "status": "ready", "captured_at": time.time()})
    gs = SimpleNamespace(position=(70, 101, 7), experience=100000, level=20, server_events=deque())
    st = SimpleNamespace(connected=True, recording_active=False, game_state=gs, game_proxy=Mock(),
                         passive_map_monitor=SimpleNamespace(atlas=atlas, learner=None))
    monkeypatch.setattr(api, "_state", st)
    monkeypatch.setattr(api, "_main_loop", object())
    monkeypatch.setattr(cavebot, "RECORDINGS_DIR", tmp_path / "recordings")
    request, mutations = http_api
    headers = {"Origin": DASHBOARD_ORIGIN, "Authorization": f"Bearer {SESSION_TOKEN}"}

    def post(path, **body):
        status, _, data = request("POST", path, body=json.dumps(body), **headers)
        assert status == 200, data
        return json.loads(data)

    post("/api/cavebot/record/start", name="hunt", mode="adaptive")
    for x in [*range(71, 112), *range(110, 89, -1)]:
        gs.position = (x, 101, 7)
        if x in (100, 110):
            gs.experience += 250
        cavebot._sample_recording_position(st)
    post("/api/cavebot/record/stop", discard=False)
    raw = cavebot.load_recording("hunt")
    p = post("/api/map/plan", scope="scope", recording="hunt", loop=True)
    assert p["complete"] and p["adaptive_evidence"]["areas"] == 2
    assert all(100 <= x <= 111 for x, _, _ in p["path"])
    full = post("/api/map/plan", scope="scope", recording="hunt", full_trace=True)
    assert full["original"][0] == [70, 101, 7] and not full.get("adaptive_evidence")
    assert cavebot.load_recording("hunt") == raw and not atlas.drafts() and not mutations


def test_preflight_and_read_access(http_api):
    request, _ = http_api
    assert request("OPTIONS", Origin="https://evil.example")[0] == 403
    assert request("OPTIONS", Origin=DASHBOARD_ORIGIN)[0] == 204
    status, headers, body = request(Authorization=f"Bearer {SESSION_TOKEN}")
    assert status == 200 and json.loads(body) == {"connected": False}
    assert "Access-Control-Allow-Origin" not in headers


def test_websocket_requires_origin_and_token_before_sending_state(monkeypatch):
    monkeypatch.setattr(api, "WS_PORT", 0)
    monkeypatch.setattr(api, "WS_AUTH_TIMEOUT", 0.1)
    monkeypatch.setattr(api, "_build_state_json", lambda: '{"connected":false}')
    async def exercise():
        server = await api._create_ws_server()
        try:
            uri = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
            for origin in (None, "https://evil.example", "null"):
                with pytest.raises(InvalidStatus):
                    async with websockets.connect(uri, origin=origin, proxy=None):
                        pytest.fail("untrusted origin accepted")
            for credentials in (None, '{"token":"wrong"}', '[]', 'invalid-json'):
                async with websockets.connect(uri, origin=DASHBOARD_ORIGIN, proxy=None) as ws:
                    if credentials is not None:
                        await ws.send(credentials)
                    with pytest.raises(ConnectionClosed):
                        await asyncio.wait_for(ws.recv(), timeout=2)
                    assert ws.close_code == 1008
                    assert not api._ws_clients
            async with websockets.connect(uri, origin=DASHBOARD_ORIGIN, proxy=None) as ws:
                await ws.send(json.dumps({"token": SESSION_TOKEN}))
                assert json.loads(await asyncio.wait_for(ws.recv(), 2)) == {"connected": False}
        finally:
            server.close()
            await server.wait_closed()
        assert not api._ws_clients
    asyncio.run(exercise())

def test_alert_read_and_config_routes_keep_authentication(http_api,monkeypatch):
    from types import SimpleNamespace
    from game_alerts import AlertCenter
    center=AlertCenter()
    monkeypatch.setattr(api,'_state',SimpleNamespace(alert_center=center))
    request,_=http_api
    headers={'Origin':DASHBOARD_ORIGIN,'Authorization':f'Bearer {SESSION_TOKEN}'}
    for method in ('GET','POST'):
        assert request(method,'/api/alerts',body='{}' if method=='POST' else None,**{**headers,'Origin':'https://evil.example'})[0]==403
    status,_,body=request('GET','/api/alerts',**headers)
    assert status==200 and json.loads(body)['events']==[]
    calls=[]
    monkeypatch.setattr(api._Handler,'_run_control',lambda self,*args:(calls.append(args),self._json_response({'ok':True})))
    assert request('POST','/api/alerts',body='{"command":"acknowledge","through":0}',**headers)[0]==200
    assert calls==[('_async_alerts','acknowledge',{'command':'acknowledge','through':0})]
    assert request('POST','/api/alerts',body='x'*4097,**headers)[0]==400
    assert request('POST','/api/alerts',body='{"command":"start"}',**headers)[0]==400
    assert len(calls)==1
