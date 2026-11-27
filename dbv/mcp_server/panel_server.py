"""Serve the prebuilt panel from `dashboard/.next` on loopback, without Node.js.

Every panel page is prerendered by `next build` (static HTML plus its React
Server Component payload), so a player's installation only needs these files,
not the Next.js server. Requests are answered like `next start` would for a
static app: a page's HTML, its `.rsc` payload when the client router asks for
it, `/_next/static/*` assets and files from `dashboard/public`.

Run standalone (release build check, manual testing):
    python -m dbv.mcp_server.panel_server [DASHBOARD_DIR]
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import logging
from pathlib import Path
import sys
import threading
from urllib.parse import unquote, urlsplit

log = logging.getLogger("panel_server")

HOST = "127.0.0.1"
PORT = 4747
ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
# Windows' registry can map .js to text/plain; never rely on mimetypes here.
TYPES = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".json": "application/json", ".map": "application/json", ".txt": "text/plain; charset=utf-8",
    ".rsc": "text/x-component", ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp", ".ico": "image/x-icon",
    ".woff": "font/woff", ".woff2": "font/woff2", ".ttf": "font/ttf", ".wasm": "application/wasm",
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg",
}
RSC_VARY = "RSC, Next-Router-State-Tree, Next-Router-Prefetch, Next-Router-Segment-Prefetch"


def _inside(base, relative):
    """`base/relative` when it stays inside base and is a file, else None."""
    if not relative or "\0" in relative or "\\" in relative or ":" in relative:
        return None
    parts = relative.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return None
    base = base.resolve()
    target = (base / relative).resolve()
    if base not in target.parents or not target.is_file():
        return None
    return target


def resolve(dashboard, path, rsc=False):
    """(file, status, headers) for a request path, or None when nothing matches."""
    build = Path(dashboard) / ".next"
    path = unquote(path)
    if path.startswith("/_next/static/"):
        target = _inside(build / "static", path[len("/_next/static/"):])
        return target and (target, 200, {"Cache-Control": "public, max-age=31536000, immutable"})
    if path.startswith("/_next/"):
        return None
    page = path.strip("/") or "index"
    pages = build / "server" / "app"
    if not page.startswith("_"):
        target = _inside(pages, page + (".rsc" if rsc else ".html"))
        if target:
            headers = {"Cache-Control": "no-store"}
            if rsc:
                headers["Vary"] = RSC_VARY
            return target, 200, headers
    target = _inside(Path(dashboard) / "public", path.lstrip("/"))
    if target is None and path == "/local-spells/catalog.json":
        # No catalogue exported from this player's client: serve the one shipped with the bot.
        target = _inside(Path(dashboard) / "spells", "catalog.json")
    return target and (target, 200, {"Cache-Control": "no-cache"})


class _Handler(BaseHTTPRequestHandler):
    server_version = "DBVictoryPanel"
    sys_version = ""
    dashboard = Path(".")

    def log_message(self, fmt, *args):  # keep stdout for MCP; requests are not worth logging
        pass

    def _send(self, status, body=b"", content_type="text/plain; charset=utf-8", headers=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self):
        # Host check rejects DNS-rebinding pages that resolve their own name to 127.0.0.1.
        if self.headers.get("Host") not in ALLOWED_HOSTS:
            return self._send(421, b"Misdirected request")
        path = urlsplit(self.path).path
        found = resolve(self.dashboard, path, rsc=self.headers.get("RSC") == "1")
        if found is None:
            missing = _inside(self.dashboard / ".next" / "server" / "app", "_not-found.html")
            body = missing.read_bytes() if missing else b"Not found"
            return self._send(404, body, TYPES[".html"] if missing else TYPES[".txt"], {"Cache-Control": "no-store"})
        target, status, headers = found
        try:
            body = target.read_bytes()
        except OSError:
            return self._send(404, b"Not found")
        self._send(status, body, TYPES.get(target.suffix.lower(), "application/octet-stream"), headers)

    do_HEAD = do_GET

    def do_POST(self):
        self._send(405, b"Method not allowed", headers={"Allow": "GET, HEAD"})

    do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_POST


def ready(dashboard):
    return (Path(dashboard) / ".next" / "BUILD_ID").is_file()


def start(dashboard, port=PORT):
    """Serve in a daemon thread; returns the server (call shutdown() to stop)."""
    handler = type("PanelHandler", (_Handler,), {"dashboard": Path(dashboard)})
    server = ThreadingHTTPServer((HOST, port), handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, name="panel-server", daemon=True).start()
    return server


def main():
    from dbv import paths
    dashboard = Path(sys.argv[1]) if len(sys.argv) > 1 else paths.DASHBOARD_DIR
    if not ready(dashboard):
        raise SystemExit(f"No panel build in {dashboard / '.next'}")
    server = start(dashboard)
    print(f"Panel on http://{HOST}:{PORT}", file=sys.stderr, flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
