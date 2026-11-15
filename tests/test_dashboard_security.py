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
    def request(method="GET", path="/api/state", **headers):
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
        try:
            conn.request(method, path, headers=headers)
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            conn.close()
    yield request, mutations
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/state"), ("POST", "/api/actions/power_up/toggle"),
    ("DELETE", "/api/actions/power_up"),
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
