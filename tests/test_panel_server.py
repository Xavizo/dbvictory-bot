"""The panel is served from dashboard/.next by Python, the way `next start` serves static pages."""
import http.client
import json

import pytest

from dbv.mcp_server import panel_server


@pytest.fixture
def dashboard(tmp_path):
    build = tmp_path / ".next"
    files = {
        ".next/BUILD_ID": "abc",
        ".next/server/app/index.html": "<html>home</html>",
        ".next/server/app/index.rsc": "0:home-rsc",
        ".next/server/app/route-editor.html": "<html>editor</html>",
        ".next/server/app/route-editor.rsc": "0:editor-rsc",
        ".next/server/app/_not-found.html": "<html>missing</html>",
        ".next/server/app/page.js": "server code",
        ".next/static/chunks/app.js": "console.log(1)",
        ".next/static/css/app.css": "body{}",
        ".next/required-server-files.json": "{}",
        "public/local-spells/icons/85.png": "png",
        "package.json": "{}",
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    assert build.is_dir()
    return tmp_path


@pytest.fixture
def served(dashboard):
    server = panel_server.start(dashboard, port=0)
    port = server.server_address[1]
    panel_server_hosts = panel_server.ALLOWED_HOSTS
    panel_server.ALLOWED_HOSTS = {f"127.0.0.1:{port}"}

    def get(path, headers=None, method="GET"):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(method, path, headers=headers or {})
        response = conn.getresponse()
        body = response.read()
        conn.close()
        return response, body
    yield get
    panel_server.ALLOWED_HOSTS = panel_server_hosts
    server.shutdown()
    server.server_close()


def test_pages_payloads_and_assets(served):
    response, body = served("/")
    assert response.status == 200 and body == b"<html>home</html>"
    assert response.getheader("Content-Type").startswith("text/html")
    assert response.getheader("X-Frame-Options") == "DENY"
    response, body = served("/route-editor?draft=")
    assert body == b"<html>editor</html>"
    response, body = served("/route-editor?_rsc=x1", {"RSC": "1"})
    assert body == b"0:editor-rsc" and response.getheader("Content-Type") == "text/x-component"
    assert "RSC" in response.getheader("Vary")
    response, body = served("/_next/static/chunks/app.js")
    assert body == b"console.log(1)" and response.getheader("Content-Type").startswith("text/javascript")
    assert "immutable" in response.getheader("Cache-Control")
    response, body = served("/local-spells/icons/85.png")
    assert body == b"png" and response.getheader("Content-Type") == "image/png"
    response, _ = served("/", method="HEAD")
    assert response.status == 200


@pytest.mark.parametrize("path", [
    "/nope", "/page.js", "/_not-found", "/_next/static/../BUILD_ID", "/_next/static/%2e%2e/BUILD_ID",
    "/_next/required-server-files.json", "/../package.json", "/%2e%2e/package.json",
    "/_next/static/chunks", "/_next/static/chunks/..%5capp.js",
])
def test_nothing_outside_the_built_pages_and_public_files(served, path):
    response, body = served(path)
    assert response.status == 404
    if response.getheader("Content-Type", "").startswith("text/html"):
        assert body == b"<html>missing</html>"


def test_rebinding_hosts_and_writes_are_refused(served):
    response, _ = served("/", {"Host": "evil.example:4747"})
    assert response.status == 421
    response, _ = served("/", method="POST")
    assert response.status == 405


def test_ready_needs_a_build(tmp_path, dashboard):
    assert panel_server.ready(dashboard)
    assert not panel_server.ready(tmp_path / "elsewhere")
